"""flights - a pure-HTTP, multi-provider flight-availability toolkit.

Query airline booking backends for cash fares and award (miles) costs without a
browser or login. Providers implement a common interface, so the same CLI,
crawler, and models work across airlines.

Quick start::

    from flights import get_provider

    airline = get_provider("frontier")
    for day in airline.lowfare_calendar("DEN", "LAS", "2026-08-02", "2026-08-15"):
        print(day.date, day.cheapest_cash, day.miles)

    for f in airline.flights("DEN", "LAS", "2026-08-05", nonstop_only=True):
        print(f.flight_number, f.depart_time, f.cheapest_cash, f.miles)
"""

# Importing the providers package registers the bundled airlines.
from . import providers  # noqa: F401,E402
from .core import (
    Airport,
    AuthError,
    BaseProvider,
    Crawler,
    DayFare,
    Flight,
    FlightsError,
    MarketNotFoundError,
    ProviderError,
    available_providers,
    get_provider,
    register_provider,
)

__version__ = "0.2.0"

__all__ = [
    "get_provider",
    "available_providers",
    "register_provider",
    "BaseProvider",
    "Crawler",
    "Airport",
    "DayFare",
    "Flight",
    "FlightsError",
    "ProviderError",
    "MarketNotFoundError",
    "AuthError",
    "__version__",
]
