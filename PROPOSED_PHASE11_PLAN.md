# PROPOSED PHASE 11 PLAN — Enterprise AI Engineering, Observability & Security Gateways

> **Status: PROPOSAL. No application code written.** Awaiting operator approval.
> **Written 2026-09-02** against `main` @ `32ed898`, after reading
> `PROJECT_HANDOVER.md`, `SESSION_HANDOVER.md`, `docs/ARCHITECTURE.md`,
> `docs/PROJECT_STATUS.md`, `USER_MANUAL.md` (§ index), `backend/api/ai/*` and
> `tests/ai_eval/*`.
>
> **The one-line verdict on the blueprint:** of the four named tools, **I
> recommend rejecting all four as dependencies** — Portkey, LangSmith,
> Guardrails-AI and RAGAS/DeepEval each solve a problem this system already
> half-solves, and each pays for it with either a new always-on daemon, a
> cloud egress of proprietary data, or a large transitive dependency tree that
> would out-mass the module it wraps. What I propose instead is **five small
> native modules and one honest re-reading of the eval brief** that between
> them deliver everything the blueprint asks for, on Postgres and Ollama, with
> zero new services and zero data leaving the network.
>
> ⚠️ **One item in the brief collides head-on with a LOCKED ruling.** Track 5
> asks for a CI gate that fails below 0.85 on Faithfulness/Answer-Relevance.
> Ruling **P10-7** says Tier 2 (model-answer) evals never gate a merge. I am
> **not** proposing we overturn P10-7 — I am proposing a split that gets you a
> hard gate anyway, on the half of the metric set that is deterministic. See
> §6.3. That resolution is the single most important decision in this document.

---

## 0. Contents

| § | Track | Verdict in one line |
|---|---|---|
| [1](#1-the-ci-bug) | **CI bug** (Track 1a) | **SOLVED — root cause proven.** The WhatsApp line is a red herring; the real failure is the QR check, and the cause is `bug_check.py` line 46 |
| [2](#2-claudemd) | **`CLAUDE.md`** (Track 1b) | Two files — a 4-line project card and a full contract; rules 15 / 1c / 13 / 14 hardcoded |
| [3](#3-track-2--ai-inputoutput-security) | **Track 2** — I/O security | **Reject `guardrails-ai`.** Native `ai/guard.py`, pure functions, house pattern from `ai/safety.py` |
| [4](#4-track-3--multi-model-gateway--semantic-caching) | **Track 3** — gateway + cache | **Reject Portkey. Reject LiteLLM too (with reasons).** Extend the seam that already exists in `client.py`; exact-match cache first, semantic second |
| [5](#5-track-4--llm-observability--tracing) | **Track 4** — observability | **Reject LangSmith (data egress).** OTel-shaped spans into a Postgres `ai_traces` table; Phoenix as an opt-in dev sink |
| [6](#6-track-5--automated-ai-evaluations) | **Track 5** — evals | **Reject RAGAS and DeepEval as deps; adopt their metric definitions.** Split deterministic (gates) from judged (scored) |
| [7](#7-track-6--the-document-extraction-gap-trail-files) | **Track 6** — Trail Files | **Trial run done, root cause found.** Three documents, three lanes, one QR between them. The model reads all three well — the execution form took **398.8 s against a UI that promises 60 s**, and the consumption paper loses **19/30 tank numbers, 14/30 names, 8/30 products** because ditto marks come back as `""` and the resolver only knows glyphs |
| [8](#8-sequencing-cost-and-risk) | Sequencing | 6 slices, ordered so the cheapest diagnosis lands first |
| [9](#9-clarifying-questions-i-need-answered) | **Questions** | 16 questions; 5 are blocking. **Q2 (the 0.85 gate vs ruling P10-7) and Q15 (amending the ditto rule) are the two that change the shape of the work.** |

---

## 1. The CI bug

### 1.1 The reported symptom is a red herring, and I can prove it

The screenshot shows this as the CI error annotation:

```
❌ Send failed #2 (attempt 3/3) — giving up: RuntimeError: simulated sender offline
```

**That line is the WhatsApp retry test passing.** `check_whatsapp_auto_retry`
([legacy/bug_check.py:1897](legacy/bug_check.py:1897)) deliberately replaces
`whatsapp_worker._send_whatsapp` with a function that raises
`RuntimeError("simulated sender offline")`, then calls `process_queue()` three
times and asserts the row goes `pending → pending → failed` at
`MAX_SEND_ATTEMPTS = 3`. The `❌ … giving up` print is
[whatsapp_worker.py:384](legacy/whatsapp_worker.py:384) — the worker's normal
terminal-failure branch — and seeing it is **the proof the cap works**.

It reaches the CI run page as an `Error:` only because of the workflow's own
reporting shim:

```yaml
grep -E "❌" bugcheck_ci.log | head -6 \
  | while IFS= read -r line; do echo "::error title=bug_check::${line}"; done
```
— [.github/workflows/postgres-dual-ci.yml:83](.github/workflows/postgres-dual-ci.yml:83)

`run_check` only prints its own `❌` glyph when `VERBOSE` is on, and it is off
in CI. So the grep matched **nothing from the harness and everything from the
application log**, and the annotation named an innocent line. The `tail -n 4`
annotations underneath it are the same mistake: `Aggregated 2 area rows…` and
`▶ 598 passed, 1 failed` are not errors either.

**The mock is not flaking. Nothing about WhatsApp is wrong.**

### 1.2 The real failure — and it is the one that has failed 30+ times

I downloaded the artifact from run
[33632154249](https://github.com/johnthebasemaker/GI_Hub_Project/actions/runs/33632154249)
(`gh run download … -n bugcheck-ci-output`). `legacy/BUG_REPORT.md` names it:

```
## ❌ Failures (1)

### QR Badges · encode → decode roundtrip preserves ID_Number
- **Error:** `TypeError: 'NoneType' object does not support the context manager protocol`
- **Hint:**  ai/cv/qr.py — requires libzbar (pyzbar). Skipped if missing.
```

This is the failure `docs/PROJECT_STATUS.md` records as *"cause is
Linux-runner-specific and unresolved"* after **30/30 runner failures since
2026-07-07**. The `::error::` shim added on 2026-07-26 to name the culprit
never did, because of §1.1 — it annotated the WhatsApp line instead.

### 1.3 Root cause — `bug_check.py` line 46 breaks `ctypes` on Linux only

Two lines at the top of the harness:

```python
# Stop mailer.py from actually launching Mail.app / xdg-open / Outlook
_orig_popen = subprocess.Popen
subprocess.Popen = lambda *a, **kw: None   # ← line 46
```
— [legacy/bug_check.py:45](legacy/bug_check.py:45), restored only at
[line 10813](legacy/bug_check.py:10813), i.e. **after every check has run**.

The chain, end to end:

1. `check_qr_decode_roundtrip` imports `pyzbar.pyzbar`
   ([bug_check.py:4774](legacy/bug_check.py:4774)).
2. `pyzbar/wrapper.py` calls `zbar_library.load()` at import time, which on any
   non-Windows platform calls `ctypes.util.find_library('zbar')`.
3. On **Linux**, `find_library` is the `else` branch of `ctypes/util.py`, whose
   first step is `_findSoname_ldconfig` →
   `with subprocess.Popen(['/sbin/ldconfig', '-p'], …) as p:`.
4. `subprocess.Popen` is the harness's `lambda: None`, so that is `with None:`
   → **`TypeError: 'NoneType' object does not support the context manager
   protocol`**, and `_findSoname_ldconfig`'s `except OSError` does not catch a
   `TypeError`.
5. `check_qr_decode_roundtrip` guards its optional dependency with
   **`except ImportError` only**, so what should have been "libzbar missing,
   skip" becomes a hard `FAIL`.

Verified locally, exactly:

```
$ .venv/bin/python -c "import subprocess; subprocess.Popen = lambda *a, **kw: None
> with subprocess.Popen(['/bin/echo','x']) as p: pass"
TypeError: 'NoneType' object does not support the context manager protocol
```

**Why it never reproduced on macOS.** On darwin, `find_library` is a completely
different function — it probes `dyld` directly and **never calls
`subprocess`** — so the neutered `Popen` is never touched. Every "simulated CI
condition" listed in `PROJECT_STATUS.md` (clean tree, latest deps, Linux
package set, UTC, case-sensitive FS) was simulated *on macOS*, and this is the
one difference that cannot be simulated there.

### 1.4 The second bug, which is worse, and which nobody has noticed

**The check has never run anywhere.** On this Mac:

```
$ ls /opt/homebrew/lib/libzbar*
/opt/homebrew/lib/libzbar.0.dylib   libzbar.a   libzbar.dylib     ← installed
$ .venv/bin/python -c "from ctypes.util import find_library; print(find_library('zbar'))"
None                                                              ← not found
```

Homebrew's `/opt/homebrew/lib` is not on dyld's default search path, so
`find_library` returns `None`, `zbar_library.load()` raises
`ImportError('Unable to find zbar shared library')` — and
`check_qr_decode_roundtrip` **catches it and returns**. The local report says
`QR Badges — 2/2`; the roundtrip assertion inside it has never executed. So:

| | macOS | Linux runner |
|---|---|---|
| exception raised | `ImportError` | `TypeError` |
| harness behaviour | **silent skip, counted as PASS** | **hard FAIL** |
| assertion actually run | ❌ no | ❌ no |

A skip that reports itself as a pass is a gate that lies. This is the same
class of defect as the `az-revert` / canary problems the SME and AI-eval work
already learned about, and it deserves the same treatment: **a skip must be a
third status, counted and printed, never folded into the pass total.**

### 1.5 Proposed fix — four parts, all small

| # | Change | Why |
|---|---|---|
| **F1** | **Import `pyzbar` once at the top of `bug_check.py`, BEFORE the `Popen` patch**, and stash the result. Neutering `Popen` afterwards is then harmless. | Makes the check actually *run* on Linux (CI installs `libzbar0`), which is the whole point of having it. |
| **F2** | Replace `except ImportError` with `except Exception`, and record the skip as a **`SKIP` status** with its reason, printed in the summary line (`598 passed, 0 failed, 1 skipped`) and listed in `BUG_REPORT.md`. | A guard that catches one exception type fails on the *shape* of the failure rather than the thing under test. And a silent skip must stop counting as a pass. |
| **F3** | **Narrow the `Popen` neutering.** Patch it where the risk is (`mailer`), not process-wide — e.g. `patch.object(mailer, "subprocess")` or a module-scoped context manager. Line 46's comment already says its purpose is "stop mailer.py from launching Mail.app". | A global monkeypatch of a stdlib primitive for the whole run is an unbounded blast radius; it took out `ctypes` and could take out anything else that shells out. |
| **F4** | **Fix the CI annotation shim** ([postgres-dual-ci.yml:83](.github/workflows/postgres-dual-ci.yml:83)): parse `BUG_REPORT.md`'s `### ` failure headings instead of grepping stdout for `❌`, and drop the `tail -n 4` annotations. | Application logs legitimately print `❌`. The report file already contains exactly the right list; grepping stdout for a glyph is how the cause stayed hidden for 30 runs. |

**Also worth doing (F5):** raise `_format_tb`'s `limit=3`
([bug_check.py:104](legacy/bug_check.py:104)) to ~8 for FAIL rows. The
traceback in the artifact stopped one frame above the raising line, which is
why the report said `pyzbar.py, line 7` and not `ctypes/util.py`.

**Expected result:** dual-CI goes green for the first time since 2026-07-07,
and `QR Badges` becomes `2/2` for real on Linux. On macOS it will report
`1 passed, 1 skipped (libzbar not on dyld's path)` unless the operator sets
`DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib` — see **Q3**.

### 1.6 The skill Track 1 asks for — `check-offline-mocks`

The brief asks for "a custom Claude skill/bash script that automatically checks
for offline mock queues to prevent the CI error above." The CI error was not
actually a mock-queue problem — but the *class* of defect it exposed is real
and worth a gate, so I propose the skill hunt for that class instead of the
symptom:

`.claude/skills/gi-ci-preflight/` + `bin/ci_preflight.sh`, which fails loudly on:

1. **Process-wide monkeypatches of stdlib primitives** that are not restored
   before the checks run (`subprocess.Popen`, `platform.system`, `os.system`,
   `socket.socket`, `time.sleep` assigned at module scope in a test harness).
   This is exactly the F3 defect, and it is greppable.
2. **Optional-dependency guards that catch a single exception type**
   (`except ImportError:` immediately following an `import` inside a `check_*`
   function) — the F2 defect.
3. **Checks that can silently no-op**: a `check_*` body whose only exit on the
   guard path is a bare `return`, with no SKIP recorded.
4. **Queue-style mocks left installed**: a `_send_*`/`_dispatch*` symbol
   reassigned without a `try/finally` restoring the original — the thing the
   brief actually asked about, and a real risk in a 10,000-line harness.
5. **Platform-forked stdlib calls** used from a harness that fakes the
   platform (`platform.system = lambda: "Linux"` at
   [bug_check.py:47](legacy/bug_check.py:47) while `sys.platform` still says
   `darwin` — a divergence that will bite something else eventually).

It runs in ~1 s, needs no services, and gets wired as the first step of
`postgres-dual-ci.yml` so it fails *before* the 34-second suite does.

---

## 2. `CLAUDE.md`

Two files, because they have different jobs and different readers.

### 2.1 `CLAUDE.md` (project root) — the Karpathy 4-line card

Karpathy's point is that the file people actually keep current is the one short
enough to read at a glance; everything else belongs behind a pointer. Proposed
content (this is the literal draft, not a description of one):

```markdown
# GI Hub 3.0

1. Two stacks, one Postgres: React+FastAPI in `frontend/`+`backend/` (live), frozen Streamlit in `legacy/`. `REPO_MAP.md` is the segregation contract.
2. Read `PROJECT_HANDOVER.md` before changing anything — 15 LOCKED rules, each one a bug whose symptom appeared far from its cause. Rules 15, 1c, 13, 14 and 9 are restated in `.claude/RULES.md` and are not negotiable.
3. Gates, all of which must be green before you say "done": `.venv/bin/python -m backend.api.service_tests` · `npm run parity:sme --prefix frontend` · `npm run test:nav --prefix frontend` · `.venv/bin/python -m tests.ai_eval.runner` · `.venv/bin/python legacy/bug_check.py` · `cd tests/e2e && npm test`.
4. Definition of Done includes `MANUAL_TESTING_GUIDE.md` and `USER_MANUAL.md` (rule 13) — a feature change that does not update them is not finished.
```

### 2.2 `~/.claude/CLAUDE.md` — the full contract

The brief asks for "another full filled claude.md for my entire Claude Code".
⚠️ **This is where I need a decision (Q1).** A *user-level* `~/.claude/CLAUDE.md`
loads into **every** session on this machine, including ones that have nothing
to do with GI Hub. Putting GI Hub's locked rules there means every future
project inherits "never pool by Material_Code alone", which is noise at best
and misleading at worst.

My recommendation: **two files, both in the repo**, and nothing user-level:

* `CLAUDE.md` — the 4-line card above (loads automatically, always).
* `.claude/RULES.md` — the full contract (~250 lines), loaded on demand and
  referenced by line 2 of the card.

If you want a genuinely global one, it should contain only machine-level facts
(the venv path, `bin/dev.sh` / `bin/power.sh`, the `:5433` Postgres, the Ollama
model set, "never run dev servers with Bash") — I have drafted that separately
and it is **Q1** whether you want it.

### 2.3 The four rules to hardcode, as they will appear

These are written as *instructions to an agent*, not as prose:

> **Rule 15 — the tests do not open the live database.**
> `backend/api/testdb.py` rewrites `DATABASE_URL` **before `backend.api.db` is
> imported**; that import ordering is the entire mechanism. Never `import
> backend.api.db` (directly or transitively) above the `testdb` call in a test
> entrypoint. Never "fix" a failing suite by pointing it at `gihub`. If you
> want a test's rows afterwards, they are in `gihub_svctest`, and that is
> correct. `GI_TEST_DB=off` exists for debugging only and prints a warning.
> **Corollary (rule 15's second half): a constraint added in an Alembic
> migration must be added to `models.py` in the same commit** — the cutover
> builds production with `create_all`, so a migration-only index is absent
> from every production box.

> **Rule 1c — the SME subset rule.**
> `Ordered_Qty` is the TOTAL procured; `Available_Qty` is the part that has
> ARRIVED, and is a **subset** of it. Therefore:
> `tier1 = available` · `tier2 = max(ordered − available, 0)` ·
> `ceiling = max(available, ordered)` · `to_buy = max(demand − ceiling, 0)`.
> Never `available + ordered`. And **both engines change together**:
> `backend/api/sme_engine.py` and `frontend/src/sme/engine.ts` are line-for-line
> mirrors; any numeric change edits BOTH and regenerates the golden **in one
> commit**, proven by `npm run parity:sme`.

> **Rule 13 — the docs are part of the Definition of Done.**
> A feature change updates `MANUAL_TESTING_GUIDE.md` in the same PR. A role
> added to `auth.ROLE_META` is added in the same commit to
> `ai/manual_qa._ROLE_ALLOWED` **and** `build_manual_pdf.ROLE_MANUAL_RECIPES`.
> The role map falls back to `store_keeper` for an unknown role — it fails safe,
> which is exactly why an omission is invisible until somebody is answered from
> the wrong chapter.

> **Rule 14 — navigation access is a role matrix and it fails closed.**
> A page names the jobs that need it (`anyRole`), never a `minLevel` seniority
> ladder. `canAccessPath` refuses any path it does not recognise.
> `npm run test:nav` fails the build when a route has no rule. **Narrowing a
> page means narrowing its endpoints in the same commit — a menu is not a
> control.**

Plus, because Track 2 depends on it, **rule 9** verbatim:

> **Rule 9 — the assistant retrieves, and the fence is upstream of the score.**
> `manual_qa.allowed_sections(role)` filters chapters **before** BM25 scores
> them, so a role's prompt cannot physically contain a chapter it may not see.
> That is the security boundary — not the system prompt. Never move the filter
> downstream, never implement a "topic filter" that becomes a second, weaker
> boundary, and never parse manual chapters with a bare `^# \d+\.` match (use
> `ai/manual_index.iter_chapters`, which is fence-aware).

### 2.4 The bash commands the file will pin

```bash
# Backend service tests — 2,188 checks, suites A…CS, own throwaway DB (rule 15)
GI_DOTENV=0 DATABASE_URL=postgresql+psycopg2://postgres@127.0.0.1:5433/gihub \
JWT_SECRET=ci-only-service-test-secret-key-32bytes-min \
.venv/bin/python -u -m backend.api.service_tests
```
```bash
# Headless E2E — 125 specs, ~55 s, builds and destroys its own gihub_e2e_pw stack
cd tests/e2e && npm test
```
```bash
# SME math — the dual-engine parity oracle (1,313 comparisons) and the UI math
npm run parity:sme --prefix frontend && npm run test:ui-math --prefix frontend
```
```bash
# Route-to-endpoint alignment (rule 14) — 50 routes, every one must be claimed
npm run test:nav --prefix frontend
```
```bash
# AI guardrail audit — Tier 1 is the hard gate; Tier 2 is a scored artefact
.venv/bin/python -m tests.ai_eval.runner
.venv/bin/python -m tests.ai_eval.runner --tier2 --json scorecard.json
```
```bash
# Frozen Streamlit regression suite — self-rooted, 599 checks
.venv/bin/python legacy/bug_check.py
```
```bash
# The Phase 11 addition — offline-mock / harness-hygiene preflight (§1.6)
bash bin/ci_preflight.sh
```

---

## 3. Track 2 — AI Input/Output Security

### 3.1 Verdict on `guardrails-ai`: **reject as a dependency**

| Criterion | Finding |
|---|---|
| **Dependency mass** | `guardrails-ai` pulls `litellm`, `opentelemetry-*`, `guardrails-api-client`, `rich`, `pydash`, `langchain-core` and more — a larger tree than the module it would guard (`manual_qa.py` is 417 lines). |
| **Runtime network** | The useful validators do not ship in the package. `guardrails hub install hub://guardrails/detect_jailbreak` **downloads at install time** from `hub.guardrailsai.com`, and the jailbreak validator is a transformer model of a few hundred MB. That is a second model resident beside the one warm 7-8B model the CPX42 ruling allows. |
| **Telemetry** | The CLI configures anonymous metrics; disabling it is a step somebody has to remember on every box. For a system whose whole posture is local-first, "off by default" beats "off if configured". |
| **Fit** | Its core abstraction (Pydantic-typed LLM output with re-ask loops) is aimed at getting *structured* output from a chat model. Our structured lanes already do this by hand and better — `ocr.extract_json_object` + `salvage_truncated_json` + per-lane `num_predict` budgets exist precisely because a re-ask on a 460-second vision read is not affordable. |
| **The decisive one** | **The real boundary is already built and is not a guardrail.** Rule 9's retrieval fence means a Store Keeper's prompt *physically cannot contain* an Admin chapter. Guardrails-AI operates on text after the prompt is assembled. Bolting a text-level topic filter in front of that risks becoming a second, weaker boundary that people then trust *instead* of the fence. |

### 3.2 What I propose instead — `backend/api/ai/guard.py`

Same shape as the module the codebase already trusts for this job,
`ai/safety.py`: **pessimistic pure functions, no I/O, trivially unit-testable,
one call site.** ~250 lines, zero new dependencies.

#### Input guard — three layers, in this order

```
question ──▶ [1] shape/abuse checks  ──▶ [2] jailbreak scanner ──▶ [3] role-topic
             (deterministic, cheap)      (deterministic, scored)     pre-flight
                                                                        │
                                                                        ▼
                                                          manual_qa.retrieve_context()
                                                          ⚠️ THE FENCE — unchanged
```

1. **Shape checks** — length cap, control characters, an upper bound on
   repeated tokens (the classic context-flood), and **an explicit refusal of
   base64/hex blobs over N chars**, which is how "encode your system prompt"
   attacks arrive.
2. **Jailbreak scanner** — a curated, versioned pattern set with **scores, not
   a boolean**: instruction-override ("ignore previous", "you are now",
   "developer mode"), role-elevation ("as an admin", "for audit purposes,
   show me"), prompt-extraction ("repeat everything above", "what is your
   system prompt"), delimiter injection (`</context>`, `### SYSTEM`),
   and encoding tricks. Two thresholds: **warn** (traced, answered normally)
   and **refuse** (traced, refused with the role's own refusal sentence).
   Both thresholds are data in a YAML file, versioned, so the eval suite can
   pin them.
3. **Role-aware topic pre-flight, and this is the delicate one.** ⚠️ **It must
   be a UX affordance, not a security control.** The fence in
   `allowed_sections()` stays exactly where it is. What this adds is: when
   retrieval returns nothing *and* the question's terms match a chapter the
   role may not see, answer with the role's refusal sentence **without calling
   the model at all**. That saves a full prompt-eval on every out-of-scope
   question and — more importantly — stops the model being handed a question
   about a subject its context deliberately excludes, which is the exact
   condition under which an LLM confabulates. It cannot *widen* anything,
   because it only ever refuses.

> **The rule this must not break:** a question that scores nothing must not
> become a refusal when the answer *is* in an allowed chapter and BM25 simply
> missed it. Today that case falls back to `_context_for_role()`. The
> pre-flight fires **only when the terms positively match a forbidden
> chapter**, never on a bare retrieval miss.

#### Output guard — three checks, all deterministic

1. **Formula defusal.** ⚠️ This one is not hypothetical: **rule 12 already
   documents this exact attack** for exported cells, and `reports._defuse`
   already knows the six characters a spreadsheet evaluates (`=` `+` `-` `@`
   TAB CR). The assistant's answer is copy-pasted into spreadsheets by HODs.
   The guard reuses `_defuse`'s character set — **one implementation, not two**
   — and blocks (rather than escapes) a leading formula character in a
   streamed answer, because an escaped `'=` in chat prose reads as a typo.
2. **PII / sensitive-data filter.** Scoped deliberately narrowly: E.164 phone
   numbers, Saudi Iqama/ID number shapes, email addresses, JWT-shaped strings,
   anything matching the `deploy/.env` key names, and bcrypt/`$2b$` prefixes.
   ⚠️ **A hit here is a bug alarm, not a formatting choice** — the manual
   corpus contains none of these, so a phone number in an answer means either
   the model hallucinated one or something reached the context that should not
   have. It is redacted **and** the trace row is flagged for review.
3. **Cross-role leak canary.** The Tier-1 eval already maintains canary strings
   unique to each chapter. The output guard reuses that same table at runtime:
   if a canary from a chapter outside `allowed_sections(role)` appears in a
   streamed answer, the stream is cut and the incident is recorded. This is
   cheap (a substring scan over a few hundred phrases) and it closes the gap
   between "we test for leaks nightly" and "we notice one in production".

#### Streaming — the constraint nobody's blueprint mentions

`/ai/assistant` is **SSE, token by token** ([router.py:86](backend/api/ai/router.py:86)).
An output guard that needs the whole answer cannot run before the first token
leaves. The design:

* **Formula defusal and canary scanning run on a sliding buffer** — hold back
  the last N characters (N = longest canary), scan, emit. Costs one buffer's
  latency, not the whole answer's.
* **PII redaction runs on line boundaries.**
* **A refusal mid-stream emits an SSE `guard` event and closes**, so the client
  can replace the partial answer rather than leave half of one on screen.

### 3.3 Where the LLM-as-a-judge goes — and where it does not

The brief offers "strict local Pydantic validators + LLM-as-a-judge" as the
alternative to `guardrails-ai`. I want the Pydantic half and **not** the judge
half, in the request path:

⚠️ **A judge in the request path is incompatible with the one-warm-model
ruling.** Judging a Hub Assistant answer means a second generation. On the same
model that doubles the user's wait; on a different model it forces a
cold-swap of a 6 GB VLM/LLM on a box the operator has ruled holds one. And a
stochastic judge that can *refuse* means the same question is answered on
Monday and denied on Tuesday — which is P10-7's argument, applied to
production instead of CI.

**The judge belongs in Tier 2 of the eval suite** (§6), run on a schedule
against a fixed dataset, where its stochasticity is measured rather than
inflicted.

### 3.4 What ships in Track 2

| Artefact | Notes |
|---|---|
| `backend/api/ai/guard.py` | Pure functions, ~250 lines, zero deps |
| `backend/api/ai/guard_patterns.yaml` | Versioned pattern set + thresholds (data, so evals can pin it) |
| Wiring | `router.assistant` (SSE), `router.data_query`, `router.nl_search`, `router.insights`, `router.eod_summary` |
| Suite `CT` in `service_tests` | ~40 deterministic checks, incl. negative controls (a legitimate question containing "ignore" must not refuse) |
| New Tier-1 eval cases | Jailbreak corpus becomes part of the 100+ dataset (§6.4) |
| Trace fields | Every guard decision is a span attribute (§5), so "why was I refused" is answerable |

---

## 4. Track 3 — Multi-Model Gateway & Semantic Caching

### 4.1 Verdict on `portkey-ai`: **reject**

Portkey's open-source gateway is a **Node.js service**. The value proposition —
unified analytics, prompt management, guardrails, virtual keys — lives in a
control plane that is either their cloud (proprietary data egress: our prompts
contain manual chapters and stock rows) or a self-hosted deployment that is a
second application to run, patch and back up.

That collides directly with **ruling P10-1**, whose reasoning I am applying
rather than re-litigating: *"Redis is a new daemon and a new 3 a.m. failure
mode."* A Node gateway in front of every LLM call is a strictly bigger version
of the same objection, and it sits **in the request path**, so when it is down
the assistant is down.

### 4.2 Verdict on `LiteLLM`: **reject for the Ollama path; consider for the cloud leg only**

This one is closer, and I want to be honest about why I am still saying no.

**In LiteLLM's favour:** the Python SDK can be used **in-process** (no proxy
daemon), it speaks Ollama and Anthropic, and `litellm.acompletion(...,
fallbacks=[...], num_retries=N)` is exactly the feature Track 3 asks for. It is
a real, well-maintained option and I would not argue against it in a greenfield
system.

**Against, and the reasons are specific to this codebase:**

1. ⚠️ **It would abstract away the vision envelope, which is three numbers that
   are ONE decision** (ARCHITECTURE §7a). `client.vision_num_ctx()` computes
   `num_ctx` from a measured image-token estimate, because getting it wrong
   does not truncate — it **aborts the Ollama runner** (`ggml_abort`, SIGABRT,
   empty body, no error field) and takes every other queued job with it.
   LiteLLM's Ollama adapter passes its own options dict. Handing that decision
   to a library's defaults would re-open a bug that cost a whole phase to find,
   and it would re-open it *silently*.
2. **The seam it provides already exists.** `client.vision_provider()` /
   `GI_AI_VISION_PROVIDER=anthropic` is a deliberate, documented runtime switch
   ("a seam that has never been exercised is a plan, not a seam"). What is
   missing is not a gateway — it is **the same seam for the chat lane** and a
   fallback *policy*.
3. **Retry semantics here are not generic.** The codebase has already learned
   that **a timeout is not an outage** ([client.py:250](backend/api/ai/client.py:250)):
   a `ReadTimeout` on a vision read means the model is *still generating*, and
   retrying it doubles the load on a box that holds one model. A generic
   `num_retries=3` would do precisely the wrong thing on the slowest, most
   important lane. Our retry policy must be **per-error-class and per-lane**,
   which is ~40 lines of our own code and a config table.
4. **Dependency mass on a 16 GB box**: LiteLLM's install pulls a large tree
   (including its own `openai`, `tokenizers`, `jinja2` stack) into a venv that
   also holds torch-adjacent OCR deps.

**The middle path, if you want it (Q6):** use the LiteLLM SDK **only inside the
cloud-fallback branch** — the code path that today hand-rolls an Anthropic
Messages request in `client.vision_json`. That gets provider-agnostic cloud
fallback (Anthropic → OpenAI → Bedrock) for free, while the Ollama path and the
`num_ctx` maths stay hand-written. I can implement it either way.

### 4.3 What I propose instead — promote `client.py` into `ai/route.py`

A ~200-line **in-process** router. No daemon, no proxy, no new port.

```
                       ┌─────────────────── ai/route.py ───────────────────┐
call(lane, …) ────────▶│ 1. lane policy   (model, num_predict, timeout,    │
                       │                   num_ctx rule, retry class map)  │
                       │ 2. cache lookup  (§4.4 — keyed by role!)          │
                       │ 3. attempt 1     → primary   (Ollama, warm)       │
                       │ 4. classify error                                 │
                       │      connect/5xx/429 → retry w/ jittered backoff  │
                       │      read-timeout    → NO RETRY, fail with the    │
                       │                        budget sentence            │
                       │      model-not-pulled→ straight to fallback        │
                       │ 5. fallback chain (cloud, if configured + allowed)│
                       │ 6. record: provider, model, ms, tokens, outcome   │
                       └───────────────────────────────────────────────────┘
```

Design points that matter:

* **Lanes, not models.** The policy table is keyed by the lane names that
  already exist (`assistant`, `ocr_consumption`, `ocr_delivery_note`,
  `ocr_purchase_doc`, `tool_identify`, `insights`, `eod`, `nl_search`,
  `ocr_consumption_form`), because `NUM_PREDICT` and the timeout are already
  per-lane and were already a bug when they were not.
* ⚠️ **The fallback chain is a policy decision, not a default.** Routing to
  Anthropic when Ollama crashes means **proprietary company data leaves the
  network**. For the vision lane the operator has already accepted that
  (ruling Q7, opt-in, off by default). For the **assistant** lane the context
  is manual chapters (lower sensitivity); for **`/ai/insights`,
  `/ai/eod-summary` and `/ai/nl-search`** the context is **live stock rows and
  generated SQL over the ERP**. I propose the chain be configurable
  **per lane** and default to **local-only for every data lane** — see **Q5**,
  which is blocking.
* **Fail-open vs fail-closed follows P10-2's shape**: routing failures on an
  *informational* lane degrade to a clear message; routing failures on a lane
  that would post data do not silently substitute a weaker model.
* **`vision_num_ctx` stays exactly where it is** and the router calls it. It is
  not abstracted, wrapped or defaulted.

### 4.4 Semantic caching — the honest version

**The single most important thing about this feature is that it is a fence
bypass waiting to happen.**

> ⚠️ **A cache key that omits the role serves an Admin's answer to a Store
> Keeper.** Rule 9's whole guarantee is that a role's *context* differs. Two
> people can ask a byte-identical question and be entitled to different
> answers. The key **must** be
> `(normalised_question, role, manual_content_hash, prompt_version, guard_patterns_version)`,
> and for the data lanes it must also include the **site scope from the JWT**.
> I would like this written into `PROJECT_HANDOVER.md` as a Phase 11 locked
> ruling before a line of cache code is written.

Given that, I propose **two stages, and stage 2 only if stage 1's numbers
justify it** (rule 11: indexes — and by extension caches — are benchmarked
before they are added):

**Stage 1 — exact-match cache, Postgres, ~60 lines.**
Normalise (lowercase, collapse whitespace, strip terminal punctuation), hash
the composite key, store `(key_hash, role, answer, model, created_at,
hit_count)` in an `ai_answer_cache` table with a TTL. Zero new dependencies,
zero new services, consistent with P10-1. It catches the real repetitive case —
the same twenty questions asked by twelve HODs — and it makes the hit rate
**measurable**, which is what tells us whether stage 2 is worth anything.

**Stage 2 — semantic cache, only if stage 1's near-miss rate is material.**
Everything needed is already on the box: **`nomic-embed-text` is already pulled
in Ollama** (confirmed on this machine today). Embed the normalised question
(137M params, milliseconds), store the vector alongside the row, and accept a
hit above a cosine threshold **within the same key partition** (same role, same
manual hash — never across).

* Storage: `pgvector` if the operator will install the extension, otherwise a
  plain `float4[]` column with brute-force cosine in SQL. At the volumes here
  (hundreds of cached questions, not millions) brute force is genuinely fine
  and needs nothing installed — the same argument that justified BM25 over a
  vector store for retrieval.
* ⚠️ **The threshold is a correctness knob, not a performance knob.** "What can
  a supervisor approve?" and "What can a supervisor **not** approve?" are
  ~0.95 cosine apart and have opposite answers. I propose the threshold be
  **validated by an eval case set** (a curated list of near-miss pairs that
  must NOT hit) before it is enabled, and that it start high (≥0.97).
* **Never cache**: anything from the `/ai/query`, `/ai/nl-search`,
  `/ai/insights` or `/ai/eod-summary` lanes. Those answer from **live stock**,
  and a cached "you have 40 drums" is a wrong number with a timestamp on it.
  Cache the *manual assistant* only.

---

## 5. Track 4 — LLM Observability & Tracing

### 5.1 Verdict on LangSmith: **reject — this is the clearest call in the document**

1. **Data privacy, disqualifying.** A LangSmith trace stores the prompt. Our
   prompts contain: manual chapters (internal ops documentation), the results
   of SQL probes over the live ERP (`/ai/insights` runs 5 SQL probes;
   `/ai/eod-summary` summarises the day's real movements), generated SQL, and
   OCR'd images of signed delivery notes and consumption sheets carrying
   employee names. Sending that to a third-party SaaS is a decision far above
   an observability upgrade, and it contradicts the entire local-first posture
   (`gi_ai_ro`, the `FORBIDDEN_TABLES` wall, PDF-only board briefs).
2. **No LangChain here.** LangSmith's ergonomics assume LangChain/LangGraph
   objects. This codebase has none, deliberately (BM25 over 390 chunks, "no
   vector store and no new dependency"). We would be writing manual `@traceable`
   decorators anyway — at which point we are writing spans, and the only
   question is where they go.
3. Self-hosted LangSmith is an enterprise-licensed, multi-container deployment.

### 5.2 What I propose — OTel-shaped spans into Postgres

**Slice A — the span vocabulary and a native sink (ships in Phase 11).**

The pipeline Track 4 names — `User Query → BM25 Scoring → Chunking → Prompt →
LLM Output` — becomes one trace with these spans:

| Span | Attributes recorded |
|---|---|
| `ai.request` | lane, role, username hash, site scope, request id, total ms, outcome |
| `ai.guard.input` | pattern hits, score, decision (allow/warn/refuse), patterns version |
| `ai.retrieve` | **allowed chapter ids**, candidate chunk count, **top-k chunk ids + BM25 scores**, alias expansions applied, whether the **fallback path** fired, context bytes |
| `ai.cache` | key hash, hit/miss, similarity (stage 2), age |
| `ai.generate` | provider, model, num_ctx, num_predict, prompt tokens, completion tokens, queue-wait ms (the semaphore!), generate ms, retries, fallback used |
| `ai.guard.output` | redactions, canary hits, defusals, truncation |

> ⚠️ **The thing that is actually missing today is retrieval telemetry.** When
> the assistant answers badly, nobody can currently tell whether BM25 retrieved
> the wrong chunks or the model ignored the right ones. `Index.search` already
> *computes* the scores; it just throws them away. Recording them is the single
> highest-value line of this whole track, and it costs almost nothing.

**Sink: a Postgres `ai_traces` table**, one row per span, JSONB attributes,
with a retention job. Consistent with P10-1 (Postgres, not a new daemon),
already backed up, already in the runbook, and queryable next to
`system_audit_log` and `ai_jobs` — which is where an incident investigation
starts anyway.

⚠️ **Four workers.** Per the standing warning in `SESSION_HANDOVER.md` — *"if
you add anything that runs on a timer or holds state in memory, assume four of
it"* — the trace writer must be **fire-and-forget with a bounded queue per
worker**, never a synchronous insert in the request path, and the retention
sweep must take the `daily_job_runs` claim (P10-2's fail-closed side).

**Slice B — an opt-in developer sink (not the production path).**
Emit the same spans through `opentelemetry-sdk`'s OTLP exporter behind
`GI_OTEL_ENDPOINT`. When set, a developer can run **Arize Phoenix** locally
(`arize-phoenix`, Apache-2.0, self-hosted, OTel-native, RAG-aware UI) and get
the waterfall view without any of it being in production's dependency set or
request path. **Phoenix is never a runtime dependency and never a service on
the Hetzner box** — it is a local debugging tool, opt-in, off by default.

**Slice C — an in-app surface.**
An `AI Traces` tab in the existing Admin Console: last N requests, per-stage
latency, retrieval scores, guard decisions, cache hit rate, provider mix. Admin
+ auditor read-only (rule 7: it is a `GET`, so the auditor gets it for free).
This is where the "why did the assistant say that?" question gets answered by
somebody who is not at a terminal.

### 5.3 Cost

Roughly: one new table + migration, ~150 lines of span plumbing, ~120 lines of
console UI, one retention job. No new services, no egress, no new production
dependency.

---

## 6. Track 5 — Automated AI Evaluations

### 6.1 Verdict on RAGAS and DeepEval: **adopt the metric definitions, reject the packages**

| | RAGAS | DeepEval |
|---|---|---|
| **Judge** | Needs an LLM + an embedding model; defaults to OpenAI | Needs an LLM; defaults to OpenAI |
| **Local models** | Possible via LangChain wrappers — a version-fragile surface | Possible via `DeepEvalBaseLLM` subclass |
| **Cloud** | — | Confident AI integration; uploads when configured |
| **Deps** | LangChain-flavoured tree | pytest + its own tree |
| **What we'd actually use** | The *definitions* of Faithfulness, Answer Relevancy, Context Precision/Recall | The same, plus a pytest runner we don't need |

The decisive argument: **we must supply our own local judge either way**
(proprietary data), so what the libraries are really selling is a set of judge
**prompt templates** and a scoring convention. Those are ~200 lines, and
vendoring them means they are **versioned in our repo** — which matters, because
a metric whose prompt silently changes on a `pip upgrade` invalidates every
historical score.

Against that: `tests/ai_eval/runner.py` **already has the parts neither library
has**, and they are the parts that have actually caught things —
the Tier 1/Tier 2 split, canary-integrity verification (a canary that drifts
into an allowed chapter is reported as a *broken suite*, not a pass), and the
`policy.yaml` allowlist pin that caught a **silent policy widening no
structural check could see** (negative control, 2026-09-02: granting a Store
Keeper the Admin and Ops chapters failed **zero** structural checks).

**So: extend `tests/ai_eval/`. Do not replace it.**

### 6.2 ⚠️ The collision with ruling P10-7, stated plainly

> **Track 5 asks:** *"If any core metric scores below 0.85, the pipeline should
> fail."*
> **P10-7 says:** *"Tier 1 AI evals gate a merge; Tier 2 never does… wiring
> [a stochastic metric] into CI produces a flaky gate, and a flaky gate is one
> people re-run rather than read."*

These cannot both be satisfied as written. I am **not** proposing to overturn
P10-7 — the reasoning behind it is sound and the current Tier 2 score (security
64% against a 95% target) would fail a 0.85 gate on day one, every day, until
somebody disabled it. That is the exact failure mode P10-7 predicts.

### 6.3 The resolution — split the metric families by determinism, not by name

This is the core proposal of Track 5, and it **increases** hard-gate coverage
rather than reducing it.

| Metric | Needs a judge? | Deterministic? | Phase 11 treatment |
|---|---|---|---|
| **Contextual Precision** | **No** | **Yes** | **HARD GATE ≥ 0.85** |
| **Contextual Recall** | **No** | **Yes** | **HARD GATE ≥ 0.85** |
| **Chapter-fence integrity** (leaks, canaries, policy pin) | No | Yes | **HARD GATE — zero tolerance** (exists today) |
| **Guard precision/recall** (jailbreak set, false-refusal set) | No | Yes | **HARD GATE ≥ 0.85** (new, Track 2) |
| Faithfulness / Groundedness | Yes | No | Tier 2 — scored artefact, trend-tracked |
| Answer Relevance | Yes | No | Tier 2 — scored artefact |
| Safety (answer-level) | Yes | No | Tier 2 — scored artefact |

**Why Contextual Precision/Recall can gate.** They measure *retrieval*, not
generation. Given a labelled case — "this question is answered by §16.3" —
whether §16.3's chunk appears in the retrieved context, and where it ranks, is
a **pure function of BM25 over a fixed corpus**. No model, no temperature, no
flake. `cases/groundedness.yaml` already carries an `expect_chapters_any` field;
this promotes it from a hint into a scored, gated metric at sub-section
granularity.

That is worth stating because it is the **most valuable gate in the whole
plan**: a retrieval regression is the failure mode this system is actually
prone to (the 800-char truncation that hid the access matrix from every
non-admin role was exactly this bug, and it survived a whole phase). A hard,
deterministic retrieval gate would have caught it on the commit that caused it.

**For Tier 2, instead of a pass/fail gate I propose a ratchet:** the score is
recorded per run; **a drop of more than X points below the trailing median
opens a bug-tracker row** (the Bug Tracking Engine already exists) and posts to
the Admin console. Nobody re-runs a trend. **Q9** asks whether you want the
ratchet to be advisory or to block a *release tag* (as opposed to a merge) —
that is a defensible middle position P10-7 does not forbid.

### 6.4 The 100+ case dataset

Today: 24 Tier-1 cases across `rbac_bypass`, `data_exfiltration`,
`groundedness`, `policy`. Target: **140 cases**, built from real material rather
than invented:

| Source | Count | Notes |
|---|---|---|
| **Chapter × role coverage grid** | ~55 | Every (role, allowed chapter) pair needs at least one question whose answer lives there. 9 roles × 24 chapters, sampled to cover each role's allowlist boundary. Generated as a *checklist*, answered by hand. |
| **Fence probes** | ~30 | For each role, a question whose answer lives in a chapter it may **not** see, with a canary. Extends the existing `rbac_bypass` set to all 9 roles (today it is thin outside `store_keeper`). |
| **Jailbreak corpus** (new, Track 2) | ~25 | Instruction-override, extraction, delimiter injection, encoding. Each with a **negative twin** — a legitimate question containing the same trigger word, which must NOT be refused. |
| **Near-miss cache pairs** (new, Track 3) | ~10 | Question pairs that are semantically close and have *opposite* answers. Used to validate the cache threshold (§4.4). |
| **Real questions** | ~20 | ⚠️ **Q11:** do we have a log of what people have actually asked? If `system_audit_log` or the assistant lane has retained questions, that is the highest-value 20 cases in the set, because invented questions test invented failure modes. |

**Provenance discipline** (borrowed from the canary-integrity idea that already
works): every case carries `source: grid | fence | jailbreak | cache | real` and
`added_in: phase-11`, and the loader **fails the suite** if a case's
`forbidden_substrings` no longer appears in exactly one chapter, or if the
chapter it names has been renumbered. The manual gains a chapter almost every
phase; a test suite that does not notice is a suite that passes forever.

### 6.5 Where it runs

* **Tier 1 + the new deterministic metrics** → inside `service_tests` (suite CQ
  extended, plus a new suite) → **every commit**, as today.
* **Tier 2 with the local judge** → `bin/` script + a scheduled workflow,
  writing `scorecard.json` and a trend row. ⚠️ Not on GitHub's runners: they have
  no GPU and no Ollama. It runs on the operator's box or the Hetzner host —
  **Q10**.

---

## 7. Track 6 — the document-extraction gap (Trail Files)

> *"Plan for the execution entries tab (supervisor and HOD portal), and SK
> portal consumption paper, delivery note paper details not getting, from pdf
> and image… Only loading not getting the results from that in the app."*

I probed all three files. **They are three different documents that belong to
three different lanes, and the reason they behave differently is structural.**
`Trail Files/` is not in git and I have not staged it.

### 7.1 What the three files actually are

| File | What it is | QR present? | Correct lane today |
|---|---|---|---|
| `Execution entry.pdf` | A **genuine GI Phase-9c printed form** — "DAILY CONSUMPTION - SURFACE SHIELD", LSC1/RLCB4, Site CNCEC, 5 material rows, filled digitally (typed, not handwritten), signed | ✅ **Yes** — decodes cleanly to `GIF1\|CNCEC\|LSC1\|\|32934F7226D44CBF` | `POST /execution/ocr/upload` → Execution Entries tab |
| `Consumption paper.jpeg` | A **handwritten PPE/consumables issue register** — "MPC3P1-CNCEC PROJECT / Daily - Consumption / Safety & Production Consumables". 30 ruled rows, columns S.No \| Name \| Tank No.# \| Product Name \| UOM \| QTY \| Remarks. Heavy **ditto marks**. Date box reads `25\|08\|26 (Night)` | ❌ **No** | `/entry/ocr` → `ocr_consumption` (**not** the execution lane) |
| `Delivery Note.jpeg` | A **GI pre-printed delivery note**, S.No 13529, 25/08/2026, MPC CNCEC PROJECT, driver ASGHAR, vehicle 1188, RAS AL KHAIR, 4 typed line items + 12 blank rows + signature block. **SAP CODE NO column is blank** | ❌ **No** | `/entry/ocr` → `ocr_delivery_note` |

**Measured, not assumed** (`cv2.QRCodeDetector` on the originals, plus the
200-dpi first-page render for the PDF):

```
Consumption paper.jpeg : 904x1280   cv2_qr=''                                   ← no QR
Delivery Note.jpeg     : 894x1280   cv2_qr=''                                   ← no QR
Execution entry.pdf p1 : 1654x2339  cv2_qr='GIF1|CNCEC|LSC1||32934F7226D44CBF'  ← decodes
```

### 7.2 The first finding — two of the three would be refused by design

`ocr_form.decode_qr` ([ocr_form.py:213](backend/api/ai/ocr_form.py:213)) raises
**HTTP 422** — *"no QR code was found on this photo"* — for any page without
one. That is correct and locked behaviour for the Execution Entries lane: the
QR is what makes positional row mapping safe (rule: "row 3 on the paper is row
3 of `recipe_rows()`", enforced by a recipe fingerprint).

So **if either paper document was uploaded through the Execution Entries tab,
the correct outcome is a refusal, not an extraction.** If that is what happened,
the bug is a **UX bug, not an OCR bug**: the two lanes are on different pages
(`/execution` vs `/entry/ocr`), and nothing on the Execution Entries tab tells a
supervisor that a non-GI paper belongs elsewhere. **Q12** asks which page these
were actually uploaded on, because it changes the fix completely.

### 7.3 The second finding — "only loading" has four candidate causes, and they are distinguishable

The Execution Entries poller
([ExecutionPage.tsx:1022](frontend/src/pages/ExecutionPage.tsx:1022)) polls
every 2 s and **stops only on `done` or `error`**. There is no client-side
timeout and no elapsed-time display. So *any* condition that leaves
`ai_jobs.status = 'running'` shows as **"Reading the form…" forever** — which is
exactly the reported symptom. The four candidates:

| # | Cause | Evidence for | How to tell |
|---|---|---|---|
| **C1** | **The read genuinely takes longer than anyone waits.** `client.py`'s own measurements: a consumption-log JPEG at 1024 output tokens took **189 s**; a full page needs **460–650 s**. `VISION_TIMEOUT_S` is **900 s**. The UI says *"This usually takes under a minute."* | The numbers are in the code as measured facts | Job eventually flips to `done`; the operator gave up first |
| **C2** | **The worker task died with its process.** `form_jobs.spawn` is an in-process `asyncio.create_task`. A dev-server `--reload`, a deploy, or a crash kills the task, and the row stays `running` until the orphan sweep. | Development is done with `uvicorn --reload` | Job stuck at `running` with a stale `heartbeat_at`; log has no completion line |
| **C3** | **Ollama down or the vision model not pulled.** Then `preflight()` should have said so *before* the upload button was offered. | Ollama was **not running** on this machine when I started this session | Job flips to `error` with a named message — so this one does *not* produce an infinite spinner, which is evidence against it |
| **C4** | **The job completed but the sheet was rejected downstream**, e.g. `validate_sheet` 422/409. | `Execution entry.pdf` is form `32934F7226D44CBF`. If that row is absent from `sme_consumption_form`, or already `consumed`, or its recipe fingerprint has drifted, `build_entry` raises. | Job flips to `error` with a specific sentence — again *not* an infinite spinner |

⚠️ **C3 and C4 both produce a visible, named error.** The reported symptom is an
*infinite spinner*, which points at **C1 or C2**. My working hypothesis is
**C1 for the PDF** (a 5-row typed form should be fast, but a cold 6 GB model
load plus a 2,600-token budget is minutes, not "under a minute") and **C2 during
development**.

### 7.4 Trial run — evidence

I started a local Ollama (`qwen2.5vl:7b`, already pulled) and ran the **real
pipeline functions** against the three files, outside the app, with no database.
Results are appended in **§7.7** below.

### 7.5 Proposed work — six items

| # | Item | Why |
|---|---|---|
| **T6-1** | **Tell the truth about elapsed time.** Replace *"usually takes under a minute"* with a live elapsed counter and the measured expectation per lane, plus a "still working — started 4 min ago" state after the median. And **surface `heartbeat_at`**: if it has not moved in 90 s, say *"this job has stopped responding"* rather than spinning. | The UI currently promises 60 s for a job the code documents as 460–650 s. That mismatch alone would produce this bug report. |
| **T6-2** | **Make a killed worker visible and recoverable.** A `running` job whose heartbeat is stale is shown as *interrupted*, with a **Retry** button that re-queues from the stored image. Today the orphan sweep is the only recovery, and it is invisible to the user. | C2. Also removes a whole class of "it's stuck" reports. |
| **T6-3** | **Route the paper documents to the right lane, in the UI.** On the Execution Entries upload card, catch the "no QR" 422 and offer the actual next step: *"This is not a printed GI form. If it is a consumption sheet or a delivery note, use Entry → OCR Import."* — with a link. | Turns a dead end into a route. Cheap, and it is the fix if the answer to **Q12** is "they uploaded them on the wrong page". |
| **T6-4** | **Fix the ditto pipeline, and bring the SK lanes up to the execution lane's standard.** ⚠️ **The trial in §7.7 found the actual extraction defect**: the model returns `""` for ditto cells, `handwritten.resolve_ditto` only recognises the glyphs `{" 〃 ,, '' `}`, so **19 of 30 tank numbers, 14 of 30 names and 8 of 30 product names are silently dropped**. Fix both ends (resolver accepts a blank-on-a-populated-row as a flagged ditto; prompt asks for an explicit sentinel instead of a punctuation mark). Also: `/entry/ocr` needs the same elapsed/heartbeat treatment as T6-1/T6-2, and the `(Night)` shift marker the model already reads should be captured (**Q13**). **The DN prompt needs no work — it was 100 % exact in the trial.** | This is the reported symptom, root-caused. ⚠️ Touching the ditto rule means amending the vendored spec first (**Q15**). |
| **T6-5** | **Add the three files to the eval corpus** — as a fixture set with a hand-written ground truth (`Trail Files/` stays out of git per your instruction; the fixtures live wherever you say — **Q14**). Then extraction accuracy per field becomes a *measured, trended number* under Track 5 instead of an opinion. | This is the bridge between Track 6 and Track 5, and it is why they are in one phase. |
| **T6-6** | **Wire the Track 4 spans through both OCR lanes.** Today when a read is poor there is no record of what the model was given (image dimensions, estimated tokens, `num_ctx`, `num_predict`, whether the reply was truncated and salvaged). | ARCHITECTURE §7a's whole point is that these three numbers are one decision — and they are currently invisible after the fact. |

### 7.6 One thing I want to flag as out of scope unless you say otherwise

The delivery note's **SAP CODE NO column is blank** on the real paper, so the DN
lane must resolve `material_text` → SAP through `ai/fuzzy.py`. That is existing
machinery, but rule 1 (component identity is `(Material_Code, SAP_Code)`) means
a fuzzy match on description alone can land on the wrong drum of a multi-part
system. I am **not** proposing to auto-accept a fuzzy SAP on a DN. **Q8.**

### 7.7 Trial-run results — measured today on this machine

I started a local `ollama serve` (`qwen2.5vl:7b`, already pulled) and called the
**real production functions** — `ocr_form.read_form`, `ocr.prep_image_for_vision`,
`client.generate`, `ocr.parse_vision_reply` — against all three files, with the
same prompts, `num_predict` and `num_ctx` the app uses. No database, no API.

| File | Lane | image tokens | `num_ctx` | `num_predict` | **Elapsed** | Result |
|---|---|---|---|---|---|---|
| `Execution entry.pdf` | `ocr_consumption_form` | 4,399 | 8,192 | 2,600 | **398.8 s** | ✅ **100 % correct** |
| `Delivery Note.jpeg` | `ocr_delivery_note` | 2,198 | 8,192 | 1,536 | **92.1 s** | ✅ **100 % correct** |
| `Consumption paper.jpeg` | `ocr_consumption` | 2,265 | 8,192 | 3,072 | **212.4 s** | ⚠️ **Reads well, but loses every ditto cell** |

#### ⚠️ Finding 1 — the model is not the problem. The clock is.

`Execution entry.pdf` came back **perfect**: `27/08/26`, `TNK-071`, `10` m²,
`Johnson`, and all five rows (`5/123`, `5/123`, `5/345`, `2/0`, `11/567`)
matching the paper exactly, with `area_sqm` parsed to `10.0`.

**It took 398.8 seconds — 6 minutes 39 seconds.** The upload card says:

> *"This usually takes under a minute."*

That single sentence is, I believe, the whole bug report. The extraction works;
the interface promises a minute, shows an unlabelled spinner, and gives the
user no elapsed time, no progress and no reason to keep waiting. **"Only
loading, not getting the results" is what a correct 6½-minute job looks like
behind a 60-second promise.** This is candidate **C1** from §7.3, now measured
rather than hypothesised — and it makes **T6-1 the highest-priority item in the
entire phase**, because it costs almost nothing and it is the thing the operator
is actually experiencing.

*(Context: this is a Mac with 11.8 GiB of VRAM; Ollama offloaded 28 of 29
layers to the GPU and kept the output layer on CPU, which is what makes
generation crawl. The CPX42 will differ — but it will not be 6× faster, and
**Q7-new** below asks whether we should measure it before deployment.)*

#### ⚠️ Finding 2 — the delivery note is already solved

Every header field and every line item, exactly right, in 92 s:

```json
{ "header": { "DN_No": "13529", "Date": "2026-08-25",
              "Mob_From": "MPC CNCEC PROJECT", "Driver_Name": "ASGHAR",
              "Vehicle_No": "1188", "Prepared_by": "ESAM",
              "Mob_To": "RAS AL KHAIR" },
  "items": [ {"material_text": "Rubber Sheet Roll 4E CN 4mm", "uom": "ROLL",  "quantity": 12},
             {"material_text": "BC 3004",                     "uom": "Cans",  "quantity": 37},
             {"material_text": "Hardener E40",                "uom": "BOX",   "quantity": 28},
             {"material_text": "Carbon Filler",               "uom": "Bags",  "quantity": 7} ] }
```

The twelve blank rows were correctly skipped and nothing was invented. **The DN
prompt needs no work.** What it needs is the elapsed-time honesty of T6-1 and
the SAP-resolution decision of **Q8** (the paper's SAP CODE NO column is blank,
so all four lines will arrive at `fuzzy.resolve_rows` with description only).

#### ⚠️ Finding 3 — the real extraction defect: ditto marks are normalised away

The consumption paper parsed cleanly (7,023 raw chars, well inside the 3,072-token
budget — **no truncation**), read the date as `25/08/26 (Night)` including the
shift marker, and got the difficult handwriting mostly right.

**But it returned `""` for every ditto cell instead of the ditto glyph.**

`ocr.CONSUMPTION_PROMPT` is explicit about this — *"Ditto marks (`"`, `〃`, `,,`)
mean 'same as above' — transcribe the GLYPH itself, never copy the value down"* —
and `handwritten.resolve_ditto` ([handwritten.py:104](backend/api/ai/handwritten.py:104))
depends on it absolutely:

```python
_DITTO = {'"', "〃", ",,", "''", "`"}
...
if v in _DITTO:            # ← "" is not in this set
    row[f] = prev.get(f)   # ← so this never runs
```

An empty string is not a ditto glyph, so **the deterministic inheritance stage
never fires and those cells stay blank**. On this real paper that is the
majority of the sheet:

| Column | Blank cells returned (of 30 rows) |
|---|---|
| `tank_no` | **19** |
| `issued_to` (Name) | **14** |
| `material_text` (Product Name) | **8** |

**This is literally the reported symptom — "consumption paper … details not
getting".** The details are not missing from the read; they are being silently
discarded between a prompt rule the model does not obey and a resolver that only
recognises the form the model does not produce.

Two candidate fixes, and I lean strongly to doing **both**:

1. **Make the resolver accept the model's actual output shape.** A blank cell in
   a `_DITTO_FIELDS` column, **on a row that is otherwise populated**, is a
   ditto. ⚠️ The qualifier matters: a blank on an *empty* row is an empty row,
   and inheriting into it would invent an issue to a person who was never
   there. This must be a distinct, flagged resolution — `INFO_DITTO_INFERRED`
   — so a human reviewing the grid can see which values were inherited from a
   guess about a blank rather than from a mark on the paper.
2. **Change the prompt to stop asking for a glyph a VLM will not emit.** Ask
   for an explicit sentinel — `"same_as_above": true` per field, or the literal
   token `<DITTO>` — which is a thing the model can reliably produce, rather
   than a punctuation mark it normalises away. Then the resolver has an
   unambiguous signal and the "blank vs ditto" ambiguity disappears entirely.

⚠️ **This is a change to a preserved rule.** `docs/features/handwritten-ocr` is
a vendored spec with a "preserve exactly" list, and ARCHITECTURE §7 states the
order: *"edit the owning spec file first, then the module, then the suite-AM
pins."* I am not proposing to skip that — **Q15** asks for your authorisation to
amend the spec.

#### Smaller observations from the same read

* `"Sunil"` came back as `"Sunit"`, `"Pair"` as `"Pcs"`, `"R/L"` as `"RU"`. All
  are name/label fields, all are already human-reviewed in the grid, and none of
  them is a quantity — which is the discipline the prompts were built around.
  **Every quantity I could check against the image was correct.**
* `date_text` captured `(Night)` verbatim. That is the shift marker of **Q13**,
  and the model reads it reliably — it is sitting there unused today.

#### What the trial changes in the plan

| | Before the trial | After |
|---|---|---|
| Cause of "only loading" | four candidates | **C1 confirmed and measured (398.8 s vs a 60 s promise)** |
| DN prompt work | assumed needed | **not needed** — it is already exact |
| Consumption lane | assumed a prompt-tuning job | **a specific, named defect**: ditto → `""` → resolver never fires |
| Execution form lane | unknown | **model side is correct**; the remaining risk is `validate_sheet` against the live registry, which I could not test (Postgres was down) |

---

## 8. Sequencing, cost and risk

Six slices. Ordered so the cheapest, highest-certainty work lands first and
each slice leaves the tree green.

| Slice | Contents | Depends on | Rough size |
|---|---|---|---|
| **11a** | **CI fix (F1–F5) + `bin/ci_preflight.sh` + `CLAUDE.md` + `.claude/RULES.md`** | nothing | Small. Unblocks a 30-run-old red CI on day one. |
| **11b** | **Track 6: T6-1/T6-2/T6-3/T6-4** — elapsed time + heartbeat/interrupted state (the 398.8 s vs 60 s mismatch), lane routing, **and the ditto fix**. | 11a | Small–medium. **This is the operator-visible bug and it is already root-caused — I would ship T6-1 and the ditto fix first, before any of Tracks 2–5.** |
| **11c** | **Track 4 slice A** — span vocabulary + `ai_traces` + retrieval telemetry | 11a | Medium. Everything after this is measurable, which is why it comes before the tuning work. |
| **11d** | **Track 2** — `ai/guard.py`, wiring, suite CT | 11c (so guard decisions are traced) | Medium. |
| **11e** | **Track 3** — `ai/route.py` + exact-match cache; semantic cache only if 11c's numbers justify it | 11c | Medium. |
| **11f** | **Track 5** — deterministic metrics as gates, 140-case dataset, Tier 2 ratchet, T6-4/T6-5 prompt validation | 11b, 11c, 11d | Medium–large. |

**Risks I want named up front:**

* ⚠️ **Guard false-refusals are worse than the attacks they prevent.** A Store
  Keeper refused at 06:00 for saying "ignore the damaged drum" has been failed
  by the system. Every jailbreak pattern ships with a negative twin, and the
  false-refusal rate is a **gated** metric (§6.3), not an afterthought.
* ⚠️ **The cache is a fence bypass if the key is wrong.** §4.4. I want this
  locked as a ruling before implementation.
* ⚠️ **The router must not swallow the `num_ctx` decision.** §4.2 point 1.
* ⚠️ **Four workers.** Traces, cache and any sweep all need the P10-2 treatment
  (`daily_job_runs` claim for jobs, per-worker bounded queues for writes).
* **Scope**: Tracks 2–5 add ~1,200 lines of backend and one console tab. None
  of it touches SME maths, RBAC, or the ledger. Track 6 touches two upload
  cards and two prompts.

---

## 9. Clarifying questions I need answered

**Blocking — I cannot design around these:**

1. **`CLAUDE.md` scope (Q1).** Repo-only (`CLAUDE.md` + `.claude/RULES.md`), or
   do you also want a **user-level `~/.claude/CLAUDE.md`** that loads in every
   session on this machine? If yes, should it carry GI Hub's locked rules, or
   only machine facts (venv path, `bin/*.sh`, `:5433`, the Ollama model set)?
   My recommendation is repo-only + a machine-facts global.
2. **The 0.85 gate vs ruling P10-7 (Q2).** Do you accept the §6.3 split —
   deterministic retrieval and guard metrics gate at ≥0.85, judged metrics
   become a trended artefact with a regression ratchet — or do you want to
   **overturn P10-7** and gate on a judged score? (If the latter, note the
   current Tier 2 security score is 64% and would fail every run.)
3. **Cloud fallback policy per lane (Q5).** Which lanes, if any, may fall back
   to Anthropic when Ollama is down? My proposal: **vision only** (already your
   ruling Q7, opt-in), **assistant maybe**, and **never** `/ai/query`,
   `/ai/nl-search`, `/ai/insights`, `/ai/eod-summary` — those carry live stock
   rows and generated SQL off the network.
4. **Which page were the Trail Files uploaded on (Q12)?** Execution Entries
   (`/execution`) or OCR Import (`/entry/ocr`)? If the papers went through
   Execution Entries, the primary fix is T6-3 (routing + a better refusal);
   if they went through OCR Import, it is T6-4 (prompt/parse work). This
   changes what slice 11b actually contains.
5. **Cache-key ruling (Q4).** Do you agree the answer cache key must include
   `role` (and site scope for data lanes), and may I write that into
   `PROJECT_HANDOVER.md` as a Phase 11 locked ruling **before** any cache code?

**Important, but I can proceed with a stated assumption:**

6. **LiteLLM middle path (Q6).** Use the LiteLLM SDK **only** for the cloud leg
   (provider-agnostic Anthropic/OpenAI/Bedrock fallback), keeping the Ollama
   path hand-written? Or fully hand-written both sides? *Assumption if you do
   not answer: fully hand-written.*
7. **macOS libzbar (Q3).** Do you want the QR roundtrip check to actually run
   on your Mac? That needs `DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib` in
   `bin/dev.sh`/your shell, or a small `zbar_library` shim. *Assumption: CI
   runs it for real; macOS reports an explicit SKIP.*
8. **DN fuzzy SAP resolution (Q8).** May a delivery-note line auto-resolve to a
   SAP code by description similarity, or must every DN line be confirmed by a
   human before it becomes a receipt? *Assumption: confirm, never auto-accept
   (consistent with rule 1).*
9. **Tier 2 ratchet teeth (Q9).** Advisory only (bug row + console), or should
   a Tier 2 regression **block a release tag** (`v*`) while still never
   blocking a merge? *Assumption: advisory in Phase 11.*
10. **Where does Tier 2 run (Q10)?** GitHub runners have no Ollama. Your box on
    a schedule, or the Hetzner host after deployment? *Assumption: your box,
    via a `bin/` script, until Hetzner exists.*
11. **Real questions (Q11).** Is there any retained log of questions people have
    actually asked the Hub Assistant (`system_audit_log`, an SSE access log,
    anything)? Those 20 cases would be the most valuable in the dataset. If
    nothing is retained, do you want Phase 11 to **start** retaining them
    (question text + role + outcome, no answer body)?
12. *(see Q12 above — blocking)*
13. **The `(Night)` shift marker (Q13).** The consumption paper's date box reads
    `25|08|26 (Night)`. Ruling **P10-9** forbids *inferring* `Shift` from a
    timestamp — but this is written on the paper by the person who did the work.
    May the OCR lane capture it as data? My reading is yes, and that it
    strengthens P10-9 rather than weakening it (it is the honest source P10-9
    says does not exist for old rows).
14a. **Amending the ditto rule (Q15) — please read §7.7 Finding 3 first.**
    `docs/features/handwritten-ocr` is a vendored spec with a "preserve exactly"
    list, and ditto resolution is on it. The trial proves the rule as written
    cannot work, because `qwen2.5vl:7b` normalises a ditto mark to `""` and the
    resolver only recognises glyphs. May I amend the spec (spec first, then
    `handwritten.py`, then the suite-AM pins, per ARCHITECTURE §7) to (a) accept
    a blank-on-a-populated-row as a **flagged** ditto, and (b) change the prompt
    to ask for an explicit sentinel rather than a punctuation mark?

14b. **Measure the CPX42 before deployment (Q16)?** The 398.8 s figure is this
    Mac (11.8 GiB VRAM, output layer on CPU). The Hetzner CPX42 is CPU-only,
    16 GB. If a 5-row form takes minutes here, it may take **considerably
    longer** there — which would change T6-1 from "tell the truth about the
    clock" into "this lane needs the cloud seam turned on for production"
    (ruling Q7 already anticipates exactly that escalation). Do you want a
    measured benchmark on the target hardware as part of Phase 11, before the
    UI copy is written to a number?

15. **Fixture location (Q14).** `Trail Files/` stays out of git. Where should the
    eval fixtures + ground truth live so CI can use them —
    `data-archive/ocr_fixtures/` (in git, and these are real signed documents
    with employee names), a gitignored `fixtures/` directory with a checked-in
    manifest, or Git LFS? My recommendation: **gitignored directory + a
    checked-in ground-truth YAML**, so the *expected values* are versioned and
    gated while the images stay out of the repository.

---

## 10. What I did NOT do

* No application code was written. This document is the deliverable.
* `Trail Files/` was read but not staged, per your instruction.
* I started a local `ollama serve` to run the trial in §7.7, **and stopped it
  again afterwards**, so the machine is back in the state I found it. Nothing
  else was started or changed: Postgres (`:5433`), the API (`:8000`) and Vite
  (`:5173`) were all down when I began — the box was in `bin/power.sh sleep` —
  and they still are. No file in the repository was modified except this one.
* I downloaded the failing CI run's artifact with `gh run download` (read-only)
  into the session scratchpad, not into the repo.
