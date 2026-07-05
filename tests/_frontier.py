"""Test doubles for the Frontier backend.

Endpoint constants and JSON payload builders shaped exactly like Frontier's
real GraphQL / low-fare responses, so provider tests can register them with
``responses`` and stay fully offline. The leading underscore keeps pytest from
collecting this module as a test file.
"""

from flights.providers.frontier.client import LOWFARE_URL

SUBSCRIPTION_KEY = "test-subscription-key"
BFF_ENDPOINT = "https://bff.test/graphql"

__all__ = [
    "BFF_ENDPOINT",
    "LOWFARE_URL",
    "SUBSCRIPTION_KEY",
    "airport_row",
    "origins_payload",
    "destinations_payload",
    "token_payload",
    "lowfare_payload",
    "availability_payload",
]


def airport_row(
    code,
    *,
    city="City",
    country_code="US",
    state_code="CO",
    lat="39.0",
    long="-104.0",
):
    return {
        "code": code,
        "cityName": city,
        "stationFullName": f"{city} International",
        "stationShortName": city,
        "countryCode": country_code,
        "countryName": "United States" if country_code == "US" else "Elsewhere",
        "stateCode": state_code,
        "stateName": state_code,
        "lat": lat,
        "long": long,
    }


def origins_payload(*codes):
    rows = [airport_row(c) for c in codes] if codes else [airport_row("DEN")]
    return {"data": {"uiOriginAirportListSetSingleton": {"getOriginData": rows}}}


def destinations_payload(*codes):
    rows = [airport_row(c) for c in codes] if codes else [airport_row("LAS")]
    return {"data": {"uiDestinationAirportListSetSingleton": {"getDestinationData": rows}}}


def token_payload(token="anon-token-abc"):
    return {
        "data": {
            "uiRetrieveAnonymousTokenSetSingleton": {
                "status": "OK",
                "message": None,
                "data": {"authToken": token},
            }
        }
    }


def lowfare_payload(origin, destination, days):
    """``days`` is a list of dicts with any of date/standard/discounted/gowild/miles/fees."""
    rows = []
    for d in days:
        rows.append(
            {
                "date": d.get("date"),
                "standardFare": d.get("standard"),
                "discountedFare": d.get("discounted"),
                "gowildFare": d.get("gowild"),
                "totalMilesPoint": d.get("miles"),
                "milesTaxesAndFees": d.get("fees"),
            }
        )
    return {"data": {"results": {f"{origin}|{destination}": rows}}}


def availability_payload(trips):
    """``trips`` is a list of dicts describing departureTrips entries."""
    out = []
    for t in trips:
        out.append(
            {
                "departTime": t.get("depart", "2025-01-01T08:00:00"),
                "arriveTime": t.get("arrive", "2025-01-01T09:30:00"),
                "departureAirportCode": t.get("origin", "DEN"),
                "arrivalAirportCode": t.get("destination", "LAS"),
                "flightNumber": t.get("flight_number", "F9 100"),
                "aircraftType": t.get("aircraft", "A320"),
                "totalFlightTime": t.get("duration", "1h30m"),
                "stops": t.get("stops", 0),
                "flightType": t.get("flight_type", "NonStop"),
                "totalMiles": t.get("total_miles"),
                "standardFareAvailabilityKey": _fare_key(t.get("standard")),
                "discountDenFareAvailabilityKey": _fare_key(t.get("discounted")),
                "goWildFareAvailabilityKey": _fare_key(t.get("gowild")),
                "milesFareAvailabilityKey": _fare_key(t.get("miles")),
            }
        )
    return {"data": {"updateUiAvailabilitySetSingleton": {"departureTrips": out}}}


def _fare_key(price):
    if price is None:
        return None
    return {"farePrice": price, "passengerFareAmount": price}
