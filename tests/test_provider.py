"""Offline tests for the Frontier provider (all HTTP mocked via responses)."""

from datetime import date

import pytest
from _frontier import (
    BFF_ENDPOINT,
    LOWFARE_URL,
    availability_payload,
    destinations_payload,
    lowfare_payload,
    origins_payload,
    token_payload,
)

from flights.core.errors import MarketNotFoundError, ProviderError


def test_origins_parses_airports(frontier, mocked_responses):
    mocked_responses.post(BFF_ENDPOINT, json=origins_payload("DEN", "MCO"))

    airports = frontier.origins()

    assert [a.code for a in airports] == ["DEN", "MCO"]
    assert airports[0].full_name == "City International"


def test_destinations_parses_airports(frontier, mocked_responses):
    mocked_responses.post(BFF_ENDPOINT, json=destinations_payload("LAS", "PHX"))

    dests = frontier.destinations("DEN")

    assert {a.code for a in dests} == {"LAS", "PHX"}


def test_graphql_errors_raise_provider_error(frontier, mocked_responses):
    mocked_responses.post(BFF_ENDPOINT, json={"errors": [{"message": "boom"}]})

    with pytest.raises(ProviderError):
        frontier.origins()


def test_lowfare_window_mints_token_and_parses(frontier, mocked_responses):
    mocked_responses.post(BFF_ENDPOINT, json=token_payload())  # anonymous token mint
    mocked_responses.post(
        LOWFARE_URL,
        json=lowfare_payload(
            "DEN",
            "LAS",
            [
                {
                    "date": "2025-01-01",
                    "standard": 120.0,
                    "gowild": 49.0,
                    "miles": 10000,
                    "fees": 5.6,
                }
            ],
        ),
    )

    fares = frontier.lowfare_window("DEN", "LAS", date(2025, 1, 1), date(2025, 1, 7))

    assert len(fares) == 1
    assert fares[0].saver_fare == 49.0
    assert fares[0].cheapest_cash == 49.0
    assert fares[0].miles == 10000
    assert fares[0].miles_fees == 5.6


def test_lowfare_window_market_not_found(frontier, mocked_responses):
    mocked_responses.post(BFF_ENDPOINT, json=token_payload())
    mocked_responses.post(LOWFARE_URL, body="Market DEN-ZZZ does not exist", status=400)

    with pytest.raises(MarketNotFoundError):
        frontier.lowfare_window("DEN", "ZZZ", date(2025, 1, 1), date(2025, 1, 7))


def test_lowfare_window_retries_after_401(frontier, mocked_responses):
    mocked_responses.post(BFF_ENDPOINT, json=token_payload("t1"))  # first mint
    mocked_responses.post(LOWFARE_URL, status=401)  # stale token rejected
    mocked_responses.post(BFF_ENDPOINT, json=token_payload("t2"))  # refresh mint
    mocked_responses.post(
        LOWFARE_URL,
        json=lowfare_payload("DEN", "LAS", [{"date": "2025-01-01", "standard": 100.0}]),
    )

    fares = frontier.lowfare_window("DEN", "LAS", date(2025, 1, 1), date(2025, 1, 7))

    assert len(fares) == 1
    assert fares[0].standard_fare == 100.0


def test_flights_filters_to_nonstop(frontier, mocked_responses):
    mocked_responses.post(BFF_ENDPOINT, json=token_payload())  # token mint
    mocked_responses.post(
        BFF_ENDPOINT,
        json=availability_payload(
            [
                {"flight_type": "NonStop", "stops": 0, "standard": 99.0},
                {"flight_type": "Connecting", "stops": 1, "standard": 79.0},
            ]
        ),
    )

    flights = frontier.flights("DEN", "LAS", "2025-01-01", nonstop_only=True)

    assert len(flights) == 1
    assert flights[0].is_nonstop
    assert flights[0].standard_fare == 99.0
