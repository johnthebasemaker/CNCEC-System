# Phase 2 · By-id scope mop-up — run log

**Findings closed:** `A02-F3` (High) · `A02-F4` (High) · `A02-F5` (High)
**Date:** 2026-07-27 · **Branch:** `security/phase-2-mop-up` (off `main`, which
already carries Themes A/B/C via PRs #6/#7/#8)

---

## 1. Why these were still open

All three are **High** severity in [02_idor.md](../reports/02_idor.md) and listed
in §2 of [99_summary.md](../reports/99_summary.md), but the §5 "Phase 2 Fix
Queue" assigned findings only to Themes A–D and never mentioned F3, F4 or F5.
They fell through the gap between the two sections.

## 2. One shared bug shape

Every one of them is the same defect: **the list endpoint is correctly scoped,
the sibling by-id route is not.** The row was reached through the path
parameter alone, so an integer or a DN number was the only thing between a user
and another site's or warehouse's data.

| Finding | Route | What the gate actually was |
|---|---|---|
| `A02-F3` | `GET /requests/{id}/items` | `get_current_user` — authentication only |
| `A02-F4` | `POST /requests/{id}/{approve,reject}` | `require_roles("store_keeper")` — a role gate, no site check anywhere in the chain |
| `A02-F5` | `GET /warehouse/assignments/{id}/items`, `GET /warehouse/dns/{dn}/items` | Router role gate only; **the handlers took no `user` parameter at all**, so `warehouse_scope()` could never be consulted |
| `A02-F5` | `GET /site/incoming-dns/{dn}/items` | `get_current_user` — authentication only |

## 3. Fixes

Reused the existing reference guards rather than inventing new ones:

- **`requests.py`** — new `_guard_smr_site()`, shaped like
  `hod._guard_pending_site`: unrestricted callers pass, a missing row passes
  through so the service still raises its own not-found, and a site-less scoped
  caller (`''`, the Theme A case) matches nothing. Applied to `items`
  (**404**, so a direct fetch doesn't confirm the id exists — the `crud.py:127`
  convention) and to `approve` / `reject` / `cancel` (**403**, inside the
  `session.begin()` block, where the boundary should be visible to the actor).
  `cancel` already enforced the stricter "must be your own request" rule; the
  guard is added so every by-id route on the router carries the same check.
- **`warehouse.py`** — both handlers now take `user: dict = Depends(_ROLE)` and
  call the existing `_guard_row_warehouse()`.
- **`receiving.py`** — site check against `site_scope(user)` using
  `site_row_visible`, returning **404**.
- Two service-layer lookups added for the guards: `supervisor.smr_site()` and
  `warehouse.dn_site()`, keeping table access in the service layer as the rest
  of these modules do.

Also removed the stale `# TODO(security): …` comment above
`GET /requests/{id}/items`, which is now resolved.

## 4. Test evidence — suite AU (13 checks)

Fixtures: two site-scoped store keepers, two warehouse-bound users, two
warehouses with their own POs/assignments/DNs, and three supervisor requests
(one at `CNCEC`, two at `OTHERSITE`).

**Verified by reverting the source fix and re-running with the tests in place —
all six exploit checks reproduce against the unpatched code:**

| Check | Unpatched result |
|---|---|
| `au-f3` request lines | **200** — the other site's line returned |
| `au-f4` reject / approve | **200 / 200** — both succeeded independently |
| `au-f4` state | statuses `['rejected', 'approved']`, **`staged=1`** — a row was written into the other site's ledger |
| `au-f5` assignment items | **200** — `"SVCU secret line for WH-SVCU-B"` |
| `au-f5` warehouse DN items | **200** — `"SVCU secret DN line for WH-SVCU-B"` |
| `au-f5` site DN items | **200** — same payload, to a level-0 user at another site |

The `A02-F4` test was corrected mid-run: the first version called `reject` then
`approve` on the *same* request, so `approve` returned 409 merely because the
status had already changed — the approve exploit was not actually demonstrated.
Split across two still-pending foreign requests, both now return 200 unpatched,
which is the real finding.

Five `still readable` / `admin` checks assert **no breaking change**: own-site
and own-warehouse fetches still return their rows, the SK's own request still
approves normally, and admin (unscoped) still reads every site's data.

## 5. Gates

| Gate | Before | After |
|---|---|---|
| `backend.api.service_tests` | 824 / 0 | **837 / 0** (+13) |
| Playwright E2E | 39 / 39 | **39 / 39** |
| `legacy/bug_check.py` | 599 / 0 | **599 / 0** |
| `npm run build --prefix frontend` | ✅ | ✅ |

`gi_database.db` sha256 verified identical before and after (`shasum -c: OK`);
never staged.

## 6. Remaining

With this branch, **every High and Critical finding from the Phase 1 audit is
closed.** Theme D (config discipline — `A04-F2/F3/F4/F7`, all Medium/Low) is in
progress on `security/theme-d-config-discipline`. `A03-F7` (TOTP secrets at
rest) remains deferred to Phase 3 by the audit's own recommendation.
