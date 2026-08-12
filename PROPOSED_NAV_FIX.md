# PROPOSED_NAV_FIX.md — navigation & RBAC isolation plan

**Status: PROPOSAL. No navigation code has been changed.**
Written 2026-08-12 against `main` @ `ca88779`. Awaiting your review.

Scope: `frontend/src/config/nav.tsx`, `frontend/src/config/entities.ts`,
`frontend/src/components/AppLayout.tsx`, and the backend role gates they are
supposed to agree with.

---

## 0. Read this part first — what I could and could not reproduce

You reported three things. I compiled the live manifest and computed the exact
page list every role sees, rather than reading the rules and guessing. The
generator is mechanical: it evaluates the real `canAccess()` against the real
`NAV` for each role at its real level from `ROLE_META`. §2 is that output.

| Your report | Verdict |
|---|---|
| Logistics is seeing Warehouse pages | **Partly confirmed.** Not `/warehouse` (Receiving & DN) — that one is correctly hidden. But Logistics does see **Warehouses master data**, the **Rack Locator**, **Assets** and **QC Accounts**, all of which read as "the warehouse's screens". |
| Warehouse and Logistics are seeing SME pages | **Confirmed for Logistics, NOT reproduced for Warehouse.** Logistics sees **Lining Coverage**, which is rendered from SME recipes and SME SQM — it is an SME page wearing a logistics label. A warehouse user sees no SME page at all in the current manifest. If you are seeing one, it is not coming from this manifest and I need the screenshot. |
| Similar bleed-over in HOD, Supervisor, Admin | **Confirmed, and worse than the two above** — see §3.2 and §3.3. The Supervisor and QC portals in particular have collected pages by inheritance, not by decision. |

I would rather hand you one honest "not reproduced" than a plan that quietly
agrees with everything. The Warehouse/SME item is the one open question.

**The most serious finding is not on your list.** `canAccessPath()` — the
function that decides whether a *typed URL* is allowed — **returns `true` for
any path it does not recognise**, and **ignores group-level access entirely**.
See §3.1. Today nothing exploits it. The next page added without a manifest
entry is open to every signed-in user in the company, silently, and no test
would notice.

---

## 1. How access is decided today

Four mechanisms, and the confusion between them is the root cause of most of
the bleed-over.

1. **`minLevel`** — a ladder. `store_keeper 0 · warehouse_user/supervisor/qc 1
   · hod 2 · logistics/auditor 3 · admin 4`. A rule of `minLevel: 1` admits
   **six of the eight roles**.
2. **`anyRole`** — an exact set. Admin is implicitly added.
3. **`writes: true`** — a capability gate, not a rung. Removes a page from
   view-only roles (Auditor) regardless of level.
4. **Admin shadow** — admin may reach anything; its *default* sidebar is the
   curated `ADMIN_DEFAULT_GROUPS`, with an "All areas" switch.

### The ladder is the leak

`minLevel` was the right tool for the legacy portal, where seniority really was
a single line. It is the wrong tool now, because the roles are no longer a
line — they are **four different jobs plus two oversight roles**:

```
        store_keeper      warehouse_user     supervisor        qc
        (runs a store)    (runs a shed)      (asks for stuff)  (inspects)
              \                 |                 /            /
               \________________|________________/____________/
                                |
                        hod (approves)  logistics (buys)
                                |
                      auditor (reads) · admin (owns)
```

A store keeper is not "less senior" than a warehouse user; they do a different
job at a different place. Encoding that as `0 < 1` means every rule written for
the store keeper leaks upward to five roles that have no use for it, and every
rule written for oversight leaks downward past the roles it was aimed at.

Concretely, `minLevel: 1` on the **Employees roster** was written so an HOD
could see who is where. It also hands the full staff list — names, phone
numbers, departments — to warehouse users, supervisors and QC inspectors.
Nobody decided that. The ladder decided it.

---

## 2. Ground truth: what each role sees today

Computed from the live manifest. Admin is shown at its **default** sidebar
(All-areas off).

### 2.1 Matrix

Legend: **●** visible · ○ hidden

| Page | SK | WH | SUP | QC | HOD | LOG | AUD | ADM |
|---|:-:|:-:|:-:|:-:|:-:|:-:|:-:|:-:|
| `/` Dashboard | ○ | ● | ● | ● | ● | ● | ● | ● |
| `/stock` Stock | ○ | ● | ● | ● | ● | ● | ● | ● |
| `/locator` Locator | ● | ● | ● | ● | ● | ● | ● | ● |
| `/assets` Assets | ● | ● | ● | ● | ● | ● | ● | ● |
| `/entry/*` (7 forms) | ● | ○ | ○ | ○ | ○ | ○ | ○ | ○ |
| `/site/incoming` | ● | ○ | ○ | ○ | ○ | ○ | ○ | ○ |
| `/sk/requests` | ● | ○ | ○ | ○ | ○ | ○ | ○ | ○ |
| `/records/inventory` | ● | ● | ● | ● | ● | ● | ● | ● |
| `/records/receipts·consumption·returns·lots` | ○ | ○ | ○ | ○ | ● | ● | ● | ● |
| `/records/purchase-requests` | ○ | ○ | ○ | ○ | ● | ● | ● | ● |
| `/records/purchase-orders` | ○ | ○ | ○ | ○ | ○ | ● | ● | ● |
| `/records/equipment` (SME) | ○ | ○ | ○ | ○ | ● | ○ | ○ | ● |
| `/hod/*` (8 pages) | ○ | ○ | ○ | ○ | ● | ○ | ● * | ○ |
| `/bulk-import` | ○ | ○ | ○ | ○ | ● | ○ | ○ | ○ |
| `/sme` Estimator | ○ | ○ | ○ | ○ | ● | ○ | ● | ○ |
| `/manhours` | ○ | ○ | ○ | ○ | ● | ○ | ○ | ○ |
| `/reports` | ○ | ○ | ○ | ○ | ● | ● | ● | ● |
| `/logistics` Procurement | ○ | ○ | ○ | ○ | ○ | ● | ○ | ○ |
| `/logistics/lining-coverage` | ○ | ○ | ○ | ○ | ○ | ● | ● | ○ |
| `/supervisor` | ○ | ○ | ● | ○ | ○ | ○ | ○ | ○ |
| `/warehouse` Receiving & DN | ○ | ● | ○ | ○ | ○ | ○ | ○ | ○ |
| `/qc/inspections` | ● | ● | ○ | ● | ● | ● | ● | ○ |
| `/qc/accounts` | ○ | ● | ○ | ○ | ● | ● | ○ | ○ |
| `/ppe/forecast` | ● | ● | ● | ● | ● | ● | ● | ○ |
| `/ppe/rules` | ● | ○ | ○ | ○ | ● | ○ | ○ | ○ |
| `/hr/employees` | ○ | ● | ● | ● | ● | ● | ● | ○ |
| `/master/*` (3) | ○ | ○ | ○ | ○ | ○ | ● | ○ | ● |
| `/admin/*` (6) | ○ | ○ | ○ | ○ | ○ | ○ | ○ | ● |
| `/documents` · `/security` · `/feedback` | ● | ● | ● | ● | ● | ● | ● | ● |

\* Auditor sees the HOD group's **read** pages only; `/hod/approvals` and
`/bulk-import` are `writes`-gated and closed to it. That part is working
exactly as designed.

### 2.2 What jumps out

- **A store keeper cannot open the Stock page.** `/stock` is `minLevel: 1`.
  The person who physically holds the stock is the one role locked out of the
  screen named after it. They get `/records/inventory` instead, which is a
  different, thinner view. This is the inverse bug and it is worth fixing in
  the same pass.
- **Seven of eight roles see the Employees roster.** Only the SK is excluded,
  and the SK is the one who issues PPE against an employee ID.
- **Seven of eight roles see the PPE Forecast**, a re-ordering worksheet.
- **QC — a level-1 role — sees Dashboard, Stock, Locator, Assets, the
  inventory ledger and the staff roster.** A quality inspector's job is a
  queue of inspections. Everything else on that list arrived by inheritance.
- **Supervisor sees five pages beyond its own portal**, none of which help it
  raise a material request.

---

## 3. Structural defects

These are ordered by how badly they fail, not by how visible they are.

### 3.1 🔴 The route guard fails OPEN — `nav.tsx`

```ts
export function canAccessPath(user: User | null, pathname: string): boolean {
  …
  for (const g of NAV) {
    const node = g.children.find((n) => n.key === path)
    if (node) return canAccess(user, node.access)   // ← group rule ignored
  }
  return true   // ← unknown path → ALLOWED
}
```

Two defects in six lines:

1. **Unknown paths are allowed.** I verified that all 44 routes declared in
   `App.tsx` currently have a manifest entry, so nothing leaks *today*. That
   is luck, not design: the guard's failure mode is "let them in", and the
   next `<Route>` added without a matching `NAV` node is open to every signed-in
   user with no error, no warning, and no failing test.
2. **Group access is ignored.** `buildMenu` checks `g.access` **and**
   `n.access`; `canAccessPath` checks only `n.access`. So the sidebar and the
   route guard enforce two different policies. Any group whose gate is
   stricter than its children's is deep-linkable by URL. Today the only such
   group is `entry` (group carries `writes`, its nodes do not), and the role
   gate saves us — again, luck.

The two together are why I would fix this **before** touching a single access
rule: a stricter matrix built on a guard that fails open is a matrix that only
holds until someone adds a page.

### 3.2 🟠 The UI and the API disagree, in both directions

The API is the boundary and it is doing its job. But the manifest is supposed
to *agree* with it, and where it does not, the disagreement is itself a bug —
either a role is shown a page whose data it cannot load, or a role can reach by
URL/API what the sidebar says is not theirs.

| Surface | API gate | Nav gate | Disagreement |
|---|---|---|---|
| `/warehouse/*` | `roles(warehouse_user, logistics)` | `anyRole:['warehouse_user']` | **API is wider.** Logistics can drive the entire warehouse — receive goods, cut DNs — by API. The sidebar says it cannot. |
| `/sme/*` | `require_level(2)` | `anyRole:['hod','auditor']` | **API is wider.** Logistics (3) can call every Estimator endpoint. This is the likeliest source of "Logistics is seeing SME". |
| `GET /hr/employees` | `get_current_user` | `minLevel: 1` | **API is wide open.** *Any* signed-in user, including a store keeper, can list the full staff roster with phone numbers. |
| `GET /stock/*` | `get_current_user` | `minLevel: 1` | **API is wider.** The SK is blocked from the page and allowed by the API — see §2.2. |
| `GET /ppe/forecast` | `require_level(0)` | `minLevel: 0` | Agrees. Both are too wide. |
| `/qc/transfers` | `roles(hod, logistics)` | (no page) | Agrees. |
| `/analytics/lining` | `roles(hod, logistics)` | `anyRole:['hod','logistics','auditor']` | **Nav is wider.** An auditor is offered Lining Coverage and gets a 403 from its data endpoint. |

The last row is the user-visible failure mode of a manifest that is wider than
the API: a page in the menu that greets you with an error.

### 3.3 🟠 Group membership is not the same question as page access

`quality` and `safety` are groups whose *gate* is a union of everyone with a
stake, which is right, but whose *children* then inherit near-identical unions.
The result is a "Safety & People" group that appears in seven sidebars.

The fix is not to narrow the groups. It is to accept that **two different
things are being asked**: "does this role work in this area?" (group) and
"does this role need this screen?" (node). Today most nodes just restate their
group.

### 3.4 🟡 `writes: true` is under-used

It is the cleanest idea in the file — a capability, not a rung — and it is
applied to only 11 of 44 pages. Several obvious write surfaces are unmarked:
`/entry/*` (the nodes, not the group), `/qc/inspections` (deliberate, and
documented — the Auditor should audit the queue), `/site/incoming`,
`/sk/requests`, `/hr/employees`.

---

## 4. Proposed target matrix

Principle, stated once so every cell below can be checked against it:

> **A page belongs to a role when that role cannot do its job without it.**
> Not when the role is senior enough. Not when the data is harmless. Oversight
> roles (HOD, Logistics, Auditor, Admin) get breadth *explicitly*, page by
> page, never by passing a number.

### 4.1 The target

Changes from today are marked: **＋** newly granted · **－** revoked · (blank) unchanged.

| Page | SK | WH | SUP | QC | HOD | LOG | AUD | ADM |
|---|:-:|:-:|:-:|:-:|:-:|:-:|:-:|:-:|
| `/` Dashboard | ●＋ | ● | ● | ●－?† | ● | ● | ● | ● |
| `/stock` Stock | ●＋ | ● | ● | ● | ● | ● | ● | ● |
| `/locator` Locator | ● | ● | ○－ | ○－ | ○－ | ● | ○－ | ● |
| `/assets` Assets | ● | ● | ● | ○－ | ● | ● | ● | ● |
| `/entry/*` (7 forms) | ● | ○ | ○ | ○ | ○ | ○ | ○ | ○ |
| `/site/incoming` | ● | ○ | ○ | ○ | ○ | ○ | ○ | ○ |
| `/sk/requests` | ● | ○ | ○ | ○ | ○ | ○ | ○ | ○ |
| `/records/inventory` | ● | ● | ● | ● | ● | ● | ● | ● |
| `/records/receipts·consumption·returns·lots` | ○ | ○ | ○ | ○ | ● | ● | ● | ● |
| `/records/purchase-requests` | ○ | ○ | ○ | ○ | ● | ● | ● | ● |
| `/records/purchase-orders` | ○ | ●＋ | ○ | ○ | ○ | ● | ● | ● |
| `/records/equipment` (SME) | ○ | ○ | ○ | ○ | ● | ○ | ●＋ | ● |
| `/hod/*` reads | ○ | ○ | ○ | ○ | ● | ○ | ● | ○ |
| `/hod/approvals` | ○ | ○ | ○ | ○ | ● | ○ | ○ | ○ |
| `/bulk-import` | ○ | ○ | ○ | ○ | ● | ○ | ○ | ○ |
| `/sme` Estimator | ○ | ○ | ○ | ○ | ● | ○ | ● | ○ |
| `/manhours` | ○ | ○ | ○ | ○ | ● | ○ | ○ | ○ |
| `/reports` | ○ | ○ | ○ | ○ | ● | ● | ● | ● |
| `/logistics` Procurement | ○ | ○ | ○ | ○ | ○ | ● | ○ | ○ |
| `/logistics/lining-coverage` | ○ | ○ | ○ | ○ | ○ | ● | ○－ | ○ |
| `/supervisor` | ○ | ○ | ● | ○ | ○ | ○ | ○ | ○ |
| `/warehouse` Receiving & DN | ○ | ● | ○ | ○ | ○ | ●＋ | ○ | ○ |
| `/qc/inspections` | ● | ● | ○ | ● | ● | ● | ● | ○ |
| `/qc/accounts` | ○ | ● | ○ | ○ | ● | ● | ○ | ○ |
| `/ppe/forecast` | ● | ○－ | ○－ | ○－ | ● | ●‡ | ○－ | ○ |
| `/ppe/rules` | ● | ○ | ○ | ○ | ● | ○ | ○ | ○ |
| `/hr/employees` | ●＋§ | ○－ | ●¶ | ○－ | ● | ○－ | ● | ○ |
| `/master/*` (3) | ○ | ○ | ○ | ○ | ○ | ● | ○ | ● |
| `/admin/*` (6) | ○ | ○ | ○ | ○ | ○ | ○ | ○ | ● |
| `/documents` · `/security` · `/feedback` | ● | ● | ● | ● | ● | ● | ● | ● |

† **Open question for you.** A QC inspector at a site — should they see the
Dashboard? It is the site's operational overview and arguably context for their
work. I have left it granted. Say the word and it goes.

‡ Logistics keeps the PPE Forecast because it is a **re-ordering worksheet** and
Logistics does the ordering. That is the one role outside the store that needs it.

§ The SK **gains** the Employees roster. Since QSEP, PPE cannot be issued
without a valid `employee_id_number`, and the SK is the person typing it. They
are currently the only role that needs the roster and the only role denied it —
a straight inversion. The roster read must be **site-scoped** (it already is,
via `resolve_site_param`) and the page must be **read-only** for them.

¶ Supervisor keeps it: they raise requests naming workers.

### 4.2 Every revocation, with its reason

| Revoked | From | Why |
|---|---|---|
| `/locator` | SUP, QC, HOD, AUD | It is a "walk to this shelf" tool. Only the people who walk to shelves (SK, WH) and the people who own the master (LOG, ADM) need it. |
| `/assets` | QC | Serialised tools. A quality inspector inspects *material*, not the hammer inventory. |
| `/ppe/forecast` | WH, SUP, QC, AUD | It is a store's re-ordering worksheet. It reveals, by name, who holds what safety gear — that is HR-adjacent data with no operational use for these four. |
| `/hr/employees` | WH, QC, LOG | Full staff roster with phone numbers. None of these three manages, moves or equips people. **This is the single biggest PII narrowing in the plan.** |
| `/logistics/lining-coverage` | AUD | The auditor already has `/hod/lining-coverage`, the same page. Two menu entries for one screen is noise, and the data endpoint 403s the auditor anyway (§3.2). |
| `/qc/inspections` | (kept for all) | **Deliberately not revoked.** An SK needs to see *why* the issue form refused them; an auditor must be able to audit the queue. Read is open, `decide` is `roles("qc")` server-side. This one is right as it stands. |

### 4.3 Every grant, with its reason

| Granted | To | Why |
|---|---|---|
| `/` + `/stock` | SK | Fixes §2.2. The store keeper is locked out of the stock screen. The API already allows them (`get_current_user`), so this is the manifest catching up to the truth. |
| `/records/purchase-orders` | WH | A warehouse user receives against a PO. Being unable to look one up is why they ask Logistics to read it out to them. |
| `/records/equipment` | AUD | Every other read entity is open to the auditor; SME equipment was missed. `/sme/*` is `require_level(2)` and the auditor is 3 — the API has always allowed it. |
| `/warehouse` | LOG | Closes §3.2 row 1 in the honest direction: the API has always let Logistics run the warehouse (`roles(warehouse_user, logistics)`). Either the page follows the API or the API narrows. **Recommend the page follows** — a Logistics user covering an unstaffed shed is a real operational need, and it is already how the system behaves. |
| `/hr/employees` | SK | See §4.1 note §. |

### 4.4 Backend changes this implies

The manifest cannot be tightened alone; five API gates are wider than any
proposed page rule and would leave the sidebar as decoration.

| Endpoint | Today | Proposed | Priority |
|---|---|---|---|
| `GET /hr/employees` | `get_current_user` | `roles(store_keeper, supervisor, hod, auditor)` + existing site scope | 🔴 PII |
| `GET /hr/employees/{id}` | `get_current_user` | same | 🔴 PII |
| `GET /ppe/forecast` | `require_level(0)` | `roles(store_keeper, hod, logistics)` | 🟠 |
| `/sme/*` | `require_level(2)` | `roles(hod, auditor)` | 🟠 closes the Logistics/SME leak at the boundary, not just in the menu |
| `/analytics/lining` | `roles(hod, logistics)` | add `auditor` **or** drop the auditor's nav entry | 🟡 pick one; §4.2 picks the latter |

`/warehouse/*` deliberately stays `roles(warehouse_user, logistics)` — §4.3.

---

## 5. Proposed implementation order

Each step is independently shippable and independently revertable.

**Step 1 — make the guard fail closed** *(no policy change, highest value)*
- `canAccessPath`: unknown path → `false`, and check `g.access` before
  `n.access`.
- Add a build-time test: every `<Route path>` in `App.tsx` has a `NAV` node.
  That test is what stops this regressing, and it is worth more than the fix.
- **Risk:** any route I missed becomes a redirect-to-home. The test above turns
  that from a production surprise into a red CI run.

**Step 2 — close the API gaps** (§4.4)
- Backend only. The sidebar is unchanged; some roles start getting honest 403s
  where they previously got data.
- **Risk:** highest of the five steps, and the one to stage first. If a screen
  somewhere quietly reads `/hr/employees` as a lookup, it breaks. I would grep
  every caller before touching it.

**Step 3 — replace `minLevel` with explicit role sets on the 9 contested pages**
- Dashboard, Stock, Locator, Assets, PPE Forecast, Employees, PO records,
  Equipment records, Lining Coverage.
- Leave `minLevel` where it genuinely means seniority (`/reports`, `/master/*`,
  `/admin/*`).
- **Risk:** low and immediately visible — a role either sees a menu entry or
  does not.

**Step 4 — mark the remaining write surfaces `writes: true`**
- `/entry/*` nodes, `/site/incoming`, `/sk/requests`.
- **Risk:** near zero; affects the Auditor only, which is already excluded by
  role from all three.

**Step 5 — regression-test the matrix**
- Extend `tests/e2e` with a per-role sidebar assertion driven by §4.1 as data.
  One table, eight roles, ~44 pages: a matrix that is *asserted* cannot drift
  back, and a future page must state its role or fail the build.

---

## 6. What I need from you before Step 1

1. **The Warehouse/SME screenshot** (§0). I could not reproduce it and I do not
   want to write a fix for a symptom I have not seen.
2. **The QC Dashboard question** (§4.1 †).
3. **A ruling on `/warehouse` for Logistics.** §4.3 recommends granting the
   page because the API already grants the capability. The alternative —
   narrowing the API to `warehouse_user` only — is equally coherent and I will
   take whichever you pick, but the current split is the worst of the three.
4. **Confirmation that revoking `/hr/employees` from Logistics is acceptable.**
   It is the one revocation with a plausible business objection (vendor
   contacts, deliveries addressed to people). If Logistics needs names, say so
   and it stays.
