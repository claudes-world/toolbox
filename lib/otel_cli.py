"""Minimal OTEL tracing for short-lived CLI tools.

Uses SimpleSpanProcessor for synchronous flush on exit — correct for CLI tools
that exit fast (unlike long-running services which use BatchSpanProcessor).

Usage:
    import sys, os
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'lib'))
    import otel_cli

    otel_cli.setup('tool-name')
    tracer = otel_cli.get_tracer(__name__)

    with tracer.start_as_current_span('tool-name.run') as span:
        try:
            # main logic
            pass
        except Exception as e:
            span.record_exception(e)
            span.set_status(trace.StatusCode.ERROR, str(e))
            raise
        finally:
            otel_cli.shutdown()

No-op mode: set OTEL_EXPORTER_OTLP_TRACES_ENDPOINT="" to load the SDK without
attaching any exporter — all trace calls become safe no-ops. Useful for tests.
"""
from __future__ import annotations

import os
import signal
import sys
from typing import Optional

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource, SERVICE_NAME
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor

_provider: Optional[TracerProvider] = None


def setup(service_name: str) -> None:
    """Initialise TracerProvider with SimpleSpanProcessor.

    SimpleSpanProcessor (not Batch) is correct for CLI tools — it flushes
    synchronously before process exit, ensuring no spans are dropped.

    No-op mode: set OTEL_EXPORTER_OTLP_TRACES_ENDPOINT="" — provider is
    created with no exporter attached; all span calls are safe no-ops.
    """
    global _provider
    if _provider is not None:
        return  # already initialized

    resource = Resource.create({
        SERVICE_NAME: service_name,
        "caller.agent": os.environ.get("AGENT_NAME", "cli-direct"),
        "deployment.environment": os.environ.get("NODE_ENV", "production"),
    })
    _provider = TracerProvider(resource=resource)

    endpoint = os.environ.get(
        "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT",
        "http://localhost:4318/v1/traces",
    )
    if endpoint:  # empty string = no-op mode
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        exporter = OTLPSpanExporter(endpoint=endpoint)
        _provider.add_span_processor(SimpleSpanProcessor(exporter))

    trace.set_tracer_provider(_provider)


def get_tracer(name: str) -> trace.Tracer:
    """Convenience wrapper — returns ``trace.get_tracer(name)``."""
    return trace.get_tracer(name)


def shutdown(timeout_ms: int = 2000) -> None:
    """Flush and shut down the provider. Idempotent — safe to call multiple times. Never raises."""
    global _provider
    if _provider is None:
        return
    try:
        _provider.shutdown()
    except Exception:
        pass


def _handle_signal(sig, frame) -> None:
    """SIGTERM/SIGINT handler — flush spans before exit.

    Lesson from Phase 3+4 reviews: without explicit signal handling, a SIGTERM
    to a CLI tool kills the process before BatchSpanProcessor flushes. With
    SimpleSpanProcessor this is less critical (sync flush), but explicit handling
    ensures clean shutdown on Ctrl-C and systemd stop signals.
    """
    shutdown()
    sys.exit(0)


signal.signal(signal.SIGTERM, _handle_signal)
signal.signal(signal.SIGINT, _handle_signal)
