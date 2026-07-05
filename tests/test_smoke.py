"""Smoke tests that prove the offline test harness itself works."""

import sqlite3

from _frontier import BFF_ENDPOINT, origins_payload

EXPECTED_TABLES = {"airports", "routes", "lowfares", "crawl_windows", "crawl_meta"}


def test_schema_db_creates_all_tables(schema_db):
    conn = sqlite3.connect(schema_db)
    try:
        names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        conn.close()
    assert EXPECTED_TABLES <= names


def test_routes_table_has_nonstop_column(schema_db):
    conn = sqlite3.connect(schema_db)
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(routes)")}
    finally:
        conn.close()
    assert "nonstop" in cols


def test_frontier_provider_constructs_without_network(frontier):
    # No responses mock is active here; if __init__ hit the network this would
    # raise a ConnectionError instead of returning a usable provider.
    assert frontier.name == "frontier"


def test_origins_round_trip_offline(frontier, mocked_responses):
    mocked_responses.post(BFF_ENDPOINT, json=origins_payload("DEN", "MCO"), status=200)

    airports = frontier.origins()

    assert [a.code for a in airports] == ["DEN", "MCO"]
    assert all(a.is_domestic_us for a in airports)
    assert len(mocked_responses.calls) == 1
