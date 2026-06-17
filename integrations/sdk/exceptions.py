class IntegrationError(Exception):
    """Base exception for all Integration SDK errors."""
    pass

class IntegrationAuthError(IntegrationError):
    """Raised when an integration fails due to invalid/expired credentials.
    Catching this should trigger a re-authentication prompt for the user.
    """
    pass

class IntegrationRateLimitError(IntegrationError):
    """Raised when an API rate limit is exceeded and max retries are exhausted."""
    pass

class IntegrationDataError(IntegrationError):
    """Raised when the third-party API returns malformed or unexpected data."""
    pass
