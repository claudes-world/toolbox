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

    PulseLock is the sole concurrency guard — do NOT wrap the service invocation
    with an external flock(1) command. If systemd ExecStart uses flock -n, the
    inherited fd and the new fd opened here are two independent open-file-descriptions;
    Linux flock(2) treats them independently and PulseLock will fail with LockHeld on
    every invocation (Linux flock man page: a lock on one fd may be denied by a lock
    the same process holds on another fd). Call `pulse --now` directly from ExecStart.
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
