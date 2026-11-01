#!/usr/bin/env python3
"""A provider whose registry entry carries `retired_message` is retired on the Python side too.

The session launcher already refused GitHub Models through `retired_message` (generated into
bin/remote-provider-registry.sh), but the Python dispatch registry ignored the field: GitHub
stayed `tier: renewing_free`, CATALOG_SCANABLE (it has a catalog_url), and the doctor called it
"unimplemented". 0.50.2: one field, honoured by both sides. No network in these tests.
"""
import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "bin"))

import remote_provider_registry as reg  # noqa: E402


def test_github_is_retired():
    assert "github" in reg.RETIRED
    assert "github" not in reg.CATALOG_SCANABLE
    assert "github" not in reg.IMPLEMENTED
    assert reg.PROVIDERS["github"].retired_message.startswith(
        "remote-session: GitHub Models is retired")


def test_retired_message_excludes_any_provider(tmp_path):
    data = json.loads((ROOT / "config" / "remote-registry.json").read_text())
    data["providers"]["gemini"]["retired_message"] = "gone"
    path = tmp_path / "registry.json"
    path.write_text(json.dumps(data))
    providers, _entries, scanable = reg._load_registry(path)
    assert providers["gemini"].retired_message == "gone"
    assert "gemini" not in scanable
    assert "groq" in scanable  # a live provider is untouched


def test_doctor_reports_retired_without_a_key(monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.setenv("LA_API_KEYS_DIR", "/nonexistent-keys-dir")
    spec = importlib.util.spec_from_file_location("doctor", ROOT / "bin" / "remote-provider-doctor.py")
    assert spec is not None and spec.loader is not None
    doctor = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(doctor)
    usable, detail, models = doctor.check_models(reg.PROVIDERS["github"])
    assert (usable, models) == (False, [])
    assert detail.startswith("retired")
    assert doctor.state_for(usable, detail) == "retired"
