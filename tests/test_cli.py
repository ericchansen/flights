"""Tests for CLI helpers."""

import logging
from dataclasses import asdict

from flights.cli import _cheapest, _configure_crawl_logging
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


def test_configure_crawl_logging_sends_crawler_logs_to_stdout(capsys):
    pkg_logger = logging.getLogger("flights")
    original = list(pkg_logger.handlers)
    try:
        _configure_crawl_logging()
        # Idempotent: a second call must not stack duplicate handlers.
        _configure_crawl_logging()
        logging.getLogger("flights.core.crawl").info("hello from the crawler")
        out = capsys.readouterr().out
        assert out == "hello from the crawler\n"
        assert sum(getattr(h, "_flights_cli", False) for h in pkg_logger.handlers) == 1
    finally:
        for h in list(pkg_logger.handlers):
            if h not in original:
                pkg_logger.removeHandler(h)
