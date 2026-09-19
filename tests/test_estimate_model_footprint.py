#!/usr/bin/env python3
"""Tests for bin/estimate-model-footprint.py — parameter-based footprint estimation.

Usage: python3 tests/test_estimate_model_footprint.py [-v]
Environment: none required.
"""
import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'bin/estimate-model-footprint.py'
_spec = importlib.util.spec_from_file_location('efp', SCRIPT)
efp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(efp)


class ParsingTests(unittest.TestCase):
    def test_moe_id_reads_total_not_active(self):
        """The case the old plan warned about: two counts in one id.

        Judging `550b-a55b` by its 55B ACTIVE figure would call a ~283 GB model local-capable.
        An MoE holds every expert in memory; only compute is sparse. So total drives size.
        """
        parsed = efp.parse_params('nvidia/nemotron-3-ultra-550b-a55b')
        self.assertEqual(parsed['total_b'], 550)
        self.assertEqual(parsed['active_b'], 55)

    def test_version_and_date_numbers_are_not_read_as_sizes(self):
        # `3.5` is a version and `0731` a date; neither is a parameter count.
        self.assertEqual(efp.parse_params('nvidia/nemotron-3.5-lightning-30b-a3b')['total_b'], 30)
        self.assertIsNone(efp.parse_params('deepseek-ai/deepseek-v4-flash-0731')['total_b'])

    def test_context_length_and_quant_labels_are_not_sizes(self):
        for model_id in ('vendor/model-128k', 'vendor/model-4bit', 'vendor/model-8bit'):
            with self.subTest(model_id=model_id):
                self.assertIsNone(efp.parse_params(model_id)['total_b'],
                                  model_id + ' has no parameter count')

    def test_missing_count_is_unknown_not_a_guess(self):
        parsed = efp.parse_params('moonshotai/kimi-k3')
        self.assertIsNone(parsed['total_b'])
        self.assertEqual(parsed['confidence'], 'unknown')

    def test_active_only_id_does_not_pretend_to_know_the_total(self):
        parsed = efp.parse_params('vendor/model-a3b')
        self.assertEqual(parsed['active_b'], 3)
        self.assertIsNone(parsed['total_b'])


class VerdictTests(unittest.TestCase):
    """The two cases the user named explicitly, and the uncertainty band between them."""

    def test_500b_class_model_is_refused_not_shrugged_at(self):
        """A 550B model must NOT come back 'unknown'.

        Leaving it unknown keeps it visible by default, spending picker attention on a model
        that obviously cannot run on this machine. The overshoot is ~3x the budget, which no
        quantization or KV choice recovers, so the verdict is confident.
        """
        result = efp.estimate('nvidia/nemotron-3-ultra-550b-a55b')
        self.assertEqual(result['suggested'], 'remote-preferred')
        self.assertEqual(result['confidence'], 'high')
        self.assertFalse(result['fits'])
        self.assertGreater(result['total_gb'], 200)

    def test_20b_model_is_accepted_without_advanced_math(self):
        """A 20B model must NOT come back 'unknown' either -- it plainly fits."""
        result = efp.estimate('openai/gpt-oss-20b')
        self.assertEqual(result['suggested'], 'local-capable')
        self.assertEqual(result['confidence'], 'high')
        self.assertTrue(result['fits'])

    def test_near_the_ceiling_is_marked_not_shrugged_at(self):
        """The uncertain band gets its own MARKER rather than a bare `unknown`.

        The id does tell us the model is in the right size class -- just not that it
        definitely fits. `potentially-local-capable` records that, and the filter keeps
        those rows VISIBLE, so fail-open holds while the user can still see which visible
        rows are the near calls. A bare `unknown` would throw away what we do know.
        """
        # 170B at 4bit = 85 GB + 8 overhead = 93 GB against a 96 GB budget: a 3 GB margin.
        result = efp.estimate('vendor/model-170b')
        self.assertTrue(result['fits'])
        self.assertEqual(result['suggested'], 'potentially-local-capable')

    def test_the_two_fit_bands_meet_with_no_confident_gap(self):
        """Every fit is either comfortably local or explicitly marked -- never a silent
        confident verdict in between. A 120B-class model at ~71% of budget used to fall in
        such a gap and read as a plain local-capable it had not earned."""
        for model_id in ('vendor/model-100b', 'vendor/model-120b', 'vendor/model-150b'):
            with self.subTest(model_id=model_id):
                result = efp.estimate(model_id)
                self.assertTrue(result['fits'])
                self.assertEqual(result['suggested'], 'potentially-local-capable')

    def test_no_parameter_count_fails_open_to_visible(self):
        result = efp.estimate('moonshotai/kimi-k3')
        self.assertEqual(result['suggested'], 'unknown')
        self.assertIsNone(result['fits'])

    def test_quantization_changes_the_verdict(self):
        """Same model, different bytes-per-weight: fp16 busts a budget 4-bit clears."""
        self.assertTrue(efp.estimate('vendor/model-70b', quant='4bit')['fits'])
        self.assertFalse(efp.estimate('vendor/model-70b', quant='fp16')['fits'])

    def test_budget_is_a_parameter_not_a_hardcoded_128gb(self):
        big = efp.estimate('vendor/model-70b', budget_gb=200)
        small = efp.estimate('vendor/model-70b', budget_gb=20)
        self.assertTrue(big['fits'])
        self.assertFalse(small['fits'])


class SweepTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def run_script(self, *args):
        return subprocess.run([sys.executable, str(SCRIPT), *args],
                              text=True, capture_output=True)

    def test_help_prints_and_exits_zero_without_doing_work(self):
        result = self.run_script('--help')
        self.assertEqual(result.returncode, 0)
        self.assertIn('--roster', result.stdout)
        self.assertIn('NEVER edits', result.stdout)

    def test_unknown_flag_is_refused_rather_than_ignored(self):
        self.assertNotEqual(self.run_script('--bogus-flag').returncode, 0)

    def test_sweep_flags_a_policy_disagreement(self):
        roster = self.root / 'roster.psv'
        roster.write_text('alias-one|nvidia|vendor/model-20b\n')
        policy = self.root / 'policy.psv'
        policy.write_text('# comment\nnvidia|vendor/model-20b|remote-preferred||nim||stale reason\n')
        result = self.run_script('--roster', str(roster), '--policy', str(policy),
                                 '--disagreements-only')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('DISAGREES', result.stdout)
        self.assertIn('local-capable', result.stdout)

    def test_sweep_flags_a_row_with_no_policy_entry(self):
        roster = self.root / 'roster.psv'
        roster.write_text('alias-one|nvidia|vendor/model-20b\n')
        policy = self.root / 'policy.psv'
        policy.write_text('')
        result = self.run_script('--roster', str(roster), '--policy', str(policy),
                                 '--disagreements-only')
        self.assertIn('UNCLASSIFIED', result.stdout)

    def test_agreement_is_not_reported_as_a_finding(self):
        roster = self.root / 'roster.psv'
        roster.write_text('alias-one|nvidia|vendor/model-20b\n')
        policy = self.root / 'policy.psv'
        policy.write_text('nvidia|vendor/model-20b|local-capable||mlx|10|agrees\n')
        result = self.run_script('--roster', str(roster), '--policy', str(policy),
                                 '--disagreements-only')
        self.assertIn('nothing to review', result.stdout)

    def test_sweep_never_modifies_the_policy_file(self):
        """The safety property: this tool reports, the policy file stays the authority."""
        roster = self.root / 'roster.psv'
        roster.write_text('alias-one|nvidia|vendor/model-20b\n')
        policy = self.root / 'policy.psv'
        original = 'nvidia|vendor/model-20b|remote-preferred||nim||stale\n'
        policy.write_text(original)
        before = policy.stat().st_mtime_ns
        self.run_script('--roster', str(roster), '--policy', str(policy))
        self.assertEqual(policy.read_text(), original)
        self.assertEqual(policy.stat().st_mtime_ns, before)

    def test_apply_is_opt_in_and_dry_run_writes_nothing(self):
        roster = self.root / 'roster.psv'
        roster.write_text('a-one|nvidia|vendor/model-20b\n')
        policy = self.root / 'policy.psv'
        original = 'nvidia|vendor/model-20b|remote-preferred||nim||stale\n'
        policy.write_text(original)

        # Default run: advisory, no write.
        self.run_script('--roster', str(roster), '--policy', str(policy))
        self.assertEqual(policy.read_text(), original, 'default run must not write')

        # Dry run: reports the change, still no write.
        result = self.run_script('--roster', str(roster), '--policy', str(policy),
                                 '--disagreements-only', '--apply-dry-run')
        self.assertIn('would rewrite 1', result.stdout)
        self.assertEqual(policy.read_text(), original, '--apply-dry-run must not write')

    def test_apply_rewrites_high_confidence_and_backs_up(self):
        roster = self.root / 'roster.psv'
        roster.write_text('a-one|nvidia|vendor/model-20b\n')
        policy = self.root / 'policy.psv'
        policy.write_text('# keep this comment\n'
                          'nvidia|vendor/model-20b|remote-preferred||nim||stale\n'
                          'nvidia|other/model-9b|remote-preferred||nim||untouched\n')
        result = self.run_script('--roster', str(roster), '--policy', str(policy),
                                 '--disagreements-only', '--apply')
        self.assertEqual(result.returncode, 0, result.stderr)
        text = policy.read_text()
        self.assertIn('nvidia|vendor/model-20b|local-capable|', text)
        self.assertIn('# keep this comment', text, 'comments must survive')
        self.assertIn('other/model-9b|remote-preferred', text, 'unrelated rows untouched')
        backups = list(self.root.glob('policy.psv.bak-autoestimate-*'))
        self.assertEqual(len(backups), 1, 'a backup must be written before the rewrite')
        self.assertIn('stale', backups[0].read_text())

    def test_apply_never_writes_a_hiding_verdict_it_cannot_vouch_for(self):
        """The gate that keeps --apply safe.

        A near-ceiling row MAY be written, but only as `potentially-local-capable`, which the
        filter keeps VISIBLE -- recording "worth a look", never "hide this". What must never be
        written is a medium-confidence `local-capable` (that WOULD hide a row the estimate
        cannot vouch for) or a bare `unknown` (no information, and it would convert fail-open
        into a silent hide).
        """
        roster = self.root / 'roster.psv'
        roster.write_text('near|nvidia|vendor/model-120b\n'
                          'nocount|nvidia|vendor/mystery-model\n')
        policy = self.root / 'policy.psv'
        policy.write_text('nvidia|vendor/model-120b|remote-preferred||nim||near ceiling\n')
        result = self.run_script('--roster', str(roster), '--policy', str(policy),
                                 '--disagreements-only', '--apply')
        self.assertEqual(result.returncode, 0, result.stderr)
        text = policy.read_text()
        self.assertIn('potentially-local-capable', text,
                      'the near-ceiling row is marked, since marking never hides it')
        self.assertNotIn('|local-capable|', text,
                         'a medium-confidence row must NOT get the hiding verdict')
        self.assertNotIn('unknown', text, 'a bare unknown is never written')
        self.assertNotIn('mystery-model', text,
                         'a row with no parameter count is not written at all')
        self.assertIn('near ceiling', text, 'the prior hand-written note is preserved')

    def test_apply_preserves_file_mode(self):
        roster = self.root / 'roster.psv'
        roster.write_text('a-one|nvidia|vendor/model-20b\n')
        policy = self.root / 'policy.psv'
        policy.write_text('nvidia|vendor/model-20b|remote-preferred||nim||stale\n')
        policy.chmod(0o600)
        self.run_script('--roster', str(roster), '--policy', str(policy),
                        '--disagreements-only', '--apply')
        self.assertEqual(policy.stat().st_mode & 0o777, 0o600,
                         'the rewrite must not widen permissions')

    def test_real_roster_and_policy_parse_without_error(self):
        """Guards against the shipped policy file drifting out of a parseable shape."""
        result = self.run_script('--roster', '-', '--policy',
                                 str(ROOT / 'config/local-capable-remote-models.psv'))
        # stdin is empty here; the point is the policy file loads cleanly.
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == '__main__':
    unittest.main(verbosity=2)
