"""Tests for the schema single-source-of-truth module."""

import sqlite3
from dataclasses import fields

from flights.core import storage
from flights.core.models import Airport, DayFare


def _table_columns(conn: sqlite3.Connection, table: str) -> list[str]:
    return [r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]


def test_init_db_creates_every_table_and_index():
    conn = sqlite3.connect(":memory:")
    storage.init_db(conn)

    tables = {
        r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    assert {"airports", "routes", "lowfares", "crawl_windows", "crawl_meta"} <= tables

    indexes = {
        r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='index'").fetchall()
    }
    assert {"idx_lowfares_date", "idx_lowfares_route"} <= indexes

    # A fresh DB already has the nonstop column (no migration needed).
    assert "nonstop" in _table_columns(conn, "routes")


def test_migrate_adds_nonstop_to_legacy_db_and_is_idempotent():
    conn = sqlite3.connect(":memory:")
    # Simulate a pre-nonstop routes table.
    conn.execute(
        "CREATE TABLE routes (provider TEXT, origin TEXT, destination TEXT, "
        "valid INTEGER, last_checked TEXT, PRIMARY KEY (provider, origin, destination))"
    )
    assert "nonstop" not in _table_columns(conn, "routes")

    storage.migrate(conn)
    assert "nonstop" in _table_columns(conn, "routes")

    # Running again must not raise (column already present).
    storage.migrate(conn)
    assert _table_columns(conn, "routes").count("nonstop") == 1


def test_insert_sql_builds_parameterized_statement():
    assert storage.insert_sql("airports", ("code", "city")) == (
        "INSERT INTO airports (code,city) VALUES (?,?)"
    )
    assert storage.insert_sql("routes", ("a", "b"), mode="INSERT OR REPLACE") == (
        "INSERT OR REPLACE INTO routes (a,b) VALUES (?,?)"
    )
    assert storage.insert_sql("routes", ("a",), mode="INSERT OR IGNORE") == (
        "INSERT OR IGNORE INTO routes (a) VALUES (?)"
    )


def test_column_tuples_match_dataclass_fields():
    # Drift guard: the schema's airport columns must equal the model's fields.
    assert storage.AIRPORT_COLUMNS == tuple(f.name for f in fields(Airport))
    # lowfares == DayFare fields (minus the non-persisted `extra`) plus scraped_at.
    dayfare_cols = tuple(f.name for f in fields(DayFare) if f.name != "extra")
    assert storage.LOWFARE_COLUMNS == (*dayfare_cols, "scraped_at")


def test_airport_row_is_in_column_order():
    a = Airport(
        code="DEN",
        city="Denver",
        full_name="Denver Intl",
        country_code="US",
        country_name="United States",
        state_code="CO",
        lat="394918N",
        long="1044016W",
    )
    row = storage.airport_row(a)
    assert row == (
        "DEN",
        "Denver",
        "Denver Intl",
        "US",
        "United States",
        "CO",
        "394918N",
        "1044016W",
    )
    assert len(row) == len(storage.AIRPORT_COLUMNS)


def test_lowfare_row_uses_supplied_provider_and_scraped_at():
    f = DayFare(
        provider="ignored-on-row",
        origin="DEN",
        destination="LAS",
        date="2026-08-05",
        standard_fare=59.0,
        discounted_fare=None,
        saver_fare=19.0,
        miles=None,
        miles_fees=None,
        currency="USD",
    )
    row = storage.lowfare_row("frontier", f, "2026-01-01T00:00:00+00:00")
    assert row == (
        "frontier",
        "DEN",
        "LAS",
        "2026-08-05",
        59.0,
        None,
        19.0,
        None,
        None,
        "USD",
        "2026-01-01T00:00:00+00:00",
    )
    assert len(row) == len(storage.LOWFARE_COLUMNS)


def test_round_trip_insert_and_select():
    conn = sqlite3.connect(":memory:")
    storage.init_db(conn)

    a = Airport("DEN", "Denver", "Denver Intl", "US", "United States", "CO", "394918N", "1044016W")
    conn.execute(
        storage.insert_sql("airports", storage.AIRPORT_COLUMNS, mode="INSERT OR REPLACE"),
        storage.airport_row(a),
    )
    f = DayFare("frontier", "DEN", "LAS", "2026-08-05", standard_fare=59.0, saver_fare=19.0)
    conn.execute(
        storage.insert_sql("lowfares", storage.LOWFARE_COLUMNS, mode="INSERT OR REPLACE"),
        storage.lowfare_row("frontier", f, "2026-01-01T00:00:00+00:00"),
    )
    conn.commit()

    assert conn.execute("SELECT code, city FROM airports").fetchone() == ("DEN", "Denver")
    cheapest = conn.execute(f"SELECT {storage.CHEAPEST_CASH_SQL} FROM lowfares").fetchone()[0]
    assert cheapest == 19.0
