"""Shared stderr progress renderer for the local cost tools."""

import queue
import shutil
import sys
import threading
import time


class Progress:
    """One parent-owned renderer; workers only send byte-count increments."""

    def __init__(self, enabled=True, stream=None):
        self.enabled = enabled
        self.stream = stream if stream is not None else sys.stderr
        self.tty = self.stream.isatty()
        self.started = time.monotonic()
        self.stage = "Discovering files"
        self.found = self.skipped = self.cached = self.parsed = self.checked = 0
        self.total_files = self.total_bytes = self.done_bytes = 0
        self.parse_started = None
        self.messages = None
        self.lock = threading.RLock()
        self.stop = threading.Event()
        self.thread = None
        self.line_open = False

    def __enter__(self):
        if self.enabled:
            self.render()
            self.thread = threading.Thread(target=self._run, daemon=True)
            self.thread.start()
        return self

    def update(self, **values):
        with self.lock:
            for key, value in values.items():
                setattr(self, key, value)

    def advance(self, **increments):
        with self.lock:
            for key, amount in increments.items():
                setattr(self, key, getattr(self, key) + amount)

    def warning(self, message):
        with self.lock:
            if self.line_open:
                self.stream.write("\n")
                self.line_open = False
            print(f"warning: {message}", file=self.stream, flush=True)

    def _drain(self):
        with self.lock:
            if self.messages is not None:
                while True:
                    try:
                        self.advance(done_bytes=self.messages.get_nowait())
                    except queue.Empty:
                        break

    def _run(self):
        last_render = time.monotonic()
        while not self.stop.wait(0.1):
            self._drain()
            if self.tty or time.monotonic() - last_render >= 5:
                self.render()
                last_render = time.monotonic()

    def render(self, final=None):
        if not self.enabled:
            return
        with self.lock:
            elapsed = time.monotonic() - self.started
            if final:
                text = (f"{final} in {elapsed:.1f}s | {self.found:,} files | "
                        f"{self.skipped:,} old | {self.cached:,} cached | {self.parsed:,} parsed")
            elif self.stage == "Parsing":
                done = min(self.done_bytes, self.total_bytes)
                fraction = done / max(self.total_bytes, 1)
                bar = "#" * int(fraction * 12)
                bar = bar.ljust(12, "-")
                duration = time.monotonic() - self.parse_started
                eta = f"{duration * (self.total_bytes - done) / done:.1f}s" if done and duration >= 0.5 else "--"
                text = (f"Parsing [{bar}] {fraction:4.0%} {self.parsed}/{self.total_files} files "
                        f"{done / 2**20:.0f}/{self.total_bytes / 2**20:.0f} MiB "
                        f"{elapsed:.1f}s ETA {eta}")
            else:
                spinner = "|/-\\"[int(elapsed * 10) % 4]
                counts = (f"{self.checked:,}/{self.found - self.skipped:,} checked, {self.cached:,} cached"
                          if self.stage == "Checking cache" else f"{self.found:,} files found")
                text = f"{self.stage} {spinner} {counts} | {elapsed:.1f}s"
            if self.tty:
                width = max(20, shutil.get_terminal_size((100, 24)).columns - 1)
                self.stream.write("\r" + text[:width].ljust(width) + ("\n" if final else ""))
                self.line_open = not bool(final)
                self.stream.flush()
            else:
                print(text, file=self.stream, flush=True)

    def __exit__(self, exc_type, exc, tb):
        self.stop.set()
        if self.thread:
            self.thread.join()
        self._drain()
        self.render(final="Done" if exc_type is None else "Interrupted" if exc_type is KeyboardInterrupt else "Failed")

