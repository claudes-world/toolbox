"""Index and classify visible Claude Code assistant responses."""

from __future__ import annotations

import difflib
import fcntl
import hashlib
import json
import math
import os
import re
import sqlite3
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable
from zoneinfo import ZoneInfo


ET = ZoneInfo("America/New_York")
SCHEMA_VERSION = 2
CLASSIFIER_VERSION = 5
FUZZY_THRESHOLD = 0.82

GROUP_LABELS = {
    "direct": "Direct error admission",
    "vindication": "You were right",
    "correction": "Correction or retraction",
    "reasoning": "Missed or flawed reasoning",
    "apology": "Explicit apology",
}
GROUP_PRIORITY = ("direct", "correction", "reasoning", "vindication", "apology")

HEADLINE_PHRASES = (
    "you were right and i was wrong",
    "you're right and i was wrong",
    "you are right and i was wrong",
)


@dataclass(frozen=True)
class Pattern:
    phrase: str
    group: str
    regex: str
    admission: bool = True


@dataclass(frozen=True)
class Match:
    phrase: str
    group: str
    tier: str
    score: float
    occurrences: int = 1


def _p(phrase: str, group: str, regex: str | None = None, admission: bool = True) -> Pattern:
    if regex is None:
        escaped = re.escape(phrase).replace(r"\ ", r"\s+")
        regex = rf"(?<!\w){escaped}(?!\w)"
    return Pattern(phrase, group, regex, admission)


EXACT_PATTERNS = (
    _p(
        HEADLINE_PHRASES[0],
        "direct",
        r"(?<!\w)you\s+were\s+right\s*,?\s+and\s+i\s+was\s+wrong(?!\w)",
    ),
    _p(
        HEADLINE_PHRASES[1],
        "direct",
        r"(?<!\w)you're\s+right\s*,?\s+and\s+i\s+was\s+wrong(?!\w)",
    ),
    _p(HEADLINE_PHRASES[2], "direct"),
    _p("i was wrong", "direct"),
    _p("my mistake", "direct"),
    _p("my error", "direct"),
    _p("i was mistaken", "direct"),
    _p("i was incorrect", "direct"),
    _p("i made a mistake", "direct"),
    _p("i made an error", "direct"),
    _p("i got that wrong", "direct"),
    _p("i got it wrong", "direct"),
    _p("where i went wrong", "direct"),
    _p("i owe you a correction", "direct"),
    _p(
        "my … was wrong",
        "direct",
        r"(?<!\w)my(?:\s+\w+){1,8}\s+was\s+wrong(?!\w)",
    ),
    _p(
        "i can see my error",
        "direct",
        r"(?<!\w)i\s+can\s+see\s+(?:my|the)(?:\s+\w+){0,3}\s+error(?!\w)",
    ),
    _p("i stand corrected", "correction"),
    _p("i need to correct myself", "correction"),
    _p(
        "let me correct that",
        "correction",
        r"(?<!\w)let\s+me\s+correct\s+(?:that|this|myself)(?!\w)",
    ),
    _p("i take that back", "correction"),
    _p("i retract that", "correction"),
    _p("that was incorrect", "correction"),
    _p(
        "that's on me",
        "correction",
        r"(?<!\w)(?:that|this)(?:'s|\s+is)\s+on\s+me(?!\w)",
    ),
    _p(
        "that one's on me",
        "correction",
        r"(?<!\w)(?:that|this)\s+one(?:'s|\s+is)\s+on\s+me(?!\w)",
    ),
    _p("i misread", "reasoning"),
    _p("i misunderstood", "reasoning"),
    _p("i overlooked", "reasoning"),
    _p("i missed that", "reasoning"),
    _p("i over-read", "reasoning", r"(?<!\w)i\s+over[-\s]read(?!\w)"),
    _p("i mistakenly", "reasoning"),
    _p("i conflated", "reasoning"),
    _p(
        "i incorrectly assumed",
        "reasoning",
        r"(?<!\w)i\s+(?:wrongly|incorrectly)\s+assumed(?!\w)",
    ),
    _p("i should have", "reasoning"),
    _p(
        "i shouldn't have",
        "reasoning",
        r"(?<!\w)i\s+(?:shouldn(?:'t|\s+not)|should\s+not)\s+have(?!\w)",
    ),
    _p("you were right", "vindication"),
    _p("you're right", "vindication"),
    _p("you are right", "vindication"),
    _p("you're absolutely right", "vindication"),
    _p("you are absolutely right", "vindication"),
    _p("you were absolutely right", "vindication"),
    _p("you're correct", "vindication"),
    _p("you are correct", "vindication"),
    _p("you were correct", "vindication"),
    _p("good catch", "vindication"),
    _p("thanks for catching that", "vindication"),
    _p(
        "you're right to flag that",
        "vindication",
        r"(?<!\w)you(?:'re|\s+are)\s+right\s+to\s+(?:flag|call\s+out|challenge)\s+that(?!\w)",
    ),
    _p("i'm sorry", "apology", admission=False),
    _p("i am sorry", "apology", admission=False),
    _p("i apologize", "apology", admission=False),
    _p(
        "sorry for/about/that",
        "apology",
        r"(?<!\w)sorry\s+(?:for|about|that\b)(?!\w)",
        admission=False,
    ),
)

COMPILED_PATTERNS = tuple(
    (pattern, re.compile(pattern.regex, re.IGNORECASE)) for pattern in EXACT_PATTERNS
)

FUZZY_TEMPLATES = {
    "reasoning": (
        ("i missed something important", "i missed"),
        ("i had missed that detail", "i had missed"),
        ("my earlier analysis missed the issue", "my … missed"),
        ("i checked the wrong thing", "i checked … wrong"),
        ("i chose the wrong approach", "i chose … wrong"),
        ("i assumed incorrectly", "i assumed"),
        ("i jumped to the wrong conclusion", "i jumped …"),
    ),
    "correction": (
        ("i need to own that mistake", "i need to own"),
        ("correction to my previous answer", "correction"),
    ),
}

PROBABLE_PATTERNS = (
    ("i missed", "reasoning", r"(?<!\w)i\s+missed(?!\s+that\b)(?!\w)"),
    ("i had missed", "reasoning", r"(?<!\w)i(?:'d|\s+had)\s+missed(?!\w)"),
    ("my … missed", "reasoning", r"(?<!\w)my(?:\s+\w+){1,8}\s+missed(?!\w)"),
    (
        "i used/checked/chose … wrong",
        "reasoning",
        r"(?<!\w)i\s+(?:used|checked|watched|chose)(?:\s+\w+){0,8}\s+wrong(?!\w)",
    ),
    ("i need to own", "correction", r"(?<!\w)i\s+need\s+to\s+own(?!\w)"),
    ("correction", "correction", r"(?im)^\s*correction\s*:"),
    ("i assumed", "reasoning", r"(?<!\w)i\s+assumed(?!\w)"),
    (
        "i jumped …",
        "reasoning",
        r"(?<!\w)i\s+jumped(?:\s+\w+){0,8}(?:conclusion|wrong|too\s+quickly)(?!\w)",
    ),
)
COMPILED_PROBABLE = tuple(
    ((phrase, group), re.compile(regex, re.IGNORECASE))
    for phrase, group, regex in PROBABLE_PATTERNS
)

ERROR_ANCHORS = {
    "wrong", "mistake", "error", "incorrect", "missed", "assumed", "conclusion",
    "correct", "correction", "own", "overlooked", "misread", "misunderstood",
}
META_WORDS = {
    "phrase", "phrases", "counter", "count", "track", "tracking", "match",
    "matching", "example", "test", "query", "search", "regex", "literal",
}

CONTEXT_WINDOWS = (
    ("claude-fable-5", 1_000_000),
    ("claude-sonnet-5", 1_000_000),
    ("claude-opus-4-8", 1_000_000),
    ("claude-opus-4-7", 1_000_000),
    ("claude-opus-4-6", 1_000_000),
    ("claude-sonnet", 200_000),
    ("claude-haiku", 200_000),
    ("claude-opus", 200_000),
)


def normalize(text: str) -> str:
    return (
        text.translate(str.maketrans({"’": "'", "‘": "'", "\u00a0": " ", "“": '"', "”": '"'}))
        .lower()
    )


def visible_text(record: object) -> str:
    if not isinstance(record, dict) or record.get("type") != "assistant":
        return ""
    message = record.get("message")
    if not isinstance(message, dict) or message.get("role") not in (None, "assistant"):
        return ""
    content = message.get("content")
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    return "\n".join(
        block.get("text", "")
        for block in content
        if isinstance(block, dict)
        and block.get("type") == "text"
        and isinstance(block.get("text"), str)
    )


def strip_non_prose(text: str) -> str:
    text = re.sub(r"```.*?```", " ", text, flags=re.DOTALL)
    text = re.sub(r"`[^`\n]+`", " ", text)
    text = re.sub(r"(?m)^\s*>\s?.*$", " ", text)
    return text


def clauses(text: str) -> list[str]:
    cleaned = strip_non_prose(normalize(text))
    return [
        part.strip()
        for part in re.split(r"(?<=[.!?])\s+|[\n;]+", cleaned)
        if 4 <= len(part.strip()) <= 320
    ]


def _tokens(text: str) -> set[str]:
    return set(re.findall(r"[a-z]+(?:'[a-z]+)?", text))


def _similarity(candidate: str, template: str) -> float:
    left, right = _tokens(candidate), _tokens(template)
    if not left or not right:
        return 0.0
    sequence = difflib.SequenceMatcher(None, candidate, template).ratio()
    token_f1 = 2 * len(left & right) / (len(left) + len(right))
    return 0.65 * sequence + 0.35 * token_f1


def classify(text: str) -> tuple[list[Match], int]:
    prose = strip_non_prose(normalize(text))
    found: list[Match] = []
    meta_excluded = 0
    exact_spans: list[tuple[int, int]] = []
    for pattern, compiled in COMPILED_PATTERNS:
        hits = list(compiled.finditer(prose))
        if not hits:
            continue
        accepted = 0
        for hit in hits:
            surrounding = prose[max(0, hit.start() - 90): min(len(prose), hit.end() + 90)]
            if _tokens(surrounding) & META_WORDS:
                meta_excluded += 1
                continue
            accepted += 1
            exact_spans.append(hit.span())
        if accepted:
            found.append(Match(pattern.phrase, pattern.group, "exact", 1.0, accepted))

    # Audited broad patterns and similarity matches always remain probable.
    exact_phrases = {match.phrase for match in found}
    for (phrase, group), compiled in COMPILED_PROBABLE:
        hits = list(compiled.finditer(prose))
        if not hits:
            continue
        accepted = 0
        for hit in hits:
            surrounding = prose[max(0, hit.start() - 90): min(len(prose), hit.end() + 90)]
            if _tokens(surrounding) & META_WORDS:
                meta_excluded += 1
            else:
                accepted += 1
        if accepted and phrase not in exact_phrases:
            found.append(Match(phrase, group, "probable", 0.9, accepted))

    for clause in clauses(prose):
        words = _tokens(clause)
        if not words & ERROR_ANCHORS or not ({"i", "my"} & words):
            continue
        if words & META_WORDS:
            meta_excluded += 1
            continue
        best: tuple[float, str, str] | None = None
        for group, templates in FUZZY_TEMPLATES.items():
            for template, label in templates:
                score = _similarity(clause, template)
                if best is None or score > best[0]:
                    best = (score, group, label)
        if best and best[0] >= FUZZY_THRESHOLD and best[2] not in exact_phrases:
            found.append(Match(best[2], best[1], "probable", round(best[0], 3), 1))
    return found, meta_excluded


def parse_timestamp(raw: object) -> datetime | None:
    if not isinstance(raw, str) or not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def context_window_for(model: str, overrides: dict[str, int] | None = None) -> int | None:
    lowered = model.lower()
    for key, value in (overrides or {}).items():
        if key.lower() in lowered:
            return value
    if "[1m]" in lowered:
        return 1_000_000
    for key, size in CONTEXT_WINDOWS:
        if key in lowered:
            return size
    return None


def context_tokens(usage: dict) -> int | None:
    keys = ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")
    if not any(key in usage for key in keys):
        return None
    return sum(int(usage.get(key, 0) or 0) for key in keys)


def project_label(record: dict, path: Path) -> str:
    cwd = record.get("cwd")
    if isinstance(cwd, str) and cwd:
        return Path(cwd).name or cwd
    try:
        return path.relative_to(path.parents[1]).parts[0]
    except (ValueError, IndexError):
        return path.parent.name


SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS source_files (
    id INTEGER PRIMARY KEY,
    path TEXT NOT NULL UNIQUE,
    inode INTEGER NOT NULL,
    size INTEGER NOT NULL,
    mtime_ns INTEGER NOT NULL,
    byte_offset INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS responses (
    id INTEGER PRIMARY KEY,
    source_id INTEGER NOT NULL REFERENCES source_files(id) ON DELETE CASCADE,
    byte_offset INTEGER NOT NULL,
    message_id TEXT,
    timestamp_utc TEXT,
    timestamp_et TEXT,
    date_et TEXT,
    hour_et INTEGER,
    weekday_et INTEGER,
    session_id TEXT,
    project TEXT,
    model TEXT,
    version TEXT,
    population TEXT NOT NULL,
    context_tokens INTEGER,
    context_window INTEGER,
    context_pct REAL,
    output_tokens INTEGER,
    primary_group TEXT,
    has_admission INTEGER NOT NULL,
    has_apology INTEGER NOT NULL,
    has_probable INTEGER NOT NULL,
    combined INTEGER NOT NULL,
    meta_excluded INTEGER NOT NULL,
    text_hash TEXT NOT NULL,
    UNIQUE(source_id, byte_offset)
);
CREATE INDEX IF NOT EXISTS responses_date_idx ON responses(date_et);
CREATE INDEX IF NOT EXISTS responses_message_idx ON responses(message_id);
CREATE INDEX IF NOT EXISTS responses_model_idx ON responses(model);
CREATE TABLE IF NOT EXISTS matches (
    response_id INTEGER NOT NULL REFERENCES responses(id) ON DELETE CASCADE,
    phrase TEXT NOT NULL,
    group_name TEXT NOT NULL,
    tier TEXT NOT NULL,
    score REAL NOT NULL,
    occurrences INTEGER NOT NULL,
    PRIMARY KEY(response_id, phrase, tier)
);
"""


def open_index(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path.parent, 0o700)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=5000")
    conn.executescript(SCHEMA)
    conn.commit()
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return conn


def _meta(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return row[0] if row else None


def _set_meta(conn: sqlite3.Connection, key: str, value: object) -> None:
    conn.execute(
        "INSERT INTO meta(key,value) VALUES(?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, str(value)),
    )


def clear_index(conn: sqlite3.Connection) -> None:
    conn.execute("DELETE FROM source_files")
    conn.commit()


class IndexLock:
    def __init__(self, path: Path):
        self.path = path
        self.handle = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.handle = self.path.open("w")
        fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX)
        return self

    def __exit__(self, *_):
        if self.handle:
            fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
            self.handle.close()


def _insert_record(
    conn: sqlite3.Connection,
    source_id: int,
    offset: int,
    path: Path,
    record: dict,
    text: str,
    overrides: dict[str, int],
) -> None:
    message = record.get("message") or {}
    model = str(message.get("model") or record.get("model") or "unknown")
    if model.lower() in ("<synthetic>", "synthetic"):
        return
    parsed = parse_timestamp(record.get("timestamp"))
    if parsed is None:
        return
    local = parsed.astimezone(ET)
    usage = message.get("usage") or record.get("usage") or {}
    ctx_tokens = context_tokens(usage)
    ctx_window = context_window_for(model, overrides)
    ctx_pct = (
        round(100 * ctx_tokens / ctx_window, 4)
        if ctx_tokens is not None and ctx_window
        else None
    )
    found, meta_excluded = classify(text)
    exact_groups = {
        match.group for match in found if match.tier == "exact" and match.group != "apology"
    }
    apology = any(match.tier == "exact" and match.group == "apology" for match in found)
    probable = any(match.tier == "probable" for match in found)
    primary = next((group for group in GROUP_PRIORITY if group in exact_groups), None)
    if primary is None and apology:
        primary = "apology"
    combined = any(match.phrase in HEADLINE_PHRASES and match.tier == "exact" for match in found)
    cursor = conn.execute(
        """
        INSERT INTO responses(
          source_id, byte_offset, message_id, timestamp_utc, timestamp_et, date_et,
          hour_et, weekday_et, session_id, project, model, version, population,
          context_tokens, context_window, context_pct, output_tokens, primary_group,
          has_admission, has_apology, has_probable, combined, meta_excluded, text_hash
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            source_id,
            offset,
            message.get("id") or record.get("uuid"),
            parsed.astimezone(timezone.utc).isoformat(),
            local.isoformat(),
            local.date().isoformat(),
            local.hour,
            local.weekday(),
            record.get("sessionId") or record.get("session_id") or path.stem,
            project_label(record, path),
            model,
            str(record.get("version") or ""),
            "subagent" if record.get("isSidechain") or "/subagents/" in str(path) else "main",
            ctx_tokens,
            ctx_window,
            ctx_pct,
            int(usage.get("output_tokens", 0) or 0),
            primary,
            int(bool(exact_groups)),
            int(apology),
            int(probable),
            int(combined),
            meta_excluded,
            hashlib.sha256(text.encode("utf-8", "replace")).hexdigest(),
        ),
    )
    response_id = cursor.lastrowid
    for match in found:
        conn.execute(
            "INSERT INTO matches(response_id,phrase,group_name,tier,score,occurrences) "
            "VALUES(?,?,?,?,?,?)",
            (
                response_id,
                match.phrase,
                match.group,
                match.tier,
                match.score,
                match.occurrences,
            ),
        )


def refresh_index(
    conn: sqlite3.Connection,
    transcript_root: Path,
    *,
    rebuild: bool = False,
    overrides: dict[str, int] | None = None,
    progress: bool = True,
) -> dict[str, int]:
    root = transcript_root.resolve()
    overrides = overrides or {}
    stored_root = _meta(conn, "transcript_root")
    version_mismatch = (
        _meta(conn, "schema_version") != str(SCHEMA_VERSION)
        or _meta(conn, "classifier_version") != str(CLASSIFIER_VERSION)
        or (stored_root is not None and stored_root != str(root))
    )
    if rebuild or version_mismatch:
        clear_index(conn)
    _set_meta(conn, "schema_version", SCHEMA_VERSION)
    _set_meta(conn, "classifier_version", CLASSIFIER_VERSION)
    _set_meta(conn, "transcript_root", root)

    resolved_paths: dict[str, Path] = {}
    for candidate in root.rglob("*.jsonl"):
        try:
            if candidate.is_file():
                resolved_paths.setdefault(str(candidate.resolve()), candidate)
        except OSError:
            continue
    paths = [resolved_paths[key] for key in sorted(resolved_paths)]
    existing = {
        row["path"]: row
        for row in conn.execute("SELECT * FROM source_files").fetchall()
    }
    current_paths = set(resolved_paths)
    removed = set(existing) - current_paths
    for path in removed:
        conn.execute("DELETE FROM source_files WHERE path=?", (path,))

    stats = Counter(files_seen=len(paths), files_removed=len(removed))
    first_build = not existing or rebuild or version_mismatch
    if first_build and progress:
        print(
            f"claude-was-wrong: indexing {len(paths):,} transcript files (first run/rebuild)…",
            file=sys.stderr,
        )

    for number, path in enumerate(paths, 1):
        try:
            resolved = str(path.resolve())
            stat = path.stat()
        except OSError:
            stats["file_errors"] += 1
            continue
        old = existing.get(resolved)
        reset = bool(
            old
            and (
                old["inode"] != stat.st_ino
                or stat.st_size < old["size"]
            )
        )
        if old and not reset and old["size"] == stat.st_size and old["mtime_ns"] == stat.st_mtime_ns:
            stats["files_unchanged"] += 1
            continue
        if old and reset:
            conn.execute("DELETE FROM responses WHERE source_id=?", (old["id"],))
            conn.execute(
                "UPDATE source_files SET inode=?,size=0,mtime_ns=?,byte_offset=0 WHERE id=?",
                (stat.st_ino, stat.st_mtime_ns, old["id"]),
            )
            source_id, offset = old["id"], 0
            stats["files_reset"] += 1
        elif old:
            source_id, offset = old["id"], old["byte_offset"]
        else:
            cursor = conn.execute(
                "INSERT INTO source_files(path,inode,size,mtime_ns,byte_offset) VALUES(?,?,?,?,0)",
                (resolved, stat.st_ino, 0, stat.st_mtime_ns),
            )
            source_id, offset = cursor.lastrowid, 0
            existing[resolved] = {
                "id": source_id,
                "path": resolved,
                "inode": stat.st_ino,
                "size": 0,
                "mtime_ns": stat.st_mtime_ns,
                "byte_offset": 0,
            }

        last_complete = offset
        try:
            with path.open("rb") as handle:
                handle.seek(offset)
                while True:
                    line_offset = handle.tell()
                    line = handle.readline()
                    if not line:
                        break
                    if not line.endswith(b"\n"):
                        break
                    last_complete = handle.tell()
                    if b'"assistant"' not in line and b'"type": "assistant"' not in line:
                        continue
                    try:
                        record = json.loads(line.decode("utf-8", "replace"))
                    except (json.JSONDecodeError, TypeError):
                        stats["malformed_lines"] += 1
                        continue
                    text = visible_text(record)
                    if not text:
                        continue
                    try:
                        _insert_record(
                            conn, source_id, line_offset, path, record, text, overrides
                        )
                        stats["responses_added"] += 1
                    except sqlite3.IntegrityError:
                        stats["duplicates_skipped"] += 1
            conn.execute(
                "UPDATE source_files SET inode=?,size=?,mtime_ns=?,byte_offset=? WHERE id=?",
                (stat.st_ino, stat.st_size, stat.st_mtime_ns, last_complete, source_id),
            )
            conn.commit()
            stats["files_updated"] += 1
        except OSError:
            conn.rollback()
            stats["file_errors"] += 1
        if first_build and progress and number % 500 == 0:
            print(f"  indexed {number:,}/{len(paths):,} files", file=sys.stderr)

    _set_meta(conn, "last_refresh_utc", datetime.now(timezone.utc).isoformat())
    conn.commit()
    return dict(stats)


def wilson(successes: int, total: int, z: float = 1.96) -> tuple[float, float]:
    if total <= 0:
        return (0.0, 0.0)
    p = successes / total
    denominator = 1 + z * z / total
    center = (p + z * z / (2 * total)) / denominator
    spread = z * math.sqrt((p * (1 - p) + z * z / (4 * total)) / total) / denominator
    return (max(0.0, center - spread), min(1.0, center + spread))


def _bucket_context(value: float | None) -> str | None:
    if value is None:
        return None
    if value < 10:
        return "0–10%"
    if value < 25:
        return "10–25%"
    if value < 50:
        return "25–50%"
    if value < 75:
        return "50–75%"
    if value < 90:
        return "75–90%"
    return "90%+"


def _rate(successes: int, total: int) -> float:
    return round(1000 * successes / total, 3) if total else 0.0


def _rollup(rows: Iterable[dict], key_fn) -> list[dict]:
    groups: dict[object, list[dict]] = defaultdict(list)
    for row in rows:
        groups[key_fn(row)].append(row)
    result = []
    for key, values in groups.items():
        exact = sum(row["has_admission"] for row in values)
        probable = sum(row["has_probable"] for row in values)
        apologies = sum(row["has_apology"] for row in values)
        low, high = wilson(exact, len(values))
        result.append(
            {
                "key": key,
                "responses": len(values),
                "admissions": exact,
                "admission_rate_per_1000": _rate(exact, len(values)),
                "probable": probable,
                "apologies": apologies,
                "ci_low_per_1000": round(low * 1000, 3),
                "ci_high_per_1000": round(high * 1000, 3),
            }
        )
    return result


def load_report(
    conn: sqlite3.Connection,
    *,
    since: date | None,
    until: date,
    population: str = "all",
    model_filter: str | None = None,
    project_filter: str | None = None,
    include_rows: bool = False,
    context_overrides: dict[str, int] | None = None,
) -> dict:
    raw_rows = [dict(row) for row in conn.execute(
        """
        SELECT r.*, s.path AS source_path
        FROM responses r JOIN source_files s ON s.id=r.source_id
        WHERE date_et IS NOT NULL AND date_et<=?
        """ + (" AND date_et>=?" if since else ""),
        ((until.isoformat(), since.isoformat()) if since else (until.isoformat(),)),
    )]
    if population != "all":
        raw_rows = [row for row in raw_rows if row["population"] == population]
    if model_filter:
        raw_rows = [row for row in raw_rows if model_filter.lower() in row["model"].lower()]
    if project_filter:
        raw_rows = [row for row in raw_rows if project_filter.lower() in row["project"].lower()]
    if context_overrides:
        for row in raw_rows:
            window = context_window_for(row["model"], context_overrides)
            row["context_window"] = window
            row["context_pct"] = (
                round(100 * row["context_tokens"] / window, 4)
                if row["context_tokens"] is not None and window
                else None
            )

    # Dedupe copied transcript records by message id, preferring richer usage.
    deduped: dict[str, dict] = {}
    for row in raw_rows:
        key = row["message_id"] or f'{row["source_id"]}:{row["byte_offset"]}'
        previous = deduped.get(key)
        if previous is None or (row["context_tokens"] or -1) > (previous["context_tokens"] or -1):
            deduped[key] = row
    rows = list(deduped.values())
    response_ids = [row["id"] for row in rows]
    matches: dict[int, list[dict]] = defaultdict(list)
    for start in range(0, len(response_ids), 800):
        batch = response_ids[start:start + 800]
        placeholders = ",".join("?" for _ in batch)
        for match in conn.execute(
            f"SELECT * FROM matches WHERE response_id IN ({placeholders})", batch
        ):
            matches[match["response_id"]].append(dict(match))

    summary = {
        "responses": len(rows),
        "admissions": sum(row["has_admission"] for row in rows),
        "probable": sum(row["has_probable"] for row in rows),
        "apologies": sum(row["has_apology"] for row in rows),
        "apologized_admissions": sum(
            row["has_admission"] and row["has_apology"] for row in rows
        ),
        "right_and_i_was_wrong": sum(row["combined"] for row in rows),
        "context_known": sum(row["context_pct"] is not None for row in rows),
        "meta_mentions_excluded": sum(row["meta_excluded"] for row in rows),
    }
    summary["admission_rate_per_1000"] = _rate(summary["admissions"], summary["responses"])
    summary["apology_rate_per_1000"] = _rate(summary["apologies"], summary["responses"])
    summary["apology_coverage_pct"] = round(
        100 * summary["apologized_admissions"] / summary["admissions"], 1
    ) if summary["admissions"] else 0.0
    summary["context_coverage_pct"] = round(
        100 * summary["context_known"] / summary["responses"], 1
    ) if summary["responses"] else 0.0

    daily = _rollup(rows, lambda row: (row["date_et"], row["population"]))
    daily.sort(key=lambda item: item["key"])
    # Fill rolling averages independently for each population.
    history: dict[str, list[float]] = defaultdict(list)
    for item in daily:
        population_key = item["key"][1]
        history[population_key].append(item["admission_rate_per_1000"])
        values = history[population_key][-7:]
        item["rolling_7d_rate"] = round(sum(values) / len(values), 3)
        item["date"], item["population"] = item.pop("key")

    populations = _rollup(rows, lambda row: row["population"])
    models = _rollup(rows, lambda row: row["model"])
    projects = _rollup(rows, lambda row: row["project"])
    for collection, label in ((populations, "population"), (models, "model"), (projects, "project")):
        for item in collection:
            item[label] = item.pop("key")
        collection.sort(key=lambda item: (-item["admission_rate_per_1000"], -item["responses"]))

    context_rows = [row for row in rows if _bucket_context(row["context_pct"]) is not None]
    context = _rollup(
        context_rows,
        lambda row: (_bucket_context(row["context_pct"]), row["population"]),
    )
    order = {"0–10%": 0, "10–25%": 1, "25–50%": 2, "50–75%": 3, "75–90%": 4, "90%+": 5}
    for item in context:
        item["bucket"], item["population"] = item.pop("key")
    context.sort(key=lambda item: (item["population"], order[item["bucket"]]))

    hourly = _rollup(rows, lambda row: (row["hour_et"], row["population"]))
    for item in hourly:
        item["hour"], item["population"] = item.pop("key")
    hourly.sort(key=lambda item: (item["population"], item["hour"]))

    weekday_hour = _rollup(rows, lambda row: (row["weekday_et"], row["hour_et"]))
    for item in weekday_hour:
        item["weekday"], item["hour"] = item.pop("key")
    weekday_hour.sort(key=lambda item: (item["weekday"], item["hour"]))

    phrase_counts: Counter[tuple[str, str, str]] = Counter()
    group_responses: Counter[tuple[str, str]] = Counter()
    for row in rows:
        groups_seen = set()
        for match in matches[row["id"]]:
            phrase_counts[(match["phrase"], match["group_name"], match["tier"])] += match["occurrences"]
            groups_seen.add((match["group_name"], match["tier"]))
        for group in groups_seen:
            group_responses[group] += 1
    phrases = [
        {
            "phrase": phrase,
            "group": group,
            "tier": tier,
            "occurrences": count,
        }
        for (phrase, group, tier), count in phrase_counts.most_common()
    ]
    categories = [
        {
            "group": group,
            "label": GROUP_LABELS[group],
            "tier": tier,
            "responses": count,
            "rate_per_1000": _rate(count, len(rows)),
        }
        for (group, tier), count in sorted(
            group_responses.items(), key=lambda item: (-item[1], item[0])
        )
    ]

    rough_days = [
        item for item in daily if item["responses"] >= 25
    ]
    roughest = max(rough_days, key=lambda item: item["admission_rate_per_1000"], default=None)
    cleanest = min(rough_days, key=lambda item: item["admission_rate_per_1000"], default=None)

    report = {
        "schema_version": 2,
        "classifier_version": CLASSIFIER_VERSION,
        "timezone": "America/New_York",
        "window": {
            "since": since.isoformat() if since else None,
            "until": until.isoformat(),
        },
        "filters": {
            "population": population,
            "model": model_filter,
            "project": project_filter,
        },
        "summary": summary,
        "daily": daily,
        "categories": categories,
        "phrases": phrases,
        "populations": populations,
        "models": models,
        "projects": projects,
        "context": context,
        "hourly": hourly,
        "weekday_hour": weekday_hour,
        "roughest_day": roughest,
        "cleanest_day": cleanest,
        "methodology": {
            "unit": "deduplicated visible assistant text response",
            "admission_definition": "high-confidence exact admission, concession, correction, or reasoning pattern",
            "probable_definition": f"anchor-gated fuzzy candidate with score >= {FUZZY_THRESHOLD}",
            "rate_unit": "admitted responses per 1,000 eligible responses",
            "context_definition": "input + cache-read + cache-creation tokens divided by estimated model context window",
            "sparse_threshold": 20,
            "caveat": "Admissions are a proxy for acknowledged mistakes, not a measure of all mistakes or causality.",
        },
    }
    if include_rows:
        report["rows"] = [
            {
                "id": row["id"],
                "timestamp_et": row["timestamp_et"],
                "date": row["date_et"],
                "hour": row["hour_et"],
                "weekday": row["weekday_et"],
                "population": row["population"],
                "model": row["model"],
                "project": row["project"],
                "session_id": row["session_id"],
                "context_pct": row["context_pct"],
                "has_admission": bool(row["has_admission"]),
                "has_apology": bool(row["has_apology"]),
                "has_probable": bool(row["has_probable"]),
                "combined": bool(row["combined"]),
                "source_path": row["source_path"],
                "byte_offset": row["byte_offset"],
                "matches": matches[row["id"]],
            }
            for row in rows
        ]
    return report


SECRET_PATTERNS = (
    (re.compile(r"(?i)\b(?:sk|ghp|xox[baprs])[-_][A-Za-z0-9_-]{12,}\b"), "<secret>"),
    (re.compile(r"(?i)\b(?:api[_-]?key|token|password|secret)\s*[:=]\s*\S+"), "<credential>"),
    (re.compile(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b"), "<email>"),
    (re.compile(r"(?<!\w)/(?:home|Users|tmp|etc|var)/[^\s,;]+"), "<path>"),
    (re.compile(r"https?://[^\s?#]+[?][^\s]+"), "<url-with-query>"),
)


def redact_snippet(text: str) -> str:
    cleaned = re.sub(r"\s+", " ", text).strip()
    for pattern, replacement in SECRET_PATTERNS:
        cleaned = pattern.sub(replacement, cleaned)
    return cleaned[:240] + ("…" if len(cleaned) > 240 else "")


def snippet_for_row(row: dict) -> str | None:
    try:
        with Path(row["source_path"]).open("rb") as handle:
            handle.seek(int(row["byte_offset"]))
            record = json.loads(handle.readline().decode("utf-8", "replace"))
        text = visible_text(record)
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    candidates = clauses(text)
    for clause in candidates:
        found, _ = classify(clause)
        if found:
            return redact_snippet(clause)
    return redact_snippet(text) if text else None
