# SESSION HANDOVER — read this first, then `PROJECT_HANDOVER.md`

> **Updated 2026-08-05**, closing the asset-management + documentation session.
> Branch **`main`**, clean, at **`5cd44b7`** (PR #30 merged), plus
> `chore/version-bump-and-docs-polish` for the version and manual work.
> The project is **feature-complete and stable**. Every gate is green.
> **Nothing is mid-flight — there is no half-finished work to pick up.**
>
> **Shipped version is `1.2.0`**, and three files must always agree on it:
> `frontend/src-tauri/tauri.conf.json`, `frontend/package.json`,
> `frontend/src-tauri/Cargo.toml`. They had drifted to `0.1.0 / 0.0.0 / 0.1.0`,
> which is why the v1.2.0 release published installers named `0.1.0`.
> `release-desktop.yml` now **fails the build** on drift or on a tag mismatch.

---

## 1. What this project is

A multi-site warehouse inventory ERP + procurement chain for General Industries,
running as **two stacks against one PostgreSQL database**:

| | Stack | Where |
|---|---|---|
| **Live / current** | React 19 + Ant Design v6 SPA → FastAPI (async SQLAlchemy) → PostgreSQL 16 on `:5433` | `frontend/`, `backend/` |
| **Frozen** | The original Streamlit app. Still runs, still gated by its own 599-check regression suite, but **no new features** | `legacy/` |

Bridge tools live in `tools/`, archived data in `data-archive/`. `REPO_MAP.md` is
the segregation contract between them.

**Deployment is PAUSED by decision, not by blocker.** Everything needed for the
Hetzner rollout is built and documented (`tools/migration/README.md`,
`docs/DEPLOY.md`); it simply has not been executed yet.

---

## 2. ⚠️ The rules you can break without noticing

Full text in `PROJECT_HANDOVER.md` → *PAST — critical architecture rules, LOCKED*.
Each of these was a real bug whose symptom showed up far from its cause.

### SME Subset Rule (rule 1c)
`Ordered_Qty` is the **TOTAL procured for the project**; `Available_Qty` is the
part of it that has **physically arrived**. Available is a *subset* of ordered.

```
tier 1  = available_qty
tier 2  = max(ordered_qty − available_qty, 0)     ← the PENDING DELIVERY
ceiling = tier1 + tier2 = max(available, ordered)
to buy  = max(demand − max(available, ordered), 0)
```

Treating them additively double-counts everything already on the shelf. It
understated the buy list by **22,951 units across 22 of 30 materials**, and on
`GI-8005763` — where all 143,000 ordered units had arrived — it read 286,000 and
reported **nothing to buy** against a demand of 152,685.

### SME Tier Segregation (rule 1b)
**The UI never merges physical and pipeline stock.** Tier 1 alone drives every
"can we build it today" answer: `Status`, `Completion_Pct`, `SQM_Achievable_Now`,
`Fulfillment_Pct`. Tier 2 feeds only the `*_With_Ordered_*` twins and the net buy
list.

`Allocated_Qty` (= tier 1 + tier 2) is a **conservation field** so that
`Demand = Allocated + Shortfall`. It is **not** a coverage numerator and nothing
may colour it green. The engine always had this right; six presentation layers
did not, and overstated buildable area by **9,118 m² — 21.5 % of the programme**.

### RBAC — the Auditor role (rule 7)
View-only is enforced **once, by ASGI middleware keyed on the HTTP method**
(`backend/api/readonly.py`) — never per-endpoint.

Every `POST` / `PUT` / `PATCH` / `DELETE` from an `auditor` is refused with 403
unless it appears on a small documented allowlist (**126 of 143** mutating routes
blocked). This shape is the point: a per-endpoint check is only as good as the
developer who remembers it, and the one that gets forgotten **fails open**. Keying
on the method means a route added next year is closed from the moment it is
written.

> **If you add an endpoint and it 403s for an auditor, that is correct.** Only add
> to the allowlist in `readonly.py` if the route genuinely changes nothing.

### Two more that bite just as hard
* **Component identity (rule 1)** — the key is `(Material_Code, SAP_Code)`.
  Never pool by `Material_Code` alone.
* **SME ⇄ ERP decoupling (rule 1a)** — every SME number comes from
  `sme_inventory_seed`. A warehouse receipt must not move an SME figure.

### And the standing one
**Both SME engines change together.** `backend/api/sme_engine.py` and
`frontend/src/sme/engine.ts` are line-for-line mirrors proven equal by
`npm run parity:sme`. Any numeric change = change BOTH + regenerate the golden,
**in one commit**.

---

## 3. What was added most recently

> **2026-08-05 (late) — locations from the workbook, and the documentation pass.**
> `feat/excel-location-sync-and-ui` (PR #30) and
> `chore/version-bump-and-docs-polish`. Full account in
> [`EXCEL_LOCATION_SYNC_RUNLOG.md`](EXCEL_LOCATION_SYNC_RUNLOG.md).
>
> * **THE GOLDEN RULE — a `Location` on a Consumption Log row is what MAKES it
>   a reusable asset.** Blank means consumable. Nothing else is consulted, and
>   that matters: 1,165 of 1,166 real rows are blank, so any looser test would
>   manufacture a thousand phantom assets on the first run.
> * **THE WORKBOOK SEEDS, THE APP OWNS.** `storage_locations` upserts
>   `DO NOTHING`; a SAP with any existing rack assignment is skipped; an
>   existing asset keeps its status, rack and GPS fix. One narrow exception,
>   guarded on `last_seen_by = 'excel-sync'` **and** no coordinates: a unit the
>   app has never touched does take a corrected Location text.
> * ⚠️ **Both new columns are effectively EMPTY today** — `Rack/Current
>   Location` is blank in 453 of 453 rows, `Location` is filled on 1 of 1,166
>   (and that row has no serial, so it cannot be keyed and is reported back
>   every run). A sync seeds nothing. That is correct, not a failure.
> * **`asset_units.status` gained the operator's vocabulary** — `working`,
>   `not_in_use`, `repair` — beside the custody values, in ONE field. The
>   workbook has no Status column, so the app is where condition is recorded.
> * **Material NAMES now sit beside every bare code** in six tables. The SME
>   name comes from `sme_recipe`, **never** `sme_inventory_seed` — rule 1a makes
>   the seed the sole source of every SME quantity, and a label lookup is how a
>   quantity read sneaks in later. Suite BJ pins that.
> * **Manuals rewritten for non-technical readers.** Every ASCII-art diagram,
>   shell command and developer identifier is gone; **zero fenced code blocks
>   remain**. `build_manual_pdf.py` gained three real fixes — see §4a.
>
> **2026-08-05 (earlier) — the overnight asset/SME programme.** Six phases on
> `feat/overnight-asset-and-sme-upgrades`; the full account is
> [`OVERNIGHT_ASSET_TRACKING_RUNLOG.md`](OVERNIGHT_ASSET_TRACKING_RUNLOG.md).
> The headlines a fresh session needs:
>
> * **Surface-Shield consumption now lands in `sme_consumption_log`** and is
>   shown as a SIDE NOTE beside the estimator. **Rule 1a is intact by ruling** —
>   `sme_inventory_seed` is never written, and suite BF proves the SME payload
>   is byte-identical across a routing write. Do not "fix" the estimator to
>   subtract it.
> * **`Tank No.` is ambiguous, not just dirty.** `TNK-091` matches both TRAIN J
>   and TRAIN K. `sme_tank_alias` holds unresolved aliases for a human; nothing
>   is ever guessed.
> * **THE APP WINS on `Surface_Area_SQM`** — `SQM_Override` survives
>   `--sme-reseed` and the sync reports the divergence.
> * **New surfaces:** `/locator` (rack locator, minLevel 0), `/assets`
>   (serialised units + GPS), SME → 🧾 Actual Consumption.
> * **`For_1_SQM` is hidden from the UI and every export**, but is still in
>   `/sme/snapshot` — the TS engine computes demand in the browser.
> * **No engine change was made**, which is why parity is 1,313 unchanged.

| Feature | Where | The short version |
|---|---|---|
| **BM25 chatbot retrieval** | `backend/api/ai/manual_index.py` | The assistant used to be handed its whole allowed manual per question (~180 KB for an Admin). It now retrieves ~6 relevant passages: **admin prompt 178,146 → 4,075 chars (97.7 %)**, 0.37 ms, no vector store and no new dependency. The role filter runs **before** scoring — that is the security boundary. |
| **Fence-aware manual parsing** | same | Shell comments inside ```` ```bash ```` blocks (`# 1. Pull the new code`) were parsing as chapters 1-4 and **overwriting Introduction, Roles, Login and the Store Keeper manual for every role**. Never parse chapters with a bare `^# \d+\.`. |
| **Idle sign-out** | `frontend/src/auth/useIdleLogout.ts` | 30 minutes, 2-minute warning, cross-tab via localStorage. The client timer is the trigger; the **revocation** is the substance — it calls the normal logout, which kills the refresh family server-side. |
| **Per-account login throttle** | `backend/api/ratelimit.py` | The existing limit was per-IP and blind to credential stuffing across hosts. 8 failures per username per 15 min. **Throttles, never locks** — a per-account limit is a DoS vector. |
| **Global ⌘K search** | `frontend/src/components/CommandPalette.tsx` | Pages *and* live stock: type a SAP code, material code or description and jump to the material card. Reuses `/stock/by-site`, so site scoping stays server-side. |
| **DB indexes** | alembic `e7c3b95a41d2` | 7 hot-path indexes, **benchmarked** on a clone inflated to 260k/240k/429k rows: 20×, 92×, 6×, 6×. Four candidates were **rejected on evidence** (two cost 9.5 MB each for zero planner uses). |
| **Branded exports** | `pdf_tables.py`, `xlsx_style.py` | Overflow-proof PDFs (columns wrap, nothing truncated) and the premium logo layout on **every** xlsx. ⚠️ **The xlsx header row moved to row 6, data to row 7.** |

---

## 4. The gates — LOCKED baselines

A change that lowers any of these is a regression, not a new normal.

| Gate | Baseline | Command |
|---|---|---|
| Backend service tests | **1228 / 0** (suites A…BJ) | `GI_DOTENV=0 .venv/bin/python -m backend.api.service_tests` |
| Playwright E2E | **57 / 57** | `cd tests/e2e && npm test` |
| SME UI math | **33 / 0** | `npm run test:ui-math --prefix frontend` |
| SME TS↔PY parity | **1,313 comparisons** | `npm run parity:sme --prefix frontend` |
| Legacy regression | **599 / 0** | `.venv/bin/python legacy/bug_check.py` |
| Frontend | `tsc -b` + build + `oxlint` clean | `npm run build --prefix frontend` |
| Alembic | single head **`a71e93b4c2f8`** | `cd backend && alembic heads` |
| `gi_database.db` | sha256 `00652932…ba038` **unchanged** | `shasum -a 256 gi_database.db` |

> ⚠️ **`tools/parity_check.py` can no longer pass and is NOT a gate.** It
> compares the frozen legacy SQLite against Postgres, and they have diverged
> permanently: `consumption` holds **1** row in SQLite against **1,133** in
> Postgres, `inventory` 306 against 466. Every sync since cutover has written
> Postgres only. Its failure carries no information about any code change —
> **do not spend a session "fixing" it.** Retiring or re-baselining it is an
> operator decision that has not been made.

### 4a. `build_manual_pdf.py` — three defects fixed 2026-08-05

Worth knowing because each one silently degraded every PDF:

1. **`_ascii()` turned unmappable characters into `?`, silently.** Emoji in tab
   names ("📥 Incoming PRs") meant a page describing eight tabs carried eight
   question marks. Now: maths symbols are spelled out, decorative symbols are
   dropped **by Unicode category** rather than by a hand-written list that every
   new emoji defeated, and anything still unrepresentable is **named in the build
   output**. That warning found 56 characters on its first run.
2. **Wrapped list items were split into a bullet plus an orphan paragraph.** The
   parser read only a bullet's first line and let its indented continuation fall
   through to the paragraph branch.
3. **Table cells were capped at four lines and hard-truncated with `...`.** Cells
   are now measured with real font metrics (`get_string_width`) and the row grows.

Also: `_strip_md_punct` removed `**` but not single `*`, so `*italic*` printed
literal asterisks while `**bold**` printed clean. Both are stripped now — body
text renders in one font by design, because mixing fonts mid-paragraph made
fpdf2 overflow the right margin.

---

## 5. Daily commands

```bash
./bin/dev.sh localhost      # Postgres + API + Vite → http://localhost:5173
./bin/dev.sh stop           # kill API + Vite + our connector
./bin/power.sh wake         # bring the shared services up after a sleep
./bin/backup_db.sh          # snapshot the database into .backups/
```

Regenerate the manual PDFs (master + one booklet per role):

```bash
.venv/bin/python build_manual_pdf.py --role all
```

### Re-cutting a release whose installers were mis-versioned

The v1.2.0 assets were named `0.1.0`. To rebuild them under the correct version,
after the version bump is merged to `main`:

```bash
gh release delete-asset v1.2.0 --yes $(gh release view v1.2.0 --json assets --jq '.assets[].name')
git tag -d v1.2.0 && git push origin :refs/tags/v1.2.0
git tag v1.2.0 && git push origin v1.2.0
```

Deleting the assets first is what makes this safe to repeat: the release job
attaches files, and a re-run against a release that still holds the old
`GI Hub_0.1.0_*` files leaves both versions sitting side by side with no
indication which is current. Deleting the **tag** is what makes the workflow fire
again — pushing an existing tag is a no-op.

> ⚠️ **Bump the three version files BEFORE re-tagging.** `release-desktop.yml`
> now refuses to build when they disagree with each other or with the tag, so a
> premature re-tag fails fast rather than publishing another mislabelled binary.

---

## 6. Open items — none of them blocking

> Re-verified 2026-08-05. **The two items that used to head this list are now
> DONE:** the Auditor reads the HOD executive-summary endpoints
> (`require_roles("hod", "auditor")`, GET-only, `readonly.py` untouched), and the
> login throttle is now **cross-worker**, backed by the `login_attempts` table
> rather than per-process memory.

1. **Nothing has been registered in the new tables yet.** `storage_locations`,
   `material_locations` and `asset_units` are all empty, and stay empty until
   either the workbook columns are filled or somebody registers a tool in the
   app. The features are live; the data is not there.
2. **Two workbook data-quality items** the sync reports on every run:
   Consumption Log row 9 (SAP 1169, `"At site"`) has a Location but **no serial**,
   so no asset can be keyed from it; and `Tank No.` `J092` matches no equipment.
3. **GPS capture needs HTTPS.** Over plain HTTP the browser refuses a position —
   the move still saves, without coordinates. Fine on the hosted address and on
   the native apps; a caveat only for local HTTP testing.
4. **Three SAPs disagree with the workbook's Current Stock**, and two are
   impossible states worth investigating: `1137` (workbook 0, DB **−15**), `1358`
   (workbook 0, DB **−2**), `1190` (workbook 142, DB 134). Negative stock means
   more was issued than ever arrived.
5. **The Android APK's internal version is stamped by CI, not by a file.**
   `frontend/android/` is gitignored and regenerated on every build, so
   `release-android.yml` patches `versionName`/`versionCode` after
   `cap add android`. `versionCode` packs the semver positionally (1.2.0 → 10200)
   because Android requires a monotonically increasing integer.
6. **Hetzner deployment** — paused by decision. Runbook is ready.

---

## 7. Where to read next

| File | What it holds |
|---|---|
| [`PROJECT_HANDOVER.md`](PROJECT_HANDOVER.md) | **The authority.** All 14 locked rules with their evidence, the baselines, developer utilities, caveats |
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | The system brain — backend/frontend/DB/testing/security map |
| [`REPO_MAP.md`](REPO_MAP.md) | The `legacy/` ⇄ `tools/` ⇄ `data-archive/` segregation contract |
| [`OVERNIGHT_OPTIMIZATION_RUNLOG.md`](OVERNIGHT_OPTIMIZATION_RUNLOG.md) | Chatbot retrieval, idle logout, throttle, indexes, ⌘K |
| [`docs/EXPORTS_ROLES_SYSADMIN_RUNLOG.md`](docs/EXPORTS_ROLES_SYSADMIN_RUNLOG.md) | PDF/xlsx engines, the Auditor role, `bin/` scripts |
| [`docs/SME_ORDERED_SUBSET_RULE_RUNLOG.md`](docs/SME_ORDERED_SUBSET_RULE_RUNLOG.md) | The subset rule, end to end |
| [`docs/SME_TIER_SEGREGATION_RUNLOG.md`](docs/SME_TIER_SEGREGATION_RUNLOG.md) | Tier segregation, per tab |
| [`EXCEL_LOCATION_SYNC_RUNLOG.md`](EXCEL_LOCATION_SYNC_RUNLOG.md) | Rack + asset seeding, "app wins", and what to type in the spreadsheet |
| [`OVERNIGHT_ASSET_TRACKING_RUNLOG.md`](OVERNIGHT_ASSET_TRACKING_RUNLOG.md) | Asset schema, the GPS scanner, tank aliases |
| [`USER_MANUAL.md`](USER_MANUAL.md) | 21 chapters, user-facing. **Also the chatbot's corpus, so edit carefully.** Written for non-technical readers: no ASCII art, no shell commands, no source identifiers — keep it that way |
| [`docs/GI_Hub_Executive_Presentation.html`](docs/GI_Hub_Executive_Presentation.html) | 18-slide management deck. Open in a browser; ← → to navigate, `F` for fullscreen |

---

**Status: stable, fully documented, all gates green. Safe to restart.**
