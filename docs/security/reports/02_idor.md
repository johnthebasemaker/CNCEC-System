# IDOR (Insecure Direct Object Reference) Audit

## Summary
- Files scanned: 31 (all routing modules + the 4 service layers they delegate row access to)
- Route handlers examined: 239
- Findings: 11 (Critical: 1, High: 4, Medium: 4, Low: 2)
- Status: **ISSUES FOUND**

**Headline:** the scoping helpers in `auth.py` are correct — I verified the fail-closed claim by reading the code paths, not the docstrings. The problem is **downstream**: `site_scope()` deliberately returns the empty string `''` for a scoped user with no site, and roughly a dozen consumers test that value for *truthiness* (`if site_id:`) rather than for *None-ness* (`if site_id is not None:`). Because `warehouse_user` and `logistics` are **required by the registration rules to have no site at all** (`auth.py:575-579`), `''` is the normal steady state for a whole role, not a corner case — so those consumers silently drop the site filter and return global data. Separately, four endpoints accept a resource ID and never re-verify scope on the fetched row, and one takes its site directly from a query parameter.

---

## Part 1 — The scoping helpers themselves (audited first)

Read end-to-end at `backend/api/auth.py:232-310`. **No logic bug found in the helpers.** Verified behaviour:

| Helper | Line | Behaviour verified | Verdict |
|---|---|---|---|
| `get_current_user` | 232-239 | Requires a bearer credential (401 if absent), decodes with `_decode(..., "access")` which enforces signature **and** `scope == "access"` (so a refresh or MFA token cannot be replayed as an access token). Claims come from the signed JWT only — never from body/query. | Correct |
| `require_level(min)` | 242-249 | `user["level"] < min → 403`. Level derives from `ROLE_META` via `_public()`. | Correct |
| `require_roles(*roles)` | 252-261 | `allowed = set(roles) | {"admin"}`; 403 unless `user["role"] in allowed`. Admin always admitted (documented). | Correct |
| `site_scope(user)` | 270-277 | `level >= 3 → None` (unrestricted); otherwise `(site_id or "").strip()`. **Returns `''` for a site-less scoped user** — the documented "matches nothing" contract. The helper itself never widens access. | Correct — but see Finding #2 for the contract |
| `resolve_site_param(user, requested)` | 280-290 | Unscoped → passthrough. Scoped → 403 if `requested` differs from scope, else returns scope. Boundary is visible, not silently rewritten. | Correct |
| `warehouse_scope(user)` | 294-299 | `role != "warehouse_user" → None`; else `(warehouse_id or "").strip()`. | Correct, with the role-string caveat in Finding #9 |
| `resolve_warehouse_param` | 302-310 | Mirrors `resolve_site_param`. | Correct |

One inherited behaviour worth stating plainly: `_public()` (line 139-143) falls back to `{"label": role, "level": 0}` for an unrecognised role string. For the **level** ladder that is fail-closed (lowest privilege). For `warehouse_scope()` it is fail-**open** — see Finding #9.

Also verified as requested: **the registration endpoint genuinely blocks admin creation at the API layer**, not just in the UI. `auth.py:556-557` rejects any role outside `_REGISTERABLE_ROLES = set(ROLE_META) - {"admin"}` with a 422 *before* any write, and the row lands in `pending_users` requiring admin approval regardless. No finding.

---

## Findings

### Finding #1 — `GET /requests` takes its site filter from a query parameter, not the JWT
- **Severity:** Critical
- **File:** `backend/api/requests.py`
- **Line(s):** 76-86
- **Category:** Authorization boundary sourced from client input (cross-site data read)
- **Dependency chain:** `@router.get("")` → `user: dict = Depends(get_current_user)` only. Router `requests.py:25` declares **no** router-level dependency. `resolve_site_param()` is never called on this path.
- **Evidence:**

  ```python
  @router.get("", summary="List material requests")
  async def listing(mine: bool = False, site_id: Optional[str] = None, status: Optional[str] = None,
                    user: dict = Depends(get_current_user),
                    session: AsyncSession = Depends(get_session)):
      # Sensible defaults per role: supervisor → own; store_keeper → site pending.
      if mine or user["role"] == "supervisor":
          return {"items": await smr.list_smr(session, requested_by=user["username"], status=status)}
      scope = site_id or (user["site_id"] or None)          # ← client value wins
      if user["role"] == "store_keeper" and status is None:
          status = "pending_sk"
      return {"items": await smr.list_smr(session, site_id=scope, status=status)}
  ```

  The attacker-supplied `site_id` is preferred over the JWT's `user["site_id"]` — it is not compared against it, and `resolve_site_param` (which would 403) is bypassed entirely. `smr.list_smr` (`services/supervisor.py`) applies `site_id` verbatim with no further check.
- **Why it's a risk:** any authenticated user from level 0 upward can enumerate another site's supervisor material requests — worker names, job/tank locations, requested materials and quantities — simply by appending `?site_id=<other-site>`. This is the exact pattern flagged as Critical in the audit brief: a site boundary read from the request instead of the token.
- **Suggested fix (do NOT apply):** replace line 83 with `scope = resolve_site_param(user, site_id)` and add the `if scope == "": return {"items": []}` guard used elsewhere in the codebase.
- **Effort:** Low

### Finding #2 — Empty site scope (`''`) is treated as "no filter" by ~12 consumers, defeating the fail-closed contract
- **Severity:** High
- **File:** multiple (see table)
- **Line(s):** see table
- **Category:** Systemic contract violation — site isolation silently disabled for site-less accounts
- **Dependency chain:** all these routes *do* declare an auth dependency and *do* call `site_scope`/`resolve_site_param`. The failure is in how the returned `''` is consumed.
- **Evidence:** the helper's own docstring states the contract (`auth.py:270-277`):

  ```python
  """None → unrestricted (admin/logistics). Otherwise the only Site_ID this
  user may read — possibly '' for a site-less scoped user (e.g. a warehouse
  account), which every consumer must treat as *matches nothing* (fail-closed),
  never as a wildcard."""
  ```

  Because `'' `is falsy in Python, `if site_id:` drops the filter while `if site_id is not None:` keeps it (as `Site_ID = ''`, matching nothing). The clearest instance — reachable by a routine account:

  ```python
  # backend/api/dashboard.py:28-36
  async def metrics(site_id: Optional[str] = None,
                    user: dict = Depends(require_level(1)),          # ← warehouse_user qualifies
                    session: AsyncSession = Depends(get_session)):
      site_id = resolve_site_param(user, site_id) or None            # '' → None
      sfilter = ' AND s."Site_ID" = :site' if site_id else ''        # → no site filter
  ```

  **Why `''` is routine, not theoretical:** `auth.py:575-579` *forbids* `warehouse_user` and `logistics` registrants from carrying a site (`"{role} is a global role — no site"`). Every `warehouse_user` therefore has `Site_ID = ''` and `level = 1`, so `site_scope()` returns `''` for them on every request.

  **Source of the value in each case:** the JWT `site_id` claim (correct source) — the defect is purely the empty-value handling.

  | # | Site | Pattern | Consequence |
  |---|---|---|---|
  | a | `dashboard.py:32` | `resolve_site_param(...) or None` | `/dashboard/metrics` (level ≥1) returns **global** stock valuation, all-site burn, top-consumed |
  | b | `ai/router.py:506` | `sid` → `analytics` probes via `_site_clause()` (returns `""` when falsy) | `/ai/insights` computes over **all sites** |
  | c | `ai/router.py:551` | same | `/ai/eod-summary` covers **all sites** |
  | d | `hod.py:515` | `site or None` → `procurement.pr_lines(..., site or None)` where `if site_id:` | PR lines for **any** site |
  | e | `entry.py:512-513` | `site_id = site_id or None` | `/entry/snapshot/{sap}` stock + trend **global** |
  | f | `entry.py:806` | `if scope and (row.Site_ID or "").strip() != scope` | returnable-loan return-check **skipped** |
  | g | `entry.py:405` | `resolve_site_param(...) or site_id` | MTC upload **writes** a client-chosen `Site_ID` |
  | h | `entry_docs.py:132-133` | `site = scope if scope else site_id.strip()` | attachment **written** to a client-chosen site |
  | i | `entry_docs.py:236` | `site = scope if scope else site_id` | WBS options read for any site |
  | j | `entry_docs.py:250-252` | `if site:` | `/entry/wbs/all` lists **all** sites' WBS rows |
  | k | `entry_docs.py:202-203` | `if scope is not None and scope and ...` | attachment download site-check **skipped** |
  | l | `entry_docs.py:289-290` | `if scope is not None and scope and ...` | WBS status **mutation** cross-site |

  Correct implementations exist in the same codebase and should be the model — e.g. `crud.py:107-110`, `stock.py:245-246`, `hod.py:130-131`, `warehouse.py:88-89`, and `main.py:321-322` (`_site()` returns the `== ''` predicate rather than dropping it).
- **Why it's a risk:** multi-site isolation is the product's primary tenancy boundary. For an entire role class it is not enforced on these paths, exposing cross-site financials (valuation), operational data, and — in (g)/(h)/(l) — permitting writes attributed to a site the user does not belong to.
- **Suggested fix (do NOT apply):** make the contract mechanical instead of advisory. Either have `site_scope()` return a sentinel that cannot be silently falsy (e.g. raise/return a `NO_SITE` object callers must handle), or add a shared `apply_site_filter(stmt, col, scope)` helper and route every consumer through it; then convert each site above to the `is not None` form and add the missing `== ""` early-returns.
- **Effort:** Medium

### Finding #3 — Supervisor-request line items are readable by any authenticated user
- **Severity:** High
- **File:** `backend/api/requests.py`
- **Line(s):** 118-121
- **Category:** Unguarded direct-object fetch (no ownership or site check)
- **Dependency chain:** `@router.get("/{request_id}/items")` → `Depends(get_current_user)` (authentication only). No `require_*`, no `site_scope`, no ownership predicate. The service it calls filters on the ID alone.
- **Evidence:**

  ```python
  @router.get("/{request_id}/items", summary="Request line items")
  async def items(request_id: int, user: dict = Depends(get_current_user),
                  session: AsyncSession = Depends(get_session)):
      return {"items": await smr.smr_items(session, request_id)}
  ```

  ```python
  # backend/api/services/supervisor.py — smr_items()
  ).where(smr_items_t.c["request_id"] == request_id).order_by(smr_items_t.c["id"])
  ```

  `request_id` is a sequential integer path parameter, so the whole table is trivially enumerable.
- **Why it's a risk:** a level-0 store keeper at site A can read every material request line at every site by incrementing an ID — the bulk-list scoping is bypassed entirely by direct fetch.
- **Suggested fix (do NOT apply):** load the SMR header first, then apply the same check the mutations should use — 404 (not 403) when `site_scope(user)` is not None and the header's `Site_ID` differs. `crud.py:127-138` is the reference implementation of this pattern.
- **Effort:** Low

### Finding #4 — A store keeper can approve or reject another site's material request
- **Severity:** High
- **File:** `backend/api/requests.py` (routes) → `backend/api/services/supervisor.py` (services)
- **Line(s):** `requests.py:129-146` (approve), `requests.py:147-155` (reject); `services/supervisor.py` `approve_smr`, `reject_smr`
- **Category:** Cross-role/cross-site mutation without a resource-ownership check
- **Dependency chain:** `Depends(_SK)` = `require_roles("store_keeper")` — a **role** gate only. No site gate anywhere in the chain.
- **Evidence:**

  ```python
  # approve_smr — the only guards are existence and status
  header = (await session.execute(select(smr_t).where(smr_t.c["id"] == request_id))).mappings().first()
  if header is None:
      return {"error": "request not found"}
  if header["status"] != "pending_sk":
      return {"error": f"request already {header['status']}"}
  ...
  site_id = header["Site_ID"]          # ← taken from the row, never compared to the caller
  ```

  ```python
  # reject_smr — updates by id + status only
  res = await session.execute(update(smr_t).where(
      (smr_t.c["id"] == request_id) & (smr_t.c["status"] == "pending_sk")
  ).values(status="rejected", sk_decided_by=sk_username, ...))
  ```

  Contrast `hod.py:_guard_pending_site` (lines 95-108) and `warehouse.py:_guard_row_warehouse` (lines 39-52), which perform exactly the missing check for their own workflows — including a correct `scope == ""` fail-closed branch.
- **Why it's a risk:** approving stages `pending_issues` rows into another site's ledger under the foreign site's `Site_ID`; rejecting denies another site's workers their PPE. Both are recorded in the audit log as the acting SK, so the damage is attributable but not prevented.
- **Suggested fix (do NOT apply):** add a `_guard_smr_site(session, request_id, user)` in the shape of `hod.py:_guard_pending_site` and call it inside the `session.begin()` block of approve, reject and cancel.
- **Effort:** Low

### Finding #5 — Three line-item endpoints return another warehouse's / site's rows on direct fetch
- **Severity:** High
- **File:** `backend/api/warehouse.py`, `backend/api/receiving.py`
- **Line(s):** `warehouse.py:96-98`, `warehouse.py:158-160`, `receiving.py:42-45`
- **Category:** Bulk-scope present, direct-fetch scope missing
- **Dependency chain:**
  - `warehouse.py` router (line 25-26) declares `dependencies=[Depends(require_roles("warehouse_user", "logistics"))]` — role gate only. The two handlers below take **no `user` parameter at all**, so `warehouse_scope()` can never be consulted.
  - `receiving.py` router (line 22) declares no router-level dependency; the handler takes `Depends(get_current_user)` (authentication only).
- **Evidence:**

  ```python
  @router.get("/assignments/{assignment_id}/items", summary="PO items for an assignment")
  async def assignment_items(assignment_id: int, session: AsyncSession = Depends(get_session)):
      return {"items": await wh.assignment_items(session, assignment_id)}

  @router.get("/dns/{dn_number}/items", summary="DN line items")
  async def dn_items(dn_number: str, session: AsyncSession = Depends(get_session)):
      return {"items": await wh.dn_lines(session, dn_number)}
  ```

  ```python
  # receiving.py — any authenticated user, any DN number
  @router.get("/incoming-dns/{dn_number}/items", summary="DN line items")
  async def dn_items(dn_number: str, user: dict = Depends(get_current_user),
                     session: AsyncSession = Depends(get_session)):
      return {"items": await wh.dn_lines(session, dn_number)}
  ```

  The corresponding **list** endpoints are correctly scoped (`warehouse.py:88-89`, `warehouse.py:152-153`, `receiving.py:36-38` all resolve the param and early-return on `''`), and the sibling **mutations** call `_guard_row_warehouse` — which is precisely the "bulk scope doesn't protect direct fetch" gap named in the brief.
- **Why it's a risk:** a `warehouse_user` bound to WH-A enumerates WH-B's purchase-order and delivery-note contents (materials, quantities, lots, expiry); via `receiving.py` any authenticated user including level 0 can do the same for any DN number.
- **Suggested fix (do NOT apply):** give both warehouse handlers a `user: dict = Depends(_ROLE)` parameter and call the existing `_guard_row_warehouse(...)` before returning; for `receiving.py`, look up the DN's `Site_ID` and compare against `site_scope(user)` with the `''` branch treated as deny.
- **Effort:** Low

### Finding #6 — `GET /ai/badge/{id_number}` exposes any employee's name and phone number across all sites
- **Severity:** Medium
- **File:** `backend/api/ai/router.py`
- **Line(s):** 406-425
- **Category:** Unscoped PII lookup by enumerable identifier
- **Dependency chain:** `Depends(require_roles("store_keeper"))` — role gate only; `site_scope()` is imported in this module but not applied here.
- **Evidence:**

  ```python
  @router.get("/badge/{id_number}", summary="Verify a scanned employee badge (Tier 1)")
  async def verify_badge(id_number: str,
                         user: dict = Depends(require_roles("store_keeper")),
                         session: AsyncSession = Depends(get_session)):
      emp_t = _MD.tables["employees"]
      row = (await session.execute(select(
          emp_t.c["ID_Number"], emp_t.c["Name"], emp_t.c["Phone_Number"],
          emp_t.c["Department"], emp_t.c["status"])
          .where(func.trim(emp_t.c["ID_Number"]) == id_number.strip()).limit(1))
      ).first()
      ...
      return {"found": True, ..., "name": row.Name, "phone": row.Phone_Number or "", ...}
  ```

  The near-identical lookup in `documents.py:308-322` **does** enforce scope and is the correct model:

  ```python
  scope = site_scope(user)
  if scope is not None and (row.Site_ID or "") != scope:
      raise HTTPException(404, "no employee with that ID number")
  ```
  (Note that version is also correctly fail-closed for `''`, since `'' != row.Site_ID` for any real site.)
- **Why it's a risk:** staff PII (name + personal phone) for every site is retrievable by any store keeper, one badge ID at a time; badge IDs are printed on physical badges and encoded in the QR sheets this same system generates.
- **Suggested fix (do NOT apply):** copy the `documents.py` scope check into `verify_badge`, selecting `Site_ID` alongside the other columns.
- **Effort:** Low

### Finding #7 — `GET /ai/submission-summary` does not verify the submission belongs to the reviewer's site
- **Severity:** Medium
- **File:** `backend/api/ai/router.py` → `backend/api/ai/submission_stats.py`
- **Line(s):** `ai/router.py:672-689`; `submission_stats.py` `staged_issue_features` / `xsite_features`
- **Category:** Level check without a resource check
- **Dependency chain:** `Depends(get_current_user)` plus an inline `if user["level"] < 2: raise 403`. No site comparison on `ref_id`.
- **Evidence:**

  ```python
  async def submission_summary(kind: str, ref_id: int,
                               user: dict = Depends(get_current_user), ...):
      if kind == "staged-issue":
          if user["level"] < 2:  # HOD reviews staged issues
              raise HTTPException(403, "reviewer access required")
          feats = await substats.staged_issue_features(session, ref_id)
  ```

  ```python
  # submission_stats.py — selects on the id alone, then returns Site_ID in the payload
  ).where(pending_issues_t.c["id"] == ref_id))).mappings().first()
  ```
- **Why it's a risk:** an HOD scoped to site A can read site B's staged-issue detail (material, quantity, issued-to, 30/60-day usage statistics) by iterating `ref_id` — the same rows `hod.py` carefully guards with `_guard_pending_site`.
- **Suggested fix (do NOT apply):** after loading `feats`, compare `feats`'s `Site_ID` against `site_scope(user)` and 404 on mismatch (including the `''` case).
- **Effort:** Low

### Finding #8 — DN receipt authorization reads `user["site_id"]` directly instead of the scope helper
- **Severity:** Medium
- **File:** `backend/api/receiving.py` → `backend/api/services/warehouse.py`
- **Line(s):** `receiving.py:26-28` (`_actor_site`), `receiving.py:48-61` (route), `services/warehouse.py` `stage_dn_receipt`
- **Category:** Bypassable ownership check (falsy site disables it) + inconsistent scope source
- **Dependency chain:** `Depends(get_current_user)` only; the site check lives inside the service and is conditional.
- **Evidence:**

  ```python
  def _actor_site(user: dict) -> Optional[str]:
      """A site-bound user's site ('' for global roles like admin -> None)."""
      return user["site_id"] or None            # ← '' becomes None
  ```

  ```python
  # services/warehouse.py — stage_dn_receipt
  if actor_site and site_id != actor_site:
      return {"error": f"DN is for site {site_id}, not your site ({actor_site})"}
  ```

  With `actor_site=None` the guard is skipped entirely, so any user whose `Site_ID` is empty can receive **any** in-transit DN, staging `pending_receipts` rows at the DN's own site. A second, opposite defect: this helper ignores the level ladder, so a `logistics` user who *does* have a site set would be wrongly restricted where `site_scope()` would correctly return `None`.
- **Why it's a risk:** cross-site mutation — staged receipts appear in another site's HOD approval queue, and the DN is flipped to `received` out from under the destination site.
- **Suggested fix (do NOT apply):** replace `_actor_site()` with `site_scope(user)` and have `stage_dn_receipt` treat `''` as deny and `None` as unrestricted, matching every other guard in the codebase.
- **Effort:** Low

### Finding #9 — Authorization decisions made by role-string comparison rather than the level ladder
- **Severity:** Medium
- **File:** multiple
- **Line(s):** `auth.py:297` · `requests.py:81, 84` · `main.py:348, 354` · `ai/router.py:254, 289` · `ai/manual_qa.py:97` · `weekly_report.py:101`
- **Category:** Fragile authorization predicate (typo/new-role bypass)
- **Evidence:** the highest-impact instance is the warehouse scope helper itself:

  ```python
  # backend/api/auth.py:294-299
  def warehouse_scope(user: dict) -> str | None:
      if user.get("role") != "warehouse_user":
          return None                      # ← ANY other/unknown role = unrestricted
      return (user.get("warehouse_id") or "").strip()
  ```

  Combined with `_public()`'s unknown-role fallback (`auth.py:140`), a user whose `users.role` is misspelled (`"warehouse"`, `"Warehouse_User"`) gets `level = 0` **and** `warehouse_scope() → None`, i.e. unrestricted warehouse visibility. Others follow the same shape:

  ```python
  requests.py:81   if mine or user["role"] == "supervisor":
  main.py:348      if user["role"] in ("warehouse_user", "logistics", "admin"):
  ai/router.py:254 if user["role"] != "admin" and row["actor"] != user["username"]:
  ```
- **Why it's a risk:** these predicates fail open on any value not in the literal list; adding a role (or a data-entry typo in `users.role`) silently changes authorization with no test coverage forcing the issue. The level ladder and `require_roles` exist precisely to centralise this.
- **Suggested fix (do NOT apply):** derive from `ROLE_META` — e.g. validate `users.role` against `ROLE_META` at login and reject unknown roles outright, and express these checks as level comparisons or a shared predicate rather than inline string literals.
- **Effort:** Medium

### Finding #10 — `/admin/oversight` is reachable by logistics despite its `/admin` prefix
- **Severity:** Low
- **File:** `backend/api/console.py`
- **Line(s):** 434, 439-441
- **Category:** Privilege implied by URL prefix does not match the enforced gate
- **Dependency chain:** `oversight = APIRouter(prefix="/admin", ...)` with **no** router-level dependency; the single route declares `dependencies=[Depends(require_level(3))]`.
- **Evidence:**

  ```python
  oversight = APIRouter(prefix="/admin", tags=["admin console"])

  @oversight.get("/oversight", summary="Cross-site procurement KPIs",
                 dependencies=[Depends(require_level(3))])
  ```
- **Why it's a risk:** not a vulnerability — cross-site procurement KPIs are within the logistics remit, and level 3 is unscoped by design. It is flagged because the `/admin` prefix invites the reviewer (and the next developer) to assume `require_level(4)`, and because the audit brief asks for confirmation on every `/admin` route.
- **Suggested fix (do NOT apply):** either move it under a non-`/admin` prefix (e.g. `/logistics/oversight`, which the frontend already aliases routes for) or add an explicit comment; no behavioural change needed if level 3 is intended.
- **Effort:** Low
- **Note:** I confirmed **no** admin route is missing a gate — every `@admin.*` route in `console.py` (23 routes) inherits `dependencies=[Depends(require_level(4))]` from `console.py:58-59`, every route in `admin.py` inherits it from `admin.py:41-42`, and `sla.py:32-33` does the same. The brief's "a missing check on even one admin route is Critical" condition is **not** triggered.

### Finding #11 — Unauthenticated `/health` discloses database and deployment details
- **Severity:** Low
- **File:** `backend/api/main.py`
- **Line(s):** 268-278
- **Category:** Information disclosure on an unauthenticated endpoint
- **Evidence:**

  ```python
  @app.get("/health", tags=["meta"], summary="Liveness + DB connectivity")
  async def health(session: AsyncSession = Depends(get_session)):
      await session.execute(text("SELECT 1"))
      return {"status": "ok", "dialect": engine.dialect.name,
              "database": engine.url.database,
              "maintenance": await maintenance_on(session),
              "entities": [e["name"] for e in ENTITIES]}
  ```
- **Why it's a risk:** returns the database name, driver dialect, full entity/table inventory and maintenance state to any unauthenticated caller. Once the Cloudflare Access bypass for `/api/*` is in place (per `docs/NATIVE_APPS.md` §6) this is internet-reachable.
- **Suggested fix (do NOT apply):** reduce the anonymous payload to `{"status": "ok"}` and move the diagnostic fields behind `require_level(4)` (or a separate `/health/detail`).
- **Effort:** Low

---

## Reviewed — No Finding

### Scoping applied correctly (the reference implementations)

| Route / module | Gate | Why it passes |
|---|---|---|
| `crud.py:127-138` `GET /{entity}/{item_id}` | `get_current_user` + per-row `site_scope` | **Re-verifies scope on the direct fetch** and returns 404 (not 403) so the ID's existence isn't leaked. Handles `scope == ""` as deny. The model for Findings #3/#5/#7 |
| `crud.py:100-110` `GET /{entity}` (list) | `resolve_site_param` + `== ""` early return | Correct empty-scope handling |
| `crud.py` create/update/delete | `write_dep=require_level(3)` (`main.py:187`) | All writers are unscoped roles by construction, so no per-row site check is required |
| `hod.py` approve / reject / edit-pending / bulk-approve | `require_level(2)` + `_guard_pending_site` | Loads the row's `Site_ID` and 403s on mismatch **including `scope == ""`** |
| `warehouse.py` acknowledge / receive / submit-dn / ship | `require_roles("warehouse_user","logistics")` + `_guard_row_warehouse` | Correct row-level warehouse check, `scope == ""` denies |
| `warehouse.py` returns list / create / disposition | `_ROLE` + `warehouse_scope` / `_guard_po_warehouse` | Scoped list plus per-row PO→warehouse guard on both mutations |
| `notifications.py` (all 4 routes) | `_ctx` → `get_current_user` + `_visible(...)` predicate | `mark_read` filters by the visibility predicate so a guessed `notif_id` cannot be marked; `_ctx` deliberately re-reads site/warehouse from the live `users` row rather than trusting stale JWT claims |
| `entry_docs.py:195-201` attachment download | `get_current_user` + uploader check + level check | Ownership enforced for level < 2 (the residual `''` site gap is Finding #2k) |
| `entry_docs.py:212-226` attachment delete | uploader-or-admin | `row.uploaded_by != user["username"] and user["level"] < 4` → 403; also blocks deletion once linked |
| `documents.py:308-322` employee badge PNG | `require_level(2)` + `site_scope` compare → 404 | Correct, fail-closed on `''` |
| `documents.py:223-300, 358+` stickers / labels / badges / master exports | `require_roles("hod","admin")` / `require_level(2)` + `resolve_site_param` + `== ""` 403 | Consistent |
| `stock.py:231, 245, 259, 275` | `get_current_user` + `resolve_site_param` + `== ""` early return; `/stock/live` 403s scoped users outright | Correct — a site-scoped user cannot read the cross-site aggregate view |
| `stock.py:295-301` `/stock/material-card` | `site_scope` with explicit `== ""` → 403 | Correct |
| `main.py:281-291` `/meta/sites`, `365-389` `/meta/inventory-summary` | `get_current_user` + `site_scope` | Both handle `''` explicitly |
| `main.py:306-362` `/meta/work-queues` | `get_current_user` + `_site()` helper | `_site()` returns the `Site_ID == scope` predicate rather than dropping it, so `''` matches nothing — correct |
| `hod.py` 111/130/268/341/415/441/470 | `require_level(2)` + `resolve_site_param` + `== ""` guard | Correct |
| `manhours.py` (14 call sites) | router `require_roles("hod")` + `resolve_site_param`, helpers use `if sid is not None:` | `''` is preserved into the WHERE clause → matches nothing. Fail-closed **despite** looking unguarded at the call site |
| `sme.py` (12 call sites), `sme_master.py` (169, 466) | router `require_level(2)` / `require_roles("hod")` + `if site_id is not None:` | Same fail-closed pattern as manhours |
| `reports.py:624-640` | router `require_level(2)` + registry validation + `resolve_site_param` + `== ""` 403 + `global_only` flag | Correct, and additionally blocks scoped users from global-only reports |
| `report_center.py` (7 call sites) | router `require_level(2)` + `site_scope` | Correct |
| `console.py` `@admin.*` (23 routes) | router `require_level(4)` | Uniform |
| `admin.py` (all routes) | router `require_level(4)` | Uniform; also protects the last-admin invariant (`admin.py:129, 203, 259`) |
| `sla.py` | router `require_level(4)` | Uniform |
| `console.py` `@xsite.*` | `require_level(2)` create/list, `require_level(4)` decide | Decision gated to admin; list uses `site_scope` |
| `console.py` `@public.*` feedback | `get_current_user`; `/feedback/mine` filters `username == user["username"]` | Self-scoped by construction |
| `logistics.py` | router `require_level(3)` | Every route is unscoped-by-design (logistics/admin), so no site check applies |
| `bulk_import.py:795-805` | `require_roles("hod")` + inline admin-only check for `inventory`/`ledger` kinds | Correct two-tier gate |
| `sme_master.py` router | `require_roles("hod")` (admin implicit) | Matches the documented exact-lock |
| `exec_summary.py`, `lining_analytics.py` | `require_roles("hod")` / `require_roles("hod","logistics")` + `site_scope` | Correct |
| `ai/router.py:244-258` `GET /jobs/{job_id}` | `require_roles("store_keeper")` + `row["actor"] != user["username"]` ownership check | Correct ownership enforcement (uses a role string — see Finding #9 — but the predicate itself is sound) |
| `ai/router.py:269-300` `/jobs/from-attachment` | ownership + site check on the source attachment | Correct |
| `ai/router.py:440-451` `/nl-search` | `require_level(3)` | Unscoped roles only — consistent with the design ruling |
| `ai/router.py:469-495` `/ai/query` | `require_level(2)`; NL fallback additionally requires `scope is None and level >= 3` | The template lane passes the JWT-derived scope into `query_router.run_query`, so generated SQL never runs for a scoped user |
| `auth.py` `/register` | rate-limited, `_REGISTERABLE_ROLES` excludes admin, site validated against admin-created sites | Verified at the API layer, not just the UI |
| `auth.py` 2FA enroll/verify/disable, `/phone/*` | `get_current_user`, every query filtered by `username == user["username"]` | Self-scoped; no ID accepted from the client |
| `webhook.py` `/whatsapp/webhook` | Unauthenticated **by design** — Meta verify-token (GET) + `X-Hub-Signature-256` HMAC (POST) + `PenaltyBox` | Identity resolved from the sender's phone number against `users`; `_cmd_stock` re-derives scope from that row (`webhook.py:124-138`) rather than trusting the message |
| `weekly_report.py:154` `/reports/weekly-exec/{token}` | Unauthenticated **by design** — sha256 token, 72-h expiry, per-scope render | Capability URL; the token is the authorization. Scope is baked in at render time (`weekly_report.py:101`) |

### Routes accepting an ID used only alongside the caller's identity (not findings, per the brief)

- `auth.py` `/2fa/*`, `/phone/*` — every statement is `WHERE username = user["username"]`; no client-supplied identifier reaches a WHERE clause.
- `notifications.py` `POST /{notif_id}/read` — `notif_id` is combined with the `_visible(username, role, site, warehouse)` predicate in a single UPDATE; a non-visible ID yields `rowcount == 0` → 404.
- `console.py` `GET /feedback/mine` — filtered by `username`.
- `ai/router.py` `GET /jobs/{job_id}` — `job_id` combined with an `actor == username` check.
- `entry_docs.py` `DELETE /entry/attachments/{aid}` — `aid` combined with an `uploaded_by == username` check.
- `requests.py` `POST /{request_id}/cancel` — `cancel_smr` verifies `header[0] != supervisor → error`, i.e. the row's owner must be the caller. (Site is not checked, but ownership is stricter, so this one is sound.)

### Background daemons — no HTTP surface

`main.py:95-127` starts exactly three `asyncio` tasks inside the lifespan — `scheduler_loop()`, `digest_loop()`, `weekly_report_loop()` — plus a one-shot `fail_orphans()` sweep. None of them bind a socket, register a route, or mount a sub-application; all are cancelled on shutdown. They are reachable only through their admin-gated manual triggers (`POST /admin/digests/run`, `POST /admin/reports/weekly-exec/run`, both under `require_level(4)`). **No finding.**

## Files Reviewed
- `backend/api/auth.py` (helpers audited end-to-end, per priority 1)
- `backend/api/main.py` (router registration, ENTITIES registry, meta routes, lifespan daemons)
- `backend/api/crud.py` (generic read/write router factory)
- `backend/api/requests.py`
- `backend/api/warehouse.py`
- `backend/api/receiving.py`
- `backend/api/hod.py`
- `backend/api/entry.py`
- `backend/api/entry_docs.py`
- `backend/api/dashboard.py`
- `backend/api/stock.py`
- `backend/api/documents.py`
- `backend/api/reports.py`
- `backend/api/report_center.py`
- `backend/api/exec_summary.py`
- `backend/api/lining_analytics.py`
- `backend/api/weekly_report.py`
- `backend/api/manhours.py`
- `backend/api/sme.py`
- `backend/api/sme_master.py`
- `backend/api/bulk_import.py`
- `backend/api/logistics.py`
- `backend/api/admin.py`
- `backend/api/console.py`
- `backend/api/sla.py`
- `backend/api/notifications.py`
- `backend/api/webhook.py`
- `backend/api/ai/router.py`
- `backend/api/ai/submission_stats.py`
- `backend/api/services/supervisor.py`
- `backend/api/services/warehouse.py`
- `backend/api/services/procurement.py`
- `backend/api/services/notifications.py`

## Files Skipped and Why
- `backend/api/ai/{analytics,safety,query_router,ocr,handwritten,fuzzy,jobs,manual_qa,pdf_extract,client}.py` — no route definitions; reached only through `ai/router.py`, whose gates are audited above. `analytics.py`/`safety.py` were covered in Audit 01.
- `backend/api/{sme_engine,sme_export_layouts,exec_pdf,ratelimit,config,db,models}.py` — computation, rendering, configuration and schema modules with no authorization surface. `ratelimit.py` belongs to Audit 03.
- `backend/api/services/{ledger,emailer,whatsapp,procurement}.py` — inspected only where a route delegates a row lookup to them (covered above); their remaining functions are called with server-derived arguments, not client IDs.
- `backend/api/service_tests.py` — test harness.
- `legacy/**`, `frontend/`, `deploy/`, `tools/`, `.github/` — out of Phase 1 scope. Note that frontend route guards (`frontend/src/config/nav.tsx`) are **not** a security control; every finding above is server-side and unaffected by them.

---

## Tooling Recommendation

Not installed, not run. For this audit area the highest-value automated check is a **custom `semgrep` rule** rather than an off-the-shelf ruleset — the generic packs do not understand this codebase's `require_*` / `site_scope` conventions. Rule sketch (for the operator to install and refine later):

```yaml
rules:
  # 1. Any route handler with no auth dependency anywhere in its signature.
  - id: fastapi-route-without-auth-dependency
    languages: [python]
    severity: ERROR
    message: >-
      Route handler declares no Depends(require_level/require_roles/get_current_user).
      Confirm the router-level `dependencies=[...]` covers it, or add a gate.
    patterns:
      - pattern-either:
          - pattern: |
              @$ROUTER.get(...)
              async def $F(...): ...
          - pattern: |
              @$ROUTER.post(...)
              async def $F(...): ...
          - pattern: |
              @$ROUTER.patch(...)
              async def $F(...): ...
          - pattern: |
              @$ROUTER.delete(...)
              async def $F(...): ...
      - pattern-not: |
          @$ROUTER.$M(...)
          async def $F(..., $U = Depends(require_level(...)), ...): ...
      - pattern-not: |
          @$ROUTER.$M(...)
          async def $F(..., $U = Depends(require_roles(...)), ...): ...
      - pattern-not: |
          @$ROUTER.$M(...)
          async def $F(..., $U = Depends(get_current_user), ...): ...

  # 2. The Finding #2 bug class: a scope value tested for truthiness, not None-ness.
  - id: site-scope-falsy-test
    languages: [python]
    severity: WARNING
    message: >-
      site_scope()/resolve_site_param() may return '' (site-less scoped user).
      Testing it for truthiness DROPS the site filter. Use `is not None`, or
      early-return on `== ""`.
    patterns:
      - pattern-either:
          - pattern: $S = resolve_site_param(...) or ...
          - pattern: |
              $S = resolve_site_param(...)
              ...
              if $S:
                  ...
```

Rule 1 will produce false positives wherever a router declares `dependencies=[...]` (semgrep cannot see that from the handler), so it is best run as a review checklist rather than a blocking gate — roughly 30 handlers in this repo are covered that way. Rule 2 is the one worth enforcing in CI: it is precise, and it maps directly onto the 12 sites listed in Finding #2. `bandit` and the off-the-shelf `p/python` pack contain no equivalent authorization checks.

---

*Audit 02 complete. Nothing outside `docs/security/reports/` was created or modified; no code, git, database, service, or package operations were performed. Findings #1 (High #1 in Audit 01) and #2 from the SQL audit are not re-litigated here and remain queued for Phase 2 fix prompts as instructed.*
