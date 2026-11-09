# Deployment: Streamable HTTP, auth, Docker, tracing

stdio is the right transport for a client on the same machine (see the README's quick start).
Use Streamable HTTP for a shared or hosted gateway.

## Run over HTTP

```bash
.venv/Scripts/econ-gateway.exe --transport http
```

The MCP endpoint is then `http://127.0.0.1:8000/mcp`. On loopback no token is needed; any other
bind address requires authentication.

## Settings (HTTP only)

| Variable | Default | Meaning |
|---|---|---|
| `ECON_HOST` / `ECON_PORT` | `127.0.0.1` / `8000` | Bind address |
| `ECON_AUTH_MODE` | `none` | `jwt`: every MCP request needs `Authorization: Bearer <JWT>` |
| `ECON_JWT_JWKS_URI` | | The issuer's JWKS URL (HTTPS; http only for localhost). Or use one of the next two |
| `ECON_JWT_PUBLIC_KEY` / `ECON_JWT_PUBLIC_KEY_FILE` | | PEM public key, inline or from a file |
| `ECON_JWT_ISSUER` / `ECON_JWT_AUDIENCE` | | Required `iss` and `aud`; tokens issued for other services are refused |
| `ECON_JWT_ALGORITHM` | `RS256` | Accepted signing algorithm |
| `ECON_JWT_REQUIRED_SCOPES` | | Comma-separated scopes every token must carry |
| `ECON_ALLOWED_HOSTS` / `ECON_ALLOWED_ORIGINS` | | Extra `Host` / `Origin` values (e.g. your public hostname behind a proxy). Loopback is always allowed |
| `ECON_ALLOW_UNAUTHENTICATED_REMOTE` | `false` | Allow a non-loopback bind with auth off, only when a proxy in front authenticates |
| `ECON_HTTP_PATH` | `/mcp` | MCP endpoint path |
| `ECON_STATELESS_HTTP` | `true` | No server-side sessions, so replicas can run behind a load balancer |

## What the server enforces

- **No open endpoint on the network.** Binding anything but loopback with `ECON_AUTH_MODE=none`
  stops at startup with an explanation.
- **Tokens must be for this server.** Issuer and audience are mandatory with `jwt`, as the MCP
  spec requires (no token passthrough). The gateway verifies tokens from your identity provider;
  it does not issue them or run an OAuth flow.
- **DNS-rebinding protection.** Requests with an unknown `Host` get 421, and requests with an
  unknown `Origin` get 403.
- **Probes without a token.** `GET /health` (liveness) and `GET /ready` (readiness: catalog
  loaded; `degraded` while an upstream circuit is open, but still 200 because cached data keeps
  flowing). Neither exposes keys, quotas or request details.

## Docker

Build from the repository root:

```bash
docker build -t econ-gateway:1.0.0 .
```

Or use `docker-compose.yml`. It adds a read-only root filesystem,
drops all capabilities, runs as a non-root user, and publishes the port on loopback only. It
reads `.env.docker`:

```bash
docker compose up --build
```

A minimal `.env.docker` (never commit it):

```
BLS_API_KEY=...
FRED_API_KEY=...
ECON_AUTH_MODE=jwt
ECON_JWT_JWKS_URI=https://your-issuer.example.com/.well-known/jwks.json
ECON_JWT_ISSUER=https://your-issuer.example.com/
ECON_JWT_AUDIENCE=econ-gateway
ECON_ALLOWED_HOSTS=econ.example.com
```

The image binds 0.0.0.0, so without the auth settings (or the explicit
`ECON_ALLOW_UNAUTHENTICATED_REMOTE=true`) it refuses to start. Caches, OEWS downloads and the BLS
quota count live on the `/data` volume. After a new OEWS release, pre-load it in the container:

```bash
docker compose run --rm econ-gateway python scripts/warm_cache.py
```

## Tracing

FastMCP emits an OpenTelemetry span for every MCP request. The gateway adds spans for each
BLS/FRED fetch, marked when served stale, and for each guarded HTTP request: upstream, status,
and which guard refused it. Spans never contain URLs, because FRED requests carry the API key.
Install the extra and point it at any OTLP collector:

```bash
uv pip install --python .venv/Scripts/python.exe -e ".[otel]"
```

Then set `ECON_TELEMETRY=otlp` and `OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318`.
`ECON_TELEMETRY=console` prints spans to stderr, which is safe in stdio mode.

## Checking a deployment

`scripts/smoke_http.py` starts the server with the same command as the image, with JWT auth and a
throwaway key. Over real HTTP it checks the probes, rejected tokens and Host headers, the refusal
to bind 0.0.0.0 without auth, and live tool calls:

```bash
.venv/Scripts/python.exe scripts/smoke_http.py
```
