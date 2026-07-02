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

from .errors import MarketNotFoundError, ProviderError
from .provider import BaseProvider

_SCHEMA = """
CREATE TABLE IF NOT EXISTS airports (
    code TEXT PRIMARY KEY, city TEXT, full_name TEXT,
    country_code TEXT, country_name TEXT, state_code TEXT, lat TEXT, long TEXT
);
CREATE TABLE IF NOT EXISTS routes (
    provider TEXT, origin TEXT, destination TEXT, valid INTEGER, last_checked TEXT,
    PRIMARY KEY (provider, origin, destination)
);
CREATE TABLE IF NOT EXISTS lowfares (
    provider TEXT, origin TEXT, destination TEXT, date TEXT,
    standard_fare REAL, discounted_fare REAL, saver_fare REAL,
    miles INTEGER, miles_fees REAL, currency TEXT, scraped_at TEXT,
    PRIMARY KEY (provider, origin, destination, date)
);
CREATE TABLE IF NOT EXISTS crawl_windows (
    provider TEXT, origin TEXT, destination TEXT, window_begin TEXT,
    window_end TEXT, status TEXT, scraped_at TEXT,
    PRIMARY KEY (provider, origin, destination, window_begin)
);
CREATE TABLE IF NOT EXISTS crawl_meta (key TEXT PRIMARY KEY, value TEXT);
CREATE INDEX IF NOT EXISTS idx_lowfares_date ON lowfares(date);
CREATE INDEX IF NOT EXISTS idx_lowfares_route ON lowfares(provider, origin, destination);
"""


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
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

        self.stat_rows = 0
        self.stat_windows = 0
        self.stat_nomarket = 0
        self.stat_errors = 0

    # ------------------------------------------------------------------ #
    # reference data                                                     #
    # ------------------------------------------------------------------ #

    def build_us_routes(self, origins: Optional[Iterable[str]] = None) -> list[tuple[str, str]]:
        origin_airports = self.provider.origins()
        with self._db_lock:
            self._conn.executemany(
                "INSERT OR REPLACE INTO airports "
                "(code, city, full_name, country_code, country_name, state_code, lat, long) "
                "VALUES (?,?,?,?,?,?,?,?)",
                [(a.code, a.city, a.full_name, a.country_code, a.country_name,
                  a.state_code, a.lat, a.long) for a in origin_airports],
            )
            self._conn.commit()

        us_origins = list(origins) if origins else [a.code for a in origin_airports if a.is_domestic_us]
        pairs: list[tuple[str, str]] = []
        prov = self.provider.name
        for i, o in enumerate(us_origins, 1):
            dests = [d for d in self.provider.destinations(o) if d.country_code == "US"]
            with self._db_lock:
                self._conn.executemany(
                    "INSERT OR IGNORE INTO airports "
                    "(code, city, full_name, country_code, country_name, state_code, lat, long) "
                    "VALUES (?,?,?,?,?,?,?,?)",
                    [(d.code, d.city, d.full_name, d.country_code, d.country_name,
                      d.state_code, d.lat, d.long) for d in dests],
                )
                self._conn.executemany(
                    "INSERT OR IGNORE INTO routes (provider, origin, destination, valid, last_checked) "
                    "VALUES (?,?,?,NULL,NULL)",
                    [(prov, o, d.code) for d in dests],
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

    def _store(self, origin: str, dest: str, begin: _dt.date, end: _dt.date,
               status: str, fares: list) -> None:
        now = _dt.datetime.utcnow().isoformat()
        prov = self.provider.name
        with self._db_lock:
            if status == "ok":
                self._conn.executemany(
                    "INSERT OR REPLACE INTO lowfares "
                    "(provider,origin,destination,date,standard_fare,discounted_fare,saver_fare,"
                    "miles,miles_fees,currency,scraped_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    [(prov, f.origin, f.destination, f.date, f.standard_fare,
                      f.discounted_fare, f.saver_fare, f.miles, f.miles_fees,
                      f.currency, now) for f in fares],
                )
                self.stat_rows += len(fares)
                self.stat_windows += 1
            elif status == "nomarket":
                self._conn.execute(
                    "INSERT OR REPLACE INTO routes (provider,origin,destination,valid,last_checked) "
                    "VALUES (?,?,?,0,?)",
                    (prov, origin, dest, now),
                )
                self.stat_nomarket += 1
            else:
                self.stat_errors += 1

            win_status = "done" if status in ("ok", "nomarket") else "error"
            self._conn.execute(
                "INSERT OR REPLACE INTO crawl_windows "
                "(provider,origin,destination,window_begin,window_end,status,scraped_at) "
                "VALUES (?,?,?,?,?,?,?)",
                (prov, origin, dest, begin.isoformat(), end.isoformat(), win_status, now),
            )
            self._conn.commit()

    # ------------------------------------------------------------------ #
    # driver                                                             #
    # ------------------------------------------------------------------ #

    def crawl(self, begin_date: str, end_date: str, origins: Optional[Iterable[str]] = None) -> None:
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
        for (o, d) in pairs:
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
        if total == 0:
            print("Nothing to do - crawl already complete for this range.")
            self._report()
            return

        start = time.time()
        completed = 0
        with ThreadPoolExecutor(max_workers=self.workers) as ex:
            futures = {
                ex.submit(self._fetch, o, d, w, we): (o, d, w, we)
                for (o, d, w, we) in tasks
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
                ex.shutdown(wait=False, cancel_futures=True)
                print(
                    "\nInterrupted - progress saved. Re-run the same command to resume.",
                    flush=True,
                )
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
        print(
            f"\nDataset: {rows} fare-rows across {routes} routes "
            f"({nomarket} invalid markets skipped) -> {self.db_path}",
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
