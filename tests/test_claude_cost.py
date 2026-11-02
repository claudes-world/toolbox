import contextlib
import copy
import io
import json
import runpy
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

TOOL = Path(__file__).parents[1] / 'claude-cost' / 'claude-cost'
CLI = runpy.run_path(str(TOOL))


def message(model='claude-fable-5-1', mid='one', output=10000, stamp='2026-09-12T12:00:00Z'):
    return {'timestamp': stamp, 'message': {'id': mid, 'model': model, 'stop_reason': 'end_turn', 'usage': {
        'input_tokens': 20000, 'cache_read_input_tokens': 60000, 'output_tokens': output,
        'cache_creation_input_tokens': 20000,
        'cache_creation': {'ephemeral_5m_input_tokens': 10000, 'ephemeral_1h_input_tokens': 10000},
        'service_tier': 'standard', 'speed': 'standard', 'inference_geo': 'global'}}}


class ClaudeCostTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def write(self, records, name='project/session.jsonl'):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('\n'.join(json.dumps(r) for r in records))

    def parse(self, cutoff=None, model=''):
        return CLI['parse'](str(self.root), cutoff, model)

    def report(self, models, projects):
        stream = io.StringIO()
        with contextlib.redirect_stdout(stream):
            CLI['to_json'](models, projects, 'sample', None, True)
        return json.loads(stream.getvalue())

    def test_independent_cost_examples_for_every_model(self):
        # 20k ordinary, 60k read, 10k 5m writes, 10k 1h writes, 10k output.
        expected = {'claude-fable-5-1': 1.04, 'claude-mythos-5-1': 1.04,
                    'claude-fable-5': 1.085, 'claude-mythos-5': 1.085,
                    'claude-opus-5': .5425, 'claude-sonnet-5': .217, 'claude-haiku-4-5': .1085}
        for model, dollars in expected.items():
            with self.subTest(model=model):
                self.write([message(model)])
                models, projects = self.parse()
                row = next(iter(models.values()))
                self.assertAlmostEqual(row['cost'], dollars)
                self.assertAlmostEqual(projects['project']['cost'], dollars)
                self.assertAlmostEqual(sum(v['cost'] for v in row['input_breakdown'].values()) +
                                       row['out'] * {'claude-fable-5-1': 50, 'claude-mythos-5-1': 50,
                                                     'claude-fable-5': 50, 'claude-mythos-5': 50,
                                                     'claude-opus-5': 25, 'claude-sonnet-5': 10,
                                                     'claude-haiku-4-5': 5}[model] / 1e6, dollars)
                self.assertTrue(next(iter(self.report(models, projects)['by_model'].values()))['pricing_complete'])

    def test_final_usage_wins_across_copies_even_if_partial_has_later_timestamp(self):
        partial = message(output=3, stamp='2026-09-12T13:00:00Z')
        partial['message']['stop_reason'] = None
        final = message(output=10000)
        final['message']['usage']['iterations'] = [{'output_tokens': 10000}]
        self.write([partial], 'project/a.jsonl')
        self.write([final, final, partial], 'project/z.jsonl')
        row = self.parse()[0]['fable-5-1']
        self.assertEqual((row['calls'], row['out']), (1, 10000))
        self.assertAlmostEqual(row['cost'], 1.04)

    def test_latest_partial_and_completed_usage(self):
        first = message(output=2)
        first['message']['stop_reason'] = None
        last = copy.deepcopy(first)
        last['message']['usage']['output_tokens'] = 30
        self.write([first, last])
        self.assertEqual(self.parse()[0]['fable-5-1']['out'], 30)
        last['message']['stop_reason'] = 'end_turn'
        later = copy.deepcopy(last)
        later['message']['usage']['output_tokens'] = 40
        self.write([first, last, later])
        self.assertEqual(self.parse()[0]['fable-5-1']['out'], 40)

    def test_time_filter_applies_to_selected_record(self):
        partial = message(stamp='2026-09-11T23:59:59Z')
        partial['message']['stop_reason'] = None
        final = message(stamp='2026-09-12T00:00:00Z')
        self.write([partial, final])
        cutoff = datetime(2026, 9, 12, tzinfo=timezone.utc)
        self.assertEqual(self.parse(cutoff)[0]['fable-5-1']['calls'], 1)
        self.assertFalse(self.parse(cutoff, 'haiku')[0])

    def test_missing_duration_range_and_partial_breakdown(self):
        r = message();u = r['message']['usage']
        u.pop('cache_creation')
        self.write([r]);models, projects = self.parse();report = self.report(models, projects)
        self.assertEqual(report['cost_range_usd'], {'min': .97, 'max': 1.11})
        self.assertFalse(report['pricing_complete'])
        self.assertEqual(models['fable-5-1']['cw_unknown'], 20000)
        u['cache_creation'] = {'ephemeral_5m_input_tokens': 10000}
        self.write([r]);models, projects = self.parse()
        self.assertEqual(models['fable-5-1']['cw_unknown'], 10000)
        self.assertAlmostEqual(models['fable-5-1']['cost_extra'], .075)

    def test_explicit_zero_writes_and_breakdown_without_total(self):
        r = message();u = r['message']['usage']
        u['cache_creation_input_tokens'] = 0;u.pop('cache_creation')
        self.write([r]);models, projects = self.parse()
        self.assertTrue(self.report(models, projects)['pricing_complete'])
        u.pop('cache_creation_input_tokens');u['cache_creation'] = {'ephemeral_1h_input_tokens': 20000}
        self.write([r]);models, _ = self.parse()
        self.assertEqual(models['fable-5-1']['cw1h'], 20000)
        self.assertEqual(models['fable-5-1']['cw_unknown'], 0)

    def test_invalid_usage_is_reported_and_valid_following_records_survive(self):
        for field, value in [('input_tokens', -1), ('output_tokens', True), ('cache_creation_input_tokens', 1),
                             ('cache_creation', []), ('cache_read_input_tokens', '60000')]:
            with self.subTest(field=field):
                bad = message(mid='bad');bad['message']['usage'][field] = value
                self.write([bad, message()])
                models, projects = self.parse();report = self.report(models, projects)
                self.assertEqual(report['invalid_usage_records'], 1)
                self.assertEqual(report['total_cost_usd'], 1.04)
                self.assertIsNone(report['cost_range_usd']['max'])

    def test_unknown_and_unsupported_tier_preserve_counts_not_guessed_prices(self):
        for model, speed in [('claude-fable-5-2', 'standard'), ('claude-sonnet-5', 'fast')]:
            r = message(model);r['message']['usage']['speed'] = speed
            self.write([r]);models, projects = self.parse();report = self.report(models, projects)
            self.assertEqual(report['unpriced_calls'], 1)
            self.assertEqual(report['total_cost_usd'], 0)
            self.assertEqual(next(iter(report['by_model'].values()))['cache_read'], 60000)
            self.assertIsNone(report['cost_range_usd']['max'])

    def test_sonnet_intro_alias_and_json_consumer_fields(self):
        self.write([message('claude-sonnet-5')]);models, projects = self.parse();report = self.report(models, projects)
        self.assertEqual(report['total_cost_usd'], report['total_cost_intro_usd'])
        row = report['by_model']['sonnet-5']
        for field in ('calls', 'fresh_input', 'cache_read', 'cache_write', 'output', 'cost_usd'):
            self.assertIn(field, row)
        self.assertEqual(row['cache_write'], row['cache_write_5m'] + row['cache_write_1h'] + row['cache_write_unknown'])
        r = subprocess.run([sys.executable, str(TOOL), '--all', '--projects-dir', str(self.root)],
                           capture_output=True, text=True, check=True)
        self.assertNotIn('intro pricing', r.stdout)
        self.assertIn('cache_write_1h', r.stdout)

    def test_missing_metadata_is_disclosed(self):
        r = message();u = r['message']['usage'];u.pop('service_tier');u.pop('inference_geo')
        self.write([r]);report = self.report(*self.parse())
        self.assertFalse(report['pricing_complete'])
        self.assertEqual(len(report['pricing_assumptions']), 2)
        self.assertEqual(report['total_cost_usd'], 1.04)


if __name__ == '__main__': unittest.main()
