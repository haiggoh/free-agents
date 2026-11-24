"""tests/settings_guard.py — helper for the conftest's real-settings report (0.63.2).

Lives in its OWN module, not in conftest.py: `from conftest import …` is ambiguous in this repo
(tests/live_proxy/conftest.py is also a module named `conftest`), and in the ship gate it
resolved to the live-proxy one (ImportError, 2026-11-24). Not a test file (no test_ prefix).
"""


def changed_setting_keys(before, after) -> list[str]:
    """Dotted names of the keys whose value differs between two settings.json byte snapshots
    (values are never returned: settings.json can hold tokens). A missing file is {};
    unparseable JSON reports as ['<unparseable settings.json>']."""
    import json

    def load(b):
        if b is None:
            return {}
        try:
            d = json.loads(b)
        except ValueError:
            return None
        return d if isinstance(d, dict) else None

    def flat(d, p=""):
        out = {}
        for k, v in d.items():
            kk = f"{p}.{k}" if p else str(k)
            if isinstance(v, dict) and v:
                out.update(flat(v, kk))
            else:
                out[kk] = json.dumps(v, sort_keys=True)
        return out

    a, b = load(before), load(after)
    if a is None or b is None:
        return ["<unparseable settings.json>"]
    fa, fb = flat(a), flat(b)
    return sorted(k for k in set(fa) | set(fb) if fa.get(k) != fb.get(k))
