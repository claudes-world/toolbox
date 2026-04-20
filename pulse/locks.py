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

    Raises LockHeld immediately if the lock is already held.

    Safety note: when invoked via systemd ExecStart (flock -n /path pulse_binary),
    the pulse process inherits the flock fd. Calling PulseLock() then opens a NEW fd
    to the same path. Linux flock(2) allows the same process to re-acquire — the new
    LOCK_EX succeeds immediately. Concurrent manual `pulse --now` from a DIFFERENT
    process correctly gets LOCK_NB rejected → LockHeld. No deadlock risk.
    """

    def __init__(self, path: Path | None = None) -> None:
        self._path = path or _lock_path()
        self._fd: int | None = None

    def __enter__(self) -> "PulseLock":
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._fd = os.open(str(self._path), os.O_CREAT | os.O_WRONLY, 0o600)
        try:
            fcntl.flock(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as e:
            os.close(self._fd)
            self._fd = None
            raise LockHeld(f"pulse already running (lock held at {self._path})") from e
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
