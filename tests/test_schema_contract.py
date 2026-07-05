"""Schema contract test: every consumer's queries must match the storage schema.

This is the permanent guard against the F1-class bug (a consumer selecting a
column the schema doesn't have). It builds a DB from ``storage`` — the single
source of truth — seeds one representative row per table, then exercises every
consumer of that DB:

* the crawler's own read helpers (real ``crawl.py`` SQL),
* ``examples/explore_dataset.py`` (run as a script), and
* ``web/build_data.py`` (run as a script).

If any consumer references a non-existent column, SQLite raises and the test
fails. ``tests/test_export.py`` owns the detailed export-shape assertions; here
we only prove each consumer *runs* against the current schema.
"""

import json
import sqlite3
import subprocess
import sys
from pathlib import Path

from flights.core import storage
from flights.core.crawl import Crawler
from flights.core.models import Airport, DayFare

REPO_ROOT = Path(__file__).resolve().parents[1]
BUILD_DATA = REPO_ROOT / "web" / "build_data.py"
EXPLORE = REPO_ROOT / "examples" / "explore_dataset.py"


class _StubProvider:
    """Minimal stand-in: the crawler read helpers only touch ``provider.name``."""

    name = "frontier"


def _seed(db_path: str) -> None:
    conn = sqlite3.connect(db_path)
    try:
        storage.init_db(conn)
        airports = [
            Airport(
                "DEN", "Denver", "Denver Intl", "US", "United States", "CO", "395142N", "1044023W"
            ),
            Airport(
                "LAS", "Las Vegas", "Reid Intl", "US", "United States", "NV", "360448N", "1150907W"
            ),
        ]
        conn.executemany(
            storage.insert_sql("airports", storage.AIRPORT_COLUMNS, mode="INSERT OR REPLACE"),
            [storage.airport_row(a) for a in airports],
        )
        fares = [
            DayFare("frontier", "DEN", "LAS", "2025-01-01", 120.0, None, 49.0, 10000, 5.6),
            DayFare("frontier", "LAS", "DEN", "2025-01-01", 80.0, 70.0, None, 12000, 5.6),
        ]
        conn.executemany(
            storage.insert_sql("lowfares", storage.LOWFARE_COLUMNS, mode="INSERT OR REPLACE"),
            [storage.lowfare_row("frontier", f, "t") for f in fares],
        )
        conn.executemany(
            storage.insert_sql("routes", storage.ROUTE_COLUMNS, mode="INSERT OR REPLACE"),
            [
                ("frontier", "DEN", "LAS", None, "t", 1),  # probed: nonstop
                ("frontier", "LAS", "DEN", None, "t", 0),  # probed: connecting-only
                ("frontier", "DEN", "MIA", 0, "t", None),  # invalid market
                ("frontier", "DEN", "SEA", None, "t", None),  # valid, not yet probed
            ],
        )
        conn.execute(
            storage.insert_sql(
                "crawl_windows", storage.CRAWL_WINDOW_COLUMNS, mode="INSERT OR REPLACE"
            ),
            ("frontier", "DEN", "LAS", "2025-01-01", "2025-01-07", "done", "t"),
        )
        conn.execute("INSERT INTO crawl_meta (key,value) VALUES ('provider','frontier')")
        conn.commit()
    finally:
        conn.close()


def test_crawler_read_helpers_match_schema(tmp_path):
    db = tmp_path / "us.db"
    _seed(str(db))

    crawler = Crawler(_StubProvider(), str(db), workers=1)
    try:
        assert crawler._invalid_markets() == {("DEN", "MIA")}
        assert crawler._probed_markets() == {("DEN", "LAS"), ("LAS", "DEN")}
        assert crawler._done_windows() == {("DEN", "LAS", "2025-01-01"): "2025-01-07"}
        crawler._report()  # exercises the report SELECTs; must not raise
    finally:
        crawler._conn.close()


def test_explore_dataset_runs_against_schema(tmp_path):
    db = tmp_path / "us.db"
    _seed(str(db))

    proc = subprocess.run(
        [sys.executable, str(EXPLORE), str(db)],
        capture_output=True,
        text=True,
        cwd=tmp_path,  # it writes best_deals.csv into the CWD
    )
    assert proc.returncode == 0, f"explore_dataset failed:\n{proc.stderr}"

    out = tmp_path / "best_deals.csv"
    assert out.exists()
    lines = out.read_text(encoding="utf-8").splitlines()
    assert lines[0] == "provider,origin,destination,cheapest_cash_fare"
    assert len(lines) == 3  # header + two routes


def test_build_data_runs_against_schema(tmp_path):
    db = tmp_path / "us.db"
    out = tmp_path / "data.json"
    _seed(str(db))

    proc = subprocess.run(
        [sys.executable, str(BUILD_DATA), str(db), "-o", str(out)],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, f"build_data failed:\n{proc.stderr}"

    payload = json.loads(out.read_text(encoding="utf-8"))
    assert "meta" in payload and "routes" in payload
    assert payload["meta"]["n_airports"] == 2
