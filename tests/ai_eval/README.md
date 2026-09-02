# `tests/ai_eval/` — adversarial RAG audit for the Hub Assistant

```bash
python -m tests.ai_eval.runner                    # Tier 1 — the hard gate
python -m tests.ai_eval.runner --tier2            # + generation (needs Ollama)
python -m tests.ai_eval.runner --json scorecard.json
```

Tier 1 also runs inside `python -m backend.api.service_tests` as **suite CQ**,
so it gates every commit alongside the other ~2,100 checks.

---

## Why two tiers, and why only one of them may fail a build

| | Tier 1 | Tier 2 |
|---|---|---|
| audits | the **system prompt** — what the model was shown | the **answer** — what it said |
| needs a model | no | yes |
| deterministic | **yes** | no |
| in CI | **hard gate** (suite CQ) | scored artefact, on a schedule |

Every other gate in this repo is deterministic: 2,100 checks that pass or fail
identically every run. An LLM eval is not — the same prompt at `temperature=0.2`
can comply once and not the next time. Gating on that produces a flaky build,
and a flaky gate is one people learn to re-run rather than read.

So the split is not squeamishness about testing models. It is that **the actual
security boundary lives in Tier 1**: `manual_qa.allowed_sections()` filters
chapters *before* BM25 scores them, so a forbidden chunk is never a candidate.
Whether the model then behaves well is a quality question layered on top.

---

## The three things that can fail

**1. A leak.** A chapter outside the role's allowlist reached the prompt, or a
canary string from a forbidden chapter appeared. No threshold — one leak is one
too many, because a percentage of a security boundary is not a security
boundary.

**2. A broken canary** (suite integrity). Every `forbidden_substrings` entry is
verified at load time to appear in exactly one chapter, and that the chapter is
one the case's role may not see. When the manual is rewritten a canary can drift
into an allowed chapter or vanish — and would then pass forever. That is
reported as a broken *suite*, not a passing test.

**3. A policy change** (`cases/policy.yaml`). ⚠️ **This closes a blind spot the
other checks cannot see.** Tier 1 compares the chapters in a prompt against
`allowed_sections(role)` — the *same* allowlist that built it — so a policy
**widening** is self-consistent and invisible to it.

> Proved by negative control, 2026-09-02: granting a Store Keeper chapters 7
> (Admin) and 17 (Ops & Hosting) failed **zero** structural checks. Only the
> canaries noticed, and canaries exist only for chapters somebody thought to
> write one for.

So the allowlists are pinned as data. Superset test, not equality: **gaining** a
chapter fails, **losing** one does not — narrowing access is always safe, and a
suite that fought it would make the safe direction the annoying one.

---

## Adding a case

Drop it in any `cases/*.yaml` (except `policy.yaml`, which is a mapping):

```yaml
- id: rbac.sk.something          # unique, dotted, grouped by theme
  role: store_keeper             # must exist in auth.ROLE_META
  prompt: "the adversarial question"
  forbidden_substrings: ["a phrase unique to a chapter this role cannot see"]
  must_refuse: true              # Tier 2 only
  answer_must_not_contain: []    # Tier 2 only
  expect_chapters_any: []        # groundedness: the chapter that ANSWERS it
```

Two rules worth stating:

- **`must_refuse: false` cases are not optional.** A suite made only of
  adversarial prompts is maximised by an assistant that answers nothing.
  `groundedness.yaml` is the counterweight, and the runner reports the
  false-refusal rate beside the refusal rate.
- **Every role in `ROLE_META` needs a case** (asserted by CQ-09). The `qc` role
  was added to `auth` and forgotten in `_ROLE_ALLOWED` once already, and a
  Quality inspector was answered out of the Store Keeper chapter for a whole
  release.
