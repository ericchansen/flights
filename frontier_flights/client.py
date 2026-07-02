"""Core HTTP client for Frontier Airlines' booking backend.

All data is fetched over plain HTTPS against the same endpoints the
flyfrontier.com single-page app uses. The only credentials required are:

* ``BFF_SUBSCRIPTION_KEY`` - an Azure APIM product key exposed publicly at
  ``/api/environment`` (shared by all anonymous web visitors, not per-user).
* an anonymous session ``authToken`` minted on demand via the
  ``RetrieveAnonymousToken`` GraphQL query. It lasts ~15 minutes and is
  refreshed automatically.

The ``frontiertoken`` header is required to be present but its value is not
validated, so we send a random UUID.
"""

from __future__ import annotations

import datetime as _dt
import time
import uuid
from dataclasses import dataclass, field
from typing import Iterable, Iterator, Optional

import requests


# --------------------------------------------------------------------------- #
# Constants (reverse-engineered)                                              #
# --------------------------------------------------------------------------- #

ENVIRONMENT_URL = "https://www2.flyfrontier.com/api/environment"
DEFAULT_BFF_ENDPOINT = "https://mtier.flyfrontier.com/consumerappsbff/graphql"
LOWFARE_URL = (
    "https://mtier.flyfrontier.com/flightavailabilityssv/NFAvailabilityLowfareSearch"
)

# Fare "product" codes seen in the app. TC=standard, R=?, GW=GoWild, FC=?.
DEFAULT_FARE_TYPES = ["TC", "R", "GW", "FC"]

# Lowfare calendar only returns a ~7-day window per request.
LOWFARE_WINDOW_DAYS = 7

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36"
)


# --------------------------------------------------------------------------- #
# GraphQL documents                                                           #
# --------------------------------------------------------------------------- #

_Q_RETRIEVE_TOKEN = (
    "query RetrieveAnonymousToken { "
    "uiRetrieveAnonymousTokenSetSingleton { status message "
    "data { authToken cultureCode currencyCode roleCode locationCode "
    "domainCode organizationCode expires userKey personKey } } }"
)

_Q_ORIGINS = (
    "query UiOriginAirportListSetSingleton { "
    "uiOriginAirportListSetSingleton { getOriginData { "
    "code cityName stationFullName stationShortName countryCode countryName "
    "stateCode stateName lat long } } }"
)

_Q_DESTINATIONS = (
    "query GetDestination($input: UpdateUiDestinationSetSingletonInput!) { "
    "uiDestinationAirportListSetSingleton(input: $input) { getDestinationData { "
    "code cityName stationFullName stationShortName countryCode countryName "
    "stateCode stateName lat long } } }"
)

_Q_TRIP_SCHEDULE = (
    "query GetTripSchedule($input: UiTripScheduleSetSingletonInput!) { "
    "uiTripScheduleSetSingleton(input: $input) { status message "
    "tripScheduleResponse { departureDate earliest latest flights noteCode } } }"
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


# --------------------------------------------------------------------------- #
# Data models                                                                 #
# --------------------------------------------------------------------------- #


class FrontierError(RuntimeError):
    """Raised when the Frontier backend returns an error or unexpected shape."""


@dataclass(frozen=True)
class Airport:
    code: str
    city: str
    full_name: str
    country_code: str
    country_name: str
    state_code: Optional[str] = None
    lat: Optional[str] = None
    long: Optional[str] = None

    @property
    def is_domestic_us(self) -> bool:
        return self.country_code == "US"


@dataclass(frozen=True)
class DayFare:
    """One row of the low-fare calendar for a route/date."""

    origin: str
    destination: str
    date: str  # YYYY-MM-DD
    standard_fare: Optional[float]
    discounted_fare: Optional[float]
    gowild_fare: Optional[float]
    total_miles: Optional[int]
    miles_taxes_fees: Optional[float]
    currency: str = "USD"

    @property
    def cheapest_cash(self) -> Optional[float]:
        vals = [v for v in (self.standard_fare, self.discounted_fare, self.gowild_fare)
                if v is not None]
        return min(vals) if vals else None


@dataclass(frozen=True)
class Flight:
    """A single scheduled flight with its fares."""

    origin: str
    destination: str
    date: str
    flight_number: str
    depart_time: str  # ISO
    arrive_time: str  # ISO
    aircraft: Optional[str]
    stops: int
    flight_type: str  # "NonStop" / "Direct" / "Connecting"
    total_flight_time: Optional[str]
    standard_fare: Optional[float]
    discounted_fare: Optional[float]
    gowild_fare: Optional[float]
    miles: Optional[int]
    currency: str = "USD"

    @property
    def is_nonstop(self) -> bool:
        return self.stops == 0 or self.flight_type.lower() in ("nonstop", "direct")

    @property
    def cheapest_cash(self) -> Optional[float]:
        vals = [v for v in (self.standard_fare, self.discounted_fare, self.gowild_fare)
                if v is not None]
        return min(vals) if vals else None


# --------------------------------------------------------------------------- #
# Client                                                                       #
# --------------------------------------------------------------------------- #


class FrontierClient:
    """Pure-HTTP client for Frontier flight availability.

    Parameters
    ----------
    subscription_key:
        Azure APIM key. If omitted, it is fetched from ``/api/environment``.
    bff_endpoint:
        GraphQL BFF URL. If omitted, taken from ``/api/environment`` (falls
        back to the known default).
    currency:
        ISO currency code for fares (default ``USD``).
    request_delay:
        Seconds to sleep between HTTP calls to stay polite (default ``0.3``).
    timeout:
        Per-request timeout in seconds (default ``30``).
    max_retries:
        Retry attempts on transient (5xx / network) errors (default ``3``).
    """

    def __init__(
        self,
        subscription_key: Optional[str] = None,
        bff_endpoint: Optional[str] = None,
        currency: str = "USD",
        request_delay: float = 0.3,
        timeout: float = 30.0,
        max_retries: int = 3,
    ) -> None:
        self.currency = currency
        self.request_delay = request_delay
        self.timeout = timeout
        self.max_retries = max_retries

        self._session = requests.Session()
        self._session.headers.update({"User-Agent": USER_AGENT, "Accept": "application/json"})

        self._subscription_key = subscription_key
        self._bff_endpoint = bff_endpoint

        self._token: Optional[str] = None
        self._token_expires: float = 0.0  # epoch seconds

        if self._subscription_key is None or self._bff_endpoint is None:
            self._load_config()

    # ---- configuration --------------------------------------------------- #

    def _load_config(self) -> None:
        """Fetch subscription key + BFF endpoint from the public env route."""
        resp = self._session.get(ENVIRONMENT_URL, timeout=self.timeout)
        if resp.status_code != 200:
            # Fall back to known defaults if the env route changes shape.
            self._bff_endpoint = self._bff_endpoint or DEFAULT_BFF_ENDPOINT
            if self._subscription_key is None:
                raise FrontierError(
                    f"Could not load /api/environment (HTTP {resp.status_code}); "
                    "pass subscription_key explicitly."
                )
            return
        env = resp.json()
        if self._subscription_key is None:
            self._subscription_key = env.get("BFF_SUBSCRIPTION_KEY")
        if self._bff_endpoint is None:
            self._bff_endpoint = env.get("BFF_ENDPOINT") or DEFAULT_BFF_ENDPOINT
        if not self._subscription_key:
            raise FrontierError("BFF_SUBSCRIPTION_KEY not present in /api/environment")

    # ---- low-level HTTP -------------------------------------------------- #

    def _headers(self, with_token: bool = False) -> dict:
        h = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "ocp-apim-subscription-key": self._subscription_key,
            "frontiertoken": str(uuid.uuid4()),
        }
        if with_token:
            h["authtoken"] = self._ensure_token()
            h["authorization"] = f"Bearer {self._token}"
        return h

    def _post(self, url: str, payload: dict, with_token: bool) -> dict:
        last_exc: Optional[Exception] = None
        for attempt in range(1, self.max_retries + 1):
            if self.request_delay:
                time.sleep(self.request_delay)
            try:
                resp = self._session.post(
                    url,
                    json=payload,
                    headers=self._headers(with_token=with_token),
                    timeout=self.timeout,
                )
            except requests.RequestException as exc:  # network error
                last_exc = exc
                time.sleep(min(2 ** attempt, 8))
                continue
            if resp.status_code >= 500:
                last_exc = FrontierError(f"HTTP {resp.status_code}: {resp.text[:200]}")
                time.sleep(min(2 ** attempt, 8))
                continue
            if resp.status_code != 200:
                raise FrontierError(f"HTTP {resp.status_code}: {resp.text[:300]}")
            data = resp.json()
            if isinstance(data, dict) and data.get("errors"):
                raise FrontierError(f"GraphQL errors: {data['errors']}")
            return data
        raise FrontierError(f"Request to {url} failed after {self.max_retries} tries: {last_exc}")

    def _graphql(self, operation: str, query: str, variables: dict, with_token: bool) -> dict:
        payload = {"operationName": operation, "variables": variables, "query": query}
        return self._post(self._bff_endpoint, payload, with_token=with_token)

    # ---- token management ------------------------------------------------ #

    def _mint_token(self) -> None:
        data = self._graphql("RetrieveAnonymousToken", _Q_RETRIEVE_TOKEN, {}, with_token=False)
        node = (
            data.get("data", {})
            .get("uiRetrieveAnonymousTokenSetSingleton", {})
        )
        payload = node.get("data") or {}
        token = payload.get("authToken")
        if not token:
            raise FrontierError(f"Failed to mint anonymous token: {node.get('message')}")
        self._token = token
        # `expires` looks like "07/02/2026 17:16:36" (server local). Rather than
        # parse an ambiguous TZ, assume a conservative 12-minute lifetime.
        self._token_expires = time.time() + 12 * 60

    def _ensure_token(self) -> str:
        if self._token is None or time.time() >= self._token_expires:
            self._mint_token()
        return self._token  # type: ignore[return-value]

    # ---- public: reference data ------------------------------------------ #

    def origins(self) -> list[Airport]:
        """All airports Frontier flies out of."""
        data = self._graphql(
            "UiOriginAirportListSetSingleton", _Q_ORIGINS, {}, with_token=False
        )
        rows = (
            data.get("data", {})
            .get("uiOriginAirportListSetSingleton", {})
            .get("getOriginData")
            or []
        )
        return [self._to_airport(r) for r in rows]

    def destinations(self, origin: str) -> list[Airport]:
        """All destinations reachable from ``origin`` (the route map)."""
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
        return [self._to_airport(r) for r in rows]

    def routes(self, origins: Optional[Iterable[str]] = None) -> Iterator[tuple[str, str]]:
        """Yield ``(origin, destination)`` pairs across the network.

        If ``origins`` is None, every origin airport is expanded. Note this
        yields each directed pair once; A->B and B->A are both produced if
        both directions are served.
        """
        if origins is None:
            origins = [a.code for a in self.origins()]
        for o in origins:
            for d in self.destinations(o):
                yield (o, d.code)

    def trip_schedule(self, origin: str, destination: str, begin_date: str) -> list[dict]:
        """Per-day flight counts for a route (which dates have service).

        ``begin_date`` is ``YYYY-MM-DD``. Returns the raw schedule rows
        (``departureDate``, ``earliest``, ``latest``, ``flights``).
        """
        variables = {
            "input": {
                "authToken": self._ensure_token(),
                "origin": origin,
                "destination": destination,
                "beginDate": _iso_z(begin_date),
                "currencyCode": self.currency,
            }
        }
        data = self._graphql("GetTripSchedule", _Q_TRIP_SCHEDULE, variables, with_token=True)
        node = data.get("data", {}).get("uiTripScheduleSetSingleton", {})
        return node.get("tripScheduleResponse") or []

    # ---- public: fares --------------------------------------------------- #

    def lowfare_calendar(
        self,
        origin: str,
        destination: str,
        begin_date: str,
        end_date: str,
        fare_types: Optional[list[str]] = None,
    ) -> list[DayFare]:
        """Cheapest fare + miles per day for a route over a date range.

        The backend returns at most a ~7-day window per call, so longer ranges
        are automatically chunked and concatenated.
        """
        start = _parse_date(begin_date)
        end = _parse_date(end_date)
        if end < start:
            raise ValueError("end_date must be >= begin_date")

        out: list[DayFare] = []
        cursor = start
        while cursor <= end:
            window_end = min(cursor + _dt.timedelta(days=LOWFARE_WINDOW_DAYS - 1), end)
            out.extend(
                self._lowfare_window(
                    origin, destination, cursor, window_end, fare_types
                )
            )
            cursor = window_end + _dt.timedelta(days=1)
        return out

    def _lowfare_window(
        self,
        origin: str,
        destination: str,
        begin: _dt.date,
        end: _dt.date,
        fare_types: Optional[list[str]],
    ) -> list[DayFare]:
        body = {
            "BypassCache": True,
            "GetAllDetails": True,
            "IncludeTaxesAndFees": True,
            "Passengers": {
                "Types": [{"Type": "ADT", "DiscountCode": "", "Count": 1}],
                "ResidentCountry": "US",
            },
            "Codes": {
                "Currency": self.currency,
                "SourceOrganization": None,
                "PromotionCode": None,
            },
            "Filters": {
                "GroupByDate": None,
                "FlightFilter": None,
                "Loyalty": None,
                "BookingClasses": None,
                "ProductClasses": None,
                "FareTypes": fare_types or DEFAULT_FARE_TYPES,
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
        # This is a REST (not GraphQL) endpoint but shares the auth scheme.
        data = self._post(LOWFARE_URL, body, with_token=True)
        results = (data.get("data") or {}).get("results") or {}
        key = f"{origin}|{destination}"
        rows = results.get(key) or []
        out: list[DayFare] = []
        for r in rows:
            out.append(
                DayFare(
                    origin=origin,
                    destination=destination,
                    date=str(r.get("date", ""))[:10],
                    standard_fare=_num(r.get("standardFare")),
                    discounted_fare=_num(r.get("discountedFare")),
                    gowild_fare=_num(r.get("gowildFare")),
                    total_miles=_int(r.get("totalMilesPoint")),
                    miles_taxes_fees=_num(r.get("milesTaxesAndFees")),
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
        """Individual flights for a route on a specific date, with fares + miles.

        ``date`` is ``YYYY-MM-DD``. Set ``nonstop_only=True`` to return only
        direct (``stops == 0``) flights.
        """
        variables = {
            "input": {
                "authToken": self._ensure_token(),
                "origin": origin,
                "destination": destination,
                "beginDate": _iso_z(date),
                "endDate": None,
                "adultPassengerCount": adults,
                "childPassengerCount": 0,
                "teenPassengerCount": 0,
                "infantWithSeatPassengerCount": 0,
                "promoCode": None,
            }
        }
        data = self._graphql("UpdateAvailability", _M_AVAILABILITY, variables, with_token=True)
        node = data.get("data", {}).get("updateUiAvailabilitySetSingleton") or {}
        trips = node.get("departureTrips") or []
        out: list[Flight] = []
        for t in trips:
            flight = Flight(
                origin=origin,
                destination=destination,
                date=date,
                flight_number=str(t.get("flightNumber", "")),
                depart_time=t.get("departTime", ""),
                arrive_time=t.get("arriveTime", ""),
                aircraft=t.get("aircraftType"),
                stops=int(t.get("stops") or 0),
                flight_type=t.get("flightType") or "",
                total_flight_time=t.get("totalFlightTime"),
                standard_fare=_key_price(t.get("standardFareAvailabilityKey")),
                discounted_fare=_key_price(t.get("discountDenFareAvailabilityKey")),
                gowild_fare=_key_price(t.get("goWildFareAvailabilityKey")),
                miles=_int(_key_price(t.get("milesFareAvailabilityKey"))),
                currency=self.currency,
            )
            if nonstop_only and not flight.is_nonstop:
                continue
            out.append(flight)
        return out

    # ---- helpers --------------------------------------------------------- #

    @staticmethod
    def _to_airport(r: dict) -> Airport:
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


# --------------------------------------------------------------------------- #
# small utilities                                                             #
# --------------------------------------------------------------------------- #


def _parse_date(s: str) -> _dt.date:
    return _dt.datetime.strptime(s[:10], "%Y-%m-%d").date()


def _iso_z(date_str: str) -> str:
    """YYYY-MM-DD -> 'YYYY-MM-DDT00:00:00Z' (the shape the BFF expects)."""
    return f"{_parse_date(date_str).isoformat()}T00:00:00Z"


def _num(v) -> Optional[float]:
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f != 0 else f  # keep 0.0 as-is


def _int(v) -> Optional[int]:
    if v is None:
        return None
    try:
        return int(round(float(v)))
    except (TypeError, ValueError):
        return None


def _key_price(key: Optional[dict]) -> Optional[float]:
    if not key:
        return None
    return _num(key.get("farePrice"))
