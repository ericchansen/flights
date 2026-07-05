"""Example provider template package (not registered by default).

See :mod:`flights.providers.example.client` for how to copy this into a real
airline provider and register it.
"""

from .client import ExampleProvider

__all__ = ["ExampleProvider"]
