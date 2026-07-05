"""Generic, resumable, concurrent network crawler.

Works against any :class:`~flights.core.provider.BaseProvider`: it discovers the
US route map, then fetches each 7-day low-fare window concurrently, committing
to SQLite as it goes. Provider-specific details (endpoints, auth) live behind
``provider.lowfare_window`` / ``provider.destinations``.

Crash-safe & resumable: every window is recorded in ``crawl_windows``; a re-run
skips finished windows. Invalid markets are cached in ``routes`` and skipped.
"""

from __future__ import annotations

import datetime as _dt
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Iterable, Optional

from . import storage
from .errors import MarketNotFoundError, ProviderError
from .provider import BaseProvider


class Crawler:
    def __init__(
        self,
        provider: BaseProvider,
        db_path: str,
        workers: int = 8,
    ) -> None:
        self.provider = provider
        self.db_path = db_path
        self.workers = workers

        self._db_lock = threading.Lock()
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        storage.init_db(self._conn)

        self.stat_rows = 0
        self.stat_windows = 0
        self.stat_nomarket = 0
        self.stat_errors = 0
        self.stat_nonstop_yes = 0
        self.stat_nonstop_no = 0

    # ------------------------------------------------------------------ #
    # reference data                                                     #
    # ------------------------------------------------------------------ #

    def build_us_routes(self, origins: Optional[Iterable[str]] = None) -> list[tuple[str, str]]:
        origin_airports = self.provider.origins()
        with self._db_lock:
            self._conn.executemany(
                storage.insert_sql("airports", storage.AIRPORT_COLUMNS, mode="INSERT OR REPLACE"),
                [storage.airport_row(a) for a in origin_airports],
            )
            self._conn.commit()

        us_origins = (
            list(origins) if origins else [a.code for a in origin_airports if a.is_domestic_us]
        )
        pairs: list[tuple[str, str]] = []
        prov = self.provider.name
        for i, o in enumerate(us_origins, 1):
            dests = [d for d in self.provider.destinations(o) if d.country_code == "US"]
            with self._db_lock:
                self._conn.executemany(
                    storage.insert_sql(
                        "airports", storage.AIRPORT_COLUMNS, mode="INSERT OR IGNORE"
                    ),
                    [storage.airport_row(d) for d in dests],
                )
                self._conn.executemany(
                    storage.insert_sql("routes", storage.ROUTE_COLUMNS, mode="INSERT OR IGNORE"),
                    [(prov, o, d.code, None, None, None) for d in dests],
                )
                self._conn.commit()
            pairs.extend((o, d.code) for d in dests)
            print(f"  routes: [{i}/{len(us_origins)}] {o} -> {len(dests)} US dests", flush=True)
        return pairs

    # ------------------------------------------------------------------ #
    # planning                                                           #
    # ------------------------------------------------------------------ #

    def _windows(self, begin: _dt.date, end: _dt.date) -> list[_dt.date]:
        out, cur = [], begin
        while cur <= end:
            out.append(cur)
            cur += _dt.timedelta(days=self.provider.lowfare_window_days)
        return out

    def _done_windows(self) -> dict[tuple[str, str, str], str]:
        """Map (origin, dest, window_begin) -> the window_end already covered."""
        with self._db_lock:
            rows = self._conn.execute(
                "SELECT origin, destination, window_begin, window_end FROM crawl_windows "
                "WHERE provider=? AND status='done'",
                (self.provider.name,),
            ).fetchall()
        return {(o, d, wb): we for (o, d, wb, we) in rows}

    def _invalid_markets(self) -> set[tuple[str, str]]:
        with self._db_lock:
            rows = self._conn.execute(
                "SELECT origin, destination FROM routes WHERE provider=? AND valid=0",
                (self.provider.name,),
            ).fetchall()
        return set(rows)

    def _probed_markets(self) -> set[tuple[str, str]]:
        """Markets whose nonstop status is already known (skip on resume)."""
        with self._db_lock:
            rows = self._conn.execute(
                "SELECT origin, destination FROM routes "
                "WHERE provider=? AND nonstop IS NOT NULL",
                (self.provider.name,),
            ).fetchall()
        return set(rows)

    # ------------------------------------------------------------------ #
    # nonstop probe                                                      #
    # ------------------------------------------------------------------ #

    def _sample_dates(self, begin: _dt.date, end: _dt.date) -> list[str]:
        """1-2 representative dates in the range to probe for nonstop service."""
        span = (end - begin).days
        if span <= 0:
            return [begin.isoformat()]
        mid = begin + _dt.timedelta(days=span // 2)
        return [d.isoformat() for d in sorted({begin, mid})]

    def _probe_one(self, origin: str, dest: str, sample_dates: list[str]):
        """Return 1 if any nonstop trip exists, 0 if trips exist but none are
        nonstop, or None if service could not be determined (no trips / errors).

        Uses ``flights(nonstop_only=False)`` so we can distinguish
        "connecting-only" (0) from "no service on the sampled day" (unknown).
        """
        result = None
        for date in sample_dates:
            try:
                trips = self.provider.flights(origin, dest, date, nonstop_only=False)
            except MarketNotFoundError:
                return None
            except Exception:  # noqa: BLE001 - keep the probe alive
                continue
            if trips:
                if any(t.is_nonstop for t in trips):
                    return 1
                result = 0
        return result

    def _store_nonstop(self, origin: str, dest: str, nonstop) -> None:
        if nonstop is None:
            return  # leave NULL (unknown) so a later run re-probes it
        now = _dt.datetime.now(_dt.UTC).isoformat()
        prov = self.provider.name
        with self._db_lock:
            self._conn.execute(
                storage.ROUTE_NONSTOP_UPSERT_SQL,
                (prov, origin, dest, None, now, nonstop),
            )
            self._conn.commit()
        if nonstop:
            self.stat_nonstop_yes += 1
        else:
            self.stat_nonstop_no += 1

    def _probe_pass(self, pairs: list[tuple[str, str]], begin: _dt.date, end: _dt.date) -> None:
        """Populate ``routes.nonstop`` for every valid, not-yet-probed market."""
        invalid = self._invalid_markets()
        probed = self._probed_markets()
        todo = [(o, d) for (o, d) in pairs if (o, d) not in invalid and (o, d) not in probed]
        if not todo:
            print("\nNonstop probe: nothing to do (all markets already probed).", flush=True)
            return
        sample_dates = self._sample_dates(begin, end)
        total = len(todo)
        print(
            f"\nProbing nonstop service for {total} market(s) on "
            f"{len(sample_dates)} sample date(s) ({', '.join(sample_dates)})...",
            flush=True,
        )
        start = time.time()
        completed = 0
        with ThreadPoolExecutor(max_workers=self.workers) as ex:
            futures = {ex.submit(self._probe_one, o, d, sample_dates): (o, d) for (o, d) in todo}
            try:
                for fut in as_completed(futures):
                    o, d = futures[fut]
                    self._store_nonstop(o, d, fut.result())
                    completed += 1
                    if completed % 50 == 0 or completed == total:
                        elapsed = time.time() - start
                        rate = completed / elapsed if elapsed else 0
                        eta = (total - completed) / rate if rate else 0
                        print(
                            f"  nonstop {completed}/{total} ({completed*100//total}%) | "
                            f"{rate:.1f}/s | yes={self.stat_nonstop_yes} "
                            f"no={self.stat_nonstop_no} | ETA {_fmt(eta)}",
                            flush=True,
                        )
            except KeyboardInterrupt:
                ex.shutdown(wait=False, cancel_futures=True)
                print(
                    "\nNonstop probe interrupted - progress saved. Re-run to resume.",
                    flush=True,
                )

    # ------------------------------------------------------------------ #
    # worker + storage                                                   #
    # ------------------------------------------------------------------ #

    def _fetch(self, origin: str, dest: str, begin: _dt.date, end: _dt.date):
        try:
            fares = self.provider.lowfare_window(origin, dest, begin, end)
            return ("ok", fares)
        except MarketNotFoundError:
            return ("nomarket", [])
        except ProviderError:
            return ("error", [])
        except Exception:  # noqa: BLE001 - keep the crawl alive on unexpected errors
            return ("error", [])

    def _store(
        self, origin: str, dest: str, begin: _dt.date, end: _dt.date, status: str, fares: list
    ) -> None:
        now = _dt.datetime.now(_dt.UTC).isoformat()
        prov = self.provider.name
        with self._db_lock:
            if status == "ok":
                self._conn.executemany(
                    storage.insert_sql(
                        "lowfares", storage.LOWFARE_COLUMNS, mode="INSERT OR REPLACE"
                    ),
                    [storage.lowfare_row(prov, f, now) for f in fares],
                )
                self.stat_rows += len(fares)
                self.stat_windows += 1
            elif status == "nomarket":
                self._conn.execute(
                    storage.insert_sql("routes", storage.ROUTE_COLUMNS, mode="INSERT OR REPLACE"),
                    (prov, origin, dest, 0, now, None),
                )
                self.stat_nomarket += 1
            else:
                self.stat_errors += 1

            win_status = "done" if status in ("ok", "nomarket") else "error"
            self._conn.execute(
                storage.insert_sql(
                    "crawl_windows", storage.CRAWL_WINDOW_COLUMNS, mode="INSERT OR REPLACE"
                ),
                (prov, origin, dest, begin.isoformat(), end.isoformat(), win_status, now),
            )
            self._conn.commit()

    # ------------------------------------------------------------------ #
    # driver                                                             #
    # ------------------------------------------------------------------ #

    def crawl(
        self,
        begin_date: str,
        end_date: str,
        origins: Optional[Iterable[str]] = None,
        probe_nonstop: bool = True,
    ) -> None:
        begin = _d(begin_date)
        end = _d(end_date)
        window_span = _dt.timedelta(days=self.provider.lowfare_window_days - 1)
        print(f"[{self.provider.name}] Discovering US route network...", flush=True)
        pairs = self.build_us_routes(origins)
        windows = self._windows(begin, end)
        done = self._done_windows()
        invalid = self._invalid_markets()

        # Each task carries its window_begin plus a window_end capped to the
        # requested end date, so a partial final window never fetches or stores
        # dates beyond what the user asked for. A window is skipped only if a
        # previous run already covered at least up to this window's end, so
        # extending the range later correctly re-fetches a formerly-capped
        # final window.
        tasks = []
        for o, d in pairs:
            if (o, d) in invalid:
                continue
            for w in windows:
                window_end = min(w + window_span, end)
                covered = done.get((o, d, w.isoformat()))
                if covered is not None and covered >= window_end.isoformat():
                    continue
                tasks.append((o, d, w, window_end))
        total = len(tasks)
        already = len(pairs) * len(windows) - total
        print(
            f"\nRoutes: {len(pairs)} | windows/route: {len(windows)} ({begin} .. {end})\n"
            f"Tasks to do: {total} ({already} already done/skipped) | Workers: {self.workers}",
            flush=True,
        )

        interrupted = False
        if total == 0:
            print("Nothing to do - low-fare crawl already complete for this range.")
        else:
            start = time.time()
            completed = 0
            with ThreadPoolExecutor(max_workers=self.workers) as ex:
                futures = {
                    ex.submit(self._fetch, o, d, w, we): (o, d, w, we) for (o, d, w, we) in tasks
                }
                try:
                    for fut in as_completed(futures):
                        o, d, w, we = futures[fut]
                        status, fares = fut.result()
                        self._store(o, d, w, we, status, fares)
                        completed += 1
                        if completed % 50 == 0 or completed == total:
                            elapsed = time.time() - start
                            rate = completed / elapsed if elapsed else 0
                            eta = (total - completed) / rate if rate else 0
                            print(
                                f"  {completed}/{total} ({completed*100//total}%) | "
                                f"{rate:.1f} win/s | rows={self.stat_rows} "
                                f"nomarket={self.stat_nomarket} err={self.stat_errors} | "
                                f"ETA {_fmt(eta)}",
                                flush=True,
                            )
                except KeyboardInterrupt:
                    # Cancel queued (not-yet-started) work so we stop promptly rather
                    # than draining the whole queue on context-manager exit.
                    interrupted = True
                    ex.shutdown(wait=False, cancel_futures=True)
                    print(
                        "\nInterrupted - progress saved. Re-run the same command to resume.",
                        flush=True,
                    )

        # Nonstop probe runs after the low-fare pass so it can skip markets the
        # crawl just proved invalid. Skipped on interrupt (it's resumable on the
        # next run). Both passes are independently resumable.
        if probe_nonstop and not interrupted:
            self._probe_pass(pairs, begin, end)
        self._report()

    def _report(self) -> None:
        with self._db_lock:
            rows = self._conn.execute(
                "SELECT COUNT(*) FROM lowfares WHERE provider=?", (self.provider.name,)
            ).fetchone()[0]
            routes = self._conn.execute(
                "SELECT COUNT(DISTINCT origin||'-'||destination) FROM lowfares WHERE provider=?",
                (self.provider.name,),
            ).fetchone()[0]
            nomarket = self._conn.execute(
                "SELECT COUNT(*) FROM routes WHERE provider=? AND valid=0", (self.provider.name,)
            ).fetchone()[0]
            nonstop_yes, nonstop_no = self._conn.execute(
                "SELECT "
                "SUM(CASE WHEN nonstop=1 THEN 1 ELSE 0 END), "
                "SUM(CASE WHEN nonstop=0 THEN 1 ELSE 0 END) "
                "FROM routes WHERE provider=?",
                (self.provider.name,),
            ).fetchone()
        print(
            f"\nDataset: {rows} fare-rows across {routes} routes "
            f"({nomarket} invalid markets skipped) -> {self.db_path}",
            flush=True,
        )
        if nonstop_yes or nonstop_no:
            print(
                f"Nonstop service: {nonstop_yes or 0} market(s) nonstop, "
                f"{nonstop_no or 0} connecting-only.",
                flush=True,
            )

    def close(self) -> None:
        self._conn.close()


def _d(s: str) -> _dt.date:
    return _dt.datetime.strptime(s[:10], "%Y-%m-%d").date()


def _fmt(seconds: float) -> str:
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h{m:02d}m"
    if m:
        return f"{m}m{s:02d}s"
    return f"{s}s"
