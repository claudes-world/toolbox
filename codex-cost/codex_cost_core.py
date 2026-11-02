"""Local transcript discovery, parsing, disposable cache, and progress display."""

import hashlib
import json
import multiprocessing
import os
import signal
import sqlite3
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from cost_progress import Progress


# Bump when record normalization changes; price changes need no cache migration.
SCHEMA_VERSION = 2
PARALLEL_MIN_BYTES = 16 * 1024 * 1024
PROGRESS_BYTES = 4 * 1024 * 1024
_worker_progress = None



def signature(stat):
    return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns


def discover(root, cutoff, progress):
    """Return all discovered paths, including old files for cache deletion checks."""
    paths, selected = set(), []
    root_stat = os.stat(root)
    directories = [(root, frozenset({(root_stat.st_dev, root_stat.st_ino)}))]
    complete = True
    cutoff_ns = int(cutoff.timestamp() * 1e9) if cutoff is not None else None
    while directories:
        directory, ancestors = directories.pop()
        try:
            with os.scandir(directory) as entries:
                for entry in entries:
                    # Match glob's hidden-file and symlink behavior, excluding cycles.
                    if entry.name.startswith("."):
                        continue
                    try:
                        is_directory = entry.is_dir()
                    except OSError as exc:
                        complete = False
                        progress.warning(f"could not inspect {entry.path}: {exc}")
                        continue
                    if is_directory:
                        try:
                            stat = entry.stat()
                        except OSError as exc:
                            complete = False
                            progress.warning(f"could not stat {entry.path}: {exc}")
                            continue
                        identity = stat.st_dev, stat.st_ino
                        if identity not in ancestors:
                            directories.append((entry.path, ancestors | {identity}))
                    elif entry.name.endswith(".jsonl"):
                        paths.add(entry.path)
                        progress.advance(found=1)
                        try:
                            sig = signature(entry.stat())
                        except OSError as exc:
                            progress.warning(f"could not stat {entry.path}: {exc}; trying to read")
                            sig = None
                        if sig is not None and cutoff_ns is not None and sig[3] < cutoff_ns:
                            progress.advance(skipped=1)
                        else:
                            selected.append((entry.path, sig))
        except OSError as exc:
            complete = False
            progress.warning(f"could not scan {directory}: {exc}")
    return sorted(selected), paths, complete


def parse_timestamp(value):
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.astimezone(timezone.utc) if parsed.tzinfo is not None else None
    except ValueError:
        return None


def _init_worker(messages):
    global _worker_progress
    _worker_progress = messages
    # Let the parent stop the pool and finish its progress line on Ctrl-C.
    signal.signal(signal.SIGINT, signal.SIG_IGN)


def read_file(path, progress_bytes=None):
    """Read all usage in a file, independent of the report's window and filter."""
    rows = []
    model, project = "", "unknown"
    requested = {}
    last_cumulative = None
    pending = 0
    stable = None
    error = None
    callback = progress_bytes or (_worker_progress.put if _worker_progress is not None else None)
    try:
        with open(path, "rb") as handle:
            before = signature(os.fstat(handle.fileno()))
            for line in handle:
                pending += len(line)
                if callback and pending >= PROGRESS_BYTES:
                    callback(pending)
                    pending = 0
                if not (b'"session_meta"' in line or b'"turn_context"' in line
                        or b'"token_count"' in line or b'\\u' in line):
                    continue
                try:
                    obj = json.loads(line.decode("utf-8", errors="ignore"))
                except (TypeError, ValueError):
                    continue
                if not isinstance(obj, dict):
                    continue
                payload = obj.get("payload")
                if not isinstance(payload, dict):
                    continue
                kind = obj.get("type")
                if kind in ("session_meta", "turn_context"):
                    if kind == "session_meta":
                        last_cumulative = None
                    requested = {k: payload.get(k) for k in ("service_tier", "inference_geo")}
                    if isinstance(payload.get("cwd"), str) and payload["cwd"]:
                        project = payload["cwd"]
                    if kind == "turn_context" and isinstance(payload.get("model"), str) and payload["model"]:
                        model = payload["model"].lower()
                    continue
                if kind != "event_msg" or payload.get("type") != "token_count" or not model:
                    continue
                info = payload.get("info")
                usage = info.get("last_token_usage") if isinstance(info, dict) else None
                if not isinstance(usage, dict) or not usage:
                    continue
                details = usage.get("input_tokens_details") or {}
                if not isinstance(details, dict):
                    details = {}
                cached = usage.get("cached_input_tokens", details.get("cached_tokens"))
                writes = usage.get("cache_write_input_tokens", details.get("cache_write_tokens"))
                counts = [usage.get("input_tokens"), cached, usage.get("output_tokens")]
                invalid = None
                if any(type(value) is not int or value < 0 for value in counts):
                    invalid = "invalid or missing input/read/output counter"
                    counts = [0, 0, 0]
                elif writes is not None and (type(writes) is not int or writes < 0):
                    invalid = "invalid cache-write counter"
                elif cached + (writes or 0) > counts[0]:
                    invalid = "cache reads plus writes exceed total input"
                cumulative = info.get("total_token_usage")
                if invalid is None and isinstance(cumulative, dict) and cumulative and all(
                        type(v) is int and v >= 0 for v in cumulative.values()):
                    if cumulative == last_cumulative:
                        continue
                    last_cumulative = cumulative
                reasoning = usage.get("reasoning_output_tokens")
                if type(reasoning) is not int or not 0 <= reasoning <= counts[2]:
                    reasoning = None
                metadata = dict(requested)
                for source in (payload, info, usage):
                    for field in ("service_tier", "inference_geo"):
                        if source.get(field) is not None:
                            metadata[field] = source[field]
                timestamp = parse_timestamp(obj.get("timestamp"))
                rows.append((timestamp.timestamp() if timestamp else None,
                             model, project, *counts, reasoning,
                             writes if type(writes) is int else None,
                             str(metadata["service_tier"]) if metadata.get("service_tier") is not None else None,
                             str(metadata["inference_geo"]) if metadata.get("inference_geo") is not None else None,
                             invalid))
            after = signature(os.fstat(handle.fileno()))
        if before == after == signature(os.stat(path)):
            stable = before
    except OSError as exc:
        error = f"could not read {path}: {exc}"
    finally:
        if callback and pending:
            callback(pending)
    return rows, stable, error


class Cache:
    def __init__(self, root):
        directory = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "codex-cost"
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        name = hashlib.sha256(os.fsencode(root)).hexdigest() + ".sqlite3"
        self.path = directory / name
        self.conn = sqlite3.connect(self.path, timeout=0.1)
        try:
            self.conn.execute("PRAGMA foreign_keys=ON")
            version = self.conn.execute("PRAGMA user_version").fetchone()[0]
            if version != SCHEMA_VERSION:
                with self.conn:
                    self.conn.execute("BEGIN IMMEDIATE")
                    # Another invocation may have initialized it while we waited.
                    if self.conn.execute("PRAGMA user_version").fetchone()[0] != SCHEMA_VERSION:
                        self.conn.execute("DROP TABLE IF EXISTS events")
                        self.conn.execute("DROP TABLE IF EXISTS files")
                        self.conn.execute("""CREATE TABLE files (
                                path TEXT PRIMARY KEY, dev INTEGER, inode INTEGER,
                                size INTEGER, mtime_ns INTEGER, ctime_ns INTEGER
                            )""")
                        self.conn.execute("""CREATE TABLE events (
                                path TEXT REFERENCES files(path) ON DELETE CASCADE,
                                ordinal INTEGER, timestamp REAL, model TEXT, project TEXT,
                                input_tokens INTEGER, cached_tokens INTEGER, output_tokens INTEGER,
                                reasoning_tokens INTEGER, write_tokens INTEGER, service_tier TEXT,
                                inference_geo TEXT, invalid TEXT, PRIMARY KEY(path, ordinal)
                            )""")
                        self.conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
            self.files = {row[0]: tuple(row[1:]) for row in self.conn.execute("SELECT * FROM files")}
        except BaseException:
            self.conn.close()
            raise

    def prune(self, paths):
        missing = self.files.keys() - paths
        if missing:
            with self.conn:
                self.conn.executemany("DELETE FROM files WHERE path=?", ((path,) for path in missing))

    def get(self, path, expected):
        # Check metadata and read events from one snapshot if another invocation writes.
        with self.conn:
            self.conn.execute("BEGIN")
            current = self.conn.execute(
                "SELECT dev,inode,size,mtime_ns,ctime_ns FROM files WHERE path=?", (path,)).fetchone()
            if current != expected:
                return None
            return list(self.conn.execute(
                "SELECT timestamp,model,project,input_tokens,cached_tokens,output_tokens,reasoning_tokens,write_tokens,service_tier,inference_geo,invalid "
                "FROM events WHERE path=? ORDER BY ordinal", (path,)))

    def put(self, path, sig, rows):
        with self.conn:
            self.conn.execute("DELETE FROM files WHERE path=?", (path,))
            self.conn.execute("INSERT INTO files VALUES(?,?,?,?,?,?)", (path, *sig))
            self.conn.executemany("INSERT INTO events VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                                  ((path, ordinal, *row) for ordinal, row in enumerate(rows)))

    def close(self):
        self.conn.close()


def file_records(root, cutoff, progress, *, workers=None, use_cache=True, full_scan=False):
    root = os.path.realpath(root)
    selected, paths, complete = discover(root, None if full_scan else cutoff, progress)
    progress.update(stage="Checking cache")
    cache = None
    pool = messages = None

    def disable_cache(exc):
        nonlocal cache
        progress.warning(f"cache unavailable ({exc}); parsing transcripts instead")
        if cache:
            cache.close()
            cache = None

    try:
        if use_cache:
            try:
                cache = Cache(root)
                if complete:
                    cache.prune(paths)
            except (OSError, sqlite3.Error) as exc:
                disable_cache(exc)
        misses = []
        for path, sig in selected:
            if cache is None or sig is None or cache.files.get(path) != sig:
                misses.append((path, sig))
            progress.advance(checked=1)
        total_bytes = sum(sig[2] for _, sig in misses if sig)
        missed_paths = {path for path, _ in misses}
        worker_count = workers if workers is not None else min(4, os.cpu_count() or 1)
        parallel = worker_count > 1 and len(misses) >= 2 and total_bytes >= PARALLEL_MIN_BYTES
        progress.update(stage="Parsing" if misses else "Loading cache",
                        total_files=len(misses), total_bytes=total_bytes,
                        parse_started=time.monotonic())
        futures = {}
        if parallel:
            context = multiprocessing.get_context("spawn")
            messages = context.Queue() if progress.enabled else None
            progress.update(messages=messages)
            pool = ProcessPoolExecutor(max_workers=min(worker_count, len(misses)), mp_context=context,
                                       initializer=_init_worker, initargs=(messages,))

            def completed(future):
                if not future.cancelled() and future.exception() is None:
                    progress.advance(parsed=1)

            for path, _ in misses:
                future = pool.submit(read_file, path)
                future.add_done_callback(completed)
                futures[path] = future
        for path, sig in selected:
            if cache is not None and sig is not None and cache.files.get(path) == sig:
                try:
                    rows = cache.get(path, sig)
                except sqlite3.Error as exc:
                    disable_cache(exc)
                else:
                    if rows is not None:
                        progress.advance(cached=1)
                        yield rows
                        continue
            if path in futures:
                rows, stable, error = futures.pop(path).result()
            else:
                if path not in missed_paths:
                    progress.advance(total_files=1, total_bytes=sig[2] if sig else 0)
                rows, stable, error = read_file(path, lambda size: progress.advance(done_bytes=size))
                progress.advance(parsed=1)
            if error:
                progress.warning(error)
            if cache is not None and stable is not None:
                try:
                    cache.put(path, stable, rows)
                except (OverflowError, sqlite3.Error) as exc:
                    disable_cache(exc)
            yield rows
        progress.update(stage="Preparing report")
    except BaseException:
        if pool:
            # Python 3.13 has no public terminate_workers() (added in 3.14).
            for process in list((getattr(pool, "_processes", None) or {}).values()):
                process.terminate()
        raise
    finally:
        if pool:
            pool.shutdown(wait=True, cancel_futures=True)
        if messages is not None:
            # The renderer drains until the workers have exited; detach before closing.
            with progress.lock:
                progress._drain()
                progress.messages = None
                messages.close()
                messages.join_thread()
        if cache:
            cache.close()
