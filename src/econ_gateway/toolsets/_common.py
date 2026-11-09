"""Helpers shared by every toolset."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations

from econ_gateway.services.errors import GatewayError

# Every gateway tool only reads public data from external systems. Set explicitly because the
# MCP defaults assume a tool may be destructive and non-idempotent (docs/RESEARCH.md §6).
READ_ONLY_EXTERNAL = ToolAnnotations(
    read_only_hint=True,
    destructive_hint=False,
    idempotent_hint=True,
    open_world_hint=True,
)


@contextmanager
def tool_errors() -> Iterator[None]:
    """Convert expected service failures into ``ToolError`` so the client model sees the message.

    ``ToolError`` text always reaches the client and is returned as a tool result with
    ``isError: true``, so the model can correct itself. Anything else is unexpected and stays
    masked by ``mask_error_details=True`` on the server.
    """
    try:
        yield
    except GatewayError as exc:
        raise ToolError(str(exc)) from exc
