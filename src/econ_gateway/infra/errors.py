"""Exceptions raised when the gateway itself stops an outbound request.

They subclass ``requests.RequestException`` on purpose: they are raised from inside the HTTP
session that the data-source clients use, so any code that already handles network failures
(the clients, the gateway's upstream layer) treats them as "the upstream could not be
reached" without special cases. The upstream layer catches ``GuardRejected`` first to turn its
``advice`` into a message for the client model.
"""

from __future__ import annotations

import requests


class GuardRejected(requests.RequestException):
    """A request was refused locally, before reaching the upstream.

    Attributes:
        upstream: Upstream name (``BLS``, ``FRED``, ...).
        advice: Sentence for the client model: what happened and when to retry.
        retry_after_s: Seconds until a retry can succeed, when known.
    """

    def __init__(self, upstream: str, advice: str, retry_after_s: float | None = None):
        super().__init__(advice)
        self.upstream = upstream
        self.advice = advice
        self.retry_after_s = retry_after_s


class CircuitOpenError(GuardRejected):
    """The upstream failed repeatedly, so requests are paused for a cool-down period."""


class RateLimitedError(GuardRejected):
    """The gateway's own request budget for the upstream is exhausted for the moment."""


class QuotaExhaustedError(GuardRejected):
    """The upstream's daily query quota is used up (BLS: 500 queries/day with a key)."""


class DisallowedHostError(requests.RequestException):
    """A request targeted a host outside the allow-list, or used plain HTTP (CLAUDE.md rule 7)."""
