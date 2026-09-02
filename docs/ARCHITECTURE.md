# GI Hub — System Architecture (the "brain" document)

> **Purpose:** a fresh AI instance (or engineer) reading ONLY this file plus
> [`PROJECT_STATUS.md`](PROJECT_STATUS.md) must understand the exact state,
> tech stack and rules of the project with no chat history. Written 2026-07-13;
> finalized 2026-07-18 (pre-deploy batch); updated 2026-07-26 (native-app
> program); **updated 2026-07-30 (SME allocation overhaul: two-tier
> Available-vs-Ordered + reverse SQM + COMPONENT IDENTITY; global table
> tools)**; **updated 2026-08-13 (workflow polish + test isolation)** at gates
> `service_tests 1502/0 (suites A…BX, own throwaway DB) · Playwright 90/90 ·
> parity:sme 1,313 · ui-math 33/0 · bug_check 599/0 · nav 46 routes ·
> build+tsc ✅ · alembic single head c7a93e5d2b18`.
> **The Hetzner deployment is PAUSED by decision** — next phase is Feature
> Fine-Tuning and UI Polish. Locked rules + baselines in one page:
> [`PROJECT_HANDOVER.md`](../PROJECT_HANDOVER.md).

---

## 1. The two applications (segregation contract)

| | LEGACY (production, frozen) | NEW STACK (ship-ready) |
|---|---|---|
| UI | Streamlit (`main.py`, `pages_internal/*.py`) | React 19 + antd 6 + Vite (`frontend/`) |
| API | — (monolith) | FastAPI (`backend/api/`), uvicorn `:8000` |
| DB | SQLite `gi_database.db` (**system of record until cutover**) | PostgreSQL 16 — CI mirror `postgresql://postgres@127.0.0.1:5433/gihub` |
| Deploy | on-prem | Hetzner CPX42 plan + Cloudflare Tunnel (`gi.giinventory.com`), nginx, deploy/ |

Rules ([REPO_MAP.md](../REPO_MAP.md) is the contract): never edit
`legacy/database.py` / `legacy/pages_internal/` for new-stack work; new-stack
commits touch only `backend/`, `frontend/`, `deploy/`, `tests/`, `docs/`.
**Phase B executed 2026-07-13**: the legacy app lives under `legacy/`, root
data artifacts under `data-archive/`, and the bridge tools under `tools/`
(`dual_ci.py`, `migrate_sqlite_to_postgres.py`, `parity_check.py`,
`migration/cutover_migrate.py` + runbook); `gi_database.db` deliberately stays
at the repo root (bridge tools + the final production load read it there, and
it must never be staged). `tools/dual_ci.py` reloads the mirror from SQLite
and verifies 5 semantic aggregates; the production cutover script is
`tools/migration/cutover_migrate.py` (sync psycopg2 URL, `--strict --wipe`;
asyncpg URLs fail with MissingGreenlet by design). After every mirror reload,
re-run `backend/scripts/create_ai_readonly_role.sql` (grants get wiped).
**⚠️ Excel injection: PostgreSQL is permanently AHEAD of the frozen SQLite**
(2026-07-13 CNCEC workbook sync + 2026-07-18 re-sync: inventory 306→442,
full ledger backfill, stock verified **429/429** vs the workbook). A
`dual_ci`/cutover reload from `gi_database.db` WIPES that data — after ANY
reload re-run the sync (same on the production box after the final load;
the runbook says so):

**Preferred since 2026-07-27: `tools/pg_excel_sync.py`** — the single-entry,
ATOMIC replacement (one transaction across all five kinds; a failure anywhere
rolls the whole sync back). Dry-run by default:

```bash
DATABASE_URL=postgresql+psycopg2://…/gihub \
  .venv/bin/python tools/pg_excel_sync.py --site CNCEC            # dry-run
DATABASE_URL=… .venv/bin/python tools/pg_excel_sync.py --site CNCEC --commit
DATABASE_URL=… .venv/bin/python tools/pg_excel_sync.py --site CNCEC \
    --sme-reseed --commit                            # SME wholesale replace
```

It imports `bulk_import.py`'s planners and replaces ONLY the write path, so
column mapping is never duplicated; every master write is `ON CONFLICT … DO
UPDATE` with `COALESCE(excluded.col, table.col)`. **It must never import
Pandas** (absent from `backend/requirements.txt` — it arrives only
transitively via streamlit for the legacy app). It refuses non-Postgres URLs
and any URL mentioning `gi_database`. Exit code 1 after a successful commit
just means the stock-verification line found mismatches — that is a signal,
not a failure.

The older per-kind chain still works and is what the historical runbook used:

```bash
tools/excel_sync.py --site CNCEC --commit            # header-NAME-driven; all 4 workbooks at repo root
tools/excel_sync_reconcile.py --commit               # zeroes superseded rows; date-less lines
tools/excel_sync.py --site CNCEC \
    --kinds sme-equipment,sme-recipes,sme-materials \
    --sme-reseed --commit                            # SME trio: wholesale replace (see below)
```

Sync mechanics (2026-07-18 final): every sheet's columns resolve **by header
name** (reorders/additions in the workbooks are safe; unknown columns warn,
never silently drop). `--kinds` restricts a run; **`--sme-reseed`** drops
recipes (global) + equipment/progress (per-site) + seeds before the SME loads
— REQUIRED whenever the workbook renumbers `Lining_System_Code`s (an upsert
would leave stale old-code rows double-counting SQM); it aborts if any
`Done_SQM > 0` would be lost unless `--force-drop-progress`. Recipe line
identity is **(code, material, SAP_Code)** — PU systems carry Comp-A/B/C/D
lines sharing one Material_Code, distinguished only by variant SAPs
(1041/-1/-2/-3); SAP-aware files merge repeated identities as coat lines
(For_1_SQM sums), legacy no-SAP files keep first-occurrence-wins.
**STOCK identity matches it since 2026-07-30**: `sme_inventory_seed`'s PK is
`(Material_Code, SAP_Code)` (alembic `a4e9b1c73f28`), so each component drum
holds its own quantities. SAP codes are whitespace-normalized on both sides
of every join (the ERP writes `"1043 - 2"` for `"1043-2"`). ⚠️ The frozen
legacy SQLite has NO `SAP_Code` column on either SME table, so a cutover
lands 86 blank-SAP recipe rows + one blank-SAP seed row per material; the
86 are REAL data (disjoint from the workbook's coded pairs — measured), and
a blank-SAP seed row is retired only when no blank-SAP recipe line still
references it. `--sme-reseed` is the remedy for a mixed state.
`tools/parity_check.py` fails against the live mirror BY DESIGN (only
meaningful on CI or a freshly-reloaded mirror). Executed 2026-07-18:
inventory+ledger 429/429, SME reseed run by the operator (recipes 41, codes
1–10, all SAP-mapped).

## 2. Backend map (`backend/api/`)

FastAPI app in `main.py` (lifespan starts 3 daemons — report scheduler,
16:00 evening digest, **Friday 17:00 weekly exec PDF** — all disabled by
`GI_SCHEDULER=0`). `models.py` (repo `backend/models.py`) is the single schema
contract; alembic migrations in `backend/alembic/versions` (single head
**`a4e9b1c73f28`** = `sme_component_pooling`; before it `f1a7c9e83b52` =
refresh_sessions_rtr, `c7d4e8f19a25` = feedback_triage, `b3f2a9c47d18` =
SME SAP codes). Modules:

| Module | Owns |
|---|---|
| `auth.py` | bcrypt login, 15-min JWT access + **RTR refresh families in `refresh_sessions`** (signed refresh JWT; `client_type` web 7d / native 90d; replay ⇒ THAT family revoked, other devices survive — full model in §6), TOTP 2FA, role levels (SK 0 · warehouse/supervisor 1 · hod 2 · logistics 3 · admin 4; `require_roles` always admits admin; `site_scope` pins level <3), registration + admin approval, **dual-OTP phone change** (`phone_otp.stage` 'old'→'new'; commit only after the NEW number verifies) |
| `entry.py` | SK staging: receipts/consumption/returns/adjustments + `/entry/bulk`. Guards: **the MTC gate for `Category == "Surface Shields"` binds at ISSUE, not at receipt** (setting `mtc_required_category`; moved 2026-08-12 — a receipt is never refused, and an uncertified goods-in notifies Logistics instead), pack→base UoM conversion (`uom_conversions`), **WBS required when the site has active `wbs_master` rows**, **supporting-document gate** (below), return source-receipt gates, FEFO auto-pick w/ allow-and-log override alerts. **`GET /entry/lining-systems`** (2026-07-18): recipe SAP lists per system code + site Done/Pending SQM — powers the Surface-Shields system-first Issue workflow (UI enforces: shield SAP without a selected system is refused; the code travels as an `LS <code>` Remarks suffix) |
| `bulk_import.py` | **Bulk Excel Import** (`POST /import/{kind}`; kinds `inventory`/`ledger` admin-only, `sme-*` {hod,admin}): dry-run→commit, upsert-only, header-name-driven, category canonicalisation, 3-tier ledger reconcile (exact-match / qty-correction / insert), Material_Code uniqueness resolution — the same plan/apply code `tools/excel_sync.py` drives |
| `entry_docs.py` | **Entry document system (parity A1/A4)**: `entry_attachments` upload/list/download/delete, `require_entry_documents` gate, WBS config endpoints. Doc types `consumption`/`receipt`/`return`/`safety_approval`/**`delivery_note`** (2026-08-13, the scan a warehouse must attach before a DN may ship) — the last two are per-item and per-shipment respectively, so neither goes through the per-BATCH `assert_entry_docs` gate |
| `hod.py` | pending queues, per-row approve/reject(+reason)/edit (`{"fields":{...}}`), `bulk-approve` (≤200), submitter bell dispatch (receipts have NO submitter column by design — returns/issues/adjustments do), return-approval → logistics email |
| `exec_summary.py` + `exec_pdf.py` | Executive Summary JSON/xlsx/**server-rendered fpdf2 PDF** (content-measured tables; nothing clips) |
| `weekly_report.py` | Friday 17:00 auto exec-PDF → `generated_reports` + sha256-tokenized 72-h link `/reports/weekly-exec/{token}` → WhatsApp+bell to every admin/HOD; `POST /admin/reports/weekly-exec/run`; needs `PUBLIC_BASE_URL` in deploy/.env |
| `lining_analytics.py` | `GET /analytics/lining-coverage` — read-only SME engine with **live-ledger availability pool**; RL/BL family coverage + 90-day-burn depletion dates (hod/logistics; scoped site-pinned, default CNCEC) |
| `logistics.py` / `warehouse.py` / `receiving.py` | PR→PO→assignment→DN two-stage approval state machine (`draft→pending_logistics→…→received`), RL/BL family separation, reschedules, force-close + 24 h undo, vendor returns |
| `requests.py` | supervisor SMRs (worker must be an active employee at the site) → SK approve → HOD issue queue |
| `sme.py` + `sme_engine.py` | SME read layer + planning engine — **dual TS/Python engines with golden parity; change BOTH or neither** (frontend twin: `frontend/src/sme/engine.ts`). **2026-07-30 COMPONENT IDENTITY:** every pool/total/shortfall/report row keys on `Material_Key` = `mat_key(Material_Code, SAP_Code)`; `sap_norm()` strips whitespace. **2026-08-05 SUBSET RULE:** the workbook's `Ordered_Qty` is the TOTAL procured and `Available_Qty` the arrived part OF it, so tier 2 = `max(ordered − available, 0)` (`Alloc_Pending` / `pool_pending_init`) and `Allocated_Qty` = `max(available, ordered)` — the additive reading understated the buy list by 22,951 units. Suite BC. **2026-08-04 SCOPE-WIDE BOTTLENECK:** every scope-level coverage KPI is `Σ buildable m² ÷ Σ remaining m²` (`session.ts scopeBottleneckCoverage`, shared by the Session and Location reports), never a quantity average — gated by `npm run test:ui-math`. **2026-08-03 STRICT TIER SEGREGATION:** readiness reads `Alloc_Available` ONLY (`Status`, `Completion_Pct`, `SQM_Achievable_Now`, `Coverage_Now_Pct`, `Fulfillment_Pct`); `Alloc_Ordered` feeds only the `*_With_Ordered_*` twins + the net buy list; `Allocated_Qty` is a conservation field and never a coverage numerator. **2026-08-02 STRICT DECOUPLING:** the estimator is a separate pool from the ERP warehouse — `available_qty` **is** `Initial_Available_Qty`, `ordered_qty` **is** `Initial_Ordered_Qty`, both from `sme_inventory_seed`; `receipts`/`consumption`/`returns`/`inventory` are never read, and the 2026-07-28 on-order netting `max(Initial_Ordered_Qty − Σreceipts, 0)` is GONE with them (it only ever undid receipts flowing into availability). Suite BA. **2026-07-28 two-tier allocation:** `Allocated_Qty = Alloc_Available + Alloc_Ordered`, `Shortfall_Available_Qty` (physical → feasibility) vs `Shortfall_Qty` (net → buy list), plus reverse-SQM `build_sqm_rollup`/`build_sqm_by_code` where a unit's achievable area is its SCARCEST component's rate. **`GET /sme/calculator?code&sqm`** (2026-07-18, level ≥2): recipe demand math `For_1_SQM × SQM` per component line, pack counts from Package_Size, **SME seed stock per COMPONENT `(Material_Code, SAP_Code)`** — 2026-08-04 overturned the last Material_Code pooling here too (was the ERP ledger until the 2026-08-02 decoupling); `sap_norm()` matches the SQL's `REPLACE(TRIM(...))` on both sides + per-line shortfall + human explanation strings — the 🧮 Smart Calculator tab's backend |
| `sme_master.py` | **Phase S6 (cutover day): Master Data CRUD** — `/sme/master/*` equipment/recipes/materials-seed/progress/settings, exact-lock {hod, admin}, HOD site-pinned, every write audited; equipment create seeds `sme_sqm_progress`, delete cascades it; materials write `sme_inventory_seed` ONLY (Canon Rule 2) |
| `ai/` | Hub Assistant SSE, OCR lanes, PDF extract, `/ai/nl-search` (unscoped, Ollama→safety gate→`gi_ai_ro` read-only PG login), **`/ai/query` two-lane chat-with-your-data** (below), **`ai/handwritten.py`** — the handwritten-consumption-form spec implementation (below §7) |
| `notifications.py` + `services/notifications.py` | in-app bell (`app_notifications`) + unified `dispatch()` (bell ALWAYS + best-effort WhatsApp; `X-Delivery-Preference: evening` stages into `pending_summary_notifications` for the 16:00 digest; critical always immediate) |
| `services/whatsapp.py` | Meta Cloud API v2 outbox (`whatsapp_outbox`), approved templates `gi_action_required/gi_status_update/gi_critical_alert/gi_otp_code/gi_evening_summary` (lang **`en`**), friendly #131030 sandbox handling |
| `webhook.py` | inbound Meta webhook (`/whatsapp/webhook` + `/api/v1/…`): verify-token handshake, **X-Hub-Signature-256 HMAC**, STOCK/RESET PASSWORD commands, session-text replies |
| `ratelimit.py` | see §6 |
| `console.py` | admin settings (whitelist incl. `maintenance_mode`, `require_entry_documents`, `mtc_required_category`), pg_dump backup, sessions revoke, outbox retries, lot lifecycle. **Bug Tracking Engine (2026-07-18)**: `bug_reports` + `title/severity/rollback_notes/safety_constraints/triage_notes`; admin triage drawer; **`GET /admin/feedback/{id}/prompt`** renders a self-contained coding-agent implementation prompt (report + triage + rollback plan + the project's non-negotiable gates) and `GET /admin/feedback-export.md` a batch digest — the portal never mutates code itself |
| `documents.py` | SOP/manual downloads, QR label sheets, employee badges, **`GET /documents/material-stickers`** (2026-07-24, {hod,admin}): 2×6 full-bleed A4 rack stickers replicating the operator's CNCEC sheet — Material Name (auto-shrink 17→11pt), QR from `SAP_Code`, SAP/MAT lines, category; `sap_codes` repeats = copies, category filter, site-scoped |
| `stock.py` | stock views + **`GET /stock/material-card?sap=&days=`** (2026-07-24; **Material Intelligence 2026-08-01**): the 📷 scan payload. `sap` resolves a SAP code, a **Material_Code**, OR a raw label payload (`"1163\|Cable Tie Wire ( Nylon)"` — the operator's stickers are `SAP\|Description`, and passing the whole string was the reported 404); `site_scope` pinning (''→403) is applied AFTER resolution. Returns stock + a gap-free 7–365-day receipt/consumption series with a **backwards-walked closing-balance line**, burn rate + **days of cover**, open **lots** (FEFO order, balance derived like SQL_LOT_BALANCE), last 12 **movements**, and a per-site split for unscoped roles only |
| `qc.py` | the `qc` role, dual scoping (`qc_scope`), accounts + admin-decided transfers, the `qc_inspections` ledger and the one decide endpoint. **2026-08-13:** the list/fetch are decorated with the material NAME (from `inventory."Equipment_Description"`) and the certificate's number/filename, and `GET /qc/inspections/{id}/certificate` streams the MTC **through the inspection**, inheriting its scoping rather than re-deriving it. A rejection mints `return_no` = `QCR-YYYYMMDD-<id>` |
| `health_monitor.py` | the 07:00 Morning Briefing: nine probes, each individually guarded, silent on a clean run but always audited, body forced to ONE line (Meta rejects a newline in a template parameter). **2026-08-13:** `probe_missing_mtc` + `dispatch_missing_mtc` — uncertified Surface Shields, grouped by PLACE and routed by location (warehouse → logistics/warehouse_user/qc; site → store_keeper/hod/qc/logistics) because the briefing's own admin+HOD audience cannot fix it |
| `testdb.py` | **the throwaway database the service tests run against (2026-08-13, rule 15).** Provisions `gihub_svctest` from `gi_database.db` via the production cutover script and rewrites `DATABASE_URL` before `db.py` is imported; refuses to run if source and target are the same name. `_apply_fixtures` carries the state a cutover-built database lacks (the AI read-only role, the `employees` site backfill, the nine PPE SAPs, the entry-doc switch) |
| `service_tests.py` | the 1502-check gate (suites A…BX), see §8 |

## 3. Database facts that bite

- Mixed-case column names are real (`"SAP_Code"`, `"Site_ID"`) — always quote.
- Ledger identity: **stock = Σreceipts − Σconsumption − Σreturns** per SAP/site
  (`v_live_stock`, `v_site_stock` views). Dates are ISO **text**.
- The 3 rowid-ledger tables keep `id := sqlite rowid` through migration so
  `posted_txn_ref` (`C:{rowid}`/`R:{rowid}`) stays valid.
- pending vs ledger naming traps: pending_returns `Return_Reason` → ledger
  returns `Reason`; pending `wbs` (lowercase) → ledger `WBS`; pending_returns
  has `override_required/override_reason/received_*` provenance columns.
- `entry_attachments` (BLOB-authoritative), `mtc_documents`, `wbs_master`,
  `form_drafts` were migrated from legacy; `generated_reports`,
  `phone_otp`, `auth_sessions`, `app_notifications`, `whatsapp_outbox`,
  `email_outbox`, `pending_summary_notifications` are new-stack-only.
- Locked rulings: **FEFO + over-issue/negative stock are allow-and-log, never
  hard-block** (2026-06-30); legacy hard-blocked — deliberate divergence.
- ⚠️ **`receipts."Date"` is the DELIVERY date typed off the vendor's paperwork,
  not when the row entered the ledger.** `posted_at` (2026-08-13,
  `c7a93e5d2b18`) is the latter, and the return form's 30-day window needs
  BOTH — it was measuring `Date` alone, so goods received this morning against
  a six-week-old document vanished from the source-receipt dropdown.
  `posted_at` is **NULL on every pre-migration row** (no backfill: a
  `DEFAULT CURRENT_TIMESTAMP` on `ADD COLUMN` would have declared all 632
  historical receipts posted on migration day), so readers must fall back to
  `Date` rather than assuming it is set.
- ⚠️ **A constraint that lives only in an Alembic revision does not exist on a
  database built by `metadata.create_all`** — which is how
  `tools/migration/cutover_migrate.py` builds production. Declare it in
  `models.py` too, in the same commit. `ux_asset_transfer_open` was missing
  from every created-from-models database until 2026-08-13.

## 4. Entry gates (parity sprint, 2026-07-13)

Master switch **`require_entry_documents`** (app_settings, **default ON** when
the row is absent; admin-editable):
- ON ⇒ Issue / Receipt / Return submission (single + bulk) requires ≥1
  uploaded supporting document (`attachment_ids`); returns additionally
  require **Return DN No.** + a **source receipt** (`GET /entry/return-sources`,
  30-day window; 365-day override needs a justification → `override_required=1`
  red-flagged in HOD approvals); qty capped to the source receipt.
- OFF ⇒ legacy-optional behaviour (tests run this way).
Independent of the switch: the **MTC gate for `Surface Shields`, which binds at
ISSUE and nowhere else** (see §4b), WBS requirement once a site has active WBS
rows, UoM conversion.

### 4b. The MTC rule, stated once and precisely

> **Material with no MTC CAN be received, and CAN be sent to site. It CANNOT be
> issued or consumed at the site.**

This line is the whole rule. It is repeated here because the sentence it
replaced — "MTC hard-block for `Surface Shields` receipts" — described the
pre-2026-08-12 behaviour and was left behind when the gate moved, so a reader
of this document would have concluded the opposite of what the code does.

| Step | Certificate required? | Enforced by |
|---|---|---|
| Warehouse goods-in (`warehouse.receive`) | **No** — recorded, and Logistics is notified to chase it | `quality.note_mtc` / `quality.warn_mtc_missing` |
| Site receipt staging (`POST /entry/receipts`, `/entry/bulk`) | **No** — same: recorded and chased | `quality.warn_mtc_missing` |
| Delivery Note creation and shipping (`warehouse.create_dn`, `ship_dn`) | **No** — travel is never gated | *(deliberately no call — suite BM asserts its absence)* |
| **Issue to a worker** (`ledger.stage_consumption`, `supervisor.approve_smr`) | **YES — hard block** | `quality.assert_mtc_for_issue` |
| **Consumption posted from an execution entry** (`execution.post_stock`) | **YES — hard block** | `quality.assert_mtc_for_issue` via the QSEP pre-check |

Refusing to RECORD something that has physically happened is the one thing an
inventory system must never do: the truck is in the yard, the certificate is in
somebody's inbox, and a receipt block makes real stock invisible to the shelf
report, to planning and to everyone. What the system controls is what happens
NEXT — and the moment before material reaches a worker is the moment the
certificate actually protects anyone.

The same reasoning is why the block is absent from DN creation specifically:
leaving it there would have reproduced the identical stall one hop later, with
the warehouse able to receive material and unable to send it anywhere.

Certificates are **inherited down the chain**, not re-uploaded — see
`quality.visible_mtc` for the precedence (PO line → DN → shipping warehouse →
site) and for why a certificate for site A does not clear site B.

### 4a. WBS + work types (Phase 9a, `services/wbs.py`)

Two CONDITIONAL gates — both no-ops until an HOD curates a list for the site,
which is what lets the feature ship without a flag day. Managed at `/hod/wbs`
(HOD/Admin); the endpoints predate the screen by a whole phase, which is exactly
why every one of the 1,674 live consumption rows carries a blank WBS.

- `assert_work_type` — the strict dropdown. Skipped entirely for the reserved
  markers `SUPERVISOR_REQUEST` / `STOCK_ADJUSTMENT` (written by
  `supervisor.approve_smr` and `ledger.stage_adjustment`; `rep_intent_vs_actual`
  joins on the first). A gate that refused those would break stock adjustments.
- `resolve_wbs` — precedence **explicit → work-type map → `sme_equipment.WBS_No`
  → none**, returning the `source` as well as the number. The explicit value is
  never overridden: the map is a default, not a correction.
- `assert_wbs` — moved here from `entry_docs` so the gate and the resolver read
  one list.

⚠️ **ORDER.** For an ISSUE the sequence is resolve → assert, inside
`stage_consumption` (the same place the QSEP gates sit, and for the same reason:
`supervisor.approve_smr` is the other mouth of the path). Asserting the form's
raw `wbs` first — which the router used to do — rejects every blank-box entry
for want of the number the map is about to supply, making the map unreachable.
RECEIPTS still assert in the router: they have no work type to resolve from.

`wbs_work_type_map` is keyed `(Site_ID, Work_Type_Norm)` where the norm is
lower + trim + collapse-whitespace. That merges the four case-collisions the
live ledger actually holds and nothing else; near-duplicates are the HOD's
judgement, made in the UI. Nothing is seeded by migration —
`/hod/site-config/work-types/suggestions` offers the ledger's own spellings
merged and counted instead.

### 4c. The printed consumption form (Phase 9c, `services/consumption_form.py`)

`GET /execution/forms/{code}[?esc=]` — SK, supervisor, HOD. Deliberately NOT
under `/mh` (exact-locked {hod, admin}; the supervisor is the person who carries
the paper).

- **Pre-printed names + a QR** is the whole design. The vision model in 9d never
  reads a material name (its weakest task) and never reads header context; it
  reads digits in boxes. QR payload is `GIF1|site|system|sub-activity|uuid` —
  five fields always, version-tagged so a future decoder can refuse rather than
  guess.
- ⚠️ **Every download REGISTERS a new `Form_UUID`** in `sme_consumption_form`.
  A GET that writes, because two prints are two sheets and 9d must tell a
  re-print from a re-photograph.
- ⚠️ **`Recipe_Fingerprint` pins ROW ORDER**, which is the mapping contract —
  9d matches handwriting positionally. Covers (index, esc, material, SAP, uom);
  EXCLUDES `For_1_SQM`, which cannot mis-map anything.
- ⚠️ **`_row_label` appends `Material_Description` only where a material code
  appears more than once in that form.** LSC8 prints one code four times
  (Comp-A/B/C/D); seven (system, material) pairs live are in this shape.
- **No write-in rows** (ruling Q9), **blank work date** (ruling Q6 — printed in
  batches, used same- or next-day; the generation date is in the footer).
- `_txt()` transliterates before `reports._latin`, which drops what latin-1
  cannot encode — an em-dash separator vanishing is how four rows become
  indistinguishable.

### 4b. The manpower shift model (Phase 9b, ruling Q10)

`services/planner.py` and `services/session_plan.py` share ONE split helper,
`planner.shift_split`. Two planners reading the same roster and disagreeing
about how it divides would be worse than either being wrong, because only one
of them would ever be checked.

- **Total headcount is invariant to the shift count**:
  `manhours / (target_days × 11)`. A person works one shift a day, so nobody
  works both — "two shifts, so half the people" under-hires by half.
- **What nights buy is calendar time.** `Days_Day_Shift_Only`,
  `Days_Both_Shifts`, `Days_Saved_By_Nights`, from
  `manhours / (crew × 11)` on the day-only and the whole in-scope roster.
- **The per-shift split comes from the ROSTER**, never `/ shifts_per_day`.
  This operator runs ~20 day against ~80 night; an even split understates the
  night crew fourfold. `Shift_Split_Basis` names the basis: `roster` · `site` ·
  `assumed_even` · `day_only`. ⚠️ A role with day workers and **no night
  workers** is NOT a valid basis — that says there is no night crew yet, and
  read as a proportion it puts 100% of a forced two-shift plan on days.
- **Capacity counts the roles the job needs**, matching `days_with_roster`,
  which always did. An idle blaster used to inflate `normal_capacity`,
  understating the overtime and the hire-to-clear advice. The payroll totals
  stay published beside them as `roster.Capacity_GI` / `Capacity_NON_GI`.

### 4d. The paper-first consumption workflow (Phase 9d)

    DRAFT_SUPERVISOR → PENDING_SK → PENDING_HOD → APPROVED
                                                └→ REJECTED (terminal, no bounce-back)

Labour-only activities skip PENDING_SK — a store keeper has nothing to verify.
`DRAFT_SK` / `PENDING_SUPERVISOR` are RETIRED, not deleted: the 9d migration
rejects any live row in them and `assert_transition` explains rather than
returning a lookup miss.

**Modules.** `ai/ocr_form.py` (QR decode → rectify → vision → parse) ·
`ai/form_jobs.py` (the async worker; the entry is created IN the worker, so the
model's reading never round-trips through a client and the `Form_UUID` is
consumed in the same transaction) · `services/form_intake.py` (the four
refusals: unknown sheet, wrong site, already filed, stale fingerprint) ·
`services/execution.py` (`sk_verify`, `qsep_status`, `post_stock`).

- ⚠️ **Four layers per material line**, each rendering only when it differs:
  `OCR_Qty` → `Supervisor_Qty` → `SK_Qty` → `Actual_Qty`.
- ⚠️ **`post_stock` is the ONLY writer for lining consumption** (Q1-b), calling
  `ledger.post_consumption` so FEFO, the over-issue warning and the audit line
  stay in one place. Idempotent on `Consumption_ID`; `Source_Ref` is
  `SME_EXEC:<entry id>:<line id>` — the **id**, because `next_entry_no` reuses
  a deleted entry's number.
- ⚠️ **QSEP blocks by default, HOD may override** (Q2-D) with a mandatory reason
  and a `qc_hod` dispatch. Checked AFTER the HOD's edits.
- **Rectification**: `PX_PER_MM = 8`, homography from four corner fiducials,
  falling back to the QR quad, then to "no crop" — `X-Crop` says which.
- **Cloud seam**: `client.vision_json()` + `GI_AI_VISION_PROVIDER`.

### 4e. Efficiency over time (Phase 9e, `services/mh_analytics.py`)

`GET /mh/analytics/daily` + `/scope` — HOD/Admin, inheriting the Man-Hours lock.
Reads `mh_timesheets` (hours, remarks) and `mh_production` (SQM). No migration:
both tables already carry `Work_Date`, `Equipment_Tag`, `System_Code`.

- ⚠️ **Two divisions by zero.** `sqm == 0` → `daily_mh_per_sqm = null`;
  `cum_sqm == 0` → `cum_mh_per_sqm = null` as well. A day can fail the first
  and pass the second.
- ⚠️ **`gap` vs `idle`.** Hours with no area is a gap (ruling Q12, carries a
  reason); neither hours nor area is idle. They look identical on a chart and
  mean opposite things.
- ⚠️ **The reason is `mh_timesheets.Remarks`, verbatim.** No taxonomy is
  inferred.
- Every calendar day between the first and last observation is emitted; the
  window is the OBSERVED range, not the requested one. `_date_span` caps at 730
  days so one typo'd year cannot ask a browser for 700,000 bars.
- Mixed lining systems WARN rather than refuse — the HOD may have wanted that
  view.

## 5. Frontend map (`frontend/src/`)

React Router routes in `App.tsx`; **`config/nav.tsx` is the single
source of truth for nav + route guards** (exact-lock `anyRole` / `minLevel`;
duplicate menu keys across groups are forbidden — use route aliases like
`/logistics/lining-coverage`). API via axios `api` (`api/client.ts`):
**`API_BASE` = `VITE_API_URL` (native builds; injected by release-*.yml as
`https://gi.giinventory.com/api`) or relative `/api`** (web: Vite proxies →
`:8000`; `VITE_API_PROXY` overrides for E2E); axios runs `withCredentials`
(cross-origin refresh cookie for the native shells); token in localStorage
`gi_token`, silent refresh on 401 (the rotated refresh token returns as an
httpOnly Set-Cookie — no JS storage), `detectClientType()` sends
`client_type` web|native at login (drives the RTR TTL), **429 →
`gi-rate-limited` event → RateLimitToast deadline countdown**, backend
unreachable → throttled console hint + `gi-api-unreachable` toast
(RateLimitToast is mounted at the app ROOT so the login page shows it),
`logApiFailure()` prints message/code/status/headers on network-err/403/5xx
incl. a Cloudflare-Access-block note. TanStack Query hooks in `api/hooks.ts`.

**PWA/offline:** vite-plugin-pwa autoUpdate SW (build-only; dev unaffected),
NetworkFirst cache for read APIs; **strict OTA** — `main.tsx` polls
`reg.update()` every 15 min AND on tab refocus, so deployments reach every
open client without a manual refresh. **Offline mutation queue**
(`offline/queue.ts`, IndexedDB `gi-offline`): only entry-form POSTs opt in via
`postWithOfflineFallback()` → `{queued:true}` + amber toast + header
`OfflineSyncBadge`; replay on reconnect with `X-Offline-Replay: 1`; rejected
rows are dropped+surfaced. **Send/Receive (Outlook-style):**
`SyncControls.tsx` header button = flushQueue + invalidate ALL queries;
gear popover sets the auto-sync cap (localStorage `gi_sync_interval_min`,
1–120 min, default 1; re-armed live via the `gi-sync-interval` event).
**Native shells:** Capacitor (`capacitor.config.ts`; `android/`/`ios/`
GITIGNORED — regenerated by `npx cap add`) + Tauri v2 (`src-tauri/`
COMMITTED; explicit CSP — update its `connect-src` if the API domain ever
changes). **QR scan-to-dashboard:** header 📷 → `QrScanner` →
`MaterialCardModal` (`GET /stock/material-card`: whitespace-normalized SAP,
site-scope-pinned, 30-day gap-free 2-series Recharts trend). **Entry documents:** `EntryDocsUpload` (file +
`capture="environment"` camera), `DocumentLibraryPage` (/hod/documents) with
inline image/PDF preview reused by the ApprovalsPage 📎 drawer. **Draft
recovery:** `lib/formDraft.ts` (localStorage, debounced) + DraftBanner on the
three entry forms. SME engine twin lives in `sme/engine.ts` (golden parity).

**2026-07-30 global table tools:** every grid renders through
**`lib/smartTable.tsx`** — a drop-in replacement for antd's `Table` (import
`Table` from there, not from `'antd'`; 99 instances across 45 files). It
derives a sorter for every `dataIndex`-backed column and a checkbox filter
for every categorical one, with no call-site change and no added chrome.
Rules: no `dataIndex` ⇒ no sorter (action columns); numeric and boolean
columns get a sorter but no filter; **server-paginated grids are left alone**
(auto-detected from controlled pagination — `total` AND `current` set — because
sorting one page of N silently lies). Filters cap at 30 distinct values,
search box above 8; explicit `sorter`/`filters` always win; `smart={bool}`
overrides. Known limit: filter labels come from the RAW field value, so a
column whose `render` maps codes to labels needs an explicit `filters` array
(as `UsersPage` does). Companion **`sme/materialCols.tsx`** renders material
components — variant SAP under the code, names WRAP rather than ellipse.

**2026-07-18 UI polish:** every antd Table carries `sticky={{ offsetHeader:
64 }}` (SmePage grids use the live-measured pinned-band offset instead);
**smart decimals** via `lib/format.ts` (`fmtQty`/`fmtCell` — `5.00`→`5`,
fractions keep ≤4 dp) wired into the generic `lib/columns.tsx` renderer (SME
coverage percentages keep their fixed `.1f` style; `sme/engine.ts` strings
are golden-parity-pinned). New pages/components: `BulkImportPage` (dry-run →
commit), `sme/SmartCalculator.tsx`, IssuePage Surface-Shields system-first
flow, OcrImportPage "Validate (handwritten spec)" + TSV export, Admin Console
feedback triage drawer + 📋 Prompt copy.

## 6. Security & rate limiting

`ratelimit.py` (in-house, no slowapi — resolves client IP
**CF-Connecting-IP → X-Real-IP → peer**, per-process store):
- per-endpoint dependencies: login 10/60, register 30/60, OTP burst 5/60 …
- `check_bucket(key,…)` arbitrary-key windows: **OTP 3/hour per source IP AND
  3/hour per target phone** (checked before anything else; failed sends burn
  quota; 429 + Retry-After).
- `PenaltyBox`: **5 invalid webhook HMAC signatures / 10 min ⇒ 15-min IP ban**
  (refused pre-parse, even for later valid signatures).
- `strict_limits_enabled()`: strict rules ON in production, **relaxed when
  `GI_DOTENV=0`** (hermetic tests), force-enabled by `GI_FORCE_STRICT_LIMITS=1`
  (suite AF).
**Auth = RTR (Refresh Token Rotation, 2026-07-25, alembic `f1a7c9e83b52`):**
15-min access JWTs + a **signed refresh JWT** (scope `refresh`, jti/fam/
client claims) in the `gi_refresh` httpOnly cookie, tracked in
**`refresh_sessions`** (UUID id, users.id FK, family_id, unique jti,
client_type, is_revoked + forensic columns). One login = one **family**;
`client_type` from the login body sets the TTL — **web 7 days, native
(Tauri/Capacitor) 90 days** (on MFA logins it rides inside the signed
mfa_token). Every `/auth/refresh` rotates in-family (old row → revoked/
'rotated'/replaced_by). **Replaying a revoked token = breach: the WHOLE
family is revoked (successor included) + SESSION_REUSE audit + 401 — the
user's OTHER families/devices survive.** Logout and the admin console's
single-session revoke are family-wide; `revoke_all_sessions()` (admin
reset / user delete / WhatsApp RESET PASSWORD) spans all families + the
legacy `auth_sessions` table (revoke-only now). Production sets the cookie
`SameSite=None; Secure` so cross-origin native refresh works; CORS defaults
include the fixed shell origins (`tauri://localhost`,
`capacitor://localhost`, …). ⚠️ `refresh_sessions` must STAY in the
`gi_ai_ro` REVOKE set (`create_ai_readonly_role.sql`) AND
`ai/safety.py` FORBIDDEN_TABLES. Secrets live ONLY in gitignored `deploy/.env`
(`config.py` dotenv-loads it on bare metal unless `GI_DOTENV=0` — that pin in
service_tests must NEVER be removed). Secret-scan every push range for the Meta token prefix (`EAA…`) and the
WhatsApp phone-number ID before pushing (the exact grep lives in the project
memory — deliberately not reproduced here).

## 7. AI routing layers

1. **Hub Assistant** (`/ai/assistant`, SSE) + insights/EOD — same-box Ollama,
   one warm model. Retrieval is `ai/manual_index.py` (BM25 over
   `USER_MANUAL.md`, no vector store, no embeddings) + `ai/manual_qa.py`
   (role gating, prompt). **Measured 2026-08-24 on the live 229 KB manual:
   2 ms to chunk, 15 ms to build the BM25 tables, 0.3 ms per search.** The
   index is warmed in the FastAPI lifespan (`manual_qa.warm()`), which is
   hygiene rather than a speed fix — perceived latency is Ollama token
   generation, not this. Four properties worth knowing:
   * **the role filter runs BEFORE scoring** (`Index.search(allowed=…)`), so a
     role's prompt cannot physically contain a chapter it may not see. That is
     the security boundary, not the prompt;
   * **an alias map expands both documents and queries** (`_ALIASES`,
     `expand_aliases`) — the corpus writes "purchase requisition" and users
     type "PR". It expands, never substitutes, and cannot widen what a role
     reaches (the chapter filter is upstream of it);
   * **a table adheres to the paragraph above it** when a long sub-section is
     wrapped, so a caption and its table are never retrieved separately;
   * **the fallback path** (used only when nothing scores) keeps whole `##`
     sub-sections up to `_PER_SECTION_CHAR_CAP = 3000` and never truncates §2
     at all — at the old 800-character head-truncation the access matrix,
     which starts ~1,900 characters in, was in NO non-admin prompt.

   ⚠️ **`USER_MANUAL.md` at the REPO ROOT is the only manual.** It is the AI
   corpus, the source of the in-app PDF and the ops PDF (both built by
   `tools/export_docs_pdf.py`, one command, two destinations). A second
   role-based `docs/USER_MANUAL.md` existed from 2026-07-26 to 2026-08-24 and
   fell four phases behind; suite CJ now fails if it comes back, and it
   compares §19's tab count against `ManHoursPage.tsx` rather than a number
   typed into the test.
2. **`POST /ai/query` (chat-with-your-data, level ≥2)** — two lanes:
   **template lane** = deterministic intent router (`ai/query_router.py`:
   returns/receipts/issues/stock/low-stock/expiring/top-suppliers/PRs/POs +
   time windows + site mention **+ 2026-07-18 deep filters: category
   aliases → bound `ILIKE :cat`, and material-family keywords ("furan",
   "remafix"…) → ILIKE over description/material-code PLUS the SME tables
   via the `sme_recipe.SAP_Code` join**), fully bound-param SQL, **scoped
   users' site enforced from the JWT** (safe for HODs, works with Ollama
   down; count questions return a `metric`); **NL lane** = unmatched
   questions from UNSCOPED roles only → `/ai/nl-search` machinery (Ollama
   coder → SCHEMA_HINT incl. SME schema + deep-ILIKE rule →
   `is_safe_select` gate → `gi_ai_ro` read-only login). The AI-5 ruling
   stands: generated SQL never runs for a scoped user.
3. Doc-intel: PR/PO PDF extract (preview-only), vision-OCR job queue, badge
   verify. LocateAnything is RETIRED.
4. **Handwritten consumption forms** (spec: `docs/features/handwritten-ocr`,
   v1.0 — vendored; "preserve exactly" list inside): the vision model
   (`ocr.CONSUMPTION_PROMPT`) TRANSCRIBES faithfully (ditto glyphs verbatim,
   struck-through flagged, raw `qty_text`, top-right `date_text`); every
   rule is deterministic in `ai/handwritten.py` — 3-format date parsing w/
   digit fixes, the 18-entry corrections list, ditto resolution, qty rules
   (additive `2+3` sums, blank→1, zero rejects), the spec fuzzy scorer
   (auto-accept conf ≥40 + lead ≥8, top-5 candidates), the CLOSED 8-rule
   substitution table (R1–R4), whole-batch stock simulation
   ((date,form,row) order, low-stock 5, negative → blocked), the
   [?]/⚠️/🚨 flag taxonomy, and the **17-column legacy TSV export**
   (blocked rows never exported). Endpoint: SK-locked
   `POST /ai/ocr/handwritten-process` — READ-ONLY (posting stays in the
   Issue flow). Changing a preserved rule: edit the owning spec file first,
   then the module, then the suite-AM pins.

### 7a. ⚠️ The vision envelope — three numbers that are ONE decision

Fixed 2026-09-01 after two production reports ("the Consumption Log fails
silently", "the new PDF hangs with a ReadTimeout"). Both were diagnosed as the
model being bad at tables. It is not. Reproduced on the operator's own files,
the model read both correctly and was cut off by the limits around it.

| Knob | Where | Value | Why that number |
|---|---|---|---|
| output budget | `ai/jobs.py: NUM_PREDICT`, `ai/ocr_form.py: FORM_NUM_PREDICT` | per lane (3072 / 1536 / 2560 / 384 / 2600) | a DN is 4 items × 3 fields (~350 tok); a consumption log is 30 rows × 9 fields (~2,400 tok). One number for both clipped the sheet at row 14 and never clipped the note |
| context window | `ai/client.py: vision_num_ctx()` | computed per call; floor 8192, ceiling 16384 | **Ollama runs this model at `n_ctx=4096` whatever the 128k model card says.** An 1800 px page is 3,120 prompt tokens of that. Since 2026-09-02 the window is sized from `ocr.estimate_image_tokens()` — calibrated to err HIGH, because under-counting aborts the runner while over-counting only costs KV cache |
| HTTP timeout | `ai/client.py: VISION_TIMEOUT_S` | 900 s | measured: the form lane takes 269–444 s and the consumption lane 361 s. The old shared 240 s ceiling could not physically be met |

⚠️ **Raising the output budget without raising `num_ctx` is WORSE than leaving
it alone.** Asking for 4,096 predicted tokens over a 1,400-token image aborted
the Ollama runner outright (`ggml_abort`, SIGABRT, "llama runner terminated"),
taking every other queued job with it and answering with an empty body and no
error field. Suite CP-05 pins the relationship.

Behind the budget sits a second guard: `ocr.salvage_truncated_json` rebuilds the
complete prefix of a reply that simply stops, **cutting only at a closing
bracket** and dropping the unfinished element. A clipped reply used to be
discarded whole — thirteen correctly-read rows thrown away to punish one clipped
row, reported to the store keeper as "unparseable, try the Paste tab".

Three more rules the lanes now share:
- **nothing read is a FAILURE, not a result.** An empty row list raises instead
  of finishing at `status='done'` with a blank grid, which was indistinguishable
  from a photo of a blank form.
- **an unreadable quantity stays `null`.** The prompt has always said "never
  invent 0 or 1"; `clean_consumption_row` invented it anyway, because
  `_to_float(None)` is `0.0`. The paste lane keeps its `0.0` default — a cell
  typed empty by a human is a different fact from a box a camera could not read.
- **images are capped twice**: `MAX_VISION_BYTES` for what reaches the model,
  `SOURCE_MAX_DIM`/`SOURCE_MAX_BYTES` for the copy kept for the QR decode and the
  row-crop rectifier. `/execution/ocr/upload` normalises at the door, so a 20 MB
  HEIC no longer arrives base64'd at ~27 MB in `ai_jobs.payload_json` — and the
  workers now NULL that column on both terminal transitions.

### 7a-ii. ⚠️ The orphan sweep, and why it is heartbeat-based

Fixed 2026-09-02 (alembic `c4a7e2b81f36`). `ai/jobs.py:fail_orphans()` used to
run at startup and execute, with no filter for who owned the row:

```sql
UPDATE ai_jobs SET status='error' WHERE status IN ('queued','running')
```

Correct for one process — the worker is an in-process `asyncio.create_task`, so
its jobs die with it. **Wrong for `uvicorn --workers 4`, which is what
`deploy/Dockerfile.api` runs.** When one worker crashed and uvicorn respawned
it, the new process's lifespan failed the in-flight OCR jobs of the three
workers still running them. Invisible at deploy (all four boot before any job
exists); it only bit on a respawn.

`sweep_orphans()` now reaps on **liveness, not existence**:

| Column | Meaning |
|---|---|
| `ai_jobs.worker_id` | which process claimed the row (`pid-uuid8`) — answers "which worker was this on" after the fact |
| `ai_jobs.heartbeat_at` | touched every 30 s (`GI_AI_HEARTBEAT_S`) for as long as the owner is working |

The predicate is `COALESCE(heartbeat_at, started_at, created_at) < now −
ORPHAN_STALE_SECONDS` (180 s, five missed beats), covering all three ages a row
can have: beating, claimed-but-not-yet-beaten, and never-claimed (a `queued`
row whose process died between the commit and `spawn`).

Three rules a later edit must not break:

- **The beat starts BEFORE `GEN_SEMAPHORE`.** A third concurrent job waits
  minutes on the 2-permit semaphore with status already `running`; a beat
  started after the wait would let the sweep reap a job that is patiently queued.
- **The stale window is NOT the job timeout.** A vision read legitimately runs
  up to `VISION_TIMEOUT_S` (900 s). Sizing the sweep off job *duration* would
  mean waiting 15 minutes to reap a corpse; sizing it off the *beat* is
  independent of how long the job takes.
- **The sweep also runs on a timer** (`orphan_sweep_loop`, 300 s). A worker that
  dies while its siblings stay up leaves an orphan no startup ever sees —
  uvicorn respawns it in seconds, long before the row goes stale.

### 7b. Uniqueness Bloom filters (`services/bloom.py`)

Three sets — `usernames` (both registries), `sap_codes`, `asset_serials` —
built at boot and refreshed every 300 s. ~2.4 KB and ~5 µs per lookup each.

⚠️ **The filter is an accelerator; the database is the authority.** A Bloom
filter answers "definitely NOT present" exactly and "maybe" probabilistically,
so it can retire the round trip for a FREE name and never for a taken one. The
rule that makes this safe under multiple uvicorn workers: **a "definitely not
present" may skip a READ and may never authorise a WRITE** — this process holds
its own copy of the bits, so a username another worker registered is absent from
it until the next refresh. Every `UNIQUE` index and `IntegrityError` handler is
untouched. Suite CP-20..CP-29.

`GET /auth/username-available` is the live case: a free name is answered from
memory (`checked: "bloom"`), a name that looks taken is CONFIRMED against both
`users` and `pending_users` before the endpoint will say so (`checked: "db"`).

## 7c. Phase 10 slice 10a — mandatory 2FA, shared limiters, AI guardrail audit

### The 2FA mandate (`auth.mfa_gate`)

⚠️ **The capability was already shipped; slice 10a added the ENFORCEMENT.**
`pyotp` enrol/verify/disable, the login challenge, the step-up password check
(audit A03-F8) and `SecurityPage.tsx` have existed since Phase 2, and
`users.totp_secret` / `totp_enabled` were already columns. What did not exist
was any reason for a privileged account to turn it on.

`POST /auth/login` now has a **third** outcome:

| password | role mandated | enrolled | deadline | outcome |
|---|---|---|---|---|
| ok | – | yes | – | `mfa_required` + `mfa_token` (unchanged) |
| ok | no | no | – | `access_token` (unchanged) |
| ok | yes | no | future | `access_token` + `mfa_enrollment_due` (banner) |
| ok | yes | no | **passed** | **`enrollment_required` + `enroll_token`** |

⚠️ **The enrolment token is scope-limited, and that is the whole control.**
`_decode` matches `scope` exactly and `get_current_user` asks for `"access"`, so
an `enroll` token opens only the three `/auth/2fa/*` routes that use
`enroll_or_current_user` — **not** `/2fa/disable`, which keeps the ordinary
dependency. Minting a normal access token there (the obvious shortcut) would
turn "you must set up 2FA" into the way to skip 2FA, and the account would be
both exempt and believed protected. Suite CR-05 is that assertion.

Both knobs are `app_settings` rows, matching `mtc_required_category`:
`mfa_required_roles` (default `admin,logistics,hod,qc_hod,auditor`) and
`mfa_enforced_from` (ISO date). ⚠️ **Every uncertain branch resolves towards
ACCESS** — no row, an unparseable date, an empty role list all mean warn-only.
A bug in the rollout of a control must not lock a company out of its own
inventory system.

### `rate_buckets` — the cross-worker half of four limiters

`deploy/Dockerfile.api` runs `uvicorn --workers 4`, and four limiters kept state
in process memory, so each enforced **4× its configured limit**:

| limiter | documented | actual on 4 workers |
|---|---|---|
| `rate_limit(n, w)` per-IP dependency | n | 4n |
| `check_bucket` OTP budgets (per IP, per phone) | 3/hr | 12/hr |
| `PenaltyBox` webhook HMAC bans | banned | banned on 1 worker of 4 |
| `_totp_failures` second-factor attempts | 5 | **20** |

The last is the worst: it is the ceiling on brute-forcing the second factor
against a `_verify_totp` that accepts three codes at any instant
(`valid_window=1`).

**Postgres, not Redis** — operator ruling re-confirmed 2026-09-02, generalising
the `login_attempts` mechanism rather than introducing a second idea of a shared
counter. Two layers, not a replacement: the in-memory check runs first and costs
nothing; the row makes the ceiling true across workers. Gated by
`strict_limits_enabled()` so hermetic runs write no rows.

⚠️ **It fails OPEN** (ruling Q1.2) — a throttle that takes sign-in down when its
own storage hiccups is worse than the attack it prevents. Deliberately the
opposite of the access matrix, which fails **closed**.

⚠️ **`read_bucket_shared` vs `check_bucket_shared` is not a style choice.** The
counting function INCREMENTS, so using it to ask "is this IP banned?" creates
the ban it was asking about — the first invalid webhook signature answered 429
instead of 403 until suite `limits` caught it. A test and a tally are different
verbs.

### `tests/ai_eval/` — the adversarial RAG audit

Two tiers; **only Tier 1 gates a merge** (suite CQ). Tier 1 audits the finished
*system prompt* — deterministic, no model — and therefore covers the retrieval
path AND the fallback path at once. Tier 2 audits the *answer*, needs a live
model, and is stochastic; it is a scored artefact on a schedule, because a
flaky gate is one people re-run rather than read.

Three ways it fails: a **leak** (no threshold — one is too many), a **broken
canary** (a canary that drifted into an allowed chapter would pass forever), and
a **policy change**.

⚠️ **The policy pin closes a blind spot the structural check cannot see.** Tier 1
compares the prompt's chapters against `allowed_sections(role)` — the same
allowlist that built it — so a policy *widening* is self-consistent and
invisible. Proved by negative control: granting a Store Keeper chapters 7 and 17
failed **zero** structural checks. `cases/policy.yaml` pins the allowlists as
data; superset test, so gaining a chapter fails and losing one does not.

## 7d. Phase 10 slice 10b — daily claim, valuation, training gate

### ⚠️ The daily loops were firing four times a day

`report_center.scheduler_loop` claims its work with an atomic `last_run`
UPDATE. The three DAILY loops did **not**: the 07:00 morning briefing, the
16:00 evening digest and the Friday 17:00 executive report each slept until
their hour and dispatched, **in every worker**. `deploy/Dockerfile.api` runs
`uvicorn --workers 4`, so every one of those messages reached every recipient
four times. Invisible in development (one worker) and invisible in the tests
(the loops are off under `GI_SCHEDULER=0`), which is how it survived three
phases.

`services/dailyjob.py` is the claim they were missing — one row per job key,
one atomic `INSERT … ON CONFLICT DO UPDATE … WHERE last_run < :due RETURNING`.

- **It must stay ONE statement.** Select-then-compare-then-update is a
  check-then-write, and four workers waking on the same clock tick is exactly
  the case that window is open for.
- **`due` is the SCHEDULED time, not `now`** — comparing against `now` lets a
  worker that started a second later re-claim the same run.
- ⚠️ **It fails CLOSED**, the opposite of the rate limiter. A missed briefing is
  silence somebody notices; a double-claimed one is four messages everybody
  learns to ignore.

### Track 2 — the day-shift chase

`health_monitor.probe_day_shift_mtc` asks a sharper question than
`probe_missing_mtc`: not "is there uncertified stock?" (a standing condition,
true for weeks) but "is uncertified material staged for the crew starting in an
hour?" — which has a deadline, and the deadline is today.

`sme_execution_entry.Shift` (`'Day'`/`'Night'`/NULL) makes that answerable.
⚠️ **NULL is skipped, not assumed.** Every pre-slice-10b entry has one and there
is no honest backfill; reading NULL as `'Day'` would make the probe scream about
a year of history on its first morning.

Channels are chosen by **who** is being asked, not by urgency:

| Recipient | Channel | Why |
|---|---|---|
| Logistics, site SK/HOD/QC | in-app + WhatsApp | colleagues; numbers are in `users` |
| Logistics **only** | **email** (`emailer.py`) | the role that can obtain the document, and they live in a mailbox. Emailing everybody makes three copies of one message |
| anybody outside the company | **WhatsApp DRAFT** | `whatsapp.draft_text` writes `status='draft'` and never posts. Released by a human via `POST /admin/whatsapp/{id}/approve` |

⚠️ A chase that leaves the company is read as the company's position; an
automated one naming the wrong purchase order is a commercial mistake nobody
reviewed. `_po_for` returns `None` rather than a plausible wrong PO, and the
message then says "PO unknown".

The chase rides the **same 07:00 claim** rather than a second 07:30 timer — two
alerts half an hour apart trains people to ignore both.

### Track 3 — valuation & 30-day burn (`services/valuation.py`)

⚠️ **Un-costed lines are counted, never summed as zero.** `inventory.Unit_Cost`
defaults to 0; on the live database **every** line is un-costed, so a naive
valuation reports `SAR 0.00` for a site holding 731 units — arithmetically
correct and a lie a board would act on. They are reported as
**"Not Valued (N items)"** with a footnote saying the value is a **floor**, and
a coverage percentage sits beside the total.

⚠️ **ERP and SME numbers are printed side by side and never added** (rule 1a).
`inventory`/`consumption` are the live ledger; `sme_inventory_seed` is a frozen
project-wide estimate. Summing them double-counts every material in both.

⚠️ **`months_cover` is `None` when the burn is zero**, and the burn rate divides
by the **full** window rather than by the days that had activity — dividing by
active days flatters a site that worked eight days in thirty, and a board reads
it as a run rate.

`fpdf2` only (`_ValuationPDF` subclasses `_ExecPDF`, so there is one branding
implementation). Reachable by admin, HOD and auditor via `_EXEC_READERS`.

### Track 5 — the training hub and the SOFT gate

⚠️ **The gate never refuses**, and that is operator ruling Q5.1. Phase 9 made
photographing a form the primary way consumption is filed; a hard gate means a
supervisor holding a filled sheet at 06:00 cannot file it because of a video.
FEFO (allow-and-log) and the MTC gate (moved out of receipt) were ruled the same
way. `GET /training/gate/{feature}` returns `allowed: true` unconditionally;
only `show_interstitial` varies. **If a future slice makes it hard, the refusal
must move server-side into `POST /execution/ocr/upload` — a UI-only gate is not
a control.**

"Watch Later" POSTs to `/training/defer`, which increments a counter the HOD
dashboard shows with a name against it. The control is visibility.

⚠️ **`module_version` is in the compliance unique key.** Bumping
`training_modules.version` invalidates every prior acknowledgement by
construction — old rows are kept (auditable) but stop matching. Keyed on
`(user, module)` alone, re-recording a tutorial would leave everybody certified
against a video they have never seen: worse than no record, because it would be
produced as evidence.

⚠️ **Videos are URIs, not blobs** (Q5.3). A 200–600 MB tutorial set in a
`LargeBinary` lands in every nightly `pg_dump`, cannot serve HTTP Range requests
(so the viewer cannot seek), and competes for OLTP buffers. Languages: `en`,
`ta`, `ta-Latn` (Tanglish), `ar`.

⚠️ **The HOD dashboard is driven from `users`, not from `training_compliance`.**
Listing the compliance table shows only people who have engaged — so somebody
who has never opened the module, the one most worth knowing about, would be
invisible. The absence is the finding.

## 8. Testing — the gates

> 🔄 **2026-08-13 — the service tests run against their OWN database.**
> `backend/api/testdb.py` provisions `gihub_svctest` from `gi_database.db`
> (via the production cutover script) and rewrites `DATABASE_URL` **before
> `backend.api.db` is imported** — the engine is built at import time, so that
> ordering is the entire mechanism. `DATABASE_URL` below now names only the
> CLUSTER; its database is never opened, and provisioning exits non-zero if
> the two resolve to the same name. Suites B…BX commit through the real ASGI
> app and cannot be rolled back, which is why isolation and not cleanup is the
> answer. See `PROJECT_HANDOVER.md` rule 15.

```bash
# 1. service tests (1868 checks, suites A…CJ) — CI mirror, own throwaway DB
DATABASE_URL=postgresql+psycopg2://postgres@127.0.0.1:5433/gihub \
JWT_SECRET=ci-only-service-test-secret-key-32bytes-min \
.venv/bin/python -u -m backend.api.service_tests

# 2. SQLite↔PG parity oracle (5 aggregates) — same env vars (Phase B: tools/)
#    ⚠️ meaningful ONLY on CI or a freshly-cutover DB (PG is permanently ahead).
#    `sme_materials` is asserted as a CONSERVATION invariant since 2026-07-30:
#    the PG port is per-COMPONENT while the frozen SQLite view pools by
#    Material_Code, so both sides are rolled up and every quantity must match.
.venv/bin/python tools/parity_check.py

# 3. frontend
npm run build --prefix frontend && cd frontend && npx tsc --noEmit

# 4. headless E2E (Playwright — builds/destroys its own gihub_e2e_pw stack)
cd tests/e2e && npm test        # 107 tests, ~37 s

# 5. alembic single head
.venv/bin/python -c "from alembic.config import Config; from alembic.script import ScriptDirectory; c=Config('backend/alembic.ini'); c.set_main_option('script_location','backend/alembic'); print(ScriptDirectory.from_config(c).get_heads())"
```
Test-compat switches: service_tests sets `require_entry_documents='0'` first
(suite AH tests it ON); Playwright global-setup does the same in its clone —
the `gated` project (entry-docs.spec) runs AFTER the parallel pack because it
flips the global setting. Legacy `legacy/bug_check.py` (599, self-rooted —
run `.venv/bin/python legacy/bug_check.py`) guards the frozen Streamlit app;
its models-parity check carries an allowlist for new-stack-only columns
(SME `SAP_Code`s, bug_reports triage fields). New suites 2026-07-18:
**AJ** bulk import · **AK** OCR doc assist + prompt pins · **AL** QR/
returnables · **AM** handwritten-OCR stages + ask-data filters · **AN**
Surface-Shields workflow + Smart Calculator + report scoping · **AO** Bug
Tracking Engine. 2026-07-24…26: **AP** material-card scan dashboard
(site-scope matrix) · **AQ** RTR (per-client TTLs, in-family rotation,
replay → family revocation w/ other-family isolation, logout, audit).
2026-07-27…30: **AW** pg_excel_sync (PG-only guards, atomicity, idempotency,
COALESCE preservation) · **AX** session-report aggregation · **AY** two-tier
allocation + reverse SQM · **AZ** component identity (pools, dirty-SAP
normalization, naming, per-component shortfall, the bottleneck as a specific
drum, 4 `az-revert` checks against the old grain, 3 `az-sql` checks on the
seed-driven availability SQL, 5 `az-cutover` convergence checks, 2
`az-header` workbook-column checks).
2026-08-02: **BA** SME strict decoupling — 11 checks that post real ERP
receipts/consumption/returns against an SME material's own SAP and require
every SME read back byte-identical, plus a word-boundary source guard that
neither SME quantity query names an ERP table.
2026-08-05: **BC** SME subset rule — 16 checks that tier 2 is the PENDING
delivery and the ceiling is `max(available, ordered)`, including the live
GI-8005763 shape (143,000 of 143,000 → empty pipeline, 9,685 to buy) and two
checks that READ the workbook to confirm `available <= ordered` still holds.
2026-08-03: **BB** SME tier segregation — 18 checks on the PHENACIN ACP POWDER
shape (0 physical / 500 on order): 0 immediate achievable SQM, BLOCKED status,
bottleneck named despite a zero NET shortfall, nothing on the buy list, the
forecast parked in its own `*_With_Ordered_*` fields, plus a control (move the
same units to `available` → fully ready) and a revert-check on the outlawed
`Allocated_Qty / Demand_Qty` formula.
⚠️ **The derived-view parity gate is partly vacuous for SME materials** —
every SME material in the legacy data has zero ERP movement, so both sides
agree trivially on exactly the columns the decoupling touches. Suite BA
covers it directly instead, on live-shaped data.
⚠️ Test-authoring trap suite AQ exposed: httpx
`cookies.set(..., domain="host")` SILENTLY drops the cookie (host-only
domain mismatch) — suite E's replay checks passed vacuously for months;
never pass `domain=`. The NL round-trip check needs the `gi_ai_ro` role —
CI provisions it in the workflow; locally re-run
`backend/scripts/create_ai_readonly_role.sql` after any reload/DDL drift.
Manual matrix: [automatic_test.md](automatic_test.md).

**CI/CD:** `postgres-dual-ci.yml` = bug_check + dual_ci + parity +
**gi_ai_ro provisioning step** + service_tests (with `GI_AI_RO_URL`) +
frontend build. ⚠️ **The dual-ci job has NEVER passed on the GitHub runner**
(30/30 failures since 2026-07-07, always at the bug_check step) despite
599/0 locally under every simulated CI condition (clean tree, latest deps,
Linux package set, UTC, case-sensitive FS) — since 2026-07-26 the step
re-emits failing ❌ checks as public `::error::` annotations + uploads
`bugcheck_ci.log`/`BUG_REPORT.md` artifacts, so the next push names the
culprit. `deploy.yml` (v1 Streamlit) is **manual-only**; `deploy-v2.yml`
(manual, gated) is the cutover pipeline (post-restructure `tools/` paths +
the RO-role step). **Release pipeline (tags `v*` or workflow_dispatch
ONLY):** `release-desktop.yml` (macos-14 + windows-latest, `npx tauri
build` → dmg/nsis-exe/msi — ✅ green on v0.1.0–v1.0.1) and
`release-android.yml` (ubuntu + **JDK 21 — Capacitor 8 hardcodes Java 21;
JDK 17 was the v0.1.0–v1.0.1 failure**; `cap add android` regenerates the
gitignored project → debug APK); both inject
`VITE_API_URL=https://gi.giinventory.com/api` and attach assets to the same
tag Release via softprops/action-gh-release.

## 9. Operational notes

- Local dev: **`./bin/dev.sh localhost`** raises Postgres + API + Vite in one
  command (`tunnel` adds the cloudflared connector for local.giinventory.com;
  `gi` serves the gi.giinventory.com mirror without one; `stop` guarantees a
  clean slate — see PROJECT_HANDOVER §"Running it locally"). The pieces
  individually are still `./run_api.sh` (:8000, asyncpg → :5433/gihub) +
  `npm run dev --prefix frontend` (:5173). Hermetic: prefix
  `GI_DOTENV=0 GI_SCHEDULER=0`.
- **Local Cloudflare tunnel:** exactly ONE connector should run — the managed
  root LaunchDaemon (`/Library/LaunchDaemons/com.cloudflare.cloudflared.plist`).
  Several simultaneous connectors on different tunnel IDs is what produces the
  recurring **Error 1033**; diagnosis + recovery commands live in
  `deploy/cloudflared/README.md`. ⚠️ The tunnel token is passed as a
  command-line argument and is therefore visible in `ps aux`.
- Mirror Postgres runs on brew postgresql@16 :5433 (autostart).
- Meta/WhatsApp is LIVE (templates approved, lang `en`); operator TODOs that
  remain: approve `gi_evening_summary`, set webhook env + subscribe URL,
  set `PUBLIC_BASE_URL`.
- **Native apps + Cloudflare Access:** the domain sits behind Access; the
  installed apps have NO Access SSO session, so `/api` calls die as
  CORS-killed 302s ("Server unreachable" while the web portal works).
  One-time Zero Trust dashboard fix: Access application for
  `gi.giinventory.com/api/*` with a **Bypass (Everyone)** policy (the API
  self-guards). Full walkthrough + Apple-Silicon install fix (`sudo xattr
  -cr` **+ `codesign --force --deep --sign -`** — M-series refuses fully
  unsigned code) + local JDK-21 path: `docs/NATIVE_APPS.md`;
  troubleshooting: `docs/DEBUGGING.md` + `tools/diagnose_sync.py`.
- Remaining program work: **the Hetzner production deployment is PAUSED by
  decision (2026-07-30)** — the next phase is Feature Fine-Tuning and UI
  Polish. When it resumes: runbook `tools/migration/README.md` (includes the
  post-load Excel re-sync + SME reseed). Everything else through the 2026-07-18 pre-deploy
  batch AND the 2026-07-24…26 native program (Capacitor/Tauri + release
  pipeline + RTR + Send/Receive + QR stickers/scan) is SHIPPED
  (B2/B3/B4/B7 remain documented-optional). Ops handoff PDFs live in
  `docs/export/`
  (regenerate: `python tools/export_docs_pdf.py`).
