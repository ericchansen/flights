"""Provider-aware command-line interface.

Every command takes ``--provider`` (default: ``frontier``) and writes CSV
(``--out``) and/or SQLite (``--db``); with neither, the first rows print to
stdout. Rows are sorted cheapest-first.

    python -m flights.cli routes   --provider frontier --out routes.csv
    python -m flights.cli lowfares --routes DEN-LAS --days 30 --out fares.csv
    python -m flights.cli flights  --routes DEN-LAS --date 2026-08-05 --nonstop
    python -m flights.cli crawl    --db data.db --days 30 --workers 8
"""

import argparse
import csv
import datetime as _dt
import logging
import sqlite3
import sys
from dataclasses import asdict, fields

from .core import (
    BaseProvider,
    Crawler,
    DayFare,
    Flight,
    FlightsError,
    available_providers,
    cheapest_cash,
    get_provider,
)

# --------------------------------------------------------------------------- #
# output sinks                                                                #
# --------------------------------------------------------------------------- #


def _log(*args) -> None:
    """Write human-facing progress/status to stderr so stdout stays pure CSV."""
    print(*args, file=sys.stderr, flush=True)


def _configure_crawl_logging() -> None:
    """Surface the crawler's ``logging`` progress on stdout.

    The crawler logs progress through ``logging`` (so it stays quiet when used
    as a library). The ``crawl`` command opts in to seeing it, emitting plain
    messages on stdout to match the tool's historical output.
    """
    pkg_logger = logging.getLogger("flights")
    pkg_logger.setLevel(logging.INFO)
    if not any(getattr(h, "_flights_cli", False) for h in pkg_logger.handlers):
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(logging.Formatter("%(message)s"))
        handler._flights_cli = True  # type: ignore[attr-defined]
        pkg_logger.addHandler(handler)


def _write_csv(path: str, rows: list, header: list[str]) -> None:
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=header, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    _log(f"  wrote {len(rows)} rows -> {path}")


def _write_sqlite(db_path: str, table: str, rows: list, header: list[str]) -> None:
    conn = sqlite3.connect(db_path)
    try:
        cols = ", ".join(f'"{c}"' for c in header)
        conn.execute(f'CREATE TABLE IF NOT EXISTS "{table}" ({cols})')
        conn.executemany(
            f'INSERT INTO "{table}" ({cols}) VALUES ({", ".join(["?"] * len(header))})',
            [[r.get(c) for c in header] for r in rows],
        )
        conn.commit()
    finally:
        conn.close()
    _log(f"  wrote {len(rows)} rows -> {db_path}::{table}")


def _emit(rows: list[dict], header: list[str], out, db, table: str) -> None:
    if not rows:
        _log("  (no rows)")
    if out:
        _write_csv(out, rows, header)
    if db:
        _write_sqlite(db, table, rows, header)
    if not out and not db:
        w = csv.DictWriter(sys.stdout, fieldnames=header, extrasaction="ignore")
        w.writeheader()
        for r in rows[:50]:
            w.writerow(r)
        if len(rows) > 50:
            _log(f"... ({len(rows) - 50} more rows; use --out to save all)")


def _header(cls) -> list[str]:
    # drop the free-form 'extra' dict from tabular output
    return [f.name for f in fields(cls) if f.name != "extra"]


# --------------------------------------------------------------------------- #
# commands                                                                    #
# --------------------------------------------------------------------------- #


def cmd_routes(provider: BaseProvider, args) -> None:
    origins = _split(args.origins) if args.origins else provider.us_origins()
    _log(f"[{provider.name}] Fetching route map...")
    rows = []
    for i, o in enumerate(origins, 1):
        dests = provider.destinations(o)
        for d in dests:
            rows.append(
                {
                    "provider": provider.name,
                    "origin": o,
                    "destination": d.code,
                    "dest_city": d.city,
                    "dest_country": d.country_code,
                }
            )
        _log(f"  [{i}/{len(origins)}] {o}: {len(dests)} destinations")
    # Use a distinct table name so this route-map export never collides with the
    # crawler's `routes` market-validity cache when writing into a shared DB.
    _emit(
        rows,
        ["provider", "origin", "destination", "dest_city", "dest_country"],
        args.out,
        args.db,
        "route_map",
    )


def cmd_lowfares(provider: BaseProvider, args) -> None:
    begin = args.begin or _dt.date.today().isoformat()
    end = args.end or (_d(begin) + _dt.timedelta(days=args.days - 1)).isoformat()
    pairs = _resolve_pairs(provider, args)
    header = _header(DayFare)
    nonstop = getattr(args, "nonstop", False)
    sample_dates = _sample_dates(begin, end) if nonstop else []
    _log(
        f"[{provider.name}] Low-fare calendar: {len(pairs)} route(s), {begin}..{end}"
        f"{' (nonstop markets only)' if nonstop else ''}"
    )
    all_rows = []
    for i, (o, d) in enumerate(pairs, 1):
        if nonstop and not _market_has_nonstop(provider, o, d, sample_dates):
            _log(f"  [{i}/{len(pairs)}] {o}-{d}: skipped (no nonstop service)")
            continue
        try:
            fares = provider.lowfare_calendar(o, d, begin, end)
        except FlightsError as exc:
            _log(f"  [{i}/{len(pairs)}] {o}-{d}: ERROR {exc}")
            continue
        rows = [asdict(f) for f in fares]
        rows = _apply_filters(
            rows,
            args,
            cash_keys=("standard_fare", "discounted_fare", "saver_fare"),
            miles_key="miles",
        )
        all_rows.extend(rows)
        _log(f"  [{i}/{len(pairs)}] {o}-{d}: {len(rows)} day(s) kept")
    all_rows.sort(key=lambda r: c if (c := _cheapest(r)) is not None else 1e9)
    _emit(all_rows, header, args.out, args.db, "lowfares")


def cmd_flights(provider: BaseProvider, args) -> None:
    pairs = _resolve_pairs(provider, args)
    dates = _split(args.date) if args.date else [_dt.date.today().isoformat()]
    header = _header(Flight)
    _log(
        f"[{provider.name}] Flights: {len(pairs)} route(s) x {len(dates)} date(s)"
        f"{' (nonstop)' if args.nonstop else ''}"
    )
    all_rows, n, total = [], 0, len(pairs) * len(dates)
    for o, d in pairs:
        for date in dates:
            n += 1
            try:
                flights = provider.flights(o, d, date, nonstop_only=args.nonstop)
            except FlightsError as exc:
                _log(f"  [{n}/{total}] {o}-{d} {date}: ERROR {exc}")
                continue
            rows = [asdict(f) for f in flights]
            rows = _apply_filters(
                rows,
                args,
                cash_keys=("standard_fare", "discounted_fare", "saver_fare"),
                miles_key="miles",
            )
            all_rows.extend(rows)
            _log(f"  [{n}/{total}] {o}-{d} {date}: {len(rows)} flight(s)")
    all_rows.sort(key=lambda r: c if (c := _cheapest(r)) is not None else 1e9)
    _emit(all_rows, header, args.out, args.db, "flights")


def cmd_crawl(provider: BaseProvider, args) -> None:
    _configure_crawl_logging()
    begin = args.begin or _dt.date.today().isoformat()
    end = args.end or (_d(begin) + _dt.timedelta(days=args.days - 1)).isoformat()
    origins = _split(args.origins) if args.origins else None
    crawler = Crawler(provider, db_path=args.db, workers=args.workers)
    try:
        crawler.crawl(
            begin,
            end,
            origins=origins,
            probe_nonstop=not getattr(args, "skip_nonstop_probe", False),
        )
    finally:
        crawler.close()


# --------------------------------------------------------------------------- #
# helpers                                                                     #
# --------------------------------------------------------------------------- #


def _split(s: str) -> list[str]:
    return [x.strip().upper() for x in s.split(",") if x.strip()]


def _d(s: str) -> _dt.date:
    return _dt.datetime.strptime(s[:10], "%Y-%m-%d").date()


def _cheapest(row: dict) -> float | None:
    """Cheapest cash tier for a row-dict, via the shared model rule."""
    return cheapest_cash(*(row.get(k) for k in ("standard_fare", "discounted_fare", "saver_fare")))


def _sample_dates(begin: str, end: str) -> list[str]:
    """1-2 representative dates to probe a market for nonstop service."""
    b, e = _d(begin), _d(end)
    span = (e - b).days
    if span <= 0:
        return [b.isoformat()]
    mid = b + _dt.timedelta(days=span // 2)
    return [x.isoformat() for x in sorted({b, mid})]


def _market_has_nonstop(provider: BaseProvider, o: str, d: str, dates: list[str]) -> bool:
    """True if the market offers nonstop service on any sampled date.

    Uses ``flights(nonstop_only=False)`` and inspects ``is_nonstop`` so an empty
    result on one sampled day (no service) doesn't wrongly reject the market.
    """
    for date in dates:
        try:
            trips = provider.flights(o, d, date, nonstop_only=False)
        except FlightsError:
            continue
        if trips:
            return any(t.is_nonstop for t in trips)
    return False


def _apply_filters(rows, args, cash_keys, miles_key):
    if getattr(args, "max_price", None) is not None:
        rows = [r for r in rows if _cheapest(r) is not None and _cheapest(r) <= args.max_price]
    if getattr(args, "max_miles", None) is not None:
        rows = [r for r in rows if r.get(miles_key) is not None and r[miles_key] <= args.max_miles]
    return rows


def _resolve_pairs(provider: BaseProvider, args) -> list[tuple[str, str]]:
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
        return [(o, dd.code) for o in origins for dd in provider.destinations(o)]
    raise SystemExit(
        "Specify --routes ORIG-DEST,... or --origins CODE,... [--destinations CODE,...]"
    )


# --------------------------------------------------------------------------- #
# argument parsing                                                            #
# --------------------------------------------------------------------------- #


def _add_common_args(parser: argparse.ArgumentParser, suppress: bool) -> None:
    """Add the global options. When ``suppress`` is True the args carry no
    default (argparse.SUPPRESS), so a subparser copy only overrides the value
    when the user actually passes it after the subcommand."""
    parser.add_argument(
        "--provider",
        default=argparse.SUPPRESS if suppress else "frontier",
        help=f"Airline provider (default: frontier). Available: {', '.join(available_providers())}",
    )
    parser.add_argument(
        "--subscription-key",
        default=argparse.SUPPRESS if suppress else None,
        help="Provider API key override (else auto-fetched).",
    )
    parser.add_argument(
        "--currency",
        default=argparse.SUPPRESS if suppress else "USD",
    )


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="flights", description="Scrape airline flight availability (cash + miles)."
    )
    _add_common_args(p, suppress=False)

    # A parent parser lets the same global options be accepted *after* the
    # subcommand too (e.g. `flights crawl --provider foo`).
    common = argparse.ArgumentParser(add_help=False)
    _add_common_args(common, suppress=True)

    sub = p.add_subparsers(dest="command", required=True)

    pr = sub.add_parser("routes", parents=[common], help="Dump the origin->destination route map.")
    pr.add_argument("--origins", help="Comma list of origins (default: all US).")
    pr.add_argument("--out")
    pr.add_argument("--db")
    pr.set_defaults(func=cmd_routes)

    pl = sub.add_parser(
        "lowfares", parents=[common], help="Per-day cheapest fares + miles (calendar)."
    )
    _route_args(pl)
    pl.add_argument("--begin")
    pl.add_argument("--end")
    pl.add_argument("--days", type=int, default=30)
    pl.add_argument(
        "--nonstop",
        action="store_true",
        help="Only include markets that offer nonstop (continuous) service.",
    )
    pl.add_argument("--max-price", type=float)
    pl.add_argument("--max-miles", type=int)
    pl.add_argument("--out")
    pl.add_argument("--db")
    pl.set_defaults(func=cmd_lowfares)

    pf = sub.add_parser(
        "flights", parents=[common], help="Individual flights (with stops) for dates."
    )
    _route_args(pf)
    pf.add_argument("--date", help="Comma list of dates YYYY-MM-DD (default: today).")
    pf.add_argument("--nonstop", action="store_true")
    pf.add_argument("--max-price", type=float)
    pf.add_argument("--max-miles", type=int)
    pf.add_argument("--out")
    pf.add_argument("--db")
    pf.set_defaults(func=cmd_flights)

    pc = sub.add_parser("crawl", parents=[common], help="Network-wide resumable crawl into SQLite.")
    pc.add_argument("--db", required=True)
    pc.add_argument("--begin")
    pc.add_argument("--end")
    pc.add_argument("--days", type=int, default=60)
    pc.add_argument("--origins", help="Comma list of US origins (default: all US).")
    pc.add_argument("--workers", type=int, default=8)
    pc.add_argument(
        "--skip-nonstop-probe",
        action="store_true",
        help="Skip the per-market nonstop-service probe pass.",
    )
    pc.set_defaults(func=cmd_crawl)
    return p


def _route_args(sp) -> None:
    sp.add_argument("--routes", help="Comma list of ORIG-DEST, e.g. DEN-LAS,MCO-DEN.")
    sp.add_argument("--origins", help="Comma list of origins (expanded to all destinations).")
    sp.add_argument("--destinations", help="Comma list of destinations (paired with origins).")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        provider = get_provider(
            args.provider,
            subscription_key=args.subscription_key,
            currency=args.currency,
        )
    except TypeError:
        # provider that doesn't accept these kwargs
        provider = get_provider(args.provider)
    except FlightsError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    try:
        args.func(provider, args)
    except FlightsError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        return 130
    finally:
        provider.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
