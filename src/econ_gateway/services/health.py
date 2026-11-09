"""Liveness and readiness reports for the HTTP endpoints ``/health`` and ``/ready``.

- **Liveness** (``/health``): the process is up and serving requests. Constant and cheap, so
  orchestrators can poll it often.
- **Readiness** (``/ready``): the server can answer tool calls. That needs the local catalog;
  it does *not* need BLS or FRED to be up, because cached data is served during an outage
  (decision 25). An open circuit makes the status ``degraded`` but keeps the replica in
  rotation: taking every replica out because BLS is down would turn a partial outage into a
  full one.

Both bodies are safe to expose without authentication: no key values, quotas or request
details, only whether keys are configured and each upstream's circuit state.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from econ_gateway import __version__
from econ_gateway.services.upstream import Upstream


def liveness() -> dict[str, Any]:
    """Body of ``/health``."""
    return {"status": "ok", "version": __version__}


def _circuit_states(status: dict[str, object]) -> dict[str, str]:
    states: dict[str, str] = {}
    for name, info in status.items():
        circuit = info.get("circuit") if isinstance(info, dict) else None
        if isinstance(circuit, dict) and "state" in circuit:
            states[name] = str(circuit["state"])
    return states


def readiness(upstream: Upstream, toolsets: Sequence[str]) -> tuple[bool, dict[str, Any]]:
    """Body of ``/ready`` and whether the server is ready.

    Returns:
        ``(ready, body)``; ``ready`` is False only when the catalog cannot be loaded.
    """
    try:
        catalog_series = len(upstream.catalog())
        catalog: dict[str, Any] = {"ok": catalog_series > 0, "series": catalog_series}
    except Exception as exc:  # reported, never raised: a probe must always get an answer
        catalog = {"ok": False, "error": type(exc).__name__}
    circuits = _circuit_states(upstream.status())
    ready = bool(catalog["ok"])
    degraded = any(state != "closed" for state in circuits.values())
    status = "not_ready" if not ready else ("degraded" if degraded else "ready")
    body = {
        "status": status,
        "version": __version__,
        "toolsets": list(toolsets),
        "checks": {
            "catalog": catalog,
            "upstream_circuits": circuits,
            "keys_configured": {"bls": upstream.has_bls_key, "fred": upstream.has_fred_key},
        },
    }
    return ready, body
