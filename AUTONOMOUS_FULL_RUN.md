# Autonomous Full Run — Phase 2 + Operator Tasks + Phase 3+

## User Policy Reminder (B/C — Explicitly Authorized)

The user has explicitly authorized:

- **B: Auto-merge to `main`** — you may merge PRs to `main` autonomously after opening them, in this order: docs → hygiene → security patches → operator tasks → feature work. Do NOT batch merges. Merge each PR one at a time and only after CI passes (if CI is configured). If any PR fails CI or produces unexpected verification results, do not merge it — leave it open, log the issue, move on to the next.
- **C: Scope through Phase 3+** — after Phase 2 and operator tasks are complete, continue with any Phase 3 work implied by `PROJECT_STATUS.md`, `SESSION_HANDOFF.md`, or other repo docs. Stop only when: (a) a stop-and-ask condition triggers, (b) you have no more actionable work in the docs, or (c) the session context budget requires a natural break.

Non-negotiable guardrails in `AUTONOMOUS_WORKFLOW.md` v2 still apply — auto-merge does not override them.

## Session Bootstrap — Do These First, In Order

1. Read `docs/AUTONOMOUS_WORKFLOW.md` v2 in full — this is the operating agreement
2. Read every `.md` file in the repo root
3. Read `SESSION_HANDOFF.md` at the repo root — this is your context anchor from the prior session
4. Read `docs/security/reports/99_summary.md` for Phase 1 findings
5. Read this file (`AUTONOMOUS_FULL_RUN.md`) — the work-order
6. Then run the mandatory pre-flight per AUTONOMOUS_WORKFLOW.md

## Mandatory Pre-Flight Reports (Before Any Work)

Before doing anything else, produce a single chat message with:

1. **Bootstrap confirmation** — list every file you read during bootstrap
2. **Repository state** — current `main` HEAD SHA, open PRs, active branches, working tree status
3. **Restated plan in your own words** — a numbered list of what you're about to do, in order, from the current state to full completion of Phase 2 + operator tasks + Phase 3+
4. **Predicted stop-and-ask points** — any part of your plan you expect will hit `AUTONOMOUS_WORKFLOW.md` §Stop-and-Ask
5. **Estimated commit and PR count** — rough count so the user can size the eventual review load

Post this message and **stop for user acknowledgment before starting work.** This is the only mandatory user gate in the entire run. Every other decision is yours.

## Work Sequence (Execute In This Order)

### Sequence 1 — Phase 2 Theme D (may already be merged)

**Source:** `docs/security/reports/99_summary.md` §5 Theme D + PR #4 if still open

**Deliverable:** boot-time startup diagnostic that reports the state of every documented secret (name only, never value) at application boot. Automates the docs-vs-reality comparison that had to be done manually in Audit 04.

**Check first:** if PR #4 is already merged, verify the diagnostic is in place. If it is, mark Theme D complete and move on.

**If not merged or not in place:**

- Branch: `security/theme-d-config-discipline`
- Add module `backend/api/secret_diag.py`
- Wire into `backend/api/main.py` lifespan
- Test in `service_tests.py`
- Verify locally (unit + integration), open PR, auto-merge

### Sequence 2 — Phase 2 Theme A (Falsy-Check Systemic Remediation)

**Source:** `docs/security/reports/02_idor.md` A02-F2 + `docs/security/reports/99_summary.md` §5 Theme A

**Deliverable:** systemic fix for the 12 sites using `if site_id:` (falsy) where `if site_id is not None:` (fail-closed) was correct. Plus a lint rule to prevent regression.

**Sites to fix** (from Audit 02):

- `dashboard.py:32` — HIGHEST impact, `/dashboard/metrics` returns global stock to warehouse users
- All 12 sites in Audit 02 A02-F2. Read the audit and enumerate.

**Do NOT touch** the 26 correctly-fail-closed `is not None` sites in `manhours.py` and `sme.py`. Audit 02 verified these.

**Also fix** the deferred item from Pre-Phase-2 Patch B: `GET /requests/{request_id}/items` missing per-request scope check (inline TODO placed in Patch B).

**Lint rule:**

- Add a `semgrep` or `ruff` rule (whichever fits the project's existing lint setup, or add semgrep if none is present — check `PROJECT_STATUS.md` and any CI config for tooling preferences) that flags `if variable:` patterns where the variable name matches `site_id`, `warehouse_id`, or similar scope identifiers used in the codebase
- Add the rule to CI if CI exists; otherwise add a `make lint` or `npm run lint` target
- Document the rule in `docs/security/LINT_RULES.md` (create if missing)

**Verification:**

- Unit tests for every fixed site (parameterized: user with `''` site vs `None` vs valid site)
- Integration test proving `/dashboard/metrics` no longer leaks global stock to a warehouse user
- Browser verification: log in as a warehouse user, hit `/dashboard/metrics`, confirm scoped result
- Regression: run the full existing test suite

**PR:** `security: Phase 2 Theme A — falsy-check remediation`
**Merge:** auto-merge on green.

### Sequence 3 — Phase 2 Theme B (AI NL→SQL Hardening)

**Source:** `docs/security/reports/01_sql_injection.md` A01-F1 + A01-F2 + `docs/security/reports/99_summary.md` §5 Theme B

**⚠️ STOP-AND-ASK EXPECTED HERE.** Theme B touches `backend/api/ai/safety.py`, `backend/api/ai/router.py`, and possibly `backend/api/auth.py`. Per `AUTONOMOUS_WORKFLOW.md` v2 §Stop-and-Ask #3, any change to auth code paths requires explicit user approval before proceeding. Read the audit findings carefully, propose your fix approach, post the plan, and stop.

**Deliverable (once approved):**

- Rewrite the safety gate so `FORBIDDEN_TABLES` cannot be bypassed by schema-qualified names (`public.users`), `ONLY` keyword, or other regex evasions. Prefer AST/parser-based validation over regex.
- Add the 7 sensitive tables (`bug_reports`, `email_outbox`, `employees`, `entry_attachments`, `phone_otp`, `system_audit_log`, `whatsapp_outbox`) to the blocklist and to `backend/scripts/create_ai_readonly_role.sql`
- Add AI query audit logging (per-query row: user, sanitized query, timestamp, allowed/denied, result row count) — enables post-hoc detection
- Add a startup assertion that `gi_ai_ro`'s REVOKE state is correct (defense-in-depth check that runs at boot)

**Verification:**

- Unit tests for each bypass pattern the audit identified
- Integration test: attempt a bypass query as a logistics user, verify 403 or safe rejection
- Browser verification: log into the AI query UI, run a known-safe query, verify result; run a known-bad query, verify rejection
- Regression: existing AI tests

**PR:** `security: Phase 2 Theme B — AI NL→SQL lane hardening`
**Merge:** auto-merge on green **only if user has approved the auth-adjacent changes** in the stop-and-ask above.

### Sequence 4 — Phase 2 Theme C (Auth Surface Completion)

**Source:** `docs/security/reports/03_auth_session.md` (multiple findings) + `docs/security/reports/99_summary.md` §5 Theme C

**⚠️ STOP-AND-ASK EXPECTED HERE.** Theme C is entirely in `auth.py` territory. Per v2 §Stop-and-Ask #3, propose the plan and wait.

**Deliverable (once approved):**

- Add rate limiting to `/2fa/enroll`, `/2fa/verify`, `/2fa/disable` (A03-H3)
- Unify `GI_ENV` string acceptance across `is_production()` and any remaining string-comparison sites (already partially done in Pre-Phase-2 Patch C — check what remains)
- Ensure refresh cookies ship with `Secure=True` in production and confirm the CORS-origins-empty-string fallback is closed
- Any other Audit 03 finding queued for Theme C in `99_summary.md`

**Verification:**

- Unit tests for rate limiter hits on each 2FA endpoint
- Integration test: rapid-fire attempt to brute-force a 2FA code, verify block
- Browser verification: full auth flow login → 2FA → refresh → logout
- Regression: existing auth tests

**PR:** `security: Phase 2 Theme C — auth surface completion`
**Merge:** auto-merge on green **only if user has approved the auth changes**.

### Sequence 5 — Operator Tasks (Dev Environment)

**Source:** `docs/security/OPERATOR_TASKS_POST_PATCHES.md` + `docs/security/reports/99_summary.md` §6

**⚠️ STOP-AND-ASK EXPECTED HERE.** Per v2 §Mandatory Pre-Flight (Operator Tasks), you must name the environment before any destructive statement. Confirm target is `dev` explicitly. The user has authorized dev-environment operator tasks — do NOT run against production (Hetzner not yet provisioned per `SESSION_HANDOFF.md`).

**Deliverables:**

- **Task 4:** Generate and rotate `JWT_SECRET` and `POSTGRES_PASSWORD` in local `deploy/.env`. Never print values to chat. Write to `/tmp/*.txt` with `chmod 600`, report filename.
- **Task 5:** Add the 7 sensitive-table `REVOKE`s to `backend/scripts/create_ai_readonly_role.sql` on a branch, run them against local `gi_ai_ro`, verify with `information_schema.role_table_grants`, open PR, auto-merge.
- **Task 6:** Docker image `.env` leakage check — build `deploy/Dockerfile.api`, inspect the resulting image for any `.env` file, report pass/fail.

**Skip** Tasks 1, 2, 3 (refresh session revoke, password resets, archive user handling) — production doesn't exist yet, dev has only 8 reseeded users, so these are no-ops until production provisioning.

**Verification:**

- `deploy/.env` no longer contains `CHANGE_ME`
- `gi_ai_ro` cannot SELECT from any of the 7 sensitive tables (verify via `psql`)
- Docker image contains no `.env` file (verify via `docker run --rm <image> find /app -name '.env'`)

**PR:** `security: operator tasks — dev env secret rotation + AI-lane REVOKEs + docker check`
**Merge:** auto-merge on green.

### Sequence 6 — Phase 3+ (Whatever the Docs Say)

**Source:** `SESSION_HANDOFF.md`, `PROJECT_STATUS.md`, and any other repo docs describing future work

Read the docs. Identify what's next after Phase 2. Common candidates in this project's history:

- Native app releases (from `NATIVE_APPS.md`)
- Cloudflare Access Bypass policy for `/api/*` (from `PROJECT_STATUS.md` §3)
- SAP integration items
- SME engine expansions
- Documentation refreshes

For each Phase 3+ item:

1. If it requires production credentials or is blocked on Hetzner provisioning → mark blocked, log in `SESSION_HANDOFF.md`, skip
2. If it requires external service touches (Meta, Cloudflare, DNS) → stop-and-ask per v2 §Stop-and-Ask #5
3. If it's a self-contained code or docs change → execute, PR, auto-merge

**Continue until one of:**

- All docs-described work is complete or blocked
- Session context budget hits ~70% (leave headroom to write a clean handoff)
- A stop-and-ask condition triggers that you cannot resolve

## Final Handoff (Before Session Ends)

Regardless of how the run ends, before the session closes:

1. Update `SESSION_HANDOFF.md` with the new state — every PR opened, every merge landed, every stop-and-ask triggered, every deferred item
2. Commit the updated handoff on a fresh branch `docs/session-handoff-<date>`, push, open PR, auto-merge
3. Post a final chat summary listing:
   - Every PR opened and its merge status
   - Every commit landed on `main`
   - Every stop-and-ask that triggered and how it was resolved (or that it's still pending)
   - Every blocked item and its blocker
   - The updated `SESSION_HANDOFF.md` URL on `main`
4. Then stop.

## Verification Discipline (Non-Negotiable, Applies to Every PR)

Every PR body must include:

- Tests run and results
- Browser verification for any user-facing change (login as which role, which page, what you clicked, what you saw)
- Regression check (what could have broken, why it didn't)
- Rollback plan for any schema, config, or destructive change
- Verification limitations (anything you could not verify, and why)

If a verification step cannot be run in this environment (e.g., no production DB to test against, external service rate-limited), do not fake it. Log the limitation and stop-and-ask if the missing verification is central to the PR.

## Merge Discipline (B Policy Details)

- Merge one PR at a time
- Wait for CI to complete (if CI is configured)
- If CI fails, do not merge. Investigate, fix in a follow-up commit on the same branch, wait for CI again.
- If CI passes, merge with a squash or merge commit (match the project's existing convention — check `main` history)
- After merge: delete the feature branch on origin
- Then proceed to the next PR

## What NOT to Do (Even Under B/C)

The non-negotiable guardrails in `AUTONOMOUS_WORKFLOW.md` v2 all still apply. Restated for emphasis:

- **Never** commit secrets to git (including test fixtures)
- **Never** force-push to any branch
- **Never** rewrite git history on `main` or any pushed branch
- **Never** delete Phase 1 audit reports or Phase 1 patch commits
- **Never** bypass `deploy/.env` for secrets
- **Never** run destructive SQL against production (Hetzner isn't provisioned; if it becomes provisioned mid-run, treat it as production and stop)
- **Never** install unauthorized dependencies (add to `requirements.txt` / `package.json` in the PR, note in body)
- **Never** merge a PR whose verification failed or was skipped for reasons other than "not applicable"

## Escalation Protocol (Reminder)

When you hit a stop-and-ask:

1. Post the current state
2. State the specific condition triggered (by number, from v2 §Stop-and-Ask list)
3. Present options in the neutral format (v2 Rule R2) — no "recommended" adjacent to options
4. Stop all tool calls
5. Wait for user reply

Do not proceed based on your own recommendation. The user's textual reply is the only valid unblock.

## Trust Framework (Reminder)

Between stop-and-asks, you are trusted to plan, execute, verify, commit, push, merge, and move on. The user is not paste-relaying. They will check GitHub periodically. If everything is going well, they will not intervene — that is the intended workflow, not a signal to be more cautious.

Cautious ≠ correct. Careful ≠ correct. **Correct** = correct. Do the work as if the reviewer is a good engineer who will read the diff — because eventually one will.

## Session End

When the run naturally ends (either at completion, at a genuine stop-and-ask, or at a context budget break), leave the repo in a state where the next fresh session can pick up cleanly from `SESSION_HANDOFF.md`. That is your last responsibility before signing off.
