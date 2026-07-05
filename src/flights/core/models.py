"""Provider-agnostic data models.

These normalized shapes are what every provider returns, so downstream code
(CLI, crawler, analysis) never has to care which airline produced a row.

Provider-specific extras that don't fit the common fields go in ``extra`` so
we never lose data and never have to widen the core schema per airline.
"""

from dataclasses import dataclass, field
from typing import Any


def cheapest_cash(*fares: float | None) -> float | None:
    """Lowest of the given cash-fare tiers, or ``None`` if all are missing.

    The single Python implementation of the "cheapest cash" rule, shared by the
    :class:`DayFare` / :class:`Flight` ``cheapest_cash`` properties and the CLI's
    row-dict sort key. Its SQL twin is
    :data:`flights.core.storage.CHEAPEST_CASH_SQL`.
    """
    present = [f for f in fares if f is not None]
    return min(present) if present else None


@dataclass(frozen=True)
class Airport:
    code: str
    city: str
    full_name: str
    country_code: str
    country_name: str
    state_code: str | None = None
    lat: str | None = None
    long: str | None = None

    @property
    def is_domestic_us(self) -> bool:
        return self.country_code == "US"


@dataclass(frozen=True)
class DayFare:
    """The cheapest fare + award cost for one route on one date.

    ``standard_fare`` / ``discounted_fare`` are the two common cash tiers most
    carriers expose. ``saver_fare`` is the lowest promotional/basic tier when a
    carrier offers one (e.g. Frontier's GoWild!). ``miles`` / ``miles_fees`` are
    the award redemption cost + its cash taxes.
    """

    provider: str
    origin: str
    destination: str
    date: str  # YYYY-MM-DD
    standard_fare: float | None = None
    discounted_fare: float | None = None
    saver_fare: float | None = None
    miles: int | None = None
    miles_fees: float | None = None
    currency: str = "USD"
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def cheapest_cash(self) -> float | None:
        return cheapest_cash(self.standard_fare, self.discounted_fare, self.saver_fare)


@dataclass(frozen=True)
class Flight:
    """A single scheduled flight with its fares and stop information."""

    provider: str
    origin: str
    destination: str
    date: str
    flight_number: str
    depart_time: str  # ISO
    arrive_time: str  # ISO
    aircraft: str | None = None
    stops: int = 0
    flight_type: str = ""  # "NonStop" / "Connecting" / provider-specific
    duration: str | None = None
    standard_fare: float | None = None
    discounted_fare: float | None = None
    saver_fare: float | None = None
    miles: int | None = None
    currency: str = "USD"
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def is_nonstop(self) -> bool:
        return self.stops == 0 or self.flight_type.lower() in ("nonstop", "direct")

    @property
    def cheapest_cash(self) -> float | None:
        return cheapest_cash(self.standard_fare, self.discounted_fare, self.saver_fare)
