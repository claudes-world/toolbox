import os
import subprocess
import unittest
from pathlib import Path


TOOL = Path(__file__).parents[1] / "hooks" / "share-doc"


class ShareDocRoutingTests(unittest.TestCase):
    def run_tool(self, *args, env=None, input_text=""):
        clean_env = os.environ.copy()
        for name in (
            "SHARE_DOC_BOT_ENV",
            "TELEGRAM_CHAT_ID",
            "TELEGRAM_THREAD_ID",
            "WORLDOS_CURRENT_BOT_ENV",
            "WORLDOS_CURRENT_CHAT_ID",
            "WORLDOS_CURRENT_THREAD_ID",
            "WORLDOS_CURRENT_ENVELOPE_PATH",
        ):
            clean_env.pop(name, None)
        clean_env.update(env or {})
        return subprocess.run(
            [str(TOOL), *args],
            input=input_text,
            text=True,
            capture_output=True,
            env=clean_env,
            check=False,
        )

    def test_no_destination_fails_closed(self):
        result = self.run_tool("--no-audio", input_text="# Must not generate\n")
        self.assertEqual(result.returncode, 3)
        self.assertIn("no default sending route", result.stderr)

    def test_legacy_telegram_environment_does_not_become_a_default(self):
        result = self.run_tool(
            "--no-audio",
            env={"TELEGRAM_CHAT_ID": "1676859445", "TELEGRAM_THREAD_ID": "7"},
            input_text="# Must not generate\n",
        )
        self.assertEqual(result.returncode, 3)
        self.assertIn("no destination", result.stderr)

    def test_explicit_chat_requires_explicit_bot_identity(self):
        result = self.run_tool("--chat-id", "123", "--dry-run")
        self.assertEqual(result.returncode, 3)
        self.assertIn("also requires --bot-env", result.stderr)

    def test_current_requires_authoritative_bot_and_chat(self):
        no_context = self.run_tool("--to", "current", "--dry-run")
        self.assertEqual(no_context.returncode, 3)
        self.assertIn("no authoritative WorldOS chat context", no_context.stderr)

        no_bot = self.run_tool(
            "--to",
            "current",
            "--dry-run",
            env={"WORLDOS_CURRENT_CHAT_ID": "123"},
        )
        self.assertEqual(no_bot.returncode, 3)
        self.assertIn("no authoritative WorldOS bot identity", no_bot.stderr)

    def test_current_dry_run_preserves_bot_chat_and_thread_tuple(self):
        result = self.run_tool(
            "--to",
            "current",
            "--dry-run",
            env={
                "WORLDOS_CURRENT_BOT_ENV": "/run/secrets/current-bot.env",
                "WORLDOS_CURRENT_CHAT_ID": "-100123",
                "WORLDOS_CURRENT_THREAD_ID": "77",
            },
        )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(
            result.stdout.strip(),
            "destination: bot-env=/run/secrets/current-bot.env chat=-100123 thread=77",
        )

    def test_current_rejects_route_overrides(self):
        env = {
            "WORLDOS_CURRENT_BOT_ENV": "/run/secrets/current-bot.env",
            "WORLDOS_CURRENT_CHAT_ID": "-100123",
        }
        mixed_chat = self.run_tool(
            "--to", "current", "--chat-id", "456", "--dry-run", env=env
        )
        self.assertEqual(mixed_chat.returncode, 2)
        self.assertIn("exactly one destination", mixed_chat.stderr)

        mixed_bot = self.run_tool(
            "--to",
            "current",
            "--bot-env",
            "/run/secrets/other.env",
            "--dry-run",
            env=env,
        )
        self.assertEqual(mixed_bot.returncode, 2)
        self.assertIn("cannot override authoritative", mixed_bot.stderr)


if __name__ == "__main__":
    unittest.main()
