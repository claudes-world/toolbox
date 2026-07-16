import json
import subprocess
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path


TOOL = Path(__file__).parents[1] / "claude-was-wrong" / "claude-was-wrong"


class ClaudeWasWrongTests(unittest.TestCase):
    def run_tool(self, records):
        with tempfile.TemporaryDirectory() as temp_dir:
            transcript = Path(temp_dir) / "project" / "session.jsonl"
            transcript.parent.mkdir()
            transcript.write_text(
                "".join(json.dumps(record) + "\n" for record in records),
                encoding="utf-8",
            )
            completed = subprocess.run(
                [str(TOOL), "--days", "2", "--json", "--transcripts-dir", temp_dir],
                check=True,
                capture_output=True,
                text=True,
            )
            return json.loads(completed.stdout)

    @staticmethod
    def record(kind, text, timestamp, block_type="text"):
        return {
            "type": kind,
            "timestamp": timestamp,
            "sessionId": "test-session",
            "message": {
                "role": kind,
                "content": [{"type": block_type, "text": text}],
            },
        }

    def test_counts_only_visible_assistant_text_and_deduplicates_daily_total(self):
        now = datetime.now(timezone.utc).isoformat()
        records = [
            self.record("assistant", "Sorry. You’re right. I was wrong. Good catch.", now),
            self.record("user", "I was wrong", now),
            self.record("assistant", "I was wrong", now, block_type="thinking"),
        ]

        result = self.run_tool(records)

        self.assertEqual(result["phrase_occurrences"]["you're right"], 1)
        self.assertEqual(result["phrase_occurrences"]["i was wrong"], 1)
        self.assertEqual(result["phrase_occurrences"]["good catch"], 1)
        self.assertEqual(result["phrase_occurrences"]["sorry"], 1)
        self.assertEqual(result["admission_messages"], 1)

    def test_sorry_requires_a_complete_word(self):
        now = datetime.now(timezone.utc).isoformat()
        records = [
            self.record("assistant", "I'm sorry about that.", now),
            self.record("assistant", "I am sorry about that too.", now),
            self.record("assistant", "That is a sorry-looking state.", now),
            self.record("assistant", "No match inside sorryish.", now),
        ]

        result = self.run_tool(records)

        self.assertEqual(result["phrase_occurrences"]["sorry"], 3)
        self.assertEqual(result["phrase_occurrences"]["i'm sorry"], 1)
        self.assertEqual(result["phrase_occurrences"]["i am sorry"], 1)
        self.assertEqual(result["admission_messages"], 3)

    def test_tracks_all_requested_you_were_right_variations(self):
        now = datetime.now(timezone.utc).isoformat()
        records = [
            self.record("assistant", "You're right.", now),
            self.record("assistant", "You’re right.", now),
            self.record("assistant", "You are right.", now),
            self.record("assistant", "You were right.", now),
        ]

        result = self.run_tool(records)

        self.assertEqual(result["phrase_occurrences"]["you're right"], 2)
        self.assertEqual(result["phrase_occurrences"]["you are right"], 1)
        self.assertEqual(result["phrase_occurrences"]["you were right"], 1)
        self.assertEqual(result["admission_messages"], 4)

    def test_ignores_messages_before_window(self):
        old = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
        result = self.run_tool([self.record("assistant", "I was wrong", old)])
        self.assertEqual(result["phrase_occurrences"]["i was wrong"], 0)
        self.assertEqual(result["admission_messages"], 0)


if __name__ == "__main__":
    unittest.main()
