"""Gateway settings, read from environment variables (prefix ``ECON_``) and ``.env``.

Upstream API keys (``BLS_API_KEY``, ``FRED_API_KEY``) are deliberately *not* modelled here:
the data-source clients read them directly from the environment, and keeping a second copy would
create two sources of truth. ``has_bls_key`` / ``has_fred_key`` only report whether they are set.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal
from urllib.parse import urlsplit

from dotenv import load_dotenv
from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]

Toolset = Literal["core", "wages", "regional", "compute"]
AVAILABLE_TOOLSETS: tuple[Toolset, ...] = ("core", "wages", "regional", "compute")
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})

# Comma-separated in the environment. NoDecode stops pydantic-settings from parsing the value as
# JSON first, which fails for "core,wages" before our splitter runs (DESIGN.md decision 42).
CsvList = Annotated[list[str], NoDecode]


def load_env_files() -> None:
    """Load the gateway's ``.env`` into ``os.environ`` without overriding existing variables."""
    load_dotenv(PROJECT_ROOT / ".env", override=False)


class Settings(BaseSettings):
    """Runtime settings for the gateway.

    Attributes:
        log_level: Minimum level for stderr logs.
        toolsets: Toolsets to mount. Fewer toolsets means fewer tools for the client model to
            choose between (see docs/RESEARCH.md, github-mcp-server toolsets).
        transport: Default transport for ``econ-gateway`` when no CLI flag is given.
        host: Bind address for the HTTP transport.
        port: Port for the HTTP transport.
        state_dir: Small runtime state that must survive restarts (the BLS daily query count).
        http_path: URL path of the MCP endpoint.
        stateless_http: Serve each HTTP request independently (no server-side sessions), so
            several replicas can sit behind a load balancer. The tools need no session state.
        auth_mode: ``none`` or ``jwt`` (bearer tokens verified against a public key or JWKS).
        jwt_jwks_uri: JWKS URL of the token issuer (HTTPS; http only for localhost).
        jwt_public_key: PEM public key (or HMAC secret) instead of a JWKS URL.
        jwt_public_key_file: File holding the PEM public key.
        jwt_issuer: Required ``iss`` claim.
        jwt_audience: Required ``aud`` claim: tokens must be issued for this server.
        jwt_algorithm: Signing algorithm to accept.
        jwt_required_scopes: Scopes every token must carry.
        allowed_hosts: Extra ``Host`` header values accepted over HTTP (e.g. the public name
            behind a reverse proxy). Loopback names are always accepted.
        allowed_origins: Browser origins accepted over HTTP.
        allow_unauthenticated_remote: Permit binding a non-loopback address with auth off.
            Only for deployments where a proxy in front already authenticates every request.
        telemetry: ``off``, ``otlp`` (export traces via the standard ``OTEL_EXPORTER_OTLP_*``
            variables) or ``console`` (print spans to stderr, for debugging).
    """

    model_config = SettingsConfigDict(env_prefix="ECON_", extra="ignore")

    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "WARNING"
    toolsets: Annotated[list[Toolset], NoDecode] = Field(
        default_factory=lambda: list(AVAILABLE_TOOLSETS)
    )
    transport: Literal["stdio", "http"] = "stdio"
    host: str = "127.0.0.1"
    port: int = Field(default=8000, ge=1, le=65535)
    state_dir: Path = PROJECT_ROOT / ".state"
    # ---------------------------------------------------------------- HTTP (Phase 7)
    http_path: str = Field(default="/mcp", pattern=r"^/[A-Za-z0-9_/-]*$")
    stateless_http: bool = True
    auth_mode: Literal["none", "jwt"] = "none"
    jwt_jwks_uri: str | None = None
    jwt_public_key: str | None = None
    jwt_public_key_file: Path | None = None
    jwt_issuer: str | None = None
    jwt_audience: str | None = None
    jwt_algorithm: str = "RS256"
    jwt_required_scopes: CsvList = Field(default_factory=list)
    allowed_hosts: CsvList = Field(default_factory=list)
    allowed_origins: CsvList = Field(default_factory=list)
    allow_unauthenticated_remote: bool = False
    telemetry: Literal["off", "otlp", "console"] = "off"

    @field_validator(
        "toolsets", "jwt_required_scopes", "allowed_hosts", "allowed_origins", mode="before"
    )
    @classmethod
    def _split_csv(cls, value: object) -> object:
        """Accept ``ECON_TOOLSETS=core,wages`` as well as a JSON list."""
        if isinstance(value, str) and not value.strip().startswith("["):
            return [part.strip() for part in value.split(",") if part.strip()]
        return value

    @field_validator("log_level", mode="before")
    @classmethod
    def _upper(cls, value: object) -> object:
        """Allow lower-case level names such as ``info``."""
        return value.upper() if isinstance(value, str) else value

    @field_validator("toolsets")
    @classmethod
    def _core_first(cls, value: list[Toolset]) -> list[Toolset]:
        """Always mount core: wages/regional take area_ids from core's find_area."""
        if value and "core" not in value:
            return ["core", *value]
        return value

    @model_validator(mode="after")
    def _check_jwt(self) -> Settings:
        """Refuse an auth configuration that would accept tokens it should not."""
        if self.auth_mode != "jwt":
            return self
        sources = [self.jwt_jwks_uri, self.jwt_public_key, self.jwt_public_key_file]
        if sum(source is not None for source in sources) != 1:
            raise ValueError(
                "ECON_AUTH_MODE=jwt needs exactly one of ECON_JWT_JWKS_URI, "
                "ECON_JWT_PUBLIC_KEY or ECON_JWT_PUBLIC_KEY_FILE."
            )
        # The MCP spec forbids accepting tokens issued for another service ("token
        # passthrough"), so the audience and issuer are mandatory, not optional hardening.
        if not self.jwt_issuer or not self.jwt_audience:
            raise ValueError("ECON_AUTH_MODE=jwt needs ECON_JWT_ISSUER and ECON_JWT_AUDIENCE.")
        if self.jwt_jwks_uri:
            parts = urlsplit(self.jwt_jwks_uri)
            local = parts.hostname in LOOPBACK_HOSTS
            if parts.scheme != "https" and not (parts.scheme == "http" and local):
                raise ValueError("ECON_JWT_JWKS_URI must use https (http only for localhost).")
        return self

    def jwt_key(self) -> str | None:
        """The configured public key (or HMAC secret), read from the file if one is given."""
        if self.jwt_public_key_file is not None:
            return self.jwt_public_key_file.read_text(encoding="utf-8")
        return self.jwt_public_key

    def http_safety_error(self) -> str | None:
        """Why serving HTTP with these settings would be unsafe, or ``None`` if it is fine.

        An unauthenticated MCP endpoint on a reachable address lets anyone spend the server's
        BLS quota and FRED key (docs/RESEARCH.md section 7).
        """
        if self.host in LOOPBACK_HOSTS or self.auth_mode != "none":
            return None
        if self.allow_unauthenticated_remote:
            return None
        return (
            f"Refusing to serve HTTP on {self.host} without authentication. Set "
            "ECON_AUTH_MODE=jwt (see docs/DEPLOYMENT.md), bind to 127.0.0.1, or set "
            "ECON_ALLOW_UNAUTHENTICATED_REMOTE=true if a proxy in front authenticates requests."
        )

    @property
    def has_bls_key(self) -> bool:
        """Whether ``BLS_API_KEY`` is set (raises BLS limits from 25 to 500 queries/day)."""
        return bool(os.getenv("BLS_API_KEY", "").strip())

    @property
    def has_fred_key(self) -> bool:
        """Whether ``FRED_API_KEY`` is set (required for any FRED data or search)."""
        return bool(os.getenv("FRED_API_KEY", "").strip())


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings (cached; tests construct ``Settings`` directly)."""
    return Settings()
