"""Tests for pulse.otel — OTEL SDK setup, no-op mode, shutdown, etc."""
from __future__ import annotations

import logging
import threading
import time
from unittest.mock import MagicMock, patch

import pytest

import pulse.otel as otel_mod


def _reset_otel_module() -> None:
    """Reset module-level state between tests."""
    otel_mod._tracer_provider = None
    otel_mod._meter_provider = None
    otel_mod._shutdown_called = False


# ---------------------------------------------------------------------------
# No-op mode
# ---------------------------------------------------------------------------


def test_noop_mode_no_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """setup() with OTEL_EXPORTER_OTLP_TRACES_ENDPOINT='' completes without error."""
    _reset_otel_module()
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", "")

    otel_mod.setup(service_name="test-noop")  # must not raise


def test_noop_mode_tracer_is_valid(monkeypatch: pytest.MonkeyPatch) -> None:
    """get_tracer() returns a usable tracer after no-op setup."""
    _reset_otel_module()
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", "")

    otel_mod.setup(service_name="test-noop")
    tracer = otel_mod.get_tracer("test")
    assert tracer is not None
    # Should be usable (no exporter means spans are just dropped)
    with tracer.start_as_current_span("test-span"):
        pass  # must not raise


def test_noop_mode_no_exporters_created(monkeypatch: pytest.MonkeyPatch) -> None:
    """No OTLP exporters are created in no-op mode."""
    _reset_otel_module()
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", "")

    otel_mod.setup(service_name="test-noop-exporters")

    # TracerProvider must be set but have zero active span processors
    tp = otel_mod._tracer_provider
    assert tp is not None, "TracerProvider should be set in no-op mode"
    processors = tp._active_span_processor._span_processors
    assert len(processors) == 0, f"Expected no span processors in no-op mode, got: {processors}"


# ---------------------------------------------------------------------------
# Normal mode (fake endpoint)
# ---------------------------------------------------------------------------


def test_normal_mode_setup_completes(monkeypatch: pytest.MonkeyPatch) -> None:
    """setup() with a fake endpoint completes without raising."""
    _reset_otel_module()
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", raising=False)
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_METRICS_ENDPOINT", raising=False)

    # Patch exporters so we don't need a real collector
    with (
        patch("opentelemetry.exporter.otlp.proto.http.trace_exporter.OTLPSpanExporter", return_value=MagicMock()),
        patch("opentelemetry.exporter.otlp.proto.http.metric_exporter.OTLPMetricExporter", return_value=MagicMock()),
        patch("pulse.otel.PeriodicExportingMetricReader", return_value=MagicMock()),
        patch("pulse.otel.BatchSpanProcessor", return_value=MagicMock()),
    ):
        otel_mod.setup(service_name="test-normal", endpoint="http://fake-host:4318/v1/traces")


def test_normal_mode_get_tracer_works(monkeypatch: pytest.MonkeyPatch) -> None:
    """get_tracer() returns a tracer after normal-mode setup."""
    _reset_otel_module()
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", raising=False)
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_METRICS_ENDPOINT", raising=False)

    with (
        patch("opentelemetry.exporter.otlp.proto.http.trace_exporter.OTLPSpanExporter", return_value=MagicMock()),
        patch("opentelemetry.exporter.otlp.proto.http.metric_exporter.OTLPMetricExporter", return_value=MagicMock()),
        patch("pulse.otel.PeriodicExportingMetricReader", return_value=MagicMock()),
        patch("pulse.otel.BatchSpanProcessor", return_value=MagicMock()),
    ):
        otel_mod.setup(service_name="test-tracer", endpoint="http://fake-host:4318/v1/traces")
        tracer = otel_mod.get_tracer("my-component")
        assert tracer is not None


# ---------------------------------------------------------------------------
# Silent drop — unreachable collector
# ---------------------------------------------------------------------------


def test_silent_drop_one_warning(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    """Unreachable endpoint → exactly 1 WARNING logged, no exception propagated."""
    _reset_otel_module()
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", raising=False)
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_METRICS_ENDPOINT", raising=False)

    with (
        patch("opentelemetry.exporter.otlp.proto.http.trace_exporter.OTLPSpanExporter", return_value=MagicMock()),
        patch("opentelemetry.exporter.otlp.proto.http.metric_exporter.OTLPMetricExporter", return_value=MagicMock()),
        patch("pulse.otel.PeriodicExportingMetricReader", return_value=MagicMock()),
        patch("pulse.otel.BatchSpanProcessor", return_value=MagicMock()),
        caplog.at_level(logging.WARNING, logger="pulse.otel"),
    ):
        otel_mod.setup(service_name="test-silent", endpoint="http://unreachable-host:4318/v1/traces")

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING and "pulse.otel" in r.name]
    assert len(warnings) == 1, f"Expected 1 warning, got {len(warnings)}: {[r.message for r in warnings]}"
    assert "unreachable" in warnings[0].message or "silently dropped" in warnings[0].message


# ---------------------------------------------------------------------------
# Shutdown idempotency
# ---------------------------------------------------------------------------


def test_shutdown_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    """shutdown() called twice raises no exception."""
    _reset_otel_module()
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", "")

    otel_mod.setup(service_name="test-idempotent")

    otel_mod.shutdown(timeout_ms=500)
    otel_mod.shutdown(timeout_ms=500)  # must not raise


def test_shutdown_before_setup_safe() -> None:
    """shutdown() before setup() is safe (no providers initialised)."""
    _reset_otel_module()
    otel_mod.shutdown(timeout_ms=200)  # must not raise


# ---------------------------------------------------------------------------
# Shutdown timeout enforcement
# ---------------------------------------------------------------------------


def test_shutdown_completes_within_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    """shutdown() returns within 2s even if providers hang."""
    _reset_otel_module()
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", "")

    otel_mod.setup(service_name="test-timeout")

    # Make the tracer provider shutdown block for a long time
    if otel_mod._tracer_provider is not None:
        original_shutdown = otel_mod._tracer_provider.shutdown

        def slow_shutdown() -> None:
            time.sleep(10)  # much longer than timeout

        otel_mod._tracer_provider.shutdown = slow_shutdown  # type: ignore[method-assign]

    start = time.monotonic()
    otel_mod.shutdown(timeout_ms=500)  # 500ms budget
    elapsed = time.monotonic() - start

    # Should return roughly within timeout + a small margin (e.g. 1s for thread overhead)
    assert elapsed < 2.0, f"shutdown took {elapsed:.2f}s — exceeded timeout"
