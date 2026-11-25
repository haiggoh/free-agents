#!/usr/bin/env python3
"""tests/live_proxy/test_midstream_claude_cli.py — R3 Task 3 delivery: the REAL claude CLI
keeps the turn on a mid-stream upstream death, and the NEXT request carries the hint
(acceptance criteria 2 and 3 of the R3 plan).

Skipped with a NAMED reason only when the claude binary is absent — the receipt then says
"not tested", never "passed".
"""
import json
import os
import shutil
import subprocess

import pytest

CLAUDE = shutil.which("claude")


def _run_claude(cfg, proxy_port, args, timeout=180):
    env = dict(os.environ)
    env.update({
        "ANTHROPIC_BASE_URL": f"http://127.0.0.1:{proxy_port}",
        # both set to the SAME value, exactly like remote-session.sh's live sessions —
        # otherwise LiteLLM sees a master key + a differing user key and demands a db
        "ANTHROPIC_AUTH_TOKEN": "sk-test",
        "ANTHROPIC_API_KEY": "sk-test",          # the rig config's master_key
        "ANTHROPIC_MODEL": "claude-opus-5-5",     # the rig maps exactly this model name
        "CLAUDE_CONFIG_DIR": str(cfg),
        "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
    })
    # prompt/args FIRST: --mcp-config is variadic and would swallow a trailing prompt
    return subprocess.run([CLAUDE or "claude", "-p", *args, "--strict-mcp-config", "--mcp-config",
                           '{"mcpServers":{}}', "--permission-mode", "bypassPermissions"],
                          capture_output=True, text=True, env=env, timeout=timeout)


def _transcript(cfg):
    proj = cfg / "projects"
    files = sorted(proj.glob("*/*.jsonl"), key=lambda p: p.stat().st_mtime) if proj.exists() else []
    assert files, f"no transcript under {proj}"
    return files[-1]


@pytest.mark.skipif(CLAUDE is None, reason="claude binary not on PATH — delivery NOT tested (named skip)")
def test_claude_cli_keeps_turn_and_next_request_carries_hint(fake_upstream, live_proxy,
                                                            two_tier_config, tmp_path):
    assert CLAUDE is not None  # (skipif guards this; the assert narrows the type)
    up = fake_upstream("toolcut")
    proxy = live_proxy(two_tier_config(up.port, None, hooks=True))
    try:
        up.set_mode("toolcut 2")
        cfg = tmp_path / "claude-cfg"
        cfg.mkdir()
        r = _run_claude(cfg, proxy.port, ["say hi"])
        assert r.returncode == 0, (r.returncode, r.stdout[-400:], r.stderr[-400:])
        assert "[free-agents] The provider closed" in r.stdout, (r.stdout[-800:], up.hits())
        # acceptance 2: the transcript has NO isApiErrorMessage record for this turn
        recs = [json.loads(line) for line in _transcript(cfg).read_text().splitlines() if line.strip()]
        errors = [rec for rec in recs if rec.get("isApiErrorMessage")]
        assert not errors, errors[:1]
        # acceptance 3: resume with the fake back to ok — the next request carries the hint
        up.set_mode("ok")
        session = _transcript(cfg).stem
        r2 = _run_claude(cfg, proxy.port, ["--resume", session, "and again"])
        assert r2.returncode == 0, (r2.returncode, r2.stdout[-300:], r2.stderr[-300:])
        hits = up.hits()
        assert any(h.get("has_hint") for h in hits[1:]), hits
    finally:
        proxy.stop()
