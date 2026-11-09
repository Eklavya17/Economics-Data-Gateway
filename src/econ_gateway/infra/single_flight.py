"""Merge identical concurrent requests so the upstream is called once.

Agents often fire the same lookup from several parallel tool calls (e.g. CPI for both
``real_wage_growth`` and ``compare_series``). The first caller for a key does the work; callers
that arrive while it is running wait and share its result or exception. Nothing is cached
after the call finishes: caching is the disk cache's job. (Go's ``singleflight`` package is the
usual reference for this pattern.)
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Hashable
from typing import Any, TypeVar

T = TypeVar("T")


class _Call:
    """One in-flight call: its completion event and outcome."""

    def __init__(self) -> None:
        self.done = threading.Event()
        self.result: Any = None
        self.error: BaseException | None = None


class SingleFlight:
    """Run at most one call per key at a time; concurrent duplicates share the outcome."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._calls: dict[Hashable, _Call] = {}

    def do(self, key: Hashable, fn: Callable[[], T]) -> tuple[T, bool]:
        """Run ``fn`` unless an identical call is in flight; then wait for that one instead.

        Args:
            key: Identity of the request (e.g. ``("fred", series_id, start, end)``).
            fn: Does the work.

        Returns:
            ``(result, shared)``: ``shared`` is True when the result came from another
            caller's run. Shared results are the same object, so callers must not mutate them.

        Raises:
            BaseException: Whatever ``fn`` raised, in the leader and in every waiting caller.
        """
        with self._lock:
            call = self._calls.get(key)
            leader = call is None
            if call is None:
                call = self._calls[key] = _Call()
        if not leader:
            call.done.wait()
            if call.error is not None:
                raise call.error
            return call.result, True
        try:
            call.result = fn()
        except BaseException as exc:
            call.error = exc
            raise
        finally:
            with self._lock:
                del self._calls[key]
            call.done.set()
        return call.result, False

    @property
    def in_flight(self) -> int:
        """Number of keys currently being fetched."""
        with self._lock:
            return len(self._calls)
