# PROPOSED PHASE 6 PLAN — QC, PPE, Employee Lifecycle, Procurement Automation

> **Status: PROPOSAL. No application code and no migration has been written.**
> Written 2026-08-09 against `main` @ `d1ba8c2`, after a read-only analysis pass
> over `backend/models.py`, `backend/api/**`, `frontend/src/config/nav.tsx` and
> the live `:5433/gihub` mirror.
>
> Read [`SESSION_HANDOVER.md`](SESSION_HANDOVER.md) and
> [`PROJECT_HANDOVER.md`](PROJECT_HANDOVER.md) first — every locked rule cited
> below by number lives there.

---

## 0. Naming collision, stated up front

**"Phase 6" is already taken in this codebase.** `entry.py:80` calls the MTC/UoM
receipt guards "Phase 6", `notifications.py:39` calls the delivery-preference
contextvar "Phase 6", and `warehouse.py:239` calls the DN multi-stage approval
"Phase 6". Those are the 2026-07-10 UAT programme.

This document keeps the filename you asked for, but the programme inside it is
referred to as **QSEP** (Quality · Safety · Employees · Procurement) in every
code comment, migration docstring and test-suite name it proposes, so nobody
reading `entry.py` in a year has to guess which Phase 6 a comment means.

---

## 1. What already exists (the analysis pass)

Findings that change the design. Each was verified by reading the code or
querying the live mirror, not inferred.

### 1.1 Roles and RBAC

| Fact | Where | Consequence for this plan |
|---|---|---|
| Seven roles, level ladder SK 0 · warehouse/supervisor 1 · hod 2 · logistics/auditor 3 · admin 4 | `auth.py:94` `ROLE_META` | `qc` slots in at **level 1**, the parallel ladder — see §2.1 |
| `SITE_SCOPE_MIN_LEVEL = 3` — below it, reads are pinned to `Site_ID` | `auth.py:287` | A level-1 QC is automatically site-scoped. A **warehouse** QC has no site, so `site_scope()` returns `''`, which by design **matches nothing** |
| `warehouse_scope()` pins only `warehouse_user`; every other *known* role gets `None` = unrestricted | `auth.py:349` | ⚠️ **Trap.** Adding `qc` to `ROLE_META` silently grants it *global* warehouse visibility on day one. Must be handled in the same commit |
| Registration validates `_SCOPED_REG_ROLES` / `_UNSCOPED_REG_ROLES`; a role in **neither** set falls through with **no validation at all** | `auth.py:112,663` | `qc` is the first dual-scope role (site **or** warehouse). Needs a third, explicit branch — not silence |
| View-only is enforced once by method-keyed ASGI middleware | `readonly.py` (rule 7) | Every new `@router.post` in this programme is **closed to `auditor` by default**. Nothing goes on the allowlist |
| Suite BD enumerates every mutating route and asserts each of the six named roles has **0 of 143** blocked | `service_tests.py:9665` | The role loop is a hardcoded list — `qc` must be **added to it**, or the new role's write reach is unpinned |
| `admin.update_user` revokes all sessions when role/site/warehouse changes | `admin.py:210` | Any QC or employee transfer that moves a `users` row **must** call `revoke_all_sessions` — authz rides inside the 15-minute access token |

### 1.2 MTC and Surface Shields

* The MTC gate exists and is **category-exact**: `inventory."Category"` must equal
  the `app_settings.mtc_required_category` value, default `"Surface Shields"`
  (`entry.py:81`). The live mirror has **36 rows** in that category.
* The gate is wired into **exactly two call sites** — `POST /entry/receipts` and
  the receipt branch of `POST /entry/bulk` (`entry.py:223,480`).
* **Three paths bypass it completely:**
  1. `warehouse.receive()` (`services/warehouse.py:117`) — warehouse goods-in
     against a PO assignment. No MTC test.
  2. `warehouse.create_dn()` (`:178`) — a Delivery Note can be prepared and
     shipped with surface-shield lines and no certificate anywhere in the chain.
  3. `warehouse.stage_dn_receipt()` (`:379`) — inserts straight into
     `pending_receipts`, never calling `_apply_receipt_guards`.
* The requirement is *"without it, the material cannot be sent to the site"*, so
  **the hard gate belongs at DN creation/submission**, which is precisely where
  there is no check today.
* `mtc_documents` already carries `Site_ID / SAP_Code / Lot_Number / Quantity /
  mtc_number / file_blob / status / pending_receipt_id`. It has **no**
  `Warehouse_ID`, no `po_item_id`, no `DN_Number` and no QC decision fields.

### 1.3 PPE

* **There is no `PPE` category.** The live `inventory."Category"` distribution:

  | Category | Rows | | Category | Rows |
  |---|---:|---|---|---:|
  | R/L Consumables | 101 | | R/L Cons | 13 |
  | EQUIPMENTS/TOOLS | 79 | | Others | 11 |
  | BR CC PU Tools | 52 | | VEHICLES | 10 |
  | **Safety** | **50** | | CONTRACTING SERVICES | 5 |
  | Office | 47 | | Blasting | 3 |
  | **Surface Shields** | **36** | | Refractory / Vehicles | 2 each |
  | R/L Tools | 32 | | Mech / **QC** | 1 each |
  | Electrical Items | 22 | | | |

  `Safety` (50 rows) is the closest thing. **Renaming 50 rows is not implicit
  work** — `Category` is read by the MTC gate, the SME routing planner, six
  reports and the stock filters. See ruling R2 in §6.
* A PPE-shaped flow *already exists in miniature*:
  `supervisor_material_requests` carries `Old_PPE_Returned` and
  `No_Return_Reason`, and `supervisor.create_smr()` refuses a request when the
  old PPE was not returned and no reason was given (`services/supervisor.py:117`).
  That is the "mandatory reason" pattern this plan generalises — it is not a
  new idea in this codebase, and the new code should read like it.
* `entry_attachments` is the existing document store: BLOB-authoritative, 15 MB
  cap, MIME allowlist, site-scoped list/download, uploader-only delete
  (`entry_docs.py`). `_DOC_TYPES` is currently `("consumption", "receipt", "return")`.
* `returnable_items` (loans with `expected_return_time` + overdue WhatsApp) is a
  *different* concept — a borrowed tool comes back; PPE is consumed and expires.
  They must not be merged.

### 1.4 Employees — the finding that shapes requirement 3

**There are two disjoint employee registries, and nothing joins them.**

| | `employees` | `mh_employees` |
|---|---|---|
| Key | `ID_Number` **UNIQUE (global)** | `UNIQUE (Site_ID, Employee_Code)` |
| Live rows | **2** | **22** (all `CNCEC`) |
| Written by | `POST /employees` generic CRUD (`main.py:94`) | `POST /mh/import` attendance workbook, HOD-only (`manhours.py:1097`) |
| Read by | SMR worker validation (`supervisor.py:122`), QR badges (`documents.py:310`), master export | timesheets, MH variance, employee timeline |
| Site | nullable — **one of the two live rows has `''`** | `NOT NULL`, part of the key |

* `mh_employees.linked_id_number` **exists in the schema and is never written or
  read anywhere in the repository** — declared in the baseline migration
  (`ad1a8cc8e964:277`), referenced only by `models.py:953`. It is the intended
  join key and it is dead.
* The Excel upload the requirement calls "the Employee Excel upload in the HOD
  portal" is `POST /mh/import`. It parses the `ADD EMPLOYEE` and `SAR` sheets
  of the `to-john_Attendance` workbook and upserts **`mh_employees` only**. It
  never touches `employees`, so a worker imported from the roster **cannot be
  named on a supervisor material request** — `create_smr` looks them up in
  `employees` and returns *"worker not in employee master"*.
* Because `mh_employees` is keyed on `(Site_ID, Employee_Code)`, **a site
  transfer necessarily creates a second row**. Any PPE history hung off that
  table forks on transfer — which is exactly what requirement 3 forbids.

### 1.5 Procurement chain, OCR and notifications

The chain is complete and each hop is audited:

```
SK issue  → pending_issues
HOD       → /hod/prs (create draft) → /hod/prs/{n}/submit
Logistics → procurement.create_po_from_pr → assign_po (po_assignments)
Warehouse → wh.receive (PO lines: Delivered_Qty, line_status)
Warehouse → wh.create_dn (MANUAL line pick) → submit_dn
Logistics → decide_dn_logistics (date/logistics stage)
HOD       → decide_dn_hod (content stage) → wh.ship_dn (in_transit)
Site SK   → stage_dn_receipt → pending_receipts (pending_hod)
HOD       → commit_receipt → ledger
```

* **DN creation is manual.** `POST /warehouse/dns` takes an explicit
  `line_items` array. There is no auto-draft.
* **`create_dn` already enforces RL/BL strict separation** — one DN per
  `rl_bl_family` (`services/warehouse.py:195`). Any auto-generator must respect it.
* **Urgent delivery already has 90 % of its machinery**: `po_reschedule_requests`
  (HOD/WH → Logistics, `reason` mandatory, approve pushes the new date onto the
  PO — `procurement.py:476,518`). "Urgent" is a reschedule to an *earlier* date.
* **OCR for PR/PO exists and is preview-only.** `POST /ai/extract/pr` (level ≥2)
  and `POST /ai/extract/po` (level ≥3) run the two ported pdfplumber parsers in
  a worker thread and return a preview; the confirm step goes through the
  audited services. This was a deliberate fix to a legacy flaw (legacy inserted
  silently, with no audit row — `ai/router.py:128`).
  ⚠️ **They read `await file.read()` and discard the bytes.** Nothing is stored.
  The requirement *"all documents must be securely stored"* is **unmet today**.
  ⚠️ They are **pdfplumber-only** — a photographed/scanned PR cannot be
  processed at all. The vision lane (`ai/jobs.py`, kinds `ocr_consumption`,
  `ocr_delivery_note`, `tool_identify`) is where image scans belong.
* **Notification audit of the chain** (`dispatch()` = bell + WhatsApp):

  | Step | Event | Status |
  |---|---|---|
  | SK stages an entry | `entry_staged` → hod | ✅ |
  | HOD approve / reject | `entry_approved` / `entry_rejected` | ✅ |
  | HOD submits PR | `pr_submitted_to_logistics` → logistics | ✅ |
  | **Logistics raises a PO** | — | ❌ **GAP** — the site never learns its PR became a PO |
  | Logistics assigns to warehouse | `po_assigned_to_warehouse` | ✅ |
  | **Warehouse records goods received** | — | ❌ **GAP** |
  | WH submits DN | `dn_pending_logistics` | ✅ |
  | Logistics decides DN | `dn_pending_hod` / `dn_rejected` | ✅ |
  | HOD decides DN | `dn_hod_approved` / `dn_rejected` | ✅ |
  | WH ships | `dn_shipped` → store_keeper | ✅ |
  | **Site receives the DN** | — | ❌ **GAP** — `stage_dn_receipt` writes an audit row and nothing else; the HOD is never told N receipts just landed in their queue |
  | **Vendor return closed / force-close undone** | — | ❌ **GAP** |

  Four real holes, all on the same shape: a *state change that creates work for
  someone else* with no bell.

---

## 2. Design

### 2.1 The `qc` role

**Placement.** `ROLE_META["qc"] = {"label": "Quality Control", "level": 1}` —
the parallel ladder beside `warehouse_user` and `supervisor`. Level 1 means
`site_scope()` pins a site QC to their own site automatically, and `require_level`
can never be used to isolate it (same as the other two), so **every QC endpoint
uses `require_roles("qc", ...)`**, never a level check.

**Dual scoping — the hard part.** A QC belongs to *either* a site *or* a
warehouse/logistics department. Three code changes, all in one commit:

1. `warehouse_scope()` gains a `qc` branch: pinned to `Warehouse_ID` when one is
   set, `None` when the account is site-bound. Without this the new role gets
   global warehouse visibility the moment it appears in `ROLE_META` (§1.1).
2. A new `qc_scope(user) -> {"site": str|None, "warehouse": str|None}` helper
   in `auth.py`, consumed by every `/qc/*` read. It **fails closed**: an
   account with neither binding resolves to `{"site": "", "warehouse": ""}`,
   which matches nothing. This follows the existing
   `site_filter_applies` / `site_row_visible` discipline (audit Theme A) — the
   `''` case is never treated as a wildcard.
3. Registration: a new `_DUAL_SCOPE_REG_ROLES = {"qc"}` branch in
   `/auth/register` requiring **exactly one** of site / warehouse, with the site
   validated against `_admin_site_names()` exactly as scoped roles are.

**Creation.** `POST /admin/users` stays at `require_level(4)` — it is untouched.
A dedicated route is added:

```
POST /qc/accounts        require_roles("hod", "warehouse_user", "logistics")
```

It can create **only** `role="qc"`, and only inside the creator's own scope: an
HOD may bind it to their own `Site_ID`, a warehouse user to their own
`Warehouse_ID`, logistics to any warehouse. Audited as `CREATE_QC_ACCOUNT`.
Password policy reuses `admin.MIN_PW` (one policy for every credential-setting
path — audit A03-F11).

The self-service path is the existing `/auth/register` → `pending_users` →
admin approval, with the dual-scope branch above.

**Transfer between sites.** Requirement: *HODs transfer, Admin approves.*

```
POST  /qc/accounts/{username}/transfer     require_roles("hod")   → pending_admin
POST  /qc/transfers/{id}/decide            require_level(4)       → approve | reject
```

Backed by a new `qc_transfer_requests` table (§3). On approve: update
`users.Site_ID`, **call `revoke_all_sessions(reason="qc-transfer")`**, audit,
and `dispatch()` to both HODs and the QC themselves.

### 2.2 Surface-Shield MTC guard

Extract the category test out of `entry.py` into a new
**`backend/api/services/quality.py`**, so there is one definition and four call
sites instead of two inlined copies:

```python
async def mtc_required(session, *, sap_code=None, material_code=None) -> bool
async def assert_mtc_present(session, *, sap_code, lot, site_or_wh, mtc_id)
async def assert_qc_cleared(session, *, sap_code, site_id, lot, qty)
```

`material_code` matters: `dn_items` carries `Material_Code`, not `SAP_Code`.

Enforcement points (all new except the first two, which move):

| # | Where | Rule |
|---|---|---|
| 1 | `POST /entry/receipts`, `/entry/bulk` | unchanged behaviour, now via `quality.` |
| 2 | `warehouse.receive()` | a surface-shield PO line requires an `mtc_document_id` |
| 3 | **`warehouse.create_dn()`** | **hard block** — a DN line whose material is surface-shield must reference an MTC that is QC-**approved** |
| 4 | `warehouse.stage_dn_receipt()` | defence in depth; by then #3 has already held |

`mtc_documents` gains `Warehouse_ID`, `po_item_id`, `DN_Number`,
`qc_inspection_id` (§3).

### 2.3 QC approval ledger

One table, `qc_inspections`, keyed to **the physical lot at a place**, because
that is the granularity `lots` already uses — `UNIQUE(Lot_Number, SAP_Code,
Site_ID)` (`models.py:1500`). Partial approval is `approved_qty <
submitted_qty`; the remainder is `rejected_qty` and a `decision_reason` is
mandatory whenever `rejected_qty > 0`.

Triggers (each `dispatch()`es to `recipient_role="qc"` narrowed by site or
warehouse, `wa_template="action_required"`):

* warehouse goods-in of a surface-shield line → inspection at the warehouse,
* site DN receipt of a surface-shield line → inspection at the site.

### 2.4 Issuance block

`quality.assert_qc_cleared()` is called from **two** places, because the SK
issue path has two mouths:

1. `ledger.stage_consumption()` — `/entry/consumption` and the consumption
   branch of `/entry/bulk`.
2. `supervisor.approve_smr()` — which inserts **directly into `pending_issues`**
   (`services/supervisor.py:207`) and would otherwise walk straight past a
   guard placed only in `stage_consumption`.

The check: for a surface-shield SAP at a site, the requested quantity must be
covered by `Σ approved_qty − Σ already issued` for that `(Site_ID, SAP_Code,
Lot_Number)`. No inspection row, or an inspection in `pending` / `rejected`
→ 422 naming the lot.

> ⚠️ **This is the first hard block on the issue path.** The standing rule is
> *"FEFO + over-issue stay allow-and-log — never add a hard block"*
> (`PROJECT_HANDOVER.md` §6, and the FEFO ruling of 2026-06-30). This gate does
> **not** overturn it: it is a new, separately-authorised block on *quality
> status*, and it is scoped to the 36 surface-shield SAPs. FEFO and over-issue
> behaviour on every other material is untouched, and the QC block must be
> implemented as its own predicate — never by turning the FEFO warning into an
> error.

### 2.5 PPE

**What counts as PPE.** Membership is defined by the existence of a
`ppe_rules` row for that `SAP_Code`, **not** by an `inventory."Category"`
value. Reasons: there is no PPE category today; `Safety` (50 rows) is a
superset that includes things nobody wears out on a schedule; and `Category`
is load-bearing for the MTC gate and the SME routing planner. A new
`app_settings` key `ppe_default_category` (default `Safety`) only pre-filters
the *picker* on the configuration page.

**Configuration** (`/sk/ppe/rules`, `require_roles("store_keeper")` + HOD read):
`usable_days` per SAP, optionally per site. Site row wins over the global row.

**Distribution** (`POST /sk/ppe/issues`):

* `safety_doc_id` → an `entry_attachments` row with the new
  `doc_type="safety_approval"`. Mandatory, 422 without it. This reuses the
  existing upload endpoint, MIME allowlist, size cap, scoping and audit rather
  than inventing a second document store.
* `expires_on = issued_on + usable_days` is **computed and stored** at write
  time, not derived on read — the rule may change later and history must not
  move retroactively. `usable_days_applied` is stored beside it for the same
  reason.
* Early replacement: if the same `(employee_id_number, SAP_Code)` already has an
  `active` row with `expires_on > today`, then `early_reason` is mandatory —
  the same shape as `create_smr`'s existing "give a reason, old PPE not
  returned" guard. The superseded row flips to `replaced`.
* Distribution optionally links to the `pending_issues` row it created, so the
  physical stock movement stays in the one ledger. **PPE does not get a parallel
  stock ledger.**

**Reporting.** `GET /ppe/employees/{id_number}/history` — every distribution
ever, across sites, ordered by date, with the site each was issued at.

**Forecast.** `GET /ppe/forecast?days=10&site_id=`:

```
expiring_qty  = Σ active distributions with expires_on ∈ [today, today+days]
on_hand       = current site stock for that SAP (existing stock helper)
already_on_order = Σ open PO line qty for that material
suggested_qty = max(expiring_qty − on_hand − already_on_order, 0)
```

Deliberately **deterministic, not statistical**. There is no history to fit a
model to (22 roster rows, zero PPE distributions), and a confidently-wrong
forecast is worse than an arithmetic one. A 90-day rolling issue rate is shown
**beside** the number as a sanity column, never folded into it. The result
feeds the existing `POST /hod/prs/auto-draft` so a forecast becomes a draft PR
in one click.

> This netting is the same shape as SME rule 1c (*don't order what you already
> have or have already ordered*), and it should be written to read that way —
> but it is **ERP data, not SME data**, and it must never read
> `sme_inventory_seed`. See §5.

### 2.6 Employee identity, transfers and PPE carry-over

**Ruling to adopt (R1, §6): `employees.ID_Number` is the PERSON.
`mh_employees` is a per-site EMPLOYMENT RECORD.** Every PPE row, every
movement row and every badge keys on `ID_Number`. Nothing hangs off
`mh_employees.id`, because that row forks on transfer.

Work:

1. **Revive `linked_id_number`.** `POST /mh/import` also upserts `employees`
   (`ID_Number = Employee_Code`) and writes `mh_employees.linked_id_number`.
   ⚠️ `employees.ID_Number` is **globally unique**, so the same person at two
   sites is ONE row — the import must `ON CONFLICT (ID_Number) DO UPDATE`
   (name, phone, department, site), never insert a second row. A naive insert
   409s on the second site.
2. **Backfill.** One of the two live `employees` rows has `Site_ID = ''`, which
   the SMR worker check (`(w[2] or "") != site_id`) rejects for every site — a
   silent, invisible failure. The migration reports it; it is **not** guessed.
3. **`employee_movements`** records every site change with `from_site`,
   `to_site`, `effective_date`, `reason`, `moved_by`.
4. `POST /hod/employees/{id}/transfer` — `require_roles("hod")`, source-site
   scoped. Applies immediately (requirement 3 asks only for HOD capability;
   admin approval is required for the **QC user** transfer, §2.1, which is a
   different thing). It updates `employees.Site_ID`, inserts the movement,
   upserts an `mh_employees` row at the target site, and **leaves the old
   `mh_employees` row in place** — timesheets are keyed on
   `(Site_ID, Employee_Code, Work_Date)` and history must not move.
5. **PPE carries over for free**, because `ppe_distributions.employee_id_number`
   is site-independent. The target SK's history view queries by ID_Number; each
   row keeps its own issuing `Site_ID` for site reporting. `dispatch()` on
   transfer sends the target site's SK a summary of what the worker already
   holds and when it expires — that notification *is* the duplicate-issue
   prevention the requirement asks for.
6. **Admin tracking**: `GET /admin/employees/{id_number}/timeline` — current
   site, the full `employee_movements` series, and PPE/man-hour presence per
   site. Rendered as a timeline chart on a new Admin page.

### 2.7 Procurement automation, OCR and notifications

**Auto-DN.** A new `services/warehouse.auto_draft_dns(session, *, po_number,
assignment_id, username)` runs at the end of `wh.receive()`. It groups the
newly-received quantities by `(Site_ID, rl_bl_family)` and calls the **existing**
`create_dn()` once per group — so RL/BL strict separation, the
over-shipment guard and the DN numbering are all inherited rather than
reimplemented. Output is `status='draft'`; a human still submits it. Controlled
by an `app_settings` flag `auto_draft_dn` (default `1`), because a warehouse
that batches shipments will want it off.

**Urgent delivery.** Extend `po_reschedule_requests` with `urgency` (`normal` |
`urgent`) rather than adding a table. `POST /hod/reschedule` already exists;
an urgent request sets `severity="critical"` on the dispatch, which — by the
existing rule in `notifications.dispatch()` — **bypasses the evening digest and
sends immediately**. Validation: `requested_date < current_date` and a reason
of at least N characters.

**OCR — store the document.** Both `/ai/extract/{pr,po}` change shape:

1. Persist the upload to `entry_attachments` **before** parsing, with
   `doc_type` `pr_scan` / `po_scan`, and return `attachment_id` in the preview.
2. The confirm step (`POST /hod/prs`, `POST /logistics/pos`) accepts that id
   and links it, exactly as `_link_mtc` links an MTC to a staged receipt.
3. Accept images as well as PDFs: an image upload is queued through the
   existing `ai_jobs` worker with a new kind `ocr_purchase_doc` and a new
   prompt in `ocr.py` — same async job / polling contract the OCR Import page
   already uses.
4. `purchase_orders.attachment_blob/name/mime` already exists and is now a
   **second** store for the same thing. Do not write to both: keep
   `entry_attachments` as the one path and leave the PO columns for the legacy
   rows they already hold. Note it in `docs/ARCHITECTURE.md`.

**The four notification gaps** (§1.5) are closed with `dispatch()` calls in the
services that own the transition — never in the routers, so the bulk and API
paths both fire:

| Gap | New event | Recipient |
|---|---|---|
| PO raised from a PR | `po_created_for_pr` | `hod` @ the PR's site |
| Warehouse recorded goods in | `po_goods_received` | `logistics` + `hod` @ site |
| Site received a DN | `dn_receipt_staged` | `hod` @ site (+ `warehouse_user`) |
| Vendor return closed | `vendor_return_closed` | raiser + `logistics` |

Plus the QSEP events: `qc_inspection_required`, `qc_decision`,
`mtc_blocked_dn`, `ppe_expiring_soon`, `ppe_issued`, `employee_transferred`,
`qc_transfer_pending_admin`.

**No new WhatsApp templates.** All of these fit the four approved reusable
templates (`gi_action_required`, `gi_status_update`, `gi_critical_alert`,
`gi_evening_summary`) whose bodies are `{{1}}=title` / `{{2}}=body`. Getting a
new template approved by Meta is a multi-day operator task and this programme
must not depend on one.

---

## 3. Database migrations

Six migrations, chained off the current head **`a71e93b4c2f8`**. Revision ids
below are proposals; regenerate if they collide.

Every one of them must also be mirrored in `backend/models.py` — the models
file is reflected at import time by `services/ledger._MD` and every service
reads its tables from there.

### M1 — `b4d17c8e93a2` · QC role support and the inspection ledger
*(down-revision `a71e93b4c2f8`)*

```
CREATE TABLE qc_inspections (
  id                 serial PRIMARY KEY,
  Site_ID            text,                 -- exactly one of Site_ID / Warehouse_ID
  Warehouse_ID       text,
  SAP_Code           text NOT NULL,
  Material_Code      text,
  Lot_Number         text,
  source_type        text NOT NULL,        -- warehouse_receipt | dn_receipt | site_receipt
  source_ref         text NOT NULL,
  mtc_document_id    integer,
  submitted_qty      double precision NOT NULL,
  approved_qty       double precision NOT NULL DEFAULT 0,
  rejected_qty       double precision NOT NULL DEFAULT 0,
  status             text NOT NULL DEFAULT 'pending',
                     -- pending | approved | partially_approved | rejected
  decision_reason    text,
  inspected_by       text,
  inspected_at       timestamp,
  created_by         text NOT NULL,
  created_at         timestamp DEFAULT CURRENT_TIMESTAMP,
  UNIQUE (source_type, source_ref, SAP_Code, Lot_Number)
);

CREATE TABLE qc_transfer_requests (
  id             serial PRIMARY KEY,
  username       text NOT NULL,
  from_site      text,
  to_site        text NOT NULL,
  reason         text NOT NULL,
  requested_by   text NOT NULL,
  requested_at   timestamp DEFAULT CURRENT_TIMESTAMP,
  status         text NOT NULL DEFAULT 'pending_admin',
  decided_by     text,
  decided_at     timestamp,
  decision_notes text
);

ALTER TABLE mtc_documents
  ADD COLUMN "Warehouse_ID"   text,
  ADD COLUMN po_item_id       integer,
  ADD COLUMN "DN_Number"      text,
  ADD COLUMN qc_inspection_id integer;
```

The `UNIQUE` on `qc_inspections` is what makes the trigger idempotent — a
re-run of a warehouse receipt must not open a second inspection.
**No foreign keys**, matching the rest of this schema: the baseline migration
declares **zero** FK constraints across 74 tables and uses bare integer
references throughout. The single exception in the whole codebase is
`refresh_sessions.user_id` (`models.py:629`), added with `ON DELETE CASCADE`
because a deleted user's sessions must die with them — which is a genuinely
different requirement from anything here.

### M2 — `c9e35a71d4b6` · PPE rules and distributions
*(down-revision `b4d17c8e93a2`)*

```
CREATE TABLE ppe_rules (
  id                  serial PRIMARY KEY,
  SAP_Code            text NOT NULL,
  Site_ID             text,               -- NULL = global default
  usable_days         integer NOT NULL,
  requires_safety_doc integer NOT NULL DEFAULT 1,
  notes               text,
  created_by          text,
  created_at          timestamp DEFAULT CURRENT_TIMESTAMP,
  updated_at          timestamp DEFAULT CURRENT_TIMESTAMP
);
CREATE UNIQUE INDEX ux_ppe_rules_sap_site
  ON ppe_rules ("SAP_Code", COALESCE("Site_ID", ''));

CREATE TABLE ppe_distributions (
  id                       serial PRIMARY KEY,
  Site_ID                  text NOT NULL,
  employee_id_number       text NOT NULL,
  SAP_Code                 text NOT NULL,
  Material_Code            text,
  Lot_Number               text,
  Qty                      double precision NOT NULL,
  issued_on                text NOT NULL,      -- ISO date, matching the ledger's convention
  usable_days_applied      integer NOT NULL,
  expires_on               text NOT NULL,
  safety_doc_id            integer NOT NULL,   -- entry_attachments.id
  replaces_distribution_id integer,
  early_replacement        integer NOT NULL DEFAULT 0,
  early_reason             text,
  pending_issue_id         integer,
  consumption_id           integer,
  status                   text NOT NULL DEFAULT 'active',
                           -- active | replaced | expired | returned
  issued_by                text NOT NULL,
  created_at               timestamp DEFAULT CURRENT_TIMESTAMP
);
```

`COALESCE(Site_ID,'')` in the unique index is required: Postgres treats NULLs
as distinct, so a plain `UNIQUE (SAP_Code, Site_ID)` would allow unlimited
duplicate *global* rules — the exact class of bug the `''` scoping rules in
this codebase exist to prevent.

`issued_on` / `expires_on` are `text` ISO dates because every other date in
this schema is (`receipts."Date"`, `lots."Received_Date"`, `pr_master.Delivery_Date`).
Consistency beats correctness-in-isolation here; a mixed convention is how the
returnables timezone bug happened.

### M3 — `d2f84b19e57c` · Employee identity and movements
*(down-revision `c9e35a71d4b6`)*

```
CREATE TABLE employee_movements (
  id                 serial PRIMARY KEY,
  employee_id_number text NOT NULL,
  from_site          text,
  to_site            text NOT NULL,
  effective_date     text NOT NULL,
  reason             text,
  moved_by           text NOT NULL,
  status             text NOT NULL DEFAULT 'applied',
  created_at         timestamp DEFAULT CURRENT_TIMESTAMP
);

ALTER TABLE employees
  ADD COLUMN "Designation" text,
  ADD COLUMN "Worker_Type" text,
  ADD COLUMN "Company"     text;
```

The three added columns let `employees` carry what the attendance workbook
already supplies, so the two registries stop disagreeing about the same person.
`linked_id_number` needs **no migration** — it already exists; this programme
just starts writing it.

**Data step, run as a separate reported operation, never inside the migration:**
backfill `mh_employees.linked_id_number` and create the missing `employees`
rows for the 22 roster workers. The one `employees` row with `Site_ID=''` is
**reported, not guessed** — same discipline as the Consumption-Log row with a
Location and no serial (`EXCEL_LOCATION_SYNC_RUNLOG.md`).

### M4 — `e6a91c37b208` · Procurement automation
*(down-revision `d2f84b19e57c`)*

```
ALTER TABLE po_reschedule_requests
  ADD COLUMN urgency text NOT NULL DEFAULT 'normal';   -- normal | urgent

ALTER TABLE delivery_notes
  ADD COLUMN auto_generated integer NOT NULL DEFAULT 0,
  ADD COLUMN source_assignment_id integer;

ALTER TABLE pr_master     ADD COLUMN source_attachment_id integer;
ALTER TABLE purchase_orders ADD COLUMN source_attachment_id integer;
```

### M5 — `f8b2d64a19e3` · seed rows
*(down-revision `e6a91c37b208`)*

`app_settings` inserts, all `ON CONFLICT DO NOTHING` so a re-run is a no-op:
`ppe_default_category='Safety'`, `ppe_forecast_days='10'`, `auto_draft_dn='1'`,
`qc_required_category` (aliasing the existing `mtc_required_category` value so
the two can diverge later without a schema change).

### M6 — indexes, **deferred and conditional**

Rule 11: *indexes are benchmarked before they are added.* No index beyond the
unique constraints above ships in this programme until it has been measured on
an inflated clone, exactly as `e7c3b95a41d2` was — where four of eleven
candidates were **rejected on evidence**. Likely candidates to *measure*:
`ppe_distributions (employee_id_number, status)`,
`ppe_distributions (Site_ID, expires_on)`,
`qc_inspections (Site_ID, SAP_Code, status)`. On today's row counts (22
employees, 0 distributions) none of them will earn their keep; this is a
post-adoption task.

---

## 4. Frontend

New pages, each registered in `frontend/src/config/nav.tsx` — the one source of
truth for navigation access (rule: the API enforces, the manifest makes the UI
agree).

| Route | Label | Access rule | `writes` |
|---|---|---|---|
| `/qc/inspections` | QC Inspections | `{ anyRole: ['qc','hod','logistics','warehouse_user'] }` | ✅ |
| `/qc/accounts` | QC Accounts | `{ anyRole: ['hod','logistics','warehouse_user'] }` | ✅ |
| `/sk/ppe/rules` | PPE Rules | `{ anyRole: ['store_keeper'] }` | ✅ |
| `/sk/ppe/issue` | PPE Distribution | `{ anyRole: ['store_keeper'] }` | ✅ |
| `/ppe/forecast` | PPE Order Forecast | `{ minLevel: 1 }` | — (read) |
| `/hod/employees` | Employees & Transfers | `{ anyRole: ['hod'] }` | ✅ |
| `/admin/employees` | Employee Tracking | `{ minLevel: 4 }` | — (read) |

Notes:

* Every table goes through `frontend/src/lib/smartTable.tsx`, not antd's
  `Table` (rule 5) — 99 instances, 45 files, no exceptions.
* `writes: true` is what makes a page unreachable for the Auditor. The two
  **read** surfaces (`/ppe/forecast`, `/admin/employees`) are deliberately not
  marked, so an auditor can see them; their action buttons are disabled by
  `useReadOnly()`.
* The Employee Tracking timeline is a chart. Per the `dataviz` skill's rules it
  reuses the existing theme tokens rather than introducing a palette.
* `/qc/inspections` shows the QC's *own* scope only — the dual-scope resolution
  happens server-side (§2.1); the page never sends a site or warehouse it chose
  for itself.

---

## 5. How this avoids breaking the locked rules

Each row is a rule from `PROJECT_HANDOVER.md` and the specific thing this
programme does to stay on the right side of it.

| Rule | Risk introduced here | Mitigation |
|---|---|---|
| **1a — SME ⇄ ERP decoupling** | The PPE forecast and the auto-DN both do "what do we have / what's on order" arithmetic, which *looks* like SME work | **No new module may name `sme_inventory_seed`, `sme_recipe`, `sme_equipment` or `sme_consumption_log`.** The forecast reads `inventory` / `receipts` / `consumption` / `po_items` only. Suite BA already greps for those table names — the new suite extends the same grep to `quality.py`, `ppe.py` and the employee module |
| **1 / 1b / 1c** — component identity, tier segregation, subset rule | None — this programme touches no SME maths | **No change to `sme_engine.py` or `engine.ts`.** Parity stays at 1,313 comparisons and `test:ui-math` at 33/0. If either moves, something has gone wrong |
| **7 — Auditor is view-only** | ~25 new mutating routes | They are blocked by default (method-keyed middleware). **Nothing is added to `readonly.py`'s allowlist.** Suite BD's `EXPECTED_ALLOWED` set is unchanged; its role loop gains `"qc"` so the new role's write reach is pinned too |
| **12 — exports are defused, numbers are not** | Three new exports (PPE history, PPE forecast, employee timeline) carry free text written by an SK (`early_reason`, `decision_reason`, movement `reason`) and read by an HOD in Excel | Route every one through `xlsx_style.xl_val` / `reports.to_csv` / `sme_export_layouts._cell`. **`usable_days`, quantities and day-counts must reach the writer as `int`/`float`, never as strings** — the trap in rule 12 |
| **FEFO / over-issue stay allow-and-log** | §2.4 adds the first hard block on the issue path | The QC block is a separate predicate on quality status, scoped to the 36 surface-shield SAPs, explicitly authorised by the requirement. FEFO and over-issue keep their warn-and-record behaviour untouched, and the block must never be implemented by promoting an existing warning to an error |
| **Never delete `system_audit_log` rows** | New audit actions | Delta-counted assertions only, as every existing suite does |
| **`gi_database.db` is untouchable** | Employee backfill touches employee data | Postgres only. `shasum -a 256 gi_database.db` verified unchanged before and after |
| **`legacy/` is frozen** | The legacy portal has its own employee and PPE-adjacent screens | **Zero changes under `legacy/`.** `legacy/bug_check.py` stays at 599/0. `legacy/config.py` stays at six roles — `qc`, like `auditor`, is new-stack only |
| **Rule 11 — benchmark indexes** | Four new tables | No index beyond the uniqueness constraints until measured (§3 M6) |
| **Notifications** | 11 new events | All four existing Meta templates; **no new template submission**, so nothing waits on Meta approval |
| **Two-stack DB** | New tables invisible to the frozen Streamlit app | Expected and correct — `tools/parity_check.py` is already retired as a gate and its failure carries no information |

---

## 6. Rulings needed before implementation

Five decisions the plan cannot make on its own. Defaults are stated; say the
word and they are adopted as-is.

**R1 — `employees.ID_Number` is the person; `mh_employees` is a per-site
employment record.** PPE, movements and badges key on `ID_Number`.
*Default: adopt.* Without it, requirement 3's "PPE history carries over" is
not implementable, because `mh_employees` is keyed on `(Site_ID, Employee_Code)`
and forks on transfer.

**R2 — PPE membership comes from `ppe_rules`, not from a `Category` rename.**
*Default: adopt.* The alternative — renaming 50 `Safety` rows to `PPE` — is a
data migration whose blast radius includes the MTC gate, the SME routing
planner and six reports. If you want the category renamed anyway, it is a
separate, separately-verified change.

**R3 — the QC hard block applies at DN creation, not only at issue.**
*Default: adopt.* The requirement says material "cannot be sent to the site"
without an MTC, and today the DN path has no check at all. Blocking only at
issue means uncertified material still physically travels.

**R4 — employee site transfer is HOD-immediate; only the QC *user* transfer
needs admin approval.** *Default: adopt* — it is what requirements 1 and 3
literally say. If employee transfers should also queue for admin, that is one
extra status on `employee_movements` and one endpoint.

**R5 — the PPE forecast is deterministic, not statistical.** *Default: adopt.*
There are zero PPE distributions and 22 roster rows; any fitted model would be
fabricating confidence. Revisit after six months of real data.

---

## 7. Build order and gates

Six slices. Each ends green on **every** gate before the next begins — that is
how this codebase has been built and it is why the baselines have never
regressed.

| # | Slice | Ships | New suite |
|---|---|---|---|
| **1** | QC role + scoping | `ROLE_META`, `warehouse_scope` fix, `qc_scope`, registration branch, `/qc/accounts`, transfer + admin approval, M1 | **BL** (~20) |
| **2** | MTC guard + inspection ledger | `services/quality.py`, four enforcement points, triggers, `/qc/inspections`, notifications | **BM** (~22) |
| **3** | Issuance block | `assert_qc_cleared` in `stage_consumption` **and** `approve_smr`, cumulative approved-qty accounting | **BN** (~12) |
| **4** | PPE | M2, rules page, distribution with mandatory safety doc + early-replacement reason, history, forecast, exports | **BO** (~25) |
| **5** | Employees | M3, `/mh/import` writes `employees` + `linked_id_number`, backfill report, transfers, carry-over notification, admin timeline | **BP** (~20) |
| **6** | Procurement + OCR + notifications | M4/M5, auto-draft DN, urgent delivery, document persistence on both extract routes, image lane, the four gap notifications | **BQ** (~22) |

### Gates — none of these may move except where stated

| Gate | Baseline now | After |
|---|---|---|
| Backend service tests | **1245 / 0** (A…BK) | **~1366 / 0** (A…BQ) — *rises* |
| Playwright E2E | 57 / 57 | 57 + ~8 new specs |
| SME UI math | 33 / 0 | **33 / 0 — unchanged** |
| SME TS↔PY parity | 1,313 comparisons | **1,313 — unchanged** |
| Legacy regression | 599 / 0 | **599 / 0 — unchanged** |
| Frontend | `tsc -b` + build + `oxlint` clean | same |
| Alembic | single head `a71e93b4c2f8` | single head `f8b2d64a19e3` |
| `gi_database.db` | sha256 `00652932…ba038` | **unchanged** |

> The three "unchanged" rows are the ones that matter most. If SME parity or
> `test:ui-math` moves during this programme, a boundary has been crossed that
> should not have been — stop and find out why before continuing.

### Test-suite shape

Following the house style, each new suite does more than spot-check:

* **BL** asserts every branch of the dual-scope resolver against `''`,
  `None` and a foreign binding, and adds `"qc"` to suite BD's zero-blocked
  role loop.
* **BM** posts a real surface-shield DN with no MTC and requires the 422, then
  a *revert* check that fails if the guard is removed from `create_dn` —
  the pattern suite AZ uses.
* **BN** drives **both** issue mouths (`/entry/consumption` and SMR approval),
  because a guard in only one of them is the exact failure this suite exists
  to catch.
* **BO** round-trips a PPE xlsx and asserts a negative/formula-shaped
  `early_reason` is defused **and** that `usable_days` still sums as a number
  (rule 12's trap).
* **BP** transfers a worker between sites and asserts the PPE history is
  visible from the destination and that the source's timesheets did not move.
* **BQ** asserts an auto-drafted DN never mixes RL and BL families, and that
  each of the four previously-missing notifications now fires exactly once.

### Effort

Roughly **10–14 working days** end to end, slice 4 (PPE) and slice 6
(procurement/OCR) being the two largest. Slices 1–3 are one coherent unit and
should not be split across sessions — the `warehouse_scope` fix in slice 1 is
what keeps slice 2's scoping honest.

---

## 8. Open questions for the operator

1. **Who inspects at the warehouse?** The live mirror has one warehouse
   (`WH-01`, Main Warehouse @ CNCEC) and one `warehouse_user`. Is the warehouse
   QC a separate person, or does the warehouse user hold the QC role too? The
   design supports both; the answer changes the default binding on
   `POST /qc/accounts`.
2. **Which SAPs are PPE, and what are their usable times?** The plan builds the
   configuration screen; it cannot invent the rules. Safety Shoes = 6 months and
   Goggles = 3 months were the two examples given — a starting list of 10–20
   SAPs with day-counts would let slice 4 ship with real seed data instead of
   an empty table.
3. **The `Site_ID = ''` employee row** (`ID_Number` 30816, "Johnson Andrew",
   Store). Which site does that person belong to? It is currently invisible to
   every supervisor material request.
4. **Rejected surface shields — where do they physically go?** The plan records
   `rejected_qty` and blocks issue. It does not currently move the stock. The
   existing vendor-return flow (`procurement.raise_vendor_return`, which
   reopens the PO line) is the natural destination — confirm and it becomes one
   more slice-2 hop.
5. **Should an expired-but-still-worn PPE item raise anything?** The forecast
   covers "expiring in the next 10 days". Nothing currently happens on the day
   it expires. A daily `ppe_expired` dispatch to the site SK is cheap and
   reuses the existing `digest_loop` scheduler — say if you want it.

---

*Nothing in this document has been implemented. Say which rulings in §6 you
accept and this becomes the build order in §7.*
