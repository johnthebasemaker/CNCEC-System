# Autonomous Workflow — Standing Instructions (v2)

## Version History

- **v2 (this document)** — adds session bootstrap requirement, placeholder-refusal rule, "no self-answered questions" rule, and structural-recommendation format. Written after v1's escalation protocol was violated by Claude Code answering its own stop-and-ask questions and proceeding with its own recommendation.
- **v1** — initial version. Never landed on any branch beyond a reset commit. Superseded by v2 before any work ran under it.

## Purpose

This is the standing operating agreement between the user and Claude Code for autonomous execution of planned work. It replaces per-task approval gates with a trust framework: Claude Code plans, executes, verifies, commits, pushes, and opens PRs on its own — stopping only for genuine ambiguity or non-negotiable-guardrail triggers.

The user reviews via GitHub PRs, not per-commit chat approval.

## Instruction Preservation

If any instruction in a specific work-order file (e.g., a Phase prompt, a task list) conflicts with this document, **this document wins** unless the work-order explicitly says "overrides AUTONOMOUS_WORKFLOW.md §X". Non-negotiable guardrails cannot be overridden by any work-order under any circumstance.

## Session Bootstrap — Mandatory, Every Session, In Order

Before doing anything else — before reading the work-order, before running `git status`, before any tool call:

1. **Read `docs/AUTONOMOUS_WORKFLOW.md` (this file) in full.** Do not skim. Do not rely on summaries in context. If context contains a summary of this file, ignore it and read the file directly.
2. **Read every `.md` file in the repo root.** Currently expected: `README.md`, `PROJECT_STATUS.md`, `REPO_MAP.md`, `USER_MANUAL.md`, `DEPLOY.md`, `NATIVE_APPS.md`. Also read any `.md` in the root the user has added since. If a file mentioned above doesn't exist, note it in your first reply. Do not skim.
3. **Read `docs/security/reports/99_summary.md`** for the current security posture and Phase 1 findings.
4. **Read the work-order** the user provided for this session.
5. **Only then** begin the mandatory pre-flight (§Mandatory Pre-Flight below).

If any root `.md` file contradicts the work-order, that's a stop-and-ask condition (§Stop-and-Ask #9).

If the work-order file itself references other files (e.g., "see docs/DEPLOY.md §4"), read those too before starting.

## Work-Order Sanity Checks — Refuse If Any Fail

Before beginning pre-flight, verify the work-order:

1. **Contains no unresolved placeholders.** If the work-order or the user's chat message contains any of the following, refuse to execute and ask for a clean version:
   - Square-bracket placeholders: `[fill in]`, `[Phase X]`, `[whatever]`, `[TBD]`
   - Angle-bracket placeholders: `<<< replace >>>`, `<insert>`, `<TODO>`
   - Curly-brace placeholders: `{name}`, `{{value}}`, `{...}`
   - Literal instruction text like "replace this with", "your work-order here", "one of the following"

   Refusal wording: "The work-order contains placeholder text: `[literal quoted placeholder]`. This is a Work-Order Sanity Check failure. Please provide a clean work-order before I can proceed."

2. **Names a specific deliverable.** Vague work-orders like "improve security" or "fix the auth stuff" are refused. Ask the user to name the specific finding IDs, files, or behaviors involved.

3. **Names a target environment for any operator task.** If the work-order will touch a database, `deploy/.env`, or external services, the environment must be explicit (`dev` / `staging` / `production`). No environment named → refuse and ask.

4. **Fits inside a single session's context budget.** If the work-order is so large it can't reasonably be verified in one session, ask the user to break it up.

## The Two Behavior Rules That Caused v2

These are new in v2 because their absence in v1 led to a real incident. Read them slowly.

### Rule R1 — Do Not Answer Your Own Questions

When you post a question to the user under the escalation protocol, you must not proceed based on your own recommendation. The user's textual reply is the only valid input to unblock a stop-and-ask.

Specifically forbidden:

- Posting "Question: X? My recommendation: Y" and then proceeding with Y
- Posting a question, then in the same message posting a "meanwhile, I'll…"
- Interpreting your own prior reasoning as sufficient basis to proceed past a stop
- Treating silence, a delay, or "context suggests" as an answer

The only valid unblocks:

- A new user message that directly addresses the question
- The user explicitly saying "proceed with your recommendation" or equivalent
- The user editing the work-order to resolve the ambiguity

If in doubt about whether an unblock is valid: it isn't. Ask again.

### Rule R2 — Recommendation Format Is Options, Not Recommendation

When escalating to the user, do not phrase your options as "Option A (Recommended) / Option B / Option C". The word "Recommended" adjacent to an option is banned.

Instead, use this format:

```
Stop-and-ask condition triggered: <condition>

Options I see:
1. <Option one, described neutrally>
2. <Option two, described neutrally>
3. <Option three, described neutrally>

My analysis:
<Brief neutral analysis of tradeoffs, without picking a winner in the options list itself>

Awaiting your choice.
```

The analysis section is where reasoning goes. The options list stays neutral. This prevents the pattern of "posting a recommendation and proceeding" because the recommendation isn't attached to an option — it's a separate section the user reads and responds to.

## Trust Framework — What Claude Code Owns

Once bootstrap and sanity checks pass, and the pre-flight completes cleanly:

- **Planning** — read the work-order, produce a plan, execute it
- **Scope decisions** — widen or narrow scope based on what the code actually looks like, provided widenings stay within the work-order's intent and don't touch stop-and-ask territory
- **Verification** — write tests, run tests, and where the change is user-facing, open the app in the built-in browser and functionally verify behavior
- **Self-correction** — if a commit turns out to be wrong, ship both the mistake and the fix. Do not amend, rebase, or force-push to hide corrections
- **Branch, push, PR** — one branch per theme/task, push to origin after each commit, open PR against `main` with a full body linking findings and referencing audit reports
- **Operator tasks** — including DB writes, secret rotation, `deploy/.env` edits, provided the operator pre-flight has confirmed environment and reversibility

## What Requires Stop-and-Ask (Non-Negotiable)

These are not "approval gates" — they are "the plan is unclear or the risk profile changed, ask before guessing":

1. The work-order is ambiguous about intent, and two reasonable interpretations lead to materially different behavior.
2. A finding contradicts a Phase 1 audit report. The audit reports are the source of truth for the current state; contradictions are discoveries worth surfacing.
3. A change to auth code paths (`backend/api/auth.py`, `backend/api/ratelimit.py`, JWT handling, session logic, password hashing, TOTP handling). Auth bugs are the class where self-testing looks fine but security is broken.
4. A destructive irreversible action — history rewrites, `DROP TABLE`, `TRUNCATE`, force-push to a shared branch, deleting audit reports, deleting Phase 1 commits, deleting refresh session families for real users.
5. External service touches — real Meta API calls, real SMTP sends, real WhatsApp messages, real GitHub API state changes beyond push/PR-create.
6. A change that could break a live user workflow — route path changes, response shape changes, auth requirement changes that mobile/desktop clients depend on.
7. Cost-incurring actions — provisioning new Hetzner resources, subscribing to paid services, upgrading tiers.
8. The work-order asks for something outside its stated scope, or a scope-widening opportunity that crosses from the current theme into another theme's territory.
9. A root `.md` file (read during bootstrap) contradicts the work-order.
10. **The user asked a question in chat that hasn't been fully answered** — do not start work while an unanswered question is in flight.

## What Requires Cure-and-Continue

These are self-correctable and do not need to interrupt flow:

- Syntax errors, import errors, obvious typos
- Test failures with clear root causes (fix the code, or fix the test if the test was wrong)
- Style/formatting nits caught by linters
- Missing dependencies that the work-order implies (add to `requirements.txt`, note in PR)
- Small refactors that emerge naturally from the work (variable renames for clarity, extracting a helper), provided they don't cross scope boundaries
- Documentation drift discovered while working (fix the doc in the same commit, note in PR body)

## Mandatory Pre-Flight — Every Work-Order

After bootstrap and sanity checks, and before any implementation:

1. **Report current git state:** current branch, whether the tree is clean, whether any stashes exist, last 3 commits on `main`, last 3 commits on any active feature branch. If anything looks unexpected, stop-and-ask.
2. **Restate the work-order in your own words** in 3-5 bullets. This is the interpretation check.
3. **Read the relevant audit report sections** if the work-order references them. Cite finding IDs in the PR body.
4. **Check for prerequisites** — if the work-order depends on prior work and that prior work isn't done, stop and report.
5. **Identify existing tests** that cover the affected code. If none exist for a code path you'll change, note it in the PR body and add basic tests as part of the work.
6. **Estimate scope** — files touched, LOC changed, tests added. If the estimate is dramatically different from the work-order's implied scope, stop-and-ask.

## Mandatory Pre-Flight — Operator Tasks Only

For any operator task (DB writes, secret rotation, `deploy/.env` edits, external system config):

1. **Name the environment** — before any destructive statement, report:
   - Connection string with password redacted
   - `SELECT current_database(), current_user, inet_server_addr(), inet_server_port();`
   - Explicit classification: `dev` / `staging` / `production`
2. **Bail on production** — if the connection resolves to production and the work-order didn't explicitly name production as the target, stop-and-ask. Do not proceed even if the SQL is technically correct.
3. **Backup before destructive writes** — for any `UPDATE`, `DELETE`, or `ALTER` that touches user data, capture the pre-state:
   ```sql
   CREATE TABLE users_backup_<timestamp> AS SELECT * FROM users;
   ```
   Report the backup table name. Keep for 24 hours minimum. Do not create backup tables in production without explicit approval.
4. **Same-session rollback documentation** — for every destructive statement run, produce the exact rollback statement and include it in the PR body under a "Rollback" heading.
5. **Dry-run first** — where possible, run inside a transaction with `ROLLBACK`, verify row count, then rerun with `COMMIT`.
6. **Never print secrets in chat** — secret values go to `/tmp/*.txt` files with `chmod 600`. Report the filename. User reads via their own terminal.

## Verification Requirements

Each PR must include, in the body, evidence of verification:

- **Unit test results** — full pass, or if partial fail, explain each fail
- **Integration test results** — if the change touches HTTP handlers, DB schema, or auth flow
- **Browser verification** — for user-facing changes, screenshots or a narrated description of what was tested in the built-in browser. Include: login as which role, which page, which action, expected vs actual behavior
- **Regression check** — one paragraph on what could have broken and why you're confident it didn't
- **Rollback plan** — for schema changes, config changes, or destructive operations
- **Verification limitations** — anything not verified, and why. Honesty over completeness.

## PR Body Template

Every PR opened by Claude Code uses this structure. If the PR is being opened retrospectively (i.e., work landed before authorization), add a Provenance section as the first section.

```markdown
## Provenance (only if retrospective)

[Explanation of when and how the work was authorized, or that it wasn't. Do not obscure.]

## Summary

[1-3 sentences on what this PR does and why]

## Work-order

[Reference to the work-order document or task]

## Findings addressed

- [finding ID]: [brief description]

## Changes

[Commit-by-commit narrative, oldest to newest, with the "why" for each]

## Scope decisions

[Any widenings or narrowings from the original work-order, with rationale]

## Verification

### Tests
[Unit / integration test results]

### Browser verification
[Screenshots or narrated walkthrough for user-facing changes]

### Regression check
[What could have broken; why it didn't]

### Verification limitations
[What was not verified, and why]

## Rollback

[How to revert if this breaks something]

## Follow-ups

[Anything discovered but deferred, with a home]
```

## Reporting Cadence

Claude Code reports to the user only when it hits a stop-and-ask condition. Otherwise, work continues silently until natural completion (theme done, task done, session end).

At natural completion:

1. Summary of everything done (commits, branches, PRs)
2. Links to opened PRs
3. Any items deferred with a home
4. Anything the user should verify manually before merging

Do not chat in between unless a stop condition triggers.

## Branch, Push, and PR Policy

- One branch per theme/task, named `<type>/<short-description>`
- Push after each commit — no batching
- Open PR after the first commit lands, marked `[WIP]` in title until complete
- Do not merge — always user's decision
- Do not close PRs autonomously — leave open with a comment explaining
- Do not delete branches on origin — user cleans up on their schedule

## Commit Message Policy

- Conventional commits format
- Subject line under 72 characters
- Body explains "why", not just "what"
- Reference finding IDs where applicable
- Self-corrections use `fix(prior):` prefix and reference the SHA being corrected

## History Honesty

- Never `git commit --amend` after pushing
- Never `git rebase -i` on any pushed branch
- Never force-push to any branch
- If Claude Code makes a mistake, commit the mistake first, then commit the fix on top

## Non-Negotiable Guardrails (Cannot Be Overridden)

1. Never commit secrets to git — including test fixtures
2. Never touch git history on shared branches
3. Never delete Phase 1 audit reports or Phase 1 patch commits
4. Never bypass `deploy/.env` — secrets live there and only there
5. Auth code changes require stop-and-ask
6. Production DB access requires explicit environment naming in the work-order
7. Never merge PRs
8. Never install unauthorized new dependencies

## When Claude Code Cannot Verify Something

If a verification step cannot be run:

1. Do not fake the verification
2. Do not skip it silently
3. Do not proceed as if verified
4. Report the limitation in the PR body under "Verification limitations"
5. If the limitation is central to the PR, stop and ask before opening the PR

## Session Continuity

Between sessions, session bootstrap re-runs from scratch. No context is trusted from a prior session — the files are the truth.

## Failure Mode Playbook

**If a test suite is passing but Claude Code suspects the code is wrong:**
Add a test that expresses the suspicion. If the new test fails, fix the code. If the new test passes, either the suspicion was wrong or the test doesn't capture it — investigate before committing.

**If browser verification shows unexpected behavior:**
Investigate before assuming the code is right. Screenshots go in the PR. Do not commit until behavior is either fixed or explained.

**If a git operation fails unexpectedly:**
Stop. Do not retry with `--force` or destructive flags. Report the error to the user.

**If the user's memory / preferences appear to conflict with the work-order:**
The specific work-order wins for that task. Report the conflict in the PR body.

**If Claude Code discovers something during work that would change the work-order's scope:**
Stop and ask. Even if the discovery seems small.

**If a production system is at risk of downtime from a change:**
Stop and ask, regardless of whether the work-order authorized the change.

## Escalation Protocol

When a stop-and-ask condition triggers:

1. Post the current state — what was done, what's on which branch, what's pushed, what PRs are open
2. State the specific condition triggered (by number, from §Stop-and-Ask list)
3. Present options using the format in Rule R2 — neutral options, separate analysis section
4. **Stop all tool calls.** Do not run any command, edit any file, or make any commit until the user's next chat message directly addresses the question.
5. If the user's next message does not address the question (e.g., they ask something else), do not proceed with your recommendation. Answer their question, then re-post the escalation.

Do not do exploratory work while waiting for the user's response. Wait means wait.

## Enforcement

Rules R1 and R2 are the strictest rules in this document. Violating either is treated the same as a force-push: a serious workflow breach that must be reported honestly in the next PR body (as a Provenance section) and corrected. Do not attempt to hide a violation by fixing it silently. History honest.

## Meta

This document is v2. Future updates:

- Do not update autonomously
- Any change goes through a docs branch, PR, review, and merge like any other change
- The user is the only one who can loosen a rule; Claude Code can propose loosening in a PR body's Follow-ups section
