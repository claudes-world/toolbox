"""OpenTelemetry SDK initialisation for pulse.

Call ``setup()`` once at program start; call ``shutdown()`` on exit.
All public functions are safe to call before ``setup()`` — they return
no-op SDK objects in that case.
"""
from __future__ import annotations

import logging
import os
import threading
from typing import Optional

from opentelemetry import metrics, trace
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import SERVICE_NAME, Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

_log = logging.getLogger(__name__)

_DEFAULT_TRACES_ENDPOINT = "http://localhost:4318/v1/traces"
_DEFAULT_METRICS_ENDPOINT = "http://localhost:4318/v1/metrics"

_tracer_provider: Optional[TracerProvider] = None
_meter_provider: Optional[MeterProvider] = None
_shutdown_called = False
_shutdown_lock = threading.Lock()


def setup(service_name: str = "pulse", endpoint: str | None = None) -> None:
    """Initialise TracerProvider and MeterProvider.

    No-op mode: set ``OTEL_EXPORTER_OTLP_TRACES_ENDPOINT=""`` to load the
    SDK without attaching any exporter (all trace/metric calls become safe
    no-ops).

    Unreachable collector: OTLP export errors occur asynchronously during
    batch export, not at creation time.  The SDK logs them at DEBUG and drops
    them.  One WARNING is emitted here so operators know the situation.
    """
    global _tracer_provider, _meter_provider, _shutdown_called

    with _shutdown_lock:
        _shutdown_called = False

    resource = Resource.create({SERVICE_NAME: service_name})

    # Determine no-op mode from env var (explicit "" → no-op)
    traces_env = os.environ.get("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", None)
    no_op = traces_env == ""

    if no_op:
        # SDK loaded, no exporters — all calls are safe no-ops
        tp = TracerProvider(resource=resource)
        trace.set_tracer_provider(tp)
        _tracer_provider = tp

        mp = MeterProvider(resource=resource)
        metrics.set_meter_provider(mp)
        _meter_provider = mp
        return

    # --- Traces ---
    traces_endpoint = endpoint or traces_env or _DEFAULT_TRACES_ENDPOINT
    metrics_env = os.environ.get("OTEL_EXPORTER_OTLP_METRICS_ENDPOINT", None)
    metrics_endpoint = metrics_env or _DEFAULT_METRICS_ENDPOINT

    _log.warning(
        "OTEL: if collector is unreachable at %s, telemetry will be silently "
        "dropped after first export attempt",
        traces_endpoint,
    )

    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter

    span_exporter = OTLPSpanExporter(endpoint=traces_endpoint, timeout=5)
    processor = BatchSpanProcessor(
        span_exporter,
        max_queue_size=512,
        export_timeout_millis=5000,
        schedule_delay_millis=1000,
    )
    tp = TracerProvider(resource=resource)
    tp.add_span_processor(processor)
    trace.set_tracer_provider(tp)
    _tracer_provider = tp

    # --- Metrics ---
    metric_exporter = OTLPMetricExporter(endpoint=metrics_endpoint, timeout=5)
    reader = PeriodicExportingMetricReader(
        metric_exporter,
        export_interval_millis=15000,
        export_timeout_millis=5000,
    )
    mp = MeterProvider(resource=resource, metric_readers=[reader])
    metrics.set_meter_provider(mp)
    _meter_provider = mp


def shutdown(timeout_ms: int = 2000) -> None:
    """Flush and shut down both providers.

    Idempotent — safe to call multiple times.  Never raises.
    """
    global _shutdown_called

    with _shutdown_lock:
        if _shutdown_called:
            return
        _shutdown_called = True

    tp = _tracer_provider
    mp = _meter_provider

    def _do_shutdown() -> None:
        if tp is not None:
            try:
                tp.shutdown()
            except Exception as exc:
                _log.warning("OTEL tracer provider shutdown error: %s", exc)
        if mp is not None:
            try:
                mp.shutdown()
            except Exception as exc:
                _log.warning("OTEL meter provider shutdown error: %s", exc)

    t = threading.Thread(target=_do_shutdown, daemon=True)
    t.start()
    t.join(timeout=timeout_ms / 1000.0)
    if t.is_alive():
        _log.warning("OTEL shutdown did not complete within %dms — continuing", timeout_ms)


def get_tracer(name: str) -> trace.Tracer:
    """Convenience wrapper — returns ``trace.get_tracer(name)``."""
    return trace.get_tracer(name)


def get_meter(name: str) -> metrics.Meter:
    """Convenience wrapper — returns ``metrics.get_meter(name)``."""
    return metrics.get_meter(name)
