# OVERNIGHT RUN LOG — asset tracking, rack locator, SME upgrades

> **Branch `feat/overnight-asset-and-sme-upgrades`**, six commits off `f44eac7`.
> Built 2026-08-04 → 2026-08-05 against the plan in
> [`PROPOSED_ARCHITECTURE_PLAN.md`](PROPOSED_ARCHITECTURE_PLAN.md) and the five
> operator rulings recorded there.
>
> **Every gate is green.** Backend service tests **1094 → 1193 / 0**, and the
> other seven are unchanged or higher. Nothing is half-finished.

---

## 0. The five rulings, and what each one became

| # | Your ruling | What was built |
|---|---|---|
| 1 | **Tank ambiguity: build the alias table + UI** | `sme_tank_alias` + auto-mapping + the resolve screen (§2) |
| 2 | **Do NOT overturn rule 1a. Log the draw, show it as a side note, let me assign equipment + SQM** | `sme_consumption_log` routing + the 🧾 Actual Consumption tab (§3) |
| 3 | **App wins on SQM** | `SQM_Override`, preserved through `--sme-reseed`, divergence reported (§4) |
| 4 | **Hide the recipe from UI/reports only** | Column + explanation string removed; `For_1_SQM` still on the wire (§7) |
| 5 | **Native GPS + Maps link, no Leaflet** | `lib/geolocation.ts`, zero new dependencies (§6) |

---

## 1. What shipped, by commit

| Commit | Phase | What |
|---|---|---|
| `c5ecf0f` | 1 | Surface-Shield routing · tank aliases · app-wins SQM · sync hardening |
| `4b200bc` | 5.1 + 5.4 | Auditor reads HOD/SME · `Total Procured` in session exports |
| `7b0b610` | 4 | Select All · header fit · hide the 1-SQM recipe |
| `14a085a` | 3 | Warehouse rack locator |
| `ed81854` | 2 | Asset tracking · serials · HTML5 GPS |
| `2bd66cb` | 5.2/5.3/5.5 | Shared login throttle · exec-summary email · dashboard widgets |

Five migrations, chained, each mirrored in `models.py` and each with a working
`downgrade()`:

```
e7c3b95a41d2  (was head)
  → b8d41f6a92c3  consumption.Item_Type + sme_tank_alias
  → c1a72e5b83d9  sme_equipment SQM_Override (+ _By, _At)
  → d5b83c17e604  storage_locations + material_locations
  → e9f2a4c68b71  asset_units + asset_movements
  → f3c81d5a97e2  login_attempts
  → a71e93b4c2f8  users.email                              ← single head
```

---

## 2. The tank aliases — and why nothing is guessed

**The finding that shaped this.** `Tank No.` in the Consumption Log is not
merely dirty, it is **ambiguous**. Measured over the 103 Surface-Shield rows:

```
TNK-091 39 · J0091 25 · J-0091 22 · J091 7 · J 0091 4
J092 2 · Sample Plate 1 · others 1
```

Four of those are one real tank. `alias_norm()` collapses them by upper-casing,
stripping separators, **and stripping leading zeros inside each run of digits** —
`J091 / J0091 / J-0091 / "J 0091"` all become `J91`, which matches the equipment
tag `J091`. That is **58 of the 103 rows** resolved automatically.

`TNK-091` is the one that cannot be. It suffix-matches **both**:

```
522-8J10-TNK-091   TRAIN J
522-8k10-TNK-091   TRAIN K
```

Two different vessels on two different trains, and **39 rows** — the largest
single bucket — ride on the choice. Either answer renders plausibly in every
report afterwards, which is exactly why it is not made automatically.

**`match_alias()` prefers an exact normalised match and only then tries a
suffix; a suffix that hits more than one tag is reported as ambiguous.** Aliases
that are places rather than vessels (`To site`, `House Keeping`, `In yard`,
`Scaffolding`) are pre-marked `ignored` so they never reach the queue.

### How you resolve one

**SME Estimator → 🧾 Actual Consumption → Tank aliases.** The table shows the
alias as typed, its matching form, how many rows it is holding, and **why** it
is unresolved (`no equipment tag matches`, or `2 equipment tags match —
ambiguous`).

**Resolve →** gives you two decisions:

* **"It is this equipment"** — pick the tag. Every logged row still `unassigned`
  and carrying that alias is tagged immediately, and the count is reported back
  ("39 logged row(s) tagged").
* **"Not an equipment"** — for a place or an activity. It leaves the queue and
  stops being asked about.

`/sme/actuals/aliases/{id}/candidates` returns the matcher's own candidates
**and** the full tag list, so you decide with the same evidence the sync had.
An alias can never be mapped to equipment that does not exist (422), and a
`mapped` alias with no tag is refused — a mapping that points nowhere is worse
than an unresolved one.

---

## 3. Surface-Shield consumption — beside the plan, never inside it

Live dry-run against the real workbook:

```
▶ ledger  (CNCEC_Inventory.xlsx)
      surface-shield  +58 SME log row(s)  (42 unassigned)  ·  8 tank alias/es
      ⚠ tank alias 'TNK-091': 39 row(s) held — matches 2 equipment tags
      ⚠ tank alias 'J092': 2 row(s) held — no equipment tag matches
      ⚠ 44 Surface-Shield row(s) are not estimator materials — ERP ledger only
```

### Rule 1a is preserved mechanically, not by promise

Your ruling was explicit: **do not reduce the estimator's availability.** Three
guards make that structural rather than aspirational:

1. **`sme_inventory_seed` is never written by this path.** Every SME quantity
   comes from that table, so nothing can move. Suite BF reads the whole SME
   payload, runs a routing write **and an assignment**, and requires the
   response to be **byte-identical** both times.
2. **A source-level check** asserts the routing code contains no
   `insert/update/delete(seed_t)`. It *reads* the seed — it has to know which
   materials the estimator models — and the test says so rather than pretending
   otherwise.
3. **Suite BA's grep was extended.** Its existing word-boundary check cannot
   catch `sme_consumption_log` (the name embeds `consumption`, and `_` is a word
   character), so that table now has its own assertion against both SME quantity
   queries.

### The side note

The 🧾 tab opens with a banner that says the estimator's availability is
deliberately **not** reduced by these figures. That wording is a safeguard, not
decoration: it is what stops someone later "fixing" the estimator to subtract
them. `/sme/actuals/summary` carries the same sentence in its payload.

### Assigning a logged draw — the UI you asked for

The workbook states a quantity and a `Tank No.`, but **never a system code and
never an area**. So rows land with `status='unassigned'` and you complete them:

1. **SME Estimator → 🧾 Actual Consumption → Actual draw.** Unassigned rows are
   badged and listed with their source (`Tank No. TNK-091 · SAP 1043 · from …`).
2. **Assign →** pick the **Equipment**, then the **Lining system code** — the
   code list is narrowed to the codes that tag actually carries, so an
   impossible pair is not offered (and is a 422 if forced).
3. Type the **SQM actually covered**. This is the number only a human has.
4. On save, `Expected_Qty = For_1_SQM × SQM_Completed` is computed from the
   recipe and the variance follows. Before that the row deliberately shows
   `—` rather than a variance measured against a guessed system code.

Variance is left **NULL** against a zero expectation rather than publishing a
divide-by-zero dressed up as a percentage.

---

## 4. The app wins on SQM

`Surface_Area_SQM` drives demand for every report, so an edit the next workbook
sync silently reverted would resurface days later as a wrong buy list with
nothing to point at.

**SME → 🗄️ Master Data → Equipment → `SQM` button.** The dialog states plainly
that this changes project demand, then saves the value **and** an override.
`--sme-reseed` deletes and rebuilds the site's rows; the override is snapshotted
before the delete and re-applied after, and the sync prints:

```
⚠ 3 equipment row(s) keep an operator SQM override (workbook differs):
    522-8J10-TNK-091 / 1  workbook 247 → override 251.5  (hod)
```

Storing the override as its own column is what makes the divergence *visible*.
Without it there is no way to tell a deliberate correction from a stale
workbook value, and "preserve edits" degenerates into "never sync again".
**Clear override** hands the row back to `Equipment.xlsx` — an override you
cannot undo is a trap.

---

## 5. The rack locator

Two tables, because a material legitimately sits in more than one place:
`storage_locations` (zone / rack / row / bin; `code` is the QR payload on the
shelf label) and `material_locations` (many-to-many, `is_primary` marks the one
to walk to first). A column on `inventory` was rejected — it is one row per SAP
and already carries a UNIQUE on `Material_Code`.

### Using it

* **⌘K** — type a material name or SAP code and the rack appears **on the hit**,
  before the quantity, because "where do I go" is the question being asked. Two
  requests share one debounce and one abort signal; `/stock/by-site` remains the
  authority on which materials you may see, so the locator can never widen a
  result set.
* **Locator page** (top-level nav, **minLevel 0** — the store keeper is the
  person who walks to the shelf):
  * **Find a material** — type or scan; shows every rack, primary first.
  * **Racks** — create, edit, retire.
  * **Scan a rack** — the reverse direction: what is *meant* to be on this
    shelf. It costs nothing extra and turns a stock count into a checklist.

### Four rules it keeps

| Rule | Why |
|---|---|
| Promoting a rack **demotes** the others | Two primaries means "where first" has two answers |
| An unlocated material returns `located: false`, never omitted | Silence reads as "we don't stock it" and sends someone to buy what is already on a shelf |
| Deleting a rack takes its assignments | An assignment pointing at a deleted rack renders as a blank shelf, which *looks* like an answer |
| No cache, no search engine | 452 inventory rows; suite BG runs `EXPLAIN ANALYZE` and requires the index. Rule 11 exists because four indexes were once added and measured as useless |

---

## 6. Asset tracking, serials, and the GPS scanner

### Why the workbook could not do this

Stated plainly because it changes what was built:

* `Consumption Log.Serial No.` — 101 of 1,110 rows, and the values are **batch
  numbers**: `3441` appears on **both** components of one CUMIFLOOR primer,
  `100160374` on garnet. Consumables have no unique serials.
* `Receipt Log.Serial No.` — 79 real values of 565.
* Every `Location` column is blank, single-valued, or a site name.

So the app owns asset identity. `asset_units` is **one row per physical thing**,
keyed `(Site_ID, SAP_Code, serial_no)` — the same lesson as rule 1: what
distinguishes two physical objects belongs in the key. **Assets only**: a row
exists where you make one, so consumables have none by absence, not by a flag.

### The two-hammers flow

**Assets page → Scan.** `/assets/resolve` answers three ways, and the order is
the design:

| Result | When | What you see |
|---|---|---|
| **unit** | the label carried a serial or asset tag | that exact hammer, opened |
| **choice** | the label carried only the SAP, and several units exist | *"Which one is in your hand?"* — the serials, each with its status and last known place |
| **material** | no registered units | falls back to an ordinary material search |

A serial is tried **before** a SAP because it is the more specific claim.
Silently picking the first unit is how the wrong hammer gets marked lost.

### Updating a location — two taps

**Update location** opens one form:

* **Rack** — any storage location from §5; or
* **…or describe the place** — free text, because not everything lives on a
  shelf ("Loaded on truck 4771", "with the subcontractor"). Forcing a rack code
  makes people leave the field blank instead.
* **Status** (`in_stock` / `issued` / `returned` / `lost` / `scrapped`) and
  **Held by**.
* **Capture coordinates** — a switch, on by default.

Save writes an append-only `asset_movements` row **and** the cached
`current_*` fields **in one transaction**, so the summary can never disagree
with its own history.

### How the GPS behaves — three normal failures, all handled

`navigator.geolocation` with `enableHighAccuracy`, an 8-second timeout, and a
**Google Maps link** for display. No Leaflet: the capture is the valuable part
and needs no library, while a tile library costs ~150 KB plus an external host
on every load of a PWA expected to work offline in a warehouse.

| Situation | What happens |
|---|---|
| **Page not on HTTPS** (a bare-IP LAN build) | Detected up front. A banner explains it and the switch is disabled — it would otherwise fail silently |
| **Permission declined** | *"Location permission was declined — the move was still recorded."* |
| **No fix** (indoors, steel building) | *"No position available here (common indoors) — the move was still recorded."* |

In every case **the location update still lands**, with coordinates absent.
Capture never gates the update: the update carries the operational value, the
coordinate is the bonus. Coordinates outside Earth are refused at the door
rather than stored and plotted later.

The unit view shows the coordinates, the accuracy (`±12 m`), an **open in Maps**
link, and the full movement timeline — each entry saying either its position or
*"no coordinates"*, never inheriting the previous one.

> ⚠️ **These coordinates are where an employee was standing.** They are visible
> to the roles that can already see the asset, the Auditor's write guard keeps
> them read-only, and the movement log is append-only so a position cannot be
> quietly rewritten. This is the first genuinely personal data the system
> stores.

---

## 7. SME UX — measured, not guessed

### Header fit

Diagnosed in the browser against the Total Overview master table (18 columns):

| | Before | After |
|---|---|---|
| Headers wrapping | **10 of 18** (one onto 3 lines) | **0** |
| Header row height | **83 px** over 40 px body rows | **39 px** |
| Header ↔ body drift | 0 px | 0 px |

The cause was **this repo's own `smartTable`**: it draws a sorter (22 px) and,
for categorical columns, a filter (22 px) *inside* the `<th>`. On `Code` — four
characters at a declared 70 px — that left ~20 px for the title, so a
four-letter word broke three ways. A word that short cannot wrap on its own,
so this was never a call-site width problem.

Fixed where the icons are added: the title is kept on one line, and a column
that **declares** a numeric width has it raised to fit that line plus the icons.
Only ever raised; auto-width columns untouched. **Body cells are not touched** —
rule 5 requires material names to keep wrapping, since truncation once ate the
character distinguishing `CUMICRETE PU MF 300 (1MM) C` from its three siblings.

### Select All

One shared `MultiSelectAll` across all seven SME multi-selects. "Select all"
resolves to the **current option list**, not a sentinel token, so the value
stays a plain array and every consumer keeps working with nothing to unwrap.

### The 1-SQM recipe is gone from the UI

Two places showed it, and both are fixed:

* the Smart Calculator's **`Per SQM`** column;
* the server explanation, which spelled the formula out into every export:
  `"2.5 KG/SQM × 40 SQM = 100 KG"` → **`"100 KG for 40 SQM"`**.

It also leaked into **every session document**: the Equipment Breakdown derived
its columns from dict key order, so `For_1_SQM` plus five engine internals
(`Material_Key`, `Pool_Before/After`, `Pending_Pool_Before/After`) rode along.
An explicit `_SESSION_LINE_COLS` now governs the session report, location report
and execution plan.

⚠️ **`For_1_SQM` remains in `/sme/snapshot`** — per your ruling. The TypeScript
engine computes demand in the browser and cannot work without it.

---

## 8. The rest

**Ordered Quantity in the session exports.** `Total_Procured_Qty` was **missing
from `WeightedProcurementRow`** — the drill-down would have rendered a blank
cell. Added, accumulated from the **split tiers** exactly as
`sme._material_demand_rows` does, so the document and the screen cannot drift.
That sum is *not* the additive error rule 1c overturned: `Alloc_Pending` is
already `max(ordered − available, 0)`, so tier1 + tier2 is the **ceiling**.
`ui-math §F` pins it — 40 arrived of 100 procured reads **40 / 60 / 100**, never
140, conserves against `Allocated_Qty`, and leaves readiness at tier-1 40%.

**Auditor visibility.** The API was never the blocker: `/hod/*` and `/sme/*` are
`require_level(2)` and an auditor is level 3. One correction to the plan, caught
by a new suite BD check rather than in review — `/hod/executive-summary` carried
its own `require_roles("hod")`, so opening the nav alone would have produced a
menu item that 403s. Those three GET-only endpoints now include `auditor`.
**`readonly.py` is untouched and nothing was added to its allowlist.**

**Shared login throttle.** `login_attempts` in Postgres (not Redis — the counter
ticks a few times a minute). Still **throttles, never locks**: the window rolls
forward on its own, a correct password deletes the row, and no administrator is
ever in the recovery path. If its own storage fails, sign-in still works.

**Exec-summary email.** A missing *channel*, not a missing cron — the Friday
17:00 job, the PDF and the 72-hour token already existed. `users.email` was
added (nullable, not unique; a departmental mailbox is legitimately shared) with
a fallback to the configured inbox so it delivers with no data entry. The email
carries the **expiring link, not the PDF bytes** — an attachment lives forever
in whatever inbox it reaches.

**Dashboard widgets.** Top 5 Expiring (includes already-expired lots — FEFO
stays allow-and-log, so this warns rather than blocks) and Highest Value, which
ships its own **coverage** because `Unit_Cost` defaults to 0.

---

## 9. Gates — before and after

| Gate | Was | Now |
|---|---|---|
| Backend service tests | 1094 / 0 | **1193 / 0** (suites BF · BG · BH · BI) |
| Playwright E2E | 57 / 57 | **57 / 57** |
| SME UI math | 27 / 0 | **33 / 0** (§F) |
| SME TS↔PY parity | 1,313 | **1,313** (no engine change) |
| Legacy regression | 599 / 0 | **599 / 0** |
| Frontend build | clean | **clean** |
| Alembic | `e7c3b95a41d2` | single head **`a71e93b4c2f8`** |
| `gi_database.db` | `00652932…ba038` | **unchanged** |

Run after every phase, not only at the end.

`legacy/bug_check.py`'s models-parity allowlist gained the five new-stack-only
columns (`consumption.Item_Type`, the three `sme_equipment.SQM_Override*`,
`users.email`) — the documented pattern, same as the 2026-07-18 SAP overhaul.

**No engine change was made in any phase.** `sme_engine.py`, `engine.ts` and the
parity golden are untouched, which is why parity is identical rather than
regenerated.

---

## 10. What is waiting for you

1. **`TNK-091` — TRAIN J or TRAIN K?** 39 rows are held. SME → 🧾 Actual
   Consumption → Tank aliases → Resolve. `J092` (2 rows) matches no equipment
   at all and may be a tank that does not exist in `Equipment.xlsx` yet.
2. **42 unassigned Surface-Shield rows** await equipment + SQM once the aliases
   are resolved.
3. **The racks are empty.** `storage_locations` starts with no rows — the
   locator has nothing to find until the warehouse is mapped. Locator → Racks →
   Add rack.
4. **No asset units are registered.** The 79 real serials in the Receipt Log are
   a plausible seed, but importing them blind was not done deliberately; they
   are worth an operator's eye first.
5. **`users.email` is empty**, so the weekly email currently goes to the
   configured inbox for every recipient. Filling addresses in makes it per-person.
6. **GPS needs HTTPS.** Production (`gi.giinventory.com`) and `localhost` both
   qualify; a bare-IP LAN build will show the banner and disable capture.
7. **Hetzner deployment** — still paused by decision, unchanged by this work.
