"""Shared pytest fixtures.

Every provider request is mocked with the ``responses`` library, so the suite
runs fully offline and deterministically — no live traffic to any airline
(honoring the project's "scrape gently" policy). Fixtures here build a temp
SQLite database straight from the crawler's canonical schema and a Frontier
provider wired to fake endpoints that never touches the network on import.
"""

import sqlite3

import pytest
import responses as responses_lib
from _frontier import BFF_ENDPOINT, SUBSCRIPTION_KEY

from flights.core.crawl import _SCHEMA
from flights.providers.frontier.client import FrontierProvider


@pytest.fixture
def schema_db(tmp_path):
    """Path to a fresh SQLite DB built from the crawler's canonical schema."""
    db = tmp_path / "flights.db"
    conn = sqlite3.connect(db)
    conn.executescript(_SCHEMA)
    conn.commit()
    conn.close()
    return str(db)


@pytest.fixture
def mocked_responses():
    """An active ``responses`` mock; any unregistered request raises."""
    with responses_lib.RequestsMock() as rsps:
        yield rsps


@pytest.fixture
def frontier():
    """A FrontierProvider on fake endpoints (no network during construction)."""
    provider = FrontierProvider(
        subscription_key=SUBSCRIPTION_KEY,
        bff_endpoint=BFF_ENDPOINT,
        request_delay=0.0,
        max_retries=2,
    )
    try:
        yield provider
    finally:
        provider.close()
