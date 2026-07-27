# Phase 2 · Theme D — run log (config discipline)

**Findings closed:** `A04-F3` · `A04-F4` (Medium) · `A04-F7` (Low)
**Also closed in passing:** the unfinished half of `A04-F5`
**Date:** 2026-07-27 · **Branch:** `security/phase-2-theme-d-final` (off `main`,
which carries Themes A/B/C + the by-id mop-up via PRs #6–#9)

---

## 1. Findings

### `A04-F4` — a published test secret satisfied the production guard
`jwt_secret()` rejected a missing, short, or dev-default key. The documented
gate key `ci-only-service-test-secret-key-32bytes-min` is 43 chars and none of
those, so it **passed** — and it is the string that appears in five docs and two
workflows, i.e. the one most likely to be pasted into a `.env` "just to get the
server up".

Added `_PUBLISHED_SECRETS`, a denylist checked independently of length, covering
the CI key, both `CHANGE_ME_*` placeholders, and the usual `changeme`/`secret`
family. The length check still runs first, so its error message stays specific.

### `A04-F7` — `PUBLIC_BASE_URL` fell back to localhost
It builds the 72-hour tokenized weekly-report links broadcast over WhatsApp. An
unset value produced `http://localhost:8000/...`, which resolves on the
*recipient's own device* — the link fails silently and a 256-bit capability
token was transmitted for nothing.

New `config.public_base_url()` fails fast in production on unset **and** on a
localhost value; dev keeps the old fallback so no local setup is required.
`weekly_report._public_base()` now delegates to it, so there is one definition
of "where do outbound links point".

### `A04-F3` — secrets file readable beyond its owner
`deploy/.env` was already `chmod 600` on this machine (the operator did that),
so the local half was done. The missing half was structural: nothing *reminded*
anyone. `_load_env_files()` now warns at load time when the file's mode has any
group/other bits, naming the exact `chmod` to run. Never fatal — a wrong mode
must not stop the app serving.

### `A04-F5` (residual) — ignore coverage was reported patched but wasn't
The summary lists `A04-F5` as "User-Patched Pre-Phase-2". Verified with
`git check-ignore`: only the explicit `gi_database.db` / archive paths had been
added. **Still unignored:** `.env.local`, `.env.production`, `*.pem`, `*.key`,
`*.crt`, `*.p12`, `*.pfx`, `*.bak`, `*.log`. All added, with `!.env.example`
negating the tracked templates. Confirmed no already-tracked file becomes
ignored (that would be the breaking change to watch for here).

## 2. Operator tasks

| Task | Result |
|---|---|
| Strong `JWT_SECRET` | 64 hex chars (`secrets.token_hex(32)`, = `openssl rand -hex 32`) written to the gitignored `deploy/.env`. Value never printed or committed; only a sha256 prefix was echoed. |
| Strong `POSTGRES_PASSWORD` | 48 chars, **alphanumeric only** — deliberately no punctuation, because compose builds `postgresql+asyncpg://user:pass@db/...` and a `@ : / #` would corrupt the DSN. Length carries the entropy instead. |
| `.dockerignore` excludes `.env` + `gi_database.db` | Already covered `.env`, `**/.env`, `*.db`, `*.bak`. **Two real gaps found and fixed:** no `.env` *variant* was excluded (an operator's `.env.production` would have been baked into the image), and the bare `*.db`/`*.bak` patterns are anchored at the context root so a nested one would have slipped through. Key material (`*.pem/*.key/*.p12/*.pfx`) added too. |
| Runbook mentions `GI_TRUSTED_PROXIES` + `CORS_ORIGINS` | Added to `docs/DEPLOY.md` §1 (with a table explaining the failure mode of each) and to `tools/migration/README.md` step 5. |

### Two problems found while doing the operator tasks

**1. `deploy/.env` never reached the container.** `docker-compose.prod.yml` had
no `env_file:`, and its `environment:` block named only `GI_ENV`, `JWT_SECRET`,
`DATABASE_URL`, `CORS_ORIGINS`, `OLLAMA_HOST`, `GI_AI_CONCURRENCY`,
`GI_BACKUPS_DIR`. Compose reads `deploy/.env` for `${VAR}` *interpolation*, which
is not the same as passing it in — so `WHATSAPP_*`, `SMTP_*`,
`EMAIL_LOGISTICS_TO` and `PUBLIC_BASE_URL` were **never visible to the API in
production**. WhatsApp and email would have degraded to "not configured", and
with the new `A04-F7` guard the API would have refused to boot. Fixed with
`env_file: [.env]` plus explicit entries for the three production-critical vars.

**2. The `CORS_ORIGINS` guidance in `.env.example` had gone stale — and
dangerously so.** It read: *"Leave BLANK … the code defaults then ALSO allow the
native app shells."* That was true before Theme C changed the production default
to an empty list. Following it would have silently broken every Tauri/Capacitor
API call. Rewritten with the explicit origin list to paste.

## 3. Not changed — flagged for the operator

**`PUBLIC_BASE_URL` looks wrong.** `deploy/.env` has
`PUBLIC_BASE_URL=https://api.giinventory.com` while `DOMAIN=gi.giinventory.com`,
and nginx serves the API at **`/api` on the `DOMAIN` host** — there is no
`api.giinventory.com` anywhere else in the deploy kit. If that hostname has no
DNS record, every weekly-report link breaks, which is precisely the failure
`A04-F7` describes. The likely correct value is `https://gi.giinventory.com`.
Left alone because it is a DNS/routing decision, not a code one.

**The boot-time secret diagnostic** (the audit's Theme D centrepiece, and its
recommended *first* task) is **not implemented** and is not on `main`. It exists
on the abandoned `security/theme-d-config-discipline` branch (`f507941`), which
this session was told not to use. This branch covers the three named findings
only.

## 4. Test evidence — suite AV (10 checks)

Asserted directly against the real config functions and the real ignore files,
since these are boot-time and build-time controls with no HTTP surface.
Includes `_dockerignore_excluded()`, a faithful reimplementation of
moby/patternmatcher (last-match-wins, `!` negation, `**` spanning, ancestor
exclusion) so the check runs on machines without a Docker CLI — this one has no
Docker installed.

Negative-verified against the pre-fix tree:

| Check | Pre-fix result |
|---|---|
| `av-f4` | `ci-only-…` and `CHANGE_ME_run_openssl…` both **accepted** in production |
| `av-docker` | `.env.production`, `deploy/.env.local`, `deploy/certs/privkey.pem`, `sub/local.db` all **excluded=False** → would enter the image |
| `av-compose` | `env_file: None`, `PUBLIC_BASE_URL in env: False` |
| `av-git` | `.env.local`, `.env.production`, `*.pem`, `*.key`, `*.crt`, `*.p12`, `*.pfx`, `*.log`, `*.bak` all reported NOT IGNORED at session start |

Two checks guard against over-correction: `av-f4` asserts a real 60-char secret
is still accepted, and `av-docker` asserts application sources and
`deploy/.env.example` still reach the build context.

## 5. Gates

| Gate | Before | After |
|---|---|---|
| `backend.api.service_tests` | 837 / 0 | **847 / 0** (+10) |
| Playwright E2E | 39 / 39 | **39 / 39** |
| `legacy/bug_check.py` | 599 / 0 | **599 / 0** |
| `npm run build --prefix frontend` | ✅ | ✅ |

Local dev verified unaffected after the secret rotation: the app imports
cleanly, `is_production()` stays `False`, and CORS is not involved locally
because Vite proxies `/api` to `:8000` on the same origin.

`gi_database.db` sha256 verified identical (`shasum -c: OK`); never staged.
`deploy/.env` holds the new secrets, remains mode `0600`, and remains gitignored
— it is **not** part of this commit.
