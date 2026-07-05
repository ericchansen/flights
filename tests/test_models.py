"""Unit tests for the provider-agnostic data models."""

import pytest

from flights.core.models import Airport, DayFare, Flight, cheapest_cash


def test_cheapest_cash_helper():
    assert cheapest_cash(120.0, None, 49.0) == 49.0
    assert cheapest_cash(None, None, None) is None
    assert cheapest_cash() is None


def test_cheapest_cash_property_delegates_to_helper():
    fare = DayFare("frontier", "DEN", "LAS", "2025-01-01", standard_fare=120.0, saver_fare=49.0)
    assert fare.cheapest_cash == cheapest_cash(
        fare.standard_fare, fare.discounted_fare, fare.saver_fare
    )


def test_dayfare_cheapest_cash_picks_minimum():
    fare = DayFare("frontier", "DEN", "LAS", "2025-01-01", standard_fare=120.0, saver_fare=49.0)
    assert fare.cheapest_cash == 49.0


def test_dayfare_cheapest_cash_ignores_none_tiers():
    fare = DayFare("frontier", "DEN", "LAS", "2025-01-01", discounted_fare=88.0)
    assert fare.cheapest_cash == 88.0


def test_dayfare_cheapest_cash_all_none():
    fare = DayFare("frontier", "DEN", "LAS", "2025-01-01")
    assert fare.cheapest_cash is None


def test_flight_cheapest_cash():
    flight = Flight(
        "frontier",
        "DEN",
        "LAS",
        "2025-01-01",
        "F9 100",
        "2025-01-01T08:00:00",
        "2025-01-01T09:30:00",
        standard_fare=200.0,
        saver_fare=59.0,
    )
    assert flight.cheapest_cash == 59.0


@pytest.mark.parametrize(
    ("stops", "flight_type", "expected"),
    [
        (0, "", True),
        (1, "", False),
        (0, "Connecting", True),  # stops == 0 short-circuits
        (1, "NonStop", True),
        (2, "Direct", True),
        (1, "connecting", False),
    ],
)
def test_flight_is_nonstop(stops, flight_type, expected):
    flight = Flight(
        "frontier",
        "DEN",
        "LAS",
        "2025-01-01",
        "F9 100",
        "",
        "",
        stops=stops,
        flight_type=flight_type,
    )
    assert flight.is_nonstop is expected


def test_airport_is_domestic_us():
    assert Airport("DEN", "Denver", "Denver Intl", "US", "United States").is_domestic_us
    assert not Airport("CUN", "Cancun", "Cancun Intl", "MX", "Mexico").is_domestic_us
