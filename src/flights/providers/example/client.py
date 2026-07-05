"""A minimal, self-contained example provider.

Copy this package to ``flights/providers/<airline>/`` as the starting point for a
new airline backend, then:

1. Replace the static demo data below with real HTTP calls to the airline's
   booking backend (see ``flights/providers/frontier/client.py`` for a full,
   real implementation with auth and GraphQL).
2. Return the normalized :mod:`flights.core.models` shapes (``Airport``,
   ``DayFare``, ``Flight``) so the generic CLI and crawler work unchanged.
3. Register it in ``flights/providers/__init__.py``::

       from .example import ExampleProvider
       register_provider("example", ExampleProvider)

This provider makes **no network calls** — it returns a tiny hard-coded dataset
so you can run the whole pipeline offline while learning the interface::

    from flights.providers.example import ExampleProvider
    p = ExampleProvider()
    p.lowfare_calendar("AAA", "BBB", "2026-01-01", "2026-01-03")
"""

import datetime as _dt

from ...core.errors import MarketNotFoundError
from ...core.models import Airport, DayFare, Flight
from ...core.provider import BaseProvider

# A tiny fake network: two airports and the single market between them.
_AIRPORTS = {
    "AAA": Airport("AAA", "Alphaville", "Alphaville Intl", "US", "United States", "AA"),
    "BBB": Airport("BBB", "Betaburg", "Betaburg Intl", "US", "United States", "BB"),
}
_MARKETS = {("AAA", "BBB"), ("BBB", "AAA")}


class ExampleProvider(BaseProvider):
    """Returns deterministic demo data; a template for real providers."""

    name = "example"
    lowfare_window_days = 7
    default_currency = "USD"

    def origins(self) -> list[Airport]:
        return list(_AIRPORTS.values())

    def destinations(self, origin: str) -> list[Airport]:
        origin = origin.upper()
        return [_AIRPORTS[d] for (o, d) in _MARKETS if o == origin]

    def lowfare_window(
        self, origin: str, destination: str, begin: _dt.date, end: _dt.date
    ) -> list[DayFare]:
        origin, destination = origin.upper(), destination.upper()
        if (origin, destination) not in _MARKETS:
            raise MarketNotFoundError(f"{origin}-{destination} is not an example market")
        out: list[DayFare] = []
        day = begin
        while day <= end:
            # A cheap "saver" on weekends, a standard fare otherwise.
            saver = 19.0 if day.weekday() >= 5 else None
            out.append(
                DayFare(
                    provider=self.name,
                    origin=origin,
                    destination=destination,
                    date=day.isoformat(),
                    standard_fare=59.0,
                    saver_fare=saver,
                    miles=5000,
                    miles_fees=5.6,
                )
            )
            day += _dt.timedelta(days=1)
        return out

    def flights(
        self,
        origin: str,
        destination: str,
        date: str,
        nonstop_only: bool = False,
        adults: int = 1,
    ) -> list[Flight]:
        origin, destination = origin.upper(), destination.upper()
        if (origin, destination) not in _MARKETS:
            raise MarketNotFoundError(f"{origin}-{destination} is not an example market")
        return [
            Flight(
                provider=self.name,
                origin=origin,
                destination=destination,
                date=date,
                flight_number="EX 100",
                depart_time=f"{date}T08:00:00",
                arrive_time=f"{date}T09:30:00",
                stops=0,
                flight_type="NonStop",
                standard_fare=59.0,
                saver_fare=19.0,
                miles=5000,
            )
        ]
