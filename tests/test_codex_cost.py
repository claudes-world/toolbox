import json
import io
import os
import runpy
import signal
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch


TOOL = Path(__file__).parents[1] / "codex-cost" / "codex-cost"
sys.path.insert(0, str(TOOL.parent))
import codex_cost_core as core

CLI = runpy.run_path(str(TOOL))
MISSING = object()


def context(model="gpt-6-astra", project="/example"):
    return {"type": "turn_context", "payload": {"model": model, "cwd": project}}


def usage(input_tokens=272_000, cached=200_000, output=10_000,
          reasoning=MISSING, timestamp="2026-09-10T00:00:00Z"):
    counts = {"input_tokens": input_tokens, "cached_input_tokens": cached, "cache_write_input_tokens": 0, "output_tokens": output}
    if reasoning is not MISSING:
        counts["reasoning_output_tokens"] = reasoning
    return {"type": "event_msg", "timestamp": timestamp, "payload": {
        "type": "token_count", "info": {"last_token_usage": counts},
    }}


class CodexCostTests(unittest.TestCase):
    def estimate(self, model, input_tokens, cached_tokens, output_tokens):
        records = [
            {"type": "turn_context", "payload": {"model": model, "cwd": "/example"}},
            {"type": "event_msg", "payload": {
                "type": "token_count", "info": {"last_token_usage": {
                    "input_tokens": input_tokens,
                    "cached_input_tokens": cached_tokens,
                    "cache_write_input_tokens": 0,
                    "output_tokens": output_tokens,
                }},
            }},
        ]
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / "session.jsonl").write_text(
                "\n".join(json.dumps(record) for record in records)
            )
            result = subprocess.run(
                [sys.executable, str(TOOL), "--all", "--json", "--by-project",
                 "--model", model, "--sessions-dir", directory, "--no-cache", "--no-progress"],
                capture_output=True, text=True, check=True,
            )
        return json.loads(result.stdout)

    def test_astra_standard_at_long_context_boundary(self):
        result = self.estimate("gpt-6-astra", 272_000, 200_000, 10_000)
        self.assertEqual(result["by_model"]["gpt-6-astra"]["calls"], 1)
        self.assertEqual(result["total_cost_usd"], 1.42)
        self.assertEqual(result["naive_cost_usd"], 3.22)
        self.assertEqual(result["by_project"]["/example"], 1.42)

    def test_astra_snapshot_long_context_includes_cached_input(self):
        result = self.estimate("gpt-6-astra-2026-09-10", 300_000, 200_000, 10_000)
        self.assertEqual(result["by_model"]["gpt-6-astra"]["cost_usd"], 3.15)
        self.assertEqual(result["naive_cost_usd"], 6.75)
        self.assertEqual(result["cache_saved_usd"], 3.60)

    def test_sol_uses_current_price_and_long_context(self):
        result = self.estimate("gpt-5.6-sol", 300_000, 200_000, 10_000)
        self.assertEqual(result["by_model"]["gpt-5.6-sol"]["cost_usd"], 1.26)


class CodexCostIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "sessions"
        self.root.mkdir()
        self.cache_home = Path(self.temp.name) / "cache"
        self.env = patch.dict(os.environ, {"XDG_CACHE_HOME": str(self.cache_home)})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.cutoff = datetime(2026, 9, 10, tzinfo=timezone.utc)

    def write(self, name="log.jsonl", records=None):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(json.dumps(r) for r in
                                   (records if records is not None else [context(), usage(reasoning=2000)])) + "\n")
        return path

    def parse(self, cutoff=None, model="", **kwargs):
        return CLI["parse"](str(self.root), cutoff, model, **kwargs)

    def cli(self, *args, check=True):
        return subprocess.run([sys.executable, str(TOOL), "--all", "--json",
                               "--sessions-dir", str(self.root), *args],
                              text=True, capture_output=True, check=check, timeout=30)

    def test_context_bands_and_output_costs_are_subdivisions(self):
        self.write(records=[context(), usage(reasoning=2000),
                            usage(input_tokens=300_000, reasoning=8000)])
        models, projects = self.parse(use_cache=False)
        row = CLI["model_json"](models["gpt-6-astra"])
        low, high = row["context_bands"].values()
        self.assertEqual(row["calls"], 2)
        self.assertEqual(row["cost_usd"], 4.57)
        self.assertEqual(projects["/example"]["cost"], models["gpt-6-astra"]["cost"])
        self.assertEqual((low["cost_usd"], high["cost_usd"]), (1.42, 3.15))
        self.assertEqual(low["total_tokens"], 282_000)
        self.assertEqual(high["total_tokens"], 310_000)
        self.assertEqual(low["token_pct"], 47.6)
        self.assertEqual(low["cost_pct"], 31.1)
        split = row["output_breakdown"]
        self.assertEqual(split["reasoning"], {
            "tokens": 10_000, "token_pct": 50.0, "cost_usd": 0.70, "cost_pct": 56.0,
        })
        self.assertEqual(split["non_reasoning"]["cost_usd"], 0.55)
        self.assertEqual(sum(b["tokens"] for b in split.values()), row["out"])
        self.assertEqual(sum(b["cost_pct"] for b in split.values()), 100)
        self.assertEqual(CLI["totals"](models)["cost"], models["gpt-6-astra"]["cost"])

    def test_reasoning_missing_invalid_zero_and_all(self):
        for reasoning in (MISSING, None, -1, 10_001, "10", True, 1.5, 0, 10_000):
            with self.subTest(reasoning=reasoning):
                self.write(records=[context(), usage(reasoning=reasoning)])
                models, _ = self.parse()
                split = CLI["model_json"](models["gpt-6-astra"])["output_breakdown"]
                valid = type(reasoning) is int and 0 <= reasoning <= 10_000
                self.assertEqual(split["unclassified"]["tokens"], 0 if valid else 10_000)
                self.assertEqual(split["reasoning"]["tokens"], reasoning if valid else 0)
                self.assertEqual(split["non_reasoning"]["tokens"], 10_000 - reasoning if valid else 0)

    def test_zero_output_and_empty_band_percentages(self):
        self.write(records=[context(), usage(output=0, reasoning=0)])
        models, _ = self.parse()
        row = CLI["model_json"](models["gpt-6-astra"])
        for bucket in row["output_breakdown"].values():
            self.assertEqual(bucket["token_pct"], 0)
            self.assertEqual(bucket["cost_pct"], 0)
        high = row["context_bands"]["gt_272k"]
        self.assertEqual((high["token_pct"], high["cost_pct"]), (0, 0))

    def test_old_files_skipped_but_full_scan_and_all_include_them(self):
        path = self.write()
        old = self.cutoff.timestamp() - 1
        os.utime(path, (old, old))
        with patch.object(core, "read_file", side_effect=AssertionError("old transcript opened")):
            self.assertFalse(self.parse(cutoff=self.cutoff)[0])
        self.assertEqual(self.parse(cutoff=self.cutoff, full_scan=True)[0]["gpt-6-astra"]["calls"], 1)
        self.assertEqual(self.parse()[0]["gpt-6-astra"]["calls"], 1)

    def test_cutoff_equality_old_directory_and_prior_context(self):
        path = self.write("2020/01/01/resumed.jsonl", [context(),
                          usage(timestamp="2026-09-09T23:59:59Z"),
                          usage(timestamp="2026-09-10T00:00:00Z"),
                          usage(timestamp="2026-09-10T00:00:00"), usage(timestamp=None)])
        timestamp = self.cutoff.timestamp()
        os.utime(path, (timestamp, timestamp))
        models, _ = self.parse(cutoff=self.cutoff)
        self.assertEqual(models["gpt-6-astra"]["calls"], 1)
        self.assertEqual(self.parse()[0]["gpt-6-astra"]["calls"], 4)

    def test_cache_retains_other_models_windows_and_project_changes(self):
        self.write(records=[context(), usage(timestamp="2026-09-09T00:00:00Z"),
                            context("gpt-5.6-sol", "/another"), usage()])
        first = self.parse(cutoff=self.cutoff, model="sol")
        self.assertEqual(set(first[0]), {"gpt-5.6-sol"})
        with patch.object(core, "read_file", side_effect=AssertionError("cache hit opened transcript")):
            models, projects = self.parse()
        self.assertEqual(set(models), {"gpt-5.6-sol", "gpt-6-astra"})
        self.assertEqual(set(projects), {"/example", "/another"})

    def test_cache_reprices_records(self):
        self.write()
        self.parse()
        with patch.object(core, "read_file", side_effect=AssertionError("cache should be reused")):
            with patch.dict(CLI["PRICING"], {"gpt-6-astra": (20, 2, 25, 100)}):
                models, _ = self.parse()
        self.assertAlmostEqual(models["gpt-6-astra"]["cost"], 2.84)

    def test_cache_append_truncate_replace_and_delete(self):
        path = self.write()
        self.assertEqual(self.parse()[0]["gpt-6-astra"]["calls"], 1)
        with path.open("a") as handle:
            handle.write(json.dumps(usage()) + "\n")
        self.assertEqual(self.parse()[0]["gpt-6-astra"]["calls"], 2)
        self.write(records=[context("gpt-5.6-sol"), usage()])
        self.assertEqual(set(self.parse()[0]), {"gpt-5.6-sol"})
        replacement = self.write("replacement", [context(), usage()])
        replacement.replace(path)
        self.assertEqual(set(self.parse()[0]), {"gpt-6-astra"})
        path.unlink()
        self.assertFalse(self.parse()[0])
        cache = core.Cache(str(self.root))
        try:
            self.assertFalse(cache.files)
            self.assertEqual(cache.conn.execute("SELECT COUNT(*) FROM events").fetchone()[0], 0)
        finally:
            cache.close()

    def test_same_size_rewrite_with_preserved_mtime_invalidates(self):
        path = self.write(records=[context(), usage(reasoning=2000)])
        self.parse()
        stat = path.stat()
        path.write_text(path.read_text().replace('"reasoning_output_tokens": 2000',
                                                 '"reasoning_output_tokens": 8000'))
        os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        self.assertEqual(path.stat().st_size, stat.st_size)
        models, _ = self.parse()
        self.assertEqual(models["gpt-6-astra"]["output_breakdown"]["reasoning"]["tokens"], 8000)

    def test_partial_tail_is_retried_after_append(self):
        path = self.write()
        extra = json.dumps(usage())
        with path.open("a") as handle:
            handle.write(extra[:40])
        self.assertEqual(self.parse()[0]["gpt-6-astra"]["calls"], 1)
        with path.open("a") as handle:
            handle.write(extra[40:] + "\n")
        self.assertEqual(self.parse()[0]["gpt-6-astra"]["calls"], 2)

    def test_changed_during_parse_is_not_cached(self):
        path = self.write()
        real_signature = core.signature
        signatures = 0

        def changing(stat):
            nonlocal signatures
            signatures += 1
            sig = real_signature(stat)
            return (*sig[:-1], sig[-1] + 1) if signatures == 4 else sig

        with patch.object(core, "signature", side_effect=changing):
            self.assertEqual(self.parse()[0]["gpt-6-astra"]["calls"], 1)
        with patch.object(core, "read_file", wraps=core.read_file) as reader:
            self.parse()
            reader.assert_called_once()

    def test_cache_failures_fall_back_and_no_cache_does_not_create_database(self):
        self.write()
        reference = self.parse(use_cache=False)
        self.assertFalse(self.cache_home.exists())
        for error in (PermissionError("read only"), sqlite3.DatabaseError("corrupt")):
            with patch.object(core, "Cache", side_effect=error), patch("sys.stderr", new=io.StringIO()):
                self.assertEqual(self.parse(), reference)
        self.parse()
        with patch.object(core.Cache, "get", side_effect=sqlite3.OperationalError("locked")), \
                patch("sys.stderr", new=io.StringIO()):
            self.assertEqual(self.parse(), reference)

    def test_corrupt_and_locked_databases_fall_back(self):
        self.write()
        reference = self.parse()
        cache = core.Cache(str(self.root))
        db_path = cache.path
        cache.close()
        connection = sqlite3.connect(db_path)
        try:
            connection.execute("BEGIN EXCLUSIVE")
            with patch("sys.stderr", new=io.StringIO()) as stderr:
                self.assertEqual(self.parse(), reference)
                self.assertIn("cache unavailable", stderr.getvalue())
        finally:
            connection.close()
        db_path.write_bytes(b"not a SQLite database")
        with patch("sys.stderr", new=io.StringIO()) as stderr:
            self.assertEqual(self.parse(), reference)
            self.assertIn("cache unavailable", stderr.getvalue())

    def test_cache_schema_mismatch_is_rebuilt(self):
        self.write()
        reference = self.parse()
        cache = core.Cache(str(self.root))
        cache.conn.execute("PRAGMA user_version=999")
        cache.close()
        self.assertEqual(self.parse(), reference)

    def test_distinct_sessions_roots_have_separate_caches(self):
        self.write()
        first = self.parse()
        other = self.root.parent / "other"
        other.mkdir()
        (other / "log.jsonl").write_text(json.dumps(context("gpt-5.6-sol")) + "\n" + json.dumps(usage()))
        second = CLI["parse"](str(other), None, "")
        self.assertEqual(set(second[0]), {"gpt-5.6-sol"})
        self.assertEqual(self.parse(), first)
        self.assertEqual(len(list((self.cache_home / "codex-cost").glob("*.sqlite3"))), 2)

    def test_symlink_directories_are_followed_without_cycles(self):
        external = self.root.parent / "external"
        external.mkdir()
        (external / "log.jsonl").write_text(json.dumps(context()) + "\n" + json.dumps(usage()))
        (self.root / "linked").symlink_to(external, target_is_directory=True)
        (external / "cycle").symlink_to(self.root, target_is_directory=True)
        self.assertEqual(self.parse()[0]["gpt-6-astra"]["calls"], 1)

    def test_parser_skips_irrelevant_json_and_accepts_escaped_type(self):
        path = self.write(records=[context(), usage(), {"type": "response_item", "payload": "irrelevant"}])
        text = path.read_text().replace('"turn_context"', '"turn_\\u0063ontext"')
        path.write_text(text + "{broken JSON\n[]\n")
        with patch.object(core.json, "loads", wraps=json.loads) as loads:
            rows, stable, error = core.read_file(str(path))
        self.assertEqual(len(rows), 1)
        self.assertEqual(loads.call_count, 2)
        self.assertIsNotNone(stable)
        self.assertIsNone(error)

    def test_parallel_sequential_cached_and_uncached_match(self):
        for index in range(4):
            self.write(f"{index}.jsonl", [context(), usage(input_tokens=272_000 + index,
                                                          reasoning=1000 * index)])
        reference = self.parse(workers=1, use_cache=False)
        with patch.object(core, "PARALLEL_MIN_BYTES", 0):
            self.assertEqual(self.parse(workers=2, use_cache=False), reference)
            self.assertEqual(self.parse(workers=2), reference)
        with patch.object(core, "read_file", side_effect=AssertionError("cache miss")):
            self.assertEqual(self.parse(), reference)

    def test_progress_and_json_are_separate_and_suppressible(self):
        self.write()
        result = self.cli()
        self.assertIn("gpt-6-astra", json.loads(result.stdout)["by_model"])
        self.assertIn("Discovering files", result.stderr)
        self.assertIn("Done in", result.stderr)
        quiet = self.cli("--no-progress")
        self.assertEqual(quiet.stderr, "")
        self.assertEqual(json.loads(result.stdout), json.loads(quiet.stdout))
        invalid = self.cli("--workers", "0", check=False)
        self.assertEqual(invalid.returncode, 2)

    def test_human_output_has_bands_and_reasoning(self):
        self.write()
        result = subprocess.run([sys.executable, str(TOOL), "--all", "--no-progress",
                                 "--sessions-dir", str(self.root)],
                                capture_output=True, text=True, check=True)
        for label in ("<=272k", ">272k", "Output breakdown", "non-reasoning", "Band shares"):
            self.assertIn(label, result.stdout)

    def test_separate_writes_for_all_new_models(self):
        expected = {"gpt-6-astra": 1.01, "gpt-5.6-sol": .404, "gpt-5.6-terra": .222,
                    "gpt-5.6-luna": .0222, "gpt-5.6-cyber": 1.3875}
        for model, dollars in expected.items():
            with self.subTest(model=model):
                event = usage(input_tokens=100000, cached=60000, output=10000, reasoning=2500)
                event["payload"]["info"]["last_token_usage"].update(cache_write_input_tokens=20000,
                    service_tier="default", inference_geo="global")
                self.write(records=[context(model), event])
                models, projects = self.parse(use_cache=False)
                row = models[model]
                self.assertAlmostEqual(row["cost"], dollars)
                self.assertEqual((row["fresh_in"], row["cache_read"], row["cache_write"]), (20000, 60000, 20000))
                self.assertEqual(CLI["token_total"](row), 110000)
                self.assertTrue(CLI["model_json"](row)["pricing_complete"])
                self.assertAlmostEqual(sum(v["cost"] for v in row["input_breakdown"].values()) +
                                       sum(v["cost"] for v in row["output_breakdown"].values()), dollars)
                self.assertAlmostEqual(projects["/example"]["cost"], dollars)

    def test_long_context_writes_and_logged_response_tier_precedence(self):
        ctx = context("gpt-5.6-sol");ctx["payload"]["service_tier"] = "fast"
        event = usage(input_tokens=300000, cached=200000, output=10000)
        event["payload"]["info"]["last_token_usage"].update(cache_write_input_tokens=50000,
                                                         service_tier="default", inference_geo="global")
        self.write(records=[ctx, event])
        row = self.parse()[0]["gpt-5.6-sol"]
        self.assertAlmostEqual(row["cost"], 1.36)
        self.assertAlmostEqual(row["context_bands"]["gt_272k"]["cost"], 1.36)
        event["payload"]["info"]["last_token_usage"]["service_tier"] = "priority"
        self.write(records=[ctx, event])
        self.assertAlmostEqual(self.parse()[0]["gpt-5.6-sol"]["cost"], 2.72)

    def test_missing_write_count_has_range_but_zero_is_exact(self):
        event = usage(input_tokens=100000, cached=60000, output=10000)
        u = event["payload"]["info"]["last_token_usage"]
        u.update(service_tier="default", inference_geo="global")
        u.pop("cache_write_input_tokens")
        self.write(records=[context(), event])
        row = CLI["model_json"](self.parse()[0]["gpt-6-astra"])
        self.assertEqual(row["cost_range_usd"], {"min": .96, "max": 1.06})
        self.assertEqual(row["cache_unknown"], 40000)
        self.assertFalse(row["pricing_complete"])
        u["cache_write_input_tokens"] = 0
        self.write(records=[context(), event])
        row = CLI["model_json"](self.parse()[0]["gpt-6-astra"])
        self.assertTrue(row["pricing_complete"])
        self.assertEqual(row["fresh_in"], 40000)
        self.assertEqual(row["cost_range_usd"], {"min": .96, "max": .96})

    def test_nested_api_usage_and_cached_record_roundtrip(self):
        event = usage(input_tokens=100000, cached=60000, output=10000)
        u = event["payload"]["info"]["last_token_usage"]
        u.pop("cached_input_tokens");u.pop("cache_write_input_tokens")
        u["input_tokens_details"] = {"cached_tokens": 60000, "cache_write_tokens": 20000}
        self.write(records=[context(), event])
        reference = self.parse(use_cache=False)
        self.assertAlmostEqual(reference[0]["gpt-6-astra"]["cost"], 1.01)
        self.assertEqual(self.parse(), reference)
        with patch.object(core, "read_file", side_effect=AssertionError("cache hit expected")):
            self.assertEqual(self.parse(), reference)

    def test_repeated_cumulative_counts_reset_and_identical_real_calls(self):
        def event(total):
            r = usage()
            if total is not None:
                r["payload"]["info"]["total_token_usage"] = {"input_tokens": total, "output_tokens": total}
            return r
        self.write(records=[context(), event(10), event(10), event(20), event(2), event(2),
                            event(10), event(None), event(None)])
        self.assertEqual(self.parse()[0]["gpt-6-astra"]["calls"], 6)

    def test_invalid_counts_are_reported_in_cache_and_do_not_drop_next_record(self):
        for field, value in [("input_tokens", -1), ("cached_input_tokens", 300000),
                             ("cache_write_input_tokens", 100000), ("cache_write_input_tokens", True),
                             ("output_tokens", "10")]:
            with self.subTest(field=field):
                bad = usage();bad["payload"]["info"]["last_token_usage"][field] = value
                self.write(records=[context(), bad, usage()])
                models, _ = self.parse()
                row = CLI["model_json"](models["gpt-6-astra"])
                self.assertEqual(row["invalid_usage_records"], 1)
                self.assertEqual(row["calls"], 1)
                self.assertEqual(row["cost_usd"], 1.42)
                self.assertIsNone(row["cost_range_usd"]["max"])
                self.assertEqual(self.parse()[0], models)

    def test_unknown_model_and_tier_are_never_guessed(self):
        for model, tier in [("gpt-5.6-sol-pro", "default"), ("gpt-6-astra", "ultrafast")]:
            event = usage();event["payload"]["info"]["last_token_usage"]["service_tier"] = tier
            self.write(records=[context(model), event])
            row = CLI["model_json"](self.parse()[0][model])
            self.assertEqual(row["unpriced_calls"], 1)
            self.assertEqual(row["cost_usd"], 0)
            self.assertEqual(row["cache_read"], 200000)
            self.assertIsNone(row["cost_range_usd"]["max"])

    @unittest.skipUnless(hasattr(os, "mkfifo"), "requires Unix FIFOs and signals")
    def test_interrupt_stops_parallel_workers(self):
        # A blocked read makes interruption deterministic without large or slow fixtures.
        os.mkfifo(self.root / "0-blocked.jsonl")
        for index in range(2):
            with (self.root / f"{index + 1}.jsonl").open("wb") as handle:
                handle.truncate(9 * 1024 * 1024)
        process = subprocess.Popen([sys.executable, str(TOOL), "--all", "--no-cache", "--workers", "2",
                                    "--sessions-dir", str(self.root)], stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, text=True, start_new_session=True)
        try:
            # Startup status is flushed before discovery; wait for pool startup as well.
            self.assertIn("Discovering files", process.stderr.readline())
            time.sleep(0.7)
            process.send_signal(signal.SIGINT)
            stdout, stderr = process.communicate(timeout=5)
            self.assertEqual(process.returncode, 130)
            self.assertIn("Interrupted", stderr)
            self.assertNotIn("Traceback", stderr)
            self.assertEqual(stdout, "")
        finally:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
                process.communicate()


class ProgressTests(unittest.TestCase):
    def test_large_file_reports_bytes_before_completion(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "log.jsonl"
            path.write_bytes(b'{"type":"irrelevant","padding":"' + b"x" * (core.PROGRESS_BYTES + 1) + b'"}\n')
            chunks = []
            core.read_file(str(path), chunks.append)
            self.assertEqual(sum(chunks), path.stat().st_size)
            self.assertTrue(chunks)

    def test_terminal_progress_warning_and_interruption(self):
        stream = io.StringIO()
        stream.isatty = lambda: True
        progress = core.Progress(stream=stream)
        with self.assertRaises(KeyboardInterrupt):
            with progress:
                progress.update(stage="Parsing", parse_started=core.time.monotonic() - 1,
                                total_files=2, total_bytes=100, done_bytes=50)
                progress.render()
                progress.warning("example warning")
                raise KeyboardInterrupt
        text = stream.getvalue()
        self.assertIn("[######------]", text)
        self.assertIn("ETA", text)
        self.assertIn("\nwarning: example warning\n", text)
        self.assertIn("Interrupted", text)
        self.assertTrue(text.endswith("\n"))

    def test_nonterminal_progress_has_no_control_characters(self):
        stream = io.StringIO()
        with core.Progress(stream=stream) as progress:
            progress.update(found=100, skipped=90, cached=8, parsed=2)
        self.assertNotIn("\r", stream.getvalue())
        self.assertIn("90 old | 8 cached | 2 parsed", stream.getvalue())


if __name__ == "__main__":
    unittest.main()
