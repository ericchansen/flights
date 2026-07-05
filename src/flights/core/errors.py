"""Exception hierarchy shared by all flight providers."""


class FlightsError(RuntimeError):
    """Base class for every error raised by this library."""


class ProviderError(FlightsError):
    """A provider's backend returned an error or an unexpected response."""


class MarketNotFoundError(ProviderError):
    """The requested origin/destination pair is not a market the airline sells."""


class AuthError(ProviderError):
    """The provider could not obtain or refresh the credentials it needs."""
