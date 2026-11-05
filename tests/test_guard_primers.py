"""Guard primers: every PreToolUse guard in hooks/hooks.json carries a module-level PRIMER
(one line, <= 140 chars) that hooks/offload-nudge.py injects once at SessionStart — prevention,
so the guard below stays a safety net that rarely fires.

cloud-agent-intercept.py is exempt here: its primer is mode-aware and built inside
offload-nudge.py's guards_section from the route modes (agent/workflow), so it goes silent
when the menu opt-out (route=paid) turns the hook off. A guard added without a PRIMER fails
this suite — the barrier, not a reminder."""
import importlib.util
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HOOKS = json.loads((ROOT / "hooks" / "hooks.json").read_text())["hooks"]
EXEMPT = {"cloud-agent-intercept.py"}
PRIMER_MAX = 140


def _pretooluse_scripts():
    for entry in HOOKS["PreToolUse"]:
        for hook in entry["hooks"]:
            m = re.search(r"hooks/([\w.-]+\.py)", hook["command"])
            if m:
                yield m.group(1)


def _load(name):
    spec = importlib.util.spec_from_file_location(
        name.replace("-", "_").removesuffix(".py"), ROOT / "hooks" / name)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_every_pretooluse_guard_in_hooks_json_has_a_primer_or_is_exempt():
    names = sorted(set(_pretooluse_scripts()))
    assert names, "no PreToolUse hook scripts resolved from hooks.json"
    for name in names:
        if name in EXEMPT:
            continue
        primer = getattr(_load(name), "PRIMER", None)
        assert isinstance(primer, str) and primer.strip(), f"{name} has no PRIMER constant"
        assert len(primer) <= PRIMER_MAX, \
            f"{name}: PRIMER is {len(primer)} chars (max {PRIMER_MAX}): {primer!r}"
        assert "\n" not in primer, f"{name}: PRIMER must be a single line"


def test_exempt_set_is_exactly_the_mode_aware_guard():
    # If cloud-agent-intercept stops being the only mode-aware guard, revisit the exemption.
    assert EXEMPT == {"cloud-agent-intercept.py"}
    assert "cloud-agent-intercept.py" in set(_pretooluse_scripts())
