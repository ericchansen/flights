"""Frontier Airlines provider.

Talks to Frontier's booking backend (Navitaire New Skies behind an Azure APIM
gateway), reverse-engineered from ``www2.flyfrontier.com``. Pure HTTP, no
browser or login. See the package README for the full endpoint reference.

Auth model:
* ``ocp-apim-subscription-key`` - public APIM key from ``/api/environment``.
* ``authtoken`` - anonymous ~15-min Navitaire session JWT, minted on demand via
  the ``RetrieveAnonymousToken`` GraphQL query and auto-refreshed.
* ``frontiertoken`` - must be present but is not validated (a random UUID).

Thread-safe: token minting is locked and each thread gets its own HTTP session,
so a single instance can back the concurrent crawler.
"""

import datetime as _dt
import threading
import time
import uuid

import requests

from ...core.errors import AuthError, MarketNotFoundError, ProviderError
from ...core.models import Airport, DayFare, Flight
from ...core.provider import BaseProvider

ENVIRONMENT_URL = "https://www2.flyfrontier.com/api/environment"
DEFAULT_BFF_ENDPOINT = "https://mtier.flyfrontier.com/consumerappsbff/graphql"
LOWFARE_URL = "https://mtier.flyfrontier.com/flightavailabilityssv/NFAvailabilityLowfareSearch"
DEFAULT_FARE_TYPES = ["TC", "R", "GW", "FC"]
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36"
)

_Q_RETRIEVE_TOKEN = (
    "query RetrieveAnonymousToken { uiRetrieveAnonymousTokenSetSingleton { "
    "status message data { authToken expires organizationCode roleCode userKey } } }"
)
_Q_ORIGINS = (
    "query UiOriginAirportListSetSingleton { uiOriginAirportListSetSingleton { "
    "getOriginData { code cityName stationFullName stationShortName countryCode "
    "countryName stateCode stateName lat long } } }"
)
_Q_DESTINATIONS = (
    "query GetDestination($input: UpdateUiDestinationSetSingletonInput!) { "
    "uiDestinationAirportListSetSingleton(input: $input) { getDestinationData { "
    "code cityName stationFullName stationShortName countryCode countryName "
    "stateCode stateName lat long } } }"
)
_M_AVAILABILITY = """
mutation UpdateAvailability($input: UpdateUiAvailabilitySetSingletonInput!) {
  updateUiAvailabilitySetSingleton(input: $input) {
    departureTrips {
      departTime arriveTime departureAirportCode arrivalAirportCode
      flightNumber aircraftType totalFlightTime totalTripTime
      stops flightType totalMiles layoverTime overNightDay
      standardFareAvailabilityKey { farePrice passengerFareAmount }
      discountDenFareAvailabilityKey { farePrice passengerFareAmount }
      goWildFareAvailabilityKey { farePrice passengerFareAmount }
      milesFareAvailabilityKey { farePrice passengerFareAmount }
    }
  }
}
""".strip()

_thread_local = threading.local()


class FrontierProvider(BaseProvider):
    name = "frontier"
    lowfare_window_days = 7
    default_currency = "USD"

    def __init__(
        self,
        subscription_key: str | None = None,
        bff_endpoint: str | None = None,
        currency: str = "USD",
        request_delay: float = 0.0,
        timeout: float = 45.0,
        max_retries: int = 3,
    ) -> None:
        self.currency = currency
        self.request_delay = request_delay
        self.timeout = timeout
        self.max_retries = max_retries

        self._subscription_key = subscription_key
        self._bff_endpoint = bff_endpoint
        self._token: str | None = None
        self._token_expires: float = 0.0
        self._token_lock = threading.Lock()

        if self._subscription_key is None or self._bff_endpoint is None:
            self._load_config()

    # ---- session / config ------------------------------------------------ #

    def _session(self) -> requests.Session:
        s = getattr(_thread_local, "frontier_session", None)
        if s is None:
            s = requests.Session()
            s.headers.update({"User-Agent": USER_AGENT, "Accept": "application/json"})
            _thread_local.frontier_session = s
        return s

    def _load_config(self) -> None:
        try:
            resp = self._session().get(ENVIRONMENT_URL, timeout=self.timeout)
        except requests.RequestException as exc:
            # A transient failure reaching the public config endpoint should not
            # sink the client when the caller already supplied a key; fall back
            # to the known default BFF endpoint.
            if self._subscription_key:
                self._bff_endpoint = self._bff_endpoint or DEFAULT_BFF_ENDPOINT
                return
            raise AuthError(f"Could not reach {ENVIRONMENT_URL}: {exc}") from exc
        if resp.status_code == 200:
            env = resp.json()
            if self._subscription_key is None:
                self._subscription_key = env.get("BFF_SUBSCRIPTION_KEY")
            if self._bff_endpoint is None:
                self._bff_endpoint = env.get("BFF_ENDPOINT") or DEFAULT_BFF_ENDPOINT
        self._bff_endpoint = self._bff_endpoint or DEFAULT_BFF_ENDPOINT
        if not self._subscription_key:
            raise AuthError(
                "BFF_SUBSCRIPTION_KEY unavailable from /api/environment; "
                "pass subscription_key explicitly."
            )

    # ---- headers / http -------------------------------------------------- #

    def _headers(self, with_token: bool = False) -> dict:
        h = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "ocp-apim-subscription-key": self._subscription_key,
            "frontiertoken": str(uuid.uuid4()),
        }
        if with_token:
            h["authtoken"] = self._ensure_token()
        return h

    def _post(self, url: str, payload: dict, with_token: bool) -> dict:
        last: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            if self.request_delay:
                time.sleep(self.request_delay)
            try:
                resp = self._session().post(
                    url, json=payload, headers=self._headers(with_token), timeout=self.timeout
                )
            except requests.RequestException as exc:
                last = exc
                time.sleep(min(2**attempt, 6))
                continue
            if resp.status_code == 200:
                data = resp.json()
                if isinstance(data, dict) and data.get("errors"):
                    raise ProviderError(f"GraphQL errors: {data['errors']}")
                return data
            if resp.status_code == 400 and "does not exist" in resp.text:
                raise MarketNotFoundError(resp.text[:200])
            if resp.status_code == 401 and with_token:
                new_token = self._mint_token()
                # Some operations embed the auth token in the request body
                # (e.g. GraphQL variables.input.authToken); keep it in sync with
                # the freshly minted header token before retrying.
                _sync_body_token(payload, new_token)
                continue
            if resp.status_code >= 500:
                last = ProviderError(f"HTTP {resp.status_code}: {resp.text[:200]}")
                time.sleep(min(2**attempt, 6))
                continue
            raise ProviderError(f"HTTP {resp.status_code}: {resp.text[:300]}")
        raise ProviderError(f"Request to {url} failed after {self.max_retries} tries: {last}")

    def _graphql(self, operation: str, query: str, variables: dict, with_token: bool) -> dict:
        endpoint = self._bff_endpoint or DEFAULT_BFF_ENDPOINT
        return self._post(
            endpoint,
            {"operationName": operation, "variables": variables, "query": query},
            with_token=with_token,
        )

    # ---- token ----------------------------------------------------------- #

    def _mint_token(self) -> str:
        with self._token_lock:
            data = self._graphql("RetrieveAnonymousToken", _Q_RETRIEVE_TOKEN, {}, with_token=False)
            node = data.get("data", {}).get("uiRetrieveAnonymousTokenSetSingleton", {})
            token = (node.get("data") or {}).get("authToken")
            if not token:
                raise AuthError(f"Failed to mint anonymous token: {node.get('message')}")
            self._token = token
            self._token_expires = time.time() + 12 * 60  # conservative TTL
            return token

    def _ensure_token(self) -> str:
        if self._token is None or time.time() >= self._token_expires:
            self._mint_token()
        return self._token  # type: ignore[return-value]

    # ---- interface ------------------------------------------------------- #

    def origins(self) -> list[Airport]:
        data = self._graphql("UiOriginAirportListSetSingleton", _Q_ORIGINS, {}, with_token=False)
        rows = (
            data.get("data", {}).get("uiOriginAirportListSetSingleton", {}).get("getOriginData")
            or []
        )
        return [_airport(r) for r in rows]

    def destinations(self, origin: str) -> list[Airport]:
        data = self._graphql(
            "GetDestination",
            _Q_DESTINATIONS,
            {"input": {"originAirport": origin}},
            with_token=False,
        )
        rows = (
            data.get("data", {})
            .get("uiDestinationAirportListSetSingleton", {})
            .get("getDestinationData")
            or []
        )
        return [_airport(r) for r in rows]

    def lowfare_window(
        self, origin: str, destination: str, begin: _dt.date, end: _dt.date
    ) -> list[DayFare]:
        body = {
            "BypassCache": True,
            "GetAllDetails": True,
            "IncludeTaxesAndFees": True,
            "Passengers": {
                "Types": [{"Type": "ADT", "DiscountCode": "", "Count": 1}],
                "ResidentCountry": "US",
            },
            "Codes": {"Currency": self.currency, "SourceOrganization": None, "PromotionCode": None},
            "Filters": {
                "GroupByDate": None,
                "FlightFilter": None,
                "Loyalty": None,
                "BookingClasses": None,
                "ProductClasses": None,
                "FareTypes": DEFAULT_FARE_TYPES,
            },
            "Criteria": [
                {
                    "OriginStationCodes": [origin],
                    "DestinationStationCodes": [destination],
                    "BeginDate": begin.isoformat(),
                    "EndDate": end.isoformat(),
                }
            ],
        }
        data = self._post(LOWFARE_URL, body, with_token=True)
        results = (data.get("data") or {}).get("results") or {}
        rows = results.get(f"{origin}|{destination}") or []
        out = []
        for r in rows:
            out.append(
                DayFare(
                    provider=self.name,
                    origin=origin,
                    destination=destination,
                    date=str(r.get("date", ""))[:10],
                    standard_fare=_f(r.get("standardFare")),
                    discounted_fare=_f(r.get("discountedFare")),
                    saver_fare=_f(r.get("gowildFare")),
                    miles=_i(r.get("totalMilesPoint")),
                    miles_fees=_f(r.get("milesTaxesAndFees")),
                    currency=self.currency,
                )
            )
        return out

    def flights(
        self,
        origin: str,
        destination: str,
        date: str,
        nonstop_only: bool = False,
        adults: int = 1,
    ) -> list[Flight]:
        variables = {
            "input": {
                "authToken": self._ensure_token(),
                "origin": origin,
                "destination": destination,
                "beginDate": f"{date[:10]}T00:00:00Z",
                "endDate": None,
                "adultPassengerCount": adults,
                "childPassengerCount": 0,
                "teenPassengerCount": 0,
                "infantWithSeatPassengerCount": 0,
                "promoCode": None,
            }
        }
        data = self._graphql("UpdateAvailability", _M_AVAILABILITY, variables, with_token=True)
        trips = (data.get("data", {}).get("updateUiAvailabilitySetSingleton") or {}).get(
            "departureTrips"
        ) or []
        out = []
        for t in trips:
            fl = Flight(
                provider=self.name,
                origin=origin,
                destination=destination,
                date=date[:10],
                flight_number=str(t.get("flightNumber", "")),
                depart_time=t.get("departTime", ""),
                arrive_time=t.get("arriveTime", ""),
                aircraft=t.get("aircraftType"),
                stops=int(t.get("stops") or 0),
                flight_type=t.get("flightType") or "",
                duration=t.get("totalFlightTime"),
                standard_fare=_key(t.get("standardFareAvailabilityKey")),
                discounted_fare=_key(t.get("discountDenFareAvailabilityKey")),
                saver_fare=_key(t.get("goWildFareAvailabilityKey")),
                miles=_i(_key(t.get("milesFareAvailabilityKey"))),
                currency=self.currency,
            )
            if nonstop_only and not fl.is_nonstop:
                continue
            out.append(fl)
        return out


def _sync_body_token(payload: dict, token: str) -> None:
    """Update an auth token embedded in a GraphQL request body, if present.

    Frontier's GraphQL operations carry the session token in
    ``variables.input.authToken``; after a 401-triggered refresh the body must
    be updated so the retry doesn't resend the expired token. REST bodies (the
    low-fare search) carry no body token, so this is a safe no-op for them.
    """
    try:
        inp = payload["variables"]["input"]
    except (KeyError, TypeError):
        return
    if isinstance(inp, dict) and "authToken" in inp:
        inp["authToken"] = token


def _airport(r: dict) -> Airport:
    return Airport(
        code=r.get("code", ""),
        city=r.get("cityName", ""),
        full_name=r.get("stationFullName", ""),
        country_code=r.get("countryCode", ""),
        country_name=r.get("countryName", ""),
        state_code=r.get("stateCode"),
        lat=r.get("lat"),
        long=r.get("long"),
    )


def _f(v):
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _i(v):
    try:
        return int(round(float(v))) if v is not None else None
    except (TypeError, ValueError):
        return None


def _key(key: dict | None):
    return _f(key.get("farePrice")) if key else None
