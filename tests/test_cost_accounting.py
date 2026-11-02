"""Independent published rate fixtures: never derive expected rates from production tables."""
import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cost_accounting import model_key, rates_for


class PublishedRates(unittest.TestCase):
    def test_openai_standard_and_modifiers(self):
        expected = {
            'gpt-6-astra': (10, 1, 12.5, 50),
            'gpt-5.6-sol': (4, .4, 5, 20),
            'gpt-5.6-terra': (2, .2, 2.5, 12),
            'gpt-5.6-luna': (.2, .02, .25, 1.2),
        }
        for model, short in expected.items():
            for length in (271999, 272000, 272001):
                for tier, factor in [('default', 1), ('fast', 2), ('priority', 2), ('flex', .5), ('batch', .5)]:
                    with self.subTest(model=model, length=length, tier=tier):
                        actual, assumptions, issue = rates_for('openai', model, length,
                                                              {'service_tier': tier, 'inference_geo': 'global'})
                        want = [r * factor * ((1.5 if i == 3 else 2) if length > 272000 else 1)
                                for i, r in enumerate(short)]
                        self.assertIsNone(issue)
                        self.assertFalse(assumptions)
                        for a, b in zip(actual, want): self.assertAlmostEqual(a, b)

    def test_cyber_is_not_ordinary_gpt5(self):
        actual, _, issue = rates_for('openai', 'gpt-5.6-cyber', 272000, {'service_tier': 'default'})
        self.assertEqual(actual, (12.5, 1.25, 15.625, 75))
        self.assertIsNone(issue)
        for length, tier in [(272001, 'default'), (1000, 'fast'), (1000, 'flex')]:
            self.assertIsNone(rates_for('openai', 'gpt-5.6-cyber', length, {'service_tier': tier})[0])

    def test_claude_rates_and_no_long_context_premium(self):
        expected = {
            'fable-5-1': (10, .25, 12.5, 20, 50), 'mythos-5-1': (10, .25, 12.5, 20, 50),
            'fable-5': (10, 1, 12.5, 20, 50), 'mythos-5': (10, 1, 12.5, 20, 50),
            'opus-5': (5, .5, 6.25, 10, 25), 'sonnet-5': (2, .2, 2.5, 4, 10),
            'haiku-4-5': (1, .1, 1.25, 2, 5),
        }
        for model, prices in expected.items():
            for length in (1000, 900000):
                with self.subTest(model=model, length=length):
                    rates, _, issue = rates_for('claude', model, length, {'service_tier': 'standard'})
                    self.assertEqual(rates, prices)
                    self.assertIsNone(issue)
                    batch = rates_for('claude', model, length, {'service_tier': 'batch'})[0]
                    self.assertEqual(batch, tuple(p * .5 for p in prices))

    def test_speed_and_geography_stack(self):
        rates, _, _ = rates_for('claude', 'opus-5', 400000,
                               {'service_tier': 'standard', 'speed': 'fast', 'inference_geo': 'us'})
        for a, b in zip(rates, (11, 1.1, 13.75, 22, 55)): self.assertAlmostEqual(a, b)
        rates, _, _ = rates_for('openai', 'gpt-5.6-sol', 300000,
                               {'service_tier': 'priority', 'inference_geo': 'us'})
        for a, b in zip(rates, (17.6, 1.76, 22, 66)): self.assertAlmostEqual(a, b)
        for provider, model, metadata in [
                ('openai', 'gpt-6-astra', {'service_tier': 'fast', 'inference_geo': 'eu'}),
                ('openai', 'gpt-5.6-sol', {'service_tier': 'ultrafast'}),
                ('claude', 'sonnet-5', {'speed': 'fast'}),
                ('claude', 'opus-5', {'service_tier': 'batch', 'speed': 'fast'}),
                ('claude', 'haiku-4-5', {'inference_geo': 'us'})]:
            self.assertIsNone(rates_for(provider, model, 1000, metadata)[0])

    def test_legacy_rates_and_boundaries(self):
        self.assertEqual(rates_for('openai', 'gpt-5.5', 272001, {'service_tier': 'default'})[0],
                         (10, 1, None, 45))
        self.assertEqual(rates_for('openai', 'gpt-5.5', 272000, {'service_tier': 'fast'})[0],
                         (12.5, 1.25, None, 75))
        self.assertIsNone(rates_for('openai', 'gpt-5.5', 272001, {'service_tier': 'fast'})[0])
        self.assertEqual(rates_for('openai', 'gpt-5.4', 272000, {'service_tier': 'flex'})[0],
                         (1.25, .13, None, 7.5))
        self.assertEqual(rates_for('openai', 'gpt-5.4', 272001, {'service_tier': 'flex'})[0],
                         (2.5, .25, None, 11.25))

    def test_exact_ids_aliases_and_snapshots(self):
        for source, target in [('gpt-5.6', 'gpt-5.6-sol'), ('gpt-daybreak-blue-latest', 'gpt-5.6-sol'),
                               ('gpt-daybreak-red-latest', 'gpt-5.6-cyber'),
                               ('gpt-6-astra-2026-09-10', 'gpt-6-astra')]:
            self.assertEqual(model_key(source, 'openai'), target)
        self.assertEqual(model_key('claude-fable-5-1', 'claude'), 'fable-5-1')
        self.assertEqual(model_key('claude-haiku-4-5-20251001', 'claude'), 'haiku-4-5')
        for provider, model in [('openai', 'gpt-5.6-sol-pro'), ('openai', 'gpt-5.60'),
                                ('claude', 'claude-fable-5-2'), ('claude', 'claude-opus')]:
            self.assertIsNone(rates_for(provider, model_key(model, provider), 1000, {})[0])


if __name__ == '__main__': unittest.main()
