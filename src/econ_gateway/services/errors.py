"""Errors raised by services.

Every message is shown to the client model as-is, so it must say what went wrong *and* what
to do next (docs/RESEARCH.md §2, "Errors"). Never put secrets or stack traces in a message.
"""

from __future__ import annotations


class GatewayError(Exception):
    """Base class for expected failures whose message is safe and useful for the client model."""


class InvalidRequestError(GatewayError):
    """The arguments were well-formed but cannot be served (e.g. start date after end date)."""


class SeriesNotFoundError(GatewayError):
    """The source has no series with the requested ID."""


class ConfigurationError(GatewayError):
    """The server is missing configuration needed for this request (e.g. an API key)."""


class DataNotAvailableError(GatewayError):
    """The source has not published this slice (e.g. an OEWS year or QCEW quarter not yet out)."""


class UpstreamError(GatewayError):
    """BLS or FRED failed or refused the request (outage, quota, timeout)."""
