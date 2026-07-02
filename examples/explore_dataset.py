"""Explore / export a crawled us_lowfares.db dataset.

Usage:
    python examples/explore_dataset.py [path_to_db]

Prints headline stats and writes best_deals.csv (cheapest cash fare per route).
"""

import csv
import sqlite3
import sys


def main(db_path: str = "us_lowfares.db") -> None:
    conn = sqlite3.connect(db_path)
    q = conn.execute

    total = q("SELECT COUNT(*) FROM lowfares").fetchone()[0]
    routes = q("SELECT COUNT(DISTINCT origin || dest) FROM lowfares").fetchone()[0]
    lo, hi = q("SELECT MIN(date), MAX(date) FROM lowfares").fetchone()
    priced = q("SELECT COUNT(*) FROM lowfares WHERE discounted_fare IS NOT NULL").fetchone()[0]

    print("=== Frontier US low-fare dataset ===")
    print(f"  rows        : {total:,}")
    print(f"  routes      : {routes:,}")
    print(f"  date range  : {lo} .. {hi}")
    print(f"  priced rows : {priced:,}")

    print("\n=== 15 cheapest cash fares ===")
    rows = q(
        "SELECT origin, dest, date, discounted_fare, total_miles "
        "FROM lowfares WHERE discounted_fare IS NOT NULL "
        "ORDER BY discounted_fare LIMIT 15"
    ).fetchall()
    for o, d, dt, fare, miles in rows:
        print(f"  {o}-{d}  {dt}  ${fare:>6.2f}  {miles} mi")

    # Export: cheapest cash fare per route
    best = q(
        "SELECT origin, dest, MIN(discounted_fare) AS best_fare "
        "FROM lowfares WHERE discounted_fare IS NOT NULL "
        "GROUP BY origin, dest ORDER BY best_fare"
    ).fetchall()
    out = "best_deals.csv"
    with open(out, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["origin", "dest", "cheapest_cash_fare"])
        w.writerows(best)
    print(f"\nWrote {len(best)} routes -> {out}")

    conn.close()


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "us_lowfares.db")
