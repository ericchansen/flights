"""Export the crawled low-fare SQLite dataset to a compact JSON snapshot for the
web map (`web/public/data.json`).

Reads the `airports` and `lowfares` tables produced by `flights ... crawl`,
converts the DMS coordinate strings (e.g. "333812N" / "0842541W") to decimal
degrees, and aggregates, for every origin->destination market:

  * the cheapest cash fare and cheapest award-miles cost overall, and
  * per-date cheapest cash / miles arrays aligned to `meta.dates`,

so the front-end can rank deals, color arcs, drive a date-window slider, and show
a route's day-by-day fares with no server and no live API calls.

Usage:
    python web/build_data.py [path_to_db] [-o out.json]

Defaults: db = D:\\flights-data\\us_lowfares.db, out = web/public/data.json
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
from datetime import datetime, timezone

DEFAULT_DB = r"D:\flights-data\us_lowfares.db"


def dms_to_decimal(raw: str | None) -> float | None:
    """Convert a packed DMS string like '333812N' / '0842541W' to decimal degrees.

    Format is DD..MMSS + hemisphere letter (last char). Longitude is zero-padded
    to three degree digits. West/South are negative.
    """
    if not raw:
        return None
    raw = raw.strip()
    if len(raw) < 5:
        return None
    hemi = raw[-1].upper()
    digits = raw[:-1]
    try:
        seconds = int(digits[-2:])
        minutes = int(digits[-4:-2])
        degrees = int(digits[:-4])
    except ValueError:
        return None
    value = degrees + minutes / 60 + seconds / 3600
    if hemi in ("S", "W"):
        value = -value
    return round(value, 5)


def _table_exists(q, table: str) -> bool:
    row = q(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    return row is not None


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("db", nargs="?", default=DEFAULT_DB, help="path to crawl SQLite DB")
    here = os.path.dirname(os.path.abspath(__file__))
    ap.add_argument(
        "-o",
        "--out",
        default=os.path.join(here, "public", "data.json"),
        help="output JSON path",
    )
    args = ap.parse_args()

    conn = sqlite3.connect(args.db)
    q = conn.execute

    provider = "frontier"
    if _table_exists(q, "crawl_meta"):
        row = q("SELECT value FROM crawl_meta WHERE key='provider'").fetchone()
        if row and row[0]:
            provider = row[0]

    dates = [r[0] for r in q("SELECT DISTINCT date FROM lowfares ORDER BY date").fetchall()]
    date_index = {d: i for i, d in enumerate(dates)}
    n_dates = len(dates)

    cheapest_expr = (
        "MIN(COALESCE(standard_fare,1e9),COALESCE(discounted_fare,1e9),"
        "COALESCE(saver_fare,1e9))"
    )

    # --- airports (parse coords) -------------------------------------------
    airports: dict[str, dict] = {}
    for code, city, name, cc, state, lat, lon in q(
        "SELECT code, city, full_name, country_code, state_code, lat, long "
        "FROM airports"
    ).fetchall():
        dlat, dlon = dms_to_decimal(lat), dms_to_decimal(lon)
        if dlat is None or dlon is None:
            continue
        airports[code] = {
            "code": code,
            "city": city,
            "name": name,
            "country": cc,
            "state": state,
            "lat": dlat,
            "lon": dlon,
        }

    # --- per-market nonstop flag (from the crawler's routes table) ---------
    # 1 = market offers nonstop service, 0 = connecting-only, absent = unknown
    # (older DB, or a market the nonstop probe never resolved).
    nonstop_by_pair: dict[tuple[str, str], int] = {}
    if _table_exists(q, "routes"):
        route_cols = {r[1] for r in q("PRAGMA table_info(routes)").fetchall()}
        if "nonstop" in route_cols:
            for o, d, ns in q(
                "SELECT origin, destination, nonstop FROM routes WHERE nonstop IS NOT NULL"
            ).fetchall():
                nonstop_by_pair[(o, d)] = int(ns)

    # --- routes: per (origin,dest,date) cheapest cash + miles --------------
    routes: dict[tuple[str, str], dict] = {}
    rows = q(
        f"SELECT origin, destination, date, {cheapest_expr} AS cash, "
        f"MIN(CASE WHEN miles>0 THEN miles END) AS miles, "
        f"MIN(miles_fees) AS fees "
        f"FROM lowfares GROUP BY origin, destination, date"
    ).fetchall()

    for origin, dest, date, cash, miles, fees in rows:
        if origin not in airports or dest not in airports:
            continue
        di = date_index.get(date)
        if di is None:
            continue
        key = (origin, dest)
        r = routes.get(key)
        if r is None:
            r = routes[key] = {
                "o": origin,
                "d": dest,
                "cashByDate": [None] * n_dates,
                "milesByDate": [None] * n_dates,
                "fees": None,
                "nonstop": nonstop_by_pair.get(key),
            }
        if cash is not None and cash < 1e9:
            r["cashByDate"][di] = round(cash, 2)
        if miles is not None and miles > 0:
            r["milesByDate"][di] = int(miles)
            if fees is not None and (r["fees"] is None or fees < r["fees"]):
                r["fees"] = round(fees, 2)

    # --- overall cheapest per route + best-date indices --------------------
    cash_vals: list[float] = []
    miles_vals: list[int] = []
    route_list = []
    for r in routes.values():
        cbd, mbd = r["cashByDate"], r["milesByDate"]
        cash_pairs = [(v, i) for i, v in enumerate(cbd) if v is not None]
        miles_pairs = [(v, i) for i, v in enumerate(mbd) if v is not None]
        if not cash_pairs and not miles_pairs:
            continue
        if cash_pairs:
            cash, ci = min(cash_pairs)
            r["cash"], r["cashDate"] = cash, ci
            cash_vals.append(cash)
        else:
            r["cash"], r["cashDate"] = None, None
        if miles_pairs:
            miles, mi = min(miles_pairs)
            r["miles"], r["milesDate"] = miles, mi
            miles_vals.append(miles)
        else:
            r["miles"], r["milesDate"] = None, None
        route_list.append(r)

    # Keep only airports that actually appear in a priced market. The source
    # `airports` table also lists ~39 international airports (Mexico, Caribbean,
    # Central America) that have zero rows in this US-domestic crawl; emitting
    # them would scatter meaningless dots across the map.
    served = {r["o"] for r in route_list} | {r["d"] for r in route_list}
    airports = {code: a for code, a in airports.items() if code in served}

    meta = {
        "provider": provider,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "dates": dates,
        "date_min": dates[0] if dates else None,
        "date_max": dates[-1] if dates else None,
        "cash_min": round(min(cash_vals), 2) if cash_vals else None,
        "cash_max": round(max(cash_vals), 2) if cash_vals else None,
        "miles_min": min(miles_vals) if miles_vals else None,
        "miles_max": max(miles_vals) if miles_vals else None,
        "n_airports": len(airports),
        "n_routes": len(route_list),
        "n_origins": len({r["o"] for r in route_list}),
        "n_nonstop": sum(1 for r in route_list if r.get("nonstop") == 1),
    }

    payload = {
        "meta": meta,
        "airports": sorted(airports.values(), key=lambda a: a["code"]),
        "routes": route_list,
    }

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, separators=(",", ":"), ensure_ascii=False)

    size_kb = os.path.getsize(args.out) / 1024
    print(f"Wrote {args.out}  ({size_kb:,.0f} KB)")
    print(
        f"  airports={meta['n_airports']}  routes={meta['n_routes']}  "
        f"origins={meta['n_origins']}  nonstop={meta['n_nonstop']}  dates={n_dates} "
        f"({meta['date_min']}..{meta['date_max']})"
    )
    print(
        f"  cash ${meta['cash_min']}..${meta['cash_max']}  "
        f"miles {meta['miles_min']}..{meta['miles_max']}"
    )
    conn.close()


if __name__ == "__main__":
    main()
