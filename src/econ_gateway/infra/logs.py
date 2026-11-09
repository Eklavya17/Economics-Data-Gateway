"""Structured JSON logging to stderr.

stdout carries the MCP protocol in stdio mode, so a single stray write there corrupts the
session. Everything (our logs, FastMCP's, the data-source clients') is routed to stderr here.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime

# Attributes every LogRecord has; anything else was passed via ``extra=`` and is emitted as a field.
_STANDARD_ATTRS = set(vars(logging.makeLogRecord({}))) | {"message", "asctime", "taskName"}


class JsonFormatter(logging.Formatter):
    """Format records as one JSON object per line, including ``extra=`` fields."""

    def format(self, record: logging.LogRecord) -> str:
        """Render ``record`` as a JSON line."""
        payload: dict[str, object] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        payload.update({k: v for k, v in vars(record).items() if k not in _STANDARD_ATTRS})
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging(level: str = "WARNING") -> None:
    """Send all logging to stderr as JSON at ``level``. Safe to call more than once."""
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
