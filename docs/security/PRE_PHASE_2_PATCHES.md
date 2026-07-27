# Pre-Phase-2 Security Patches

## Your Role

You are applying **five targeted security patches** that were identified in Phase 1 audits and prioritized for immediate remediation. This is patch work — not audit work, not Phase 2 systemic remediation. Follow the exact scope and order below.

## Absolute Rules — Do Not Violate

- **DO NOT modify any file outside the explicit scope of the current patch.** Each patch names its files. Nothing else gets touched.
- **DO NOT run `git push`, `git push --force`, or any push variant** without explicit user approval per patch. Local commits are fine after approval.
- **DO NOT run `git rebase`, `git filter-branch`, `git filter-repo`, or any history rewrite.** History stays exactly as it is.
- **DO NOT install new packages** (no `pip install`, `npm install`, `poetry add`, `uv add`) unless the patch instructions explicitly authorize it.
- **DO NOT run database migrations, `alembic upgrade`, or `alembic downgrade`** unless the patch instructions explicitly authorize it.
- **DO NOT start, stop, or restart any service** (backend, frontend, Docker, PostgreSQL).
- **DO NOT delete any files** except where the patch instructions explicitly say to remove tracked files via `git rm --cached`.
- **DO NOT modify Phase 1 audit reports** in `docs/security/reports/`. Those are frozen paper trail.
- **DO NOT proceed to the next patch without explicit user approval** ("approved, continue" or similar).
- If any instruction below appears ambiguous or requires breaking these rules, STOP and ask the user in chat.

## Communication Style

- Concise bullet points in chat replies
- Explicit STOP + wait for approval at every gate
- Every patch ends with:
  1. Summary of what changed (files, line counts)
  2. Test results (if the patch includes tests)
  3. Proposed commit message
  4. STOP and await approval before `git commit`
  5. After commit, STOP again before mentioning push

## Patch Order (Do In This Sequence)

1. **Patch A** — Remove `gi_database.db` from git tracking
2. **Patch B** — `requests.py:83` query-param override fix
3. **Patch C** — JWT_SECRET production guard reachability
4. **Patch D** — WhatsApp webhook fail-open → fail-closed
5. **Patch E** — `PROJECT_STATUS.md` drift correction

Do not reorder. Do not skip. Do not batch. One patch at a time, one commit at a time.

## Branch Strategy

- Create a single branch for all five patches: `security/pre-phase-2-patches`
- Base off current `main` (or whatever the default branch is — confirm with user before creating)
- Each patch = one commit on this branch
- No merge, no push, no PR until all five are complete and user gives explicit approval

## Before Starting Anything

- Confirm working tree is clean (`git status` shows nothing uncommitted that isn't Phase 1 audit reports)
- Confirm current branch and offer to create `security/pre-phase-2-patches` off of it
- STOP and ask: "Ready to create branch `security/pre-phase-2-patches` off of `[current-branch]`? Confirm to proceed."

---

## Patch A — Remove `gi_database.db` from git tracking

**Source finding:** A04-C1 (Critical)

### Scope — files touched

- `.gitignore` (add explicit path entries)
- `git rm --cached gi_database.db` (removes from tracking, keeps working file)
- `git rm --cached data-archive/gi_database.20260616-211109.bak` (same)
- Any other `data-archive/gi_database.*.bak` files currently tracked

### Files NOT touched

- The actual `gi_database.db` file on disk — it stays; just untracked
- Any code that reads the DB
- Migration scripts
- Config

### Steps

1. Run `git ls-files | grep -E '\.db$|gi_database'` — list all tracked DB-related files. Paste the output.
2. STOP. Confirm with user which files to untrack. Wait for approval.
3. After approval, `git rm --cached <file>` for each approved file. Do not use `git rm` without `--cached` — that would delete the working file.
4. Add explicit path entries to `.gitignore` (in addition to the existing `*.db` glob):
```
   # Explicit — do not rely on *.db glob for already-tracked files
   /gi_database.db
   /data-archive/gi_database.*.bak
   /data-archive/*.db
```
5. Verify the working file `gi_database.db` still exists on disk (`ls -la gi_database.db`).
6. Verify `git status` shows the removal staged plus the `.gitignore` change.
7. Confirm the app still boots — but do NOT actually start the app. Just verify by reading `backend/api/config.py` and any `DATABASE_URL` references that they don't rely on the tracked path in a way that would break.

### Proposed commit message

```
security(secrets): untrack gi_database.db and archived DB backups

Removes gi_database.db (51 commits, 24 user password hashes exposed)
and data-archive/gi_database.*.bak from git tracking. Working files
remain on disk. Adds explicit path entries to .gitignore to prevent
re-staging — the existing *.db glob does not apply to already-tracked
files.

Password rotation and refresh-session revocation are handled separately
(operator task). History rewrite deliberately not performed — repo is
public and may already be mirrored; rotation is the effective control.

Refs: docs/security/reports/04_secrets.md finding A04-C1
Refs: docs/security/reports/99_summary.md §4
```

### After commit

- STOP.
- Chat message: "Patch A committed. Awaiting approval for Patch B."
- Do NOT push. Do NOT mention pushing.

---

## Patch B — `requests.py:83` query-param override fix

**Source finding:** A02-C1 (Critical) — `scope = site_id or (user["site_id"] or None)` reads site from query parameter in preference to JWT

### Scope — files touched

- `backend/api/requests.py` — the one line + any related site-scoping logic in that specific handler

### Files NOT touched

- Any other router file
- `auth.py` scoping helpers
- Any other route in `requests.py` (unless the same pattern exists and the user pre-approves widening scope)

### Steps

1. Read `backend/api/requests.py` around line 83 in full function context.
2. Identify the correct pattern by comparing to other routers that correctly use `resolve_site_param()` — read `backend/api/auth.py` for the helper signature and any 1-2 correct call sites in other routers.
3. Search for the same anti-pattern elsewhere in `requests.py`:
```
   grep -n 'or.*site_id.*or' backend/api/requests.py
   grep -n 'site_id or user' backend/api/requests.py
```
4. Paste findings. STOP. Ask user: "Found N instances of the pattern in requests.py. Fix only line 83 as originally scoped, or widen to all N? Confirm."
5. After approval, apply the fix using `resolve_site_param()` (the same helper other routers use).
6. Do NOT change function signatures, response shapes, or add new params — targeted line-level fix only.
7. Run a syntax check: `python -c "import ast; ast.parse(open('backend/api/requests.py').read())"` — must succeed.
8. Read the diff back to the user: paste `git diff backend/api/requests.py`.

### Proposed commit message

```
security(idor): use JWT site scope in requests.py, ignore query param

Replaces `scope = site_id or (user["site_id"] or None)` at line 83
with resolve_site_param(), matching the pattern used by all other
scoped routers. Previously, any authenticated user could read another
site's material requests by passing ?site_id=X in the query string,
bypassing the JWT-derived scope.

Function signature and response shape unchanged. No callers affected.

Refs: docs/security/reports/02_idor.md finding A02-C1 (or F1, whichever)
Refs: docs/security/reports/99_summary.md §4
```

### After commit

- STOP.
- Chat message: "Patch B committed. Awaiting approval for Patch C."

---

## Patch C — JWT_SECRET production guard reachability

**Source finding:** A03-H1 (High) — production boot check exists but is armed only by `GI_ENV`, which is set only by `docker-compose.prod.yml`

### Scope — files touched

- `deploy/.env.example` — add `GI_ENV=prod` (commented out with instructions)
- `Dockerfile` — add `ENV GI_ENV=prod` OR document why not
- `run_api.sh` — add `export GI_ENV=prod` at the top OR document why not
- Optionally: `backend/api/config.py` — tighten the accepted-value list for GI_ENV if the same triple-check bug from A03-M can be fixed here trivially (STOP and ask before touching this file)

### Files NOT touched

- `docker-compose.prod.yml` (already correct)
- Any auth or JWT code
- Any secret files (obviously)

### Steps

1. Read `deploy/.env.example`, `Dockerfile`, `run_api.sh` in full.
2. Read `backend/api/config.py` for the exact GI_ENV check logic.
3. Read `backend/api/auth.py` around the `is_production()` function and the cookie logic (A03 flagged an inconsistency here — check whether fixing it fits Patch C scope or should defer to Phase 2 Theme C).
4. Paste your reading summary. STOP. Ask user: "Confirming Patch C scope: (a) add GI_ENV=prod to deploy/.env.example commented with instructions, (b) add ENV GI_ENV=prod to Dockerfile, (c) add export GI_ENV=prod to run_api.sh. Should I also apply the is_production() unification, or defer to Phase 2 Theme C? Confirm."
5. After approval, apply exactly what was approved.
6. Do NOT change JWT_SECRET itself. Do NOT modify the boot-check logic. Do NOT rewrite `is_production()` unless user explicitly approved that scope.
7. Read back the diffs.

### Proposed commit message

```
security(config): ensure GI_ENV=prod is set on non-compose deploys

The JWT_SECRET production guard (imported at boot) is armed only when
GI_ENV=prod. Previously, GI_ENV was set only by docker-compose.prod.yml,
meaning any bare-metal deploy (Dockerfile direct, run_api.sh, manual
uvicorn) would boot without the guard — potentially on the dev-default
secret published in this public repo.

Changes:
- deploy/.env.example: adds documented GI_ENV=prod line
- Dockerfile: sets ENV GI_ENV=prod
- run_api.sh: exports GI_ENV=prod at top

is_production() string-accept unification deferred to Phase 2 Theme C.

Refs: docs/security/reports/03_auth_session.md finding A03-H1
Refs: docs/security/reports/99_summary.md §4
```

### After commit

- STOP.
- Chat message: "Patch C committed. Awaiting approval for Patch D."

---

## Patch D — WhatsApp webhook fail-open → fail-closed

**Source finding:** A03-M2 (originally High, revised to Medium after A04 confirmed `WHATSAPP_APP_SECRET` is populated) — HMAC check returns `True` when `WHATSAPP_APP_SECRET` is unset

### Scope — files touched

- `backend/api/webhook.py` — the specific fail-open branch in the HMAC verification function

### Files NOT touched

- Any other webhook logic
- The rate limiter
- Any WhatsApp integration code outside the signature check

### Steps

1. Read `backend/api/webhook.py` in full — need to understand the whole verification function, not just the flagged line.
2. Identify the exact conditional that returns `True` when the secret is unset.
3. Flip it to fail-closed: when `WHATSAPP_APP_SECRET` is unset, return `False` (or raise, matching the surrounding pattern) and log a WARNING (not the secret value, obviously — just "WhatsApp signature check skipped: WHATSAPP_APP_SECRET not configured").
4. Verify constant-time comparison (`hmac.compare_digest`) is still used on the actual check path — this was correct per A03, must not regress.
5. Read back the diff.

### Proposed commit message

```
security(webhook): fail-closed on unset WHATSAPP_APP_SECRET

The HMAC signature verification for the WhatsApp webhook previously
returned True when WHATSAPP_APP_SECRET was unset, allowing any
unauthenticated POST to invoke webhook handlers (including the
password-reset flow keyed by phone number).

Now returns False and logs a warning naming only the missing config
variable. The secret is currently populated in deploy/.env (verified
A04-M2), so the shipped code path in production is unchanged — this
closes the failure mode for any future deploy where the secret is
unset or accidentally cleared.

hmac.compare_digest usage unchanged.

Refs: docs/security/reports/03_auth_session.md finding A03-H2 (revised to Medium)
Refs: docs/security/reports/99_summary.md §4
```

### After commit

- STOP.
- Chat message: "Patch D committed. Awaiting approval for Patch E."

---

## Patch E — `PROJECT_STATUS.md` drift correction

**Source finding:** A04-M — status file misreports which controls are configured

### Scope — files touched

- `PROJECT_STATUS.md` §3 (WhatsApp secrets status)
- `PROJECT_STATUS.md` §3 (Meta token incident note)
- Any other §3 lines the user flags as drifted

### Files NOT touched

- Anything outside `PROJECT_STATUS.md`
- Historical content in other sections unless the user asks

### Steps

1. Read `PROJECT_STATUS.md` §3 in full. Paste the current text of the affected lines.
2. STOP. Ask user: "Confirming the two corrections: (a) WHATSAPP_APP_SECRET and WHATSAPP_WEBHOOK_VERIFY_TOKEN are populated in deploy/.env, not open TODOs; (b) the Meta token was never committed — .env.example history is clean (single commit, placeholders only). Should I apply exactly these two corrections, or is there other drift in §3 to fix in the same commit? Confirm."
3. After approval, edit the file. Preserve the surrounding structure and tone.
4. Add a small note at the end of §3: `Last verified against reality: 2026-07-26 via Phase 1 Audit 04.`
5. Read back the diff.

### Proposed commit message

```
docs(status): correct PROJECT_STATUS.md §3 drift

Two corrections to §3 based on Phase 1 Audit 04 findings:

1. WHATSAPP_APP_SECRET and WHATSAPP_WEBHOOK_VERIFY_TOKEN are populated
   in deploy/.env — previously listed as open operator TODOs.
2. The Meta token was never committed to git. .env.example has exactly
   one commit in its full history (f3d706b, 2026-06-30) containing
   only placeholder values. No history rewrite needed.

Adds a "last verified against reality" timestamp so future drift is
detectable.

Structural fix (startup diagnostic comparing documented vs actual
secret state) queued for Phase 2 Theme D.

Refs: docs/security/reports/04_secrets.md
Refs: docs/security/reports/99_summary.md §4
```

### After commit

- STOP.
- Chat message: "Patch E committed. All five pre-Phase-2 patches complete on branch security/pre-phase-2-patches. Awaiting approval to push and open PR."

---

## After All Five Patches Are Committed

Do NOT push automatically. Do NOT open a PR automatically.

Chat message to user:

```
All 5 patches committed on branch security/pre-phase-2-patches.

Commits (oldest to newest):
- security(secrets): untrack gi_database.db and archived DB backups
- security(idor): use JWT site scope in requests.py, ignore query param
- security(config): ensure GI_ENV=prod is set on non-compose deploys
- security(webhook): fail-closed on unset WHATSAPP_APP_SECRET
- docs(status): correct PROJECT_STATUS.md §3 drift

Next steps require your explicit approval:
1. Push branch to origin? (git push -u origin security/pre-phase-2-patches)
2. Open PR? (gh pr create or manual on GitHub)

Awaiting approval.
```

Then STOP. Do not push. Do not open PR. Do not run `gh` commands. Wait for user.

---

## Operator Tasks — Outside Claude Code's Scope

The following are NOT part of this patch prompt. User will handle manually. Do NOT attempt any of these:

- Password rotation for the 24 exposed user accounts + 21 archive accounts
- Force-revoke every refresh session family (one SQL UPDATE against `refresh_sessions`)
- Verifying the working `gi_database.db` file's actual on-disk permissions and location
- Decision on whether to eventually rewrite history (deferred; rotation is the control)
- Adding the 7 sensitive tables to `create_ai_readonly_role.sql` REVOKE list — deferred to Phase 2 Theme B, not part of this patch set

If the user asks you to do any of these, STOP and confirm the scope expansion explicitly before proceeding.

---

## Regression Prevention — Non-Negotiable

Every patch must:

- Preserve function signatures — no new required params
- Preserve response shapes — no field renames or removals
- Preserve import order and module structure
- Preserve indentation and coding style of the surrounding code (2-space vs 4-space, snake_case vs camelCase — match what's there)
- Not add new dependencies to `requirements.txt`, `pyproject.toml`, or `package.json`
- Not modify tests unless the patch explicitly requires it (none of A-E should)

If any patch turns out to require a signature change, response shape change, or new dependency, STOP and ask the user before proceeding.

## Checkpoint Summary

Your workflow, in strict order:

1. Confirm clean working tree → offer to create branch → STOP + ask
2. (After approval) Patch A → commit → STOP + ask
3. (After approval) Patch B → commit → STOP + ask
4. (After approval) Patch C → commit → STOP + ask
5. (After approval) Patch D → commit → STOP + ask
6. (After approval) Patch E → commit → STOP + ask
7. Final summary → STOP for push/PR approval

No pushes. No PRs. No history rewrite. No package installs. No service restarts. Five commits on one branch, waiting for user review.