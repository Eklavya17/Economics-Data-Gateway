"""Daily query quota tracking (BLS allows 500 queries/day with a key, 25 without).

The count is persisted to a small JSON file so a restarted server (Claude Desktop restarts
stdio servers often) does not forget what it has already spent. The day rolls over at
midnight in ``tz``. BLS does not document its reset time; US Eastern is assumed because the
API is operated from Washington, D.C. If BLS itself reports the quota as used up, the gateway
believes it (``mark_exhausted``) even when its own count disagrees, e.g. because the same key
is used by Project 1 or another machine.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from econ_gateway.infra.errors import QuotaExhaustedError

log = logging.getLogger(__name__)


class DailyQuota:
    """Thread-safe daily request counter with an optional on-disk copy.

    Args:
        name: Upstream name used in messages.
        limit: Requests allowed per day.
        path: JSON file for the count; ``None`` keeps it in memory only.
        tz: Time zone whose midnight starts a new day.
        clock: Wall clock in epoch seconds; injectable for tests.
    """

    def __init__(
        self,
        name: str,
        *,
        limit: int,
        path: Path | None,
        tz: ZoneInfo,
        clock: Callable[[], float] = time.time,
    ):
        self.name = name
        self.limit = limit
        self._path = path
        self._tz = tz
        self._clock = clock
        self._lock = threading.Lock()
        self._day, self._used, self._exhausted = self._load()

    # ------------------------------------------------------------ queries

    def remaining(self) -> int:
        """Requests left today (0 once the upstream has said the quota is used up)."""
        with self._lock:
            self._roll_over()
            return 0 if self._exhausted else max(self.limit - self._used, 0)

    def check(self, reserve: int = 0) -> None:
        """Raise unless more than ``reserve`` requests are left today.

        Args:
            reserve: Requests to keep back. Optional calls (series metadata) pass a reserve so
                they never spend the last queries that data requests need.

        Raises:
            QuotaExhaustedError: When the quota (minus the reserve) is used up.
        """
        if self.remaining() > reserve:
            return
        hours = self.seconds_until_reset() / 3600
        raise QuotaExhaustedError(
            self.name,
            f"The gateway's {self.name} daily query quota ({self.limit}) is used up; it resets "
            f"at midnight US Eastern, in about {hours:.0f} h.",
            self.seconds_until_reset(),
        )

    def seconds_until_reset(self) -> float:
        """Seconds until the next local midnight."""
        now = datetime.fromtimestamp(self._clock(), self._tz)
        midnight = datetime.combine(now.date() + timedelta(days=1), datetime.min.time(), self._tz)
        return (midnight - now).total_seconds()

    def snapshot(self) -> dict[str, object]:
        """State for status reports and logs."""
        remaining = self.remaining()
        with self._lock:
            return {
                "day": self._day,
                "used": self._used,
                "limit": self.limit,
                "remaining": remaining,
            }

    # ------------------------------------------------------------ updates

    def consume(self, count: int = 1) -> None:
        """Record ``count`` requests sent."""
        with self._lock:
            self._roll_over()
            self._used += count
            self._save()

    def mark_exhausted(self) -> None:
        """Record that the upstream refused a request because the daily quota is used up."""
        with self._lock:
            self._roll_over()
            self._exhausted = True
            self._save()

    # ------------------------------------------------------------ persistence

    def _today(self) -> str:
        return datetime.fromtimestamp(self._clock(), self._tz).date().isoformat()

    def _roll_over(self) -> None:
        """Start a fresh count when the local date has changed (call with the lock held)."""
        today = self._today()
        if today != self._day:
            self._day, self._used, self._exhausted = today, 0, False

    def _load(self) -> tuple[str, int, bool]:
        today = self._today()
        if self._path is None or not self._path.exists():
            return today, 0, False
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            log.warning("quota file unreadable; starting from zero", extra={"err": str(exc)})
            return today, 0, False
        if data.get("day") != today:
            return today, 0, False
        return today, int(data.get("used", 0)), bool(data.get("exhausted", False))

    def _save(self) -> None:
        """Write the count; a failed write only costs accuracy after a restart, so just log it."""
        if self._path is None:
            return
        payload = {"day": self._day, "used": self._used, "exhausted": self._exhausted}
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_suffix(".tmp")
            tmp.write_text(json.dumps(payload), encoding="utf-8")
            tmp.replace(self._path)  # atomic on the same filesystem: never a half-written file
        except OSError as exc:
            log.warning("could not save quota file", extra={"err": str(exc)})
