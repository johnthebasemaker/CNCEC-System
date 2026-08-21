# PROPOSED PHASE 8 PLAN — Unification, Uniqueness, Quality Oversight, and an Assistant That Answers

**Status:** proposal. No application code written.
**Date:** 2026-08-20
**Baseline:** `main` @ `ff1db9a` (Phase 7 merged) · alembic head `e5b2d7c94a16` ·
gates 1667/0 · E2E 90/90 · legacy 599/0 · SME parity 1,313 · UI math 33/0 · nav 47

---

## How to read this

Section 0 is the part to read first. Reading the Phase 7 code against the live
database turned up **five defects that already exist in shipped code or in the
data** — four of them silently wrong numbers in the planner you just approved.
They are not new work I am inventing; they change what Track 1 has to be, and
one of them changes what you believe about your workbook sync. Everything after
Section 0 is the six tracks as you specified them.

Tracks are then broken down as: **schema → API → frontend → tests → risk**, and
each closes with the specific decisions I need from you. All open questions are
collected and numbered in Section 8 so you can answer in one pass.

---

## Answers and status — updated 2026-08-20

All twelve questions answered. **Q7 changed the design** and §3.4 / §7.1 / §8
have been rewritten to match; the rest confirmed the recommendations.

| Q | Ruling |
|---|---|
| Q1 | LSC10 splits **proportionally** on the tag's LSC8 : LSC9 area ratio |
| Q2 | The proposed blasting mapping, splitting the areas rather than picking one |
| Q3 | Report orphans in the dry run; **delete the `Blasting Civil PU Area` row** in 8a |
| Q4 | Backend assembles the job label. ⚠️ `Lining_System` in `For_1_SQM.xlsx` has been re-edited — the labeler reads **that column**, not `Lining_System_Name` (which holds the short code, `RLCB4`) |
| Q5 | `qc_hod` at **level 2** with a cross-site exemption, **scoped to Surface Shield items only** — no PPE, tools or other categories |
| Q6 | Default to 2 shifts when night workers exist, **and always allow the HOD to force a 2-shift plan** even when none do |
| Q7 | ❗**CORRECTION — a PR may carry SEVERAL POs** (partial fulfilment). No `uq_po_per_pr`. Uniqueness is `PR_Number` in the registry and `PO_Number` in `purchase_orders`, nothing more |
| Q8 | Cache the cascade ~60 s |
| Q9 | 90 days stagnant / 60 days to expiry |
| Q10 | Delete the stale `docs/USER_MANUAL.md` |
| Q11 | Instrument first, report numbers, then change |
| Q12 | Escalations must name a specific site or warehouse |

**Slice progress**

| Slice | Branch | State |
|---|---|---|
| **8a** | `feat/phase8-planner-math` | ✅ **merged** — see §0.1–0.3. Gates 1,698 / 0 · alembic head `d4b8c1e63a27` |
| **8b** | `feat/phase8-planner-ux` | ✅ **shipped** — dedup (Q13), job label, CV/ME, Target Days, multi-select, per-role dashboard. Gates 1,725 / 0 · E2E 96. No migration |
| 8c | `feat/phase8-procurement-lock` | awaiting green light |
| 8d–8f | — | queued |

**Q13 — ANSWERED 2026-08-21.** A surface carrying two stacked systems is
blasted **ONCE**. The prep area is deduplicated on an exact (location, area)
match; J027 is 4,555 m², not 5,059. Applied in 8b; the gross is still published
beside it so a plan can be reconciled against one printed before the ruling.

---

# 0. What I found while reading (read this first)

## 0.1 🔴 The planner double-counts every benchmark that exists twice under one sub-activity

This is live, in `backend/api/services/planner.py`, and it produces confidently
wrong numbers today.

`_applicable_norms()` returns **every** `sme_manpower_norm` row for a system code
and `plan()` sums their man-hours. The comment justifying it is right about the
case it names — primer AND screed AND buffing genuinely add — but it never
distinguishes *sequential sub-activities* (which add) from *alternative
benchmarks for one sub-activity* (which compete). The database has both.

Measured against the live mirror:

| Code | Rows | Why they exist | Correct m-h/m² | What the planner returns | Error |
|---|---|---|---|---|---|
| `LSC4` / `ESC41` | 2 (CV + ME) | Same brick lining, filed once for civil and once for mechanical. **Identical crew, identical productivity.** | 6.5987 | 13.1974 | **2.00×** |
| `LSC5` / `ESC51` | 2 (CV + ME) | Same, 63 mm | 8.0427 | 16.0855 | **2.00×** |
| `LSC10` / `ESC101` | 2 (both CV) | One seal-coat code serving the 4 mm system (70 m²/shift) and the 6 mm system (90 m²/shift) | 1.4667 *or* 1.8857 | 3.3524 | **2.29×** |

The role split is doubled with it: LSC4's crew is `MASON 4 / HELPER 3 /
MORTAR_MIXER 2 / BRICK_CUTTER 1` filed twice, so the gap analysis asks you to
hire two crews for one job.

**The fix needs no operator input for CV/ME, because the data already answers
it.** `sme_equipment` carries `Type` per (tag, system) row, and it lines up
exactly with the benchmark that should be used:

```
LSC4 equipment → all CV  (1 row,  CONCRETE SUBSTRATE)
LSC5 equipment → all ME  (12 rows, TANK / VESSEL)
```

So the rule is: **pick the norm whose `Type` matches the equipment's `Type` for
that (tag, code); do not sum across types.** LSC10 is the one that still needs a
ruling — see Q1.

## 0.2 🔴 Surface prep sums all five blasting benchmarks

Same root cause, larger blast radius. For surface prep (`code = ''`),
`_applicable_norms()` returns every system-agnostic norm — currently four `ESC1`
rows and one `ESC2` row — and sums them:

```
ESC1  Blasting Civil Floor & Wall   0.1467 m-h/m²
ESC1  Blasting Civil PU Area        0.8250      ← see 0.3, this row should not exist
ESC1  Blasting Civil PU 4mm Area    0.8250
ESC1  Blasting Civil PU 6mm Area    1.1000
ESC2  Blasting Steel Surface        0.8000
                                    ───────
planner uses                        3.6967 m-h/m²
```

A plain concrete floor should cost **0.1467**. The planner charges **3.6967** —
**25× too high** — and bills a steel tank for four kinds of civil blasting it
will never receive. Every surface-prep plan produced since Phase 7 shipped is
wrong by roughly this factor.

Blasting cannot be disambiguated by `Type` alone (all four CV rows are CV), so
this one needs a ruling. My recommendation is in Q2.

## 0.3 🟠 Your "26 unchanged, 0 rejections" sync is hiding an orphan row

The workbook `Manpower_Hour_Details.xlsx` has **26** benchmark rows.
`sme_manpower_norm` has **27**.

The extra row is `CV / ESC1 / ESC1 / Blasting Civil PU Area` — the row you
renamed to `Blasting Civil PU 4mm Area`. `Activity` is part of the five-part
identity, so a **rename is an insert, not an update**: the new row was created,
the old one was left behind, and because the importer has no delete pass, every
workbook row matched and nothing was rejected. The sync reported success
truthfully; the report just does not cover rows that vanish from the workbook.

Two consequences, and the second is the one that matters long-term:

- **Today:** the orphan contributes a phantom 0.825 m-h/m² to every surface-prep
  plan (it is one of the five rows in 0.2).
- **Structurally:** any future rename of `Activity`, `Type`, `Lining_System_Code`,
  `Execution_Sub_Activity_Code` or `Variant_Key` will silently fork a row the
  same way, and the sync will keep saying "0 rejections".

Phase 8 should add an **orphan report** to `plan_sme_manpower_norms` — norms in
the database that no workbook row claims — surfaced in the dry run, never
auto-deleted. See Q3 for whether you want deletion offered at all.

## 0.4 🟠 `USER_MANUAL.md` is three phases behind — this *is* the AI bug

You reported the assistant giving outdated answers. The retrieval pipeline is
not the primary cause; the corpus is.

```
USER_MANUAL.md        last modified 2026-08-13
Phase 5 / 6 / 7       shipped        2026-08-18 → 2026-08-20
```

§19 "Man-Hours & Labor Tracking Manual" still documents **5 tabs**. The page has
**11** (and it was already one behind before Phase 7 — Scorecard shipped in
2026-07 and was never written up). It documents worker type as **"OWN / Supply"** — renamed to GI / NON_GI
in Phase 4. It states **"Normal hours are the Total capped at 8"** — which since
Phase 4 is an HOD-configurable threshold, 8 for GI and 10 for Non-GI. It has no
mention of the Execution workflow, the four report tabs, `Shift` (Day/Night), or
the Planner.

The assistant is answering correctly *from a manual that describes the system as
it was a week ago*. Track 6's documentation work is therefore a **prerequisite
for**, not a companion to, the prompt tuning.

### The "HODs can't access the Manpower portal" answer, specifically

I found the exact mechanism. §2 contains two passages that disagree in
isolation:

- §2.1 prose: *"…Material Estimator and Man-Hours pages are locked to their own
  role exactly — Logistics outranks a Head of Department but still cannot open
  the HOD Portal."*
- §2.2 table: the `🕒 Man-Hours` row shows **✅ for HOD**.

`manual_index.chunk_chapter()` splits at `##` boundaries, so **2.1 and 2.2 are
separate chunks** and only one may be retrieved. When 2.1 wins, the model reads
"Man-Hours is locked to its own role" with no table to say which role that is,
and infers exclusion.

The fallback path is worse: `_context_for_role()` head-truncates non-admin
chapters at `_PER_SECTION_CHAR_CAP = 800`, and 800 characters into §2 lands
*inside the 2.1 hierarchy table* — before the access matrix has started. So on
the fallback path the matrix is **never visible to any non-admin role at all**.

Both are fixable and both are in Track 6.

## 0.5 🟡 `_next_pr_number()` can mint the same PR number twice

Relevant to Track 3, minor today, structural.

```python
last = SELECT PR_Number ... WHERE PR_Number LIKE 'PR-20260820-%' ORDER BY id DESC LIMIT 1
nxt  = int(last.split('-')[-1]) + 1
```

Read-then-write with no lock and no unique constraint (`pr_master.PR_Number` is
`nullable=False` only — many rows share one PR number by design). Two HODs
creating a PR in the same second at different sites both read `0003` and both
write `0004`, producing two different PRs that are one PR to every downstream
query. Track 3 closes this properly.

---

# 1. TRACK 1 — Manpower Planner: display, CV/ME, multi-select, target days

## 1.1 The correctness work comes first

Sections 0.1–0.3 must be fixed before any display work, or Phase 8 ships a
better-looking wrong answer. Concretely, `_applicable_norms()` is replaced by a
**selection** step and a **summation** step that are visibly different
operations:

```
1. gather   → every norm row for this system code
2. select   → within each Execution_Sub_Activity_Code group, choose ONE
              (by equipment Type; then by the Q1/Q2 discriminator)
3. sum      → across DISTINCT sub-activity groups only
```

Step 2 is the new one, and it must **report** what it chose and what it
discarded. A planner that silently picks between two benchmarks that differ 2×
is not better than one that sums them; the output will carry a
`Benchmark_Selection` block naming the chosen row, the rejected rows, and the
reason.

Where selection is ambiguous the plan **warns and picks the higher-cost
benchmark** rather than guessing cheap. An overstated labour requirement gets
noticed at the daily meeting; an understated one gets noticed when the deadline
is missed.

## 1.2 Shifts and Days — the arithmetic

You asked for Total Shifts Needed and Total Days (Day + Night) Needed. Two of
these three quantities are unambiguous and one is not, so I want to name them
separately rather than ship one number that means different things per column.

**Crew-shifts (workload, deployment-independent).** How many benchmark-crew
shifts of work exist. This is what "shifts needed" means in the workbook's own
terms:

```
crew_shifts(activity) = remaining_sqm / Standard_Productivity_Per_Shift
                      = required_manhours / Manhours_Per_Shift        ← identical
```

Those two forms are algebraically the same because
`manhours_per_sqm = Manhours_Per_Shift / Standard_Productivity_Per_Shift`. That
is a free self-check and the test suite will assert it.

**Calendar shifts (elapsed, deployment-dependent).** How many shifts of wall
clock it actually takes, given the headcount you deploy:

```
calendar_shifts = required_manhours / (deployed_headcount x SHIFT_WORKED_HOURS)
```

These coincide only when you deploy exactly the benchmark crew. They are
different questions and the UI will label them as such.

**Days.**

```
days = calendar_shifts / shifts_per_day        shifts_per_day ∈ {1, 2}
```

`shifts_per_day = 2` means **two disjoint crews**, Day and Night. It does *not*
double any individual's hours — nobody works both. The planner will default
`shifts_per_day` from the roster (2 if any active employee in a required role is
on `Shift = 'Night'`, else 1) and let the HOD override, because "we could run
nights" is a decision, not a fact about the roster.

## 1.3 Target Days — the reverse calculation

This is the clean part, and it needs no new model. A person works **one** shift
per day, so over `D` days each person offers `D × 11` hours regardless of how
many shifts per day the site runs. Therefore:

```
deadline_hours = target_days x SHIFT_WORKED_HOURS
```

and every quantity the Phase 7 planner already computes follows unchanged:

```
total_headcount   = required_manhours / (target_days x 11)
per-shift crew    = total_headcount / shifts_per_day
normal capacity   = (n_GI x 8 + n_NONGI x 10) x target_days
overtime incurred = max(0, required_manhours - normal_capacity)     (capped at OT capacity)
```

Note that `shifts` in the shipped model is `deadline_hours / 11`, which under
this substitution is exactly `target_days` — the existing overtime arithmetic
becomes "per day" without a single formula changing. That is a strong signal the
Phase 7 model was the right shape.

**Worked example** (to be pinned in the test suite and printed in
`MANUAL_TESTING_GUIDE.md`):

```
600 m² of LSC4 remaining, brick lining, benchmark 6.5987 m-h/m²
required manhours  = 600 x 6.5987            = 3,959 m-h
crew-shifts        = 600 / 16.67             = 36.0 crew-shifts
target_days        = 5   →  deadline_hours   = 55 h per person
total headcount    = 3,959 / 55              = 72.0 people
  MASON      4/10 of crew → 28.8 → 29
  HELPER     3/10          → 21.6 → 22
  MORTAR_MIXER 2/10        → 14.4 → 15
  BRICK_CUTTER 1/10        →  7.2 →  8
normal capacity (all GI)   = 72 x 8 x 5      = 2,880 m-h
overtime                   = 3,959 - 2,880   = 1,079 m-h
to clear with Non-GI       = ceil(1079 / (10 x 5)) = 22 workers
```

Every one of those numbers is checkable on paper, which is the point.

## 1.4 Job display detail

"Show the full context" resolves to a join the planner does not currently make.
`sme_manpower_norm` already carries `Activity` and `Sub_Activity`;
`Lining_System_Name` lives on `sme_recipe`; `Type` lives on both
`sme_equipment` (per tag+code) and `sme_manpower_norm` (per benchmark).

Proposed canonical job label, used identically in the planner, the execution
queue, the report tabs and the SME hand-off:

```
J027 · LSC4 [CV] — Carbon Brick Lining
       ESC41 · Carbon Brick lining 30mm → Brick Lining
```

I want to build this **once** as a shared formatter rather than in each page,
for the same reason `syscode_sort_key` is one function: four independent
implementations will drift and then disagree in an export.

- Backend: a `job_label()` helper in `services/planner.py` (or a new
  `services/jobs.py` if Track 4 also needs it — likely).
- Frontend: `frontend/src/sme/jobLabel.ts`, mirroring it.

**This is a third dual-implementation surface** (after the SME engine and the
sort key). I would rather it be one-sided: the backend returns the assembled
`Job_Label` plus its parts, and the frontend renders what it is given. See Q4.

## 1.5 The global CV/ME tag — and where it is genuinely ambiguous

`Type` is a property of an **(equipment, system code)** row, not of a system
code. The live data proves it:

```
LSC1 → CV on 9 CONCRETE SUBSTRATE rows   AND   ME on 19 TANK / VESSEL rows
```

So a chip reading `LSC1 [CV]` on a screen that aggregates across equipment is
false. The rule I will apply everywhere:

- Per-equipment context (planner rows, execution entries, session lines,
  equipment report): `CODE [Type]` from that row. Unambiguous.
- Aggregated-by-code context (system-code report, code filters, KPI rollups):
  `CODE [CV/ME]` when the code spans both, `CODE [CV]` when it does not.
  Never silently pick one.

`_overview_rows()` in `sme.py` already emits `Type` per row, and
`/sme/model-snapshot` already ships it, so **no new query is needed** — this is
rendering, and a small number of exports that need a new column.

## 1.6 Multi-select

Reuse `frontend/src/sme/MultiSelectAll.tsx` unchanged — it already resolves
"Select all" to the real value list rather than a sentinel, which is exactly
what the planner needs.

Two aggregation traps to get right in the backend:

- **Do not take the cross product.** Selecting tags `{A,B}` and codes `{X,Y}`
  is not four jobs. It is the *intersection with reality*: the (tag, code)
  pairs that actually exist in `sme_sqm_progress`. Building the cross product
  invents work on pairs that were never planned.
- **Surface prep is per-tag, not per-(tag, code).** A selection mixing lining
  codes and surface prep for the same tag must count the prep contribution
  **once**, or a tag with six lining codes gets billed six blastings.

The multi-job plan sums man-hours and role hours across jobs, shares one
deadline, and computes one gap and one overtime strategy against one roster.
Per-job rows stay visible in a collapsible breakdown so the total can be
decomposed.

## 1.7 Schema

**None.** Track 1 is entirely computation and presentation over tables that
already exist. That is worth stating explicitly: the least risky track.

## 1.8 API

| Route | Change |
|---|---|
| `POST /mh/planner` | `equipment_tag: str` → `jobs: list[{equipment_tag, lining_system_code}]`; add `target_days`, `shifts_per_day`. Keep the singular fields accepted for one release so nothing breaks mid-deploy. |
| `POST /mh/planner` (response) | add `Total_Crew_Shifts`, `Total_Calendar_Shifts`, `Total_Days`, `shifts_per_day`, `Benchmark_Selection`, per-role `Assign_Per_Shift` |
| `GET /mh/planner/targets` | add `Type`, `System_Name`, `Job_Label`; return surface prep with its resolved blasting variant so the UI can show what it will cost |

`deadline_hours` and `target_days` are mutually exclusive; supplying both is a
422 rather than a silent precedence rule.

## 1.9 Frontend

`ManpowerPlanner.tsx` grows from one job to many:

1. **Selection** — two `MultiSelectAll`s (equipment, system codes) + a
   surface-prep toggle + `Target Days` / `Hours per person` (one or the other,
   radio-selected so the exclusivity is visible).
2. **Workload & required hours** — KPI row extended to Man-hours · Crew-shifts ·
   Calendar-shifts · Days, then the per-activity table with the full job label.
3. **Roster vs Gap** — the per-role table becomes a **collapsible per-role
   panel**: header shows `Total Needed / What We Have / To Assign`, expanding
   shows the GI-Non-GI-Day-Night split and which jobs drove the requirement.
4. **Overtime strategy** — unchanged apart from reading days rather than hours.

## 1.10 Tests (suite CE)

- `crew_shifts == manhours / Manhours_Per_Shift` for every norm (the identity
  from 1.2)
- LSC4 selects one Type, not two — asserted against 6.5987, and asserted **not**
  13.1974 by name, so a regression names itself
- Surface prep on a concrete tag charges Floor & Wall only
- Cross-product guard: tags `{A,B}` × codes `{X,Y}` where only 3 pairs exist
  yields 3 jobs
- Surface prep counted once for a tag carrying 6 codes
- `target_days = 5` and `deadline_hours = 55` produce byte-identical output
- The 3,959 m-h worked example, hand-checkable
- `shifts_per_day = 2` halves days and does not change total headcount

---

# 2. TRACK 2 — The QC-HOD (Head of Qualities)

## 2.1 The hard problem is the role level, not the dashboard

You want **cross-site visibility**. In this codebase cross-site is
`site_scope(user) is None`, which is `level >= SITE_SCOPE_MIN_LEVEL` — **3**.

Putting `qc_hod` at level 3 grants it, for free, every endpoint gated by
`require_level(0|1|2|3)` — **97 routes**, including Entry Log reads, SME,
Records, Reports, Burn Rate, and the HOD's own queues. That is not a Head of
Qualities. It is a second Logistics account with a quality-themed sidebar.

`auth.py` has a comment about exactly this trap, written when `qc` was added:

> *"the moment 'qc' appeared in ROLE_META it would have inherited GLOBAL
> warehouse visibility … Adding a role to ROLE_META and forgetting this file is
> the mistake this comment exists to prevent."*

**Recommendation: `qc_hod` at level 2, with an explicit cross-site exemption.**

```python
QC_OVERSIGHT_ROLES = {"qc_hod"}          # cross-site on the quality axis only

def site_scope(user):
    if user.get("role") in QC_OVERSIGHT_ROLES:
        return None                      # unrestricted READS
    ...
```

Level 2 keeps it out of level-3 territory; the exemption gives cross-site reads;
and — critically — **every QC-HOD route uses `require_roles("qc_hod")`, never
`require_level`**, so its surface is enumerable rather than inherited. This is
the pattern `qc` already established and it has held.

I want your explicit sign-off on this because it is the one Phase 8 decision
that is expensive to reverse (Q5).

## 2.2 Write access and the read-only guard

The QC-HOD's listed capabilities are *view*, *track*, *receive notifications*,
and *contact people*. Only the last writes anything, and it writes
notifications, not ledgers. So:

- The ASGI read-only middleware (`readonly.py`) should treat `qc_hod` as
  read-only **with a narrow allowlist**: its own comms routes and nothing else.
- It cannot approve inspections (that is `qc`), cannot decide DNs, cannot touch
  stock, cannot raise a PR.

This makes the role safe to hand out and makes the audit story simple: every
QC-HOD action is either a read or a message.

## 2.3 Schema

Two new tables, plus one column.

```sql
-- 1. The comms log. "Demand an MTC" has to be a record, not a fired-and-forgotten
--    notification, or nobody can answer "how long has this been chased?"
CREATE TABLE qc_escalations (
    id                SERIAL PRIMARY KEY,
    raised_by         TEXT NOT NULL,
    target_role       TEXT NOT NULL,          -- qc | warehouse_user | logistics
    target_site       TEXT,                   -- exactly one of site/warehouse
    target_warehouse  TEXT,
    kind              TEXT NOT NULL,          -- mtc_demand | inspection_request | transfer_suggestion
    SAP_Code          TEXT,
    Material_Code     TEXT,
    Lot_Number        TEXT,
    PO_Number         TEXT,
    message           TEXT NOT NULL,
    status            TEXT NOT NULL DEFAULT 'open',   -- open | resolved | withdrawn
    resolved_by       TEXT,
    resolved_at       TIMESTAMP,
    resolution_note   TEXT,
    notification_id   INTEGER,                -- app_notifications.id actually sent
    created_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX ix_qc_escalations_open ON qc_escalations (status, kind, created_at);

-- 2. Stagnation thresholds, per category, HOD-configurable.
--    Hard-coding "90 days" would make your policy a code change.
CREATE TABLE qc_stagnation_rules (
    id             SERIAL PRIMARY KEY,
    Category       TEXT NOT NULL,
    stagnant_days  INTEGER NOT NULL DEFAULT 90,
    expiry_warn_days INTEGER NOT NULL DEFAULT 60,
    status         TEXT NOT NULL DEFAULT 'active',
    updated_by     TEXT,
    updated_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (Category)
);
```

`users` needs no change — `qc_hod` is a `role` value, and the role carries no
site, exactly like `logistics` and `auditor`.

## 2.4 Stagnation and expiry — what the query actually is

`lots` already carries `Received_Date`, `Expiry_Date`, `Site_ID`, `SAP_Code` and
the `v_lot_balance` view already computes `Remaining_Qty`. So stagnation is a
join, not new bookkeeping:

```
stagnant lot  ⟺  Remaining_Qty > 0
              AND inventory.Category = <controlled category>
              AND days_since(last consumption of this lot) > stagnant_days
              AND (Expiry_Date IS NULL OR Expiry_Date > today)
```

"Last consumption" comes from `consumption` filtered to the lot; a lot with
**no** consumption at all measures from `Received_Date`. That distinction
matters — "received and never touched" and "used until March then abandoned" are
different problems, and the dashboard will show which one each row is.

The redistribution suggestion ("push them to other sites") ranks candidate
target sites by open demand for the same SAP — which the SME demand matrix and
`pr_master` already know. It is a **suggestion with a Contact button**, never an
automatic transfer.

## 2.5 The notification trap — and why the existing daily sweep will not reach a QC-HOD

`dispatch_missing_mtc()` in `health_monitor.py` already does the daily
uncertified-Surface-Shield sweep you described, grouped one alert per place,
fired from `briefing_loop()`. So the *mechanism* exists.

But it will not reach a cross-site QC-HOD. The visibility rule is:

```
recipient_role = role
AND (recipient_site      IS NULL OR recipient_site      = site)
AND (recipient_warehouse IS NULL OR recipient_warehouse = warehouse)
```

The daily alert sets `recipient_site = 'SITE-A'`. A QC-HOD carries `site_id =
''`. `'SITE-A' != ''`, so **the row is invisible to them** — the bell would show
nothing while the condition it describes is live at six sites.

The fix is *not* to relax the visibility rule (that would leak every site-scoped
notification to every unscoped role). It is a **second, unscoped, aggregated
dispatch**: one daily message to `qc_hod` with `recipient_site = NULL`,
summarising every place at once — which is the right shape for an oversight role
anyway. A per-site fan-out to someone responsible for all sites is six messages
saying one thing.

## 2.6 API — `backend/api/qc_hod.py`

```
GET  /qc-hod/overview            KPI: uncertified SAPs · sites affected · stagnant lots
                                 · expiring ≤N days · open escalations
GET  /qc-hod/surface-shield/pos  Surface Shield PO lines + MTC presence per line
GET  /qc-hod/mtc                 MTC register, cross-site, filterable
GET  /qc-hod/missing-mtc         The daily sweep's rows, on demand
GET  /qc-hod/usage               Which sites are consuming which controlled materials
GET  /qc-hod/stagnation          Stagnant + expiring lots with a redistribution suggestion
GET  /qc-hod/escalations         The comms log
POST /qc-hod/escalations         Raise one → writes the row AND dispatches
POST /qc-hod/escalations/{id}/resolve
GET  /qc-hod/settings  PUT       Stagnation thresholds
GET  /qc-hod/export/{key}        csv | xlsx | pdf  (rule 12 via reports.to_csv/to_xlsx)
```

All `require_roles("qc_hod")` — plus `admin`, which reaches everything.

## 2.7 Frontend

One page, `QcHodPage.tsx`, tabs: **Overview · Surface Shield POs · MTC Register ·
Where It's Being Used · Stagnation & Expiry · Escalations · Settings**.

Nav: a new `quality-oversight` group in `config/nav.tsx`, `anyRole: ['qc_hod']`,
`writes: false`.

## 2.8 The role-registration checklist

Adding a role touches more files than is obvious, and `auth.py` says so in a
comment. Every one of these is mandatory in the same PR:

| File | What |
|---|---|
| `auth.py` `ROLE_META` | label + level 2 |
| `auth.py` `site_scope` | the `QC_OVERSIGHT_ROLES` exemption |
| `auth.py` `warehouse_scope` | must return `None` (cross-warehouse reads) — the exact line the `qc` comment warns about |
| `auth.py` `qc_scope` | `{site: None, warehouse: None}` |
| `auth.py` registration sets | `_UNSCOPED_REG_ROLES` — admin-created only, never self-registered |
| `readonly.py` | read-only + comms allowlist |
| `ai/manual_qa.py` `_ROLE_ALLOWED` | its chapters (**the QSEP release forgot this and the code comment says so**) |
| `ai/manual_qa.py` `_ROLE_LABEL`, `_ROLE_REFUSAL` | its own phrasing |
| `config/nav.tsx` | sidebar + route guard |
| `auth/readOnly.ts` | client-side mirror |
| `USER_MANUAL.md` §2 | the access matrix row |
| suite BU (strict-RBAC) | negative access for every other role |

## 2.9 Risk

The dangerous failure here is **silent over-permission**: a role that reads more
than intended looks like it is working. Suite BU must assert the QC-HOD gets
**403** on a named list of routes it must never reach (Entry Log, HOD approvals,
Logistics PO creation, Admin, SME writes), not merely that its own routes work.

---

# 3. TRACK 3 — PR & PO uniqueness and idempotency

## 3.1 What is already correct

`assign_po()` was hardened on 2026-08-13 and is the model to follow: it refuses a
second assignment, treats a repeat of the *same* warehouse as idempotent success,
and its comment states why re-assignment is refused rather than replaced. Track 3
extends that discipline; it does not invent it.

## 3.2 What is not

| Gap | Consequence |
|---|---|
| `pr_master.PR_Number` has no uniqueness of any kind, and `_next_pr_number()` is read-then-write | Two PRs can share one number (0.5) |
| `submit_pr()` accepts `logistics_status IN ('site_draft','submitted')` | Re-submitting an already-submitted PR fires a second Logistics notification |
| `create_po_from_pr()` checks only that the **PO number** is free | Nothing stops a second PO against the same submitted PR under a different number |
| `create_po_manual()` accepts an unlisted PR by design | The manual lane can duplicate a PR the automatic lane already consumed |
| Buttons | Assign is hidden post-action; Submit and Create PO are not |

The third row is the serious one: **PO number uniqueness is not PR uniqueness.**
`PO-1001` and `PO-1002` can both be raised against `PR-20260820-0001` today, and
the second `UPDATE ... SET logistics_status='in_po'` matches zero rows and passes
silently, because the first already moved them.

## 3.3 The model: one state machine, enforced in the database

Rather than adding guards one at a time, I propose the Phase 5 approach that
worked — **transitions as data**, one definition, and a DB constraint that makes
the illegal state unrepresentable.

```
PR:  site_draft ──submit──> submitted ──po_raised──> in_po ──> closed
                     │                                 │
                     └────── force_closed <────────────┘

PO:  open ──assign──> assigned ──acknowledge──> receiving ──> closed
              │                                                │
              └────────────── force_closed / cancelled ────────┘
```

Every transition function: read current state, refuse if not a legal
predecessor, `UPDATE ... WHERE state = <expected>`, and **assert `rowcount > 0`**.
That last step is the one that turns a silent no-op into an error, and it is
exactly what `create_po_from_pr` is missing today.

## 3.4 Schema

```sql
-- The PR number registry. One row per PR, so the number can be UNIQUE — which
-- pr_master itself cannot be, since a PR is many lines.
CREATE TABLE pr_registry (
    PR_Number   TEXT PRIMARY KEY,
    Site_ID     TEXT NOT NULL,
    created_by  TEXT,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ⚠️ NO uq_po_per_pr. OPERATOR RULING 2026-08-20 (Q7): a PR may legitimately
-- carry SEVERAL POs — partial fulfilment splits one request across vendors or
-- deliveries. The uniqueness required is only that a PR_Number is unique in the
-- registry and a PO_Number is unique in purchase_orders (which it already is).
-- Constraining one PO per PR would have made partial fulfilment unrepresentable.
-- What still has to be fixed is the SILENT part: create_po_from_pr's
-- `UPDATE ... SET logistics_status='in_po'` matching zero rows and passing, and
-- the manual lane raising a PO against a PR the automatic lane already consumed.
-- Those are state-machine assertions (§3.3), not a unique index.

-- Idempotency keys for the four mutating procurement actions.
CREATE TABLE procurement_idempotency (
    idem_key    TEXT PRIMARY KEY,
    action      TEXT NOT NULL,
    result_json TEXT NOT NULL,
    created_by  TEXT,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

`_next_pr_number()` becomes: generate, `INSERT INTO pr_registry`, retry on
conflict up to N times. The race closes because the database, not the reader,
decides who got the number.

The `pr_registry` backfill still has to **survey first**: if `pr_master` already
holds a PR number issued at two different sites, the primary key cannot be
created and the migration must refuse with a readable list rather than
half-applying.

## 3.5 Idempotency at the API layer

`Idempotency-Key` header on `POST /hod/prs`, `/hod/prs/{n}/submit`,
`/logistics/pos`, `/logistics/pos/{n}/assign`. Same key + same action → the
stored result is replayed with `"replayed": true`. Same key + *different* body →
**409**, because that is a client bug, not a retry.

## 3.6 Frontend

The UI half of the guard, matching `assign_po`'s existing precedent:

- Submit PR, Create PO, Assign PO: hidden (not merely disabled) once the state
  has moved past the point where the action is legal.
- `useMutation` gets an `Idempotency-Key` generated per form-mount, so a
  double-click and a flaky-network retry send the *same* key.
- Every 409 renders the server's message verbatim. A guard the user cannot read
  gets worked around.

## 3.7 Tests (suite CF)

- Two concurrent `create_pr` calls → two distinct numbers (the race, run under
  `asyncio.gather`)
- Second `create_po_from_pr` on the same PR → error, and `pr_master` unchanged
- Second `submit_pr` → idempotent, and **exactly one** `pr_submitted_to_logistics`
  notification row (counting the notification is what catches the current bug)
- Same idempotency key twice → identical body, one side effect
- Same key, different body → 409
- The migration's duplicate survey, against a fixture that has duplicates

---

# 4. TRACK 4 — SME Session Builder ⇄ Manpower

## 4.1 The good news: the material-availability engine already exists

"What we can do right now" is `sme_engine.build_sqm_rollup()`, which is already
parity-locked, already server-side, and already emits per (tag, code):

```
Remaining_SQM                 ← the overall total (materials no object)
SQM_Achievable_Now            ← what we can do right now (PHYSICAL stock only)
SQM_Achievable_With_Ordered   ← once the open POs land
SQM_Deficit                   ← what has to be procured
Coverage_Now_Pct
```

So Track 4 needs **no new estimation logic and no new dual-engine surface**. It
is a composition: run the cascade, take the rollup, feed each SQM figure through
the same `manhours_per_sqm` the planner already uses.

This is worth dwelling on because the alternative was expensive. `codeStats` in
`session.ts` has no Python twin, and computing availability client-side would
have meant an official manpower document whose numbers came from a browser —
which the SME Canon explicitly rejects.

## 4.2 The three-column output

Mirroring the SME session report's own shape, per role:

| | Basis | Man-hours | Headcount for the target | Shifts | Days |
|---|---|---|---|---|---|
| **We can do** | `SQM_Achievable_Now` | … | … | … | … |
| **Overall total** | `Remaining_SQM` | … | … | … | … |
| **Blocked** | `SQM_Deficit` | … | — | — | — |

The **Blocked** row deliberately has no headcount. Labour you cannot deploy
because the material has not arrived is not a hiring requirement — printing a
headcount against it invites somebody to hire for it. It shows man-hours (the
size of the delay) and the materials responsible.

## 4.3 State handoff — the actual engineering problem

`ScenarioProvider` is mounted **inside `SmePage`**. `ManHoursPage` is a different
route and cannot read it. Three options:

| | How | Verdict |
|---|---|---|
| A | Lift `ScenarioProvider` to `App.tsx` | Widest blast radius; the provider's per-user/per-site key logic and its `?scenario=` URL sync were carefully scoped and I do not want to disturb them for this |
| B | URL parameter — reuse the existing `?scenario=` encoding, add `?codes=` | **Recommended.** Zero new state, shareable, survives refresh, and the encoding is already written and tested |
| C | `localStorage` handoff key | Invisible, needs its own lifecycle, and a stale key is a wrong report with no way to tell |

**Recommendation: B.** The button becomes a `navigate('/mh?tab=session&scenario=…&codes=…')`.
`ManHoursPage` reads the params, selects the tab, and runs the plan. The SME
scenario store is untouched.

## 4.4 API

```
POST /mh/planner/session
  { priority_order: [...tags], codes: [...], site_id, target_days, shifts_per_day }
  →  runs sme_engine cascade → build_sqm_rollup → planner arithmetic
  →  { jobs[], can_do{}, overall{}, blocked{}, by_role[], materials_blocking[] }
GET  /mh/planner/session/export/{key}    csv | xlsx | pdf
```

Gated `require_roles("hod")`. Reads only.

⚠️ **Cost.** The cascade over a full site is the heaviest read in the codebase.
Running it inside a planner request that the user will re-fire on every target-day
change needs either a short-TTL cache keyed on (site, order, codes) or a debounced
client. I lean towards computing the cascade once per selection and re-running only
the (cheap) manpower arithmetic when `target_days` changes — the cascade does not
depend on the deadline. See Q8.

## 4.5 Frontend

- `SessionBuilder.tsx`: a **"📊 Session Report For MP&H"** button beside the
  existing share-link control, enabled only when the session is non-empty.
- `ManHoursPage.tsx`: a new **"🔗 SME Session Plan"** tab, and `?tab=` handling.
- `SessionManpowerReport.tsx`: the three-column layout above, per-role
  collapsible, exports.

## 4.6 Tests (suite CG)

- A session of 2 tags with known stock produces `can_do` man-hours exactly equal
  to `SQM_Achievable_Now × manhours_per_sqm`
- Zero physical stock → `can_do` is 0 man-hours and `blocked` is the whole
  requirement — **and the blocked row carries no headcount** (the trap in 4.2)
- `can_do + blocked == overall`, to float tolerance (conservation)
- The URL round-trip: tags and codes survive encode → navigate → decode
- Cascade result is independent of `target_days` (justifies the caching in 4.4)

---

# 5. TRACK 5 — KPI cards

Small, and worth being precise about, because "stretch to full width" has two
readings.

The current pattern is `<Col xs={12} md={6}>` × 4 — a fixed 4-up grid. When a
row has 3 or 5 cards (`AdminConsolePage`, `ExecutiveSummaryPage` at `xl={4}`,
and the planner's own KPI rows), the last row is short and the dead space is on
the right.

Proposal: a `<KpiRow>` wrapper that lays its children out with **flex, not a
24-column grid** — `display:flex; flex-wrap:wrap; gap:16px` with each card
`flex: 1 1 220px`. Cards then divide the row evenly whatever their count, and
wrap to equal-width rows below the breakpoint. `KpiCard` itself needs
`height: 100%` so a two-line title does not make one card shorter than its
neighbours.

Applies to: `Dashboard`, `AdminConsolePage`, `ExecutiveSummaryPage`,
`ManHoursPage`, `ManpowerPlanner`, `ExecutionReportTabs`, `LiningCoveragePage`,
`PpeForecastPage`, `MaterialCardPage`, `EmployeesPage`, `SmeDashboard`.

Verified in the preview at 375 / 768 / 1280 px and in both themes; the
`responsive.spec.ts` E2E gains an assertion that no KPI row leaves more than one
card-width of trailing space at desktop.

---

# 6. TRACK 6 — Documentation and the AI Assistant

## 6.1 Order of operations

**Documentation first.** Section 0.4 shows the assistant is largely accurate
about a stale manual. Tuning the prompt over a corpus that is three phases out of
date optimises the wrong thing.

## 6.2 The documentation work

| File | Work |
|---|---|
| `USER_MANUAL.md` §19 | Rewrite. 5 tabs → 11. GI/NON_GI. Configurable thresholds. Shift. Execution workflow. Four report tabs. The Planner. |
| `USER_MANUAL.md` §18 | SME: the MP&H hand-off, CV/ME tags |
| `USER_MANUAL.md` §2 | **Restructure so the matrix cannot be separated from its caveat** (see 6.3). Add `qc_hod`. |
| `USER_MANUAL.md` new §23 | "2026-08 Phase 7 + 8 Feature Update" |
| `USER_MANUAL.md` §16 | Procurement: the uniqueness locks |
| `PROJECT_HANDOVER.md` | Phase 8 rulings, LOCKED |
| `MANUAL_TESTING_GUIDE.md` | §§15a–15f, one per track |
| `docs/ARCHITECTURE.md` | qc_hod, pr_registry, qc_escalations, the planner selection step |
| `REPO_MAP.md` | new modules |
| `docs/PROJECT_STATUS.md` | gates, head, state |

🟡 **`docs/USER_MANUAL.md` is a stale 26 KB duplicate from 2026-07-26.** The
assistant reads the **root** copy (`GI_USER_MANUAL_PATH`, default
`USER_MANUAL.md`). Two files with one name is how they drift, and one of them is
already six weeks behind. Q10 asks whether to delete it or make it a pointer.

## 6.3 The §2 restructure — the specific fix for the HOD answer

The bug is structural: a `##` boundary between a claim and its qualifier lets
retrieval return one without the other. Three changes:

1. **Put the access matrix and the exact-lock caveat in the same `##`
   sub-section**, so they cannot be chunked apart.
2. **Rewrite the caveat to name the roles positively.** "Man-Hours is locked to
   its own role" is true and unusable out of context. "Man-Hours is open to HOD
   and Admin only" is true, useful, and cannot be misread in isolation.
3. **Add a per-role capability block** (your "strict role-based capability
   matrices") — one `###` per role listing what that role can and cannot do, so
   a role question retrieves a chunk that is *about that role* rather than a
   table cell.

Point 3 also helps retrieval mechanically: BM25 scores a chunk headed
"### 2.3.5 Head of Department — what you can do" very highly for "what can an
HOD do", which no cell in a wide markdown table ever will.

## 6.4 Assistant accuracy — the prompt

The "look at section 2.1" behaviour has three sources, and all three need
addressing:

1. `render_context()` labels every chunk `=== Section 2: … › 2.2 … ===`. The
   model sees section numbers and repeats them. **Keep the labels** (they are
   how the role filter stays auditable) but instruct explicitly that they are
   provenance, not citations to pass on.
2. The manual's own body says things like "See §20." The model parrots
   cross-references. Documentation fix: cross-references become descriptive
   ("see the Auditor chapter"), and the prompt bans bare section numbers.
3. Nothing currently forbids it. New rules:

```
- ANSWER THE QUESTION. Never reply by pointing at a section, a chapter or a
  page number. If the CONTEXT contains the answer, state it. "That is covered
  in section 2.1" is not an answer and is never an acceptable reply.
- Give the concrete steps or the concrete fact, in the user's own terms.
- You may name a screen the user should open ("Man-Hours → Manpower Planner").
  You may not name a document section as a substitute for answering.
- If the CONTEXT is thin but not empty, answer what it does support and say
  plainly what you could not find. Do not refuse a partially answerable question.
```

## 6.5 Assistant accuracy — retrieval

| Change | Why |
|---|---|
| Raise `_PER_SECTION_CHAR_CAP` from 800, or drop head-truncation for the fallback entirely | 800 chars into §2 is *before* the access matrix — the fallback path cannot answer a permission question for any non-admin role |
| Guarantee §2 in the candidate set for role/permission questions | The single most-asked class of question; a lexical miss on it is the failure you reported |
| Add a small **alias map** (`manpower → man-hours`, `MP&H`, `portal → page`, `blasting → surface prep`) applied to the query before tokenising | You say "Manpower portal"; the manual says "Man-Hours page". BM25 cannot bridge that, and it is precisely the query that failed |
| Add `qc_hod` to `_ROLE_ALLOWED` / `_ROLE_LABEL` / `_ROLE_REFUSAL` | The QSEP release forgot this for `qc` and the code comment says so |
| Widen `k` from 6 and `char_budget` from 7000 for level ≥ 2 roles | HOD questions span the SME/Man-Hours/procurement chapters |

## 6.6 Assistant performance

Measure before changing. Current suspects, in the order I would check them:

1. **`_index()` is `lru_cache`d per process** — but the cache is cold after every
   restart and the manual is ~220 KB. Build it in the FastAPI lifespan so the
   first user of the day does not pay for it.
2. **`_manual_text()` is cached but never invalidated** — editing the manual
   requires a restart. Add an mtime check.
3. **`GEN_SEMAPHORE` defaults to 2** and `MODEL_CHAT` is `llama3.1:8b` with
   `KEEP_ALIVE = 30m`. On the CPX42 one-warm-model ruling, two concurrent 8B
   generations is the likely source of "sometimes slow". Worth measuring against
   a semaphore of 1 with a queue-position SSE event, which the endpoint already
   emits.
4. **`num_predict = 512`** — most answers need far fewer; a lower cap on the
   manual lane shortens the tail.

I want to instrument first and report numbers rather than propose a target
(Q11).

## 6.7 Tests (suite CH)

- The `_ROLE_ALLOWED` map covers **every** key in `ROLE_META` — a role added
  without a chapter list fails the build. This is the test that would have caught
  the `qc` omission.
- Retrieval for "can an HOD open the manpower portal" returns a chunk containing
  the access matrix, for every role allowed §2
- The prompt contains the no-citation rule
- Alias map: "manpower portal" and "man-hours page" retrieve the same top chunk
- Manual freshness: §19's tab count matches `ManHoursPage`'s tab count — a
  **doc-drift gate**, so the manual cannot silently fall behind again

That last one is the most valuable test in Track 6. It is what turns 0.4 from a
recurring problem into a one-time fix.

---

# 7. Migrations, sequencing, gates

## 7.1 Migrations (in order, single head maintained)

| Rev | Track | Contents | Risk |
|---|---|---|---|
| `a1c4e7f2b830` | 2 | `qc_escalations`, `qc_stagnation_rules`, seed default thresholds | Low — additive |
| `b2d5f8a3c941` | 3 | `pr_registry` + backfill from `pr_master` DISTINCT, **preceded by a survey that refuses** if a PR number was issued at two sites; `procurement_idempotency`. **No `uq_po_per_pr`** — operator ruling Q7: a PR may carry several POs | 🟠 Can fail on real data. `data_upgrade(conn)` contract; survey output must name every offending PR |
| `c3e6a9b4d052` | 1 | *(none expected)* — reserved only if Q1/Q2 need a `Variant_Discriminator` column | — |

Tracks 4, 5, 6 need no migration.

`tools/migration/cutover_migrate.py` must gain the new tables in
`POST_LOAD_ADDITIONS`, and `verify_data_migration_contract()` must see the
`b2d5f8a3c941` data step — the Phase 7 guard exists precisely so this cannot be
forgotten.

## 7.2 Sequencing

Six tracks is too much for one branch. Proposed order — **correctness before
features, corpus before consumer**:

| Slice | Branch | Contents | Why here |
|---|---|---|---|
| **8a** | `feat/phase8-planner-math` | 0.1 · 0.2 · 0.3 + suite CE math half | Fixes wrong numbers now in production. Nothing else should build on a 25× error |
| **8b** | `feat/phase8-planner-ux` | Track 1 display, multi-select, target days, CV/ME | Depends on 8a's selection step |
| **8c** | `feat/phase8-procurement-lock` | Track 3 | Independent; the risky migration gets its own PR and its own rollback |
| **8d** | `feat/phase8-qc-hod` | Track 2 | Independent |
| **8e** | `feat/phase8-sme-mp-link` | Track 4 + Track 5 | Needs 8b's arithmetic |
| **8f** | `feat/phase8-docs-ai` | Track 6 | **Last**, so it documents the finished system |

I will stop after each slice for your green-light, as in Phase 7.

## 7.3 Gates (every slice)

```bash
python backend/api/service_tests.py          # 1667 → ~1780 expected
npx playwright test --config tests/e2e/playwright.config.ts
python legacy/bug_check.py                   # 599/0 — must not move
npm --prefix frontend run parity:sme         # 1,313 — must not move
npm --prefix frontend run test:ui-math       # 33/0
npm --prefix frontend run test:nav           # 47 → 48 (qc-hod)
npm --prefix frontend run build
python tools/parity_check.py                 # 5/5
```

`bug_check.py`'s models-parity allowlist needs the new tables, or it goes red on
the migration slice.

---

# 8. Questions I need answered

Grouped by how much they block. **Q1, Q2, Q5 and Q7 change the design**; the rest
change details.

## 🔴 Blocking

**Q1 — LSC10 (PU seal coat) has two benchmarks and no discriminator in the data.**
`ESC101` exists at 70 m²/shift ("PU lining 4mm") and 90 m²/shift ("PU lining
6mm"), both CV. Every tag carrying LSC10 also carries **both** LSC8 (4 mm) and
LSC9 (6 mm), so the sibling code does not decide it. Which?

  - **(a) Split proportionally — my recommendation.** Divide LSC10's remaining
    area between the two in the same ratio as that tag's LSC8 : LSC9 remaining
    area, and price each part at its own rate. Data-driven, needs no new column,
    and reflects the physical reality that the tag has both a 4 mm and a 6 mm
    area under one seal coat.
  - (b) Always use the slower (70) rate. Safe, simple, overstates.
  - (c) Add a `Variant_Key` to the workbook and make the operator choose per
    equipment. Most accurate, most data entry, needs a migration.

**Q2 — Which blasting benchmark applies to which equipment?** Four CV variants
(Floor & Wall 300 m²/shift; PU 4 mm 40; PU 6 mm 40) plus ME Steel Surface (55),
and `sme_surface_prep_progress` is per-**tag** with no system attached. My
recommendation, derived from the codes each tag actually carries:

```
tag has LSC1/LSC5 on a TANK or VESSEL (Type ME)  → ESC2  Blasting Steel Surface
tag has LSC9 (PU 6mm)                            → ESC1  Blasting Civil PU 6mm Area
tag has LSC8 (PU 4mm)                            → ESC1  Blasting Civil PU 4mm Area
otherwise (concrete substrate, CV)               → ESC1  Blasting Civil Floor & Wall
```

A tag with several of these blasts several kinds of surface, so the prep area
should **split by the areas of the codes that drive each variant** rather than
pick one. Do you agree with the mapping, and with splitting rather than picking?

**Q5 — QC-HOD at level 2 with an explicit cross-site read exemption?** (§2.1.)
Level 3 would grant it 97 endpoints by inheritance. Level 2 + exemption keeps its
surface enumerable. Confirm, or tell me you want it at level 3 and I will
enumerate what that opens.

**Q7 — ✅ ANSWERED 2026-08-20, and it corrects the plan.** A PR may
legitimately carry **several** POs — partial fulfilment. The uniqueness wanted is
only `PR_Number` unique in the registry and `PO_Number` unique in
`purchase_orders`. `uq_po_per_pr` is **dropped from the design**; §3.4 is
updated. The silent failures it was meant to catch — the zero-row
`logistics_status` update, and the manual lane re-raising a consumed PR — are
handled by the state-machine assertions in §3.3 instead.

## 🟠 Design-shaping

**Q3 — Orphan norms.** Should the sync **report** database norms no workbook row
claims (my recommendation — report in the dry run, never auto-delete), or also
**offer deletion** behind an explicit `--prune` flag? And: shall I remove the
current `Blasting Civil PU Area` orphan as part of slice 8a?

**Q4 — Job label ownership.** Backend assembles `Job_Label` and the frontend
renders it *(recommended — avoids a third dual-implementation surface)*, or a
mirrored TS formatter like the sort key?

**Q6 — "Total Days" default.** Should `shifts_per_day` default to 2 when the
roster has any active Night worker in a required role, or always default to 1 and
make running nights an explicit choice? (Today: 22 active employees, all Day.)

**Q8 — Session-plan performance.** Cache the cascade for ~60 s per (site, order,
codes) so target-day changes are instant *(recommended)*, or re-run it every
time and accept the latency?

## 🟡 Detail

**Q9 — Stagnation thresholds.** Defaults of 90 days without movement and 60 days
to expiry, HOD-editable per category — right numbers to start from?

**Q10 — `docs/USER_MANUAL.md`** (stale 26 KB duplicate, 2026-07-26): delete it,
or replace its contents with a pointer to the root manual?

**Q11 — Assistant performance target.** Shall I instrument first (cold-start
index build, prompt-eval time, generation time, queue wait) and report numbers
before proposing changes, or go straight to the four fixes in §6.6?

**Q12 — Escalation reach.** Should a QC-HOD escalation reach the target role at
**every** site (e.g. "all site QCs"), or must they pick a specific site or
warehouse each time? *(Recommendation: pick a target; a broadcast to every site
QC about one site's material is the kind of message people learn to ignore.)*

---

## What I will not do without an answer

- Choose the LSC10 or blasting variant myself (Q1, Q2) — a wrong choice here is
  a wrong number in every plan, and it is not mine to guess.
- Delete the orphan norm row or the duplicate manual (Q3, Q10).
- Register `qc_hod` at any level until §2.1 is confirmed (Q5).

Everything else in this plan I can build on the recommendations as stated.
