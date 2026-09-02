# REPO MAP — who owns what in this repository

> **PHASE B EXECUTED 2026-07-13 (cutover day).** The physical restructure this
> file used to *schedule* has happened: the legacy app moved to `legacy/`,
> root data artifacts to `data-archive/`, and the SQLite→PG bridge tools to
> `tools/`. The repo root is now the NEW STACK's home. This file remains the
> boundary contract.

Two applications still coexist until the legacy Streamlit instance is switched
off (users are being pointed at the React app):

1. **LEGACY (being retired):** Python + Streamlit + SQLite, feature-frozen,
   now entirely under `legacy/`. Its regression gate must stay green
   (`.venv/bin/python legacy/bug_check.py` → 599/0).
2. **NEW STACK (production):** React + FastAPI + PostgreSQL — `backend/`,
   `frontend/`, `deploy/`, `tests/e2e/`, `tests/ai_eval/` at the repo root.

## Ownership

| Path | Owner | Notes |
|---|---|---|
| `legacy/` | Legacy | The complete frozen Streamlit app: `main.py`, `database.py`, `pages_internal/`, `ai/`, `services/`, `pwa/`, `scripts/` (bootstrap/ops), `tests/` (pytest), `.streamlit/`, gates (`bug_check.py`, `test_ui_crawler.py`), legacy deploy surface (`docker-compose.yml`, `Dockerfile.streamlit`, `Dockerfile.fastapi` = RAG sidecar, `docker/`, `host_setup/`), runtime dirs (`uploads/`, `logs/`, `backups/`). Run it with `GI_DB_FILE=../gi_database.db` (the DB stays at root) |
| `backend/` | New stack | FastAPI API (`backend/api/`), SQLAlchemy `models.py` (the schema contract — also verified by legacy bug_check's parity check), Alembic |
| `frontend/` | New stack | React + Vite + AntD SPA; SME TS engine twin in `src/sme/engine.ts`. **Native shells (2026-07-24):** `capacitor.config.ts` (the `android/`/`ios/` projects are GITIGNORED — regenerated per build by `npx cap add`) and `src-tauri/` (**COMMITTED** Tauri v2 scaffold incl. the explicit CSP in `tauri.conf.json` — its `connect-src` must track the API domain) |
| `deploy/` | New stack deploy | `docker-compose.prod.yml`, `Dockerfile.api`/`Dockerfile.web`, nginx, certbot, backup + v2 pipeline scripts — see `docs/DEPLOY.md` |
| `tests/e2e/` | New stack | Playwright suite (**125**) — global-setup loads its throwaway DB via `tools/migration/cutover_migrate.py` |
| `tests/ai_eval/` | New stack | The adversarial RAG audit (Phase 10 Track 4). **Tier 1 is a hard gate** and also runs inside service-test suite CQ; **Tier 2 is a scored artefact, never a gate** — it needs a live model and is stochastic. `cases/*.yaml` are the adversarial prompts; `cases/policy.yaml` pins each role's chapter allowlist as data, so widening one is a signed diff rather than a change nobody saw |
| `tools/` | Bridge + ops | `dual_ci.py` (mirror reload; imports `legacy/database.py` by design), `migrate_sqlite_to_postgres.py` (core copier), `parity_check.py` (SQLite-views ↔ PG-SQL oracle — ⚠️ fails vs the LIVE mirror by design since the Excel injection), `pg_smoke.py`, `migration/cutover_migrate.py` + `migration/README.md` (**the production cutover runbook**), **`pg_excel_sync.py`** (⭐ the preferred sync since 2026-07-27: ONE atomic transaction across all five kinds, Postgres-native `ON CONFLICT` upserts, dry-run by default, PG-only guards — imports `bulk_import.py`'s planners so mapping is never duplicated, and **must never import Pandas**), **`excel_sync.py`** (the older per-kind chain; header-name-driven, `--kinds`, `--sme-reseed`) + **`excel_sync_reconcile.py`** (post-sync ledger reconciliation), **`export_docs_pdf.py`** (manual/SOP → `docs/export/` PDFs). The bridge pieces retire once the legacy app is switched off; the Excel-sync + PDF tools are permanent ops |
| `data-archive/` | Archive | Root-level artifacts moved at Phase B: seed xlsx files, sample PO pdf, `IMG_2397.JPG`, `gi_database.*.bak`, `PyWhatKit_DB.txt`, `demo_seed.db` |
| `gi_database.db` | **Shared bridge — root by design** | The legacy SQLite system of record AND the source for `tools/dual_ci.py` / `parity_check.py` / the final production `cutover_migrate.py` load. Deliberately NOT moved (and never staged — it is live, constantly-modified data) |
| `reports_archive/` | Shared runtime | Deliberately the same directory both stacks' report archives use |
| `GI_Hub_SOP.pdf` · `GI_Hub_User_Manual.pdf` · `SOP.md` · `USER_MANUAL.md` · `build_*_pdf.py` | New stack docs | Served by `backend/api/documents.py` (repo root) and read by `ai/manual_qa.py` — must stay at root |
| `deletion.html` · `privacy_policy_whatsapp.html` · `terms.html` | Shared | Meta/WhatsApp app compliance pages (registered by URL) — do not move |
| `requirements.txt` | Shared | The one venv both Python stacks use; pulls in `backend/requirements.txt` |
| `run_api.sh` | New stack | Local backend launcher (`:8000`) — also what `bin/dev.sh` execs, so backend startup stays single-sourced |
| `bin/dev.sh` | Dev tooling | The unified local stack launcher: `localhost` \| `tunnel` \| `gi` \| `stop` \| `status` \| `logs`. Owns process-group teardown; never touches Postgres or the ROOT cloudflared daemon. Scratch state in gitignored `.dev/` |
| `.github/workflows/` | Shared | `postgres-dual-ci.yml` gates BOTH apps: `legacy/bug_check.py` + `tools/dual_ci.py` + `tools/parity_check.py` + **gi_ai_ro role provisioning** + `backend.api.service_tests` (777) + frontend build — ⚠️ its dual-ci job has NEVER passed on the GitHub runner (bug_check step; failures now surface as public annotations + artifacts). `deploy.yml` (v1, **manual-only** — server not provisioned) · `deploy-v2.yml` (manual cutover pipeline, gated) · **release pipeline (2026-07-25, `v*` tags/dispatch only): `release-desktop.yml` (Tauri dmg/exe/msi — green) + `release-android.yml` (Capacitor APK, JDK 21 required)** — both inject `VITE_API_URL` and attach assets to the tag's GitHub Release |
| `CNCEC_Inventory.xlsx` etc. (root `*.xlsx`) | Operator data | The four live tracking workbooks `tools/excel_sync.py` reads — **gitignored, never committed** (archived snapshots live in `data-archive/`) |
| `PROJECT_HANDOVER.md` | Shared docs | **The fresh-session entry point** — locked architecture rules (PAST), current baselines (PRESENT), next phase (FUTURE) |
| `docs/` · `handoff.md` | Shared docs | New-stack brain (`ARCHITECTURE.md`) + status/migration log · ops PDFs (`docs/export/`; the manual itself lives at the repo root — **one** manual, the 2026-07-26 `docs/USER_MANUAL.md` duplicate was deleted in Phase 8) · **native build/install/release guide (`docs/NATIVE_APPS.md`) + sync-doctor debugging guide (`docs/DEBUGGING.md`)** · handwritten-OCR spec (`docs/features/handwritten-ocr/`) · SME Canon + legacy handoff |

## Rules of engagement (the short version)

1. New-stack work touches **only** `backend/`, `frontend/`, `deploy/`,
   `tests/e2e/`, `tests/ai_eval/`, `docs/`.
2. Never edit `legacy/**` for new-stack work; the legacy gate
   (`legacy/bug_check.py` 599/0) must stay green after every change until the
   Streamlit instance is switched off.
3. ~~SME `sme_*` read-only freeze~~ **lifted at cutover (Phase S6, 2026-07-13)**
   — Master Data CRUD lives in `backend/api/sme_master.py` (exact-lock
   {hod, admin}, audited). The rest of the Canon holds: explicit-PK ordering,
   `sme_inventory_seed` never mingles with ERP `inventory`.
4. **PostgreSQL is AHEAD of the frozen SQLite** (Excel injection). After any
   `dual_ci`/cutover reload: re-run `backend/scripts/create_ai_readonly_role.sql`
   AND the Excel sync chain (`tools/excel_sync.py --commit` →
   `excel_sync_reconcile.py --commit` → SME `--sme-reseed` — exact recipe in
   ARCHITECTURE §1). `tools/parity_check.py` is only meaningful on CI or a
   freshly-reloaded mirror.
5. Two deployment surfaces until the legacy switch-off — legacy =
   `legacy/docker-compose.yml`, new stack = `deploy/`. Don't mix them.
6. **SME engine parity contract:** `frontend/src/sme/engine.ts` and
   `backend/api/sme_engine.py` are proven equal against
   `backend/api/sme_parity_fixture.json`/`sme_parity_golden.json` (**1,276
   comparisons**; `service_tests` suite G + `npm run parity:sme`). Any numeric
   change = change BOTH engines + regenerate the golden in ONE commit.
7. **SME material identity is `(Material_Code, SAP_Code)`** (locked
   2026-07-30) — recipes AND stock. Never pool by `Material_Code` alone; a
   multi-part system is four physical drums sharing one code. Details:
   [`PROJECT_HANDOVER.md`](PROJECT_HANDOVER.md).
8. **Frontend tables import `Table` from `src/lib/smartTable.tsx`**, not from
   `'antd'` — that wrapper is what gives every grid sorting + filtering.
9. Audit rows are never deleted (`system_audit_log`); tests use delta counts.
