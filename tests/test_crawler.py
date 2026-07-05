"""Crawler orchestration tests using an in-memory fake provider (no network)."""

import datetime as _dt
import sqlite3

from flights.core.crawl import Crawler, CrawlStore
from flights.core.errors import MarketNotFoundError
from flights.core.models import Airport, DayFare, Flight


def _airport(code):
    return Airport(code, code, f"{code} Intl", "US", "United States", "CO", "395142N", "1044023W")


class FakeProvider:
    """Minimal BaseProvider-compatible stub with call counting.

    Not a subclass of BaseProvider so we can keep it tiny; it implements exactly
    what Crawler touches (name, lowfare_window_days, origins, destinations,
    lowfare_window, flights).
    """

    name = "fake"
    lowfare_window_days = 7
    default_currency = "USD"

    def __init__(self, dests=("LAS",), nomarket=()):
        self._dests = dests
        self._nomarket = set(nomarket)
        self.lowfare_calls = []
        self.flight_calls = []

    def origins(self):
        return [_airport("DEN"), _airport("LAS")]

    def destinations(self, origin):
        return [_airport(c) for c in self._dests] if origin == "DEN" else []

    def lowfare_window(self, origin, destination, begin, end):
        self.lowfare_calls.append((origin, destination, begin.isoformat(), end.isoformat()))
        if destination in self._nomarket:
            raise MarketNotFoundError(f"{origin}-{destination}")
        return [
            DayFare(
                self.name,
                origin,
                destination,
                begin.isoformat(),
                standard_fare=99.0,
                saver_fare=49.0,
                miles=10000,
                miles_fees=5.6,
            )
        ]

    def flights(self, origin, destination, date, nonstop_only=False, adults=1):
        self.flight_calls.append((origin, destination, date))
        return [
            Flight(
                self.name,
                origin,
                destination,
                date,
                "F9 1",
                "",
                "",
                stops=0,
                flight_type="NonStop",
                standard_fare=99.0,
            )
        ]


def _run(provider, db, **kw):
    crawler = Crawler(provider, db, workers=2)
    try:
        crawler.crawl("2025-01-01", "2025-01-07", **kw)
    finally:
        crawler.close()


def test_crawl_populates_all_tables(tmp_path):
    db = str(tmp_path / "c.db")
    provider = FakeProvider()

    _run(provider, db, probe_nonstop=True)

    conn = sqlite3.connect(db)
    try:
        assert conn.execute("SELECT COUNT(*) FROM lowfares").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM airports").fetchone()[0] >= 2
        nonstop = conn.execute(
            "SELECT nonstop FROM routes WHERE origin='DEN' AND destination='LAS'"
        ).fetchone()[0]
        assert nonstop == 1
        status = conn.execute("SELECT status FROM crawl_windows").fetchone()[0]
        assert status == "done"
    finally:
        conn.close()


def test_crawl_is_resumable(tmp_path):
    db = str(tmp_path / "c.db")

    first = FakeProvider()
    _run(first, db, probe_nonstop=True)
    assert len(first.lowfare_calls) == 1
    assert len(first.flight_calls) == 1  # nonstop probe ran once

    # A second run over the same range and DB must re-fetch nothing and
    # re-probe nothing, because both passes are recorded and resumable.
    second = FakeProvider()
    _run(second, db, probe_nonstop=True)
    assert second.lowfare_calls == []
    assert second.flight_calls == []


def test_nomarket_is_cached_and_not_stored(tmp_path):
    db = str(tmp_path / "c.db")
    provider = FakeProvider(dests=("LAS", "NOM"), nomarket=("NOM",))

    _run(provider, db, probe_nonstop=True)

    conn = sqlite3.connect(db)
    try:
        valid = conn.execute(
            "SELECT valid FROM routes WHERE origin='DEN' AND destination='NOM'"
        ).fetchone()[0]
        assert valid == 0
        nom_rows = conn.execute("SELECT COUNT(*) FROM lowfares WHERE destination='NOM'").fetchone()[
            0
        ]
        assert nom_rows == 0
        # The invalid market must be skipped by the nonstop probe.
        assert ("DEN", "NOM") not in provider.flight_calls
    finally:
        conn.close()


def test_windows_helper_chunks_by_window_days(tmp_path):
    provider = FakeProvider()
    crawler = Crawler(provider, str(tmp_path / "c.db"), workers=1)
    try:
        windows = crawler._windows(_dt.date(2025, 1, 1), _dt.date(2025, 1, 20))
    finally:
        crawler.close()
    assert windows == [_dt.date(2025, 1, 1), _dt.date(2025, 1, 8), _dt.date(2025, 1, 15)]


def test_stored_timestamps_are_utc_aware(tmp_path):
    db = str(tmp_path / "c.db")
    _run(FakeProvider(), db, probe_nonstop=False)

    conn = sqlite3.connect(db)
    try:
        scraped_at = conn.execute("SELECT scraped_at FROM lowfares").fetchone()[0]
    finally:
        conn.close()

    parsed = _dt.datetime.fromisoformat(scraped_at)
    assert parsed.tzinfo is not None
    assert parsed.utcoffset() == _dt.timedelta(0)


def test_crawler_emits_progress_via_logging(tmp_path, caplog):
    db = str(tmp_path / "c.db")
    with caplog.at_level("INFO", logger="flights.core.crawl"):
        _run(FakeProvider(), db, probe_nonstop=True)

    messages = "\n".join(r.getMessage() for r in caplog.records)
    assert "Discovering US route network" in messages
    assert "Dataset:" in messages


def test_crawler_is_quiet_on_stdout_by_default(tmp_path, capsys):
    # As a library, the crawler must not write to stdout on its own; progress is
    # emitted through logging and only surfaced when the caller adds a handler.
    db = str(tmp_path / "c.db")
    _run(FakeProvider(), db, probe_nonstop=True)

    assert capsys.readouterr().out == ""


def test_crawlstore_persists_and_reports_in_isolation(tmp_path):
    # The repository concern works standalone, with no Crawler/orchestration.
    store = CrawlStore(str(tmp_path / "s.db"))
    try:
        fare = DayFare("fake", "DEN", "LAS", "2025-01-01", standard_fare=99.0, saver_fare=49.0)
        stored = store.record_window_result(
            "fake",
            "DEN",
            "LAS",
            _dt.date(2025, 1, 1),
            _dt.date(2025, 1, 7),
            "ok",
            [fare],
            "2025-01-01T00:00:00+00:00",
        )
        assert stored == 1
        assert store.done_windows("fake") == {("DEN", "LAS", "2025-01-01"): "2025-01-07"}

        rows, routes, nomarket, nonstop_yes, nonstop_no = store.report_counts("fake")
        assert (rows, routes, nomarket) == (1, 1, 0)
    finally:
        store.close()
