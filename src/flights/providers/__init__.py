"""Provider registrations.

Importing this package registers every bundled provider so that
:func:`flights.core.get_provider` can look them up by name. To add a new
airline, implement a :class:`~flights.core.provider.BaseProvider` subclass in a
subpackage here and register it below.

See :mod:`flights.providers.example` for a minimal, self-contained template to
copy from (it is intentionally *not* registered here, so it never shows up in
:func:`flights.core.available_providers`).
"""

from ..core.registry import register_provider
from .frontier import FrontierProvider

register_provider("frontier", FrontierProvider)

__all__ = ["FrontierProvider"]
