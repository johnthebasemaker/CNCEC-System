# Phase 2 · Theme B — run log

**Findings closed:** `A01-F1` (High) · `A01-F2` (High) · `A01-F3` (Medium) ·
`A01-F5` (Low) · `A03-F10` (Medium) · `A02-F6` (Medium) · `A02-F7` (Medium)
**Date:** 2026-07-27 · **Branch:** `security/phase-2-theme-b-ai-lane`

---

## 1. Wall 1 — the SQL text gate (`ai/safety.py`)

### A01-F1 · schema-qualified bypass

The forbidden-table check anchored on the bare name immediately after
`FROM`/`JOIN`/`INTO`/`UPDATE`:

```python
r"\b(?:from|join|into|update)\s+[\"']?(users|…)\b"
```

so `FROM public.users`, `FROM ONLY users` and `FROM "public"."users"` all
passed. Replaced with **reference resolution**: capture the whole
(optionally schema-qualified, optionally `ONLY`-prefixed, optionally quoted)
table reference, then compare its **base name** against the denylist.

Verified — all seven audit-supplied bypasses now blocked, and seven
legitimate ERP queries (including `FROM public.inventory` and
`WHERE "Supplier" = 'users'`) still pass. Zero false positives.

### A01-F2 · denylist omissions

`FORBIDDEN_TABLES` grew from 4 names to 16, adding `phone_otp`, `employees`,
`mh_employees`, `whatsapp_outbox`, `email_outbox`, `app_notifications`,
`pending_summary_notifications`, `system_audit_log`, `ai_jobs`, `bug_reports`,
`entry_attachments`, `mtc_documents`.

### A01-F5 · configuration introspection

`current_setting`, `set_config`, `version()`, `inet_server_*`, `dblink`,
`lo_import/export`, `has_*_privilege`, `txid_current`, `query_to_xml` (call
form) plus the niladic `current_user`, `session_user`, `current_role`,
`current_catalog`, `current_database`, `current_schema(s)`. None carried a
`pg_` prefix, so the existing catalog pattern never saw them.

## 2. Wall 2 — the `gi_ai_ro` role (`create_ai_readonly_role.sql`)

### A01-F2 / A01-F3 · grants inverted to an allowlist

Was `GRANT SELECT ON ALL TABLES` + `ALTER DEFAULT PRIVILEGES … GRANT SELECT`
minus a five-name REVOKE — fail-open twice over: every future migration's table
became readable automatically, and six sensitive tables were never in the
REVOKE at all.

Now: `REVOKE ALL ON ALL TABLES`, `ALTER DEFAULT PRIVILEGES … REVOKE`, then
`GRANT SELECT` on exactly the nine tables `SCHEMA_HINT` advertises
(`inventory`, `receipts`, `consumption`, `returns`, `pr_master`,
`purchase_orders`, `sme_recipe`, `sme_equipment`, `sme_sqm_progress`).

**Applied to the local mirror: 69 tables granted → 9.** Verified by connecting
as `gi_ai_ro`: `users`, `phone_otp`, `employees`, `whatsapp_outbox`,
`email_outbox`, `system_audit_log`, `entry_attachments`, `refresh_sessions`,
`bug_reports` all return `permission denied`; `receipts` still returns rows.

### A01-F3 · runtime assertion

The REVOKEs are wiped by every `dual_ci`/cutover reload and re-applied by
operator ritual — nothing noticed when they weren't. Added
`analytics.ro_wall_status()`, which probes four sensitive tables as `gi_ai_ro`
and is called from the app lifespan, printing
`[ai] read-only wall: OK|DEGRADED — <detail>` at boot. Never fatal, so a dev box
without the role still starts.

**Negative-verified**: granting `SELECT ON users` back made it report
`{'ok': False, 'detail': 'AI read-only role CAN READ users — re-run
backend/scripts/create_ai_readonly_role.sql'}`; revoking restored `ok: True`.

## 3. Lane IDOR + audit trail

| Finding | Fix |
|---|---|
| `A02-F6` `/ai/badge/{id}` | Hide employees positively assigned to a **different** site, answering exactly as for an unknown badge |
| `A02-F7` `/ai/submission-summary` | Compare the row's site against the caller's scope → 404 on mismatch; a cross-site request stays visible to **both** parties |
| `A03-F10` `/ai/query`, `/ai/nl-search` | New `_audit_ai_query()` writes an `AI_QUERY` row: asker, lane, scope, question, and the SQL actually executed. Best-effort — an audit failure never turns a working answer into a 500 |

### Deliberate deviation from the audit's suggested fix — `A02-F6`

The audit proposed copying `documents.py:308`, which denies any row whose
`Site_ID` doesn't equal the scope — **including blank**. Applying that broke a
pre-existing gate check (`badge verify: active employee found`), and the reason
matters: `employees."Site_ID"` is nullable, has no default, and sits last in the
table, i.e. it postdates most rows. Denying blank would have made every
employee recorded before that column invisible to scoped store keepers, killing
badge scanning in production.

Implemented instead: blank `Site_ID` means *unassigned staff*, still scannable;
only a **different** site's employee is hidden. That closes the leak the finding
actually describes (another site's roster) without the regression. Covered by a
dedicated assertion.

## 4. Test evidence — suite AS (14 checks)

Gate bypasses, denylist coverage, introspection blocking, false-positive
control, `ro_wall_status()`, per-table `permission denied` proof, allowlist
still readable, badge scoping (own / away / admin / unassigned),
submission-summary scoping (scoped 404, admin 200), and an `AI_QUERY` audit-row
delta asserting the username, lane and question are recorded.

## 5. Gates

| Gate | Before | After |
|---|---|---|
| `backend.api.service_tests` | 795 / 0 | **809 / 0** (+14) |
| Playwright E2E | 39 / 39 | **39 / 39** |
| `legacy/bug_check.py` | 599 / 0 | **599 / 0** |
| `npm run build --prefix frontend` | ✅ | ✅ |

`gi_database.db` sha256 verified identical (`shasum -c: OK`); never staged.

## 6. Operator action required

`create_ai_readonly_role.sql` must be re-run **on production** after the final
data load — the same post-reload ritual as before, but now the boot log states
whether it took.
