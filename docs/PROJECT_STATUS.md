# PROJECT STATUS — resume here (updated 2026-07-26 · FEATURE-COMPLETE + native apps — only the Hetzner deployment remains)

**This is the single source of truth for "where we left off."** A fresh chat
should read this file, then [`ARCHITECTURE.md`](ARCHITECTURE.md) (**the full
system map: backend modules, DB traps, entry gates, rate limiting, AI routing,
Excel-sync mechanics, PWA/offline, test commands — read it before touching
code**), then [`REPO_MAP.md`](../REPO_MAP.md) (segregation contract),
[`NEW_STACK_HANDOFF.md`](NEW_STACK_HANDOFF.md) (how-to-work rules), and
[`POSTGRES_MIGRATION.md`](POSTGRES_MIGRATION.md) §8 (the complete per-slice
run log — the project's full history lives THERE, not here).
Legacy/SME rules: [`handoff.md`](../handoff.md) (SME Canon).

---

## 0. Current state in one paragraph

**The GI Hub v2 stack (React 19 + FastAPI + PostgreSQL 16) is
FEATURE-COMPLETE and deployment-ready.** Everything through the 2026-07-18
five-phase pre-deploy batch is shipped and green: the full legacy parity
program, Man-Hours, the Intelligence layer (AI-0…AI-5), the SME rebuild
S1–S6 + Smart Calculator, native WhatsApp/email/notifications (inbound
webhook included), the entry-document/MTC/WBS gates, the Playwright E2E
suite, the production cutover script, the Bulk Excel Import feature, the
handwritten-OCR spec pipeline (17-column TSV export), the Surface-Shields
system-first issue workflow, global UI polish (sticky headers, smart
decimals, scoped reports), fixed CI/CD, and the Admin Bug Tracking Engine
with its coding-agent prompt generator. The CNCEC tracking workbooks are
fully injected (stock **429/429** vs the workbook; SME reseeded to the
renumbered system codes 1–10 with exact SAP joins). On top of that, the
**2026-07-24…26 native program** shipped: Capacitor (Android/iOS) + Tauri
(Windows/macOS) wrappers with an automated GitHub-Releases pipeline,
**RTR auth** (refresh-token families; 90-day native sessions), the
Outlook-style **Send/Receive sync engine**, the **QR sticker + scan-to-
dashboard ecosystem**, and native production API routing (`VITE_API_URL` +
CSP/CORS). The repo is now PUBLIC at `johnthebasemaker/GI_Hub_Project`
with tags v0.1.0–v1.0.1 (desktop installers already on the Releases page).
**The ONLY remaining work is the Hetzner production deployment** (runbook:
`tools/migration/README.md`; deploy kit: `docs/DEPLOY.md` + `deploy/`),
plus one Cloudflare dashboard action for the native apps (§3.6).

## 1. Gates (all green locally — 2026-07-26)

| Gate | Result | Command |
|---|---|---|
| Backend service tests | **777/0** (suites A…AQ) | `DATABASE_URL=postgresql+psycopg2://postgres@127.0.0.1:5433/gihub JWT_SECRET=ci-only-service-test-secret-key-32bytes-min .venv/bin/python -u -m backend.api.service_tests` |
| Playwright E2E | **39/39** (~15 s, own throwaway DB) | `cd tests/e2e && npm test` |
| Legacy regression | **599/0** | `.venv/bin/python legacy/bug_check.py` |
| Frontend | build + `tsc -b` ✅ | `npm run build --prefix frontend` |
| SME engine parity | 509 comparisons | `npm run parity:sme --prefix frontend` |
| Alembic | single head **`f1a7c9e83b52`** (refresh_sessions/RTR) | see ARCHITECTURE §8 |
| Release pipeline | desktop ✅ (dmg/exe/msi on v0.1.0–v1.0.1) · Android fixed (JDK 21) — next tag should attach the `.apk` | `git tag vX.Y.Z && git push origin vX.Y.Z` |

⚠️ **`postgres-dual-ci.yml` has NEVER passed on the GitHub runner** (30/30
failures since 2026-07-07, always at the `legacy/bug_check.py` step) while
the same tree/deps/package-set pass 599/0 locally under every simulated CI
condition — cause is Linux-runner-specific and unresolved. The workflow now
re-emits the failing ❌ checks as public `::error::` annotations and uploads
`bugcheck_ci.log` + `BUG_REPORT.md` as an artifact, so the FIRST push after
2026-07-26 will name the exact failing checks on the run page.

⚠️ `tools/parity_check.py` **fails against the live mirror BY DESIGN** —
PostgreSQL is permanently ahead of the frozen SQLite since the Excel
injection. It stays meaningful only on CI / a freshly-reloaded mirror.

## 2. What shipped (compressed — full history in POSTGRES_MIGRATION.md §8)

- **Parity + platform (…2026-07-12):** 10-slice parity build · Man-Hours ·
  AI-0…AI-5 (assistant, OCR lanes, NL→SQL with the `gi_ai_ro` second wall,
  two-lane `/ai/query`) · SME S1–S5 dual-engine rebuild · feature-gap P0–P6 +
  I-A/I-B + deferred-MED · Phase 7/7b/7c WhatsApp + SMTP + ubiquitous
  `dispatch()` · inbound webhook (STOCK / RESET PASSWORD, HMAC + penalty box)
  · evening digest · dual-OTP phone change · UAT rounds (E.164, exec PDF,
  cutover script `tools/migration/cutover_migrate.py` verified `--strict`).
- **Cutover day (2026-07-13):** SME **S6 Master Data CRUD** · **Phase B
  restructure** (`legacy/` · `tools/` · `data-archive/`) · Excel injection
  #1 · Bulk Excel Import (`/import/{kind}` + BulkImportPage) · C3 OCR doc
  assist · QR/returnables parity · role-based `docs/USER_MANUAL.md` v2.
- **Pre-deploy batch (2026-07-18):**
  1. **SME SAP-code overhaul** — `sme_recipe.SAP_Code` +
     `sme_inventory_seed.SAP_Code` (alembic `b3f2a9c47d18`); recipe identity
     (code, material, SAP); system codes RENUMBERED 1–10 via
     `excel_sync.py --sme-reseed` (guarded wholesale SME replace); header-
     name-driven sync w/ unknown-column warnings; stock 429/429.
  2. **AI** — ask-data deep filters (category ILIKE + material-family
     keywords joined through the SME SAP codes, template lane = safe for
     scoped users) · **handwritten-OCR spec** implemented stage-for-stage
     (`ai/handwritten.py`, spec vendored at `docs/features/handwritten-ocr`)
     with the 17-column legacy TSV export.
  3. **SME/SK portals** — Surface-Shields **system-first issue workflow**
     (`/entry/lining-systems`; Done vs Pending SQM; `LS <code>` remark) ·
     **🧮 Smart Calculator** (`/sme/calculator`: For_1_SQM × SQM demand,
     pack counts, live stock coverage, explanations).
  4. **UI polish** — sticky headers on all ~93 tables · smart decimals
     (`lib/format.ts`) · report column scoping (+Material description).
  5. **Infra** — CI diagnosed & fixed (see ARCHITECTURE §8) · **Bug
     Tracking Engine** (severity/rollback/safety triage on `bug_reports`,
     `GET /admin/feedback/{id}/prompt` self-contained coding-agent prompt,
     `.md` digest export).
- **Native-app program (2026-07-24…26, commits ff3ce5b…65e2653):**
  1. **Diagnostics** — `tools/diagnose_sync.py` sync doctor (+
     `docs/DEBUGGING.md`).
  2. **Native shells + sync engine** — Capacitor scaffold (`android/`/`ios/`
     gitignored, regenerated per build) + Tauri v2 (`frontend/src-tauri/`
     COMMITTED, identifier `com.giinventory.hub.desktop`) · strict OTA (SW
     poll every 15 min + on refocus) · **Send/Receive** header button
     (`SyncControls.tsx`: flushQueue + invalidate ALL queries) · sync-cap
     setting `gi_sync_interval_min` (1–120 min, live re-arm).
  3. **QR ecosystem** — `GET /documents/material-stickers` (hod/admin;
     2×6 full-bleed A4 replica of the operator's CNCEC sticker sheet;
     name auto-shrink; QR = SAP_Code) · 📷 header scanner →
     `MaterialCardModal` via `GET /stock/material-card` (site-scoped
     stock + 30-day gap-free Recharts trend; suite AP).
  4. **Release pipeline** — `release-desktop.yml` (macos-14 + windows,
     `npx tauri build`) + `release-android.yml` (ubuntu, **JDK 21** —
     Capacitor 8 hardcodes Java 21) → assets on the tag's GitHub Release;
     triggers = `v*` tags + workflow_dispatch ONLY.
  5. **RTR auth (`refresh_sessions`, alembic `f1a7c9e83b52`)** — refresh
     cookie is a signed JWT (jti/fam/client claims); login `client_type`
     'web' = 7-day family / 'native' = 90-day; rotation stays in-family;
     **replaying a revoked token revokes the WHOLE family** (other
     devices survive) + SESSION_REUSE audit; logout + admin revoke are
     family-wide; `auth_sessions` = legacy revoke-only; table is in the
     `gi_ai_ro` REVOKE set + AI safety FORBIDDEN_TABLES; suite AQ.
  6. **Native API routing** — builds inject
     `VITE_API_URL=https://gi.giinventory.com/api` (`API_BASE` in
     client.ts; web builds stay relative `/api`); axios withCredentials;
     CORS defaults include the fixed shell origins; refresh cookie
     `SameSite=None;Secure` under `GI_ENV=production`; explicit Tauri CSP;
     `logApiFailure()` console diagnostics incl. Cloudflare-Access
     detection; Apple-Silicon 3-step install fix documented.

## 3. Deployment — the one remaining task

Follow `tools/migration/README.md` end-to-end. Highlights:
1. Provision Hetzner CPX42 · `deploy/` kit (`docs/DEPLOY.md`) · TLS ·
   Cloudflare Tunnel (`gi.giinventory.com`; rate-limiter reads
   CF-Connecting-IP).
2. `ollama pull` the 3 models; `create_ai_readonly_role.sql` + set
   `GI_AI_RO_URL` (password-protected in production).
3. Final data load: `cutover_migrate.py --strict --wipe` from
   `gi_database.db`, **then the Excel re-sync + SME reseed** (the runbook's
   exact commands — the injection lives only in PG).
4. `deploy/.env` secrets (`GI_ENV=production`, `JWT_SECRET`, `WHATSAPP_*`
   incl. webhook verify-token/app-secret, `SMTP_*`, `EMAIL_LOGISTICS_TO`,
   `PUBLIC_BASE_URL`) — never in git.
5. Smoke gates against production; point users at React; `deploy-v2.yml`
   (manual) thereafter.
6. **Native apps (Cloudflare dashboard, one-time):** add a Zero Trust
   Access application for **`gi.giinventory.com/api/*`** with a **Bypass
   (Everyone)** policy — the installed apps have no Access SSO session, so
   without it every native API call dies as a CORS-killed 302 ("Server
   unreachable" with a working web portal). The API self-guards (JWT +
   roles + rate limits); Access stays on the HTML portal. Details:
   `docs/NATIVE_APPS.md` §6. Then drop the Release installers into
   `downloads/` and real-link USER_MANUAL §1.2.

**Operator TODOs still open (Meta side):** approve `gi_evening_summary`
(2 body vars, lang `en`); subscribe the webhook URL in Meta. The other
four templates are LIVE (lang `en`).

**Operator TODOs still open (server side):** generate strong `JWT_SECRET`
and `POSTGRES_PASSWORD` — both currently `CHANGE_ME` in `deploy/.env`.
`GI_ENV=production` must be set to arm the JWT boot guard (see item 4).

**Secrets already populated in `deploy/.env`:**
`WHATSAPP_WEBHOOK_VERIFY_TOKEN`, `WHATSAPP_APP_SECRET`,
`PUBLIC_BASE_URL`, `SMTP_*`, `EMAIL_LOGISTICS_TO`. Verified 2026-07-26
via Phase 1 Audit 04.

`.env.example` git history verified clean of Meta credentials (single
commit `f3d706b`, placeholders only) — Phase 1 Audit 04.

_Last verified against reality: 2026-07-26 via Phase 1 Audit 04._

## 4. Hard-won gotchas a fresh session must know

- **Never delete `system_audit_log` rows** — audit assertions are
  DELTA-counted.
- **SME engine changes** = change BOTH engines (`backend/api/sme_engine.py`
  + `frontend/src/sme/engine.ts`) + regenerate the golden in ONE commit.
  Shared half-up rounding `floor(x·10ⁿ+0.5)` — never "fix" to half-even.
- **Recipe identity is (code, material, SAP_Code)** — PU component lines
  share a Material_Code; don't collapse them. CRUD dup-checks the triple.
- After ANY mirror reload: re-run `create_ai_readonly_role.sql` (REVOKEs
  get wiped) AND the Excel sync chain (ARCHITECTURE §1).
- `gi_database.db` stays modified-but-uncommitted at repo root; **never
  stage it**; `*.xlsx` is gitignored (live operator workbooks at root).
- FEFO + over-issue stay **allow-and-log** — never add a hard block.
- Secret-scan every push range for the Meta token prefix (`EAA…`).
- antd v6: Select internals are `.ant-select-content`; virtual Table rows
  are `[data-row-key]`. The Claude preview browser throttles hidden tabs —
  verify via API/DB when clicks won't land.
- service_tests conventions: `check()` helper, unique prefixes
  (SVC6-/SVCJ-/…/SVCO-) with cleanup, per-suite `X-Real-IP` (login
  rate-limit), `GI_DOTENV=0` pin must never be removed.
- **httpx cookie trap:** `client.cookies.set(..., domain="host")` with a
  host-only domain SILENTLY drops the cookie — suite E's replay checks
  passed vacuously for months until suite AQ exposed it. Never pass
  `domain=` in tests.
- **RTR:** refresh tokens are signed JWTs in the same `gi_refresh`
  httpOnly cookie; pre-RTR opaque cookies just force one re-login.
  `refresh_sessions` must STAY in `create_ai_readonly_role.sql`'s REVOKE
  list and `ai/safety.py` FORBIDDEN_TABLES. UUID PKs broke the cutover
  sequence-reset once (MAX(uuid)) — it now targets Integer PKs only.
- **Android builds need JDK 21** (Capacitor 8 hardcodes Java 21); local
  Mac: `JAVA_HOME="/Applications/Android Studio.app/Contents/jbr/Contents/Home"`.
- Repo is PUBLIC — `gh` is NOT authenticated on this Mac (CI job logs are
  unreadable anonymously; that's why dual-CI failures now surface as
  annotations). Keep the pre-push secret scan ritual.

## 5. Docs & assets map

- `docs/ARCHITECTURE.md` — the brain. `docs/automatic_test.md` — manual
  test matrix. `docs/USER_MANUAL.md` — role-based v2 manual (+ screenshots
  `docs/screenshots/v2/`). `docs/NATIVE_APPS.md` — native build/release/
  install guide (Apple-Silicon fix, Cloudflare-Access bypass, JDK 21).
  `docs/DEBUGGING.md` — sync doctor + gates + console debugging.
  `docs/features/handwritten-ocr/` — OCR spec (preserve-exactly rules).
  `docs/DEPLOY.md` + `deploy/` — infra kit. `tools/migration/README.md` —
  cutover runbook.
- **Ops handoff PDFs:** `docs/export/` (User Manual + SOP), regenerated via
  `python tools/export_docs_pdf.py`.
- Root `USER_MANUAL.md` / `SOP.md` are the **frozen legacy** docs still
  served in-app by the API; repoint to the v2 manual post-deploy.
