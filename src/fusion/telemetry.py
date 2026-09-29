"""OpenTelemetry setup shared by every service (API, MCP server, consumers, agent).

Traces and metrics go over OTLP/HTTP to grafana/otel-lgtm (Grafana on :3000).
If the OpenTelemetry packages are missing, or OTEL_ENABLED=false, every
call here becomes a no-op, so the code runs unchanged in tests and CI.
"""

from __future__ import annotations

import contextlib
from typing import Any

from fusion.config import settings

try:
    from opentelemetry import metrics, trace
    from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    _OTEL = True
except ImportError:  # pragma: no cover - exercised only where OTel isn't installed
    _OTEL = False

_configured = False


def setup(service_name: str) -> bool:
    """Configure exporters once per process. Returns True if telemetry is live."""
    global _configured
    if _configured:
        return _OTEL and settings.otel_enabled
    _configured = True
    if not (_OTEL and settings.otel_enabled):
        return False
    resource = Resource.create({"service.name": service_name, "service.namespace": "fraud-fusion"})
    tracer_provider = TracerProvider(resource=resource)
    tracer_provider.add_span_processor(
        BatchSpanProcessor(OTLPSpanExporter(endpoint=f"{settings.otel_endpoint}/v1/traces")))
    trace.set_tracer_provider(tracer_provider)
    reader = PeriodicExportingMetricReader(
        OTLPMetricExporter(endpoint=f"{settings.otel_endpoint}/v1/metrics"), export_interval_millis=5000)
    metrics.set_meter_provider(MeterProvider(resource=resource, metric_readers=[reader]))
    return True


class _NoSpan:
    def set_attribute(self, *a: Any) -> None:
        ...

    def record_exception(self, *a: Any) -> None:
        ...


@contextlib.contextmanager
def span(name: str, **attributes: Any):
    """`with span("resolve", source="kyc") as s:` - a trace span, or nothing."""
    if not _OTEL:
        yield _NoSpan()
        return
    with trace.get_tracer("fusion").start_as_current_span(name) as s:
        for k, v in attributes.items():
            s.set_attribute(k, v)
        yield s


class _NoCounter:
    def add(self, *a: Any, **k: Any) -> None:
        ...


def counter(name: str, description: str = ""):
    """A monotonic counter metric, e.g. events processed per source and outcome."""
    if not _OTEL:
        return _NoCounter()
    return metrics.get_meter("fusion").create_counter(name, description=description)


def instrument_fastapi(app) -> None:
    if _OTEL and settings.otel_enabled:
        try:
            from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
            FastAPIInstrumentor.instrument_app(app)
        except ImportError:
            pass


def instrument_httpx() -> None:
    if _OTEL and settings.otel_enabled:
        try:
            from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
            HTTPXClientInstrumentor().instrument()
        except ImportError:
            pass


def flush() -> None:
    """Push buffered spans and metrics before a short-lived process exits."""
    if _OTEL and settings.otel_enabled:
        with contextlib.suppress(Exception):
            trace.get_tracer_provider().force_flush()
            metrics.get_meter_provider().force_flush()
