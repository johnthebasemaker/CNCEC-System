# Operator Tasks — Post Pre-Phase-2 Patches

## Your Role

You are executing **operator security tasks** identified in Phase 1 audits. Unlike the audit (read-only) and patch (code-only) phases, this phase involves **live database writes, credential generation, and production configuration edits**. Every destructive action requires explicit user approval.

## Context — Read This First

- PR #3 (`security/pre-phase-2-patches`) is open, not merged. These operator tasks close current exposure so that PR can safely merge.
- `gi_database.db` was in git history until commit `a09da0b` untracked it. The 24 user password hashes it contained may already be scraped by anyone monitoring the public repo.
- The user is on-site in Saudi Arabia on 12-hour shifts. Every task must be executable in a single sitting or resumable across sittings without ambiguity.

## Absolute Rules — Do Not Violate

- **DO NOT execute any destructive SQL** (`UPDATE`, `INSERT`, `DELETE`, `TRUNCATE`, `DROP`, `ALTER`, `GRANT`, `REVOKE`) without prior explicit user approval for that specific statement.
- **DO NOT connect to the production database** unless the user has explicitly confirmed you should. Default assumption: dev DB only (127.0.0.1:5433 per `config.py`).
- **DO NOT edit `deploy/.env` without approval.** This file holds live credentials.
- **DO NOT commit anything to git.** All work in this prompt is local operational work — code changes (Task 6) go to a separate branch and are committed only with explicit approval.
- **DO NOT push, open PRs, or run `gh` commands.**
- **DO NOT print any secret value in chat.** No JWT_SECRET, no bcrypt hash, no phone number, no token. When you need to show generated values to the user for pasting into `deploy/.env`, generate them into a file at `/tmp/secrets_to_paste.txt` (chmod 600) and tell the user the filename — do NOT paste to chat.
- **DO NOT proceed to the next task without explicit user approval** for the current task's completion.
- **DO NOT run any task marked "USER-ONLY".** If a task says the user must do it themselves (e.g., deliver temp passwords to actual humans), stop at the handoff point.
- If any instruction below appears ambiguous or requires breaking these rules, STOP and ask the user in chat.

## Communication Style

- Concise bullet points in chat replies
- Explicit STOP + wait for approval at every destructive action
- For SQL: always paste the exact statement + expected row count + confirmation gate before running
- For file edits: always show the diff before writing
- Report the DB connection string in use (with password redacted) at the start of every DB task

## Task Order (Do In This Sequence)

1. **Task 0** — Pre-flight discovery (read-only)
2. **Task 1** — Revoke all refresh session families
3. **Task 2** — Force-reset the 24 exposed user passwords
4. **Task 3** — Rotate or disable the 21 archive-account passwords
5. **Task 4** — Rotate `JWT_SECRET` and `POSTGRES_PASSWORD`
6. **Task 5** — Add REVOKEs for the 7 sensitive tables to `create_ai_readonly_role.sql`
7. **Task 6** — Docker image spot-check for `.env` leakage

Do not reorder. Do not skip. Do not batch.

---

## Task 0 — Pre-Flight Discovery (Read-Only)

Before touching anything, establish the environment and confirm expectations match reality.

### Steps

1. Report the database connection string being used, with password redacted:
```
   Environment variable DATABASE_URL: postgresql+asyncpg://gihub_user:*****@HOST:PORT/DBNAME
```
   If the user must approve production access, this line makes the target unambiguous.

2. Confirm you can connect and it's the expected database:
```sql
   SELECT current_database(), current_user, inet_server_addr(), inet_server_port();
```

3. Confirm the tables that will be affected exist and have the expected shape:
```sql
   SELECT COUNT(*) AS active_refresh_families
   FROM refresh_sessions
   WHERE is_revoked IS FALSE;

   SELECT COUNT(*) AS user_count, COUNT(password_hash) AS with_hash
   FROM users;

   SELECT column_name, data_type
   FROM information_schema.columns
   WHERE table_name = 'users'
   ORDER BY ordinal_position;
```

4. Confirm the schema has (or lacks) a `must_reset_password` column. Report yes/no. This decides Task 2 approach.

5. Report `deploy/.env` presence and file mode (without printing contents):
```bash
   ls -la deploy/.env
   stat -c '%a %U:%G' deploy/.env 2>/dev/null || stat -f '%p %Su:%Sg' deploy/.env
```

6. Report which `create_ai_readonly_role.sql` REVOKE lines are already present (grep-only, no execution).

### STOP

Paste the discovery report and ask:

> **Discovery complete. Which environment am I connected to (dev / staging / production)?**
> Please confirm before I proceed to Task 1. All subsequent tasks assume this same target.

Wait for user to explicitly name the environment. Do not assume.

---

## Task 1 — Revoke All Refresh Session Families

**Purpose:** Invalidate every outstanding refresh token so any pre-existing session cannot be used, even if a token was derived from the leaked hash data.

### Pre-check (read-only)

```sql
-- Confirm target count
SELECT COUNT(*) AS to_revoke
FROM refresh_sessions
WHERE is_revoked IS FALSE;

-- Sample the newest 3 to sanity-check timestamps (no PII in output)
SELECT family_id, created_at, client_type
FROM refresh_sessions
WHERE is_revoked IS FALSE
ORDER BY created_at DESC
LIMIT 3;
```

Report counts and sample. STOP.

### Ask for approval

> **About to run:**
> ```sql
> UPDATE refresh_sessions
> SET is_revoked = TRUE,
>     revoked_at = NOW(),
>     revoked_reason = 'phase-1-mass-revoke-post-audit'
> WHERE is_revoked IS FALSE;
> ```
>
> **Expected: N rows updated (from pre-check count).**
> **Effect: every logged-in user will be forced to re-authenticate on next request.**
>
> Approve to run? (yes / no)

Wait for explicit `yes`. Do not accept "sure", "ok", or "proceed" — require the exact word to prevent accidental approval.

### After approval

1. Run the UPDATE.
2. Report actual rows affected.
3. Verify with:
```sql
   SELECT COUNT(*) FROM refresh_sessions WHERE is_revoked IS FALSE;
```
   Should return 0.
4. If any row remains unrevoked, STOP and report — do not attempt a second UPDATE without approval.

### STOP

Report Task 1 complete with the row count, and await approval to start Task 2.

---

## Task 2 — Force-Reset the 24 Exposed User Passwords

**Purpose:** The 24 password hashes in `gi_database.db` are potentially compromised. Force every user to set a new password on next login.

### Approach depends on Task 0 discovery

**If `must_reset_password` column exists:**
- Use it directly. UPDATE all 24 users to `must_reset_password = TRUE`. Login flow already redirects.

**If it does not exist:**
- Two sub-options, present to user and let them choose:
  - **Option A:** Add the column via a migration, then UPDATE. Cleanest, matches your Alembic pattern.
  - **Option B:** Generate 24 strong temporary passwords, bcrypt-hash them at cost 12, UPDATE `users.password_hash` directly. User delivers temp passwords out-of-band (WhatsApp / in-person).

### Pre-check (read-only)

```sql
-- Which users are affected? Match against the 24 hashes that were in gi_database.db.
-- If you can't cross-reference (unlikely — the leaked file may not be readable), treat ALL current users as potentially exposed.
SELECT COUNT(*) FROM users WHERE role != 'disabled';

-- Sample a few usernames + roles + sites (no phone, no hash)
SELECT username, role, "Site_ID"
FROM users
WHERE role != 'disabled'
ORDER BY username
LIMIT 5;
```

Report the counts. STOP.

### Ask for approach

> **Discovery-dependent decision. Column `must_reset_password` exists: [yes/no].**
>
> **Recommended approach:**
> - If column exists: UPDATE flag for all N non-disabled users. Login redirects them.
> - If column does not exist:
>   - **A (migration + flag):** cleanest, mirrors existing Alembic pattern. ~30 min work.
>   - **B (direct password reset with temp passwords):** faster (10 min), but requires you to deliver 24 passwords manually via WhatsApp/in-person.
>
> **Which do you want?** (A / B / or "add column first, then flag")

Wait for user choice.

### If Option A (column + flag)

1. Create a new Alembic migration in `backend/alembic/versions/` following the existing naming pattern (check the most recent migration file for template).
2. The migration should:
   - Add `must_reset_password BOOLEAN NOT NULL DEFAULT FALSE` to `users`
   - Have proper `upgrade()` and `downgrade()`
3. Show the migration file diff to user for approval BEFORE running `alembic upgrade`.
4. After user approves the migration file, ask separately for approval to run `alembic upgrade head`.
5. After migration succeeds, show the UPDATE statement:
```sql
   UPDATE users SET must_reset_password = TRUE WHERE role != 'disabled';
```
6. Ask for approval, then run.
7. Verify:
```sql
   SELECT COUNT(*) FROM users WHERE must_reset_password = TRUE;
```

### If Option B (direct temp passwords)

1. Generate 24 strong temporary passwords (16 chars, alphanumeric + safe symbols) into `/tmp/temp_passwords.txt` with format:
```
   username1<TAB>tempPass1
   username2<TAB>tempPass2
   ...
```
   `chmod 600` the file. Do NOT paste to chat.
2. In a Python script (do NOT run inline — save as `/tmp/hash_temps.py`), bcrypt-hash each password at cost 12 using the project's existing bcrypt library.
3. Generate the UPDATE statements (parameterized) into `/tmp/reset_users.sql`.
4. Show the SQL file structure (not values) to the user for review.
5. Ask for approval to run.
6. After running, verify:
```sql
   SELECT username, LENGTH(password_hash) AS hash_len
   FROM users
   WHERE role != 'disabled'
   ORDER BY updated_at DESC
   LIMIT 5;
```
   Hash length should be 60 (bcrypt). Do NOT print hashes.
7. Tell the user: **temp passwords are in `/tmp/temp_passwords.txt` — deliver to each user via WhatsApp or in-person, then delete the file.**

### STOP

Report Task 2 complete. If Option B, remind the user about the temp password file cleanup:
```bash
shred -u /tmp/temp_passwords.txt   # or: rm -P /tmp/temp_passwords.txt on macOS
```

Await approval to start Task 3.

---

## Task 3 — Rotate or Disable the 21 Archive Users

**Purpose:** The 21 users in `data-archive/gi_database.20260616-211109.bak` may include former employees whose accounts should be disabled, not reset.

### Pre-check

The archive file is untracked in git but the working copy is on disk. Read the archive DB read-only using SQLite's `mode=ro`:

```bash
sqlite3 'file:data-archive/gi_database.20260616-211109.bak?mode=ro&immutable=1' \
  "SELECT username, role FROM users;"
```

Cross-reference with the current `users` table:

```sql
-- Which archive users still exist in production?
-- Feed the archive usernames in as a list; report which are present and their current role.
```

Report:
- N archive users still present in current DB → potentially need reset
- N archive users NOT in current DB → already gone, nothing to do
- Of the present ones, N are currently `role = 'disabled'` → nothing to do
- Of the present ones, N are currently active → need decision

### Ask for approach

> **Archive user analysis:**
> - Present + active: N (need action)
> - Present + already disabled: N (skip)
> - Not in current DB: N (skip)
>
> **For the N present + active archive users, which action?**
> - **Disable** (set `role = 'disabled'`) — recommended for former employees
> - **Reset password** (same as Task 2) — for still-active employees
> - **Per-user decision** — I'll list each username + role + last-login timestamp and you decide one by one

Wait for user choice.

### Execute per approval

For each action, show the SQL, get approval, run, verify.

### STOP

Report Task 3 complete. Await approval for Task 4.

---

## Task 4 — Rotate `JWT_SECRET` and `POSTGRES_PASSWORD`

**Purpose:** Both are currently `CHANGE_ME` in `deploy/.env` per Audit 04-M. Must be strong values before production traffic hits the deployed app.

### Environment check

Confirm the target `deploy/.env` file:
```bash
ls -la deploy/.env
```

**Warn user if this is a production deploy path** — replacing `JWT_SECRET` on production requires a service restart, which will drop all connections. Ask them to confirm they can accept the downtime window before proceeding.

### Generate secrets

Do NOT print values to chat. Generate into a temp file:

```bash
cat > /tmp/secrets_to_paste.txt <<'EOF'
# Generated for deploy/.env update — paste these values in, then delete this file.

JWT_SECRET=$(openssl rand -base64 64 | tr -d '\n')
POSTGRES_PASSWORD=$(openssl rand -base64 32 | tr -d '/+=\n' | head -c 32)
EOF
chmod 600 /tmp/secrets_to_paste.txt
```

Actually run this and populate the file. Tell the user:

> **Two strong secrets generated at `/tmp/secrets_to_paste.txt`.**
>
> To review before applying:
> ```bash
> cat /tmp/secrets_to_paste.txt
> ```
>
> **Next: I'll edit `deploy/.env` in-place to replace both `CHANGE_ME` values. Approve?** (yes / no)

### Apply

After approval, edit `deploy/.env`:
1. Read current `deploy/.env` (do NOT print contents).
2. Replace the `JWT_SECRET=CHANGE_ME` line with the new value.
3. Replace the `POSTGRES_PASSWORD=CHANGE_ME` line with the new value.
4. Confirm the file no longer contains `CHANGE_ME` for either variable:
```bash
   grep -c CHANGE_ME deploy/.env
```
   Report the count (was 2, should now be 0 for these two vars).

### Post-write reminders

Tell the user:

- **DB password change requires PostgreSQL user password sync.** If `POSTGRES_PASSWORD` is used by the running Postgres container/service, you must also `ALTER USER` in the DB or restart the container so it picks up the new password from the environment.
- **JWT_SECRET change invalidates every issued JWT.** All users are logged out (again). Task 1's family revoke already did this in effect — this just closes the last edge case.
- **Delete the temp file:**
```bash
  shred -u /tmp/secrets_to_paste.txt
```
- **Do NOT commit `deploy/.env`** (it's already gitignored — verify with `git check-ignore -v deploy/.env`).

### STOP

Report Task 4 complete. Await approval for Task 5.

---

## Task 5 — Add REVOKEs for 7 Sensitive Tables

**Purpose:** The `gi_ai_ro` role can read `bug_reports`, `email_outbox`, `employees`, `entry_attachments`, `phone_otp`, `system_audit_log`, `whatsapp_outbox`. Closes A01-F2 at the DB layer even before the safety gate rewrite lands in Phase 2 Theme B.

### Branch setup

This task involves a code change to a script + a live SQL run. Create a new branch:

```bash
git checkout main
git pull  # if user approves — otherwise skip
git checkout -b security/ai-readonly-sensitive-tables
```

STOP after branch creation to confirm.

### Edit the script

Add to `backend/scripts/create_ai_readonly_role.sql`:

```sql
-- Sensitive tables — Audit 01 F2, added post-Phase-1
REVOKE SELECT ON public.bug_reports FROM gi_ai_ro;
REVOKE SELECT ON public.email_outbox FROM gi_ai_ro;
REVOKE SELECT ON public.employees FROM gi_ai_ro;
REVOKE SELECT ON public.entry_attachments FROM gi_ai_ro;
REVOKE SELECT ON public.phone_otp FROM gi_ai_ro;
REVOKE SELECT ON public.system_audit_log FROM gi_ai_ro;
REVOKE SELECT ON public.whatsapp_outbox FROM gi_ai_ro;
```

Placement: after the existing REVOKE block, before any GRANT block. Match indentation. Add a preceding blank line + comment.

Show the diff. STOP.

### Ask for approval to run against live DB

> **Two-part approval needed:**
>
> **Part A — code:** commit the script edit to branch `security/ai-readonly-sensitive-tables`. Commit message:
> ```
> security(ai-lane): revoke gi_ai_ro read on 7 sensitive tables
>
> Closes A01-F2 at the DB layer. Applies REVOKE SELECT for tables
> containing user PII, message bodies, and audit history — none of
> which the AI NL→SQL lane needs. Complements the safety gate
> rewrite queued in Phase 2 Theme B.
>
> Refs: docs/security/reports/01_sql_injection.md finding A01-F2
> Refs: docs/security/reports/99_summary.md §5 Theme B
> ```
>
> **Part B — live DB:** run the same 7 REVOKE statements against the connected database now, so the wall goes up immediately without waiting for the next mirror reload.
>
> Approve A only, B only, both, or neither?

### After approval

- Part A: commit + STOP for push approval later.
- Part B: run the 7 REVOKEs one at a time, verify each with:
```sql
  SELECT COUNT(*)
  FROM information_schema.role_table_grants
  WHERE grantee = 'gi_ai_ro'
    AND privilege_type = 'SELECT'
    AND table_name IN (
      'bug_reports', 'email_outbox', 'employees',
      'entry_attachments', 'phone_otp',
      'system_audit_log', 'whatsapp_outbox'
    );
```
  Should return 0 after the REVOKEs.

### STOP

Report Task 5 complete. Await approval for Task 6.

---

## Task 6 — Docker Image `.env` Leakage Spot-Check

**Purpose:** Verify the `.dockerignore` `**/.env` pattern added in Patch C actually excludes `deploy/.env` from built images.

### Steps

1. Check if Docker is available in this environment:
```bash
   which docker && docker --version
```
   If not available, STOP and tell user to run this task on a machine with Docker installed.

2. If available, build the image:
```bash
   docker build -t gi-hub-api-secret-check -f deploy/Dockerfile.api .
```

3. Inspect the built image for `.env` files:
```bash
   docker run --rm --entrypoint sh gi-hub-api-secret-check -c \
     'find /app -name ".env" -not -name ".env.example" 2>/dev/null'
```

4. Expected: **no output** (empty result). Any `.env` in the image is a failure.

5. Also check the image layer history for `.env`:
```bash
   docker history gi-hub-api-secret-check --no-trunc | grep -i env
```

6. Clean up the test image:
```bash
   docker rmi gi-hub-api-secret-check
```

### STOP

Report Task 6 complete with pass/fail. If fail, do NOT attempt to fix — flag for a separate patch session.

---

## After All Tasks Are Complete

Do NOT push. Do NOT open a PR.

Chat message to user:

```
All 6 operator tasks complete on [environment name].

Summary:
- Task 1: refresh session families revoked (N rows)
- Task 2: password reset approach = [A/B]; N users flagged/updated
- Task 3: archive users [disabled/reset/mixed]; N affected
- Task 4: JWT_SECRET + POSTGRES_PASSWORD rotated in deploy/.env
- Task 5: 7 REVOKEs added to script (branch security/ai-readonly-sensitive-tables); DB [applied / not applied]
- Task 6: Docker image .env leakage check [pass / fail / skipped]

Pending user actions:
- If Option B in Task 2: deliver temp passwords from /tmp/temp_passwords.txt to users, then shred the file
- Delete /tmp/secrets_to_paste.txt after confirming deploy/.env values persist
- Restart backend service to pick up new JWT_SECRET and POSTGRES_PASSWORD
- If Task 5 branch created: decide whether to push and merge, or defer to Phase 2 Theme B

Awaiting approval for:
1. Push security/ai-readonly-sensitive-tables to origin?
2. Merge PR #3 (security/pre-phase-2-patches) into main?
```

Then STOP.

---

## Regression Prevention — Non-Negotiable

- Every SQL UPDATE / INSERT / DELETE / ALTER / REVOKE gets approval per statement, not per batch
- No signature changes to any table
- No schema migrations except the one explicitly approved in Task 2 Option A
- No new dependencies
- No touching production without explicit environment confirmation from Task 0
- No printing of any secret value, hash, phone number, or credential in chat
- If a task requires reading `deploy/.env`, do not print its contents — count matches, grep for placeholders only

## Checkpoint Summary

Your workflow, in strict order:

1. Task 0 discovery → environment confirmation gate → STOP
2. Task 1 revoke families → per-statement approval → STOP
3. Task 2 password reset → approach choice → per-statement approval → STOP
4. Task 3 archive users → per-user or batch approval → STOP
5. Task 4 rotate secrets → temp file → in-place edit approval → STOP
6. Task 5 REVOKE additions → branch + commit + optional DB run → STOP
7. Task 6 Docker spot-check → pass/fail report → STOP
8. Final summary → STOP for push/merge decisions

No production writes without environment confirmation. No secret values in chat. No merging PR #3 automatically. Six tasks, one approval per destructive action.