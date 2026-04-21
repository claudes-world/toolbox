"""Central metric instrument registry for pulse.

Create all instruments once via ``init_instruments()`` after ``otel.setup()``.
All public accessors are no-op-safe — if called before init, they return a
sentinel that silently discards writes.
"""
from __future__ import annotations

from opentelemetry.metrics import Counter, Histogram

from pulse import otel as _otel


def _meter():
    return _otel.get_meter("pulse")


# ---------------------------------------------------------------------------
# Module-level instrument handles (None until init_instruments() is called)
# ---------------------------------------------------------------------------

_run_duration: Histogram | None = None
_repos_succeeded: Counter | None = None
_repos_failed: Counter | None = None
_capture_errors: Counter | None = None

# Mutable backing stores for ObservableGauge callbacks
_rate_limit_used: list[int] = [0]
_dependabot_alerts: dict[str, int] = {}


# ---------------------------------------------------------------------------
# No-op sentinels — dropped silently when instruments aren't yet initialised
# ---------------------------------------------------------------------------

class _NoopHistogram:
    def record(self, amount: float, attributes: dict | None = None) -> None:  # noqa: ARG002
        pass


class _NoopCounter:
    def add(self, amount: int, attributes: dict | None = None) -> None:  # noqa: ARG002
        pass


_NOOP_HISTOGRAM = _NoopHistogram()
_NOOP_COUNTER = _NoopCounter()


# ---------------------------------------------------------------------------
# Initialisation
# ---------------------------------------------------------------------------

def init_instruments() -> None:
    """Create all metric instruments. Call once after otel.setup().

    Idempotent — safe to call multiple times.
    """
    global _run_duration, _repos_succeeded, _repos_failed, _capture_errors

    if _run_duration is not None:
        return  # already initialised

    m = _meter()

    _run_duration = m.create_histogram(
        "pulse_run_duration_seconds",
        description="Wall-clock duration of a full snapshot run",
        unit="s",
    )
    _repos_succeeded = m.create_counter(
        "pulse_repos_succeeded_total",
        description="Repos captured successfully",
    )
    _repos_failed = m.create_counter(
        "pulse_repos_failed_total",
        description="Repos that failed capture",
    )
    _capture_errors = m.create_counter(
        "pulse_capture_errors_total",
        description="Field-level capture errors",
    )

    def _rate_limit_callback(options):  # noqa: ARG001
        return [(_rate_limit_used[0], {})]

    m.create_observable_gauge(
        "pulse_rate_limit_used_points",
        callbacks=[_rate_limit_callback],
        description="GitHub GraphQL rate limit points used in this run",
    )

    def _dependabot_callback(options):  # noqa: ARG001
        return [(count, {"severity": sev}) for sev, count in _dependabot_alerts.items()]

    m.create_observable_gauge(
        "pulse_dependabot_alerts_total",
        callbacks=[_dependabot_callback],
        description="Open Dependabot alerts by severity",
    )


# ---------------------------------------------------------------------------
# Public accessors
# ---------------------------------------------------------------------------

def get_run_duration() -> Histogram | _NoopHistogram:
    """Return the run-duration histogram, or a no-op if not yet initialised."""
    return _run_duration if _run_duration is not None else _NOOP_HISTOGRAM


def get_repos_succeeded() -> Counter | _NoopCounter:
    """Return the repos-succeeded counter, or a no-op if not yet initialised."""
    return _repos_succeeded if _repos_succeeded is not None else _NOOP_COUNTER


def get_repos_failed() -> Counter | _NoopCounter:
    """Return the repos-failed counter, or a no-op if not yet initialised."""
    return _repos_failed if _repos_failed is not None else _NOOP_COUNTER


def get_capture_errors() -> Counter | _NoopCounter:
    """Return the capture-errors counter, or a no-op if not yet initialised."""
    return _capture_errors if _capture_errors is not None else _NOOP_COUNTER


def set_rate_limit_used(n: int) -> None:
    """Update the cumulative rate-limit-used gauge backing value."""
    _rate_limit_used[0] = n


def get_dependabot_alerts_gauge() -> None:
    """No-op accessor kept for symmetry — gauge is registered in init_instruments()."""
    return None


def set_dependabot_alerts(counts: dict[str, int]) -> None:
    """Update the backing dict read by the dependabot ObservableGauge callback."""
    _dependabot_alerts.clear()
    _dependabot_alerts.update(counts)
