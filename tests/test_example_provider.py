"""Tests for the example provider template.

These keep the copy-paste template valid: if the BaseProvider interface changes,
this test fails and the template gets updated alongside it. The example is not
registered in flights.providers, so it never pollutes available_providers().
"""

import datetime as _dt

import pytest

from flights.core import available_providers
from flights.core.errors import MarketNotFoundError
from flights.providers.example import ExampleProvider


@pytest.fixture
def example():
    return ExampleProvider()


def test_example_is_not_registered_by_default():
    assert "example" not in available_providers()


def test_origins_and_destinations(example):
    codes = {a.code for a in example.origins()}
    assert codes == {"AAA", "BBB"}
    assert [d.code for d in example.destinations("aaa")] == ["BBB"]


def test_lowfare_window_returns_one_fare_per_day(example):
    fares = example.lowfare_window("AAA", "BBB", _dt.date(2026, 1, 1), _dt.date(2026, 1, 3))
    assert [f.date for f in fares] == ["2026-01-01", "2026-01-02", "2026-01-03"]
    assert all(f.provider == "example" for f in fares)


def test_lowfare_calendar_auto_chunks_across_windows(example):
    # 10 days spans more than one 7-day window; the default calendar stitches them.
    fares = example.lowfare_calendar("AAA", "BBB", "2026-01-01", "2026-01-10")
    assert len(fares) == 10


def test_flights_are_nonstop(example):
    flights = example.flights("AAA", "BBB", "2026-01-01")
    assert len(flights) == 1
    assert flights[0].is_nonstop
    assert flights[0].cheapest_cash == 19.0


def test_unknown_market_raises(example):
    with pytest.raises(MarketNotFoundError):
        example.lowfare_window("AAA", "ZZZ", _dt.date(2026, 1, 1), _dt.date(2026, 1, 1))
    with pytest.raises(MarketNotFoundError):
        example.flights("AAA", "ZZZ", "2026-01-01")
