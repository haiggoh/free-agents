#!/usr/bin/env python3
"""A receipt the manager writes must be readable by the manager — the deletion bug's root cause.

Measured 2026-10-30: every real receipt held `pip_freeze` but no `version` key, and every reader
(base.locked_requirements, VenvMixin.locked_requirements, RapidMLXManager._locked_requirements)
required `payload["version"] == version`. So `remove` always refused ("run snapshot"), and
`snapshot` wrote the same keyless receipt again — a loop no user action could break.

The writer under test is the production one (save_receipt); the legacy fixture copies the REAL
key set observed on disk, never a hand-made shape that only the new reader understands.
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "install"))

import managers.rapid_mlx  # noqa: E402,F401
import managers.vllm_mlx  # noqa: E402,F401
from managers import get_manager  # noqa: E402

# The exact top-level keys of ~/.cache/local-agents/rapid-runtime-manager/receipts/rapid-mlx-0.16.1.json
LEGACY_KEYS = ["packages", "pip_freeze", "python", "python_version", "rapid_binary_sha256",
               "rapid_version_output", "result", "schema_version", "validated_at"]


def legacy_receipt(freeze: list[str]) -> dict:
    data: dict = {k: "" for k in LEGACY_KEYS}
    data.update({"packages": {}, "pip_freeze": freeze, "result": "snapshot", "schema_version": 2})
    return data


class ReceiptRoundTripTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_written_receipt_is_read_back_for_every_venv_backend(self):
        for name, pkg in (("rapid-mlx", "rapid-mlx"), ("vllm-mlx", "vllm-mlx")):
            with self.subTest(backend=name):
                mgr = get_manager(name)(home=self.home)
                freeze = [f"{pkg}==1.2.3", "mlx==0.32.2"]
                mgr.save_receipt("1.2.3", {"pip_freeze": freeze, "result": "snapshot"})
                self.assertEqual(mgr.locked_requirements("1.2.3"), freeze)
                saved = json.loads(mgr.receipt_path("1.2.3").read_text(encoding="utf-8"))
                self.assertEqual(saved["version"], "1.2.3")
                self.assertEqual(saved["backend"], name)

    def test_legacy_receipt_without_version_key_is_accepted_when_the_freeze_pins_it(self):
        mgr = get_manager("rapid-mlx")(home=self.home)
        path = mgr.receipt_path("0.16.1")
        path.parent.mkdir(parents=True, exist_ok=True)
        freeze = ["rapid-mlx==0.16.1", "mlx==0.31.2"]
        path.write_text(json.dumps(legacy_receipt(freeze)), encoding="utf-8")
        self.assertEqual(mgr.locked_requirements("0.16.1"), freeze)

    def test_freeze_that_does_not_pin_the_version_is_rejected(self):
        mgr = get_manager("rapid-mlx")(home=self.home)
        mgr.save_receipt("0.2.0", {"pip_freeze": ["rapid-mlx==0.1.0"]})
        self.assertIsNone(mgr.locked_requirements("0.2.0"))

    def test_receipt_for_another_version_is_rejected(self):
        mgr = get_manager("rapid-mlx")(home=self.home)
        path = mgr.receipt_path("0.3.0")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"version": "0.2.0", "pip_freeze": ["rapid-mlx==0.3.0"]}), encoding="utf-8")
        self.assertIsNone(mgr.locked_requirements("0.3.0"))

    def test_receipt_is_private(self):
        mgr = get_manager("rapid-mlx")(home=self.home)
        mgr.save_receipt("0.2.0", {"pip_freeze": ["rapid-mlx==0.2.0"]})
        self.assertEqual(mgr.receipt_path("0.2.0").stat().st_mode & 0o777, 0o600)


if __name__ == "__main__":
    unittest.main()
