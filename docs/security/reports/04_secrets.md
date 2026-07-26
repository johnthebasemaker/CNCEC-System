# Secrets Management Audit

## Summary
- Files scanned: working tree (`.env*`, `deploy/`, Docker manifests, `.gitignore`, `.dockerignore`, all backend config/service modules) + **full git history across all refs**
- Findings: 8 (Critical: 1, High: 0, Medium: 4, Low: 3)
- Status: **ISSUES FOUND**

**Headline — the good news first, because it reframes the incident in `PROJECT_STATUS.md`:** the Meta token that reportedly reached `.env.example` **was never committed**. `.env.example` has exactly one commit in its entire history (`f3d706b`, 2026-06-30) and that version contains only `PASTE_YOUR_...` placeholders. Every high-signal prefix search — `EAAG`, `EAAB`, `sk-ant-`, `ghp_`, `AKIA`, and all three private-key headers — returned **zero** genuine hits. Both `.env.example` files in the working tree are clean placeholder-only right now.

**The bad news is elsewhere, and it is worse:** the live SQLite database `gi_database.db` is **tracked in this public repository**, across 51 commits, and the blob at `HEAD` contains a `users` table with **24 bcrypt password hashes** and **93 phone numbers**. A second tracked file, `data-archive/gi_database.20260616-211109.bak`, holds 21 more hashes. `.gitignore` lists `*.db`, which does nothing for files that were already tracked — so the rule reads as protection while the commits continued.

---

## Part 1 — Git history scan (executed first, per the brief)

### Commands run (read-only)

```
git log --all --full-history -p -- .env.example
git log --all --full-history -p -- deploy/.env.example
git log --all -S'EAAG' / -S'EAAB' / -S'EAA'
git log --all -S'sk-ant-' / -S'sk-' / -S'ghp_' / -S'AKIA'
git log --all -S'JWT_SECRET=' / -S'DATABASE_URL='
git log --all -S'password=' / -S'PASSWORD=' / -S'SMTP_PASSWORD'
git log --all -S'ANTHROPIC_API_KEY' / -S'OPENAI_API_KEY'
git log --all -S'BEGIN RSA PRIVATE KEY' / -S'BEGIN OPENSSH PRIVATE KEY' / -S'BEGIN PRIVATE KEY'
git ls-files --error-unmatch .env / deploy/.env
git config --get-regexp '^remote\..*\.url'
```

### Results

| Search | Commits | Verdict |
|---|---|---|
| `.env.example` full history | 1 (`f3d706b`, 2026-06-30) | **CLEAN** — placeholders only (see below) |
| `deploy/.env.example` full history | 8 | **CLEAN** — every version placeholder-only |
| `EAAG` (Meta long-lived token) | **0** | Clean |
| `EAAB` (Meta app secret) | **0** | Clean |
| `EAA` (broad) | 5 | **All false positives** — 2 are the documentation string ``Meta token prefix (`EAA…`)`` in `ARCHITECTURE.md`/`PROJECT_STATUS.md`; 3 are byte matches inside binary blobs (`Equipment.xlsx`, `GI_Hub_User_Manual.pdf`, `.DS_Store`, `gi_database.db`) |
| `sk-ant-` (Anthropic) | **0** | Clean |
| `ghp_` (GitHub PAT) | **0** | Clean |
| `AKIA` (AWS) | **0** | Clean |
| `BEGIN RSA/OPENSSH/PRIVATE KEY` | **0** each | Clean |
| `SMTP_PASSWORD`, `ANTHROPIC_API_KEY`, `OPENAI_API_KEY` | **0** each | Clean |
| `sk-` (generic) | 17 | All false positives — substring of ordinary words (`task-`, `disk-`, `risk-`) in prose and code |
| `JWT_SECRET=` | 9 | All are the **CI test value** or `${VAR}` references — see Finding #4 |
| `DATABASE_URL=` | 19 | All are local/CI URLs (`postgresql+psycopg2://postgres@127.0.0.1:5433/gihub`, no password) or `${VAR}` interpolation |
| `password=` / `PASSWORD=` | 3 / 2 | All `${POSTGRES_PASSWORD}` references or `CHANGE_ME` placeholders in `.env.example` |
| `.env` / `deploy/.env` tracked? | — | **Never tracked** — `git ls-files --error-unmatch` errors for both |
| `.git/config` remote URL | — | `https://github.com/johnthebasemaker/GI_Hub_Project.git` — **no embedded credentials** |

**The only historical version of `.env.example`, verbatim** (`f3d706b`, 2026-06-30):

```
META_PHONE_NUMBER_ID=PASTE_YOUR_PHONE_NUMBER_ID
META_ACCESS_TOKEN=PASTE_YOUR_PERMANENT_SYSTEM_USER_TOKEN
META_WEBHOOK_VERIFY_TOKEN=PASTE_A_RANDOM_STRING_YOU_CHOOSE
META_APP_SECRET=PASTE_YOUR_META_APP_SECRET
```

**Conclusion on the `PROJECT_STATUS.md` §4 incident:** the note reads *"a real token briefly hit `.env.example` and was blanked pre-commit."* The history confirms **pre-commit** is accurate — the token never entered a commit object on any reachable ref. **No Meta token is in git history. No revocation or history rewrite is required on that basis.**

> Caveat stated plainly: `git log --all` covers reachable refs. A secret could in principle survive in an unreachable dangling object (from an amended or reset commit) that `--all` does not traverse. Confirming that requires `git fsck --lost-found` plus inspection of dangling blobs, which is read-only and safe but was not run here as it falls outside the brief's command list. Worth adding to the `trufflehog` pass, which walks the full object database.

---

## Findings

### Finding #1 — The live SQLite database, containing password hashes and staff PII, is tracked in a public repository
- **Severity:** Critical
- **File:** `gi_database.db` (repo root) · `data-archive/gi_database.20260616-211109.bak`
- **Line(s):** n/a — binary blobs; 51 commits touching `gi_database.db`, most recent `2cdcdc6` (2026-07-18)
- **Category:** Credential and PII disclosure via committed database artifact
- **Evidence:** all values below were counted, never printed.

  ```
  $ git ls-files --error-unmatch gi_database.db
  gi_database.db                                     ← TRACKED

  $ git check-ignore -v gi_database.db
  (no output)                                        ← NOT ignored, despite `*.db` in .gitignore

  $ git log --all --oneline -- gi_database.db | wc -l
  51

  $ git cat-file -s $(git rev-parse HEAD:gi_database.db)
  1179648                                            ← 1.1 MB blob at HEAD

  $ git show HEAD:gi_database.db | strings | grep -oE "CREATE TABLE users[^)]{0,80}"
  CREATE TABLE users (

  $ git show HEAD:gi_database.db | strings | grep -cE '\$2[aby]\$[0-9]{2}\$'
  24                                                 ← bcrypt password hashes

  $ git show HEAD:gi_database.db | strings | grep -coE '\+[0-9]{9,15}'
  93                                                 ← E.164 phone numbers

  $ git show HEAD:data-archive/gi_database.20260616-211109.bak | strings | grep -cE '\$2[aby]\$[0-9]{2}\$'
  21                                                 ← a second copy, older vintage
  ```

  **Why `.gitignore` did not prevent this:** line 7 of `.gitignore` is `*.db`, but git ignore rules apply only to *untracked* files. `gi_database.db` was first committed long before that rule mattered and has been re-committed 51 times since. `REPO_MAP.md:29` states the intended policy — *"Deliberately NOT moved (and never staged — it is live, constantly-modified data)"* — and `PROJECT_STATUS.md` §4 repeats *"never stage it"*. The policy is correct; it is simply not enforced by anything, and the file is in fact staged.

  **Mitigating factors, stated fairly:** the hashes are bcrypt at cost 12 (verified in Audit 03), so they are not directly usable and resist bulk cracking. **Zero** TOTP secrets are populated (`grep -cE '^[A-Z2-7]{32}$'` → 0), so 2FA seeds are not exposed. `demo_seed.db` contains no hashes.

  **Aggravating factors:** the repository is **public** (`johnthebasemaker/GI_Hub_Project`, per `PROJECT_STATUS.md` §0), so this is world-readable and may already be cloned, forked, or indexed by third-party mirrors. Audit 03 Finding #11 established that the minimum password length is **6 characters with no complexity or breach screening** — 6-character passwords against bcrypt cost 12 are economically crackable for any attacker willing to spend GPU time on a 24-hash target list, and password reuse extends the damage beyond this application. The 93 phone numbers are staff and employee personal data with GDPR/PDPL implications independent of the hashes.
- **Why it's a risk:** every current user's password hash and every stored phone number is publicly downloadable, permanently, from an immutable object store. Rotating a password today does not remove the old hash from history.
- **Suggested fix (do NOT apply):** treat all 24 credentials as compromised — force a password reset for every user (the existing `POST /admin/users/{username}/reset-password` already revokes sessions), then purge the blobs from history with `git filter-repo` (or BFG) across all refs, force-push, and have every collaborator re-clone. Follow with `git rm --cached gi_database.db data-archive/*.bak` so the existing `*.db` ignore rule finally takes effect, and add `*.bak` alongside it. Because the repository is public, assume the data is already harvested: **rotation is mandatory and history rewriting alone is insufficient.** Consider whether the repository needs to be public at all.
- **Effort:** High (coordination — credential rotation, history rewrite, collaborator re-clone)

### Finding #2 — `PROJECT_STATUS.md` understates the configured state of the WhatsApp secrets
- **Severity:** Medium
- **File:** `docs/PROJECT_STATUS.md` (§3) · `deploy/.env`
- **Line(s):** `PROJECT_STATUS.md:157-160`
- **Category:** Security-state documentation drift
- **Evidence:** the status document lists these as still-open operator TODOs:

  ```
  **Operator TODOs still open (Meta side):** approve `gi_evening_summary`
  (2 body vars, lang `en`); set `WHATSAPP_WEBHOOK_VERIFY_TOKEN` +
  `WHATSAPP_APP_SECRET`; subscribe the webhook URL in Meta; set
  `PUBLIC_BASE_URL`.
  ```

  The actual state of `deploy/.env` on this machine (key names and populated-status only — no values, not even prefixes, were read into this report):

  | Key | State |
  |---|---|
  | `WHATSAPP_APP_SECRET` | **POPULATED** |
  | `WHATSAPP_WEBHOOK_VERIFY_TOKEN` | **POPULATED** |
  | `WHATSAPP_TOKEN` | **POPULATED** |
  | `WHATSAPP_PHONE_NUMBER_ID` | **POPULATED** |
  | `PUBLIC_BASE_URL` | **POPULATED** |
  | `JWT_SECRET` | placeholder (`CHANGE…`) |
  | `POSTGRES_PASSWORD` | placeholder (`CHANGE…`) |
  | `SMTP_HOST` / `SMTP_USER` / `SMTP_PASS` | placeholder |
  | `CORS_ORIGINS`, `AWS_*` (bucket/region/keys) | EMPTY |
- **Why it's a risk:** **this materially corrects Audit 03 Finding #2.** I rated the WhatsApp webhook fail-open as High partly because `PROJECT_STATUS.md` presented `WHATSAPP_APP_SECRET` as unset, making the fail-open branch the *shipped default*. On this machine it is set, so the HMAC check is live locally and the practical exposure is lower than stated. The code defect is unchanged and the fix you are applying today is still correct — but the severity driver was partly a stale document. More generally, a status file that misreports which security controls are configured causes exactly this class of mis-prioritisation, in both directions.
- **Suggested fix (do NOT apply):** update §3 to reflect what is actually configured, and prefer a runtime check (a startup log line or an admin-console panel listing which security-relevant variables are set/unset, without values) over a hand-maintained list.
- **Effort:** Low

### Finding #3 — `deploy/.env` holding live Meta credentials is world-readable
- **Severity:** Medium
- **File:** `deploy/.env`
- **Line(s):** n/a — filesystem permissions
- **Category:** Excessive file permissions on a secrets file
- **Evidence:**

  ```
  $ ls -l deploy/.env
  -rw-r--r--@ johnsonandrew  deploy/.env
  ```

  Mode `0644` — readable by **every** user account on the host. The file holds a populated `WHATSAPP_TOKEN` (a permanent System User token per `PROJECT_STATUS.md`), `WHATSAPP_APP_SECRET`, and `WHATSAPP_WEBHOOK_VERIFY_TOKEN`. Correctly gitignored (`git check-ignore` → `.gitignore:13:.env`) and never tracked, so this is a local-filesystem exposure only.
- **Why it's a risk:** on the developer Mac the practical risk is low (single-user machine), but the same file is copied to the Hetzner box during deployment, where it will sit alongside any other service account, and the Meta token grants full send capability on the business WhatsApp number. Any local process — including a compromised npm/pip dependency running as another user — can read it.
- **Suggested fix (do NOT apply):** `chmod 600 deploy/.env` locally and add the same to the deployment runbook (`tools/migration/README.md`); consider Docker secrets or a systemd `EnvironmentFile` with restricted ownership on the production host.
- **Effort:** Low

### Finding #4 — A publicly documented test secret satisfies the production strength check
- **Severity:** Medium
- **File:** `docs/ARCHITECTURE.md`, `docs/PROJECT_STATUS.md`, `docs/NEW_STACK_HANDOFF.md`, `docs/DEBUGGING.md`, `docs/automatic_test.md`, `.github/workflows/postgres-dual-ci.yml`, `.github/workflows/deploy-v2.yml`
- **Line(s):** e.g. `ARCHITECTURE.md` §8 gate command; `postgres-dual-ci.yml` env block
- **Category:** Known-value secret that passes the production guard
- **Evidence:** the documented gate command, repeated in five docs and two workflows, is:

  ```
  JWT_SECRET=ci-only-service-test-secret-key-32bytes-min
  ```

  Evaluated against `config.py:98-113`:

  ```
  len: 43 | >=32: True | == _DEV_JWT_SECRET: False
  PASSES production guard -> True
  ```

  The production guard rejects a missing secret, a short secret, and the specific dev-default string — but this value is none of those, so `GI_ENV=production` would accept it without complaint.
- **Why it's a risk:** it is the string every developer copy-pastes to run the gates, it appears in a public repository, and it is the value most likely to be pasted into a `.env` "just to get the server up" during a deployment session. If that ever happens, the signing key for every access, refresh and MFA token is public knowledge — full authentication bypass — and the fail-fast check that exists specifically to prevent this will stay silent. It compounds Audit 03 Finding #1, where the guard may not be armed at all.
- **Suggested fix (do NOT apply):** add the known test/CI values to a denylist inside `jwt_secret()` alongside `_DEV_JWT_SECRET`, so any published constant is refused in production regardless of length. Ideally generate the CI secret per-run (`openssl rand -hex 32` in the workflow) rather than hardcoding one.
- **Effort:** Low

### Finding #5 — `.gitignore` lacks key, certificate, backup and environment-variant patterns
- **Severity:** Medium
- **File:** `.gitignore`
- **Line(s):** 7-14, 37
- **Category:** Incomplete ignore coverage for secret-bearing file types
- **Evidence:** verified with `git check-ignore -v` against representative paths:

  | Pattern | Ignored? |
  |---|---|
  | `.env`, `deploy/.env` | **Yes** — `.gitignore:13:.env` (matches at any depth) |
  | `.env.example`, `deploy/.env.example` | **No** — correct, these should be tracked |
  | `.env.local` | **NOT IGNORED** |
  | `.env.production` | **NOT IGNORED** |
  | `*.pem` | **NOT IGNORED** |
  | `*.key` | **NOT IGNORED** |
  | `*.crt` | **NOT IGNORED** |
  | `*.p12` / `*.pfx` | **NOT IGNORED** |
  | `*.bak` | **NOT IGNORED** (this is how `data-archive/gi_database.*.bak` got tracked — Finding #1) |
  | `*.log` | **NOT IGNORED** |

  No key or certificate material exists in the working tree today — a `find` for `*.pem`, `*.key`, `*.crt`, `*.p12`, `*.pfx`, `credentials.json`, `service-account*.json`, `token.json` returned **nothing**. So this is preventive, not a live exposure.
- **Why it's a risk:** the deployment kit provisions TLS via certbot (`deploy/init-letsencrypt.sh`), so private keys will exist on the production host, and any future local TLS testing would drop a `.pem`/`.key` into a directory with no ignore coverage. `.env.production` is precisely the filename an operator would create while setting up the Hetzner box.
- **Suggested fix (do NOT apply):** add `.env.*` with a `!.env.example` negation (covering `.env.local`, `.env.production`, `.env.dev` in one rule), plus `*.pem`, `*.key`, `*.crt`, `*.p12`, `*.pfx`, `*.bak`, `*.log`.
- **Effort:** Low

### Finding #6 — Staff phone numbers persist in git history via a removed WhatsApp log
- **Severity:** Low
- **File:** `PyWhatKit_DB.txt` (untracked today; present in history)
- **Line(s):** n/a
- **Category:** PII in git history
- **Evidence:**

  ```
  $ git log --all --oneline -- PyWhatKit_DB.txt data-archive/PyWhatKit_DB.txt
  d303a30 refactor(phase-b): cutover-day restructure ...
  bf75fd9 updated file
  22ffc49 updated files
  5cb5763 uptaded Files
  2dfab24 Updated Files A whatsapp

  $ git show bf75fd9:PyWhatKit_DB.txt | grep -coE '\+?[0-9]{9,15}'
  100
  ```

  The file is the `pywhatkit` library's send log. It is no longer in the working tree (moved out at the Phase B restructure and now untracked), but ~100 phone numbers remain in the historical blobs of a public repository.
- **Why it's a risk:** contact-level PII disclosure. Materially the same exposure class as Finding #1's 93 numbers, at lower volume and without credentials attached.
- **Suggested fix (do NOT apply):** include this path in the same `git filter-repo` pass proposed for Finding #1 — one history rewrite should remove all of it.
- **Effort:** Low (if bundled with Finding #1; otherwise not worth a standalone rewrite)

### Finding #7 — `PUBLIC_BASE_URL` falls back to a localhost default used in outbound links
- **Severity:** Low
- **File:** `backend/api/weekly_report.py`
- **Line(s):** 54
- **Category:** Non-secret configuration default with an operational security consequence
- **Evidence:**

  ```python
  return os.environ.get("PUBLIC_BASE_URL", "http://localhost:8000").rstrip("/")
  ```

  This value builds the 72-hour tokenized weekly-report download link that is sent to every admin and HOD over WhatsApp. It is populated in `deploy/.env` today, so the fallback is not currently in play.
- **Why it's a risk:** not a secret, but if the variable is ever unset in production the capability URLs mailed out become `http://localhost:8000/reports/weekly-exec/<token>` — the recipient's device resolves `localhost` to itself, the link silently fails, and the 256-bit token has been transmitted over WhatsApp for nothing. A misconfiguration produces broken security-relevant links rather than a loud error.
- **Suggested fix (do NOT apply):** require `PUBLIC_BASE_URL` when `is_production()`, mirroring the `JWT_SECRET` fail-fast pattern.
- **Effort:** Low

### Finding #8 — AWS backup credentials are plumbed end-to-end but unconfigured
- **Severity:** Low
- **File:** `deploy/.env` · `deploy/docker-compose.prod.yml:159-164` · `deploy/backup/backup-pg.sh:22-49`
- **Category:** Cataloguing — declared-but-unused credential surface
- **Evidence:** `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_DEFAULT_REGION` and `AWS_S3_BUCKET` are all **EMPTY** in `deploy/.env`, passed through compose as `${AWS_*:-}`, and consumed by the off-box backup script — which correctly no-ops when the bucket is blank:

  ```bash
  [ -n "${AWS_S3_BUCKET:-}" ] || return 0          # S3 not configured → skip
  ```
- **Why it's a risk:** none today — this is the brief's "loaded but never used" hygiene category. It is catalogued because off-box backups are a stated Phase I-A deliverable, so these keys **will** be populated at deployment; when they are, they must be scoped to `s3:PutObject`/`s3:ListBucket` on one prefix (the script's own comment says "put/list only"), never a general-purpose IAM user, and they inherit the `0644` problem in Finding #3.
- **Suggested fix (do NOT apply):** when provisioning, create a dedicated IAM user with a least-privilege bucket policy and enable bucket-level SSE (`AWS_SSE=AES256` is already set).
- **Effort:** Low

---

## Operator-Pending Secrets

Everything the operator must set or verify before Phase 2, consolidated from `PROJECT_STATUS.md` §3, `docs/DEPLOY.md`, `tools/migration/README.md` and the actual state of `deploy/.env`. **Note the discrepancy column** — the status doc is out of date (Finding #2).

| Secret / setting | `PROJECT_STATUS.md` says | Actual `deploy/.env` state | Action needed |
|---|---|---|---|
| `JWT_SECRET` | set at deploy | **placeholder** (`CHANGE…`) | **Must set** — `openssl rand -hex 32`. Fail-fast fires only if `GI_ENV` is also set (Audit 03 H1) |
| `GI_ENV` | implied by compose | **absent from `deploy/.env` entirely** | **Must set** to `production` — you are patching this today |
| `POSTGRES_PASSWORD` | set at deploy | **placeholder** (`CHANGE…`) | Must set |
| `WHATSAPP_APP_SECRET` | "still open" | **POPULATED** | Verify it matches the Meta console; propagate to the server |
| `WHATSAPP_WEBHOOK_VERIFY_TOKEN` | "still open" | **POPULATED** | Same |
| `WHATSAPP_TOKEN` | live | **POPULATED** | Permanent System User token — rotate if the `0644` exposure (Finding #3) concerns you |
| `PUBLIC_BASE_URL` | "still open" | **POPULATED** | Verify it points at `https://gi.giinventory.com`, not localhost (Finding #7) |
| `SMTP_HOST` / `SMTP_USER` / `SMTP_PASS` | set at deploy | **placeholder** | Must set for the email outbox to function |
| `EMAIL_LOGISTICS_TO` | set at deploy | POPULATED | Verify |
| `GI_AI_RO_URL` | "password-protected in production" | **absent from `deploy/.env`** | **Must set** — and re-run `create_ai_readonly_role.sql` with `ALTER ROLE gi_ai_ro PASSWORD`. Ties directly to Audit 01 Findings #2/#3 |
| `CORS_ORIGINS` | "set only if you split origins" | **EMPTY** | Empty string triggers the dev-origin fallback in production — Audit 03 Finding #5 |
| `AWS_*` (backup) | optional | **EMPTY** | Optional; if enabling, use a least-privilege IAM user (Finding #8) |
| `gi_evening_summary` template | "still open" | n/a (Meta console) | Approve in Meta |
| Meta webhook URL subscription | "still open" | n/a (Meta console) | Subscribe |
| Cloudflare Access bypass for `/api/*` | open (`NATIVE_APPS.md` §6) | n/a (Cloudflare console) | Required for native apps; **pair with an origin firewall** limiting ingress to Cloudflare ranges, or Audit 03 Finding #6 (spoofable rate-limit headers) becomes live |

---

## Reviewed — No Finding

### Working tree secret scan
- **`.env*` inventory:** exactly three files — `.env.example` (tracked, placeholders only), `deploy/.env.example` (tracked, placeholders only), `deploy/.env` (**gitignored, never tracked**, holds real values). Confirmed via `git check-ignore -v` and `git ls-files --error-unmatch`.
- **Both tracked `.env.example` files verified clean right now.** Every assignment is a `PASTE_…`, `CHANGE…`, `you@example…`, `erp.example…` placeholder or empty. The brief's "actual secret in `.env.example` → Critical, flag immediately in chat" condition does **not** apply, which is why this audit ran to completion without interruption.
- **No key or certificate material anywhere in the working tree** — `find` for `*.pem`, `*.key`, `*.crt`, `*.p12`, `*.pfx`, `credentials.json`, `service-account*.json`, `token.json` returned nothing.
- **No `config.json` / `settings.json` with embedded secrets.** `frontend/src-tauri/tauri.conf.json` carries only CSP and bundle metadata.

### Runtime secret exposure
- **No secret is printed or logged.** Grepping `print(`, `log.{info,debug,warning,error}(`, `logger.*(` against `secret|token|password|api_key|jwt|authorization|passwd` across `backend/` yields exactly **one** hit, and it is safe by construction:

  ```python
  # webhook.py:82 — logs the COMPARISON RESULT, never the token
  log.warning("webhook handshake rejected (mode=%r, token %s)", mode,
              "matched" if token == expected else "mismatch/unset")
  ```
- **WhatsApp/SMTP services do not log credentials.** `whatsapp.py:80` logs `raw[:300]` of a *Meta API error response*, which contains no outbound token; `whatsapp.py:77` logs a fixed sandbox-restriction message. `emailer.py` logs nothing credential-bearing.
- **No global exception handler, no `debug=True`, no traceback exposure.** `main.py` registers no `@app.exception_handler` and no `add_exception_handler`, so FastAPI's default handler returns a generic `{"detail": "Internal Server Error"}` with the traceback going to the server log only. Route-level handlers consistently surface `type(e).__name__` and `e.orig` rather than full stack traces.
- **`/health`** exposes dialect, database name, entity list and maintenance state but **no secrets** (already filed as Audit 02 Finding #11).

### Config loading discipline
- **No hardcoded secret defaults.** Every `os.environ.get("KEY", "<non-empty>")` in the backend was inspected; all 19 non-empty defaults are non-sensitive operational config — `OLLAMA_HOST`, model names (`llama3.1:8b`, `qwen2.5-coder:7b`, `qwen2.5vl:7b`), timeouts, concurrency, `SMTP_PORT=587`, `SMTP_STARTTLS=1`, `WHATSAPP_API_VERSION=v20.0`, template names, `GI_DIGEST_HOUR=16`, directory paths. Genuine secrets (`WHATSAPP_TOKEN`, `WHATSAPP_APP_SECRET`, `SMTP_PASS`, `GI_AI_RO_URL`, `DATABASE_URL`) all use bare `os.environ.get("KEY", "")` or no default. The single exception is `JWT_SECRET`'s `_DEV_JWT_SECRET` fallback, already filed as Audit 03 Finding #1 and not re-litigated here.
- **`.env` load order traced** (`config.py:14-33`, per the brief's question about shadowing):

  ```python
  root = Path(__file__).resolve().parents[2]
  for p in (root / ".env", root / "deploy" / ".env"):
      if p.is_file():
          load_dotenv(p, override=False)
  ```

  Root `.env` loads **first**, `deploy/.env` second, both with `override=False`. Because `python-dotenv` with `override=False` never replaces an already-set variable, **the first file to define a key wins** — so a stale root `.env` *would* shadow the deploy-time `deploy/.env` for any key both define. Today no root `.env` exists (only `.env.example`), so nothing is shadowed. It is worth the operator's awareness rather than a finding: the process environment always wins over both (correct, so compose/systemd injection cannot be clobbered), and the ordering is only a hazard if someone creates a root `.env` on the production host. `GI_DOTENV=0` skips the mechanism entirely and is pinned in `service_tests`.

### Docker / deploy exposure
- **`deploy/docker-compose.prod.yml` — no plaintext secrets.** Every credential is a `${VAR}` reference: `JWT_SECRET: ${JWT_SECRET}`, `DATABASE_URL: postgresql+asyncpg://${POSTGRES_USER}:${POSTGRES_PASSWORD}@db:5432/${POSTGRES_DB}`, `AWS_*: ${AWS_*:-}`. The only literal values are non-sensitive (`GI_ENV: production`, `OLLAMA_HOST: http://ollama:11434`, `GI_BACKUPS_DIR: /backups`, `TZ: Asia/Riyadh`, `PGHOST: db`).
- **No `ENV KEY=<secret>` in any Dockerfile.** `deploy/Dockerfile.api`, `deploy/Dockerfile.web` and both legacy Dockerfiles set only `PIP_NO_CACHE_DIR`, `PATH`, `PYTHONUNBUFFERED`, `PYTHONDONTWRITEBYTECODE`. The brief's Critical condition does not apply.
- **`.dockerignore` excludes `.env`** (line 38) **and `.streamlit/secrets.toml`** (line 39) — the build context cannot pick up the real secrets file.

### Backup / debug artifacts
- **Tracked `.sql`:** exactly one — `backend/scripts/create_ai_readonly_role.sql`, a DDL/grant script with no credentials (it explicitly instructs `ALTER ROLE gi_ai_ro PASSWORD '...'` be run out-of-band).
- **Tracked `.log` / `.dump` / `.tmp`:** none.
- **Tracked `.bak`:** one — `data-archive/gi_database.20260616-211109.bak`, reported in Finding #1.
- **`legacy/BUG_REPORT.md`** is tracked; inspected for embedded secrets — it contains gate output and check names only.
- **Docs scanned for embedded secret values:** the only `KEY=<20+ char value>` match across all Markdown is `JWT_SECRET=ci-only-service-test-secret-key-32bytes-min` (Finding #4). No Meta token, phone-number ID, SMTP password or database credential appears in any document.

### Secrets stored in the database (brief's key-colocation question)
Two credential-derived values are persisted, and **neither uses an encryption key**, so the "key colocated with data" failure mode does not arise:
- `phone_otp.code_hash` — bcrypt hash of the OTP, single-use, 10-minute expiry, 5-attempt cap. One-way; no key.
- `generated_reports.token_hash` / `auth_sessions.refresh_hash` — sha256 of a high-entropy random token. One-way; no key.

`users.totp_secret` **is** stored reversibly (plaintext base32) — already filed as Audit 03 Finding #7. It is unencrypted rather than badly-encrypted, so again there is no colocated-key issue; currently zero users have enrolled, so no live seed is exposed.

## Files Reviewed
- Git history: all refs, via the 14 pickaxe/path searches listed in Part 1
- `.env.example`, `deploy/.env.example` (tracked, contents verified)
- `deploy/.env` (gitignored — key names and populated-status only; **no values read into this report**)
- `.gitignore`, `.dockerignore`, `.git/config`
- `deploy/docker-compose.prod.yml`, `deploy/Dockerfile.api`, `deploy/Dockerfile.web`, `deploy/backup/backup-pg.sh`, `legacy/docker-compose.yml`, `legacy/Dockerfile.streamlit`, `legacy/Dockerfile.fastapi`
- `backend/api/config.py` (secret resolution, dotenv order, CORS)
- `backend/api/services/whatsapp.py`, `backend/api/services/emailer.py` (credential load + logging)
- `backend/api/webhook.py`, `backend/api/weekly_report.py`, `backend/api/ai/client.py`, `backend/api/main.py`, `backend/api/report_center.py`, `backend/api/services/notifications.py`, `backend/api/ai/manual_qa.py` (env defaults)
- `docs/PROJECT_STATUS.md` §3-4, `docs/DEPLOY.md`, `docs/ARCHITECTURE.md` §8, `REPO_MAP.md`
- `.github/workflows/postgres-dual-ci.yml`, `.github/workflows/deploy-v2.yml` (secret handling in CI)
- Binary blobs `gi_database.db`, `data-archive/gi_database.20260616-211109.bak`, `data-archive/demo_seed.db`, historical `PyWhatKit_DB.txt` — **inspected by pattern count only; no values extracted, no files written**

## Files Skipped and Why
- `frontend/` — out of Phase 1 scope. Noted for the frontend phase: `VITE_*` variables are compile-time-inlined into the bundle and therefore must never hold secrets; `VITE_API_URL` (the only one injected by the release workflows) is a public URL, so nothing is exposed today.
- `legacy/**` application code — frozen; its `.streamlit/secrets.toml` is gitignored (`.gitignore:14`) and absent from the working tree.
- `node_modules/`, `.venv/` — third-party dependency trees; dependency-embedded secrets are a supply-chain question for `pip-audit`/`npm audit`, not this audit.
- `.git/` internals beyond `config` and object inspection — no rewriting or object surgery attempted, per the read-only constraint.

---

## Tooling Recommendation

Not installed, not run. **This is the audit area where tooling adds the most beyond manual review**, because my searches covered *known* prefixes on *reachable* refs, while these tools use entropy analysis and walk the full object database — including the dangling-object gap I flagged in Part 1.

- **`trufflehog`** — the top recommendation for this repository. Beyond regex prefixes it applies entropy detection **and live-credential verification** (it will actually call the Meta/AWS/GitHub APIs to test whether a discovered key still works, which converts "found a string" into "found an active credential"). Suggested invocation: `trufflehog git file://. --json`. It walks all objects, so it covers the unreachable-blob case my `git log --all` searches cannot.
- **`gitleaks`** — same purpose, different engine; faster and easier to wire into CI as a blocking check (`gitleaks detect --source . --redact`). The `--redact` flag matters here given the report-leakage concern you raised. Running **both** is reasonable for a one-time historical sweep, since their rule sets differ; keep only one in CI.
- **`detect-secrets`** (Yelp) — the right tool for *going forward* rather than for history. It generates a `.secrets.baseline` of known/accepted findings so CI fails only on **new** secrets, which suits a repository that will legitimately keep placeholder-bearing `.env.example` files tracked. Pair with `pre-commit`.
- **`git-secrets`** (AWS Labs) — pre-commit hook that blocks commits matching credential patterns. Narrower than `detect-secrets` (AWS-centric by default) but trivial to extend with the Meta `EAA` prefix, and it is the control that would have made the near-miss described in `PROJECT_STATUS.md` §4 structurally impossible rather than luckily avoided. Given the *actual* incident in this repo is a committed database rather than a committed token, also add a hook rejecting `*.db`/`*.bak` additions by size or extension.

---

*Audit 04 complete. Nothing outside `docs/security/reports/` was created or modified; no code, git-write, database, service, or package operations were performed. All git commands were read-only (`log`, `show`, `ls-files`, `check-ignore`, `cat-file`, `config --get-regexp`). Suspected-secret values were counted, never printed. Per your instructions, Audit 01 #1/#2, Audit 02 #1/#2 and Audit 03 H1/H2/H3 are not re-litigated here and remain on their own tracks.*
