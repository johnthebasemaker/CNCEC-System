# SESSION HANDOVER — read this first, then `PROJECT_HANDOVER.md`

> **Written 2026-08-04**, closing a long working session. Branch **`main`**,
> clean, at commit **`482d176`** (PR #25 merged).
> The project is **feature-complete and stable**. Every gate is green.
> **Nothing is mid-flight — there is no half-finished work to pick up.**

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

> **2026-08-05 — the overnight asset/SME programme.** Six phases on
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
| Backend service tests | **1193 / 0** (suites A…BI) | `GI_DOTENV=0 .venv/bin/python -m backend.api.service_tests` |
| Playwright E2E | **57 / 57** | `cd tests/e2e && npm test` |
| SME UI math | **33 / 0** | `npm run test:ui-math --prefix frontend` |
| SME TS↔PY parity | **1,313 comparisons** | `npm run parity:sme --prefix frontend` |
| Legacy regression | **599 / 0** | `.venv/bin/python legacy/bug_check.py` |
| Frontend | `tsc -b` + build + `oxlint` clean | `npm run build --prefix frontend` |
| Alembic | single head **`a71e93b4c2f8`** | `cd backend && alembic heads` |
| `gi_database.db` | sha256 `00652932…ba038` **unchanged** | `shasum -a 256 gi_database.db` |

> `tools/parity_check.py` (5/5) is meaningful **only** on CI or a freshly
> cutover mirror — it fails against the live dev DB by design, because Postgres
> is permanently ahead of the frozen SQLite.

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

---

## 6. Open items — none of them blocking

> Verified 2026-08-04. The first two items on this list are **done**:
> `launchctl print-disabled` shows all four stale agents `disabled`, so the
> ~2,880 daily respawns have stopped, and `deploy/cloudflared/config.yml` ends
> cleanly at the `http_status:404` rule with no stray UUID.

1. **The Auditor cannot see the HOD group or the SME Estimator** — those are
   exact-locked to `{hod, admin}`, and an exact-lock is not a level. If auditors
   should read them, add `'auditor'` to those `anyRole` lists; the write guard
   keeps them read-only regardless.
2. **The per-account login throttle is per-process.** With N uvicorn workers the
   effective budget is N × 8. Same caveat the existing IP limiter carries; a
   shared store (Redis) is the fix when deploying behind more than one worker.
3. **Hetzner deployment** — paused by decision. Runbook is ready.

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
| [`USER_MANUAL.md`](USER_MANUAL.md) | 21 chapters, user-facing. The chatbot's corpus — **edit carefully** |

---

**Status: stable, fully documented, all gates green. Safe to restart.**
