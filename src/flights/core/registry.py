"""Provider registry: look up airline backends by name.

Providers register themselves (see ``flights/providers/__init__.py``). Callers
use :func:`get_provider` / :func:`available_providers` without importing any
concrete provider class.
"""

from collections.abc import Callable

from .errors import FlightsError
from .provider import BaseProvider

_REGISTRY: dict[str, Callable[..., BaseProvider]] = {}


def register_provider(name: str, factory: Callable[..., BaseProvider]) -> None:
    """Register a provider factory (class or callable) under ``name``."""
    _REGISTRY[name.lower()] = factory


def available_providers() -> list[str]:
    return sorted(_REGISTRY)


def get_provider(name: str, **kwargs) -> BaseProvider:
    """Instantiate the provider registered under ``name``.

    Extra keyword arguments are forwarded to the provider constructor.
    """
    key = name.lower()
    if key not in _REGISTRY:
        raise FlightsError(
            f"Unknown provider '{name}'. Available: {', '.join(available_providers()) or '(none)'}"
        )
    return _REGISTRY[key](**kwargs)
