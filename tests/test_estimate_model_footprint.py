#!/usr/bin/env python3
"""Tests for bin/estimate-model-footprint.py — parameter-based footprint estimation.

Usage: python3 tests/test_estimate_model_footprint.py [-v]
Environment: none required.
"""
import importlib.util
from pathlib import Path
import os
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

    def test_near_the_ceiling_still_hides_user_rule_2026_10_20(self):
        """A fit at >=4-bit HIDES the row, even near the ceiling.

        User rule (2026-10-20), superseding the old visible-band: "filter any model that
        has downloadable weights on huggingface that would fit my setup at 4 bit
        quantization (or higher). Models that would only fit with 2bit or 3bit
        quantizations are edge cases [shown]." The old caution now lives in the
        confidence label (medium) and the reason text, not in visibility.
        """
        # 170B at 4bit = 85 GB + 8 overhead = 93 GB against a 96 GB budget: a 3 GB margin.
        result = efp.estimate('vendor/model-170b')
        self.assertTrue(result['fits'])
        self.assertEqual(result['suggested'], 'local-capable')
        self.assertEqual(result['confidence'], 'medium')   # the caution moved here
        self.assertIn('ceiling', result['reason'])

    def test_every_fit_is_local_capable_no_visible_gap(self):
        """One rule, no bands: fits at 4-bit hide at ANY margin; only non-fits stay visible.
        A 120B-class model at ~71% of budget is the user's own example (gpt-oss-120b)."""
        for model_id in ('vendor/model-100b', 'vendor/model-120b', 'vendor/model-150b'):
            with self.subTest(model_id=model_id):
                result = efp.estimate(model_id)
                self.assertTrue(result['fits'])
                self.assertEqual(result['suggested'], 'local-capable')

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
        # stdin=DEVNULL: a sweep with --roster '-' reads stdin, and an ad-hoc run under an
        # open pipe (no EOF) would block forever instead of failing (measured 2026-10-20).
        return subprocess.run([sys.executable, str(SCRIPT), *args],
                              text=True, capture_output=True, stdin=subprocess.DEVNULL)

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

    def test_apply_writes_fits_and_never_a_bare_unknown(self):
        """The gate that keeps --apply safe, under the user's 2026-10-20 rule.

        A fit at >=4-bit IS the user's vouch now (their standing instruction, quoted in
        test_near_the_ceiling_still_hides_user_rule_2026_10_20), so --apply writes
        `local-capable` at any confidence. What must STILL never be written is a bare
        `unknown` -- no information, and writing it would convert fail-open into a
        silent hide. Medium-confidence `remote-preferred` (a NEAR overshoot) stays
        human work: only the far-past-the-ceiling ones are safe to write.
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
        self.assertIn('nvidia|vendor/model-120b|local-capable|', text,
                      'the fit is written; the user rule made fit-at-4bit decisive')
        self.assertIn('near ceiling', text,
                      'the hand-written reason is preserved alongside the auto note')
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


class DeviceBudgetTests(unittest.TestCase):
    """T4: the budget comes from the DEVICE (unified RAM on macOS/Linux, VRAM on CUDA),
    never from a hardcoded 128 GB. Tests fake the device read — they never call sysctl
    or nvidia-smi on the real machine."""

    def setUp(self):
        self._orig_device = efp._DEVICE_BUDGET
        efp._DEVICE_BUDGET = (None, None)          # no memo: every test fakes explicitly
        os.environ.pop('LA_MEMORY_BUDGET_GB', None)

    def tearDown(self):
        efp._DEVICE_BUDGET = self._orig_device
        os.environ.pop('LA_MEMORY_BUDGET_GB', None)

    def test_derive_budget_gb(self):
        # 75% of unified RAM mirrors the standing config convention (96 GB of 128).
        self.assertAlmostEqual(efp.derive_budget_gb(128.0, 'unified'), 96.0)
        # VRAM is dedicated (no OS takeback), so its share is higher.
        self.assertAlmostEqual(efp.derive_budget_gb(24.0, 'vram'), 21.6)

    def test_explicit_budget_beats_everything(self):
        efp._DEVICE_BUDGET = (128.0, 'unified')
        self.assertEqual(efp._resolve_budget_gb(50.0), (50.0, 'explicit'))

    def test_env_budget_beats_device(self):
        os.environ['LA_MEMORY_BUDGET_GB'] = '80'
        efp._DEVICE_BUDGET = (128.0, 'unified')
        self.assertEqual(efp._resolve_budget_gb(None), (80.0, 'env'))

    def test_device_budget_when_no_env(self):
        efp._DEVICE_BUDGET = (128.0, 'unified')
        self.assertEqual(efp._resolve_budget_gb(None), (96.0, 'device'))
        efp._DEVICE_BUDGET = (24.0, 'vram')
        self.assertEqual(efp._resolve_budget_gb(None), (21.6, 'device'))

    def test_fallback_budget_when_device_unknown(self):
        efp._DEVICE_BUDGET = (None, None)
        budget, source = efp._resolve_budget_gb(None)
        self.assertEqual(budget, efp.DEFAULT_BUDGET_GB)
        self.assertEqual(source, 'default')

    def test_estimate_uses_device_budget_and_names_the_source(self):
        efp._DEVICE_BUDGET = (32.0, 'unified')     # a small machine: budget 24 GB
        r = efp.estimate('vendor/model-70b')       # 35 + 8 = 43 GB: does NOT fit 24
        self.assertEqual(r['budget_gb'], 24.0)
        self.assertEqual(r['budget_source'], 'device')
        self.assertEqual(r['suggested'], 'remote-preferred')   # visible: clearly not local
        # and the user's own case on their machine (fake 128 GB): gpt-oss-120b now hides
        efp._DEVICE_BUDGET = (128.0, 'unified')
        r = efp.estimate('openai/gpt-oss-120b')
        self.assertEqual(r['budget_gb'], 96.0)
        self.assertEqual(r['suggested'], 'local-capable')


if __name__ == '__main__':
    unittest.main(verbosity=2)
