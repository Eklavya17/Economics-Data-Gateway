"""Streamable HTTP serving: authentication, health routes, host checks and the uvicorn runner.

stdio is still the recommended transport for a local client. HTTP is for shared or hosted
deployments (Docker, a team server), and adds requirements stdio does not have
(docs/RESEARCH.md section 7):

- **Authentication**: bearer JWTs verified against a public key or the issuer's JWKS, with the
  issuer *and* audience checked so tokens minted for other services are refused (no token
  passthrough). ``Settings.http_safety_error`` refuses to expose an unauthenticated endpoint on a
  non-loopback address.
- **DNS-rebinding protection**: FastMCP's Host/Origin guard in strict mode. Loopback names are
  always accepted; anything else must be listed in ``ECON_ALLOWED_HOSTS`` / ``_ORIGINS``.
- **Stateless requests** (``ECON_STATELESS_HTTP``, default on): no server-side sessions, so
  replicas can sit behind a load balancer.
- **Probes**: ``/health`` (liveness) and ``/ready`` (readiness) are outside authentication, as
  orchestrators expect; their bodies contain nothing sensitive (``services/health.py``).
"""

from __future__ import annotations

from typing import Any

import anyio
import uvicorn
from fastmcp import FastMCP
from fastmcp.server.auth import AuthProvider
from fastmcp.server.auth.providers.jwt import JWTVerifier
from starlette.requests import Request
from starlette.responses import JSONResponse

from econ_gateway.config import Settings
from econ_gateway.services.health import liveness, readiness
from econ_gateway.services.upstream import Upstream


def build_auth(settings: Settings) -> AuthProvider | None:
    """The token verifier for ``ECON_AUTH_MODE`` (``None`` when auth is off).

    Settings validation has already ensured exactly one key source plus issuer and audience.
    """
    if settings.auth_mode == "none":
        return None
    return JWTVerifier(
        public_key=settings.jwt_key(),
        jwks_uri=settings.jwt_jwks_uri,
        issuer=settings.jwt_issuer,
        audience=settings.jwt_audience,
        algorithm=settings.jwt_algorithm,
        required_scopes=settings.jwt_required_scopes or None,
    )


def register_health_routes(mcp: FastMCP, upstream: Upstream, settings: Settings) -> None:
    """Add ``GET /health`` and ``GET /ready`` to the server's HTTP app."""
    toolsets = list(settings.toolsets)

    @mcp.custom_route("/health", methods=["GET"], include_in_schema=False)
    async def health(request: Request) -> JSONResponse:
        return JSONResponse(liveness())

    @mcp.custom_route("/ready", methods=["GET"], include_in_schema=False)
    async def ready(request: Request) -> JSONResponse:
        # The first catalog load reads a CSV; keep it off the event loop.
        ok, body = await anyio.to_thread.run_sync(readiness, upstream, toolsets)
        return JSONResponse(body, status_code=200 if ok else 503)


def http_app_options(settings: Settings) -> dict[str, Any]:
    """Keyword arguments for ``FastMCP.http_app`` (also used by tests via ``asgi_server``)."""
    return {
        "path": settings.http_path,
        "stateless_http": settings.stateless_http,
        "host_origin_protection": True,  # strict: check Host and Origin on every request
        "allowed_hosts": list(settings.allowed_hosts),
        "allowed_origins": list(settings.allowed_origins),
    }


def serve_http(mcp: FastMCP, settings: Settings, *, host: str, port: int) -> None:
    """Run the Streamable HTTP app with uvicorn until interrupted.

    Raises:
        SystemExit: When the bind address and auth settings are unsafe together.
    """
    problem = settings.model_copy(update={"host": host}).http_safety_error()
    if problem:
        raise SystemExit(problem)
    app = mcp.http_app(**http_app_options(settings))
    uvicorn.run(
        app,
        host=host,
        port=port,
        log_level=settings.log_level.lower(),
        # Honor X-Forwarded-* only from a proxy on this machine (uvicorn's default).
        proxy_headers=True,
        server_header=False,
    )
