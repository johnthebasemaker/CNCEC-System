# Phase 9 — Proposed Plan

> **Status: COMPLETE 2026-08-28. Approved 2026-08-25. Q1–Q16 answered — see §8. Slices 9a + 9b are
> SHIPPED on `feat/phase9-wbs-and-math`; 9c–9f remain.**
>
> | Slice | Branch | State |
> | --- | --- | --- |
> | **9a** WBS + work types | `feat/phase9-wbs-and-math` | ✅ shipped — suite CK (42), E2E +4, migration `e3a7d9b21f64` |
> | **9b** planner shift math | `feat/phase9-wbs-and-math` | ✅ shipped — suite CL (13), CI-33b/c, no migration |
> | **9c** form generation + QR | `feat/phase9-form-gen` | ✅ shipped — suite CM (38), E2E +4, migration `f4b8e2c07d15` |
> | **9d** OCR state machine | `feat/phase9-ocr-workflow` | ✅ shipped — suite CN (48), E2E +6, migration `a2c9f5e81b43` |
> | **9e** analytics chart | `feat/phase9-analytics` | ✅ shipped — suite CO (26), E2E +4, no migration |
> | **9f** rename + docs | `feat/phase9-naming-docs` | ✅ shipped — CJ 25–33, no migration |
>
> **The rulings, locked:** Q1 **(b)** — the OCR form becomes the ONLY way lining
> material is deducted; the SK stops raising separate manual issues for it.
> Q2 **(D)** — hard block with an explicit HOD override, mandatory reason and
> QC-HOD notification, **plus a Lot/Batch section**: a handwritten space on the
> printed form and, in the app, a dropdown sourced from the Receipt Log's
> `serial no.` for that material. Q3 — drain existing rows. Q4 — rejection is a
> hard terminal stop; no bounce-back loops. Q5/Q6 — QR header yes; PDF, JPG,
> JPEG, PNG, HEIC; **RAW dropped**; forms downloadable any time, uploaded same-
> or next-day via the Supervisor portal. Q7 — local `qwen2.5vl:7b`, prompt
> tuned hard for digits, **with a config seam in `ai/client.py`** for a cloud
> key later. Q8/Q9 — one form, one photo; no write-in rows (supervisors use
> only recipe-defined materials, and may write 0). Q10 — **(b) nights buy
> time**, with a fluid workforce and disproportionate shifts. Q11/Q12 —
> cumulative-to-date MH/m²; a gap plus the reason on zero-area days.
> Q13 — UI display text only, JSON keys untouched. Q14 — strict dropdown with
> an HOD management UI. Q15 — forward only. Q16 — `(Site_ID, Work_Type_Norm)`.

**Original plan follows, unchanged.**

> **Status: DRAFT, awaiting approval. No application code written.**
> Written 2026-08-25 against `main` @ `de37728`, alembic head `c7e1a4b92d63`.
> Baselines to protect: service tests **1868/0**, E2E **107/107** (21 specs),
> legacy **599/0**, SME parity **1,313**, UI math **33/0**, nav **48**.

---

## 0. What I read before proposing anything

`backend/api/services/execution.py` (the Phase 5 state machine) · `services/ledger.py`
(the stock ledger) · `services/quality.py` (the QSEP gates) · `services/planner.py`
(the manpower arithmetic) · `ai/ocr.py`, `ai/handwritten.py`, `ai/jobs.py`,
`ai/fuzzy.py` · `docs/features/handwritten-ocr/` (11 spec files) · `entry_docs.py`
(WBS) · `manhours.py` · the baseline + workflow migrations · `frontend/src/pages/`
(`OcrImportPage`, `ManpowerPlanner`, `ManHoursPage`, `ExecutionPage`) ·
`config/nav.tsx` · and the **live mirror** (`gihub` on :5433) for row counts and
data shape.

### 0.1 ⚠️ Four things already exist. Phase 9 is mostly rewiring, not building.

| The ask sounds like | What is already there |
|---|---|
| "introduce OCR" | `ai/ocr.py` `CONSUMPTION_PROMPT` (calibrated on real GI paperwork), `ai/handwritten.py` (421 lines implementing an 11-file spec: ditto marks, corrections, fuzzy match, flags), `ai/jobs.py` (async queue, atomic claim, orphan sweep), `OcrImportPage.tsx` (394 lines) |
| "recommend a vision model" | `MODEL_VISION` already defaults to **`qwen2.5vl:7b`** — the model you listed — and the job worker already runs it under a one-warm-model semaphore |
| "crew per shift vs overall headcount" | `ManpowerPlanner.tsx` already renders **both** figures side by side, plus an alert explaining the split (slice 8b) |
| "manage WBS in the app" | `wbs_master` table + `assert_wbs()` gate + HOD endpoints `GET/POST/PATCH /hod/site-config/wbs` **all exist and are wired into the Issue and Receive forms** |

**The single reason your WBS column is blank:** those HOD endpoints have **no
frontend**. Nothing in `frontend/src/` calls `/hod/site-config/wbs`. So
`wbs_master` has **0 rows**, `assert_wbs` never bites (it is a no-op until a site
has active WBS numbers), and all **1,674** consumption rows carry no WBS. Track 4
is a screen plus a dimension, not a subsystem.

### 0.2 Live data, measured

```
consumption            1,674 rows   ·  0 with WBS  ·  35 distinct Work_Type
sme_equipment              85 rows   ·  0 of 85 have WBS_No (column exists, unpopulated)
wbs_master                  0 rows
sme_recipe                 46 rows   ·  11 systems · 3–6 materials each · 1–3 ESCs
sme_execution_entry         0 rows   ← the Phase 5 workflow has never been used here
mh_timesheets               0 rows   ← Track 2's chart has no data yet
mh_production               0 rows   ←
```

---

# TRACK 1 — The paper-first OCR consumption workflow

## 1.1 ⚠️ BLOCKING: the brief merges two ledgers that are deliberately separate

> "**Step 4 (HOD Approval)** … and approve. (This triggers the actual inventory deduction)."

**It does not today, and nothing in the execution workflow ever has.** There are
two independent systems, and the brief writes as if they are one:

```
SYSTEM A — STOCK          entry/consumption → pending_issues → [HOD] → consumption
  writes:  the quantity that left the shelf
  gates:   quality.assert_mtc_for_issue()  +  quality.assert_qc_cleared()   ← QSEP
  carries: WBS, Lot_No, FEFO, PPE distribution

SYSTEM B — EXECUTION      sme_execution_entry: DRAFT_SK → PENDING_SUPERVISOR
                                             → PENDING_HOD → APPROVED
  writes:  AREA ONLY — post_progress() → sme_sqm_progress / sme_surface_prep_progress
  gates:   none (nothing it writes is stock)
  carries: benchmark snapshot, variance reasons, HOD edit justification
```

`hod_decide()` → `post_progress()` posts **m², never quantity**. Its own docstring
is explicit: *"APPROVAL IS WHAT MOVES STOCK"* refers to **area**, and the reason
an HOD edit is safe is precisely that *"they are correcting a proposal, not
reversing a posting."*

So "HOD approval deducts inventory" is a **new capability**, and it collides with
System A. If the store keeper has already issued the drum through
`pending_issues → consumption` **and** the execution entry now deducts on
approval, **the same material is deducted twice.** This is the inventory
constraint you asked me to analyse, and it is the plan's central decision.

**→ See Q1. Nothing in Track 1 can be built until this is answered.**

## 1.2 ⚠️ The new flow inverts a deliberate anti-gaming control

`services/execution.py` states the current rule and its reason:

> *"the SUPERVISOR … must NOT be able to edit the material lines: the store
> keeper counted those, and a supervisor whose numbers look bad has both the
> motive and the opportunity to adjust the consumption they are being measured
> against."*

Track 1 makes the supervisor **the author** of those quantities. That is not a
bug in your brief — the paper form genuinely originates in the field — but the
control it removes has to be replaced consciously, not lost silently.

**The replacement is the red highlight, and it only works if it is two-sided.**
Your Step 3 says SK edits show in red so the HOD sees the SK altered the
supervisor's claim. That is half a control. The other half: the HOD must also
see **what the OCR read versus what the supervisor typed**, or a supervisor can
simply overwrite the machine's reading of their own handwriting and no one can
tell. Proposal:

| Layer | Column | Shown as |
|---|---|---|
| what the camera saw | `OCR_Qty` | grey, never editable |
| what the supervisor submitted | `Supervisor_Qty` | **amber** when ≠ `OCR_Qty` |
| what the SK verified | `Actual_Qty` | **red** when ≠ `Supervisor_Qty` |
| what the HOD settled | `Actual_Qty` + `HOD_Edit_Justification` | **purple** when ≠ SK value |

Four numbers, each with an owner and a timestamp. The existing `Original_Qty`
column already establishes this pattern; this extends it.

## 1.3 The generated form is the real accuracy win — and it should carry a QR code

This is the most important technical point in Track 1.

`sme_recipe` holds **3–6 materials per Lining_System_Code** across 11 systems. So
a generated form pre-prints the material names. **The model then never has to
read a handwritten material name** — only handwritten digits, plus Equipment and
Area.

That deletes the hardest and least reliable half of the existing pipeline:
`handwritten.py`'s 18-entry corrections table (`Yloues → Gloves`), the fuzzy
matcher, the substitution rules, and the candidate-picking UI all exist to
recover from misread **names**. Pre-printing makes them unnecessary *for this
form*. (Keep them: the free-form 30-row consumption sheet in `OcrImportPage`
still needs them.)

Push it further — **stamp a QR code in the form header** (`qrcode[pil]` is
already a dependency; `documents.py` already renders sticker sheets):

```
GIF1|<site>|<system_code>|<esc>|<form_uuid>|<issued_iso>
```

Then Site, System, sub-activity and form identity are read by a **decoder, not a
language model** — zero error rate — and the vision model's whole job shrinks to:
*for each pre-numbered row, what number is written in the QTY box?* Plus two
free fields (Equipment, Area).

A QR also gives replay protection for free: a `form_uuid` that has already been
consumed is a duplicate upload, which is otherwise very hard to detect when
someone photographs the same sheet twice.

## 1.4 ⚠️ Your file-type list exceeds what the pipeline can decode

> "uploads a photo (JPG, JPEG, RAW, PDF)"

`ocr.prep_image_for_vision()` is PIL-based. JPEG/PNG work. HEIC works —
`pillow-heif>=0.16` is in `requirements.txt`, and the code degrades with a clear
message if it is ever missing. Neither `rawpy` nor `pdf2image`/`pypdfium2` is a
dependency.

- **RAW** (CR2/NEF/ARW/DNG) — **not supported.** Needs `rawpy`/`libraw`. Also
  20–50 MB per file. Phones do not produce RAW by default; this is a DSLR
  format. I would drop it unless you have a specific camera in the field.
- **PDF** — **not supported on this path.** Needs `pypdfium2`/`pdf2image` to
  rasterise. Worth adding: "scan to PDF" is the default on office MFPs and
  several phone scanner apps, so this one is likely to be used in practice.

**→ Q6.**

## 1.5 The proposed state machine

```
                       ┌─ (manpower-only bypass, unchanged) ─┐
                       │                                     ▼
  [Supervisor]    DRAFT_SUPERVISOR ──submit──► PENDING_SK ──verify──► PENDING_HOD
  uploads photo   (OCR extracting /            (SK may edit;          (HOD may edit;
  or types        supervisor edits;             edits render RED)      justification
  manually        reasons MANDATORY)                                   + notify)
                                                                          │
                                                          ┌───────────────┴────────┐
                                                          ▼                        ▼
                                                      APPROVED                 REJECTED
                                                   (posts area; and             (reason
                                                    stock — see Q1)             mandatory)
```

Written as data, exactly like today's `TRANSITIONS` dict, so an illegal move is a
lookup miss with a readable message:

```python
TRANSITIONS = {
    DRAFT_SUPERVISOR: {PENDING_SK, REJECTED},
    PENDING_SK:       {PENDING_HOD, DRAFT_SUPERVISOR},   # SK bounce-back? → Q4
    PENDING_HOD:      {APPROVED, REJECTED, PENDING_SK},  # HOD bounce-back? → Q4
    APPROVED: set(), REJECTED: set(),
}
```

**Migration of the old states.** `sme_execution_entry` is empty in this mirror,
but production may differ. The plan assumes: **keep `DRAFT_SK` and
`PENDING_SUPERVISOR` legal for existing rows only**, drain them, and refuse *new*
entries in those states. A state machine that deletes a state some live row is
sitting in strands that row forever. **→ Q3.**

## 1.6 ⚠️ QSEP: a hard block at HOD approval cannot prevent anything

You asked me to ensure the MTC/QC gates are still strictly enforced before HOD
approval. I can wire them there — but I want the consequence on the record first,
because it is a genuine change in what the gate *means*.

Today `assert_mtc_for_issue` and `assert_qc_cleared` sit on the **issue** path
(`stage_consumption`, `supervisor.approve_smr`). They are **preventive**: they
refuse to let uncertified material leave the store.

In a paper-first world the material **has already left the store, and has already
been applied to the vessel.** A hard block at HOD approval prevents nothing. It
only strands the record — and a stranded record means the consumption is never
posted, so stock silently overstates and the variance report loses the entry
entirely. The rule would punish the paperwork, not the risk.

Three options:

| | Behaviour | Consequence |
|---|---|---|
| **A** | Hard block at HOD approval | Faithful to the letter. Entry cannot be filed until Logistics uploads the MTC. Real material stays unrecorded meanwhile. |
| **B** | Hard block at **supervisor submit**; loud warning at HOD | Fails earliest, closest to the field, while the drum is still traceable. Still after the fact. |
| **C** | Warning on this path; hard block stays on the issue path only | The gate keeps doing the job it was built for. The execution entry *reports* a QSEP breach to the HOD and QC-HOD instead of hiding it. |
| **D** | Hard block **with an explicit HOD override** + mandatory reason + QC-HOD notification | Blocks by default, but the record can always be completed. |

**My recommendation is D.** It keeps your "strictly enforced" requirement, keeps
stock truthful, and turns an uncertified application into a *visible, attributed
exception* rather than an invisible one. **→ Q2.**

## 1.7 Schema impact — Track 1

New table `sme_consumption_form` (the generated paper):

| Column | Notes |
|---|---|
| `id`, `Site_ID`, `Form_UUID` (unique) | `Form_UUID` is the QR payload + duplicate-upload guard |
| `Lining_System_Code`, `Execution_Sub_Activity_Code` | what the form was generated for |
| `Issued_To_Role`, `created_by`, `created_at` | SK / Supervisor / HOD all may generate |
| `consumed_entry_id` (nullable FK) | set when a photo of this form is accepted |

`ALTER sme_execution_entry`:

| Column | Notes |
|---|---|
| `Form_UUID`, `OCR_Job_ID`, `OCR_Attachment_ID` | provenance: which paper, which job, which photo |
| `OCR_Confidence`, `OCR_Raw_JSON` | what the model actually returned, kept verbatim |
| `sk_verified_at`, `sk_edited` (bool), `SK_Edit_Reason` | the red-highlight audit |
| `Entry_Origin` (`'ocr'` \| `'manual'` \| `'legacy'`) | so a hand-typed entry is never mistaken for a scanned one |
| `WBS_Number` | Track 4 |

`ALTER sme_execution_entry_material`:

| Column | Notes |
|---|---|
| `OCR_Qty`, `OCR_Qty_Text` | grey layer — `Qty_Text` keeps `"2+3"` verbatim |
| `Supervisor_Qty` | amber layer |
| `SK_Qty`, `sk_edited` (bool) | red layer |
| `Row_Index` | the pre-printed row number — positional mapping, no name matching |

`ALTER sme_execution_entry_manpower`: none.

One migration, additive only, no data backfill required.

## 1.8 OCR model recommendation

**Use `qwen2.5vl:7b` — which is what the code already defaults to.** Reasons:

1. It is already wired, already prompt-calibrated against real GI paperwork, and
   already covered by the suite via the monkeypatch seam in `ai/jobs.py`.
2. Ruling from the intelligence-layer program: **one warm model on the same
   box.** A second vision model means either a cold start on every switch or
   ~12 GB resident. `nomic-embed-text` (274 MB) is fine alongside; a second 6 GB
   VLM is not.
3. `llama3.1:8b` and `qwen2.5-coder:7b` have **no vision head** — they cannot
   read an image at all. Of your four local models, exactly one is a candidate.

**The honest caveat: handwritten *digits* are where 7B VLMs are weakest** —
`4/9`, `1/7`, `3/8`, and a trailing `.5`. The generated form mitigates this three
ways (pre-printed names remove name errors; boxed digit cells constrain the
glyph; QR removes header errors), but it does not eliminate it. **The design
answer is not a better model — it is that the supervisor and the SK both look at
every number before it counts.** That is already your workflow. What I would add:

- **Never auto-accept a digit the model is unsure of.** Keep `quantity: null`
  when unambiguous parsing fails (the existing prompt already does exactly this
  — *"NEVER invent 0 or 1 — a wrong quantity … is an ordering error, and a null
  is a question"*). Render nulls as an empty, focused, required field.
- **Show the crop.** Display the photo region beside each row so the supervisor
  verifies against the image, not against memory.
- **Benchmark-plausibility flag.** `sme_recipe.For_1_SQM × Area` gives an
  expected quantity. A reading 10× off is far more likely a misread digit than
  real consumption — flag it *at extraction*, before the human reviews.

If accuracy proves insufficient in UAT, the escalation is **not** a bigger local
model — it is a **cloud VLM (Claude) for this one form**, which is a config
switch in `ai/client.py`, not an architecture change. **→ Q7.**

## 1.9 Edge cases — Track 1

1. **Same form photographed twice** → `Form_UUID` already consumed. Reject with a
   link to the existing entry.
2. **Two pages of one form** → the plan assumes one form = one photo. Multi-page
   needs a page number in the QR. **→ Q8.**
3. **Photo is unreadable / blank / of the wrong thing.** Model returns rows that
   fail parsing. Must fail *loudly* at extraction, never produce an empty entry
   the supervisor silently submits.
4. **Struck-through rows.** The existing prompt captures `struck_through: true`
   rather than dropping them — keep that, and render them struck but visible.
   A cancelled line is evidence.
5. **Handwritten equipment not in the master.** Your Step 2 covers this with a
   dropdown. Never fuzzy-auto-assign an equipment tag: the wrong tag posts area
   to the wrong vessel and corrupts `Completion_Pct`.
6. **Area (`Actual_SQM`) is handwritten and drives everything** — the benchmark
   comparison, `post_progress`, and every man-hour figure. It deserves the same
   three-layer treatment as quantities, and a plausibility check against
   `sme_equipment.Surface_Area_SQM` (a single day exceeding the vessel's total
   area is a misread digit).
7. **Ollama down / model not pulled.** `/ai/health` already reports this. The
   manual-entry lane must stay open — a paper-first workflow cannot be blocked by
   a model being unavailable.
8. **Cold start 30–90 s.** Already handled by the job queue + polling. The
   supervisor is standing in a plant on mobile data; the UI must survive a locked
   phone (it does — state is in Postgres).
9. **Material on the paper that is not in the system's recipe** (a genuine
   substitution in the field). Pre-printed forms have no row for it. Needs a
   "write-in" row. **→ Q9.**
10. **Timezone.** `Work_Date` is a `Text` column; the handwritten date is parsed
    DD/MM by `parse_form_date` with a ±2-year window. Site is Saudi (UTC+3) —
    a form filled at 22:00 must not land on the previous day.

---

# TRACK 2 — Planner math and analytics

## 2.1 ⚠️ The reported bug is arithmetically correct. The *model* is what needs your ruling.

> "if a user selects Day vs. Day+Night … the total crew count shown remains the same"

That is deliberate, documented, and — under the model as stated — right:

```
M = man-hours of work   D = days   H = 11 h/shift   S = shifts/day
A person works ONE shift per day, so each offers H·D hours regardless of S.
N people deliver N·H·D man-hours.
  ⇒ Total headcount = M / (H·D)          ← independent of S
  ⇒ Per-shift crew  = Total / S           ← halves when you add nights
```

Two shifts means **two disjoint crews — nobody works both**. So nights do not
reduce how many people you employ; they reduce how many are on the deck at once.
`ManpowerPlanner.tsx` already shows both numbers and carries an alert saying
exactly this, because the intuitive reading ("two shifts, so half the people")
would **under-hire by half**.

**So before I "fix" anything I need to know which real-world question you are
asking**, because these are three different models and only one is on screen:

| Model | Adding a night shift… | Total headcount | Days |
|---|---|---|---|
| **(a) Current** — fixed deadline, one shift per person | splits the same workforce | unchanged | unchanged |
| **(b) Nights buy time** — fixed crew, deadline floats | doubles daily throughput | unchanged | **halves** |
| **(c) Workface cap** — only *k* people physically fit on the vessel | is the **only** way to add capacity | **doubles** (2 crews of *k*) | halves |

**I believe (c) is your real operational situation, and that it is the variable
the model is missing.** You cannot put 30 masons inside one tank. If the binding
constraint is *simultaneous crew size*, then the current model silently assumes
an unlimited workface and will happily tell you to field a crew that cannot
physically stand in the vessel — and *that* is a real defect, distinct from the
one reported.

The fix under (c) is one new input, `Max_Crew_Per_Shift` (per equipment, or per
benchmark via `sme_manpower_norm.Crew_Size`), and one new output line: *"11 people
needed per shift, but only 6 fit — you need 2 shifts, or 4 more days."* **→ Q10.**

## 2.2 A genuine inconsistency found in the overtime arithmetic

`planner.py:1093` computes normal capacity over the **entire roster**:

```python
n_gi = sum(int(v.get("GI", 0)) for v in available.values())      # ALL roles
normal_capacity = (n_gi * gi_thr + n_ng * ng_thr) * shifts
```

but `days_with_roster` (line ~1112) correctly restricts to roles the job needs,
with the comment *"idle blasters do not shorten a brick-lining job."* **The same
reasoning was not applied to `normal_capacity`.** An idle blaster inflates normal
capacity, which understates overtime and understates `ng_needed`/`gi_needed` —
the hiring advice. Worth fixing in Phase 9 regardless of the shift ruling.

## 2.3 The analytical bar chart

**Good news: no migration.** Everything needed is already columnar:

```
mh_timesheets  (Work_Date, Equipment_Tag, System_Code, Total_Hours, Site_ID)
mh_production  (Work_Date, Equipment_Tag, System_Code, SQM_Done,   Site_ID)
```

`recharts@3.9.2` is already a dependency. `MH_per_SQM` already exists as a
concept in `_scorecard_rows` — but aggregated per (equipment, system), never
date-wise. So this is one new endpoint + one chart component.

Proposed `GET /mh/analytics/daily`:
`?site_id&system_code&equipment[]&from&to&metric=mh_per_sqm|hours|sqm`
→ one series per equipment tag, one bar per date.

**⚠️ Two things will make a naive version lie:**

1. **`mh_timesheets` and `mh_production` are both empty (0 rows).** The chart will
   render blank on day one and stay blank until timesheets are actually entered.
   Worth knowing before it is called broken.
2. **Daily MH/m² is unstable and often undefined.** Mobilisation, scaffolding,
   curing and inspection days book **hours with zero m²** → divide-by-zero, or a
   spike to infinity that dwarfs every real bar. And `mh_production` carries a
   `Distribution_Method`, meaning SQM may be spread across days on a different
   basis than hours were.

   **Proposal:** plot **cumulative-to-date MH/m²** as the comparison line (stable,
   converges, and is the number an HOD actually argues about) with **daily hours**
   as the bars behind it. Never plot a ratio for a day with no m² — render it as a
   gap with a marked "hours, no area" flag, which is itself informative.

**→ Q11, Q12.**

---

# TRACK 3 — "Labor" → "Manpower"

Small in the UI, with three traps that a global find-and-replace walks straight
into. **7 hits in `frontend/src/`, 14 in `backend/api/`.**

**Safe to rename (user-visible):**

| File | Current |
|---|---|
| `config/nav.tsx:218` | `label: 'Labor Tracking'` → `'Manpower Tracking'` |
| `ManHoursPage.tsx:973` | `🕒 Man-Hours & Labor Tracking` |
| `ManHoursPage.tsx:852,858` | column titles `Done (Labor)`, `Labor Var` |
| `manhours.py:513,1335,1627,1638` | OpenAPI summaries + xlsx sheet titles |

**⚠️ Trap 1 — two of those are API JSON keys, not labels.**
`manhours.py:1369,1373` emit `Done_SQM_Labor` and `Labor_Variance_Pct`. They are
pinned by `service_tests.py:1217–1219` and consumed by the frontend. Renaming
them is an **API contract change**. The column *heading* can change without the
*key* changing. **→ Q13.**

**⚠️ Trap 2 — the AI corpus is pinned by the doc-drift gates.**
`ai/manual_qa.py:96` maps chapter 19 to `"Man-Hours & Labor Tracking Manual"`, and
suite CJ (`CJ-07`, `CJ-09`) asserts the literal string
`"Man-Hours / Labor Tracking |"` inside the `USER_MANUAL.md` access matrix.
Renaming the manual without the module — or the module without the tests — turns
those gates red. **All three move together, in one commit.**

**⚠️ Trap 3 — "Labour" (British) also appears** in `SessionManpowerReport.tsx:14`,
`ExecutionPage.tsx:134` and `session_plan.py:37`. Two spellings, one word.

**No database column contains "Labor"** — confirmed against every migration. The
rename cannot reach the schema.

---

# TRACK 4 — WBS assignment and reporting

## 4.1 What exists, and the precise reason the column is blank

- `wbs_master` (`WBS_Number`, `Description`, `Site_ID`, `status`, unique on
  `(WBS_Number, Site_ID)`) — **exists**, **0 rows**.
- `entry_docs.active_wbs()` / `assert_wbs()` — exist, and are already enforced on
  Issue and Receive. The gate is **conditional**: it only bites once a site has
  active WBS rows. With zero rows it is a permanent no-op.
- `GET/POST/PATCH /hod/site-config/wbs` — exist. **No frontend calls them.**
- `consumption."WBS"`, `pending_issues.wbs`, `sme_equipment.WBS_No`,
  `pr_lines/po_lines.WBS_Number` — all exist. `sme_equipment.WBS_No` is **0 of 85**
  populated.
- A WBS report already exists (`reports.rep_wbs`, "Consumption grouped by WBS") —
  today it would return one row: `(no WBS)`.

**So: the plumbing is complete and the tap was never opened.**

## 4.2 What Track 4 actually adds

**1. A Work_Type dimension.** `wbs_master` is keyed `(WBS_Number, Site_ID)`. You
want assignment *by Work Type*. That is a new table:

```
wbs_work_type_map
  id · Site_ID · Work_Type_Norm · WBS_Number · status · created_by · created_at
  UNIQUE (Site_ID, Work_Type_Norm)          ← one WBS per work type per site
  FK-ish  (WBS_Number, Site_ID) → wbs_master
```

**⚠️ `Work_Type` is free text and it is already dirty.** 35 distinct values across
1,674 rows, of which **4 pairs are pure case-collisions**:

```
civil / Civil      coating / Coating      In yard / In Yard      others / Others
```

Map on a **normalised** key (`lower(trim())`) — `Work_Type_Norm` above — or
`Civil` and `civil` get different WBS numbers and nobody notices. There are also
near-duplicates that are *not* case collisions (`Arrangement` vs
`Site Arrangement`, `Blasting` vs `Sweep blast`) which normalisation will **not**
merge. **→ Q14.**

**2. The HOD screen.** A `🏷️ WBS` tab. Two panels: the WBS master for the site
(reusing the endpoints that already exist) and the Work-Type → WBS mapping grid.
Exact-lock `{hod, admin}`, audited, same shape as the SME Master Data tab.

**3. Resolution + reporting.** A precedence chain, stated once so it can be
checked:

```
explicit WBS on the entry  →  work-type map  →  equipment WBS_No  →  none
```

Then print it. Line-level consumption exports currently do **not** carry WBS —
only the grouped `rep_wbs` does.

**⚠️ Casing.** The live `consumption` column is `"WBS"` (uppercase) while
`pending_issues` is `wbs` (lowercase); `commit_consumption` bridges them
explicitly (parity A4). Any new writer must not assume one spelling. Note also
`post_consumption()` does **not** write WBS at all — it is only ever set by the
`commit_consumption` follow-up UPDATE. That is fine today because
`commit_consumption` is its only caller, but it is a loaded gun for any future
direct-post path.

**4. Backfill.** 1,674 historical rows have no WBS. Applying the map
retroactively is a one-line UPDATE — and it would be **rewriting history on
posted financial records**. I would not do it without an explicit instruction.
**→ Q15.**

---

# 5. Migrations (one per slice, additive)

| # | Slice | Change |
|---|---|---|
| M1 | 9a | `sme_consumption_form` (new); `ALTER sme_execution_entry` +9 cols; `ALTER …_material` +5 cols |
| M2 | 9d | `wbs_work_type_map` (new); `ALTER sme_execution_entry` + `WBS_Number` (folded into M1 if 9d ships first) |
| — | 9b | **none** — the chart reads existing `mh_*` tables |
| — | 9c | **none** — no schema string contains "Labor" |

`Max_Crew_Per_Shift` (Track 2, if Q10 → model (c)) would add one column to
`sme_equipment` or `sme_manpower_norm`.

# 6. Proposed slice order

| Slice | Branch | Contents | Why here |
|---|---|---|---|
| **9a** | `feat/phase9-wbs` | Track 4 entire | Zero dependencies, opens a tap that is already plumbed, and gives Track 1 a WBS to print. Lowest risk, immediate visible value. |
| **9b** | `feat/phase9-planner-math` | Track 2 math + §2.2 fix | Needs only your Q10 ruling. Pure arithmetic + tests. |
| **9c** | `feat/phase9-form-gen` | Printable form + QR + `sme_consumption_form` | The form must exist and be in the field **before** anyone can photograph one. |
| **9d** | `feat/phase9-ocr-workflow` | The new state machine, OCR wiring, three-layer edit trail, QSEP ruling | The big one. Gated on Q1–Q4. |
| **9e** | `feat/phase9-analytics` | Date-wise normalised chart | Best built once real timesheet data exists. |
| **9f** | `feat/phase9-naming-docs` | Track 3 rename + manual + AI corpus + doc-drift gates | Last, so it renames the *finished* surface once rather than chasing it. |

# 7. Risks

1. **Double deduction** (Q1) — the single highest-severity item in Phase 9.
   Mitigation: `Source_Ref = 'SME_EXEC:<Entry_No>:<line_id>'`, following the
   existing `SMR:<no>:<item>` precedent, plus the `services/idempotency.py`
   claim-then-fill protocol.
2. **A QSEP gate that strands records instead of preventing risk** (Q2).
3. **Losing the anti-gaming control** when the supervisor becomes the author
   (§1.2). Mitigation: the four-layer, four-colour edit trail.
4. **OCR digit errors reaching stock.** Mitigation: nulls not guesses,
   benchmark-plausibility flags, image crops beside each row, two human reviews.
5. **A naive Track 3 rename** turning suite CJ red and silently changing an API
   contract (§Trap 1, Trap 2).
6. **A blank chart being reported as a bug** because `mh_timesheets` is empty.

---

# 8. Clarifying questions

Grouped by what they block. **Q1–Q4 block slice 9d entirely.**

### The two-ledger question (blocking)

**Q1.** When the HOD approves an OCR consumption entry, what should happen to
stock?
&nbsp;&nbsp;**(a)** Nothing — it posts area only, exactly as today, and stock stays
on the existing SK issue path. *(No double-deduction risk. But the paper form
then never moves inventory, which I do not think is what you want.)*
&nbsp;&nbsp;**(b)** It becomes the **only** consumption writer for these materials
— the SK stops raising separate issues for lining work, and approval writes
`consumption` rows tagged `Source_Ref='SME_EXEC:…'`. *(My recommendation. One
number, one author, one place. Biggest change to the SK's daily routine.)*
&nbsp;&nbsp;**(c)** It **reconciles** against issues the SK already posted, and
writes only the difference. *(Keeps both paths; hardest to get right; every
mismatch needs an adjudication rule.)*

**Q2.** Which QSEP option — **A** (hard block at HOD), **B** (hard block at
supervisor submit), **C** (warning here, hard block stays on the issue path), or
**D** (hard block + explicit HOD override, mandatory reason, QC-HOD notified)?
*I recommend D.*

**Q3.** Are there live `sme_execution_entry` rows in production in `DRAFT_SK` or
`PENDING_SUPERVISOR`? (There are none in the local mirror.) If yes: drain them,
or migrate them into the new states?

**Q4.** Can the SK send an entry **back** to the supervisor, and can the HOD send
one back to the SK? Or is the only backward move a full `REJECTED`? *(Today
`REJECTED` is terminal — a rejected entry cannot be revived, only re-opened as a
new one. Worth confirming that still suits a paper-first flow.)*

### The form and the model

**Q5.** Should the printable form carry the **QR header** I proposed? It removes
the whole class of header-OCR errors and gives duplicate-upload detection for
free. Any reason field forms cannot be printed from the app (e.g. printed in bulk
offsite, or pre-printed pads)?

**Q6.** **RAW** and **PDF** are not currently decodable. Drop RAW? Add PDF (office
scanners and phone scanner apps default to it)?

**Q7.** Confirm **`qwen2.5vl:7b`** as the vision model. And: if UAT shows digit
accuracy is not good enough, are you open to routing **this one form** to a cloud
VLM, or must everything stay on-premise?

**Q8.** One form = one photo, or can a day's work span multiple sheets?

**Q9.** If a supervisor uses a material **not in that system's recipe** (a field
substitution), should the form have blank write-in rows, or is that a hard
"raise it as a separate entry"?

### The planner

**Q10.** ⚠️ **The most important question in Track 2.** Which model is your
operational reality — **(a)** current (nights split the crew, total unchanged),
**(b)** nights buy time (deadline halves), or **(c)** there is a **maximum crew
that physically fits on a vessel**, so nights are the only way to add capacity?
*If (c) — what is the cap, and does it live on the equipment or on the benchmark?*

**Q11.** For the chart, confirm the comparison metric: **cumulative-to-date
MH/m²** (stable) rather than raw daily MH/m² (spikes to infinity on any day with
hours but no area)?

**Q12.** On a day with **hours but zero m²** (mobilisation, scaffolding, curing),
should the bar show as a gap, as zero, or roll into the next productive day?

### Naming and WBS

**Q13.** Rename the JSON keys `Done_SQM_Labor` → `Done_SQM_Manpower` and
`Labor_Variance_Pct` → `Manpower_Variance_Pct` (a breaking API change I would
sequence carefully), or change only the **display** headings and leave the keys
alone? *I recommend display-only.*

**Q14.** Work_Type is free text: 35 spellings, 4 pure case-collisions, plus
near-duplicates (`Arrangement` / `Site Arrangement`, `Blasting` / `Sweep blast`).
Should Phase 9 (i) normalise case only, (ii) also let the HOD **merge** spellings
into canonical work types, or (iii) convert Work_Type into a **controlled
dropdown** and migrate the 35 existing values? *(iii) is the real fix and the
most disruptive.*

**Q15.** Backfill WBS onto the **1,674 historical consumption rows** using the new
map, or apply it only from the go-live date forward? *I recommend forward-only —
retro-stamping posted records rewrites history.*

**Q16.** Can one Work Type map to **different** WBS numbers at different sites?
*(The proposed unique key `(Site_ID, Work_Type_Norm)` says yes across sites, one
per site. Confirm.)*
