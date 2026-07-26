# Phase 1 Security Audit — Discovery Report (00)

**Date:** 2026-07-26
**Scope:** Backend only (`backend/`) — FastAPI + PostgreSQL + auth + secrets handling.
**Method:** Read-only filesystem listing + `grep` counts. No files outside `docs/security/reports/` were created or modified.

---

## 1. Directory map (backend, 3 levels)

```
backend/
├── __init__.py
├── models.py                  # single SQLAlchemy schema contract (~1,200+ lines)
├── requirements.txt
├── alembic.ini
├── alembic/
│   ├── env.py
│   ├── script.py.mako
│   └── versions/              # 16 migrations, single head f1a7c9e83b52 (refresh_sessions RTR)
├── scripts/
│   └── create_ai_readonly_role.sql   # gi_ai_ro role — REVOKE list for AI NL-SQL lane
└── api/
    ├── main.py                # FastAPI app factory + lifespan (3 background daemons)
    ├── config.py              # env loading (dotenv .env + deploy/.env), DATABASE_URL normalisation
    ├── db.py                  # async engine / session dependency
    ├── auth.py                # login, JWT, RTR refresh, TOTP 2FA, role/site/warehouse scoping
    ├── ratelimit.py           # in-house rate limiter (per-endpoint deps, buckets, PenaltyBox)
    ├── admin.py  console.py  crud.py  dashboard.py  documents.py
    ├── entry.py  entry_docs.py  bulk_import.py
    ├── exec_pdf.py  exec_summary.py  weekly_report.py  report_center.py  reports.py
    ├── hod.py  logistics.py  warehouse.py  receiving.py  requests.py
    ├── lining_analytics.py  manhours.py  notifications.py  sla.py
    ├── sme.py  sme_engine.py  sme_master.py  sme_export_layouts.py
    ├── stock.py  webhook.py  service_tests.py
    ├── ai/
    │   ├── router.py  query_router.py  safety.py  client.py
    │   ├── analytics.py  fuzzy.py  handwritten.py  jobs.py
    │   ├── manual_qa.py  ocr.py  pdf_extract.py  submission_stats.py
    └── services/
        ├── emailer.py  ledger.py  notifications.py  procurement.py
        ├── supervisor.py  warehouse.py  whatsapp.py
```

Out of scope for Phase 1 (noted for later phases): `frontend/`, `deploy/`, `.github/workflows/`, `legacy/` (frozen Streamlit app), `tools/`, `tests/e2e/`.

## 2. Auth stack in use

| Component | Library | Version pin (backend/requirements.txt) |
|---|---|---|
| Password hashing | `bcrypt` | unpinned (`bcrypt`) |
| JWT sign/verify | `PyJWT` | `>=2.8` |
| TOTP 2FA | `pyotp` (via shared root `requirements.txt`) | see root requirements |
| Session model | Custom RTR — 15-min access JWTs + signed refresh JWT (jti/fam/client claims) in `gi_refresh` httpOnly cookie, tracked in `refresh_sessions` table (alembic head `f1a7c9e83b52`); legacy `auth_sessions` table is revoke-only | n/a (in-house, `backend/api/auth.py`) |
| Rate limiting | In-house (`backend/api/ratelimit.py`) — no slowapi | n/a |

Notes captured for the auth audit (03): `auth.py`'s docstring states "a dev default is used if unset" for `JWT_SECRET` — weak-default check applies. Refresh TTLs: web 7 d / native 90 d by `client_type`; replay of a revoked refresh token revokes the whole token family.

## 3. Roles / permissions

Roles exist and are enforced via FastAPI dependency factories in `backend/api/auth.py`:

- **Level ladder:** `store_keeper` 0 · `warehouse_user`/`supervisor` 1 · `hod` 2 · `logistics` 3 · `admin` 4 (`require_level(min_level)`).
- **Exact-role locks:** `require_roles(*roles)` — admin is always admitted.
- **Site scoping:** `site_scope(user)` — level < 3 users are pinned to their `Site_ID`; empty site = fail-closed ("matches nothing"); `resolve_site_param()` 403s a scoped user requesting another site.
- **Warehouse scoping:** `warehouse_scope(user)` — `warehouse_user` pinned to bound `Warehouse_ID`, unbound = matches nothing.
- Registration restricted to non-admin roles (`_REGISTERABLE_ROLES = set(ROLE_META) - {"admin"}`); admin approval required.

These scoping helpers are the primary IDOR surface for audit 02 (verify every router actually applies them).

## 4. Route handler count

Counted via `@router.*` / `@app.*` decorators across `backend/api/**/*.py`:

| Method | Count |
|---|---|
| GET (router) | 113 |
| POST | 96 |
| DELETE | 12 |
| PATCH | 10 |
| PUT | 2 |
| GET (app-level, `main.py`) | 6 |
| **Total** | **239** |

Router modules: 27 files instantiate `APIRouter` (30 instances total — `console.py` holds 4).

## 5. Raw SQL vs ORM usage

| Signal | Count | Notes |
|---|---|---|
| `text(...)` raw-SQL constructs | **89** across 26 files | Heaviest: `entry.py` (12), `ai/analytics.py` (12), `service_tests.py` (9), `console.py` (7), `sme.py` (6) |
| ORM `select(...)` | **245** | |
| `.execute(...)` calls (both styles) | 778 | |
| **f-string-built `text(f"...")` sites** | **23 flagged** | `console.py:414` · `warehouse.py:286,299,302` · `hod.py:276,349,452` · `stock.py:213,217,318,331` · `lining_analytics.py:89` · `entry.py:520,575` · `ai/analytics.py:330,337,427` · `services/warehouse.py:44,79,364` · `services/procurement.py:81,99,126` |

The 23 f-string sites are the priority worklist for audit 01 (SQL injection). Each must be checked for whether the interpolated fragment is user-controlled or a static/allowlisted SQL fragment with bound params. The AI NL→SQL lane (`ai/safety.py` `is_safe_select` gate + `gi_ai_ro` read-only role) is a second, deliberate raw-SQL surface to review.

## 6. Config / secrets loading

- `backend/api/config.py` loads repo-root `.env` then `deploy/.env` at import time via `python-dotenv` (`override=False`; skipped entirely when `GI_DOTENV=0`). All secrets are then read from `os.environ` (no pydantic-settings).
- `DATABASE_URL` defaults to `postgresql+asyncpg://postgres@127.0.0.1:5433/gihub` (local trust-auth mirror) when unset.
- Secrets expected in gitignored `deploy/.env`: `JWT_SECRET`, `WHATSAPP_*` (Meta token, webhook verify-token, app secret), `SMTP_*`, `GI_AI_RO_URL`, `PUBLIC_BASE_URL`.
- Root `.env.example` exists at repo root — to be checked in audit 04 (project history notes a real token was once briefly present there and blanked pre-commit; git-history check flagged for that audit).

## 7. Security tooling availability (report-only check)

Not found in `backend/requirements.txt` / root `requirements.txt` / `frontend/package.json`: `bandit`, `semgrep`, `pip-audit` — **none installed**, so per instructions they will not be run; each audit report will carry a "Tooling Recommendation" section instead.

---

*Next step (pending user approval): Audit 01 — SQL injection, starting from the 23 f-string `text()` sites and the 89 raw-SQL constructs above.*
