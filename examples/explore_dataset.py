"""Explore / export a crawled dataset produced by `flights ... crawl`.

Usage:
    python examples/explore_dataset.py [path_to_db]

Prints headline stats and writes best_deals.csv (cheapest cash fare per route).
Works with the multi-provider schema (the `lowfares` table has a `provider`
column and cash tiers standard_fare / discounted_fare / saver_fare).
"""

import csv
import sqlite3
import sys


def main(db_path: str = "flights.db") -> None:
    conn = sqlite3.connect(db_path)
    q = conn.execute

    total = q("SELECT COUNT(*) FROM lowfares").fetchone()[0]
    routes = q(
        "SELECT COUNT(DISTINCT provider||'|'||origin||'-'||destination) FROM lowfares"
    ).fetchone()[0]
    lo, hi = q("SELECT MIN(date), MAX(date) FROM lowfares").fetchone()
    providers = [r[0] for r in q("SELECT DISTINCT provider FROM lowfares").fetchall()]

    # cheapest of the cash tiers, per row
    cheapest = (
        "MIN(COALESCE(standard_fare,1e9), COALESCE(discounted_fare,1e9), COALESCE(saver_fare,1e9))"
    )

    print("=== Flight low-fare dataset ===")
    print(f"  providers   : {', '.join(providers)}")
    print(f"  rows        : {total:,}")
    print(f"  routes      : {routes:,}")
    print(f"  date range  : {lo} .. {hi}")

    print("\n=== 15 cheapest cash fares ===")
    rows = q(
        f"SELECT provider, origin, destination, date, {cheapest} AS fare, miles "
        f"FROM lowfares WHERE {cheapest} < 1e9 ORDER BY fare LIMIT 15"
    ).fetchall()
    for prov, o, d, dt, fare, miles in rows:
        print(f"  [{prov}] {o}-{d}  {dt}  ${fare:>6.2f}  {miles} mi")

    best = q(
        f"SELECT provider, origin, destination, MIN({cheapest}) AS best_fare "
        f"FROM lowfares WHERE {cheapest} < 1e9 "
        f"GROUP BY provider, origin, destination ORDER BY best_fare"
    ).fetchall()
    out = "best_deals.csv"
    with open(out, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["provider", "origin", "destination", "cheapest_cash_fare"])
        w.writerows(best)
    print(f"\nWrote {len(best)} routes -> {out}")

    conn.close()


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "flights.db")
