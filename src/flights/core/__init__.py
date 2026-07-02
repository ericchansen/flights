"""Provider-agnostic core: models, interface, registry, and the crawler."""

from .errors import AuthError, FlightsError, MarketNotFoundError, ProviderError
from .models import Airport, DayFare, Flight
from .provider import BaseProvider
from .registry import available_providers, get_provider, register_provider
from .crawl import Crawler

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
