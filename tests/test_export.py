"""End-to-end regression test for the web/build_data.py exporter.

This is the guard the F1 schema-drift bug never had: it builds a SQLite DB from
the crawler's canonical schema, runs the exporter exactly as a user would (as a
subprocess), and asserts the emitted JSON snapshot. If a consumer query ever
references a column the schema doesn't define, the exporter exits non-zero and
this test fails.
"""

import json
import sqlite3
import subprocess
import sys
from pathlib import Path

from flights.core import storage

REPO_ROOT = Path(__file__).resolve().parents[1]
BUILD_DATA = REPO_ROOT / "web" / "build_data.py"


def _seed(db_path):
    conn = sqlite3.connect(db_path)
    try:
        storage.init_db(conn)
        conn.executemany(
            "INSERT INTO airports "
            "(code, city, full_name, country_code, country_name, state_code, lat, long) "
            "VALUES (?,?,?,?,?,?,?,?)",
            [
                (
                    "DEN",
                    "Denver",
                    "Denver Intl",
                    "US",
                    "United States",
                    "CO",
                    "395142N",
                    "1044023W",
                ),
                (
                    "LAS",
                    "Las Vegas",
                    "Reid Intl",
                    "US",
                    "United States",
                    "NV",
                    "360448N",
                    "1150907W",
                ),
            ],
        )
        conn.execute(
            "INSERT INTO lowfares "
            "(provider,origin,destination,date,standard_fare,discounted_fare,saver_fare,"
            "miles,miles_fees,currency,scraped_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            ("frontier", "DEN", "LAS", "2025-01-01", 120.0, None, 49.0, 10000, 5.6, "USD", "t"),
        )
        conn.execute(
            "INSERT INTO routes (provider,origin,destination,valid,last_checked,nonstop) "
            "VALUES (?,?,?,?,?,?)",
            ("frontier", "DEN", "LAS", None, "t", 1),
        )
        conn.execute("INSERT INTO crawl_meta (key,value) VALUES ('provider','frontier')")
        conn.commit()
    finally:
        conn.close()


def test_build_data_exports_expected_snapshot(tmp_path):
    db = tmp_path / "us.db"
    out = tmp_path / "data.json"
    _seed(str(db))

    proc = subprocess.run(
        [sys.executable, str(BUILD_DATA), str(db), "-o", str(out)],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, f"exporter failed:\n{proc.stderr}"

    payload = json.loads(out.read_text(encoding="utf-8"))
    meta = payload["meta"]
    assert meta["n_airports"] == 2
    assert meta["n_routes"] == 1
    assert meta["n_origins"] == 1
    assert meta["n_nonstop"] == 1
    assert meta["dates"] == ["2025-01-01"]

    route = payload["routes"][0]
    assert route["o"] == "DEN"
    assert route["d"] == "LAS"
    assert route["cash"] == 49.0
    assert route["miles"] == 10000
    assert route["nonstop"] == 1
