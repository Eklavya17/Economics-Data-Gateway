"""Live end-to-end check of the HTTP deployment, using the same entry point as the Docker image.

Starts ``econ-gateway --transport http`` as a subprocess with JWT auth (a throwaway RSA key is
generated for the run), then over real HTTP checks:

1. the server refuses to bind 0.0.0.0 without authentication;
2. ``/health`` and ``/ready`` answer without a token;
3. the MCP endpoint rejects a missing token, a token for another audience, and a foreign Host;
4. a valid token can list tools and call them against live BLS/FRED data.

    .venv/Scripts/python.exe scripts/smoke_http.py

Exit code 0 when every check passes.
"""

from __future__ import annotations

import asyncio
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx2
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport
from fastmcp.server.auth.providers.jwt import RSAKeyPair

ROOT = Path(__file__).resolve().parents[1]
ISSUER, AUDIENCE = "https://smoke.example.com", "econ-gateway"


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _start(env_overrides: dict[str, str], port: int) -> subprocess.Popen[str]:
    env = {**os.environ, **env_overrides}
    return subprocess.Popen(
        [sys.executable, "-m", "econ_gateway.server", "--transport", "http", "--port", str(port)],
        cwd=ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def check(label: str, ok: bool, detail: str = "") -> bool:
    """Print one result line."""
    print(f"[{'ok' if ok else 'FAIL'}] {label}{': ' + detail if detail else ''}")
    return ok


def refuses_unauthenticated_remote_bind() -> bool:
    """The container default (0.0.0.0, auth off) must not start."""
    proc = _start({"ECON_HOST": "0.0.0.0", "ECON_AUTH_MODE": "none"}, _free_port())
    try:
        _, stderr = proc.communicate(timeout=60)
    except subprocess.TimeoutExpired:
        proc.kill()
        return check("refuses 0.0.0.0 without auth", False, "server started")
    return check(
        "refuses 0.0.0.0 without auth",
        proc.returncode != 0 and "Refusing to serve HTTP" in stderr,
        f"exit {proc.returncode}",
    )


async def _wait_until_up(base: str, timeout_s: float = 60) -> bool:
    deadline = time.monotonic() + timeout_s
    async with httpx2.AsyncClient() as http:
        while time.monotonic() < deadline:
            try:
                if (await http.get(f"{base}/health", timeout=2)).status_code == 200:
                    return True
            except httpx2.HTTPError:
                pass
            await asyncio.sleep(0.5)
    return False


async def _raw_initialize(url: str, headers: dict[str, str]) -> int:
    body = {
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                   "clientInfo": {"name": "smoke", "version": "0"}},
    }  # fmt: skip
    headers = {"Accept": "application/json, text/event-stream", **headers}
    async with httpx2.AsyncClient() as http:
        return (await http.post(url, json=body, headers=headers, timeout=30)).status_code


async def http_checks(base: str, keys: RSAKeyPair) -> list[bool]:
    """Probes, auth rejections and live tool calls against a running server."""
    url, results = f"{base}/mcp", []
    async with httpx2.AsyncClient() as http:
        ready = await http.get(f"{base}/ready", timeout=30)
    results.append(check("/ready without token", ready.status_code == 200, ready.text[:160]))
    results.append(check("no token -> 401", await _raw_initialize(url, {}) == 401))
    foreign = keys.create_token(issuer=ISSUER, audience="another-service")
    status = await _raw_initialize(url, {"Authorization": f"Bearer {foreign}"})
    results.append(check("token for another audience -> 401", status == 401))
    token = keys.create_token(issuer=ISSUER, audience=AUDIENCE)
    status = await _raw_initialize(
        url, {"Authorization": f"Bearer {token}", "Host": "evil.example"}
    )
    results.append(check("foreign Host header -> 421", status == 421))

    async with Client(StreamableHttpTransport(url, auth=token)) as client:
        tools = await client.list_tools()
        latest = await client.call_tool("get_latest", {"series_ids": ["LNS14000000", "CPIAUCSL"]})
        wages = await client.call_tool(
            "get_occupation_wages", {"occupation_codes": ["29-1141"], "area_ids": ["state:48"]}
        )
    results.append(check("valid token: tools listed", len(tools) == 10, f"{len(tools)} tools"))
    payload = latest.structured_content or {}
    values = [
        (r["meta"]["series_id"], r["latest_period"], r["latest_value"])
        for r in payload.get("results", [])
    ]
    results.append(
        check(
            "valid token: get_latest live",
            not latest.is_error and len(values) == 2,
            json.dumps(values),
        )
    )
    rows = (wages.structured_content or {}).get("rows", [])
    median = rows[0].get("annual_median") if rows else None
    results.append(check("valid token: OEWS wages live", bool(median), f"RN Texas median {median}"))
    return results


def main() -> int:
    """Run every check; exit 1 if any fails."""
    results = [refuses_unauthenticated_remote_bind()]
    keys = RSAKeyPair.generate()
    with tempfile.TemporaryDirectory() as tmp:
        key_file = Path(tmp) / "public.pem"
        key_file.write_text(keys.public_key, encoding="utf-8")
        port = _free_port()
        proc = _start(
            {
                "ECON_HOST": "127.0.0.1",
                "ECON_AUTH_MODE": "jwt",
                "ECON_JWT_PUBLIC_KEY_FILE": str(key_file),
                "ECON_JWT_ISSUER": ISSUER,
                "ECON_JWT_AUDIENCE": AUDIENCE,
            },
            port,
        )
        base = f"http://127.0.0.1:{port}"
        try:
            up = asyncio.run(_wait_until_up(base))
            results.append(check("/health without token", up))
            if up:
                results += asyncio.run(http_checks(base, keys))
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                proc.kill()
    print(f"\n{sum(results)}/{len(results)} checks passed")
    return 0 if all(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
