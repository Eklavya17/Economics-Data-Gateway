"""Phase 7: settings, Streamable HTTP, JWT auth, probes, host checks and tracing.

The HTTP app is the real one (``FastMCP.http_app`` with our options) served in-process by
``fastmcp.utilities.tests.asgi_server``: auth middleware, Host/Origin guard, session handling
and custom routes all run as in production, without binding a port.
"""

from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest
from fastmcp.server.auth.providers.jwt import RSAKeyPair
from fastmcp.utilities.tests import ASGIServer, asgi_server
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from pydantic import ValidationError

from conftest import FakeUpstream
from econ_gateway.config import Settings
from econ_gateway.http_server import build_auth, http_app_options
from econ_gateway.infra.telemetry import configure_telemetry
from econ_gateway.server import create_server

ISSUER = "https://auth.example.com"
AUDIENCE = "econ-gateway"

# ---------------------------------------------------------------- settings


def test_csv_settings_parse_from_environment(monkeypatch):
    monkeypatch.setenv("ECON_TOOLSETS", "wages,regional")
    monkeypatch.setenv("ECON_ALLOWED_HOSTS", "gw.example.com, api.example.com")
    settings = Settings()
    assert settings.toolsets == ["core", "wages", "regional"]
    assert settings.allowed_hosts == ["gw.example.com", "api.example.com"]


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"jwt_issuer": ISSUER, "jwt_audience": AUDIENCE}, "exactly one of"),
        ({"jwt_public_key": "k", "jwt_jwks_uri": "https://x/jwks", "jwt_issuer": ISSUER,
          "jwt_audience": AUDIENCE}, "exactly one of"),
        ({"jwt_public_key": "k", "jwt_issuer": ISSUER}, "ECON_JWT_AUDIENCE"),
        ({"jwt_jwks_uri": "http://auth.example.com/jwks", "jwt_issuer": ISSUER,
          "jwt_audience": AUDIENCE}, "must use https"),
    ],
)  # fmt: skip
def test_jwt_settings_are_validated(overrides, message):
    with pytest.raises(ValidationError, match=message):
        Settings(auth_mode="jwt", **overrides)


def test_local_jwks_over_http_is_allowed_for_development():
    settings = Settings(
        auth_mode="jwt", jwt_jwks_uri="http://localhost:9000/jwks", jwt_issuer=ISSUER,
        jwt_audience=AUDIENCE,
    )  # fmt: skip
    assert settings.jwt_jwks_uri


def test_remote_bind_without_auth_is_refused():
    assert Settings(host="127.0.0.1").http_safety_error() is None
    assert "Refusing to serve HTTP on 0.0.0.0" in (
        Settings(host="0.0.0.0").http_safety_error() or ""
    )
    assert Settings(host="0.0.0.0", allow_unauthenticated_remote=True).http_safety_error() is None
    with_auth = Settings(
        host="0.0.0.0", auth_mode="jwt", jwt_public_key="k", jwt_issuer=ISSUER,
        jwt_audience=AUDIENCE,
    )  # fmt: skip
    assert with_auth.http_safety_error() is None


# ---------------------------------------------------------------- HTTP without auth


@asynccontextmanager
async def _serve(
    settings: Settings, upstream: FakeUpstream | None = None
) -> AsyncIterator[ASGIServer]:
    server = create_server(settings, upstream or FakeUpstream(), auth=build_auth(settings))
    async with asgi_server(server, **http_app_options(settings)) as running:
        yield running


@pytest.fixture
async def open_server() -> AsyncIterator[ASGIServer]:
    async with _serve(Settings()) as running:
        yield running


async def test_health_and_ready(open_server: ASGIServer):
    async with open_server.http_client() as http:
        health = await http.get("/health")
        ready = await http.get("/ready")
    assert health.status_code == 200 and health.json()["status"] == "ok"
    body = ready.json()
    assert ready.status_code == 200 and body["status"] == "ready"
    assert body["checks"]["catalog"]["series"] == 9
    assert body["checks"]["upstream_circuits"] == {"BLS": "closed", "FRED": "closed"}
    assert "quota" not in json.dumps(body)  # nothing operational beyond what probes need


async def test_ready_reports_degraded_but_stays_in_rotation():
    upstream = FakeUpstream()
    upstream.status = lambda: {"BLS": {"circuit": {"state": "open"}}}  # type: ignore[method-assign]
    async with _serve(Settings(), upstream) as running, running.http_client() as http:
        response = await http.get("/ready")
    assert response.status_code == 200 and response.json()["status"] == "degraded"


async def test_ready_fails_without_catalog():
    upstream = FakeUpstream()

    def broken_catalog():
        raise FileNotFoundError("series_catalog.csv")

    upstream.catalog = broken_catalog  # type: ignore[method-assign]
    async with _serve(Settings(), upstream) as running, running.http_client() as http:
        response = await http.get("/ready")
    assert response.status_code == 503 and response.json()["status"] == "not_ready"


async def test_tools_work_over_http(open_server: ASGIServer):
    async with open_server.client() as client:
        tools = {t.name for t in await client.list_tools()}
        result = await client.call_tool("get_latest", {"series_ids": ["LNS14000000"]})
    assert "get_series" in tools
    assert result.structured_content is not None
    assert result.structured_content["results"][0]["meta"]["series_id"] == "LNS14000000"


async def test_unknown_host_header_is_rejected(open_server: ASGIServer):
    async with open_server.http_client(headers={"Host": "attacker.example.com"}) as http:
        response = await http.get("/health")
    assert response.status_code == 421


async def test_configured_host_is_accepted():
    settings = Settings(allowed_hosts=["gw.example.com"])
    host = {"Host": "gw.example.com"}
    async with _serve(settings) as running, running.http_client(headers=host) as http:
        response = await http.get("/health")
    assert response.status_code == 200


# ---------------------------------------------------------------- HTTP with JWT auth


@pytest.fixture(scope="module")
def keys() -> RSAKeyPair:
    return RSAKeyPair.generate()


@pytest.fixture
async def secured(keys: RSAKeyPair) -> AsyncIterator[ASGIServer]:
    settings = Settings(
        auth_mode="jwt", jwt_public_key=keys.public_key, jwt_issuer=ISSUER,
        jwt_audience=AUDIENCE,
    )  # fmt: skip
    async with _serve(settings) as running:
        yield running


async def _mcp_status(server: ASGIServer, token: str | None) -> int:
    """Status of a raw MCP initialize request with the given bearer token."""
    headers = {"Accept": "application/json, text/event-stream"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    body = {
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                   "clientInfo": {"name": "test", "version": "0"}},
    }  # fmt: skip
    async with server.http_client(headers=headers) as http:
        return (await http.post(server.url, json=body)).status_code


async def test_valid_token_can_call_tools(secured: ASGIServer, keys: RSAKeyPair):
    token = keys.create_token(issuer=ISSUER, audience=AUDIENCE)
    async with secured.client(auth=token) as client:
        result = await client.call_tool("get_latest", {"series_ids": ["LNS14000000"]})
    assert result.is_error is False


async def test_missing_token_is_rejected(secured: ASGIServer):
    assert await _mcp_status(secured, None) == 401


@pytest.mark.parametrize(
    "claims",
    [
        {"issuer": ISSUER, "audience": "some-other-service"},  # token passthrough
        {"issuer": "https://evil.example.com", "audience": AUDIENCE},
        {"issuer": ISSUER, "audience": AUDIENCE, "expires_in_seconds": -60},
    ],
)
async def test_wrong_or_expired_tokens_are_rejected(secured: ASGIServer, keys: RSAKeyPair, claims):
    assert await _mcp_status(secured, keys.create_token(**claims)) == 401


async def test_token_signed_by_another_key_is_rejected(secured: ASGIServer):
    forged = RSAKeyPair.generate().create_token(issuer=ISSUER, audience=AUDIENCE)
    assert await _mcp_status(secured, forged) == 401


async def test_probes_need_no_token(secured: ASGIServer):
    async with secured.http_client() as http:
        assert (await http.get("/health")).status_code == 200
        assert (await http.get("/ready")).status_code == 200


# ---------------------------------------------------------------- tracing


@pytest.fixture(scope="module")
def spans() -> InMemorySpanExporter:
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    trace.set_tracer_provider(provider)  # global, set once for this module
    return exporter


def test_guarded_requests_are_traced_without_urls(spans: InMemorySpanExporter):
    from test_reliability import FakeTransport, _guarded_session

    session, _ = _guarded_session(FakeTransport(status=200))
    session.get("https://api.bls.gov/x?registrationkey=SECRET&api_key=SECRET")
    (span,) = [s for s in spans.get_finished_spans() if s.name == "upstream.http"]
    attributes = dict(span.attributes or {})
    assert attributes["upstream.name"] == "BLS"
    assert attributes["http.response.status_code"] == 200
    assert "SECRET" not in json.dumps(attributes) and "api.bls.gov" not in json.dumps(attributes)


def test_telemetry_off_installs_nothing():
    assert configure_telemetry("off", service_name="t", version="0") is False


def test_console_spans_go_to_stderr_not_stdout():
    """In stdio mode stdout is the protocol stream: console spans must never touch it."""
    code = (
        "from econ_gateway.infra.telemetry import configure_telemetry, span\n"
        "configure_telemetry('console', service_name='t', version='0')\n"
        "with span('probe.span'):\n"
        "    pass\n"
    )
    done = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=60, check=True
    )
    assert done.stdout == ""
    assert "probe.span" in done.stderr


async def test_raw_request_with_valid_token_succeeds(secured: ASGIServer, keys: RSAKeyPair):
    """Control for the 401 tests: the same raw request with a good token is accepted."""
    assert await _mcp_status(secured, keys.create_token(issuer=ISSUER, audience=AUDIENCE)) == 200
