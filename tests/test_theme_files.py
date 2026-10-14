"""Custom theme files and the per-session theme selection for both free lanes.

Two facts measured on CLI 2.1.296 (2026-10-13) are pinned here:
  * a theme is only loaded when the `theme` setting reads "custom:<slug>" -- a bare slug is
    an unknown built-in name and falls back silently (the lime accent never showed for weeks);
  * the theme role `clawd_body` recolours the welcome-screen mascot (seen magenta in a probe).
"""
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENV = {"LAUNCH_DIR": str(ROOT / "bin"), "PATH": "/usr/bin:/bin"}


def run(script, *args, env_extra=None):
    env = dict(ENV, **(env_extra or {}))
    out = subprocess.run([sys.executable, str(ROOT / "bin" / script), *args],
                         capture_output=True, text=True, env=env)
    assert out.returncode == 0, out.stderr
    return out.stdout


def identity(kind, **extra):
    return json.dumps(dict({"session_kind": kind}, **extra))


def accent(name):
    return json.loads((ROOT / "config" / name).read_text())["theme"]["accent_color"]


def test_accents_are_the_requested_colours():
    assert accent("local-theme.json") == "#00BFFF"   # dark sky blue
    assert accent("remote-theme.json") == "#ADFF2F"  # green-yellow (user choice, 0.34.4)


def test_both_theme_files_tint_the_mascot_and_spare_semantic_colours():
    for script, data in (("generate-local-settings.py", "local-theme.json"),
                         ("generate-remote-settings.py", "remote-theme.json")):
        t = json.loads(run(script, "--emit-theme-file"))
        assert t["base"] == "dark"
        assert t["overrides"]["clawd_body"] == accent(data)
        assert t["overrides"]["claude"] == accent(data)
        for semantic in ("error", "warning", "success"):
            assert semantic not in t["overrides"]


def test_prompt_outline_is_the_accent_desaturated_at_the_same_lightness():
    # User 2026-10-14: as bright as the accent, less saturated, not dark.
    import colorsys
    def hls(h): return colorsys.rgb_to_hls(*(int(h[i:i+2], 16) / 255 for i in (1, 3, 5)))
    for script, data in (("generate-local-settings.py", "local-theme.json"),
                         ("generate-remote-settings.py", "remote-theme.json")):
        ov = json.loads(run(script, "--emit-theme-file"))["overrides"]
        border = ov["promptBorder"]
        assert ov["promptBorderShimmer"] == border
        (ah, al, as_), (bh, bl, bs) = hls(accent(data)), hls(border)
        assert abs(al - bl) < 0.02, (data, al, bl)     # not darker
        assert bs < as_ - 0.2, (data, as_, bs)         # clearly less saturated
        assert abs(ah - bh) < 0.02                     # same green / same blue


def test_local_session_selects_local_sky_with_prefix():
    s = json.loads(run("generate-local-settings.py", "--identity-json",
                       identity("local", actual_model_id="qwen3.8-35b")))
    assert s["theme"] == "custom:local-sky"


def test_remote_session_selects_free_lime_with_prefix():
    s = json.loads(run("generate-remote-settings.py", "--identity-json",
                       identity("free_api", provider_display="Free API (NVIDIA)")))
    assert s["theme"] == "custom:free-lime"


def test_theme_slug_matches_the_file_the_launcher_installs():
    # The launcher writes ~/.claude/themes/<slug>.json; the setting must name that same slug.
    local = json.loads((ROOT / "config" / "local-theme.json").read_text())["theme"]["name"]
    remote = json.loads((ROOT / "config" / "remote-theme.json").read_text())["theme"]["name"]
    assert (local, remote) == ("local-sky", "free-lime")
    launcher = (ROOT / "bin" / "launch-claude-agent.sh").read_text()
    assert "local-sky.json" in launcher


def test_gateway_identity_gets_no_theme_and_no_words():
    for script in ("generate-local-settings.py", "generate-remote-settings.py"):
        s = json.loads(run(script, "--identity-json", identity("cloud")) or "{}")
        assert "theme" not in s and "spinnerVerbs" not in s
        assert json.loads(run(script, "--emit-model-words", "--identity-json", identity("cloud"))) == []


def test_spinner_mode_follows_mod_attachment():
    ident = identity("local", actual_model_id="qwen3.8-35b")
    plain = json.loads(run("generate-local-settings.py", "--identity-json", ident))
    modded = json.loads(run("generate-local-settings.py", "--identity-json", ident,
                            env_extra={"LA_MOD_ATTACHED": "1"}))
    assert plain["spinnerVerbs"]["mode"] == "replace"
    assert modded["spinnerVerbs"]["mode"] == "append"


def test_model_words_come_from_the_real_model():
    words = json.loads(run("generate-remote-settings.py", "--emit-model-words", "--identity-json",
                           identity("free_api", provider_display="Free API (NVIDIA Nemotron)")))
    assert words and all("emotron" in w for w in words)
    local = json.loads(run("generate-local-settings.py", "--emit-model-words", "--identity-json",
                           identity("local", actual_model_id="qwen3.8-35b")))
    assert local and all(w.startswith("Qwen") for w in local)
