"""The provider interface every airline backend implements.

A provider is responsible for talking to one airline's booking backend and
returning the normalized :mod:`flights.core.models` shapes. The generic crawler
and CLI are written entirely against this interface, so adding a new airline is
a matter of subclassing :class:`BaseProvider` and registering it.

Required methods:  ``origins``, ``destinations``, ``lowfare_window``, ``flights``.
Everything else (route expansion, calendar chunking) has a default here.
"""

from __future__ import annotations

import abc
import datetime as _dt
from typing import Iterable, Iterator, Optional

from .models import Airport, DayFare, Flight


class BaseProvider(abc.ABC):
    #: short, stable identifier used on the CLI and stored in every row
    name: str = ""

    #: maximum number of days the low-fare calendar returns per request
    lowfare_window_days: int = 7

    #: default fare currency
    default_currency: str = "USD"

    # ---- required, provider-specific ------------------------------------ #

    @abc.abstractmethod
    def origins(self) -> list[Airport]:
        """All airports the airline departs from."""

    @abc.abstractmethod
    def destinations(self, origin: str) -> list[Airport]:
        """All destinations served from ``origin`` (the route map)."""

    @abc.abstractmethod
    def lowfare_window(
        self, origin: str, destination: str, begin: _dt.date, end: _dt.date
    ) -> list[DayFare]:
        """Cheapest fare + miles per day for a single <= ``lowfare_window_days`` window.

        Must raise :class:`flights.core.errors.MarketNotFoundError` if the O&D
        is not a market the airline sells.
        """

    @abc.abstractmethod
    def flights(
        self,
        origin: str,
        destination: str,
        date: str,
        nonstop_only: bool = False,
        adults: int = 1,
    ) -> list[Flight]:
        """Individual flights for a route on a specific date, with fares."""

    # ---- provided defaults ---------------------------------------------- #

    def lowfare_calendar(
        self, origin: str, destination: str, begin_date: str, end_date: str
    ) -> list[DayFare]:
        """Cheapest fare per day across an arbitrary range (auto-chunked)."""
        start = _parse_date(begin_date)
        end = _parse_date(end_date)
        if end < start:
            raise ValueError("end_date must be >= begin_date")
        out: list[DayFare] = []
        cursor = start
        while cursor <= end:
            window_end = min(
                cursor + _dt.timedelta(days=self.lowfare_window_days - 1), end
            )
            out.extend(self.lowfare_window(origin, destination, cursor, window_end))
            cursor = window_end + _dt.timedelta(days=1)
        return out

    def us_origins(self) -> list[str]:
        """Codes of domestic-US origins (default: filter :meth:`origins`)."""
        return [a.code for a in self.origins() if a.is_domestic_us]

    def routes(self, origins: Optional[Iterable[str]] = None) -> Iterator[tuple[str, str]]:
        """Yield ``(origin, destination)`` pairs across the network."""
        origins = list(origins) if origins is not None else [a.code for a in self.origins()]
        for o in origins:
            for d in self.destinations(o):
                yield (o, d.code)

    def close(self) -> None:  # pragma: no cover - optional cleanup hook
        """Release any resources (sessions, sockets). Safe no-op by default."""


def _parse_date(s: str) -> _dt.date:
    return _dt.datetime.strptime(s[:10], "%Y-%m-%d").date()
