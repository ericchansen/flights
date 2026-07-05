"""Tests for CLI helpers."""

from dataclasses import asdict

from flights.cli import _cheapest
from flights.core.models import DayFare


def test_cli_cheapest_matches_model_property():
    # The row-dict sort key must agree with the model's cheapest_cash rule, so
    # the two dedup'd code paths can never diverge.
    fare = DayFare("frontier", "DEN", "LAS", "2025-01-01", standard_fare=120.0, saver_fare=49.0)
    row = asdict(fare)
    assert _cheapest(row) == fare.cheapest_cash == 49.0


def test_cli_cheapest_all_none_is_none():
    fare = DayFare("frontier", "DEN", "LAS", "2025-01-01")
    assert _cheapest(asdict(fare)) is None
