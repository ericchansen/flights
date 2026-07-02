"""Network-wide, resumable, concurrent crawler for Frontier low-fare data.

Design goals
------------
* **Incremental & crash-safe** - every scraped 7-day window is committed to
  SQLite immediately, so an interrupted run loses at most the in-flight windows.
* **Resumable** - re-running skips windows already stored (tracked in
  ``crawl_windows``). Invalid markets are cached in ``routes`` and skipped.
* **Concurrent but polite** - a small thread pool (default 8) issues one
  single-route request each; batching is intentionally avoided because the
  backend 504s on multi-route calls and 400s if any market is invalid.
* **Self-maintaining auth** - the anonymous token is refreshed under a lock.

Schema
------
``airports(code, city, country_code, ...)``
``routes(origin, dest, valid, last_checked)``            -- market validity cache
``lowfares(origin, dest, date, *fares*, scraped_at)``    -- the dataset
``crawl_windows(origin, dest, window_begin, status)``    -- resume ledger
``crawl_meta(key, value)``                               -- run metadata
"""

from __future__ import annotations

import datetime as _dt
import sqlite3
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Iterable, Optional

import requests

from .client import (
    DEFAULT_FARE_TYPES,
    LOWFARE_URL,
    LOWFARE_WINDOW_DAYS,
    USER_AGENT,
    FrontierClient,
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS airports (
    code TEXT PRIMARY KEY, city TEXT, full_name TEXT,
    country_code TEXT, country_name TEXT, state_code TEXT, lat TEXT, long TEXT
);
CREATE TABLE IF NOT EXISTS routes (
    origin TEXT, dest TEXT, valid INTEGER, last_checked TEXT,
    PRIMARY KEY (origin, dest)
);
CREATE TABLE IF NOT EXISTS lowfares (
    origin TEXT, dest TEXT, date TEXT,
    standard_fare REAL, discounted_fare REAL, gowild_fare REAL,
    total_miles INTEGER, miles_taxes_fees REAL, currency TEXT,
    scraped_at TEXT,
    PRIMARY KEY (origin, dest, date)
);
CREATE TABLE IF NOT EXISTS crawl_windows (
    origin TEXT, dest TEXT, window_begin TEXT,
    status TEXT, scraped_at TEXT,
    PRIMARY KEY (origin, dest, window_begin)
);
CREATE TABLE IF NOT EXISTS crawl_meta (key TEXT PRIMARY KEY, value TEXT);
CREATE INDEX IF NOT EXISTS idx_lowfares_date ON lowfares(date);
CREATE INDEX IF NOT EXISTS idx_lowfares_route ON lowfares(origin, dest);
"""


class _TokenManager:
    """Thread-safe anonymous-token provider backed by a FrontierClient."""

    def __init__(self, client: FrontierClient) -> None:
        self._client = client
        self._lock = threading.Lock()

    @property
    def subscription_key(self) -> str:
        return self._client._subscription_key  # noqa: SLF001

    def token(self) -> str:
        with self._lock:
            return self._client._ensure_token()  # noqa: SLF001

    def force_refresh(self) -> str:
        with self._lock:
            self._client._mint_token()  # noqa: SLF001
            return self._client._token  # noqa: SLF001


_thread_local = threading.local()


def _session() -> requests.Session:
    s = getattr(_thread_local, "session", None)
    if s is None:
        s = requests.Session()
        s.headers.update({"User-Agent": USER_AGENT, "Accept": "application/json"})
        _thread_local.session = s
    return s


class Crawler:
    def __init__(
        self,
        db_path: str,
        client: Optional[FrontierClient] = None,
        workers: int = 8,
        currency: str = "USD",
        timeout: float = 45.0,
    ) -> None:
        self.db_path = db_path
        self.workers = workers
        self.currency = currency
        self.timeout = timeout
        self.client = client or FrontierClient(currency=currency, request_delay=0.0)
        self.tokens = _TokenManager(self.client)

        self._db_lock = threading.Lock()
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

        # counters (guarded by _db_lock)
        self.stat_rows = 0
        self.stat_windows = 0
        self.stat_nomarket = 0
        self.stat_errors = 0

    # ------------------------------------------------------------------ #
    # reference data                                                     #
    # ------------------------------------------------------------------ #

    def sync_airports(self) -> list:
        airports = self.client.origins()
        with self._db_lock:
            self._conn.executemany(
                "INSERT OR REPLACE INTO airports "
                "(code, city, full_name, country_code, country_name, state_code, lat, long) "
                "VALUES (?,?,?,?,?,?,?,?)",
                [
                    (a.code, a.city, a.full_name, a.country_code, a.country_name,
                     a.state_code, a.lat, a.long)
                    for a in airports
                ],
            )
            self._conn.commit()
        return airports

    def build_us_routes(self, origins: Optional[Iterable[str]] = None) -> list[tuple[str, str]]:
        """Return US->US directed routes, caching airports + route rows."""
        airports = self.sync_airports()
        by_code = {a.code: a for a in airports}
        us_origins = list(origins) if origins else [a.code for a in airports if a.is_domestic_us]

        pairs: list[tuple[str, str]] = []
        for i, o in enumerate(us_origins, 1):
            dests = self.client.destinations(o)
            us_dests = [d for d in dests if d.country_code == "US"]
            # cache destination airports we might not have seen as origins
            with self._db_lock:
                self._conn.executemany(
                    "INSERT OR IGNORE INTO airports "
                    "(code, city, full_name, country_code, country_name, state_code, lat, long) "
                    "VALUES (?,?,?,?,?,?,?,?)",
                    [(d.code, d.city, d.full_name, d.country_code, d.country_name,
                      d.state_code, d.lat, d.long) for d in us_dests],
                )
                self._conn.executemany(
                    "INSERT OR IGNORE INTO routes (origin, dest, valid, last_checked) "
                    "VALUES (?,?,NULL,NULL)",
                    [(o, d.code) for d in us_dests],
                )
                self._conn.commit()
            pairs.extend((o, d.code) for d in us_dests)
            print(f"  routes: [{i}/{len(us_origins)}] {o} -> {len(us_dests)} US dests", flush=True)
        return pairs

    # ------------------------------------------------------------------ #
    # planning                                                           #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _windows(begin: _dt.date, end: _dt.date) -> list[_dt.date]:
        out = []
        cur = begin
        while cur <= end:
            out.append(cur)
            cur += _dt.timedelta(days=LOWFARE_WINDOW_DAYS)
        return out

    def _done_windows(self) -> set[tuple[str, str, str]]:
        with self._db_lock:
            rows = self._conn.execute(
                "SELECT origin, dest, window_begin FROM crawl_windows WHERE status='done'"
            ).fetchall()
        return set(rows)

    def _invalid_markets(self) -> set[tuple[str, str]]:
        with self._db_lock:
            rows = self._conn.execute(
                "SELECT origin, dest FROM routes WHERE valid=0"
            ).fetchall()
        return set(rows)

    # ------------------------------------------------------------------ #
    # worker                                                             #
    # ------------------------------------------------------------------ #

    def _fetch_window(self, origin: str, dest: str, begin: _dt.date):
        end = begin + _dt.timedelta(days=LOWFARE_WINDOW_DAYS - 1)
        body = {
            "BypassCache": True, "GetAllDetails": True, "IncludeTaxesAndFees": True,
            "Passengers": {"Types": [{"Type": "ADT", "DiscountCode": "", "Count": 1}],
                           "ResidentCountry": "US"},
            "Codes": {"Currency": self.currency, "SourceOrganization": None, "PromotionCode": None},
            "Filters": {"GroupByDate": None, "FlightFilter": None, "Loyalty": None,
                        "BookingClasses": None, "ProductClasses": None,
                        "FareTypes": DEFAULT_FARE_TYPES},
            "Criteria": [{"OriginStationCodes": [origin], "DestinationStationCodes": [dest],
                          "BeginDate": begin.isoformat(), "EndDate": end.isoformat()}],
        }
        headers = {
            "Content-Type": "application/json", "Accept": "application/json",
            "ocp-apim-subscription-key": self.tokens.subscription_key,
            "authtoken": self.tokens.token(),
            "frontiertoken": str(uuid.uuid4()),
        }

        for attempt in range(1, 4):
            try:
                r = _session().post(LOWFARE_URL, json=body, headers=headers, timeout=self.timeout)
            except requests.RequestException:
                time.sleep(min(2 ** attempt, 6))
                continue
            if r.status_code == 200:
                results = (r.json().get("data") or {}).get("results") or {}
                return ("ok", results.get(f"{origin}|{dest}") or [])
            if r.status_code in (400,) and "does not exist" in r.text:
                return ("nomarket", [])
            if r.status_code == 401:  # token expired mid-run
                self.tokens.force_refresh()
                headers["authtoken"] = self.tokens.token()
                continue
            # 5xx / 504 transient
            time.sleep(min(2 ** attempt, 6))
        return ("error", [])

    def _store(self, origin: str, dest: str, begin: _dt.date, status: str, rows: list) -> None:
        now = _dt.datetime.utcnow().isoformat()
        with self._db_lock:
            if status == "ok":
                self._conn.executemany(
                    "INSERT OR REPLACE INTO lowfares "
                    "(origin,dest,date,standard_fare,discounted_fare,gowild_fare,"
                    "total_miles,miles_taxes_fees,currency,scraped_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?)",
                    [
                        (origin, dest, str(r.get("date", ""))[:10],
                         _f(r.get("standardFare")), _f(r.get("discountedFare")),
                         _f(r.get("gowildFare")), _i(r.get("totalMilesPoint")),
                         _f(r.get("milesTaxesAndFees")), self.currency, now)
                        for r in rows
                    ],
                )
                self.stat_rows += len(rows)
                self.stat_windows += 1
            elif status == "nomarket":
                self._conn.execute(
                    "INSERT OR REPLACE INTO routes (origin,dest,valid,last_checked) VALUES (?,?,0,?)",
                    (origin, dest, now),
                )
                self.stat_nomarket += 1
            else:
                self.stat_errors += 1

            win_status = "done" if status in ("ok", "nomarket") else "error"
            self._conn.execute(
                "INSERT OR REPLACE INTO crawl_windows (origin,dest,window_begin,status,scraped_at) "
                "VALUES (?,?,?,?,?)",
                (origin, dest, begin.isoformat(), win_status, now),
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
    ) -> None:
        begin = _d(begin_date)
        end = _d(end_date)
        print(f"Discovering US route network...", flush=True)
        pairs = self.build_us_routes(origins)
        windows = self._windows(begin, end)
        done = self._done_windows()
        invalid = self._invalid_markets()

        tasks = []
        for (o, d) in pairs:
            if (o, d) in invalid:
                continue
            for w in windows:
                if (o, d, w.isoformat()) in done:
                    continue
                tasks.append((o, d, w))

        total = len(tasks)
        already = len(pairs) * len(windows) - total
        print(
            f"\nRoutes: {len(pairs)} | windows/route: {len(windows)} "
            f"({begin} .. {end})\n"
            f"Tasks to do: {total} ({already} already done/skipped)\n"
            f"Workers: {self.workers}",
            flush=True,
        )
        if total == 0:
            print("Nothing to do - crawl already complete for this range.")
            self._report()
            return

        self._conn.execute(
            "INSERT OR REPLACE INTO crawl_meta (key,value) VALUES ('last_run',?)",
            (_dt.datetime.utcnow().isoformat(),),
        )
        self._conn.commit()

        start = time.time()
        completed = 0
        try:
            with ThreadPoolExecutor(max_workers=self.workers) as ex:
                futures = {
                    ex.submit(self._fetch_window, o, d, w): (o, d, w)
                    for (o, d, w) in tasks
                }
                for fut in as_completed(futures):
                    o, d, w = futures[fut]
                    try:
                        status, rows = fut.result()
                    except Exception:  # noqa: BLE001
                        status, rows = "error", []
                    self._store(o, d, w, status, rows)
                    completed += 1
                    if completed % 50 == 0 or completed == total:
                        elapsed = time.time() - start
                        rate = completed / elapsed if elapsed else 0
                        eta = (total - completed) / rate if rate else 0
                        print(
                            f"  {completed}/{total} "
                            f"({completed*100//total}%) | "
                            f"{rate:.1f} win/s | rows={self.stat_rows} "
                            f"nomarket={self.stat_nomarket} err={self.stat_errors} | "
                            f"ETA {_fmt(eta)}",
                            flush=True,
                        )
        except KeyboardInterrupt:
            print("\nInterrupted - progress saved. Re-run the same command to resume.", flush=True)
        self._report()

    def _report(self) -> None:
        with self._db_lock:
            rows = self._conn.execute("SELECT COUNT(*) FROM lowfares").fetchone()[0]
            routes_with_data = self._conn.execute(
                "SELECT COUNT(DISTINCT origin||dest) FROM lowfares"
            ).fetchone()[0]
            nomarket = self._conn.execute("SELECT COUNT(*) FROM routes WHERE valid=0").fetchone()[0]
        print(
            f"\nDataset: {rows} fare-rows across {routes_with_data} routes "
            f"({nomarket} invalid markets skipped) -> {self.db_path}",
            flush=True,
        )

    def close(self) -> None:
        self._conn.close()


# --------------------------------------------------------------------------- #
def _d(s: str) -> _dt.date:
    return _dt.datetime.strptime(s[:10], "%Y-%m-%d").date()


def _f(v):
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _i(v):
    try:
        return int(round(float(v))) if v is not None else None
    except (TypeError, ValueError):
        return None


def _fmt(seconds: float) -> str:
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h{m:02d}m"
    if m:
        return f"{m}m{s:02d}s"
    return f"{s}s"
