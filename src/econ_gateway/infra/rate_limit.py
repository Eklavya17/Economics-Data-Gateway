"""Token-bucket rate limiter shared by every worker thread that calls one upstream.

FastMCP runs each sync tool call in its own worker thread, so a burst of parallel tool calls
from one agent could otherwise fire dozens of requests at BLS or FRED at once. A token bucket
allows short bursts (``capacity``) while holding the long-run rate to ``rate_per_s``.

Callers wait for a token up to a limit and then give up, so a tool call fails with a clear
"busy, retry shortly" message instead of hanging until the tool timeout.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable


class TokenBucket:
    """Thread-safe token bucket.

    Args:
        rate_per_s: Tokens added per second (the sustained request rate).
        capacity: Maximum tokens held (the largest burst). The bucket starts full.
        clock: Monotonic clock in seconds; injectable for tests.
        sleep: Sleep function; injectable for tests.
    """

    def __init__(
        self,
        rate_per_s: float,
        capacity: float,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ):
        if rate_per_s <= 0 or capacity < 1:
            raise ValueError("rate_per_s must be > 0 and capacity >= 1")
        self.rate_per_s = rate_per_s
        self.capacity = capacity
        self._clock = clock
        self._sleep = sleep
        self._tokens = capacity
        self._updated = clock()
        self._lock = threading.Lock()

    def _refill(self, now: float) -> None:
        self._tokens = min(self.capacity, self._tokens + (now - self._updated) * self.rate_per_s)
        self._updated = now

    def _take_or_wait_time(self) -> float:
        """Take a token and return 0, or return the seconds until one will be available."""
        with self._lock:
            self._refill(self._clock())
            if self._tokens >= 1:
                self._tokens -= 1
                return 0.0
            return (1 - self._tokens) / self.rate_per_s

    def acquire(self, max_wait_s: float) -> bool:
        """Take one token, waiting up to ``max_wait_s`` seconds for it.

        Returns:
            True when a token was taken; False when none would be available in time (nothing
            is consumed in that case).
        """
        deadline = self._clock() + max_wait_s
        while True:
            wait_s = self._take_or_wait_time()
            if wait_s == 0:
                return True
            if self._clock() + wait_s > deadline:
                return False
            # Other threads may take the token first; the loop re-checks after waking.
            self._sleep(wait_s)

    @property
    def available(self) -> float:
        """Tokens available now (for status reports)."""
        with self._lock:
            self._refill(self._clock())
            return self._tokens
