#!/usr/bin/env bash
# The verify-delegated-work skill must carry the integration-seam rule (skill observation 22):
# verify the object the framework actually uses, with a probe that fails on the old code.
# Also checks the skill's frontmatter still parses as YAML with the expected name.
set -uo pipefail
HERE="$(cd -P "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKILL="$HERE/../skills/verify-delegated-work/SKILL.md"
PASS=0; FAIL=0
check() { if [ "$1" -eq 0 ]; then PASS=$((PASS + 1)); printf 'PASS  %s\n' "$2"; else FAIL=$((FAIL + 1)); printf 'FAIL  %s\n' "$2"; fi; }

r=0; python3 - "$SKILL" <<'PY' || r=1
import re, sys
s = open(sys.argv[1]).read()
m = re.match(r"^---\n(.*?)\n---\n", s, re.S)
assert m, "no frontmatter"
assert re.search(r"(?m)^name: verify-delegated-work$", m.group(1)), "name"
assert re.search(r"(?m)^description: ", m.group(1)), "description"
PY
check $r "frontmatter has name and description"

r=0; grep -qiE '^## Verify at the seam the framework reads' "$SKILL" || r=1
check $r "has the integration-seam section"
r=0; grep -qiE 'actually USES at runtime' "$SKILL" || r=1
check $r "says to assert on the runtime object, not the component"
r=0; grep -qiE 'BEFORE the change and\s*$|watch it fail' "$SKILL" || r=1
check $r "says the probe must fail on the old code"
r=0; grep -qiE 'replaying the real failing input' "$SKILL" || r=1
check $r "prefers replaying the real failing input over a paraphrase"

printf '\n%d passed, %d failed\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ]
