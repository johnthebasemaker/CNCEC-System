# Phase 1 Security Audit — Executive Summary

**Scope:** GI Hub v2 backend (FastAPI + PostgreSQL) — SQL safety, IDOR, auth/session, secrets.
**Date:** 2026-07-26 · **Mode:** read-only · **Findings:** 36 (Critical 2 · High 8 · Medium 18 · Low 8)
**Reports:** [00 discovery](00_discovery.md) · [01 SQL](01_sql_injection.md) · [02 IDOR](02_idor.md) · [03 auth](03_auth_session.md) · [04 secrets](04_secrets.md)

---

## 1. Overall Posture

**What is genuinely well-built.** The security fundamentals here are above average for a project this size. The RTR implementation is correct — replaying a revoked token revokes the whole family including the live successor, while other devices survive, and I traced that path line by line rather than trusting the docstring. JWT hygiene is clean: three decode sites, all with explicit `algorithms=[HS256]`, no `verify_signature: False` anywhere, and a scope claim that prevents access/refresh/MFA token substitution. bcrypt runs at cost 12 on every write path. The role helpers (`require_level`, `require_roles`, `site_scope`, `resolve_site_param`) are individually correct and fail-closed. The hand-written SQL layer — 89 raw `text()` constructs, 43 of them f-string-built — contains **zero** injection vulnerabilities; every interpolation is a module constant or a static fragment, with all user values bound. Secret-loading discipline is strong: no hardcoded secret defaults among 19 non-empty `os.getenv` fallbacks, no secret ever logged, all Docker secrets via `${VAR}`, `.dockerignore` correct. And `crud.py`, `hod.py`, `warehouse.py` and `notifications.py` contain textbook-correct authorization patterns that the rest of the codebase should be measured against.

**Where the systemic risks are.** Three patterns account for most of the real risk. First, the **AI NL→SQL lane** is the one place where query *text* is untrusted, and both of its walls are weaker than designed — the forbidden-table regex is bypassable with `FROM public.users` (verified empirically), and the read-only role can read `phone_otp`, `employees`, `whatsapp_outbox` and the audit log, none of which appear in either blocklist. Nothing in that lane is audit-logged, so an exfiltration attempt leaves no trace. Second, the **`''` truthiness pattern**: `site_scope()` correctly returns `''` for a site-less scoped user, but 12 consumers test it for truthiness instead of None-ness and silently drop the site filter — and because registration *forbids* `warehouse_user` and `logistics` from having a site, `''` is the permanent steady state for an entire role class, not a corner case. Third, **status-file drift**: `PROJECT_STATUS.md` reported a committed Meta token that was never committed, and reported WhatsApp secrets as unset that are in fact populated. Both errors distorted risk assessment — one inflating, one deflating.

**Overall risk level: HIGH** — driven almost entirely by two Critical findings with trivial exploit paths (a live credential database in a public repository, and a site boundary read from a query parameter), both of which the operator is patching before Phase 2 begins; absent those two, the residual posture is Medium.

---

## 2. Findings by Severity

| ID | Area | Severity | Title | Status |
|---|---|---|---|---|
| `A04-C1` | Secrets | **Critical** | `gi_database.db` + `.bak` tracked in public repo — 24 bcrypt hashes, 93 phone numbers | User-Patched Pre-Phase-2 |
| `A02-F1` | IDOR | **Critical** | `requests.py:83` — site filter taken from query param, not JWT | User-Patched Pre-Phase-2 |
| `A01-F1` | SQL | High | AI safety gate bypassable via schema-qualified names (`FROM public.users`) | Phase 2 Fix Queued |
| `A01-F2` | SQL | High | `phone_otp`/`employees`/`whatsapp_outbox`/audit log readable by `gi_ai_ro` | Phase 2 Fix Queued |
| `A02-F2` | IDOR | High | `''` scope treated as "no filter" across 12 consumers | Phase 2 Fix Queued |
| `A02-F3` | IDOR | High | `GET /requests/{id}/items` — no ownership or site check | Phase 2 Fix Queued |
| `A02-F4` | IDOR | High | SK can approve/reject another site's material request | Phase 2 Fix Queued |
| `A02-F5` | IDOR | High | 3 line-item endpoints return other warehouses'/sites' rows on direct fetch | Phase 2 Fix Queued |
| `A03-F1` | Auth | High | `JWT_SECRET` production guard armed only by compose-set `GI_ENV` | User-Patched Pre-Phase-2 |
| `A03-F3` | Auth | High | `/2fa/{enroll,verify,disable}` have no rate limit | Phase 2 Fix Queued |
| `A01-F3` | SQL | Medium | `gi_ai_ro` grants are deny-list based and fail open on new tables | Requires Operator Verification |
| `A01-F4` | SQL | Medium | `stock.py:202` — unescaped `%`/`_` in ILIKE (query-of-death) | Phase 2 Fix Queued |
| `A02-F6` | IDOR | Medium | `GET /ai/badge/{id}` — cross-site employee name + phone | Phase 2 Fix Queued |
| `A02-F7` | IDOR | Medium | `GET /ai/submission-summary` — no site check on `ref_id` | Phase 2 Fix Queued |
| `A02-F8` | IDOR | Medium | `receiving.py` `_actor_site()` — empty site disables the DN ownership check | Phase 2 Fix Queued |
| `A02-F9` | IDOR | Medium | Role-string comparisons instead of the level ladder (9 sites) | Phase 2 Fix Queued |
| `A03-F2` | Auth | Medium ⬇ | WhatsApp webhook HMAC fail-open when `WHATSAPP_APP_SECRET` unset *(revised from High — see §4)* | User-Patched Pre-Phase-2 |
| `A03-F4` | Auth | Medium | `GI_ENV=prod` passes `is_production()` but ships the refresh cookie without `Secure` | Phase 2 Fix Queued |
| `A03-F5` | Auth | Medium | Production CORS falls back to dev origins (compose passes empty string) | Phase 2 Fix Queued |
| `A03-F6` | Auth | Medium | Rate-limit keys from spoofable `CF-Connecting-IP`/`X-Real-IP`; per-process store × 4 workers | Phase 2 Fix Queued |
| `A03-F7` | Auth | Medium | TOTP secrets stored plaintext | Documented, No Fix Needed *(Phase 3)* |
| `A03-F8` | Auth | Medium | 2FA enrollment requires no re-authentication | Phase 2 Fix Queued |
| `A03-F9` | Auth | Medium | Role/site/warehouse changes don't revoke outstanding tokens (≤15 min staleness) | Phase 2 Fix Queued |
| `A03-F10` | Auth | Medium | AI query endpoints write no audit record | Phase 2 Fix Queued |
| `A04-F2` | Secrets | Medium | `PROJECT_STATUS.md` §3 misreports configured WhatsApp secrets | User-Patched Pre-Phase-2 |
| `A04-F3` | Secrets | Medium | `deploy/.env` is mode `0644` while holding live Meta credentials | Phase 2 Fix Queued |
| `A04-F4` | Secrets | Medium | Published CI secret (43 chars) passes the production strength guard | Phase 2 Fix Queued |
| `A04-F5` | Secrets | Medium | `.gitignore` lacks `*.pem/*.key/*.crt/*.p12/*.pfx`, `.env.local`, `.env.production`, `*.bak` | User-Patched Pre-Phase-2 |
| `A01-F5` | SQL | Low | `current_setting()`/`version()` pass the AI safety gate | Phase 2 Fix Queued |
| `A02-F10` | IDOR | Low | `/admin/oversight` is level-3, not admin, despite the `/admin` prefix | Documented, No Fix Needed |
| `A02-F11` | IDOR | Low | Unauthenticated `/health` discloses DB name, dialect, entity list | Phase 2 Fix Queued |
| `A03-F11` | Auth | Low | `MIN_PW = 6`, no complexity or breach screening | Phase 2 Fix Queued |
| `A03-F12` | Auth | Low | Logout reports success even when no family was revoked | Documented, No Fix Needed |
| `A04-F6` | Secrets | Low | ~100 phone numbers in history via removed `PyWhatKit_DB.txt` | Documented, No Fix Needed |
| `A04-F7` | Secrets | Low | `PUBLIC_BASE_URL` falls back to `http://localhost:8000` in outbound links | Phase 2 Fix Queued |
| `A04-F8` | Secrets | Low | AWS backup credentials plumbed but unconfigured | Documented, No Fix Needed |

---

## 3. Top 5 Most Critical

1. **`A04-C1` — `gi_database.db` tracked in a public repository.** Anyone clones the repo, runs `strings` on the 1.1 MB blob, and extracts 24 bcrypt hashes plus 93 staff phone numbers; combined with the 6-character minimum password (`A03-F11`), the weakest of those hashes fall to offline GPU cracking within hours.
2. **`A02-F1` — `requests.py:83` query-parameter site override.** A level-0 store keeper appends `?site_id=CNCEC` to `GET /requests` and reads another site's material requests — worker names, job locations, materials, quantities — because the client value is preferred over the JWT claim and `resolve_site_param` is never called.
3. **`A01-F1` — AI safety gate regex bypass.** A logistics user asks the NL lane a question crafted to make the local model emit `SELECT * FROM public.users`; the schema qualifier defeats the forbidden-table regex (verified: bare `users` rejected, `public.users` accepted), leaving only the database `REVOKE` — which `A01-F3` shows is wiped by every mirror reload.
4. **`A01-F2` — sensitive tables readable by `gi_ai_ro`.** The same lane runs `SELECT * FROM phone_otp` or `FROM employees` and succeeds outright — no schema trick needed — because neither table appears in `FORBIDDEN_TABLES` nor in the role's `REVOKE` list, exposing staff PII, WhatsApp message bodies and the audit log itself.
5. **`A03-F1` — `JWT_SECRET` production guard reachability.** The Hetzner box is started once with `uvicorn` instead of compose (a debug session, a systemd unit, a migration container); `GI_ENV` is unset, `is_production()` returns `False`, and the app boots on the dev signing key published in this public repo — every token, including forged `role: admin` claims, becomes mintable by anyone.

---

## 4. User-Patched Pre-Phase-2

Handled manually by the operator before Phase 2 begins. **Claude Code did not touch any of these.**

| ID | Action |
|---|---|
| `A04-C1` | Force-reset all 24 user passwords (custom migration script); rotate or purge the 21 archive users; revoke every refresh family via SQL; `git rm --cached gi_database.db data-archive/gi_database.*.bak`; add explicit path entries to `.gitignore` **on top of** the `*.db` glob so tracked-file skip cannot recur |
| `A04-C1` | **History-rewrite decision: NOT rewriting.** Rationale recorded — the repo is public and may already be mirrored or scraped, so a force-push breaks every existing clone without protecting data that has already left. Rotation is the real control; history rewrite would be theatre. |
| `A02-F1` | Patch `requests.py:83` to use `resolve_site_param(user, site_id)` + `== ""` guard |
| `A03-F1` | Add `GI_ENV=production` to `deploy/.env.example`, `deploy/Dockerfile.api`, and `run_api.sh` |
| `A03-F2` | Set `WHATSAPP_APP_SECRET` from the Meta console; flip `_signature_ok`'s fail-open branch to fail-closed. **Severity revised High → Medium**: `deploy/.env` shows the secret already populated, so the fail-open branch never fires in practice — but the shipped default is still wrong, so the code fix stands |
| `A04-F2` | Correct `PROJECT_STATUS.md` §3 (WhatsApp secrets are populated, not pending) and §4 (the Meta token was **never committed** — history confirms `.env.example` has one commit, placeholders only) |
| `A04-F5` | `.gitignore` path entries added as part of the `A04-C1` work |

---

## 5. Phase 2 Fix Queue

### Theme A — Systemic `''` truthiness (`A02-F2`, `A02-F8`)
**Effort: 3–4 person-days.** Touch: the 12 sites listed in [02_idor.md](02_idor.md) Finding #2, `receiving.py:_actor_site`, plus a shared `apply_site_filter(stmt, col, scope)` helper or a non-falsy sentinel from `site_scope()`. Add the `site-scope-falsy-test` semgrep rule to CI so the class cannot regress.
**Do NOT touch:** `manhours.py` and `sme.py` — their 26 call sites *look* unguarded but use `if sid is not None:` and are already fail-closed. Changing them risks breaking correct behaviour. Same for `main.py:_site()`, `crud.py`, `stock.py`, `hod.py`, `warehouse.py`, which are the reference implementations.

### Theme B — AI NL→SQL lane hardening (`A01-F1`, `A01-F2`, `A01-F3`, `A01-F5`, `A03-F10`, `A02-F6`, `A02-F7`)
**Effort: 4–5 person-days.** Touch: `ai/safety.py` (replace the table regex with a parser-based check — `sqlglot` — and extend the blocked-function list), `backend/scripts/create_ai_readonly_role.sql` (invert to allowlist grants; drop the blanket `ALTER DEFAULT PRIVILEGES`), a startup assertion that `gi_ai_ro` cannot read `users`, audit-log rows for `/ai/query` and `/ai/nl-search`, and site checks on `/ai/badge/{id}` and `/ai/submission-summary`.
**Do NOT touch:** the two-lane routing rule itself — the template lane's JWT-derived scoping and the "generated SQL never runs for a scoped user" gate (`ai/router.py:488`) are correct and are the reason this lane isn't already a breach.

### Theme C — Auth surface completion (`A03-F3`, `A03-F4`, `A03-F5`, `A03-F6`, `A03-F8`, `A03-F9`, `A03-F11`, `A02-F9`, `A02-F11`)
**Effort: 2–3 person-days.** Touch: rate limits + attempt counter on the three `/2fa/*` routes; **canonicalize `is_production()`** — one function used everywhere, with a boot-time assertion that `GI_ENV`, if set, matches an accepted value (this is the triple-check bug: `config.py` accepts `prod|production`, `auth.py:165` accepts only `production`); production CORS default; trusted-proxy allowlist for rate-limit IP resolution; step-up auth on `/2fa/enroll`; `revoke_all_sessions` on role/site change; `MIN_PW` → 12; `/health` payload reduction.
**Do NOT touch:** the RTR rotation and family-revocation logic, the `_decode` scope enforcement, or the bcrypt cost — all verified correct.

### Theme D — Config discipline (`A04-F2`, `A04-F3`, `A04-F4`, `A04-F7`)
**Effort: 1–2 person-days.** Touch: a **startup diagnostic** that reads each documented operator-pending secret, compares it against the actual environment, and logs a set/unset/placeholder table at boot (names only, never values) — this is the structural fix for the status-file drift that produced `A04-F2` and the incorrect incident note; `chmod 600 deploy/.env` + runbook entry; add published CI/test secrets to the `jwt_secret()` denylist (or generate per-run in the workflow); require `PUBLIC_BASE_URL` under `is_production()`.
**Do NOT touch:** the dotenv `override=False` semantics — process environment winning over both files is correct and protects compose/systemd injection.

---

## 6. Requires Operator Verification

Items Claude Code could not confirm under the read-only constraint:

| Item | Source | How to verify |
|---|---|---|
| Whether the `gi_ai_ro` REVOKEs are currently applied on the live mirror and (later) production | `A01-F3` | `psql -U gi_ai_ro -c 'SELECT 1 FROM users LIMIT 1'` — **must fail**. Re-run `create_ai_readonly_role.sql` after every reload |
| Whether the refresh cookie actually carries `Secure=True` with `GI_ENV` set correctly | `A03-F4` | Inspect `Set-Cookie` on a production `/auth/login` response |
| Whether the Hetzner origin will be firewalled to Cloudflare IP ranges after the `/api/*` Access bypass | `A03-F6` | Without it, rate limiting is bypassable by header spoofing |
| Whether unreachable/dangling git objects hold secrets (`git log --all` covers reachable refs only) | `A04` Part 1 | `git fsck --lost-found`, or let `trufflehog` walk the full object database |
| Whether `deploy/.env` on the production host mirrors the local file's populated state | `A04-F2` | Compare after deploy; the Theme D startup diagnostic makes this self-reporting |
| Whether the 24 exposed hashes correspond to still-active accounts | `A04-C1` | Cross-reference before the forced-reset migration |

---

## 7. Documented, No Fix Needed

| Item | Why it's fine |
|---|---|
| `A02-F10` `/admin/oversight` at level 3 | Cross-site procurement KPIs are within the logistics remit; the `/admin` prefix is misleading but the gate is intentional. **No admin route is missing a gate** — all 23 in `console.py`, all of `admin.py`, and `sla.py` inherit `require_level(4)` |
| Role-string comparisons in `requests.py:81`, `main.py:348/354`, `ai/router.py:254/289` | Semantically correct predicates; only `warehouse_scope()`'s unknown-role fail-open (in `A02-F9`) needs the fix |
| `webhook.py:82` logging | Logs `"matched"`/`"mismatch/unset"`, never the token value — the only secret-adjacent log line in the codebase |
| Background daemons | Three asyncio tasks; no sockets, no routes, no token decoding. Reachable only via admin-gated manual triggers |
| Tokenized weekly-report links | 256-bit `secrets.token_urlsafe(32)`, sha256-at-rest, 72-h expiry, scope baked in at render. Not single-use — deliberate and acceptable |
| 90-day native refresh family | `client_type` is self-asserted but grants only a TTL, never authority; the MFA leg carries it inside the signed token |
| `A03-F7` TOTP plaintext | Real, but zero users enrolled and `users` is REVOKEd from `gi_ai_ro` — defer encryption to Phase 3 |
| `A03-F12`, `A04-F6`, `A04-F8` | Cosmetic, historical-only, or unconfigured-but-correctly-gated |
| bcrypt `_DUMMY_HASH`, `hashlib.sha256` uses | Timing-attack decoy and high-entropy-token hashing respectively — correct; bandit will flag both, so they belong in a baseline |

---

## 8. CI Hardening (Phase 2 prerequisite)

None installed. Recommendation only.

| Control | Tool | Trigger | Notes |
|---|---|---|---|
| Pre-commit secret block | `detect-secrets` | pre-commit | Generate `.secrets.baseline` first so tracked `.env.example` placeholders don't fail the build; add a hook rejecting `*.db`/`*.bak` additions — the control that would have prevented `A04-C1` |
| Python static analysis | `bandit` | per-PR | Run with a baseline: `B105/B106` will flag `_DEV_JWT_SECRET` and `_DUMMY_HASH`, `B324` the sha256 uses — all intentional. Value is catching *new* hardcoded secrets |
| Pattern analysis | `semgrep` | per-PR | Rulesets `p/sqlalchemy`, `p/python`, `p/jwt`, `p/secrets` **plus** the two custom rules drafted in the audits: `fastapi-route-without-auth-dependency` ([02](02_idor.md)) and `site-scope-falsy-test` ([02](02_idor.md), the Theme A regression guard). `p/jwt` currently finds nothing — that's the point: it locks in correct decode hygiene |
| Dependency CVEs | `pip-audit` | dependency PRs | Covers SQLAlchemy/asyncpg/PyJWT/bcrypt |
| History secret sweep | `trufflehog` | **weekly cron**, not per-PR | Walks the full object database including unreachable blobs, and verifies whether found credentials are still live. Too slow for per-PR |

---

## 9. Effort Estimate

**Total Phase 2: 11–15 person-days** (Theme A 3–4 · Theme B 4–5 · Theme C 2–3 · Theme D 1–2), plus **1–2 days** for the CI hardening in §8.

**Suggested sequencing** (risk × effort):
1. **Theme D's startup diagnostic first** — half a day, and it makes every subsequent verification self-reporting instead of manual.
2. **Theme A** — highest risk-to-effort ratio; a whole role class currently crosses the tenancy boundary, and the semgrep rule ships with the fix so the class is closed permanently.
3. **Theme B** — highest absolute risk, but the largest change and the one needing the most regression care around the dual-wall design.
4. **Theme C** — mostly independent one-line fixes; the `is_production()` canonicalization should land as a single commit touching all three call sites at once.
5. **Rest of Theme D** — permissions and denylist cleanup.

**Do NOT attempt in Phase 2 (leave for Phase 3+):** git history rewrite (decided against — §4); TOTP secret encryption (`A03-F7`, needs a key-management design); Redis-backed rate limiting (`A03-F6`, single-box deploy doesn't justify it yet); the frontend audit including `localStorage` access-token handling; the frozen legacy Streamlit app; and any password-policy work beyond the `MIN_PW` bump.

---

## 10. Audit Metadata

| | |
|---|---|
| **Files scanned** | ~60 distinct artifacts — 40 backend Python modules (`backend/api/**`, `backend/models.py`, `backend/scripts/*.sql`), 12 deploy/CI/config files (`deploy/*`, `.github/workflows/*`, `.gitignore`, `.dockerignore`, `run_api.sh`), 6 documentation sources, plus 4 binary blobs inspected by pattern-count only |
| **Route handlers examined** | 239 (all of them) |
| **Git history** | All refs; 14 pickaxe searches + 2 full-history path diffs; 51 commits identified on the `gi_database.db` path |
| **Commands run** | ~45 read-only shell invocations across four audits |
| **Rules honored** | Read-only throughout · no package installs · no git writes (`log`, `show`, `ls-files`, `check-ignore`, `cat-file`, `config --get-regexp` only) · no database access · no service start/stop/restart · no file deletions |
| **Files created** | Exactly 6, all in `docs/security/reports/`: `00_discovery.md`, `01_sql_injection.md`, `02_idor.md`, `03_auth_session.md`, `04_secrets.md`, `99_summary.md` |
| **Suspected secrets** | Counted, never printed. No credential value — not even a 6-char prefix — was read into any report |

**Confirmation: no source code, configuration, infrastructure, database, or git object was modified at any point during Phase 1.**

---

*Phase 1 complete.*
