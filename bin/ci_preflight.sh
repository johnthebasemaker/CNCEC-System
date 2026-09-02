#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# bin/ci_preflight.sh — one second of checks that would have saved two months.
#
#   ./bin/ci_preflight.sh              audit the gates
#   ./bin/ci_preflight.sh --list       what it checks, and why each rule exists
#   ./bin/ci_preflight.sh FILE...      audit specific files
#
# WHY THIS EXISTS: `postgres-dual-ci.yml` failed on 30 consecutive GitHub
# runs while `legacy/bug_check.py` passed 599/0 on the operator's Mac. The
# cause was one line — a process-wide `subprocess.Popen = lambda *a, **kw:
# None` — which broke `ctypes.util.find_library` on Linux and therefore
# `import pyzbar`, and which was invisible on macOS because `find_library`
# there probes dyld and never shells out. The check that broke then swallowed
# the wreckage with `except ImportError:` and reported a PASS.
#
# Nothing in this repo's 2,900-odd gate assertions could see any of that,
# because every one of them tests the APPLICATION. This tests the HARNESS.
#
# It is deliberately the FIRST step of the CI job: it costs about a second and
# it fails with a file, a line and a reason, where the suite it precedes takes
# 34 seconds and used to fail with somebody else's log line.
# ---------------------------------------------------------------------------
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

# Prefer the project venv when it exists (it is what CI and the operator both
# actually run), fall back to whatever `python` is on PATH — GitHub's runner
# uses setup-python and has no .venv.
if [[ -x ".venv/bin/python" ]]; then
  PY=".venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
  PY="python3"
else
  PY="python"
fi

echo "▶ CI preflight · $($PY -V 2>&1)"

# ⚠️ THE AUDITOR PROVES ITSELF FIRST. Every rule below is checked against a
# snippet that must trip it AND one that must not, because a static rule that
# has quietly stopped matching is indistinguishable from a clean tree — which
# is the exact failure mode (`QR Badges 2/2` for an assertion that never ran)
# this whole script exists to prevent.
"$PY" tools/harness_hygiene.py --self-test

"$PY" tools/harness_hygiene.py "$@"
rc=$?

if [[ $rc -eq 0 && $# -eq 0 ]]; then
  echo "▶ preflight clean — the gates may now speak for the application."
fi
exit $rc
