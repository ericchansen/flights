"""Frontier Airlines unofficial flight-availability SDK.

Pure-HTTP client for Frontier's public booking backend. No browser required.

Reverse-engineered from https://www2.flyfrontier.com (Navitaire New Skies
behind an Azure APIM gateway). See README.md for the full endpoint reference.

Typical use::

    from frontier_flights import FrontierClient

    client = FrontierClient()
    for day in client.lowfare_calendar("DEN", "LAS", "2026-08-02", "2026-08-08"):
        print(day.date, day.discounted_fare, day.total_miles)

    for f in client.flights("DEN", "LAS", "2026-08-05", nonstop_only=True):
        print(f.flight_number, f.depart_time, f.standard_fare, f.miles)
"""

from .client import (
    FrontierClient,
    FrontierError,
    Airport,
    DayFare,
    Flight,
)

__all__ = [
    "FrontierClient",
    "FrontierError",
    "Airport",
    "DayFare",
    "Flight",
]

__version__ = "0.1.0"
