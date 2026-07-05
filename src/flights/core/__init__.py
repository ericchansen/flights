"""Provider-agnostic core: models, interface, registry, and the crawler."""

from .crawl import Crawler
from .errors import AuthError, FlightsError, MarketNotFoundError, ProviderError
from .models import Airport, DayFare, Flight
from .provider import BaseProvider
from .registry import available_providers, get_provider, register_provider

__all__ = [
    "Airport",
    "DayFare",
    "Flight",
    "BaseProvider",
    "Crawler",
    "get_provider",
    "register_provider",
    "available_providers",
    "FlightsError",
    "ProviderError",
    "MarketNotFoundError",
    "AuthError",
]
