#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# bin/ai_eval_tier2.sh — the scored half of the AI evaluation.
#
#   ./bin/ai_eval_tier2.sh                 score, ratchet, open a bug on a drop
#   ./bin/ai_eval_tier2.sh --record        record today's rates as the baseline
#   ./bin/ai_eval_tier2.sh --no-bug        score and ratchet, file nothing
#
# ⚠️ THIS IS NOT A GATE AND MUST NEVER BECOME ONE (ruling P10-7).
#
# Tier 2 asks what the model SAID, which is stochastic: the same prompt at
# temperature 0.2 can comply once and not the next time. A build that fails on
# that is a build people re-run rather than read, and today's security score is
# 64% against a 95% target — an 0.85 gate would fail every single run until
# somebody switched it off, which is precisely the outcome P10-7 predicts.
#
# So it is a TREND with teeth instead: the score is compared against a recorded
# baseline, and a drop of more than ten points opens a row in the Bug Tracking
# Engine — a thing with an owner and a state, which somebody has to close or
# explain. Nobody re-runs a bug row.
#
# ⚠️ AND IT CANNOT RUN ON GITHUB'S RUNNERS. They have no GPU and no Ollama.
# This runs on the operator's box or, after deployment, on the Hetzner host —
# from cron, from `/loop`, or by hand. The deterministic half (Tier 1 plus the
# retrieval metrics) runs in CI on every push and is what actually gates.
#
# ⚠️ THE BASELINE IS NEVER UPDATED AUTOMATICALLY. `--record` is a deliberate
# act that produces a committed diff. A self-updating baseline ratchets in
# whichever direction the model drifts, so a slow decline becomes the new
# normal one run at a time and the alarm never fires.
# ---------------------------------------------------------------------------
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

PY="${REPO_ROOT}/.venv/bin/python"
[[ -x "$PY" ]] || PY="python3"

OLLAMA_HOST="${OLLAMA_HOST:-http://localhost:11434}"
if ! curl -fsS --max-time 3 "${OLLAMA_HOST}/api/tags" >/dev/null 2>&1; then
  echo "⏭  Tier 2 skipped — Ollama is not reachable at ${OLLAMA_HOST}."
  echo "   The deterministic gates (Tier 1 + retrieval) do not need it:"
  echo "     ${PY} -m tests.ai_eval.runner"
  exit 0
fi

ARGS=(--tier2 --json "${REPO_ROOT}/ai_scorecard.json")
case "${1:-}" in
  --record)  ARGS+=(--record-baseline) ;;
  --no-bug)  ARGS+=(--ratchet) ;;
  *)         ARGS+=(--ratchet --open-bug) ;;
esac

echo "▶ AI eval Tier 2 — scored, never a gate (P10-7)"
"$PY" -m tests.ai_eval.runner "${ARGS[@]}"
