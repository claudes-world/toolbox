import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path


TOOL_DIR = Path(__file__).parents[1] / "claude-was-wrong"
TOOL = TOOL_DIR / "claude-was-wrong"
sys.path.insert(0, str(TOOL_DIR))

from cww_core import (  # noqa: E402
    classify,
    context_window_for,
    load_report,
    open_index,
    redact_snippet,
    refresh_index,
    wilson,
)
from cww_render import render_html  # noqa: E402


class ClaudeWasWrongTests(unittest.TestCase):
    @staticmethod
    def record(
        text,
        timestamp=None,
        *,
        kind="assistant",
        block_type="text",
        message_id=None,
        model="claude-sonnet-5",
        sidechain=False,
        context_tokens=500_000,
    ):
        timestamp = timestamp or datetime.now(timezone.utc).isoformat()
        return {
            "type": kind,
            "timestamp": timestamp,
            "sessionId": "test-session",
            "isSidechain": sidechain,
            "cwd": "/home/claude/code/example-project",
            "version": "2.1.200",
            "message": {
                "id": message_id or f"msg-{abs(hash((text, timestamp, sidechain)))}",
                "model": model,
                "role": kind,
                "content": [{"type": block_type, "text": text}],
                "usage": {
                    "input_tokens": 100,
                    "cache_read_input_tokens": context_tokens - 100,
                    "cache_creation_input_tokens": 0,
                    "output_tokens": 20,
                },
            },
        }

    def write_transcript(self, root, records, name="session.jsonl", append=False):
        transcript = Path(root) / "project" / name
        transcript.parent.mkdir(parents=True, exist_ok=True)
        with transcript.open("a" if append else "w", encoding="utf-8") as handle:
            for record in records:
                handle.write(json.dumps(record) + "\n")
        return transcript

    def run_tool(self, records, *extra):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            transcripts = root / "transcripts"
            state = root / "state"
            self.write_transcript(transcripts, records)
            completed = subprocess.run(
                [
                    str(TOOL),
                    "--days",
                    "2",
                    "--json",
                    "--transcripts-dir",
                    str(transcripts),
                    "--state-dir",
                    str(state),
                    *extra,
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            return json.loads(completed.stdout)

    def test_exact_variations_and_combined_phrase(self):
        texts = [
            "You're right.",
            "You’re right.",
            "You are right.",
            "You were right.",
            "You were right, and I was wrong.",
            "You're right and I was wrong.",
            "You are right and I was wrong.",
        ]
        result = self.run_tool([self.record(text) for text in texts])
        self.assertEqual(result["phrase_occurrences"]["you're right"], 3)
        self.assertEqual(result["phrase_occurrences"]["you are right"], 2)
        self.assertEqual(result["phrase_occurrences"]["you were right"], 2)
        self.assertEqual(result["right_and_i_was_wrong"], 3)
        self.assertEqual(result["admission_messages"], 7)

    def test_apologies_are_separate_and_standalone_sorry_is_excluded(self):
        records = [
            self.record("I'm sorry. You were right."),
            self.record("I am sorry about that."),
            self.record("Sorry about the delay."),
            self.record("That is a sorry-looking state."),
        ]
        result = self.run_tool(records)
        self.assertNotIn("sorry", result["phrase_occurrences"])
        self.assertEqual(result["phrase_occurrences"]["i'm sorry"], 1)
        self.assertEqual(result["phrase_occurrences"]["i am sorry"], 1)
        self.assertEqual(result["phrase_occurrences"]["sorry for/about/that"], 2)
        self.assertEqual(result["summary"]["apologies"], 3)
        self.assertEqual(result["summary"]["apologized_admissions"], 1)
        self.assertEqual(result["summary"]["admissions"], 1)

    def test_luna_patterns_and_fuzzy_tier(self):
        exact_text = (
            "My earlier diagnosis was wrong. I conflated the two values. "
            "I shouldn't have assumed that. Let me correct this."
        )
        matches, _ = classify(exact_text)
        phrases = {match.phrase for match in matches if match.tier == "exact"}
        self.assertIn("my … was wrong", phrases)
        self.assertIn("i conflated", phrases)
        self.assertIn("i shouldn't have", phrases)
        self.assertIn("let me correct that", phrases)

        matches, _ = classify("I had missed that important detail.")
        self.assertTrue(any(match.tier == "probable" for match in matches))

    def test_code_and_meta_mentions_do_not_count(self):
        text = (
            "The phrase `I was wrong` should be tracked. "
            "Example phrase: I was wrong."
            "\n```text\nI was wrong\n```"
        )
        matches, excluded = classify(text)
        self.assertFalse(any(match.phrase == "i was wrong" for match in matches))
        self.assertGreaterEqual(excluded, 1)

    def test_only_visible_assistant_text_is_indexed(self):
        now = datetime.now(timezone.utc).isoformat()
        records = [
            self.record("I was wrong.", now),
            self.record("I was wrong.", now, kind="user"),
            self.record("I was wrong.", now, block_type="thinking"),
            self.record("I was wrong.", now, model="<synthetic>"),
        ]
        result = self.run_tool(records)
        self.assertEqual(result["summary"]["responses"], 1)
        self.assertEqual(result["summary"]["admissions"], 1)

    def test_main_and_subagents_are_separate(self):
        result = self.run_tool(
            [
                self.record("I was wrong.", sidechain=False),
                self.record("No correction here.", sidechain=True),
            ]
        )
        populations = {item["population"]: item for item in result["populations"]}
        self.assertEqual(populations["main"]["admissions"], 1)
        self.assertEqual(populations["subagent"]["admissions"], 0)

    def test_context_math_and_window_override(self):
        result = self.run_tool([self.record("I was wrong.", context_tokens=500_000)])
        self.assertEqual(result["summary"]["context_coverage_pct"], 100.0)
        item = next(item for item in result["context"] if item["bucket"] == "50–75%")
        self.assertEqual(item["admissions"], 1)
        self.assertEqual(context_window_for("claude-sonnet-5"), 1_000_000)
        self.assertEqual(context_window_for("custom", {"custom": 123}), 123)

    def test_incremental_append_does_not_duplicate_existing_rows(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            transcripts = root / "transcripts"
            state = root / "state"
            transcript = self.write_transcript(transcripts, [self.record("I was wrong.")])
            conn = open_index(state / "index.sqlite3")
            refresh_index(conn, transcripts, progress=False)
            self.write_transcript(
                transcripts,
                [self.record("You were right.", message_id="second")],
                append=True,
            )
            refresh_index(conn, transcripts, progress=False)
            report = load_report(
                conn,
                since=datetime.now().date() - timedelta(days=1),
                until=datetime.now().date(),
            )
            conn.close()
            self.assertEqual(report["summary"]["responses"], 2)
            self.assertEqual(report["summary"]["admissions"], 2)
            self.assertTrue(transcript.exists())

    def test_resolved_transcript_alias_is_indexed_once(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            transcripts = root / "transcripts"
            state = root / "state"
            transcript = self.write_transcript(transcripts, [self.record("I was wrong.")])
            transcript.with_name("alias.jsonl").symlink_to(transcript)
            conn = open_index(state / "index.sqlite3")
            refresh_index(conn, transcripts, progress=False)
            report = load_report(
                conn,
                since=datetime.now().date() - timedelta(days=1),
                until=datetime.now().date(),
            )
            conn.close()
            self.assertEqual(report["summary"]["responses"], 1)
            self.assertEqual(report["summary"]["admissions"], 1)

    def test_duplicate_message_ids_are_deduplicated(self):
        now = datetime.now(timezone.utc).isoformat()
        result = self.run_tool(
            [
                self.record("I was wrong.", now, message_id="same"),
                self.record("I was wrong.", now, message_id="same"),
            ]
        )
        self.assertEqual(result["summary"]["responses"], 1)
        self.assertEqual(result["summary"]["admissions"], 1)

    def test_window_excludes_old_messages(self):
        old = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
        result = self.run_tool([self.record("I was wrong.", old)])
        self.assertEqual(result["summary"]["responses"], 0)

    def test_context_window_override_rebuckets_indexed_rows(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            transcripts, state = root / "transcripts", root / "state"
            self.write_transcript(
                transcripts,
                [self.record("I was wrong.", context_tokens=500_000)],
            )
            conn = open_index(state / "index.sqlite3")
            refresh_index(conn, transcripts, progress=False)
            report = load_report(
                conn,
                since=datetime.now().date() - timedelta(days=1),
                until=datetime.now().date(),
                context_overrides={"sonnet": 2_000_000},
            )
            conn.close()
            bucket = next(
                item for item in report["context"] if item["bucket"] == "25–50%"
            )
            self.assertEqual(bucket["admissions"], 1)

    def test_wilson_bounds_and_redaction(self):
        low, high = wilson(5, 100)
        self.assertLess(low, 0.05)
        self.assertGreater(high, 0.05)
        redacted = redact_snippet(
            "token=supersecretvalue /home/claude/private user@example.com"
        )
        self.assertNotIn("supersecretvalue", redacted)
        self.assertNotIn("/home/claude/private", redacted)
        self.assertNotIn("user@example.com", redacted)

    def test_html_is_self_contained_and_escapes_projects(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            transcripts, state = root / "transcripts", root / "state"
            record = self.record("I was wrong.")
            record["cwd"] = "/tmp/<unsafe>"
            self.write_transcript(transcripts, [record])
            conn = open_index(state / "index.sqlite3")
            refresh_index(conn, transcripts, progress=False)
            report = load_report(
                conn,
                since=datetime.now().date() - timedelta(days=1),
                until=datetime.now().date(),
                include_rows=True,
            )
            target = root / "report.html"
            render_html(report, target)
            conn.close()
            content = target.read_text()
            self.assertIn("<!doctype html>", content)
            self.assertNotIn("https://", content)
            self.assertNotIn("<script src=", content)
            self.assertIn("\\u003cunsafe\\u003e", content)

    def test_state_permissions(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db = Path(temp_dir) / "state" / "index.sqlite3"
            conn = open_index(db)
            conn.close()
            self.assertEqual(os.stat(db.parent).st_mode & 0o777, 0o700)
            self.assertEqual(os.stat(db).st_mode & 0o777, 0o600)


if __name__ == "__main__":
    unittest.main()
