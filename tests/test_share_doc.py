import json
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
            "WOS_COMMUNICATIONS_SOCKET",
            "WOS_AGENT_ID",
        ):
            clean_env.pop(name, None)
        # Legacy routing tests run without a lane: no client, no socket.
        clean_env["WORLDOS_SHARE_DOC_CLIENT"] = "/nonexistent/share_doc.py"
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


FAKE_CLIENT = """#!/usr/bin/env python3
import json, os, sys
record = {"argv": sys.argv[1:], "stdin": "" if sys.stdin.isatty() else sys.stdin.read()}
with open(os.environ["FAKE_RECORD"], "a", encoding="utf-8") as handle:
    handle.write(json.dumps(record) + "\\n")
mode = os.environ.get("FAKE_MODE", "ok")
if mode == "ok":
    print(json.dumps({"ok": True, "result": {"publication_id": "publication:1"}}))
elif mode == "refused":
    print(json.dumps({"ok": False, "code": "conversation_selection_required"}))
    sys.exit(1)
elif mode == "unavailable":
    sys.stderr.write("    raise CommunicationsTransportError(uncertain=write_started) from error\\n"
                     "worldos_communications_mcp.CommunicationsTransportError: WorldOS communications are unavailable\\n")
    sys.exit(1)
else:
    sys.stderr.write("    raise CommunicationsTransportError(uncertain=write_started) from error\\n"
                     "worldos_communications_mcp.CommunicationsTransportError: WorldOS communications response is uncertain\\n")
    sys.exit(1)
"""


class ShareDocWorldOSTests(unittest.TestCase):
    run_tool = ShareDocRoutingTests.run_tool

    def setUp(self):
        import socket
        import tempfile

        self.directory = tempfile.TemporaryDirectory()
        root = Path(self.directory.name)
        self.client = root / "share_doc.py"
        self.client.write_text(FAKE_CLIENT)
        self.record = root / "record.jsonl"
        self.socket_path = root / "communications.sock"
        self.listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.listener.bind(str(self.socket_path))

    def tearDown(self):
        self.listener.close()
        self.directory.cleanup()

    def lane(self, mode="ok", **extra):
        return {
            "WORLDOS_SHARE_DOC_CLIENT": str(self.client),
            "WOS_COMMUNICATIONS_SOCKET": str(self.socket_path),
            "WOS_AGENT_ID": "team--composer",
            "FAKE_RECORD": str(self.record),
            "FAKE_MODE": mode,
            **extra,
        }

    def calls(self):
        if not self.record.exists():
            return []
        return [json.loads(line) for line in self.record.read_text().splitlines()]

    def test_explicit_chat_maps_to_a_worldos_conversation(self):
        result = self.run_tool(
            "--chat-id", "-100123", "--thread-id", "77", "--no-audio",
            env=self.lane(), input_text="Weekly report\nBody line.\n",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["result"]["publication_id"], "publication:1")
        [call] = self.calls()
        argv = call["argv"]
        self.assertEqual(argv[argv.index("--conversation-id") + 1], "chat:-100123/thread:77")
        self.assertIn("--no-audio", argv)
        self.assertTrue(argv[argv.index("--idempotency-key") + 1].startswith("share-doc:"))
        self.assertEqual(call["stdin"], "# Weekly report\nBody line.\n")
        self.assertNotIn("legacy", result.stderr)

    def test_current_without_turn_metadata_uses_the_lane_default(self):
        result = self.run_tool("--to", "current", env=self.lane(), input_text="# Doc\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        [call] = self.calls()
        self.assertNotIn("--conversation-id", call["argv"])
        self.assertNotIn("--no-audio", call["argv"])

    def test_no_destination_in_a_lane_uses_the_lane_default(self):
        result = self.run_tool("--no-audio", env=self.lane(), input_text="# Doc\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("--conversation-id", self.calls()[0]["argv"])

    def test_dry_run_prints_the_worldos_destination_without_sending(self):
        result = self.run_tool(
            "--to", "current", "--dry-run",
            env=self.lane(WORLDOS_CURRENT_CHAT_ID="1676859445"),
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(result.stdout.startswith(
            "destination: worldos lane=team--composer conversation=chat:1676859445/thread:root"
        ))
        self.assertEqual(self.calls(), [])

    def test_md_path_line_is_passed_as_a_file(self):
        document = Path(self.directory.name) / "doc.md"
        document.write_text("# Doc\n")
        result = self.run_tool(
            "--chat-id", "123", env=self.lane(), input_text=f"{document}\n",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.calls()[0]["argv"][0], str(document))

    def test_broker_refusal_falls_back_to_legacy_with_one_line(self):
        result = self.run_tool(
            "--chat-id", "123", "--no-audio", env=self.lane("refused"),
            input_text="# Doc\n",
        )
        fallback = [line for line in result.stderr.splitlines() if "using legacy sender" in line]
        self.assertEqual(len(fallback), 1)
        self.assertIn("conversation_selection_required", fallback[0])
        # Legacy then applies its own routing rules (no bot env here).
        self.assertEqual(result.returncode, 3)
        self.assertIn("also requires --bot-env", result.stderr)

    def test_uncertain_response_is_never_resent_by_legacy(self):
        result = self.run_tool(
            "--chat-id", "123", env=self.lane("uncertain"), input_text="# Doc\n",
        )
        self.assertEqual(result.returncode, 75)
        self.assertNotIn("legacy", result.stderr)
        self.assertEqual(len(self.calls()), 1)

    def test_refused_connect_falls_back_despite_traceback_wording(self):
        result = self.run_tool(
            "--chat-id", "123", env=self.lane("unavailable"), input_text="# Doc\n",
        )
        self.assertIn("using legacy sender", result.stderr)
        self.assertEqual(result.returncode, 3)

    def test_native_client_arguments_pass_through(self):
        result = self.run_tool(
            "--conversation-id", "chat:1/thread:root", "--no-audio", env=self.lane(),
            input_text="# Doc\n",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.calls()[0]["argv"][:2], ["--conversation-id", "chat:1/thread:root"])


if __name__ == "__main__":
    unittest.main()
