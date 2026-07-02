"""Command-line scraper for Frontier flight data.

Examples
--------
Dump the full route map to CSV::

    python -m frontier_flights.cli routes --out routes.csv

Scan low-fare calendars for cheap direct-ish dates from a few origins::

    python -m frontier_flights.cli lowfares \
        --origins DEN,MCO,LAS --days 60 --out lowfares.csv --db frontier.db

Pull individual nonstop flights for specific routes/dates::

    python -m frontier_flights.cli flights \
        --routes DEN-LAS,DEN-PHX --date 2026-08-05 --nonstop --out flights.csv
"""

from __future__ import annotations

import argparse
import csv
import datetime as _dt
import sqlite3
import sys
from dataclasses import asdict, fields
from typing import Iterable, Optional

from .client import Airport, DayFare, Flight, FrontierClient, FrontierError
from .crawl import Crawler


# --------------------------------------------------------------------------- #
# output sinks                                                                #
# --------------------------------------------------------------------------- #


def _write_csv(path: str, rows: list, header: list[str]) -> None:
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=header)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k) for k in header})
    print(f"  wrote {len(rows)} rows -> {path}")


def _write_sqlite(db_path: str, table: str, rows: list, header: list[str]) -> None:
    conn = sqlite3.connect(db_path)
    try:
        cols = ", ".join(f'"{c}"' for c in header)
        placeholders = ", ".join(["?"] * len(header))
        conn.execute(f'CREATE TABLE IF NOT EXISTS "{table}" ({cols})')
        conn.executemany(
            f'INSERT INTO "{table}" ({cols}) VALUES ({placeholders})',
            [[r.get(c) for c in header] for r in rows],
        )
        conn.commit()
    finally:
        conn.close()
    print(f"  wrote {len(rows)} rows -> {db_path}::{table}")


def _emit(rows: list[dict], header: list[str], out: Optional[str], db: Optional[str], table: str) -> None:
    if not rows:
        print("  (no rows)")
    if out:
        _write_csv(out, rows, header)
    if db:
        _write_sqlite(db, table, rows, header)
    if not out and not db:
        # print to stdout as CSV
        w = csv.DictWriter(sys.stdout, fieldnames=header)
        w.writeheader()
        for r in rows[:50]:
            w.writerow({k: r.get(k) for k in header})
        if len(rows) > 50:
            print(f"... ({len(rows) - 50} more rows; use --out to save all)")


def _dataclass_header(cls) -> list[str]:
    return [f.name for f in fields(cls)]


# --------------------------------------------------------------------------- #
# commands                                                                    #
# --------------------------------------------------------------------------- #


def cmd_routes(client: FrontierClient, args: argparse.Namespace) -> None:
    origins = _split(args.origins) if args.origins else None
    print("Fetching route map...")
    rows: list[dict] = []
    origin_list = origins or [a.code for a in client.origins()]
    for i, o in enumerate(origin_list, 1):
        dests = client.destinations(o)
        for d in dests:
            rows.append(
                {
                    "origin": o,
                    "destination": d.code,
                    "dest_city": d.city,
                    "dest_country": d.country_code,
                }
            )
        print(f"  [{i}/{len(origin_list)}] {o}: {len(dests)} destinations")
    _emit(rows, ["origin", "destination", "dest_city", "dest_country"], args.out, args.db, "routes")


def cmd_lowfares(client: FrontierClient, args: argparse.Namespace) -> None:
    begin = args.begin or _dt.date.today().isoformat()
    end = args.end or (
        _dt.datetime.strptime(begin, "%Y-%m-%d").date() + _dt.timedelta(days=args.days - 1)
    ).isoformat()

    pairs = _resolve_pairs(client, args)
    header = _dataclass_header(DayFare)
    print(f"Scanning low-fare calendar for {len(pairs)} route(s), {begin}..{end}")

    all_rows: list[dict] = []
    for i, (o, d) in enumerate(pairs, 1):
        try:
            fares = client.lowfare_calendar(o, d, begin, end)
        except FrontierError as exc:
            print(f"  [{i}/{len(pairs)}] {o}-{d}: ERROR {exc}")
            continue
        rows = [asdict(f) for f in fares]
        if args.max_price is not None:
            rows = [r for r in rows if _cheapest(r) is not None and _cheapest(r) <= args.max_price]
        if args.max_miles is not None:
            rows = [r for r in rows if r["total_miles"] is not None and r["total_miles"] <= args.max_miles]
        all_rows.extend(rows)
        print(f"  [{i}/{len(pairs)}] {o}-{d}: {len(rows)} day(s) kept")

    all_rows.sort(key=lambda r: (_cheapest(r) if _cheapest(r) is not None else 1e9))
    _emit(all_rows, header, args.out, args.db, "lowfares")


def cmd_flights(client: FrontierClient, args: argparse.Namespace) -> None:
    pairs = _resolve_pairs(client, args)
    dates = _split(args.date) if args.date else [_dt.date.today().isoformat()]
    header = _dataclass_header(Flight)
    print(f"Fetching flights for {len(pairs)} route(s) x {len(dates)} date(s)"
          f"{' (nonstop only)' if args.nonstop else ''}")

    all_rows: list[dict] = []
    n = 0
    total = len(pairs) * len(dates)
    for (o, d) in pairs:
        for date in dates:
            n += 1
            try:
                flights = client.flights(o, d, date, nonstop_only=args.nonstop)
            except FrontierError as exc:
                print(f"  [{n}/{total}] {o}-{d} {date}: ERROR {exc}")
                continue
            rows = [asdict(f) for f in flights]
            if args.max_price is not None:
                rows = [r for r in rows if _cheapest(r) is not None and _cheapest(r) <= args.max_price]
            if args.max_miles is not None:
                rows = [r for r in rows if r["miles"] is not None and r["miles"] <= args.max_miles]
            all_rows.extend(rows)
            print(f"  [{n}/{total}] {o}-{d} {date}: {len(rows)} flight(s)")

    all_rows.sort(key=lambda r: (_cheapest(r) if _cheapest(r) is not None else 1e9))
    _emit(all_rows, header, args.out, args.db, "flights")


def cmd_crawl(client: FrontierClient, args: argparse.Namespace) -> None:
    begin = args.begin or _dt.date.today().isoformat()
    end = args.end or (
        _dt.datetime.strptime(begin, "%Y-%m-%d").date() + _dt.timedelta(days=args.days - 1)
    ).isoformat()
    origins = _split(args.origins) if args.origins else None
    crawler = Crawler(
        db_path=args.db,
        client=client,
        workers=args.workers,
        currency=args.currency,
    )
    try:
        crawler.crawl(begin, end, origins=origins)
    finally:
        crawler.close()


# --------------------------------------------------------------------------- #
# helpers                                                                     #
# --------------------------------------------------------------------------- #


def _split(s: str) -> list[str]:
    return [x.strip().upper() for x in s.split(",") if x.strip()]


def _cheapest(row: dict) -> Optional[float]:
    vals = [row.get(k) for k in ("standard_fare", "discounted_fare", "gowild_fare")]
    vals = [v for v in vals if v is not None]
    return min(vals) if vals else None


def _resolve_pairs(client: FrontierClient, args: argparse.Namespace) -> list[tuple[str, str]]:
    """Build the list of (origin, destination) pairs from CLI args."""
    if getattr(args, "routes", None):
        pairs = []
        for token in _split(args.routes):
            if "-" not in token:
                raise SystemExit(f"Bad --routes entry '{token}', expected ORIG-DEST")
            o, d = token.split("-", 1)
            pairs.append((o, d))
        return pairs

    if getattr(args, "origins", None):
        origins = _split(args.origins)
        if getattr(args, "destinations", None):
            dests = _split(args.destinations)
            return [(o, d) for o in origins for d in dests]
        # expand each origin to all its served destinations
        pairs = []
        for o in origins:
            for d in client.destinations(o):
                pairs.append((o, d.code))
        return pairs

    raise SystemExit("Specify --routes ORIG-DEST,... or --origins CODE,... [--destinations CODE,...]")


# --------------------------------------------------------------------------- #
# argument parsing                                                            #
# --------------------------------------------------------------------------- #


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="frontier_flights",
        description="Scrape Frontier Airlines flight availability (prices + miles).",
    )
    p.add_argument("--subscription-key", help="Override APIM key (else auto-fetched).")
    p.add_argument("--currency", default="USD")
    p.add_argument("--delay", type=float, default=0.3, help="Delay between requests (s).")
    sub = p.add_subparsers(dest="command", required=True)

    pr = sub.add_parser("routes", help="Dump the origin->destination route map.")
    pr.add_argument("--origins", help="Comma list of origins (default: all).")
    pr.add_argument("--out", help="CSV output path.")
    pr.add_argument("--db", help="SQLite output path.")
    pr.set_defaults(func=cmd_routes)

    pl = sub.add_parser("lowfares", help="Scan per-day cheapest fares + miles (calendar).")
    _add_route_args(pl)
    pl.add_argument("--begin", help="Start date YYYY-MM-DD (default: today).")
    pl.add_argument("--end", help="End date YYYY-MM-DD.")
    pl.add_argument("--days", type=int, default=30, help="Days from --begin if --end omitted.")
    pl.add_argument("--max-price", type=float, help="Keep only days at/under this cash price.")
    pl.add_argument("--max-miles", type=int, help="Keep only days at/under this miles cost.")
    pl.add_argument("--out", help="CSV output path.")
    pl.add_argument("--db", help="SQLite output path.")
    pl.set_defaults(func=cmd_lowfares)

    pf = sub.add_parser("flights", help="Fetch individual flights (with stops) for dates.")
    _add_route_args(pf)
    pf.add_argument("--date", help="Comma list of dates YYYY-MM-DD (default: today).")
    pf.add_argument("--nonstop", action="store_true", help="Only direct (0-stop) flights.")
    pf.add_argument("--max-price", type=float, help="Keep only flights at/under this cash price.")
    pf.add_argument("--max-miles", type=int, help="Keep only flights at/under this miles cost.")
    pf.add_argument("--out", help="CSV output path.")
    pf.add_argument("--db", help="SQLite output path.")
    pf.set_defaults(func=cmd_flights)

    pc = sub.add_parser(
        "crawl",
        help="Network-wide resumable crawl of US low-fares into SQLite.",
    )
    pc.add_argument("--db", required=True, help="SQLite output path (resumable).")
    pc.add_argument("--begin", help="Start date YYYY-MM-DD (default: today).")
    pc.add_argument("--end", help="End date YYYY-MM-DD.")
    pc.add_argument("--days", type=int, default=60, help="Days from --begin if --end omitted.")
    pc.add_argument("--origins", help="Comma list of US origins (default: all US).")
    pc.add_argument("--workers", type=int, default=8, help="Concurrent requests (default 8).")
    pc.set_defaults(func=cmd_crawl)

    return p


def _add_route_args(sp: argparse.ArgumentParser) -> None:
    sp.add_argument("--routes", help="Comma list of ORIG-DEST pairs, e.g. DEN-LAS,MCO-DEN.")
    sp.add_argument("--origins", help="Comma list of origins (expanded to all destinations).")
    sp.add_argument("--destinations", help="Comma list of destinations (paired with origins).")


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    client = FrontierClient(
        subscription_key=args.subscription_key,
        currency=args.currency,
        request_delay=args.delay,
    )
    try:
        args.func(client, args)
    except FrontierError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
