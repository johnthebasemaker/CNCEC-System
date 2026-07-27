# Phase 2 · Theme A — run log

**Findings closed:** `A02-F2` (High, 12 sites) · `A02-F8` (Medium)
**Date:** 2026-07-27 · **Branch:** `security/phase-2-theme-a-site-scope`

---

## 1. The bug class

`site_scope()` returns `''` for a scoped user bound to no site. `''` is falsy,
so `if site_id:` **drops the site filter** and serves every site's rows instead
of none. This is not a corner case: registration forbids `warehouse_user` and
`logistics` accounts from carrying a site (`auth.py:575-579`), so `''` is the
permanent steady state for a whole role class.

## 2. Fix shape

Rather than change `site_scope()`'s return type — which would ripple through
~40 already-correct call sites — three helpers were added to `auth.py` and every
affected consumer routed through them:

| Helper | Contract |
|---|---|
| `site_filter_applies(scope)` | `scope is not None` — emit the SQL predicate; `''` still filters and matches nothing |
| `site_row_visible(scope, row_site)` | Row-level check for direct fetches by id; `''` matches no row |
| `resolve_site_write(user, requested)` | `resolve_site_param` + 403 when a scoped caller has no site — a write must never fall back to a client-supplied `Site_ID` |

## 3. Sites changed

| Audit ref | File | Was | Now |
|---|---|---|---|
| a | `dashboard.py:32` | `resolve_site_param(...) or None` | `site_filter_applies` |
| b/c | `ai/analytics.py` `_site_clause` + 8 `:site` bindings | `if site_id` | `is not None` |
| d | `hod.py:516` → `services/procurement.py:116` | `site or None` / `if site_id:` | pass-through / `is not None` |
| e | `entry.py:512` | `site_id or None` | `site_filter_applies` |
| f | `entry.py:806` | `if scope and ...` | `site_row_visible` |
| g | `entry.py:405` | `resolve_site_param(...) or site_id` | `resolve_site_write` |
| h | `entry_docs.py:132` | `scope if scope else site_id` | `resolve_site_write` |
| i | `entry_docs.py:236` | `scope if scope else site_id` | resolver, site-less → no rows |
| j | `entry_docs.py:250` | `if site:` | `site_filter_applies` |
| k | `entry_docs.py:202` | `scope is not None and scope and ...` | `site_row_visible` |
| l | `entry_docs.py:289` | same | `site_row_visible` |
| A02-F8 | `receiving.py:26` + `services/warehouse.py:389` | `user["site_id"] or None` / `if actor_site and` | `site_scope(user)` / `is not None` |
| — | `ai/submission_stats.py:60` | `if site_id:` | `is not None` — same class, not in the audit list; reachable from `/entry/snapshot` |

**Deliberately untouched** (audit §5 "Do NOT touch"): `manhours.py`, `sme.py`,
`main.py:_site()`, `crud.py`, `stock.py`, `hod.py`'s `_guard_pending_site`,
`warehouse.py`'s `_guard_row_warehouse` — already fail-closed.

## 4. Test evidence — suite AR (18 checks)

New suite `test_siteless_scope_fail_closed()`. Fixtures: three site-less
accounts (`SVCR-wh` warehouse_user, `SVCR-sk` store_keeper, `SVCR-hod` hod),
all data seeded at CNCEC — a site none of them owns.

**The suite was run against the reverted (vulnerable) source to prove it
catches the exploits.** 12 of 13 reproduced:

| Check | Unpatched result |
|---|---|
| ar-a dashboard | `valuation=900.0`, 17 cross-site chart SAPs |
| ar-bc `_site_clause` | returned `''` for `''` — filter dropped |
| ar-d PR lines | 1 CNCEC line returned |
| ar-e snapshot | `stock=90.0` (CNCEC's) |
| ar-f returnable | **200 — CNCEC loan closed** |
| ar-h attachment | **201 — file written into CNCEC** |
| ar-i WBS options | CNCEC WBS numbers returned |
| ar-j WBS list | CNCEC row listed |
| ar-k doc download | 200 — CNCEC document served |
| ar-l WBS patch | **200 — CNCEC WBS mutated** |
| ar-f8 DN receive | **201 — DN flipped to `received`** |

**Honest exception — `ar-g` (`/entry/mtc`) did NOT reproduce.** Unlike (h),
that route already called `resolve_site_param`, which 403s any mismatched site,
so the `or site_id` fallback was unreachable in practice. The check is retained
as a boundary assertion and is labelled hardening, not a closed exploit. The
audit's severity for (g) was overstated.

Four `ar-ok:` checks assert **no breaking changes**: a properly-scoped CNCEC HOD
still reads their own WBS rows and PR lines, and admin still gets the global
valuation and the real global stock.

## 5. Gates

| Gate | Before | After |
|---|---|---|
| `backend.api.service_tests` | 777 / 0 | **795 / 0** (+18) |
| Playwright E2E | 39 / 39 | **39 / 39** |
| `legacy/bug_check.py` | 599 / 0 | **599 / 0** |
| `npm run build --prefix frontend` | ✅ | ✅ |

`gi_database.db` sha256 verified identical before and after (`shasum -c: OK`);
never staged, never opened for write.
