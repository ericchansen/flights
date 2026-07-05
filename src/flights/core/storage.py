"""Single source of truth for the crawl SQLite store.

Every table's DDL, its column order, the additive migration, and the row
adapters live here, so the crawler, the web exporter and the analysis scripts
all speak the same schema and can never drift apart (the root cause of the F1
web-pipeline bug, where a column rename in the crawler silently broke the
exporter's hard-coded ``SELECT``).

Column names deliberately equal the dataclass field names in
:mod:`flights.core.models`, so the tuples here stay in lockstep with the models.
"""

import sqlite3
from collections.abc import Iterable

from .models import Airport, DayFare

# Column order for every table. Consumers build INSERT/SELECT statements from
# these tuples instead of hand-typing column lists.
AIRPORT_COLUMNS = (
    "code",
    "city",
    "full_name",
    "country_code",
    "country_name",
    "state_code",
    "lat",
    "long",
)
ROUTE_COLUMNS = ("provider", "origin", "destination", "valid", "last_checked", "nonstop")
LOWFARE_COLUMNS = (
    "provider",
    "origin",
    "destination",
    "date",
    "standard_fare",
    "discounted_fare",
    "saver_fare",
    "miles",
    "miles_fees",
    "currency",
    "scraped_at",
)
CRAWL_WINDOW_COLUMNS = (
    "provider",
    "origin",
    "destination",
    "window_begin",
    "window_end",
    "status",
    "scraped_at",
)

# The cheapest of the three cash tiers, as one SQL expression that mirrors
# :attr:`flights.core.models.DayFare.cheapest_cash`. The 1e9 sentinels let MIN
# ignore NULL tiers; a result of 1e9 therefore means "no cash fare".
CHEAPEST_CASH_SQL = (
    "MIN(COALESCE(standard_fare,1e9),COALESCE(discounted_fare,1e9),COALESCE(saver_fare,1e9))"
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS airports (
    code TEXT PRIMARY KEY, city TEXT, full_name TEXT,
    country_code TEXT, country_name TEXT, state_code TEXT, lat TEXT, long TEXT
);
CREATE TABLE IF NOT EXISTS routes (
    provider TEXT, origin TEXT, destination TEXT, valid INTEGER, last_checked TEXT,
    nonstop INTEGER,
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

# Upsert used by the nonstop probe: insert a routes row (all columns) and, if the
# market already exists, refresh only nonstop + last_checked (never valid).
ROUTE_NONSTOP_UPSERT_SQL = (
    "INSERT INTO routes (provider,origin,destination,valid,last_checked,nonstop) "
    "VALUES (?,?,?,?,?,?) "
    "ON CONFLICT(provider,origin,destination) "
    "DO UPDATE SET nonstop=excluded.nonstop, last_checked=excluded.last_checked"
)


def init_db(conn: sqlite3.Connection) -> None:
    """Create all tables/indexes, apply additive migrations, and commit."""
    conn.executescript(SCHEMA)
    migrate(conn)
    conn.commit()


def migrate(conn: sqlite3.Connection) -> None:
    """Bring an older DB up to the current schema (additive only)."""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(routes)").fetchall()}
    if "nonstop" not in cols:
        conn.execute("ALTER TABLE routes ADD COLUMN nonstop INTEGER")


def insert_sql(table: str, columns: Iterable[str], *, mode: str = "INSERT") -> str:
    """Build a parameterized INSERT for ``columns`` in order.

    ``mode`` selects the conflict strategy, e.g. ``"INSERT OR REPLACE"`` or
    ``"INSERT OR IGNORE"``.
    """
    cols = tuple(columns)
    placeholders = ",".join(["?"] * len(cols))
    return f"{mode} INTO {table} ({','.join(cols)}) VALUES ({placeholders})"


def airport_row(airport: Airport) -> tuple:
    """Value tuple for an ``airports`` INSERT, in ``AIRPORT_COLUMNS`` order."""
    return (
        airport.code,
        airport.city,
        airport.full_name,
        airport.country_code,
        airport.country_name,
        airport.state_code,
        airport.lat,
        airport.long,
    )


def lowfare_row(provider: str, fare: DayFare, scraped_at: str) -> tuple:
    """Value tuple for a ``lowfares`` INSERT, in ``LOWFARE_COLUMNS`` order."""
    return (
        provider,
        fare.origin,
        fare.destination,
        fare.date,
        fare.standard_fare,
        fare.discounted_fare,
        fare.saver_fare,
        fare.miles,
        fare.miles_fees,
        fare.currency,
        scraped_at,
    )
