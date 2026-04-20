from __future__ import annotations

import fcntl
import os
from pathlib import Path
from types import TracebackType


class LockHeld(Exception):
    """Raised when the pulse lock is already held by another process."""


def _lock_path() -> Path:
    uid = os.getuid()
    run_dir = Path(f"/run/user/{uid}")
    if run_dir.is_dir() and os.access(run_dir, os.W_OK):
        return run_dir / "pulse.lock"
    return Path.home() / ".world" / "pulse" / "pulse.lock"


class PulseLock:
    """Context manager that acquires an exclusive non-blocking flock.

    Raises LockHeld immediately (non-blocking) if the lock is already held by another process.
    Concurrent invocations fail-fast rather than queue — the caller is responsible for retry
    or error reporting. This is intentional: pulse runs are time-bounded and queueing would
    cause cascading delays.

    Two separate invocation paths share this lock file for mutual exclusion:

    - ``pulse --now`` (manual / ad-hoc): acquires PulseLock directly. No external
      flock(1) wrapper involved.
    - bare ``pulse`` (systemd service path via ExecStart): wrapped by external
      ``flock -n`` in the unit's ExecStart line. The service calls
      ``_run_now_no_lock()`` internally, deliberately skipping PulseLock so
      there is no double-lock within the same process.

    Both paths contend on the same lock file as separate processes, which is
    exactly what flock(2) is designed to handle — correct mutual exclusion is
    maintained. The two-path split exists so the service can use an external
    flock as its sole guard while the manual path uses PulseLock directly,
    avoiding any same-process re-entrancy issue.
    """

    def __init__(self, path: Path | None = None) -> None:
        self._path = path or _lock_path()
        self._fd: int | None = None

    def __enter__(self) -> "PulseLock":
        if self._fd is not None:
            raise RuntimeError("PulseLock is not re-entrant")
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._fd = os.open(str(self._path), os.O_CREAT | os.O_WRONLY, 0o600)
        try:
            fcntl.flock(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as e:
            os.close(self._fd)
            self._fd = None
            raise LockHeld(f"pulse already running (lock held at {self._path})") from e
        except Exception:
            os.close(self._fd)
            self._fd = None
            raise
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        if self._fd is not None:
            try:
                fcntl.flock(self._fd, fcntl.LOCK_UN)
            finally:
                os.close(self._fd)
                self._fd = None
