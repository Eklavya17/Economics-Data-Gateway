"""Circuit breaker: stop calling an upstream that keeps failing, then probe it again later.

States (the standard pattern, e.g. Nygard, *Release It!*):

- **closed**: requests flow. Consecutive failures are counted; a success resets the count.
- **open**: after ``failure_threshold`` consecutive failures, requests fail immediately for
  ``reset_timeout_s``. The caller can serve cached data instead (the gateway's stale fallback),
  and the upstream is spared a retry storm while it recovers.
- **half_open**: after the cool-down, exactly one probe request is let through. Success closes
  the circuit; failure re-opens it for another cool-down.

What counts as a failure is decided by the caller (``infra/http.py``): network errors, timeouts,
HTTP 429 and 5xx. A 4xx such as "series does not exist" means the upstream is healthy.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import Literal

from econ_gateway.infra.errors import CircuitOpenError

BreakerState = Literal["closed", "open", "half_open"]


class CircuitBreaker:
    """Thread-safe consecutive-failure circuit breaker for one upstream.

    Args:
        name: Upstream name used in messages (``BLS``, ``FRED``).
        failure_threshold: Consecutive failures that open the circuit.
        reset_timeout_s: Seconds the circuit stays open before a probe is allowed.
        clock: Monotonic clock in seconds; injectable for tests.
    """

    def __init__(
        self,
        name: str,
        *,
        failure_threshold: int,
        reset_timeout_s: float,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.name = name
        self.failure_threshold = failure_threshold
        self.reset_timeout_s = reset_timeout_s
        self._clock = clock
        self._lock = threading.Lock()
        self._state: BreakerState = "closed"
        self._failures = 0
        self._opened_at = 0.0
        self._probe_in_flight = False

    @property
    def state(self) -> BreakerState:
        """Current state; an open circuit whose cool-down has passed reports ``half_open``."""
        with self._lock:
            if self._state == "open" and self._cooldown_left() <= 0:
                return "half_open"
            return self._state

    def before_call(self) -> None:
        """Admit a request or refuse it.

        Every admitted request must be followed by ``record_success`` or ``record_failure``.

        Raises:
            CircuitOpenError: While the circuit is open, or while a half-open probe is running.
        """
        with self._lock:
            if self._state == "open":
                if self._cooldown_left() > 0:
                    raise self._open_error(self._cooldown_left())
                self._state = "half_open"
            if self._state == "half_open":
                if self._probe_in_flight:
                    raise self._open_error(None)
                self._probe_in_flight = True

    def record_success(self) -> None:
        """The upstream answered; close the circuit."""
        with self._lock:
            self._state = "closed"
            self._failures = 0
            self._probe_in_flight = False

    def record_failure(self) -> None:
        """The upstream failed; open the circuit at the threshold (or if the probe failed)."""
        with self._lock:
            self._failures += 1
            if self._state == "half_open" or self._failures >= self.failure_threshold:
                self._state = "open"
                self._opened_at = self._clock()
            self._probe_in_flight = False

    def snapshot(self) -> dict[str, object]:
        """State for status reports and logs."""
        state = self.state
        with self._lock:
            left = self._cooldown_left() if self._state == "open" else 0.0
            return {
                "state": state,
                "consecutive_failures": self._failures,
                "retry_after_s": round(max(left, 0.0), 1),
            }

    def _cooldown_left(self) -> float:
        return self.reset_timeout_s - (self._clock() - self._opened_at)

    def _open_error(self, retry_after_s: float | None) -> CircuitOpenError:
        wait = f"about {retry_after_s:.0f} s" if retry_after_s else "a few seconds"
        return CircuitOpenError(
            self.name,
            f"{self.name} has failed {self._failures} times in a row, so the gateway is pausing "
            f"requests to it. Retry in {wait}.",
            retry_after_s,
        )
