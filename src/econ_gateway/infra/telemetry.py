"""OpenTelemetry tracing: opt-in exporter setup and a span helper for the gateway's own code.

FastMCP already emits spans for every MCP request through ``opentelemetry-api``, which does
nothing until an SDK is configured. ``configure_telemetry`` installs the SDK when
``ECON_TELEMETRY`` asks for it:

- ``otlp``: batch-export spans with the OTLP/HTTP exporter. Endpoint, headers and protocol come
  from the standard variables (``OTEL_EXPORTER_OTLP_ENDPOINT``, ``OTEL_EXPORTER_OTLP_HEADERS``...),
  so any collector (Jaeger, Grafana Tempo, Honeycomb, Datadog) works without code changes.
- ``console``: print spans to **stderr** for debugging. Never stdout: in stdio mode stdout is the
  MCP protocol stream, and one stray line breaks the connection (CLAUDE.md rule 6).

``span()`` adds the gateway's own spans (upstream fetches, guarded HTTP requests) under the
FastMCP request span. Attributes never include URLs: FRED requests carry the API key in the
query string.

The SDK and exporter are an optional extra: ``pip install -e ".[otel]"``.
"""

from __future__ import annotations

import sys
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Literal

from opentelemetry import trace
from opentelemetry.trace import Span

TelemetryMode = Literal["off", "otlp", "console"]
TRACER_NAME = "econ_gateway"
AttributeValue = str | bool | int | float


def configure_telemetry(mode: TelemetryMode, *, service_name: str, version: str) -> bool:
    """Install the OpenTelemetry SDK with the exporter for ``mode``.

    Args:
        mode: ``off`` leaves tracing as a no-op.
        service_name: ``service.name`` resource attribute.
        version: ``service.version`` resource attribute.

    Returns:
        True when an exporter was installed.

    Raises:
        RuntimeError: When ``mode`` needs the SDK and it is not installed.
    """
    if mode == "off":
        return False
    try:
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import (
            BatchSpanProcessor,
            ConsoleSpanExporter,
            SimpleSpanProcessor,
        )
    except ImportError as exc:
        raise RuntimeError(
            f'ECON_TELEMETRY={mode} needs the OpenTelemetry SDK: pip install -e ".[otel]"'
        ) from exc

    resource = Resource.create({"service.name": service_name, "service.version": version})
    provider = TracerProvider(resource=resource)
    if mode == "console":
        provider.add_span_processor(SimpleSpanProcessor(ConsoleSpanExporter(out=sys.stderr)))
    else:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    trace.set_tracer_provider(provider)
    return True


@contextmanager
def span(name: str, **attributes: AttributeValue | None) -> Iterator[Span]:
    """Start a span named ``name`` as a child of the current one.

    ``None`` attributes are dropped, so callers can pass optional values directly.
    """
    tracer = trace.get_tracer(TRACER_NAME)
    clean = {key: value for key, value in attributes.items() if value is not None}
    with tracer.start_as_current_span(name, attributes=clean) as current:
        yield current
