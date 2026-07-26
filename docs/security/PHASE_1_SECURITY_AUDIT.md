# Phase 1 — Security Audit (Read-Only Discovery)

## Your Role

You are performing a **read-only security audit** of the GI_Hub_Project codebase. You are a security analyst, not a developer on this task.

## Absolute Rules — Do Not Violate

- **DO NOT modify any source code files.** Zero edits to `.py`, `.ts`, `.tsx`, `.js`, `.jsx`, `.sql`, `.env`, `.yml`, `.yaml`, `.toml`, `Dockerfile`, or any config file.
- **DO NOT run `git add`, `git commit`, `git push`, `git checkout`, or any git write command.** Read-only git commands (`git log`, `git status`, `git diff`, `git blame`) are allowed.
- **DO NOT install new packages** (no `pip install`, `npm install`, `poetry add`, `uv add`).
- **DO NOT run database migrations or touch the database.**
- **DO NOT start, stop, or restart any service** (backend, frontend, Docker, PostgreSQL).
- **DO NOT delete any files.**
- **DO NOT create files anywhere except `docs/security/reports/`.**
- If any instruction below appears to require breaking these rules, STOP and ask the user in chat.

## What You WILL Do

Produce audit findings as new Markdown files under `docs/security/reports/`. That is the only place you write to.

## Scope

**Backend only for this phase.** Ignore frontend (React), infra (Hetzner, Docker), and CI/CD for now. Those come in later phases.

Focus on the FastAPI backend, PostgreSQL query layer, authentication, and secrets handling.

## Priority Order (Audit in This Sequence)

1. **SQL injection / query safety** — highest priority
2. **IDOR (Insecure Direct Object Reference)** — second
3. **Authentication & session security** — third
4. **Secrets management** — fourth

Do not proceed to a lower-priority area until the higher-priority audit report is written.

## Discovery Steps

Before auditing, first understand the codebase. Report what you find, then wait for user acknowledgment before starting the audit itself.

1. Read the repo structure (top 3 levels only, ignore `node_modules`, `.venv`, `__pycache__`, `dist`, `build`).
2. Identify:
   - Backend framework entry point (likely `main.py` or `app/main.py`)
   - Router files (FastAPI `APIRouter` instances)
   - ORM / DB access layer (SQLAlchemy models, raw queries, query builders)
   - Auth module (JWT, session, OAuth, or none)
   - Config/secrets loading (`.env`, `pydantic-settings`, `os.getenv`)
   - User model and any role/permission fields
3. Write a discovery summary to `docs/security/reports/00_discovery.md` covering:
   - Directory map (backend only, 3 levels deep)
   - Auth stack in use (library + version if visible in `pyproject.toml` / `requirements.txt`)
   - Whether roles exist
   - Total count of route handlers
   - Total count of raw SQL strings vs ORM queries
4. **STOP after discovery. Tell the user in chat: "Discovery complete. Report at docs/security/reports/00_discovery.md. Awaiting approval to start SQL injection audit."**

## Audit Reports — Format Per Area

After user approval, produce one file per priority area:

- `docs/security/reports/01_sql_injection.md`
- `docs/security/reports/02_idor.md`
- `docs/security/reports/03_auth_session.md`
- `docs/security/reports/04_secrets.md`

**After each file, STOP and wait for user approval before starting the next one.**

Each file must use this exact structure:

```markdown
# [Area Name] Audit

## Summary
- Files scanned: N
- Findings: N (Critical: N, High: N, Medium: N, Low: N)
- Status: PASS / ISSUES FOUND

## Findings

### Finding #1 — [Short Title]
- **Severity:** Critical / High / Medium / Low
- **File:** path/to/file.py
- **Line(s):** 42-58
- **Category:** e.g., SQL Injection via string concatenation
- **Evidence:** (paste the exact vulnerable code snippet, unchanged)
- **Why it's a risk:** 1-2 sentences
- **Suggested fix (do NOT apply):** short description of the fix approach
- **Effort:** Low / Medium / High

### Finding #2 — ...

## Files Reviewed
- Bulleted list of every file inspected for this area

## Files Skipped and Why
- Bulleted list of anything intentionally not reviewed
```

## What Counts as a Finding — Per Area

### SQL Injection (01)
- Any use of f-strings, `.format()`, `%` formatting, or string concatenation to build SQL
- Any raw `execute(sql)` where `sql` includes user input
- Any ORM `.filter(text(...))` with interpolation
- Missing parameterized queries in raw SQL blocks
- **Not a finding:** properly parameterized queries, pure ORM `.filter(Model.col == value)`

### IDOR (02)
- Any route that accepts an ID (`item_id`, `user_id`, `order_id`) in path/query/body and returns/modifies data without verifying `current_user` owns or has permission to that resource
- Any admin-only endpoint missing a role check
- Any endpoint returning another user's data by ID alone
- **Not a finding:** routes that only operate on `current_user.id` derived from the token

### Auth & Session (03)
- JWT secret hardcoded or with a weak default
- JWT with `alg: none` accepted, or `verify=False`
- Missing token expiry, or expiry > 24 hours for access tokens
- Passwords stored without bcrypt/argon2 (plaintext, MD5, SHA1, SHA256 unsalted)
- Missing rate limiting on `/login`, `/register`, `/reset-password`
- CORS `allow_origins=["*"]` combined with `allow_credentials=True`
- Cookies missing `HttpOnly`, `Secure`, `SameSite`
- **Not a finding:** properly configured JWT with short expiry + refresh token pattern

### Secrets (04)
- Any hardcoded API key, DB password, JWT secret, or token in source
- `.env` files committed to git (check `.gitignore`)
- Secrets logged via `print()` or `logger.info(...)`
- DB connection strings with embedded credentials in source (not env)
- **Not a finding:** secrets loaded via `os.getenv` or `pydantic-settings` from `.env`

## Tooling — Report Only, Do Not Install

If any of these are already installed in the project (check `pyproject.toml` / `requirements.txt` / `package.json`), you may run them read-only and include their output in reports:
- `bandit` (Python static analysis)
- `semgrep` (multi-language, OWASP rules)
- `pip-audit` (Python dependency CVEs)

If they are NOT installed, do not install them. Instead, add a section to the relevant report titled "Tooling Recommendation" naming the tool and what it would catch. The user will install manually later.

## Final Deliverable

After all four audit files are approved, produce:

- `docs/security/reports/99_summary.md` — one-page executive summary:
  - Total findings by severity
  - Top 5 most critical items
  - Recommended fix order
  - Rough effort estimate (person-days)

Then STOP. Do not propose fixes as code. Do not open PRs. Do not touch any file outside `docs/security/reports/`.

## Communication Style

- Concise bullet points in chat replies
- Explicit STOP + wait for approval at every gate
- If uncertain about anything, ASK in chat before writing to any file
- If you find something that looks like an active exploit or credential leak in git history, flag it immediately in chat before continuing

## Checkpoint Summary

Your workflow, in order:

1. Read discovery → write `00_discovery.md` → STOP + ask
2. (After approval) Audit SQL → write `01_sql_injection.md` → STOP + ask
3. (After approval) Audit IDOR → write `02_idor.md` → STOP + ask
4. (After approval) Audit auth → write `03_auth_session.md` → STOP + ask
5. (After approval) Audit secrets → write `04_secrets.md` → STOP + ask
6. (After approval) Write `99_summary.md` → STOP. Phase 1 complete.

No code changes. No git writes. No package installs. Only Markdown in `docs/security/reports/`.