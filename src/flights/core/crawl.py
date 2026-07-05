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
import logging
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Iterable, Optional

from . import storage
from .errors import MarketNotFoundError, ProviderError
from .provider import BaseProvider

logger = logging.getLogger(__name__)


class CrawlStore:
    """Owns the SQLite connection and every crawl read/write.

    Separating persistence from :class:`Crawler` keeps the orchestration
    (threading, window planning, the nonstop probe) free of SQL and lets each
    concern be tested on its own. Each method preserves the crawler's original
    locking and per-operation commit boundaries, so a crash mid-crawl leaves a
    consistent, resumable database.
    """

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        storage.init_db(self._conn)

    # -- writes -------------------------------------------------------------- #

    def upsert_airports(self, airports: Iterable, *, mode: str) -> None:
        rows = [storage.airport_row(a) for a in airports]
        with self._lock:
            self._conn.executemany(
                storage.insert_sql("airports", storage.AIRPORT_COLUMNS, mode=mode), rows
            )
            self._conn.commit()

    def add_destinations(self, provider: str, origin: str, dests: Iterable) -> None:
        """Register destination airports and their (initially unqualified) routes."""
        dest_list = list(dests)
        with self._lock:
            self._conn.executemany(
                storage.insert_sql("airports", storage.AIRPORT_COLUMNS, mode="INSERT OR IGNORE"),
                [storage.airport_row(d) for d in dest_list],
            )
            self._conn.executemany(
                storage.insert_sql("routes", storage.ROUTE_COLUMNS, mode="INSERT OR IGNORE"),
                [(provider, origin, d.code, None, None, None) for d in dest_list],
            )
            self._conn.commit()

    def record_window_result(
        self,
        provider: str,
        origin: str,
        dest: str,
        begin: _dt.date,
        end: _dt.date,
        status: str,
        fares: list,
        now: str,
    ) -> int:
        """Persist one window's outcome atomically; return the fare rows stored."""
        stored = 0
        with self._lock:
            if status == "ok":
                self._conn.executemany(
                    storage.insert_sql(
                        "lowfares", storage.LOWFARE_COLUMNS, mode="INSERT OR REPLACE"
                    ),
                    [storage.lowfare_row(provider, f, now) for f in fares],
                )
                stored = len(fares)
            elif status == "nomarket":
                self._conn.execute(
                    storage.insert_sql("routes", storage.ROUTE_COLUMNS, mode="INSERT OR REPLACE"),
                    (provider, origin, dest, 0, now, None),
                )

            win_status = "done" if status in ("ok", "nomarket") else "error"
            self._conn.execute(
                storage.insert_sql(
                    "crawl_windows", storage.CRAWL_WINDOW_COLUMNS, mode="INSERT OR REPLACE"
                ),
                (provider, origin, dest, begin.isoformat(), end.isoformat(), win_status, now),
            )
            self._conn.commit()
        return stored

    def upsert_nonstop(self, provider: str, origin: str, dest: str, now: str, nonstop: int) -> None:
        with self._lock:
            self._conn.execute(
                storage.ROUTE_NONSTOP_UPSERT_SQL, (provider, origin, dest, None, now, nonstop)
            )
            self._conn.commit()

    # -- reads --------------------------------------------------------------- #

    def done_windows(self, provider: str) -> dict[tuple[str, str, str], str]:
        """Map (origin, dest, window_begin) -> the window_end already covered."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT origin, destination, window_begin, window_end FROM crawl_windows "
                "WHERE provider=? AND status='done'",
                (provider,),
            ).fetchall()
        return {(o, d, wb): we for (o, d, wb, we) in rows}

    def invalid_markets(self, provider: str) -> set[tuple[str, str]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT origin, destination FROM routes WHERE provider=? AND valid=0",
                (provider,),
            ).fetchall()
        return set(rows)

    def probed_markets(self, provider: str) -> set[tuple[str, str]]:
        """Markets whose nonstop status is already known (skip on resume)."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT origin, destination FROM routes WHERE provider=? AND nonstop IS NOT NULL",
                (provider,),
            ).fetchall()
        return set(rows)

    def report_counts(self, provider: str) -> tuple[int, int, int, int | None, int | None]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT COUNT(*) FROM lowfares WHERE provider=?", (provider,)
            ).fetchone()[0]
            routes = self._conn.execute(
                "SELECT COUNT(DISTINCT origin||'-'||destination) FROM lowfares WHERE provider=?",
                (provider,),
            ).fetchone()[0]
            nomarket = self._conn.execute(
                "SELECT COUNT(*) FROM routes WHERE provider=? AND valid=0", (provider,)
            ).fetchone()[0]
            nonstop_yes, nonstop_no = self._conn.execute(
                "SELECT "
                "SUM(CASE WHEN nonstop=1 THEN 1 ELSE 0 END), "
                "SUM(CASE WHEN nonstop=0 THEN 1 ELSE 0 END) "
                "FROM routes WHERE provider=?",
                (provider,),
            ).fetchone()
        return rows, routes, nomarket, nonstop_yes, nonstop_no

    def close(self) -> None:
        self._conn.close()


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

        self.store = CrawlStore(db_path)

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
        self.store.upsert_airports(origin_airports, mode="INSERT OR REPLACE")

        us_origins = (
            list(origins) if origins else [a.code for a in origin_airports if a.is_domestic_us]
        )
        pairs: list[tuple[str, str]] = []
        prov = self.provider.name
        for i, o in enumerate(us_origins, 1):
            dests = [d for d in self.provider.destinations(o) if d.country_code == "US"]
            self.store.add_destinations(prov, o, dests)
            pairs.extend((o, d.code) for d in dests)
            logger.info("  routes: [%d/%d] %s -> %d US dests", i, len(us_origins), o, len(dests))
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
        return self.store.done_windows(self.provider.name)

    def _invalid_markets(self) -> set[tuple[str, str]]:
        return self.store.invalid_markets(self.provider.name)

    def _probed_markets(self) -> set[tuple[str, str]]:
        """Markets whose nonstop status is already known (skip on resume)."""
        return self.store.probed_markets(self.provider.name)

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
        self.store.upsert_nonstop(self.provider.name, origin, dest, now, nonstop)
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
            logger.info("\nNonstop probe: nothing to do (all markets already probed).")
            return
        sample_dates = self._sample_dates(begin, end)
        total = len(todo)
        logger.info(
            "\nProbing nonstop service for %d market(s) on %d sample date(s) (%s)...",
            total,
            len(sample_dates),
            ", ".join(sample_dates),
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
                        logger.info(
                            "  nonstop %d/%d (%d%%) | %.1f/s | yes=%d no=%d | ETA %s",
                            completed,
                            total,
                            completed * 100 // total,
                            rate,
                            self.stat_nonstop_yes,
                            self.stat_nonstop_no,
                            _fmt(eta),
                        )
            except KeyboardInterrupt:
                ex.shutdown(wait=False, cancel_futures=True)
                logger.info("\nNonstop probe interrupted - progress saved. Re-run to resume.")

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
        stored = self.store.record_window_result(
            self.provider.name, origin, dest, begin, end, status, fares, now
        )
        if status == "ok":
            self.stat_rows += stored
            self.stat_windows += 1
        elif status == "nomarket":
            self.stat_nomarket += 1
        else:
            self.stat_errors += 1

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
        logger.info("[%s] Discovering US route network...", self.provider.name)
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
        logger.info(
            "\nRoutes: %d | windows/route: %d (%s .. %s)\n"
            "Tasks to do: %d (%d already done/skipped) | Workers: %d",
            len(pairs),
            len(windows),
            begin,
            end,
            total,
            already,
            self.workers,
        )

        interrupted = False
        if total == 0:
            logger.info("Nothing to do - low-fare crawl already complete for this range.")
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
                            logger.info(
                                "  %d/%d (%d%%) | %.1f win/s | rows=%d nomarket=%d err=%d | ETA %s",
                                completed,
                                total,
                                completed * 100 // total,
                                rate,
                                self.stat_rows,
                                self.stat_nomarket,
                                self.stat_errors,
                                _fmt(eta),
                            )
                except KeyboardInterrupt:
                    # Cancel queued (not-yet-started) work so we stop promptly rather
                    # than draining the whole queue on context-manager exit.
                    interrupted = True
                    ex.shutdown(wait=False, cancel_futures=True)
                    logger.info(
                        "\nInterrupted - progress saved. Re-run the same command to resume."
                    )

        # Nonstop probe runs after the low-fare pass so it can skip markets the
        # crawl just proved invalid. Skipped on interrupt (it's resumable on the
        # next run). Both passes are independently resumable.
        if probe_nonstop and not interrupted:
            self._probe_pass(pairs, begin, end)
        self._report()

    def _report(self) -> None:
        rows, routes, nomarket, nonstop_yes, nonstop_no = self.store.report_counts(
            self.provider.name
        )
        logger.info(
            "\nDataset: %d fare-rows across %d routes (%d invalid markets skipped) -> %s",
            rows,
            routes,
            nomarket,
            self.db_path,
        )
        if nonstop_yes or nonstop_no:
            logger.info(
                "Nonstop service: %d market(s) nonstop, %d connecting-only.",
                nonstop_yes or 0,
                nonstop_no or 0,
            )

    def close(self) -> None:
        self.store.close()


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
