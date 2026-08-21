# MANUAL TESTING GUIDE — General Industries Hub v1.2.0

> **Written 2026-08-09.** This is the master manual-test reference for the whole
> system. It is written for three readers at once: a developer verifying a
> change, a QA tester who has never seen the system, and an automated AI testing
> agent driving the UI or the API.
>
> **It is part of the Definition of Done.** Any change to a feature must update
> the relevant section here in the same pull request — see
> `PROJECT_HANDOVER.md` rule 13.

---

## 0. How to use this guide

### 0.1 Structure

The guide is ordered **chronologically by business workflow**, not by screen.
You can start at §3 and work down, and the data each section produces is the
data the next section needs. That is deliberate: testing the QC block requires
material to exist, which requires a receipt, which requires a purchase order.

| § | Workflow | Depends on |
|---|---|---|
| 2 | Environment and preconditions | — |
| 3 | Access & Identity | §2 |
| 4 | Procure-to-Pay: request → PR → PO → receive → deliver | §3 |
| 5 | Quality Control: certificate, inspection, issue block | §4 |
| 6 | Safety / PPE | §3, and material in stock from §4 |
| 7 | Employees & site transfers | §6 |
| 8 | Daily site operations: receive, issue, return, adjust, count | §4 |
| 9 | Returnable items — the deep "drain the model" section | §8 |
| 10 | Assets & serialised equipment | §3 |
| 11 | Reports, exports & documents | any data |
| 12 | Notifications | any workflow |
| 12b | The Morning Briefing agent | any workflow |
| 13 | Cross-cutting: RBAC, scoping, read-only | all |
| 14 | Edge cases & how to drain any model | all |
| 15 | Do's and Don'ts |  |
| 16 | Testing FAQ |  |
| 17 | Reporting a bug |  |

### 0.2 The format of a test

Each **feature** carries a 5 W's + 1 H block so a reader with no prior knowledge
understands the point of the test before running it:

- **Who** — the role that performs it (and the role that must *not* be able to).
- **What** — the action under test.
- **Where** — the portal, page and control.
- **When** — the trigger, and where it sits in the lifecycle.
- **Why** — the business reason. *If you cannot state this, the test is probably
  asserting an implementation detail rather than a requirement.*
- **Which** — the choices, options or variants that exist.
- **How** — the numbered steps.

Each **case** beneath is written as **Given / When / Then**, which reads as
English to a human and parses cleanly for an agent:

> **Given** a Store Keeper at CNCEC and a Surface Shields item with no
> inspection, **When** they submit an issue for 5 units, **Then** the request is
> refused with HTTP 422 and the message names the site.

### 0.3 Conventions

| Convention | Meaning |
|---|---|
| `TC-AREA-NN` | Stable test-case ID. **Never renumber one** — bug reports cite them. Retire with `(withdrawn)` instead. |
| ✅ | Expected to succeed |
| ⛔ | Expected to be refused — *a refusal is a passing test* |
| ⚠️ | Known limitation, not a bug. Confirm the behaviour, do not raise it. |
| 🤖 | Note specifically for an automated agent |

**Refusals are results.** Roughly a third of the cases here expect the system to
say no. An agent that treats every non-200 as a failure will report this whole
guide as broken. Assert on the **status code and the message**, not on success.

### 0.4 For an automated agent

- Drive the UI through visible labels, or the API through the documented routes.
  Both are valid; the expected results below are stated at the level of
  behaviour, so they hold either way.
- **Never assert on an exact HTML structure or CSS class.** Assert on the
  message text, the status code and the resulting data.
- Message texts quoted here are the substantive part. Match on a **substring**,
  not on the full string — several messages interpolate live numbers.
- Where a test says "the message names X", assert that X appears. Naming the
  actual site or quantity is the point of the test; a generic error would pass a
  laxer assertion and would be a regression.
- Run destructive cases **last within a section**, and never against production.

---

## 1. What this system is, in one page

A multi-site warehouse, procurement and quality ERP. Material is bought
centrally, received into a warehouse, delivered to a site, issued to workers,
and sometimes returned. Layered on that are quality inspection, safety
equipment tracking, an employee roster, serialised assets and a reporting stack.

**The eight roles**, and the one line that matters for each:

| Role | Level | Scope | The one line |
|---|:---:|---|---|
| Store Keeper | 0 | one site | Does the physical entries. Cannot see another site. |
| Warehouse User | 1 | one warehouse | Receives from vendors, cuts delivery notes. |
| Supervisor | 1 | one site | Requests material for workers. Does not touch stock. |
| **QC** | 1 | one site **or** one warehouse | Inspects controlled material and releases it for issue. |
| HOD | 2 | one site | Approves everything the site does. Raises purchase requests. |
| Logistics | 3 | all sites | Turns requests into purchase orders. Sees commercial data. |
| Auditor | 3 | all sites, read-only | Reads everything, writes nothing, anywhere. |
| Admin | 4 | everything | Support and configuration. |

**Two rules that explain most refusals you will see:**

1. **Scoping fails closed.** An unscoped value means "matches nothing", not
   "matches everything". A role that should be pinned to a site but has no site
   sees an empty list — never the whole company.
2. **A higher rank does not open a lower role's workspace.** Logistics outranks
   an HOD and still cannot open the HOD Portal. Only Admin crosses workspaces.

---

## 2. Before you start

### 2.1 Environment

| | |
|---|---|
| **Who** | Whoever runs the test pass |
| **What** | Bring up a test stack and confirm it is not production |
| **Where** | A local or staging instance |
| **When** | Once, before any section below |
| **Why** | Several cases below create, transfer and delete real records |

Bring the stack up:

```bash
./bin/dev.sh localhost
```

The UI is then at `http://localhost:5173` and the API at `http://localhost:8000`.

⛔ **Never run this guide against production.** §9 and §10 create loans and
transfer assets; §11 downloads commercial data.

### 2.2 Accounts you need

You need **one account per role**, and for QC you need **two**: one bound to a
site and one bound to a warehouse, because the two scoping axes are different
code paths and a single account cannot exercise both.

| Purpose | Role | Must have |
|---|---|---|
| Site entries | store_keeper | a Site_ID |
| Goods-in | warehouse_user | a Warehouse_ID |
| Requests | supervisor | a Site_ID |
| Inspection (site) | qc | a Site_ID, **no** Warehouse_ID |
| Inspection (warehouse) | qc | a Warehouse_ID, **no** Site_ID |
| Approvals | hod | a Site_ID |
| Purchasing | logistics | neither — unscoped by design |
| Read-only | auditor | neither |
| Everything | admin | — |

⚠️ **A password set for a test account must satisfy the live policy** — see
TC-ACC-10. `test1234` will be refused; `Test-1234!` will not.

### 2.3 Data preconditions, and what is genuinely empty today

Check these before deciding something is broken. **Several Phase 6 features are
live but hold no data yet**, which is the expected state, not a defect:

| Data | Live count today | Consequence if empty |
|---|---|---|
| Surface Shields materials | 36 | The QC pipeline has something to act on ✅ |
| PPE materials | 9 | The PPE fields have something to trigger on ✅ |
| **PPE usable-time rules** | **0** | ⚠️ Every PPE issue demands a Safety Approval, no expiry is set, and **the PPE Forecast is permanently empty**. Configure a rule (§6.2) before testing §6.4. |
| **PPE distributions** | **0** | No history to read until you issue some |
| **Quality inspections** | **0** | The Inspections queue is empty until controlled material is received |
| Employees | 2 | Enough for one transfer test; add more for §7 |
| **Employee movements** | **0** | The timeline is empty until you transfer someone |
| Asset units | 3 | Real operator data — ⛔ **do not delete these** |

> 🤖 **Agent note:** an empty PPE Forecast is the single most likely false
> positive in this guide. Assert the *cause* (no usable-time rules) before
> reporting it.

---

## 3. Workflow A — Access & Identity

### 3.1 Self-registration and approval

| | |
|---|---|
| **Who** | An unauthenticated person; then an Admin |
| **What** | Request an account, and have it approved or rejected |
| **Where** | Login page → *Request Access*; Admin Portal → Pending Users |
| **When** | Before a new joiner can do anything |
| **Why** | Accounts must be granted by a person, never self-granted. A self-approved account is an unauthorised account. |
| **Which** | Store Keeper, Supervisor, Warehouse User, HOD, **Quality Control**, Logistics |

**How:** open the login page, choose *Request Access*, complete the form,
submit; then sign in as Admin and decide the request.

| ID | Given / When / Then |
|---|---|
| TC-ACC-01 | **Given** the login page, **When** you open *Request Access*, **Then** the role list includes **Quality Control**. ✅ *(This was added in Phase 6; its absence was the bug.)* |
| TC-ACC-02 | **Given** a registration for a site role, **When** no site is chosen, **Then** it is refused and names the missing site. ⛔ |
| TC-ACC-03 | **Given** a QC registration, **When** you supply a site, **Then** it is accepted and the account is site-bound. ✅ |
| TC-ACC-04 | **Given** a QC registration, **When** you supply a warehouse instead, **Then** it is accepted and the account is warehouse-bound. ✅ |
| TC-ACC-05 | **Given** a QC registration, **When** you supply **both** a site and a warehouse, **Then** it is refused. ⛔ *An inspector with two remits has none.* |
| TC-ACC-06 | **Given** a pending request, **When** the person tries to sign in, **Then** they cannot. ⛔ |
| TC-ACC-07 | **Given** a pending request, **When** an Admin approves it, **Then** sign-in succeeds and the sidebar shows only that role's pages. ✅ |
| TC-ACC-08 | **Given** a pending request, **When** an Admin rejects it, **Then** the account never becomes usable. ⛔ |
| TC-ACC-09 | **Given** a username that already exists, **When** it is requested again, **Then** it is refused. ⛔ |

### 3.2 The password policy

| | |
|---|---|
| **Who** | Anyone setting a password: Admin creating a user, Admin resetting one, a person self-registering, an HOD creating a QC account |
| **What** | The password rules, applied identically on every path |
| **Where** | Every password field in the product |
| **When** | On every credential change |
| **Why** | The policy used to exist in five copies, and self-registration silently enforced a weaker rule than the admin screens. The weakest door sets the real policy. |
| **Which** | ≥ 8 characters, an uppercase letter, a number, a special character |

| ID | Given / When / Then |
|---|---|
| TC-ACC-10 | **Given** any password field, **When** you enter `abcdefgh`, **Then** it is refused for missing uppercase, number and special character — **and the message lists all three at once**, not one at a time. ⛔ |
| TC-ACC-11 | **Given** any password field, **When** you enter `Test-12!`, **Then** it is accepted (8 chars, upper, digit, special). ✅ |
| TC-ACC-12 | **Given** a 12-character password with no uppercase, **When** submitted, **Then** it is refused. ⛔ *Length alone is no longer sufficient.* |
| TC-ACC-13 | **Repeat TC-ACC-10 on all four paths** — admin create, admin reset, self-registration, QC account creation. **Then** the message is identical on each. This is the actual test; a policy that differs by door is the defect. |

### 3.3 Login, sessions and lockout

| ID | Given / When / Then |
|---|---|
| TC-ACC-14 | **Given** valid credentials, **When** you sign in, **Then** you land on your role's home page. ✅ |
| TC-ACC-15 | **Given** a wrong password entered 8 times for one username, **When** you try a 9th, **Then** you are throttled. **And** the correct password still works after the window — it **throttles, it does not lock**, because a permanent lock is a denial-of-service anyone can trigger. |
| TC-ACC-16 | **Given** a signed-in session, **When** it idles 30 minutes, **Then** you are signed out, with a warning at 28. **And** signing out in one browser tab signs out the others. |

---

## 4. Workflow B — Procure-to-Pay

The full chain, in order. Each step feeds the next.

```
Supervisor request  →  SK approves  →  HOD approves the issue
                                    ↓
                          HOD raises a Purchase Request
                                    ↓
                     Logistics converts it to a Purchase Order
                                    ↓
                    Warehouse receives the goods against the PO
                                    ↓
              Delivery Notes are drafted, submitted, approved, shipped
                                    ↓
                        Site receives the Delivery Note
```

### 4.1 A Supervisor requests material

| | |
|---|---|
| **Who** | Supervisor (raises); Store Keeper (decides) |
| **What** | A material request for workers |
| **Where** | Supervisor Portal → new request; Store Keeper → SK Requests |
| **When** | When the field needs material |
| **Why** | The person who needs material is not the person who holds it. The request is the paper trail between them. |
| **Which** | Approve as requested · approve an adjusted quantity · reject with a reason · Supervisor cancels their own |

| ID | Given / When / Then |
|---|---|
| TC-P2P-01 | **Given** a Supervisor at a site, **When** they submit a request for an in-stock item, **Then** it appears in that site's SK queue. ✅ |
| TC-P2P-02 | **Given** the same request, **When** a Store Keeper at **another site** opens their queue, **Then** it is not listed. ⛔ |
| TC-P2P-03 | **Given** a pending request, **When** the SK approves it, **Then** issues are **staged for HOD approval** — stock has *not* moved yet. |
| TC-P2P-04 | **Given** a pending request, **When** the SK approves an adjusted quantity, **Then** the staged issue carries the adjusted number, not the requested one. |
| TC-P2P-05 | **Given** a pending request, **When** the SK sets a line's quantity to 0, **Then** the line is withdrawn. |
| TC-P2P-06 | **Given** a pending request, **When** the SK submits a **negative** quantity, **Then** it is refused. ⛔ |
| TC-P2P-07 | **Given** a pending request, **When** the SK rejects it, **Then** the reason is recorded and visible to the Supervisor. |
| TC-P2P-08 | **Given** their own pending request, **When** the Supervisor cancels it, **Then** it leaves the SK queue. ✅ |
| TC-P2P-09 | **Given** somebody else's request, **When** a Supervisor tries to cancel it, **Then** refused. ⛔ |

### 4.2 An HOD raises a Purchase Request

| | |
|---|---|
| **Who** | HOD |
| **What** | Create a PR, optionally from a scanned document |
| **Where** | HOD Portal → Purchase Requests |
| **When** | When the site needs stock it does not have |
| **Why** | Purchasing is centralised; the site states the need, Logistics places the order |
| **Which** | Manual entry · from a scanned supplier PR (§4.6) · auto-drafted from a shortage |

| ID | Given / When / Then |
|---|---|
| TC-P2P-10 | **Given** an HOD, **When** they create a PR with lines, **Then** it is saved as a draft and given a PR number. ✅ |
| TC-P2P-11 | **Given** a draft PR, **When** the HOD edits a line quantity, **Then** the change is saved. ✅ |
| TC-P2P-12 | **Given** a draft PR, **When** the HOD submits it, **Then** it reaches the Logistics queue. |
| TC-P2P-13 | **Given** a submitted PR, **When** an HOD **at another site** opens their list, **Then** it is not visible. ⛔ |

### 4.3 Logistics creates the Purchase Order

| ID | Given / When / Then |
|---|---|
| TC-P2P-14 | **Given** a submitted PR, **When** Logistics converts it, **Then** a PO is created carrying the PR's lines, and the PR is linked to it. ✅ |
| TC-P2P-15 | **Given** a PO, **When** it is routed to a warehouse, **Then** it appears in that warehouse's assignment list and in **no other** warehouse's. |
| TC-P2P-16 | **Given** a PO, **When** an HOD tries to open the Logistics Portal, **Then** refused — **rank does not grant a workspace**. ⛔ |
| TC-P2P-16a | **Given** an **already-assigned** PO, **When** Logistics opens the Purchase Orders tab, **Then** the row shows the **warehouse it went to** and the Assign button is **replaced by an `assigned` tag** — not merely greyed out. *(new 2026-08-13)* |
| TC-P2P-16b | **Given** an already-assigned PO, **When** the same warehouse is assigned again (a double-click, or a stale tab), **Then** it **succeeds silently** — no second assignment row, and the warehouse is **not** notified twice. ✅ |
| TC-P2P-16c | **Given** an already-assigned PO, **When** a **different** warehouse is assigned, **Then** it is **refused**, and the message names both the warehouse that holds it and the one being refused. ⛔ ⚠️ *Re-routing a PO another warehouse is already expecting is a decision, and there is deliberately no silent path for it.* |

### 4.4 The warehouse receives the goods

| | |
|---|---|
| **Who** | Warehouse User |
| **What** | Record what physically arrived against the PO |
| **Where** | Warehouse Portal → assignments |
| **When** | On the vendor's delivery |
| **Why** | This is the moment stock becomes real, and the moment two automatic things fire: quality inspections open, and Delivery Notes draft themselves |

| ID | Given / When / Then |
|---|---|
| TC-P2P-17 | **Given** an assignment, **When** the warehouse acknowledges it, **Then** its status advances. ✅ |
| TC-P2P-18 | **Given** an acknowledged assignment, **When** quantities are received, **Then** stock rises and the PO lines show a delivered quantity. |
| TC-P2P-19 | **Given** a receipt of **controlled (Surface Shields)** material, **Then** a **pending inspection is opened automatically** — the QC does not create it. → §5.3 |
| TC-P2P-20 | **Given** a receipt destined for a site, **Then** **draft** Delivery Notes are created automatically, grouped so R/L and B/L material never share one. → TC-P2P-22 |
| TC-P2P-21 | **Given** a receipt greater than the ordered quantity, **When** submitted, **Then** the over-shipment guard refuses the delivery note, **but the goods receipt itself still stands**. ⚠️ *This asymmetry is deliberate: the stock genuinely arrived.* Confirm the receipt survived and the reason is in the audit log. |

### 4.5 Delivery Notes

| | |
|---|---|
| **Who** | Warehouse User (prepares, submits) · Logistics and HOD (approve) · Store Keeper (receives) |
| **What** | The document that moves material from a warehouse to a site |
| **Where** | Warehouse Portal → Delivery Notes |
| **When** | After goods-in, before the truck leaves |
| **Why** | A two-stage approval means neither the warehouse nor the site can move material on its own say-so |
| **Which** | Auto-drafted (§4.4) or created by hand — **both go through the same rules** |

| ID | Given / When / Then |
|---|---|
| TC-P2P-22 | **Given** an auto-drafted DN, **Then** its status is **draft**, it is flagged as auto-generated, and a notification told the warehouse it is waiting. ⚠️ **It is deliberately not submitted** — the system cannot know a truck exists. |
| TC-P2P-23 | **Given** a draft DN, **When** vehicle and driver are added and it is submitted, **Then** it moves to the approval queue. ✅ |
| TC-P2P-24 | **Given** a submitted DN, **When** Logistics approves and then the HOD approves, **Then** it can be shipped. |
| TC-P2P-25 | **Given** a submitted DN, **When** the HOD rejects it, **Then** a reason is required and is shown to the warehouse. ⛔ |
| TC-P2P-26 | **Given** a shipped DN, **When** the destination Store Keeper opens Incoming Deliveries, **Then** it is listed and can be staged into stock. ✅ |
| TC-P2P-27 | **Given** a DN containing a **controlled** material, **When** it is created **without a Material Test Certificate on file**, **Then** it **succeeds** — the certificate is recorded on the note when one exists and demanded at issue, never at dispatch. → §5.2 ✅ ⚠️ **Inverted 2026-08-12.** |
| TC-P2P-28 | **Given** a mixed R/L and B/L line set, **When** one DN is attempted for both, **Then** it is refused. ⛔ |
| TC-P2P-28a | **Given** an HOD-approved DN, **When** **Ship** is pressed, **Then** a dialog demands the **Delivery Note number printed on the physical document** and a **scan or photo of it** — both mandatory. *(new 2026-08-13)* ⚠️ *That number is the carrier's, not ours. It is unrelated to `DN_Number`, which the system generates — the whole point is to tie the two together.* |
| TC-P2P-28b | **Given** the Ship dialog, **When** the document number is left blank, **Then** it is refused with a message naming **the number**; **When** the file is left off, **Then** the message names **the file**. ⛔ ⚠️ *Deliberately two separate errors — one is typing, the other is scanning, and a combined "paperwork missing" sends somebody to redo the half they had already done.* |
| TC-P2P-28c | **Given** a DN shipped with its paperwork, **When** the **Store Keeper**, **HOD**, **Logistics**, **QC** or the **warehouse** views it, **Then** the document number and a working download link appear **next to the delivery**, in all five places. ✅ |
| TC-P2P-28d | **Given** a DN shipped **before 2026-08-13**, **Then** the document column reads **"not shipped yet"** rather than a dead link. ⚠️ *Not a defect. No backfill was attempted, because inventing a document number for a delivery nobody scanned would be worse than admitting there isn't one.* |
| TC-P2P-28e | **Given** the Ship dialog, **Then** the **MTC upload is unchanged and still optional**. ⚠️ *A certificate covers the MATERIAL and is inherited PO → DN → warehouse → site so nobody uploads it twice; a delivery note covers THIS SHIPMENT and is inherited by nothing. Do not fuse them.* |

### 4.6 Reading scanned purchase documents (OCR)

| | |
|---|---|
| **Who** | HOD (purchase requests) · Logistics (purchase orders) |
| **What** | Upload supplier paperwork and have its lines read automatically |
| **Where** | The PR and PO creation screens; OCR Import |
| **When** | Instead of retyping forty rows by hand |
| **Why** | Retyping is slow and wrong. Also: the document itself is evidence and must be kept. |
| **Which** | **The lane is chosen by whether the file contains readable text, not by its file type.** A PDF is not evidence of text. |

**How:** upload the file, wait for the extraction, review the matched lines,
confirm, then create the PR or PO — which links the stored scan to the record.

| ID | Given / When / Then |
|---|---|
| TC-OCR-01 | **Given** a text-based PDF purchase request, **When** uploaded, **Then** the result is the **text** lane and the document number and lines are extracted. ✅ |
| TC-OCR-02 | **Given** a **scanned/photographed** PDF — printed, signed, scanned back in, containing zero text characters — **When** uploaded, **Then** the result is the **vision** lane with a job to poll. ✅ ⚠️ **This is the headline case.** Before Phase 6 such a file returned *success with zero items*, which is worse than an error: nothing distinguished "this order is empty" from "I could not read a word". |
| TC-OCR-03 | **Given** any upload, **Then** the file is **stored before parsing** and an attachment id is returned. **Verify the document is retrievable even when the parse fails** — the file that defeats the reader is the one someone needs to look at. |
| TC-OCR-04 | **Given** a stored PR scan, **When** the PR is created quoting that attachment, **Then** the PR links to it and the scan is reachable from the record. ✅ |
| TC-OCR-05 | **Given** a stored **PO** scan, **When** you offer it while creating a **PR**, **Then** it is refused and the message names the actual document type. ⛔ |
| TC-OCR-06 | **Given** a document with an unreadable quantity, **Then** that quantity comes back **blank, not zero**. ⚠️ **Assert this explicitly.** A zero on a purchase order looks like an answer and gets ordered as one. |
| TC-OCR-07 | **Given** a file over 15 MB, **When** uploaded, **Then** refused. ⛔ |
| TC-OCR-08 | **Given** a file that is neither a PDF nor an image, **When** uploaded, **Then** refused. ⛔ |
| TC-OCR-09 | **Given** the scanned-document reader switched off in Settings, **When** a scan is uploaded, **Then** it is **still stored**, and the message says so and tells you to enter the lines manually. |
| TC-OCR-10 | **Given** a PO scan uploaded by **Logistics** (who have no site), **Then** it is stored with no site and is **not** visible to a site-scoped user browsing the Document Library. ⛔ *Fail-closed: a null site matches no site filter.* |

### 4.7 Urgent reschedules

| | |
|---|---|
| **Who** | Logistics or Warehouse |
| **What** | Flag a delivery reschedule as urgent |
| **Where** | The reschedule dialog |
| **When** | When a delay cannot wait for the evening summary |
| **Why** | The default batches notifications to 4 p.m. so people are not pinged all day. Some things cannot wait. |
| **Which** | Normal (batched into the digest) · **Urgent** (sent immediately) |

| ID | Given / When / Then |
|---|---|
| TC-P2P-29 | **Given** a normal reschedule, **Then** the notification is held for the evening digest. |
| TC-P2P-30 | **Given** an **urgent** reschedule, **Then** it is sent immediately and bypasses the digest. ✅ |

---

## 5. Workflow C — Quality Control

**Read the two-gate distinction first.** It is the single most misunderstood
part of the system, and testing it wrongly produces confident false bug reports.

> 🔄 **CHANGED 2026-08-12 — the certificate gate MOVED.** It used to bind at
> warehouse goods-in and at Delivery Note creation. It now binds at **issue**,
> the same moment as the QC gate. If you are working from an older printout,
> TC-QC-03 through TC-QC-06 have been rewritten and their expected results are
> **inverted**. See §5.2.

| Gate | Binds at | Demands | Blocks whom |
|---|---|---|---|
| **Material Test Certificate** | Issue to the field | A certificate on file for that site | The Store Keeper |
| **QC approval** | Issue to the field | An inspected, approved quantity | The Store Keeper |

⚠️ **Material may be received and may travel with neither.** Do **not** raise a
bug because a warehouse booked in an uncertified Surface Shield, or because a DN
was allowed for material with no inspection and no certificate. Both are the
ruling. A receipt states that something physically arrived, and refusing to
record it does not un-arrive it — it just hides real stock from everyone. What
must never happen is either kind of unchecked material reaching a worker.

⚠️ **Two gates, two different people to chase.** A refusal at issue can be
*either* gate. A missing certificate is Logistics' to fix; a missing inspection
is the QC's. The clearance banner on the issue form names both, and a tester who
reports only "issue refused" has not finished the test — say **which** gate.

### 5.1 Scope: what is and is not controlled

| ID | Given / When / Then |
|---|---|
| TC-QC-01 | **Given** a material in the **Surface Shields** category (36 of 466), **Then** both gates apply. |
| TC-QC-02 | **Given** any other material, **Then** neither gate applies: no certificate is demanded, no inspection is opened, no issue is blocked. ✅ **Run this.** A quality gate that leaked onto the other 430 materials would halt the whole site. |

### 5.2 The certificate gate

| | |
|---|---|
| **Who** | Store Keeper (blocked); Logistics, Warehouse User or the SK (any may unblock) |
| **What** | A Material Test Certificate is mandatory before controlled material is **issued to a worker** |
| **Where** | Uploaded from Entry → Receive, or from the clearance banner on Entry → Issue; enforced at issue |
| **When** | At issue — **not** at receipt, and **not** at Delivery Note creation |
| **Why** | Refusing a *receipt* for missing paperwork does not stop the material arriving; it only stops the system knowing it arrived, so real stock sits in a yard invisible to the shelf report and to planning. The certificate protects the person putting the material on the job, so it is checked at the moment before it reaches them. |
| **Which** | Three ways to satisfy it, and any one is enough — see TC-QC-06 |

**The upload-once rule.** The person who hits this gate (the site SK) is usually
not the person holding the document (Logistics, who got it with the PO, or the
warehouse clerk, who got it with the delivery). A certificate attached to the
purchase order or to the delivery note is **inherited by the destination site**.
The SK should almost never need to upload one, and if testers find themselves
uploading a second copy of the same PDF, that is the bug.

| ID | Given / When / Then |
|---|---|
| TC-QC-03 | **Given** controlled material with **no** certificate, **When** the warehouse receives it, or a DN is created for it, or a site SK books it in, **Then** all three **succeed**. ✅ ⚠️ **Inverted 2026-08-12.** A refusal here is now the bug. |
| TC-QC-04 | **Given** that same uncertified material, **When** the SK tries to **issue** it, **Then** refused, and the message names all three ways to get a certificate on file. ⛔ |
| TC-QC-05 | **Given** the certificate is then uploaded, **When** the issue is retried, **Then** it succeeds. ✅ |
| TC-QC-06 | **Given** **Logistics** uploads the certificate against the **purchase order** (never touching the site), **When** the site SK issues, **Then** it succeeds and the clearance banner names the PO as the source. ✅ **Run this.** It is the whole point of the rule; without it three copies of one PDF end up in the system. |
| TC-QC-06b | **Given** the **warehouse** attaches the certificate to the **Delivery Note**, **Then** the receiving site inherits it the same way, and the banner names the DN. ✅ |
| TC-QC-06c | **Given** a certificate uploaded for site A, **When** site B issues the same material, **Then** site B is still **refused**. ⛔ *A certificate attests to one batch from one mill run. If one upload cleared every site, the gate would open once and never close again.* |
| TC-QC-06d | **Given** an **uncontrolled** material with no certificate, **Then** nothing is ever asked for, at any step. ✅ |
| TC-QC-06e | 🤖 **Given** a DN line, **Then** confirm the certificate resolves the material correctly. *DN lines carry a material code, not a part number; a lookup that only understood part numbers would silently match nothing on the DN path.* Test with a DN, not only with an issue. |
| TC-QC-06f | **Given** an uncertified Surface Shield is received anywhere, **Then** **Logistics is notified** to chase the document. ✅ *The block was traded for a chase-up. If the notification is missing, the ruling has silently deleted a control rather than moved it.* |

### 5.3 Inspecting

| | |
|---|---|
| **Who** | QC |
| **What** | Approve, partially approve or reject received material |
| **Where** | Quality → Inspections |
| **When** | After controlled material is received; before it can be issued |
| **Why** | Somebody qualified must confirm the material is what the certificate says |
| **Which** | The status follows from the **quantity you approve**, not from a separate choice |

| ID | Given / When / Then |
|---|---|
| TC-QC-07 | **Given** controlled material received, **Then** a **pending** inspection exists without anyone creating it. ✅ |
| TC-QC-08 | **Given** a pending inspection of 100, **When** the QC approves 100, **Then** the status is **approved**. |
| TC-QC-09 | **Given** a pending inspection of 100, **When** the QC approves 40, **Then** the status is **partially approved**. |
| TC-QC-10 | **Given** a pending inspection of 100, **When** the QC approves 0, **Then** the status is **rejected**. |
| TC-QC-11 | **Given** a rejected inspection, **Then** the material **stays in stock**, is marked unusable, and is **not** routed to Vendor Returns. ⚠️ **Explicit ruling.** An automatic vendor return removes the evidence before anyone has looked at it. |
| TC-QC-11a | **Given** a rejection of any quantity, **Then** a **Return No** (`QCR-YYYYMMDD-⟨inspection id⟩`) is minted, shown to the QC on decide, listed in the queue, and sent to **both the Store Keeper and the HOD**. *(new 2026-08-13)* ⚠️ *This does not overturn TC-QC-11: the Return No is an INVITATION for a human to raise a return, not an automatic vendor return. Nothing moves until the SK posts it and the HOD approves.* → §5.6 |
| TC-QC-12 | **Given** a decided inspection, **When** the QC decides it again, **Then** refused. ⛔ |
| TC-QC-13 | **Given** an inspection, **When** a **Store Keeper** tries to decide it, **Then** refused — reading the queue is open, deciding is not. ⛔ |
| TC-QC-14 | **Given** a **site-bound** QC, **When** they open Inspections, **Then** they see their site's and no other's. |
| TC-QC-15 | **Given** a **warehouse-bound** QC, **When** they open Inspections, **Then** they see their warehouse's, and **no site rows at all**. |
| TC-QC-16 | **Given** a QC account with **neither** binding, **When** they open Inspections, **Then** the list is **empty** — never everything. ⛔ *This is the fail-closed test. If it ever shows all sites, stop and report immediately.* |
| TC-QC-16a | **Given** any inspection, **Then** the queue **and** the inspect dialog show the **material NAME**, with the SAP and material codes beneath it. *(new 2026-08-13)* ⚠️ *The inspector was previously shown `1032` and asked to judge quality from it. The SAP code is the system's identifier, not the thing on the drum in front of them.* |
| TC-QC-16b | **Given** an inspection whose material has a certificate on file, **Then** the dialog shows the **certificate number** and an **Open certificate** link that downloads the actual file; the queue carries the same link. ✅ *It used to read "certificate #41" — which says one exists and gives no way to read it, so the approval was made against a document nobody had opened.* |
| TC-QC-16c | **Given** an inspection with **no** certificate, **Then** the dialog says so plainly and the download answers 404 rather than offering a broken link. |
| TC-QC-16d | **Given** a **warehouse-bound** QC, **When** they request the certificate of a **site** inspection by URL, **Then** **404**. ⛔ *The certificate is exactly as visible as the inspection that references it — scoping is inherited, not re-implemented.* |

### 5.4 The issue block — the hard gate

| | |
|---|---|
| **Who** | Store Keeper (blocked); QC (unblocks) |
| **What** | Controlled material cannot be issued beyond what QC has released |
| **Where** | Entry Log → Issue, and the HOD approval of a staged issue |
| **When** | At staging **and** again at approval |
| **Why** | Uninspected material must not reach a worker's hands |
| **Which** | Three distinct refusals, each naming its own numbers |

| ID | Given / When / Then |
|---|---|
| TC-QC-17 | **Given** controlled material with **no inspection**, **When** an issue is submitted, **Then** refused with *"no quality inspection exists for it at ⟨site⟩"*. ⛔ |
| TC-QC-18 | **Given** an inspection that is pending with nothing approved, **When** an issue is submitted, **Then** refused, and the message states **how many inspections are still pending**. ⛔ |
| TC-QC-19 | **Given** QC approved 40 and 40 is already issued, **When** 10 more is submitted, **Then** refused with the arithmetic spelled out: approved 40, issued 40, leaving 0, not enough for 10. ⛔ |
| TC-QC-20 | **Given** QC approved 40 and nothing issued, **When** 40 is issued, **Then** it succeeds. ✅ |
| TC-QC-21 | **Given** 40 approved and 40 **staged but not yet HOD-approved**, **When** another issue is attempted, **Then** refused. ⚠️ **Staged counts as issued.** Otherwise the same 40 units could be promised twice in the approval gap. |
| TC-QC-22 | **Given** a staged issue that passed the gate, **When** the HOD approves it, **Then** the gate is **checked again**. *Two gates, because time passes between staging and approval.* |
| TC-QC-23 | **Given** a site with 1,133 historical consumption rows predating quality control, **When** the first inspection approves 50, **Then** 50 is issuable. ⚠️ **Critical regression test.** If history counted against the approval, the site would be frozen forever by its own past. |
| TC-QC-24 | **Given** an over-issue of an **uncontrolled** material, **Then** it is still **allowed and logged**, exactly as before. ⚠️ The quality block did **not** convert FEFO or over-issue into hard blocks. Confirm this or a regression will hide here. |

### 5.5 QC accounts and transfers

| ID | Given / When / Then |
|---|---|
| TC-QC-25 | **Given** an HOD, **When** they create a QC account for their own site, **Then** it succeeds. ✅ |
| TC-QC-26 | **Given** an HOD, **When** they create a QC account for **another** site, **Then** refused. ⛔ |
| TC-QC-27 | **Given** a Warehouse User, **When** they create a QC for their warehouse, **Then** it succeeds. ✅ |
| TC-QC-28 | **Given** an HOD, **When** they request a QC transfer to another site, **Then** it is created as a **request**, and the QC has **not** moved. |
| TC-QC-29 | **Given** a pending QC transfer, **When** an **Admin** approves it, **Then** the QC's binding changes. ✅ |
| TC-QC-30 | **Given** a pending QC transfer, **When** the **HOD** tries to approve it themselves, **Then** refused. ⛔ *A QC whose remit is set by the person they inspect is not independent.* |
| TC-QC-31 | **Given** a decided transfer, **When** decided again, **Then** refused. ⛔ |

### 5.6 Returning what QC rejected *(new 2026-08-13)*

| | |
|---|---|
| **Who** | QC (raises the number) · Store Keeper (posts the return) · HOD (approves it) |
| **What** | Sending rejected material back, against the rejection that authorised it |
| **Where** | Quality → Inspections (the number) → Entry → Return Stock (the return) |
| **When** | After a partial or full rejection |
| **Why** | The SK used to be told "18 of 30 approved" and left to rebuild the return by hand — material, receipt, quantity and reason all retyped, and none of it linked back to the inspection that caused it |
| **Which** | The Return No **replaces** the source-receipt pick; everything else on the form still applies |

⚠️ **This section supersedes nothing in §5.4.** Rejected stock still sits in
stock and still cannot be issued. What is new is a documented way to send it
back, not an automatic one.

| ID | Given / When / Then |
|---|---|
| TC-QC-32 | **Given** a Return No from a rejection, **When** the SK pastes it into Return Stock and presses **Fetch**, **Then** the form fills itself: material, site, lot, the rejected quantity, and the inspector's own reason in Remarks. ✅ |
| TC-QC-33 | **Given** a fetched return, **When** the SK edits the quantity **downwards**, **Then** it is accepted. ⚠️ *Returning LESS than QC rejected is legitimate — some may already be issued, some still being argued about with the vendor. The rejection is a cap, not a fixed value.* |
| TC-QC-34 | **Given** a fetched return, **When** the SK enters **more** than was rejected, **Then** refused, naming the cap. ⛔ |
| TC-QC-35 | **Given** a fetched return, **When** it is posted **without a Return DN No.**, **Then** refused — **even if the site's entry-document setting is off**. ⛔ ⚠️ *Rejected material going back to a supplier is not something an operator convenience switch may wave through. Test this with `require_entry_documents` **off**, or you have not tested it.* |
| TC-QC-36 | **Given** the same, **When** it is posted with **no attached document**, **Then** likewise refused. ⛔ |
| TC-QC-37 | **Given** a complete QC return, **When** it is posted, **Then** it is staged for HOD approval like any other return, and on approval the quantity **leaves stock**. ✅ |
| TC-QC-38 | **Given** a Return No that has already been posted, **When** it is used a second time, **Then** refused. ⛔ ⚠️ **Run this.** One rejection is one return; a second would deduct the rejected quantity all over again and nothing about it would look wrong afterwards. |
| TC-QC-39 | **Given** a Return No from **another site**, **When** an SK or HOD there tries to fetch it, **Then** **404**. ⛔ *The number is a date plus a small integer and therefore guessable — unscoped, it would enumerate every rejection in the company.* |
| TC-QC-40 | **Given** a QC return, **Then** **no source receipt is required**. ⚠️ *Not a loosening. The rejection proves provenance better than a receipt pick does, and an inspection raised at a **warehouse** has no site receipt to point at — demanding one would show the SK an empty list they cannot get past.* |


---

## 6. Workflow D — Safety / PPE

### 6.1 The design you must understand before testing

**There is no PPE issue screen.** PPE goes out through the ordinary
Entry Log → Issue form, which grows three fields when a PPE item is selected.
There is also **no separate PPE stock ledger** — the quantity leaves through the
normal path.

> 🤖 **Agent note:** do not look for an "Issue PPE" page. Its absence is correct
> and deliberate; a second path would be a second set of rules to get wrong.

| ID | Given / When / Then |
|---|---|
| TC-PPE-01 | **Given** a PPE issue is completed, **When** you check site stock, **Then** it has fallen by the issued quantity through the **normal** ledger. ✅ **Run this first.** It is the negative property the whole design rests on: burn rate, reports, FEFO and the QC gate all keep working precisely because PPE is not special. |

### 6.2 Usable-time rules

| | |
|---|---|
| **Who** | Store Keeper or HOD |
| **What** | Say how long an item lasts before it should be replaced |
| **Where** | Safety & People → PPE Usable Time |
| **When** | Before issuing that item, ideally |
| **Why** | The expiry date on a distribution comes from here; with no rule there is no expiry |
| **Which** | A **global** rule, or a **site-specific** one. Where both exist, **the site's rule wins.** |

| ID | Given / When / Then |
|---|---|
| TC-PPE-02 | **Given** no rule for an item, **When** a rule of 90 days is created, **Then** it is listed. ✅ |
| TC-PPE-03 | **Given** a global rule of 90 days **and** a site rule of 30, **When** the item is issued at that site, **Then** the expiry is **30 days** out. |
| TC-PPE-04 | **Given** a global rule only, **When** issued at any site, **Then** the global rule applies. |
| TC-PPE-05 | **Given** an existing rule, **When** the same item and site are saved again, **Then** it **updates** rather than creating a duplicate. ⚠️ Two matching global rules would both apply and the winner would be arbitrary. |
| TC-PPE-06 | **Given** a rule, **When** it is deleted, **Then** subsequent issues of that item have **no expiry** and demand a Safety Approval again. |
| TC-PPE-07 | **Given** a Supervisor or Warehouse User, **When** they open PPE Usable Time, **Then** they cannot write to it. ⛔ |

### 6.3 Issuing PPE

| | |
|---|---|
| **Who** | Store Keeper |
| **What** | Issue safety equipment to a named person |
| **Where** | Entry Log → Issue — **the standard form** |
| **When** | Whenever PPE is handed over |
| **Why** | PPE is tracked against a **person**, not a site. Who is wearing what is the question this answers. |
| **Which** | Employee ID (always) · Safety Approval (unless a rule waives it) · early-replacement reason (only when replacing unexpired gear) |

| ID | Given / When / Then |
|---|---|
| TC-PPE-08 | **Given** a **non-PPE** item selected, **Then** no extra fields appear and the form behaves exactly as before. ✅ *Regression guard for the other ~450 materials.* |
| TC-PPE-09 | **Given** a PPE item, **When** no employee ID is given, **Then** refused: *"is PPE — name the employee receiving it"*. ⛔ |
| TC-PPE-10 | **Given** an employee ID not on the roster, **Then** refused: *"is not in the employee master"*. ⛔ |
| TC-PPE-11 | **Given** an **inactive** employee, **Then** refused, naming their actual status. ⛔ |
| TC-PPE-12 | **Given** an employee belonging to **another site**, **Then** refused: *"is at site ⟨X⟩, not ⟨Y⟩ — transfer them first if they have moved"*. ⛔ |
| TC-PPE-13 | **Given** an item whose rule requires a Safety Approval, **When** none is attached, **Then** refused. ⛔ |
| TC-PPE-14 | **Given** an attachment that is **not** a safety approval, **When** offered as one, **Then** refused, naming the actual document type. ⛔ |
| TC-PPE-15 | **Given** a worker holding the same item, **already expired**, **When** re-issued, **Then** allowed with **no** reason required. ✅ |
| TC-PPE-16 | **Given** a worker holding the same item, **not yet expired**, **When** re-issued with no reason, **Then** refused, and the message names the issue date and the expiry date. ⛔ |
| TC-PPE-17 | **Given** the same, **When** a reason is supplied, **Then** allowed, and the reason is stored. ✅ |
| TC-PPE-18 | **Given** a worker holding an item with **no expiry on record**, **When** re-issued, **Then** allowed with no reason. ⚠️ Something with no recorded expiry cannot be judged "early". |

### 6.4 Distribution lifecycle across approval

⚠️ **The distribution is written when the issue is *staged*, not when the HOD
approves it.** The boots are on the worker's feet at the moment the Store Keeper
hands them over. This is the subtle area — test it properly.

| ID | Given / When / Then |
|---|---|
| TC-PPE-19 | **Given** a PPE issue is staged, **Then** the distribution exists **immediately**, before HOD approval. |
| TC-PPE-20 | **Given** a staged PPE issue, **When** the same item is issued to the same worker again, **Then** the duplicate guard fires — **during the approval gap**, not only after it. |
| TC-PPE-21 | **Given** a staged PPE issue, **When** the HOD **approves** it, **Then** the distribution links to the committed consumption and stays active. |
| TC-PPE-22 | **Given** a staged PPE **replacement**, **When** the HOD **rejects** it, **Then** the new distribution is voided **and the previous one is restored to active**. ⚠️ **Test the restore, not just the void.** Without it the worker holds nothing on record while visibly wearing the old gear. |

### 6.5 The 15-day forecast

| | |
|---|---|
| **Who** | Anyone; acted on by an SK or HOD |
| **What** | What PPE to order in the next 15 days |
| **Where** | Safety & People → PPE Forecast |
| **When** | Weekly, before raising a PR |
| **Why** | Long enough to raise a PR and have it delivered; short enough to be a shopping list, not a wish list |
| **Which** | `suggested = expiring − on hand − already on order`, floored at zero |

| ID | Given / When / Then |
|---|---|
| TC-PPE-23 | **Given** distributions expiring inside 15 days, **Then** they are listed **with the names of the people**, not only quantities. ✅ *A column of numbers cannot be sanity-checked by a human.* |
| TC-PPE-24 | **Given** one unit expiring and 30 already on an open purchase order, **Then** the suggestion is **0**. ⚠️ **This is a correct answer, not an empty screen.** Verify the netting rather than reporting a bug. |
| TC-PPE-25 | **Given** a distribution expiring on day 16, **Then** it is **not** in the list. |
| TC-PPE-26 | **Given** **no usable-time rules configured**, **Then** the forecast is empty because nothing has an expiry. ⚠️ **Today's live state.** Configure a rule and issue an item before concluding the forecast is broken. |
| TC-PPE-27 | **Given** an expired item, **Then** **no WhatsApp alert is sent** to anyone, and the worker is **not** blocked from anything. ⚠️ **Explicit ruling:** expiry is a *suggested replacement date*, not a restriction. If an alert fires, that is the bug. |
| TC-PPE-28 | **Given** the forecast, **Then** the 90-day issue rate appears **beside** the suggestion and is not folded into it. |

---

## 7. Workflow E — Employees

| | |
|---|---|
| **Who** | HOD (transfers) · Admin (timeline) · everyone level 1+ (roster) |
| **What** | The roster, site transfers, and PPE history that follows the person |
| **Where** | Safety & People → Employees |
| **When** | On joining, moving, or when asked "who had this?" |
| **Why** | **The employee ID number is the person.** It is unique company-wide, and everything hangs off it. |
| **Which** | Transfer is **immediate** — no approval — because a person who has physically moved has already moved |

| ID | Given / When / Then |
|---|---|
| TC-EMP-01 | **Given** the roster, **When** a scoped role opens it, **Then** they see their own site's people. |
| TC-EMP-02 | **Given** an HOD, **When** they transfer an employee to another site, **Then** the change takes effect **immediately** and is recorded as a movement. ✅ |
| TC-EMP-03 | **Given** a Store Keeper, **When** they attempt a transfer, **Then** refused. ⛔ |
| TC-EMP-04 | **Given** a worker holding PPE, **When** they are transferred, **Then** **their PPE history moves with them**. ⚠️ **The headline test of the whole slice.** It works because history is keyed on the person, not the site. |
| TC-EMP-05 | **Given** a transferred worker, **When** PPE is issued at their **new** site, **Then** it is allowed; at their **old** site, refused. |
| TC-EMP-06 | **Given** an Admin, **When** they open an employee's timeline, **Then** every site they have worked at is listed with dates, plus what they currently hold. |
| TC-EMP-07 | **Given** an employee never transferred, **Then** their timeline is not an error — it shows their current placement. |
| TC-EMP-08 | **Given** Employees → Data Quality, **Then** unusable records are listed **with the reason** — a missing ID, a duplicate, an inactive worker still holding gear. |
| TC-EMP-09 | **Given** two employees, **When** you try to give them the same ID number, **Then** refused. ⛔ *The ID is the identity; a duplicate breaks every PPE record on both.* |

---

## 8. Workflow F — Daily site operations

Existing behaviour, but it is the substrate everything else runs on. Regressions
here are the most expensive kind.

| | |
|---|---|
| **Who** | Store Keeper (enters) · HOD (approves) |
| **What** | Receipts, issues, returns, adjustments, stock counts |
| **Where** | Entry Log |
| **When** | Daily |
| **Why** | Nothing moves without a record, and nothing is recorded without approval |
| **Which** | Every entry is **staged**, then approved or rejected. Approval is what makes it real. |

| ID | Given / When / Then |
|---|---|
| TC-OPS-01 | **Given** a receipt is submitted, **Then** it is staged and stock has **not** moved yet. |
| TC-OPS-02 | **Given** a staged receipt, **When** the HOD approves it, **Then** stock rises. ✅ |
| TC-OPS-03 | **Given** a staged entry, **When** the HOD rejects it, **Then** a reason is captured and stock is unchanged. |
| TC-OPS-04 | **Given** an issue for more than is in stock, **Then** it is **allowed and logged with a warning**, not blocked. ⚠️ **Standing rule.** The shelf is often right and the ledger often lags. Do not report this. |
| TC-OPS-05 | **Given** stock in several lots, **When** an issue is made, **Then** FEFO is applied and any deviation is **logged, not blocked**. ⚠️ Same standing rule. |
| TC-OPS-06 | **Given** a return, **When** submitted against a receipt, **Then** it is staged for approval. |
| TC-OPS-06a | **Given** a receipt **posted today** but **dated weeks ago** on the vendor's paperwork, **When** the SK opens Return Stock, **Then** it **is offered** as a source receipt. *(fixed 2026-08-13)* ⚠️ *This was the reported bug: the 30-day window was measured on the delivery date typed off the document, not on when the row entered the ledger. Goods received this morning were missing while older ones were listed. Both dates now qualify.* |
| TC-OPS-06b | **Given** the same list, **Then** the **most recently posted** receipts sort to the top. |
| TC-OPS-06c | **Given** a receipt that predates 2026-08-13, **Then** it still appears on its **delivery date** as before — nothing that used to be offered has been taken away. ✅ |
| TC-OPS-07 | **Given** an adjustment, **When** submitted without a reason code, **Then** refused. ⛔ |
| TC-OPS-08 | **Given** a stock count with variances, **When** staged, **Then** one adjustment per variance is created. |
| TC-OPS-09 | **Given** a Store Keeper, **When** they attempt to view another site's stock, **Then** they cannot. ⛔ |
| TC-OPS-10 | **Given** an entry with a **Location** recorded, **Then** it is treated as a reusable asset. ⚠️ **A blank Location means consumable, and that is the only test applied.** Do not expect the category or the part number to influence it. |

---

## 9. Workflow G — Returnable items: draining the model

This section is the worked example of **"draining the model"** — pushing one
feature through every state, boundary and combination it can reach, including
the ones it cannot. Use it as the template for §14.

| | |
|---|---|
| **Who** | Store Keeper |
| **What** | Lend a tool to a person and get it back |
| **Where** | Entry Log → Returnables |
| **When** | A tool leaves the store temporarily |
| **Why** | An unreturned tool is a loss nobody notices for months |
| **Which** | The model has exactly **two states: borrowed and returned** |

### 9.1 The model, stated honestly

Before testing, know what the model **does and does not** contain. Several
"obvious" test cases have no implementation to hit, and reporting them as bugs
wastes everyone's time. They are listed here as **⚠️ limitations to confirm**,
so that testing them produces a documented fact rather than a false defect.

| Concept | In the model? | What actually happens |
|---|---|---|
| Borrowed / returned | ✅ | Two statuses, nothing between |
| Expected return time | ✅ | Free-form date and time |
| Overdue detection | ✅ | Computed as expected time < now, on read |
| Borrower | ⚠️ **free text** | A typed name and phone number — **not linked to the employee roster**. Contrast PPE, which is keyed on the employee ID. |
| **Partial return** | ❌ **not modelled** | Marking returned returns the **whole** loan regardless of quantity |
| **Damaged on return** | ❌ **not modelled** | No condition is captured. Record it as a separate adjustment. |
| **Stock impact** | ❌ **none** | ⚠️ A loan does **not** decrement stock. The tool is tracked in the loan ledger only. |
| Extending a due date | ❌ | Not editable after creation |

> These are design facts as of 2026-08-09, confirmed against the code. If your
> operation needs partial returns or damage capture, that is a **feature
> request**, not a defect — raise it as one.

### 9.2 The happy path

| ID | Given / When / Then |
|---|---|
| TC-RET-01 | **Given** a Store Keeper, **When** a tool is loaned with borrower, quantity and due time, **Then** it is created as **borrowed**. ✅ |
| TC-RET-02 | **Given** a loan with a borrower phone number, **Then** the borrower is messaged directly with the due time. |
| TC-RET-03 | **Given** a borrowed loan, **When** marked returned, **Then** the status becomes **returned** and the borrower is sent a confirmation. ✅ |
| TC-RET-04 | **Given** a loan, **Then** site store keepers see an in-app entry for both the loan and the return. |

### 9.3 Draining it — every state and boundary

**Time boundaries**

| ID | Given / When / Then |
|---|---|
| TC-RET-05 | **Given** a due time in the future, **Then** the loan is not overdue and no alert fires. |
| TC-RET-06 | **Given** a due time **exactly now**, **Then** confirm which side of the boundary it falls on and that it is consistent between the list and the alert. |
| TC-RET-07 | **Given** a due time in the past, **When** the Returnables list is opened, **Then** an overdue alert fires. ⚠️ **The trigger is opening the list, not a background timer.** Nobody opens the page → nobody is alerted. Test it by opening the page, and note this in any report about "missing" alerts. |
| TC-RET-08 | **Given** an overdue loan already alerted, **When** the list is opened **again**, **Then** **no second alert** is sent. ✅ *Deduped deliberately — an alert that repeats on every page load trains people to ignore it.* |
| TC-RET-09 | **Given** an overdue loan with a borrower phone, **Then** the **borrower** is chased directly as well as the store. |
| TC-RET-10 | **Given** a loan returned **after** its due time, **Then** the return still succeeds. ⚠️ Being late does not block the return — confirm no penalty state exists. |
| TC-RET-11 | **Given** a due time in the **distant past** (e.g. last year), **Then** it behaves as any other overdue loan — no special casing. |
| TC-RET-12 | **Given** a due time far in the future (e.g. 2099), **Then** it is accepted. ⚠️ There is no sanity ceiling; confirm the current behaviour. |

**State transitions**

| ID | Given / When / Then |
|---|---|
| TC-RET-13 | **Given** a **returned** loan, **When** returned again, **Then** refused with *"already returned"*. ⛔ |
| TC-RET-14 | **Given** a loan at another site, **When** a Store Keeper marks it returned, **Then** refused: *"this loan belongs to another site"*. ⛔ |
| TC-RET-15 | **Given** a loan id that does not exist, **When** returned, **Then** a clean not-found, never a server error. ⛔ |
| TC-RET-16 | **Given** an unscoped caller, **Then** the list is **empty**, not global. ⛔ *Fail-closed.* |

**Quantity and partial return**

| ID | Given / When / Then |
|---|---|
| TC-RET-17 | **Given** a loan of 5 units, **When** marked returned, **Then** **all 5** are returned — there is no way to return 3. ⚠️ **Confirm this limitation** rather than hunting for a control that does not exist. |
| TC-RET-18 | **Given** the need to return 3 of 5, **Then** the documented workaround is to return the loan and create a new loan for the outstanding 2. Verify the workaround produces a coherent ledger. |
| TC-RET-19 | **Given** a quantity of 0, **When** a loan is created, **Then** record the behaviour. ⚠️ Likely accepted; a zero-quantity loan is meaningless and worth raising as a **suggestion** if so. |
| TC-RET-20 | **Given** a **negative** quantity, **Then** record the behaviour. If accepted, raise it — a negative loan is not a real state. |

**Damage and condition**

| ID | Given / When / Then |
|---|---|
| TC-RET-21 | **Given** a tool returned damaged, **Then** there is **no field to record it**. ⚠️ Confirm, and record the workaround: mark it returned, then raise a stock adjustment with a reason code, or update the asset's status if it is a serialised asset (§10). |
| TC-RET-22 | **Given** a tool **never** returned (lost), **Then** there is no "written off" state. ⚠️ It stays overdue indefinitely. Confirm, and note the workaround as above. |

**Data quality**

| ID | Given / When / Then |
|---|---|
| TC-RET-23 | **Given** a borrower name that does not match anyone on the roster, **Then** the loan is **still accepted**. ⚠️ Borrower is free text. Contrast with TC-PPE-10, where an unknown ID is refused — the asymmetry between the two features is real and worth knowing. |
| TC-RET-24 | **Given** a borrower name of 500 characters, **Then** confirm it is stored and that it does not break the list, the export or the printed sticker. |
| TC-RET-25 | **Given** a borrower name containing an apostrophe (`O'Brien`), **Then** it survives the list, the search and the download. |
| TC-RET-26 | **Given** a borrower name beginning with `=`, **When** the list is exported to Excel, **Then** the cell is **defused** so the spreadsheet does not execute it. → §11.3 |
| TC-RET-27 | **Given** a malformed due time, **Then** it is refused cleanly rather than stored as garbage. ⛔ |
| TC-RET-28 | **Given** a due time in a different timezone, **Then** it displays consistently in the list, the alert and the export. |

**Concurrency and scale**

| ID | Given / When / Then |
|---|---|
| TC-RET-29 | **Given** two store keepers marking the same loan returned simultaneously, **Then** one succeeds and the other gets *"already returned"* — never a double confirmation to the borrower. |
| TC-RET-30 | **Given** more than 500 loans at a site, **Then** the list is capped. Confirm the cap is visible to the user rather than silently truncating. |

---

## 10. Workflow H — Assets

| | |
|---|---|
| **Who** | Level 1+ registers and moves; the **source** HOD approves transfers |
| **What** | Serialised tools and equipment |
| **Where** | Assets |
| **When** | On registration, on movement, on a site change |
| **Why** | **One physical hammer, one row, company-wide.** The same serial cannot exist twice. |
| **Which** | Identity is **part number + serial number**, globally — not per site |

| ID | Given / When / Then |
|---|---|
| TC-AST-01 | **Given** a serial registered at site A, **When** the same part and serial are registered at site B, **Then** refused, and the message says **where it actually is** and what to do. ⛔ ⚠️ *The old message claimed it already existed "at your site", which was a lie once the thing was elsewhere.* |
| TC-AST-02 | **Given** a Store Keeper (level 0), **When** they attempt to register a unit, **Then** refused. ⛔ *All asset writes are level 1.* |
| TC-AST-03 | **Given** a unit, **When** a transfer to another site is requested, **Then** it is created as a request and the asset has **not** moved. |
| TC-AST-04 | **Given** a pending transfer, **When** the **source** site's HOD approves it, **Then** the site changes. ✅ *The site giving something up is the one that must agree.* |
| TC-AST-05 | **Given** a pending transfer, **When** the **destination** HOD tries to approve, **Then** refused. ⛔ |
| TC-AST-06 | **Given** an approved transfer, **Then** the old **rack assignment is cleared**. ⚠️ A shelf in one yard means nothing in another. |
| TC-AST-07 | **Given** an approved transfer, **Then** a movement is recorded, so "where has this been" answers for the leg between the sites. |
| TC-AST-08 | **Given** a decided transfer, **When** decided again, **Then** refused. ⛔ |
| TC-AST-09 | **Given** the transfers list, **When** opened, **Then** it returns the list — **not** an error about an invalid unit id. 🤖 *Regression guard: a literal path declared after a parameterised sibling is unreachable and answers 422.* |
| TC-AST-10 | **Given** the Assets page, **Then** columns do not overlap at narrow widths. |
| TC-AST-11 | **Given** a location update **indoors** where no GPS fix is available, **Then** the move still saves, without coordinates, and the UI explains why rather than failing silently. ⚠️ Over plain HTTP the browser refuses a position entirely — expected locally, not on the hosted address. |
| TC-AST-12 | **Given** the Excel asset sync, **When** it runs against existing units, **Then** existing status, rack and coordinates are **preserved**. ⚠️ **The workbook seeds; the app owns.** |

---

## 11. Workflow I — Reports, exports and documents

### 11.1 Reports

| ID | Given / When / Then |
|---|---|
| TC-RPT-01 | **Given** any report, **When** a scoped role runs it, **Then** only their site's rows appear. |
| TC-RPT-02 | **Given** any report, **When** exported to Excel, **Then** the header is on **row 6** and data begins on **row 7** (rows 1–4 logo and meta, row 5 title bar). ⚠️ Automation reading row 1 will fail — that is expected. |
| TC-RPT-03 | **Given** any report, **When** exported to PDF, **Then** columns **wrap** and nothing is truncated or drawn on top of a neighbour. |
| TC-RPT-04 | **Given** a long material description, **When** exported to PDF, **Then** it wraps rather than being cut. |

### 11.2 The manual PDFs

| ID | Given / When / Then |
|---|---|
| TC-RPT-05 | **Given** the manual build, **When** run with `--role all`, **Then** every booklet is produced and the geometry audit reports **0 overlapping text pairs** for each. ✅ |
| TC-RPT-06 | **Given** a table cell containing several lines of wrapped text, **Then** the following row starts **below** it, never on top of it. ⚠️ **This was the Phase 1 bug** — the measured row height disagreed with the drawn height by one line. |
| TC-RPT-07 | **Given** a table cell taller than a whole page, **Then** it splits across pages with the header repeated, rather than running off the bottom. |
| TC-RPT-08 | **Given** a code block wider than the page, **Then** it wraps inside its box rather than through the border. |
| TC-RPT-09 | **Given** a **QC** user, **When** they download their booklet, **Then** it exists and contains the QSEP chapter. ✅ *There was no QC booklet before Phase 6.* |
| TC-RPT-10 | **Given** a Store Keeper's booklet, **Then** it contains the QSEP chapter — the QC block and the PPE fields fire on **their** form, so the explanation must be in **their** booklet. |

### 11.3 Export safety

| ID | Given / When / Then |
|---|---|
| TC-RPT-11 | **Given** a remarks field containing `=HYPERLINK("http://evil","click")`, **When** exported, **Then** the cell is **defused** with a leading apostrophe and the spreadsheet does not execute it. ✅ |
| TC-RPT-12 | **Given** a cell containing the number `-5`, **When** exported, **Then** it is **not** defused and remains numeric. ⚠️ **Critical.** Defusing a negative number makes it parse as 0 in a total that still looks plausible. |
| TC-RPT-13 | **Given** a cell containing `-1+1`, **Then** it **is** defused — that is a formula, not a number. |
| TC-RPT-14 | **Repeat TC-RPT-11 on all three export writers** — CSV, and both Excel engines. They are separate libraries and the guard must be hooked into each. |

### 11.4 Documents

| ID | Given / When / Then |
|---|---|
| TC-DOC-01 | **Given** the Document Library, **When** a site-scoped user browses it, **Then** they see their site's documents and **not** documents with no site. ⛔ |
| TC-DOC-02 | **Given** an unlinked upload, **When** the uploader deletes it, **Then** it is removed; **when anyone else tries**, refused. ⛔ |
| TC-DOC-03 | **Given** a linked scan, **When** deletion is attempted, **Then** it is protected — the record depends on it. |

---

## 12. Workflow J — Notifications

| | |
|---|---|
| **Who** | Every role receives them |
| **What** | In-app bell, WhatsApp and email |
| **Where** | The bell in the header; the phone; the inbox |
| **When** | On significant actions; batched into a 4 p.m. digest unless critical |
| **Why** | An approval nobody notices is an approval that does not happen |
| **Which** | Immediate (critical) · batched (everything else) |

| ID | Given / When / Then |
|---|---|
| TC-NTF-01 | **Given** a PO is created for a PR, **Then** the raising HOD is notified. |
| TC-NTF-02 | **Given** goods are received against a PO, **Then** the relevant parties are notified. |
| TC-NTF-03 | **Given** a DN receipt is staged, **Then** the site is notified. |
| TC-NTF-04 | **Given** a vendor return is closed, **Then** the raiser is notified. |
| TC-NTF-05 | **Given** Delivery Notes are auto-drafted, **Then** the warehouse is told they are waiting and what to do next (add vehicle and driver, then submit). |
| TC-NTF-06 | **Given** a **critical** notification, **Then** it bypasses the evening digest. |
| TC-NTF-07 | **Given** a non-critical notification, **Then** it appears in the 4 p.m. digest, not immediately. |
| TC-NTF-08 | **Given** WhatsApp is unavailable, **Then** the **in-app notification still lands** and the underlying action still succeeded. ⚠️ **Notifications are best-effort and must never roll back the work.** Test by breaking the channel deliberately. |
| TC-NTF-09 | **Given** any notification, **Then** it also appears in the in-app bell. ⚠️ A warehouse-only dispatch once missed the in-app path entirely — check both channels, not just the loud one. |

---

## 12b. The Morning Briefing agent (Daily System Health)

| | |
|---|---|
| **Who** | Admin (all sites) and each HOD (their own site) receive it; anyone level 2+ can preview |
| **What** | A daily scan for operational problems, dispatched as one digest |
| **Where** | Sent to the bell and WhatsApp; previewed at *Daily System Health* |
| **When** | Automatically at 07:00 server time; on demand from the preview |
| **Why** | **Every problem it finds is the ABSENCE of an event.** A draft nobody submitted, an inspection nobody performed, a tool nobody returned. Nothing happens, so no ordinary notification fires, and the longer it stays broken the quieter it gets. |
| **Which** | Eight probes: uninspected controlled stock · negative stock · stale DN drafts · overdue loans · ageing approvals · stale PRs · expiring PPE · failed outbound messages |

⚠️ **A monitor's silence is a message, and the message is "nothing is wrong".**
That makes every way it can go quiet a correctness bug. The three tests below
are the ones that matter.

| ID | Given / When / Then |
|---|---|
| TC-HM-01 | **Given** the briefing, **When** one probe raises an error, **Then** the other seven still report **and** the digest carries a finding naming the broken probe. ⚠️ **The most important case here.** A monitor that dies on one bad query goes silent, and silence is indistinguishable from a healthy morning. |
| TC-HM-02 | **Given** a run with **no** findings, **Then** **nothing is dispatched** — but an audit row is still written. ⚠️ A daily "all clear" is read for a week and ignored forever; the audit row is how "did it run last night?" stays answerable without spending anybody's attention. |
| TC-HM-03 | **Given** a run triggered with *force*, **Then** even a clean briefing is sent — the one case where "all clear" is the message somebody actually wants, because they are proving the channel works. |
| TC-HM-04 | **Given** a scoped caller with **no site of their own**, **Then** the briefing contains no site data. ⛔ The one deliberate exception is the failed-message probe, which reports infrastructure counts with no row content. |
| TC-HM-05 | **Given** an HOD, **When** they preview, **Then** they get their own site; **When** they ask for another site, refused. ⛔ |
| TC-HM-06 | **Given** a store keeper, **When** they open the briefing, **Then** refused — it aggregates every site's operational state. ⛔ |
| TC-HM-07 | **Given** an HOD, **When** they try to *trigger a dispatch*, **Then** refused. A preview reads; a run writes to everybody's phone. ⛔ |
| TC-HM-08 | **Given** a draft 1 day old and one 30 days old with a 3-day threshold, **Then** only the 30-day one is reported. The probe filters **attention**; one that reports every draft is one nobody reads. |
| TC-HM-09 | **Given** a threshold changed in Settings, **Then** the probe honours it without a release. **And given** a malformed value, **Then** it falls back to the default rather than erroring — a typo in one settings row must not be why nobody hears about a week-old draft. |
| TC-HM-10 | **Given** any digest, **Then** the body is **one line**. Meta rejects a template parameter containing a newline, and the same body goes to WhatsApp and the bell — a multi-line body silently fails on one channel. |
| TC-HM-11 | **Given** more findings than fit, **Then** the digest ends with an explicit "(+N more)", never mid-sentence. |
| TC-HM-12 | **Given** findings of mixed severity, **Then** the worst are first — the top of a digest read on a phone is the part that matters. |
| TC-HM-13 | **Given** the feature switched off in Settings, **Then** nothing is sent, force included. An operator in a known incident can silence it without stopping the API. |
| TC-HM-14 | **Given** Surface Shields in stock with **no Material Test Certificate**, **Then** the briefing reports them, **and** a separate alert goes to the people who can act. *(new 2026-08-13 — the ninth probe.)* |
| TC-HM-15 | **Given** uncertified material **in a warehouse**, **Then** the alert reaches **Logistics, the Warehouse User and the warehouse's QC** — and nobody at a site. |
| TC-HM-16 | **Given** uncertified material **at a site**, **Then** the alert reaches that site's **Store Keeper, HOD and QC, plus Logistics** — and no other site. ⛔ |
| TC-HM-17 | **Given** a warehouse holding **nine** uncertified materials, **Then** each recipient gets **one** alert listing nine, not nine alerts. ⚠️ *Grouped by place. Per-material messages are how a real alert becomes something people filter.* |
| TC-HM-18 | **Given** the certificate is then uploaded, **Then** the alert **stops the next morning** with no further action. *This is a standing condition, not an event — it repeats daily until fixed, and that repetition is the design.* |
| TC-HM-19 | 🤖 **Given** the same material, **Then** the daily alert and the **issue refusal** must agree about whether a certificate exists. ⚠️ *Both read the same resolver. An alert that names material which is actually fine is one people learn to skip — and then the real one is skipped too.* |

⚠️ **Why the missing-MTC alert does not follow the briefing's own routing.** The
digest goes to admins and HODs. An HOD cannot obtain a certificate from a
supplier, and the store keeper who is about to be refused at the counter is not
on that list at all. Logistics appears on **both** location lists deliberately —
they are the only role who can actually get the document.

> **Automated:** service-test suite BS (19 checks) plus three Playwright cases.

## 13. Cross-cutting — RBAC, scoping and read-only

**Run this section after any change that adds an endpoint or a page.**

| ID | Given / When / Then |
|---|---|
| TC-SEC-01 | **Given** each of the eight roles, **When** they sign in, **Then** the sidebar shows exactly their pages — no more. |
| TC-SEC-02 | **Given** a role without access to a page, **When** they navigate to its URL directly, **Then** refused. ⛔ *The sidebar is a convenience; the server is the boundary.* |
| TC-SEC-03 | **Given** an **auditor**, **When** they attempt **any** create, update or delete anywhere in the product, **Then** refused. ⛔ ⚠️ **If you added an endpoint and it refuses an auditor, that is correct** — do not add it to the allowlist unless it genuinely changes nothing. |
| TC-SEC-04 | **Given** an auditor, **When** they read reports, records and dashboards across all sites, **Then** allowed. ✅ |
| TC-SEC-05 | **Given** a scoped role with **no** scope value, **Then** every list is **empty** — never global. ⛔ **The single most important security test in this guide.** Run it for store_keeper, supervisor, hod, warehouse_user and both QC axes. |
| TC-SEC-06 | **Given** an HOD, **When** they open the Logistics or Warehouse Portal, **Then** refused. ⛔ |
| TC-SEC-07 | **Given** Logistics, **When** they open the HOD Portal, **Then** refused. ⛔ *Rank does not grant a workspace.* |
| TC-SEC-08 | **Given** an Admin, **When** they open any workspace, **Then** allowed — the single deliberate exception. ✅ |
| TC-SEC-09 | **Given** any user, **When** they request another site's record by id directly, **Then** refused — not merely hidden from the list. ⛔ |

### 13.1 The strict role matrix (2026-08-12)

Pages used to be gated by a **seniority level**, and the roles are not a
ladder — they are four different jobs plus two oversight roles. `minLevel: 1`
admitted six of the eight roles, which is how seven roles ended up holding the
staff roster and how the store keeper ended up as the one role locked out of
the Stock page. Pages now **name the jobs** that need them.

⚠️ **The matrix is asserted automatically** — `tests/e2e/specs/rbac-matrix.spec.ts`
drives all eight roles against every page, through the shipped access functions.
Manual testing here is for judgement ("should a QC see this?"), not for coverage.

| ID | Given / When / Then |
|---|---|
| TC-SEC-10 | **Given** a **store keeper**, **Then** they can open **Dashboard** and **Stock**. ✅ ⚠️ **Inverted 2026-08-12** — they used to be bounced to their Issue page. The person holding the stock was the one role that could not open the screen named after it. |
| TC-SEC-11 | **Given** a **store keeper**, **Then** they can open the **Employees** roster. ✅ *They type an employee ID on every PPE issue and were the only role denied the list to type it from.* |
| TC-SEC-12 | **Given** a **warehouse user**, **a QC** or **Logistics**, **Then** the Employees roster is **refused**, in the menu and by the API. ⛔ **The privacy row.** Names and phone numbers; none of these three manages, moves or equips people. |
| TC-SEC-13 | **Given** a **QC inspector**, **Then** they see Stock, Inventory records, Inspections, Documents and their account — and **not** the Dashboard, Locator, Assets, PPE or Employees. ⛔ *An inspector's job is a queue.* |
| TC-SEC-14 | **Given** **Logistics**, **When** they open the **Warehouse** portal, **Then** allowed. ✅ *`/warehouse/*` has always accepted them server-side; the menu now agrees. Covering an unstaffed shed is real work.* |
| TC-SEC-15 | **Given** **Logistics**, **When** they call any **SME/Estimator** endpoint, **Then** refused. ⛔ *This was the reported leak: the sidebar showed them no SME page while the API served them every one.* |
| TC-SEC-16 | **Given** a **warehouse user**, **Then** they can browse **Purchase Orders**. ✅ *They receive goods against a PO and were phoning Logistics to have line quantities read out.* |
| TC-SEC-17 | **Given** **any** role, **When** they type a URL the system does not recognise, **Then** refused. ⛔ ⚠️ **Inverted 2026-08-12** — an unknown path used to be **allowed**. |
| TC-SEC-18 | 🤖 **Given** a new page is added with no entry in the navigation manifest, **Then** the build fails (`npm run test:nav`). *Failing closed turns a silent leak into a silent lockout; this is what makes it loud.* |
| TC-SEC-19 | **Given** an **auditor**, **Then** they still read the Estimator, the HOD pages and every record. ✅ **Run this.** Over-narrowing the oversight role is the failure mode of a tightening pass, and it stays quiet until an audit. |
| TC-SEC-20 | **Given** a **store keeper**, **When** they call `GET /receipts`, `/consumption`, `/returns`, `/lots` or `/purchase-requests` **directly**, **Then** refused. ⛔ *Hiding the menu row was never the control. Correctly scoping them to their own site's entire receipt history still handed them an oversight surface that is not theirs.* |
| TC-SEC-21 | **Given** a **warehouse user**, **a QC** or **Logistics**, **When** they call `GET /employees` directly, **Then** refused. ⛔ **This is the same table `/hr/employees` serves.** Narrowing one door and not the other closes nothing. |
| TC-SEC-22 | **Given** **Logistics**, **When** they download `/documents/master/employees` or an employee badge, **Then** refused. ⛔ *The roster as a spreadsheet is the worst of the four doors — it leaves the system entirely.* |
| TC-SEC-23 | **Given** a **store keeper**, **When** they try to print an employee badge, **Then** refused ⛔ — **but** they may still read a name from the roster ✅. *Reading one name to type an employee ID is not the same act as exporting the whole roster; the two are gated differently on purpose.* |
| TC-SEC-24 | **Given** **any** role, **When** they call `GET /inventory`, **Then** allowed. ✅ **Run this after any RBAC change.** It is the catalogue every entry form reads; a tightening pass that sweeps it up breaks issuing for the whole company. |
| TC-SEC-25 | **Given** **Logistics**, **Then** they can still edit **vendors** and **warehouses** ✅ but not **employees** ⛔. *The one master-data entity that is admin-only, so the privacy revocation is not undone by the editor next to it.* |

---

## 14. Edge cases, and how to drain any model

§9 is the worked example. Apply the same six passes to any feature you test.

### 14.1 The six passes

**1. State pass** — enumerate every state and every transition, including the
ones that should be impossible. For each: can I reach it twice? can I skip a
step? what happens if I go backwards? *Example: TC-RET-13, deciding a decided
inspection, approving an approved transfer.*

**2. Boundary pass** — for every number and date: zero, negative, one below,
exactly on, one above, absurdly large, empty, null. *Example: TC-PPE-25, a
distribution expiring on day 16 of a 15-day window.*

**3. Scope pass** — for every role: their own scope ✅, someone else's ⛔, no
scope at all ⛔ **empty, not global**. The third is the one that gets skipped and
it is the one that matters.

**4. Identity pass** — what happens when the thing you are naming does not
exist, is inactive, is a duplicate, or belongs to somebody else. *Example:
TC-PPE-10 through TC-PPE-12.*

**5. Interruption pass** — what if this fails halfway? Is the important half
kept and the convenient half discarded, or the other way round? *Example:
TC-P2P-21 — the goods receipt survives a failed delivery-note draft, which is
the correct direction.*

**6. Absence pass** — what does the feature do with **no data at all**? An empty
list, a report with no rows, a forecast with no rules. *This is where most false
bug reports come from* — see TC-PPE-26.

### 14.2 Text inputs — apply to every free-text field

| Input | What you are testing |
|---|---|
| Empty and whitespace-only | Is a space a valid name? |
| 500+ characters | Storage, list layout, PDF wrapping, sticker printing |
| `O'Brien`, `"quoted"` | Quote handling through search, export and display |
| `=1+1`, `+A1`, `-1+1`, `@SUM` | Spreadsheet formula defusing (§11.3) |
| `<script>alert(1)</script>` | Rendered as text, never executed |
| Arabic, accented and emoji characters | Storage, display, and PDF rendering |
| Leading/trailing spaces | Trimmed consistently, or matching silently fails |

### 14.3 The four highest-value edge cases in this system

If you only have an hour, run these:

1. **TC-SEC-05** — a scoped role with no scope must see **nothing**.
2. **TC-QC-23** — historical consumption must not block a fresh QC approval.
3. **TC-PPE-22** — rejecting a PPE replacement must **restore** the predecessor.
4. **TC-P2P-21** — a failed delivery-note draft must not roll back the goods receipt.

---

## 14b. Master data — lining-system codes (`LSC*`)

> Added 2026-08-18 (Phase 7, branch `feat/phase7-foundations`). Rule 13: this
> section ships with the change it describes.

The 2026-08 workbooks renumbered every `Lining_System_Code` from an integer
(`1`, `2`) to a string (`LSC1`, `LSC2`). Three readers of that column stopped
working **without failing**, which is what makes this section worth running by
hand: none of the three raised, none appeared in a log as an error, and two of
them reported success.

### 14b.1 What to test

| ID | Do this | Expected |
|---|---|---|
| **TC-SYS-01** | Sync the SME workbooks (`tools/pg_excel_sync.py --site CNCEC`, no `--commit`) | The plan reports a **non-zero** row count for equipment and recipes. A run that reports `0 inserts, 0 updates` plus a "skipped N rows" warning is the bug this replaced — it used to complete *successfully* having written nothing. |
| **TC-SYS-02** | Open the SME Execution Plan with systems LSC1, LSC2, LSC10, LSC11 present | Codes read **LSC1, LSC2, … LSC10, LSC11** — not LSC1, LSC10, LSC11, LSC2. |
| **TC-SYS-03** | Export the SME workbook (Session Report / Execution Plan) | Blocks are in the same order as the screen. Before the fix every code sorted equal, so block order was whatever the dict happened to hold. |
| **TC-SYS-04** | Put a `To_Be_Confirmed_LSC` row in the equipment sheet and sync | **That row alone** is skipped, and the warning names it as a *placeholder*. Every other row still lands. |
| **TC-SYS-05** | Sync a pre-renumbering workbook with numeric codes (`1`, `2`) | Still accepted, still ordered numerically. The change is additive — old files must not break. |

### 14b.2 The one that is not cosmetic

**TC-SYS-06 — allocation order.** `sme_engine.allocate()` walks each tag's
systems in code order and draws the material pool down as it goes, so **the
sort decides which system gets scarce stock first**. With a tag carrying LSC2
and LSC10 and not enough material for both, LSC2 must be served first. Lexical
order served LSC10 first and the shortfall landed on the wrong system — a wrong
*number*, not a wrong screen.

### 14b.3 Where the ordering is defined

Four places, and they must agree. Three are deliberate copies across trees that
must not import from each other:

* `backend/api/sme_engine.py` → `syscode_sort_key` — **the one implementation**;
  `sme._syskey` and `sme_export_layouts._code_sort_key` delegate to it
* `frontend/src/sme/engine.ts` → `syscodeSortKey` / `syscodeCompare` — the
  parity mirror; change it in the **same commit** or `npm run parity:sme` fails
* `legacy/database.py` → `syscode_sort_key` — legacy's copy (REPO_MAP forbids
  legacy reaching into `backend/`)

Suite **BY** asserts all three agree, and pins that a purely numeric dataset
still sorts exactly as it did before — which is why the parity golden did not
need regenerating.

---

## 14c. Execution sub-activity (`ESC*`) on the recipe line

> Added 2026-08-18 (Phase 7, branch `feat/phase7-foundations`). Rule 13.

`For_1_SQM.xlsx` now names an `Execution_Sub_Activity_Code` per benchmark line,
and it is part of the recipe line's **identity**: unique
`(Lining_System_Code, Execution_Sub_Activity_Code, Material_Code, SAP_Code)`.

### 14c.1 The number this split

Two LSC2 lines violated the old three-part key:

| System | Material | SAP | ESC21 (primer) | ESC22 (screed) | old merged value |
|---|---|---|---|---|---|
| LSC2 | GI-6002243 | 1049 | 0.2700 | 1.4674 | 1.7374 |
| LSC2 | GI-6002244 | 1050 | 0.1350 | 0.7326 | 0.8676 |

`plan_sme_recipes` did not reject that collision — it **summed** it as a
deliberate "coat merge". That was correct while a lining system was consumed as
a whole. It is wrong the moment a supervisor reports actuals against **one**
sub-activity: a correct primer draw measured against 1.7374 instead of 0.2700
reads as 15.5 % of benchmark — an apparent 84.5 % under-consumption that would
demand a written justification for a variance that does not exist.

### 14c.2 What to test

| ID | Do this | Expected |
|---|---|---|
| **TC-ESC-01** | Master Data → Recipes, add a line for a system/material/SAP that already exists, under a **different** ESC | Accepted (201). |
| **TC-ESC-02** | Repeat it under the **same** ESC | Refused 409, and the message names the sub-activity. |
| **TC-ESC-03** | Sync `For_1_SQM.xlsx` | 46 recipe rows, and LSC2/GI-6002243 appears **twice** — 0.2700 and 1.4674, never once at 1.7374. |
| **TC-ESC-04** | On a database upgraded but **not** reseeded, sync | Rows carrying `''` are **adopted** (ESC filled in place) and the sync says so. They must not be duplicated — an adopted row plus its sibling is two rows, not three. |
| **TC-ESC-05** | Leave a recipe line the workbook no longer names | Reported as "remain unclassified", never deleted. A recipe row is master data; a sync does not decide it is obsolete. |

> ⚠️ `''` is the "not yet classified" sentinel, deliberately **not** NULL:
> Postgres treats NULLs as distinct, so a nullable column in the unique
> constraint would stop the constraint constraining.

### 14c.3 Cutover data steps — the gap closed here

The cutover builds the schema from `models.py` with `create_all` and then
**stamps** `alembic_version` to head. That is right for schema, but it means no
migration ever *executes*, so every DATA step inside one was skipped: SAP comma
lists left un-normalised, the two `app_settings` keys absent, blank rows
unrepaired. A cut-over box came up schema-right and corrections-missing, and
nothing said so.

The contract: **a migration whose `upgrade()` carries DML exposes
`data_upgrade(conn)`, and `upgrade()` calls it.** Both paths then run the same
code — ordinary `alembic upgrade` through `upgrade()`, a cutover through
`cutover_migrate.run_data_migrations` — so they cannot drift. Every step must be
idempotent.

| ID | Do this | Expected |
|---|---|---|
| **TC-CUT-01** | Run a cutover | Phase [3] prints `data steps run: 5 (…)` after the stamp. |
| **TC-CUT-02** | Run it twice against the same target | Identical result, no error — every step is idempotent. |
| **TC-CUT-03** | Add a migration with an `UPDATE` in `upgrade()` and no `data_upgrade` | **Pre-flight refuses**, before a single byte is written. |
| **TC-CUT-04** | Check row-count parity after a cutover | `app_settings` shows an *expected post-load addition*, not a mismatch. A **shortfall** still fails — that is the direction no data step can cause. |

---

## 14d. Manpower benchmarks and the roster (Phase 3 + 4)

> Added 2026-08-18 (Phase 7, branch `feat/phase7-foundations`). Rule 13.

### 14d.1 A benchmark's identity is five parts

`sme_manpower_norm` is keyed on **Type + Lining_System_Code +
Execution_Sub_Activity_Code + Activity + Variant_Key**. Each part is there
because the real workbook breaks the shorter key:

| Part | What it separates |
|---|---|
| `Type` | LSC4/ESC41 and LSC5/ESC51 appear once for civil and once for mechanical |
| `Activity` | LSC10/ESC101 is ONE seal-coat code serving both PU systems — 70 m²/shift for 4 mm, 90 for 6 mm |
| `Variant_Key` | CV blasting is filed under ESC1 **twice**: 300 m²/shift with a crew of 4, and 40 with a crew of 2. Nothing else in the row differs. |

| ID | Do this | Expected |
|---|---|---|
| **TC-MP-01** | Import a workbook with two same-identity rows carrying **different** numbers | **Rejected**, and the message names `Variant_Key` as the fix. Keeping either row silently would plan a blasting crew against a benchmark 7.5× wrong. |
| **TC-MP-02** | Give those two rows distinct `Variant_Key` values and re-import | Both land. |
| **TC-MP-03** | Import a workbook with two **identical** rows (same identity, same numbers) | Collapsed to one, reported as an "identical repeat". The workbook really does list blasting twice. |
| **TC-MP-04** | Import the real `Manpower_Hour_Details.xlsx` | Block A only. Block B (rows 41-49, the day/night worked example) is skipped and the warning says so. |
| **TC-MP-05** | Look at Master Data → Manpower benchmarks | Blasting and Buffing carry a **manpower only** badge — no Surface Shield recipe exists for them. That is a category, not missing data. |

> ⚠️ Blasting rows carry `ESC1`/`ESC2` in the **Lining_System_Code** column.
> That is not a data error: blasting prepares a surface and belongs to no
> lining system. Those are the activities a supervisor opens without a store
> keeper.

### 14d.2 Roles

| ID | Do this | Expected |
|---|---|---|
| **TC-MP-06** | Rename or delete a **workbook** role | Refused (409). It is the vocabulary the benchmarks are written in — rename it in the workbook and re-sync. |
| **TC-MP-07** | Add a custom role, then delete it while a crew cites it | Add succeeds (code canonicalised, e.g. `scaffolder` → `SCAFFOLDER`); delete refused while in use. |
| **TC-MP-08** | Set a crew headcount to 0 | The role is **removed** from the crew, not stored as zero. |
| **TC-MP-09** | Put an unknown role code in a crew | Refused 422 — a typo would otherwise become an invisible zero in every plan. |

### 14d.3 Shifts and overtime

Both shifts are **12 physical hours** — 11 worked plus 1 hour lunch. `Shift`
records *which* one, never how long it is. Overtime starts at a threshold that
depends on the **worker**, not the shift:

| Worker type | OT after | 11 worked hours becomes |
|---|---|---|
| GI | 8 h | 8 normal + 3 OT |
| Non-GI | 10 h | 10 normal + 1 OT |

| ID | Do this | Expected |
|---|---|---|
| **TC-MP-10** | Post a 06:00→18:00 timesheet (60 min break) for a GI worker | 11 total, **8 normal + 3 OT**. |
| **TC-MP-11** | The same for a Non-GI worker | 11 total, **10 normal + 1 OT**. |
| **TC-MP-12** | A night shift 18:00→06:00 | Still 11 net — the midnight rollover is handled. |
| **TC-MP-13** | Change a threshold in Man-Hours → Overtime thresholds | New timesheets split at the new value. **Timesheets already posted are not re-split** — that would move overtime somebody has been paid for. |
| **TC-MP-14** | Corrupt `mh_ot_threshold_non_gi` to a non-number | Falls back to the default rather than failing every timesheet write. |
| **TC-MP-15** | As a store keeper, read or set `/mh/settings` | 403. The thresholds are the HOD's — deliberately *not* behind the admin gate, because the person accountable for the labour figures must be able to correct them. |

### 14d.4 ⚠️ Worker_Type had TWO legacy values

The ruling named `OWN` → `GI`. The column's vocabulary was **`OWN` | `Supply`**,
and `Supply` **is** the non-GI case (supplied labour, company DMC). Migrating
only `OWN` would have left a third value that every threshold lookup silently
misses, so both are mapped:

* `OWN` → `GI`
* `Supply` → `NON_GI`
* anything else → **left alone and reported**, never coerced. A worker silently
  reclassified is a worker paid against the wrong overtime threshold.

**TC-MP-16** — POST `/mh/employees` with `worker_type: "Supply"`. It is accepted
and stored as `NON_GI`. The attendance workbook still ships the old words; the
rename was ours, so the old spelling must not 422.

---

## 14e. The execution workflow — SK → Supervisor → HOD (Phase 5)

> Added 2026-08-19 (branch `feat/phase7-workflow`). Rule 13.

```
DRAFT_SK ─┐
          ├─→ PENDING_SUPERVISOR ─→ PENDING_HOD ─→ APPROVED
(bypass) ─┘                                     └─→ REJECTED
```

Three people hold three different pieces of knowledge, and the controls exist
so no one of them holds all of it.

### 14e.1 The separation of duties

| ID | Do this | Expected |
|---|---|---|
| **TC-EX-01** | As a **supervisor**, open an entry for an activity that consumes material | **409.** Somebody has to have counted what left the store. |
| **TC-EX-02** | As a **supervisor**, open a labour-only activity (blasting, buffing) | **201**, and it starts at `PENDING_SUPERVISOR` — the store keeper is skipped entirely. |
| **TC-EX-03** | As a supervisor, look at an entry's material lines | **Read-only.** You are measured against that consumption; the person it reflects on must not be able to tidy it. The API payload has no material field at all — the control is the shape of the request, not a runtime check. |
| **TC-EX-04** | As an **HOD**, approve an entry the supervisor has not filled in | **409.** No step may be skipped. |
| **TC-EX-05** | As SK or supervisor, try the HOD decision endpoint | **403.** |

### 14e.2 The two mandatory reasons

**TC-EX-06** — submit as supervisor with either reason blank → **422**, even at
zero variance.

> Why always: a reason demanded only past a threshold teaches people to aim
> just under it. A zero-variance entry carrying a stated reason is evidence the
> supervisor actually looked at the comparison.

### 14e.3 HOD edits cost a justification and a notification

| ID | Do this | Expected |
|---|---|---|
| **TC-EX-07** | As HOD, change a quantity and approve with no justification | **422**, and the message names *what* changed (`230 → 210`), not merely that something did. |
| **TC-EX-08** | Supply a justification and approve | Lands. `hod_edited` is true, `Original_Qty` still holds what the store keeper wrote. |
| **TC-EX-09** | Check the supervisor's bell | A `sme_exec_hod_edited` notification saying what changed and why. Without it they answer for numbers they never entered. |
| **TC-EX-10** | Decide an already-approved entry | **409** — it is final. |
| **TC-EX-11** | Reject with no reason | **422.** The supervisor has to know what to fix. |

### 14e.4 ⚠️ The benchmark is a SNAPSHOT, not a join

Every `Bench_*` column is copied onto the entry when the supervisor submits.

**TC-EX-12** — approve an entry, then edit the underlying
`sme_manpower_norm` productivity and the `sme_recipe` `For_1_SQM`. Re-open the
entry: **the variance is unchanged.**

> If it moved, the system would be rewriting history — last quarter's 12%
> overrun quietly becoming 4%, with no edit to the entry and nothing to point
> at. `Norm_ID` records *which* benchmark applied; the `Bench_*` columns record
> what it *said*.

### 14e.5 ⚠️ System-agnostic work stores `''`, not NULL

Blasting and buffing belong to **no lining system**. Tying their hours to one
would trap them there if the lining plan changed.

* The activity picker marks them `manpower_only` / `system_agnostic`; the UI
  **hides the lining-system field** and submits `''`.
* `''` is a real value. NULL would break the key (Postgres treats NULLs as
  distinct) and give every `GROUP BY` an untyped bucket that renders as a blank
  row. Same ruling already taken for `sme_recipe.Execution_Sub_Activity_Code`.
* The benchmark is still found — by **sub-activity**, because the workbook
  files blasting under `ESC1`/`ESC2` in its system column.

**TC-EX-13** — open a blasting entry, submit, confirm `Lining_System_Code` is
`''`, the material variance is **"not comparable"** (not 0%), and the manpower
benchmark still resolved.

> "Cannot compare" and "matched perfectly" must never render the same. A zero
> benchmark yields `None`, never a division by zero and never a green 0%.

### 14e.6 Known gap — two blasting benchmarks are not loaded

`Manpower_Hour_Details.xlsx` files **three** CV blasting rows under `ESC1`:
one at crew 4 / 300 m² per shift and two identical ones at crew 2 / 40. All
three currently carry the same `Activity` text, so the importer **rejects the
two low-productivity rows** rather than let one silently overwrite the other.

Until the workbook distinguishes them, a supervisor blasting for PU work will
be measured against the 300 m²/shift benchmark and show a large false variance.
The fix is one cell — see the sync's reject message, which names it.

---

## 14f. Variance reporting and the prep/lining split (Phase 6)

> Added 2026-08-19 (branch `feat/phase7-reporting`). Rule 13.

Four views over the same execution entries, in **Man-Hours & Labour**:
HOD Approval Queue · Actual vs Benchmark · Reason Audit Log · Surface Prep
Progress.

### 14f.1 ⚠️ Surface prep is NOT lining progress

| ID | Do this | Expected |
|---|---|---|
| **TC-VR-01** | Approve a **blasting** entry for 100 m² | `sme_surface_prep_progress.Done_SQM` gains 100. |
| **TC-VR-02** | Check `sme_sqm_progress` for that equipment | **Unchanged.** Blasting a tank is not lining it. |
| **TC-VR-03** | Approve a **lining** entry for 1,000 m² | `sme_sqm_progress.Done_SQM` gains 1,000, and surface prep gains nothing. |
| **TC-VR-04** | Open Surface Prep Progress | Coverage is prep area ÷ the **equipment's own** area. |

> `sme_sqm_progress.Done_SQM` drives Completion_Pct, SQM_Achievable_Now, the
> shortfall and the buy list. Folding prep into it would report a vessel as
> part-lined the moment it was cleaned. Coverage may legitimately exceed 100% —
> a surface can be re-blasted, and clamping it would hide rework.
>
> The test is the entry's **own** stored system code (`''`), not a lookup, so an
> entry opened as system-agnostic stays that way even if a recipe line for its
> sub-activity is added tomorrow.

### 14f.2 ⚠️ Totals sum absolutes — they never average percentages

**TC-VR-05.** Post two entries: **2 m²** drawing 8 KG against a 4 KG benchmark
(+100%), and **1,000 m²** drawing 2,000 KG against 2,000 KG (0%).

* Correct total: 2,008 actual ÷ 2,004 benchmark = **+0.2%**
* Averaging the two percentages gives **+50%**

If the header reads +50%, the report is averaging — and a programme that is 8%
over will report itself as on target.

### 14f.3 The reason audit log

| ID | Do this | Expected |
|---|---|---|
| **TC-VR-06** | As HOD, correct a quantity 8 → 5 with a justification | The log shows the justification **and** `8 → 5`. |
| **TC-VR-07** | Read a row where nothing was corrected | `Changed` is blank; the supervisor's two reasons still appear. |

> An audit line saying a quantity changed without saying from what is not an
> audit trail — which is why `Original_Qty` / `Original_Headcount` /
> `Original_Hours` are kept.

### 14f.4 ⚠️ RULE 12 — the exports carry free text

Every report exports through `reports.to_csv` / `to_xlsx`, which apply
`_defuse` / `xl_val`. These reports carry `Material_Variance_Reason`,
`Manpower_Variance_Reason` and `HOD_Edit_Justification` — **free text typed by
a supervisor and opened in Excel by an HOD**, exactly the shape the rule exists
for.

| ID | Do this | Expected |
|---|---|---|
| **TC-VR-08** | Put `=HYPERLINK("https://x/?"&A1,"Open")` in a variance reason, export CSV | The cell arrives **apostrophe-prefixed**; Excel shows the text and evaluates nothing. |
| **TC-VR-09** | Check a **negative** variance cell in the same export | **Not** prefixed. It must stay a number. |
| **TC-VR-10** | Request `?format=exe` | 422. |

> ⚠️ TC-VR-09 is the trap. Defusing `-5` would turn every negative subtotal into
> text and silently zero it in a GRAND TOTAL. A string that *is* a number is
> left alone; `-1+1` is not a number and **is** defused.
>
> Never hand rows to `csv.writer` or openpyxl directly in a new report.

### 14f.5 Access

| ID | Role | Expected |
|---|---|---|
| **TC-VR-11** | store keeper → `/execution/report/variance` | 403 |
| **TC-VR-12** | supervisor → `/execution/report/reasons` | 403 — the audit log is the HOD's and the auditor's |
| **TC-VR-13** | supervisor → `/execution/report/variance` | Allowed; they are accountable for these figures |

### 14f.6 A `null` variance is not a zero variance

**TC-VR-14** — a system-agnostic entry has no material benchmark. The material
variance must read **n/a**, never a green 0%. "Cannot compare" and "matched
perfectly" must never render the same.

---

## 14g. The manpower planner (Phase 7)

> Added 2026-08-19 (branch `feat/phase7-planner`). Rule 13.

**Man-Hours & Labour → 🧠 Manpower Planner.** It answers: to finish this job by
the deadline, how many of each role do I need, how many do I have, and what
should I hire? **It mutates nothing** — advice, never an assignment.

### 14g.1 The model, so the arithmetic can be checked

```
shifts     = deadline_hours ÷ 11          (11 worked hours in a 12-hour shift)
per person = threshold × shifts           NORMAL hours
           + (11 − threshold) × shifts    OVERTIME hours
           = 11 × shifts = deadline_hours (they reconcile)
```

`deadline_hours` is **hours available per person**, which is what makes
`headcount = man-hours ÷ deadline_hours` come out right.

### 14g.2 Worked example to check against

| ID | Do this | Expected |
|---|---|---|
| **TC-PL-01** | A job with 1,000 m² planned and 400 done | Remaining **600 m²**. |
| **TC-PL-02** | Two sub-activities at 660 and 330 man-hours per 300 m²/shift | 2.2 + 1.1 = 3.3 man-hrs/m² → **1,980 man-hours**. |
| **TC-PL-03** | Check the per-activity rows | They **sum** to the total — every activity must be done, so their hours add. |
| **TC-PL-04** | Crew 2 MASON : 1 HELPER on the first, all MASON on the second | MASON 1,540 · HELPER 440, summing back to 1,980. |
| **TC-PL-05** | Deadline 11 h | MASON required headcount = 1540 ÷ 11 = **140**. |

### 14g.3 ⚠️ Precision — man-hours per m² is derived, not read

The planner computes `Manhours_Per_Shift ÷ Standard_Productivity_Per_Shift`,
**not** the workbook's `SQ. Mtr/Hr./Person` column.

**TC-PL-06** — AR tile lining ships `0.13` in that column. The exact figure is
99 ÷ 13.33 = **7.427** man-hours/m²; the rounded one gives **7.692** — a 3.6%
overstatement on every tile plan. The rounded column is used only when the
exact pair is missing, and when neither exists the activity is **excluded with
a warning** rather than counted as free labour.

### 14g.4 Overtime, and why "prefer Non-GI" is arithmetic

With 5 GI + 5 Non-GI over an 11-hour window:

| | Calculation | Result |
|---|---|---|
| Normal capacity | 5×8 + 5×10 | **90** man-hrs |
| Overtime capacity | 5×3 + 5×1 | **20** man-hrs |
| Total | 10 × 11 | **110** man-hrs |

| ID | Workload | Expected |
|---|---|---|
| **TC-PL-07** | 99 man-hours | Feasible: 90 normal + **9 overtime**. |
| **TC-PL-08** | Clearing that 9 h | **1 Non-GI** (10 normal h) or **2 GI** (8 h each). Both shown side by side. |
| **TC-PL-09** | 33 man-hours | No overtime, no hiring advice. |
| **TC-PL-10** | 1,980 man-hours | **Not feasible** — and it says the deadline is unreachable rather than quietly reporting a plan. |
| **TC-PL-11** | Deadline 22 h | Capacity doubles to 180 normal. |

> Overtime is whatever will not fit inside **normal** capacity, so the way to
> reduce it is to raise that capacity. A Non-GI worker brings 10 normal hours
> where a GI brings 8 — 25% more. That is the entire basis of the
> recommendation; it is not a policy about who to employ. Thresholds come from
> the HOD's settings, not from constants.

### 14g.5 Surface prep

**TC-PL-12** — plan a tag with **no lining system**. The area comes from
`sme_equipment.Surface_Area_SQM` minus `sme_surface_prep_progress`, *not* from
lining progress, and only the system-agnostic benchmarks are used.

> ⚠️ "System-agnostic" is decided by **data**, not spelling: a benchmark is
> system-agnostic when **no recipe line names its system**. This first read as
> `NOT LIKE 'LSC%'`, which would silently plan any differently-named lining
> system as surface prep. Same test `/execution/activities` uses for
> `manpower_only` — one definition, two callers.

### 14g.6 ⚠️ Unmatched designations are reported, never assumed absent

The roster stores a free-text `Designation`; benchmarks cite a `Role_Code`.
Matching is case- and separator-insensitive on both the code and the printed
name.

**TC-PL-13** — give a worker a designation matching no role. They appear under
**Unmapped** with a warning, and are **not** counted as available.

> "Nobody wrote down that they are masons" and "there are no masons" call for
> completely different actions. Today **every active employee has a blank
> Designation**, so the available column reads 0 across the board until the
> roster is filled in — that is the warning working, not a bug.

### 14g.7 Guards

| ID | Do this | Expected |
|---|---|---|
| **TC-PL-14** | Deadline 0 | 422 — it refuses rather than dividing by zero. |
| **TC-PL-15** | Unknown equipment | 200 **with a warning**, never a confident zero. |
| **TC-PL-16** | Store keeper runs the planner | 403 — it is the HOD's tool. |

---

## 14h. Selection, not summation (Phase 8 · slice 8a)

> Added 2026-08-20 (branch `feat/phase8-planner-math`). Rule 13.

**⚠️ THE PLANNER'S NUMBERS CHANGE IN THIS RELEASE, DOWNWARDS, AND THAT IS THE
FIX.** Anything printed before 2026-08-20 overstates the labour required. Do
not reconcile a new plan against an old printout.

### 14h.1 What was wrong

The planner gathered every benchmark filed under a system code and **added
them up**. That is correct for *sequential* sub-activities — finishing a system
means doing the primer AND the screed AND the buffing — and wrong for
*alternative* benchmarks for **one** sub-activity, which compete. The workbook
has both shapes and nothing told them apart.

| Case | Why two rows exist | Was | Should be | Error |
|---|---|---|---|---|
| LSC4 / ESC41 | Same brick lining, filed once CV and once ME — identical crew, identical productivity | 13.1974 | 6.5987 | **2.00×** |
| LSC5 / ESC51 | The same, 63 mm | 16.0855 | 8.0427 | **2.00×** |
| LSC10 / ESC101 | One seal coat serving the 4 mm (70 m²/shift) and 6 mm (90 m²/shift) systems | 3.3524 | split | **2.29×** |
| Surface prep | Four blasting variants plus the steel one, all summed | 3.6967 | 0.1467 on plain concrete | **25×** |

### 14h.2 The three rules, in the order they are tried

| ID | Do this | Expected |
|---|---|---|
| **TC-SEL-01** | Plan a tag whose system is filed under both CV and ME (LSC4, LSC5) | **One** activity row, matching the **equipment's own Type** from the master. The discarded twin is listed under `benchmark_selection.rules_applied[].rejected`. |
| **TC-SEL-02** | Plan LSC10 on a tag carrying LSC8 and LSC9 | **Two** rows sharing the area in the **LSC8 : LSC9 ratio**. On J027 that is 982 : 2,565 → shares 0.2769 / 0.7231, and 5,614 man-hours rather than 11,891. |
| **TC-SEL-03** | Read the `why` on that rule | It names the systems and their areas. The split is derived from `Activity` text matching, so a new "PU lining 8 mm" system works with **no code change**. |
| **TC-SEL-04** | Create two benchmarks under one sub-activity that nothing distinguishes | The **dearest** is used, one row only, `needs_operator: true`, and a warning. **Never their sum** — overstating one benchmark is recoverable, silently doubling is not. |

### 14h.3 Surface prep is partitioned, not summed

Each surface on the tag is charged to the **one** benchmark that prepares it.

| ID | Do this | Expected |
|---|---|---|
| **TC-SEL-05** | Prep-plan a concrete tag (J027) | Floor & Wall for the plain systems, PU 4 mm for LSC8, PU 6 mm for LSC9 — **3,853** man-hours, not the old 31,817. |
| **TC-SEL-06** | Prep-plan a steel tag (513-37213-AGI-501) | Everything routes to **Blasting Steel Surface** because the equipment is `Type = ME`. The word "steel" in the benchmark name is *not* what decides it. |
| **TC-SEL-07** | Check the Floor & Wall row on a tag with four plain systems | **One** row carrying the summed area and all four codes in `Applies_To`, not four identical rows. |
| **TC-SEL-08** | Read `benchmark_selection.surface_prep_partition` | One entry per system: its area, its Type, the benchmark chosen and **why**. |

### 14h.4 Topcoats are blasted once

**TC-SEL-09** — LSC10's area on every tag equals LSC8 + LSC9 **exactly**
(J027: 982 + 2,565 = 3,547). It is the seal over both, so the concrete beneath
it is blasted once, before the screed. LSC10 is therefore **excluded from the
prep area**, and the exclusion is reported with the arithmetic that justified
it.

**TC-SEL-10** — the test is directional and needs **both** halves: the code's
benchmarks must name the systems it covers, **and** its area must equal their
sum. When the areas disagree the exclusion is **refused** and reported — an
area that does not add up is a data question, not a licence to drop a surface.

### 14h.5 ⚠️ Overlapping surfaces are reported, NOT deduplicated

**TC-SEL-11** — J027 files LSC1 and LSC2 at **504 m² each against an identical
`Lining_Area_Location`**. Physically that is one 504 m² surface carrying two
systems. The plan publishes both figures —

```
gross_sqm 5,059    deduplicated_sqm 4,555    double_counted_sqm 504
```

— **uses the gross**, and warns. Whether a surface carrying two systems is
blasted once or twice is an operator ruling that has not been made. Nothing is
assumed here.

### 14h.6 The sync now reports rows that left

**TC-SEL-12** — rename a benchmark's `Activity` in `Manpower_Hour_Details.xlsx`
and run `tools/pg_excel_sync.py --dry-run --kinds sme-manpower`.

`Activity` is part of the five-part identity, so a **rename is an insert**: the
new row appears and the old one stays. Before this release the run reported
`+1 ~0 rejected=0` and mentioned the leftover nowhere, because there was no
pass for rows that *vanish* from the workbook. It now prints them:

```
⚠ 1 row(s) in the database that this workbook does not name — NOT deleted:
  · id 49  CV/ESC1/ESC1  'Blasting Civil PU Area'  crew 3.0 @ 40.0 /shift
```

**TC-SEL-13** — confirm the dry run **did not delete it**. Reporting is not
pruning; an operator who imports a partial sheet by mistake must not lose the
rows it omits.

**TC-SEL-14** — alembic `d4b8c1e63a27` deletes the one known leftover
(`Blasting Civil PU Area`, superseded by `Blasting Civil PU 4mm Area`). Re-run
it: it is idempotent and prints *"nothing to do"*. It names one identity and
does **not** prune orphans in general.

### 14h.7 Crew-shifts — a free self-check

**TC-SEL-15** — every activity now publishes `Crew_Shifts`, and the two ways of
computing it must agree:

```
sqm ÷ Standard_Productivity_Per_Shift  ==  man-hours ÷ Manhours_Per_Shift
```

They are algebraically identical, so a disagreement means a corrupt benchmark
row. Suite CE asserts it on every activity in a plan.

> **This is WORKLOAD, not elapsed time** — how many shifts of the benchmark
> crew the job contains, independent of who you deploy. Elapsed shifts are
> `man-hours ÷ (deployed headcount × 11)` and coincide only when the crew you
> send is the benchmark crew.

---

## 14i. Many jobs, one deadline (Phase 8 · slice 8b)

> Added 2026-08-21 (branch `feat/phase8-planner-ux`). Rule 13.

### 14i.1 ⚠️ Stacked surfaces are now blasted ONCE

Operator ruling 2026-08-21 (Q13). Slice 8a *reported* the overlap and planned
on the gross because nobody had said which reading was right. The answer is
that a surface carrying two systems is prepared once, so the deduplicated
figure is now the one the plan uses.

| ID | Do this | Expected |
|---|---|---|
| **TC-DEDUP-01** | Prep-plan J027 | **4,555 m²**, not 5,059. LSC1 and LSC2 both claim 504 m² at an identical `Lining_Area_Location` — one surface, two systems. |
| **TC-DEDUP-02** | Read the warning | It names the codes, the shared area and both totals, so a plan can still be reconciled against one printed before the ruling. |
| **TC-DEDUP-03** | Check `benchmark_selection.surface_prep_partition` | The merged surface appears **once**, with `codes: [LSC1, LSC2]` and `merged: true`. |
| **TC-DEDUP-04** | Two systems on the same location routing to *different* blasting variants | The **dearest** is charged. Nothing in the data says which coat went on first, and overstating one surface is recoverable where understating it is not. |

> **The test is exact match on BOTH location and area, deliberately.** Partial
> overlaps exist — LSC6 covers "Pedastal Wall Side surface, Wall" while LSC1
> covers that *and* "Floor" — and no arithmetic can say how much of one lies
> inside the other. Merging on a partial match would silently drop real area.

### 14i.2 Multi-select: the intersection, never the cross product

| ID | Do this | Expected |
|---|---|---|
| **TC-MS-01** | Select 3 equipment × 2 codes where only 3 pairs exist | **3 jobs.** A cross product would invent work on pairs nobody planned. |
| **TC-MS-02** | Read the warnings | The dropped combinations are **named**, not silently omitted. |
| **TC-MS-03** | Select equipment and **no** system code | Every system on that equipment is planned. The filter says "All systems on the selected equipment", so empty has to mean all — returning nothing was a dead end the UI promised against. |
| **TC-MS-04** | Turn **Surface prep** on for a tag with six systems | **One** prep job for the tag, not six. Prep is per equipment. |
| **TC-MS-05** | Open the Equipment dropdown → **Select all** | Resolves to the real value list, not a sentinel — the tag count is the number of equipment. |

### 14i.3 Target Days, and the reverse calculation

```
deadline_hours = target_days × 11        (11 worked hours in a 12-hour shift)
Total_Required_Headcount = man-hours ÷ (target_days × 11)
Headcount_Per_Shift      = Total_Required_Headcount ÷ shifts_per_day
```

| ID | Do this | Expected |
|---|---|---|
| **TC-DAY-01** | Target days **5** vs Hours per person **55** | **Byte-identical** plans. A person works one shift a day, so 5 days is 55 hours. |
| **TC-DAY-02** | Send both in one request | **422.** They are the same quantity; silently preferring one hides a contradiction. |
| **TC-DAY-03** | 900 man-hours at 5 days | Total headcount **16.36 → 17**. |
| **TC-DAY-04** | Check the KPI row | Man-hours · **Crew-shifts** · **Days** · **Calendar shifts** · Days at current roster. |
| **TC-DAY-05** | Compare Crew-shifts to Days | They are different questions. Crew-shifts is WORKLOAD — shifts of the benchmark crew, independent of who you deploy. Days is the deadline. |

### 14i.4 ⚠️ Two shifts SPLIT the crew — they do not halve the hiring

**TC-SHIFT-01** — plan at 5 days with 1 shift, then with 2.

| | 1 shift | 2 shifts |
|---|---|---|
| Total headcount | 17 | **17** (unchanged) |
| Per shift | 17 | **9** |
| Days | 5 | 5 |
| Calendar shifts | 5 | 10 |

Nobody works both a day and a night shift, so two crews need the **same** total
people. The natural reading — "two shifts, so half the people" — under-hires by
half, which is why the page states it in a banner rather than leaving it to be
inferred. **If that banner is ever tidied away, the E2E fails.**

**TC-SHIFT-02** — auto mode reads the roster: two shifts if anyone *in a role
this job needs* is on nights, else one. An idle night blaster does not put a
brick-lining job onto two shifts.

**TC-SHIFT-03** — forcing two shifts with **no** night crew is allowed (operator
ruling Q6) and warns that the split shown is one you would have to staff.

### 14i.5 The per-role dashboard

**TC-ROLE-01** — each role is a collapsible row: `need / have / assign` in the
header, and expanding shows the GI–Non-GI–Day–Night split plus **which jobs
asked for that role**, so a headline number can always be decomposed.

**TC-ROLE-02** — the per-role man-hours still sum back to the total across every
selected job. Selection and aggregation must not lose hours.

### 14i.6 The job label, and CV/ME

**One assembler, in `backend/api/services/jobs.py`.** The API ships the label;
the frontend renders it. A mirrored TypeScript formatter would have been the
third dual-implementation surface after the SME engine and the sort key, and a
label does not earn that machinery.

| ID | Do this | Expected |
|---|---|---|
| **TC-LBL-01** | Look at any job | `J027 · LSC9 [CV] — Polyurethane Resin Acid Resistant 5mm` |
| **TC-LBL-02** | Look at surface prep | `J027 · Surface prep [CV]` — named, never a blank cell. |
| **TC-LBL-03** | Check where the name came from | `sme_recipe."Lining_System"` — the column the operator edits. **NOT** `Lining_System_Name`, which despite its name holds the short code (`RLCB4`, `CBL30`). |
| **TC-LBL-04** | LSC3, which ships `Rubber Lining  4mm` on one row and `Rubber Lining 4mm` on another | One name. Whitespace is collapsed; two spellings would render as two systems. |

**⚠️ CV/ME is a property of the (tag, code) ROW, not of the code.** `LSC1` is CV
on nine concrete rows and ME on nineteen tank/vessel rows.

| ID | Where | Expected |
|---|---|---|
| **TC-CVME-01** | A row that IS one tag + code (Total Overview, Execution Plan, the planner, the execution queues) | That row's **exact** discipline: `LSC1 [ME]`. |
| **TC-CVME-02** | An aggregate (the planner's code filter, a code rollup) | **Both**: `LSC1 [CV/ME]`. |
| **TC-CVME-03** | Anywhere | Never one Type picked from the first row met and presented as the code's discipline. That is an invented aggregate — it reads as fact and is wrong half the time. |

---

## 15. Do's and Don'ts

### Do

- **Do state the business reason** before writing a case. If you cannot, you are
  probably testing an implementation detail that is free to change.
- **Do treat a refusal as a result.** Assert the status code *and* the message.
- **Do test the empty case.** No rules, no data, no scope.
- **Do test the "no scope" case for every role.** It must be empty, never global.
- **Do run the whole section** after touching anything in it — these features
  interlock, and the QC gate sits inside the issue path.
- **Do check both notification channels**, not just the loud one.
- **Do record limitations as confirmed facts** (§9.1) rather than as defects.
- **Do cite the test ID** in any bug report.
- **Do re-read §5's two-gate table** before reporting anything about QC.

### Don't

- **Don't report FEFO or over-issue warnings as bugs.** They are allow-and-log by
  standing rule, and the QC block did not change that.
- **Don't report that a DN was allowed for uninspected or uncertified material.**
  That is the ruling — since 2026-08-12 **both** gates bind at **issue**, and
  neither binds at receipt or at dispatch. *(This line still said "the
  certificate binds at dispatch" until 2026-08-13; it was describing the rule
  the operator moved.)*
- **Don't report an empty PPE Forecast** without first checking whether any
  usable-time rules exist. Today, none do.
- **Don't report a suggestion of 0** when stock or an open order already covers
  the need — that is the arithmetic working.
- **Don't report a rejected inspection "not going to Vendor Returns".** Explicit
  ruling: it stays in stock, unusable.
- **Don't report an expired PPE item not alerting anyone.** Explicit ruling:
  expiry is a suggestion, not a restriction.
- **Don't assert on HTML structure or CSS classes.** Assert on behaviour.
- **Don't run this guide against production.**
- **Don't renumber a test case.** Bug reports reference the IDs.
- **Don't skip the negative-property tests** (TC-PPE-01, TC-QC-02, TC-PPE-08).
  They prove a new feature did not leak into everything else, and nothing else
  will catch that.
- **Don't report a DN shipped before 2026-08-13 showing "not shipped yet"** in
  the delivery-document column. No backfill was attempted on purpose —
  inventing a document number for a delivery nobody scanned would be worse than
  admitting there isn't one.
- **Don't report that a QC rejection did not raise a return by itself.** The
  Return No is an invitation for a human to raise one; nothing moves until the
  Store Keeper posts it and the HOD approves. TC-QC-11 still stands.
- **Don't report the missing-MTC alert repeating every morning.** It is a
  standing condition, not an event, and it stops the day the certificate is
  uploaded.
- **Don't report that a QC return skipped the source-receipt picker.** The
  rejection is stronger provenance, and a warehouse-raised inspection has no
  site receipt to point at.
- **Don't run the backend suite expecting to inspect its rows afterwards in
  your dev database.** Since 2026-08-13 it runs against `gihub_svctest` and
  your database is never opened. Connect to the test database instead.

---

## 16. Testing FAQ

**Q: The PPE Forecast is empty. Is it broken?**
Almost certainly not. With no usable-time rules configured, nothing has an
expiry date, so nothing can appear. Create a rule, issue the item, re-check. See
TC-PPE-26.

**Q: A forecast row suggests ordering 0. Is that a bug?**
No. The suggestion nets what you hold and what is already on order. If 30 are on
an open purchase order and 1 is expiring, 0 is the right answer. TC-PPE-24.

**Q: Material reached site without an inspection. Should I report it?**
No. That is the operator's ruling. Material may travel uninspected; it may not
be *issued* uninspected. TC-QC-02 and §5.

**Q: Why did the issue form refuse me with a message about quantities?**
QC has approved less than you are trying to issue, and staged-but-unapproved
issues count against the approval. TC-QC-19 and TC-QC-21.

**Q: A rejected inspection left the material in stock. Bug?**
No — explicit ruling. It stays, marked unusable and blocked from issue, until a
person decides. TC-QC-11.

**Q: I can't find the "Issue PPE" page.**
There isn't one, deliberately. PPE goes out through the standard Issue form,
which grows extra fields when a PPE item is selected. §6.1.

**Q: Can I return part of a loaned quantity?**
No — not modelled. Return the loan and create a new one for the outstanding
quantity. TC-RET-17 and TC-RET-18.

**Q: A tool came back damaged. Where do I record that?**
Nowhere on the loan. Mark it returned, then either raise a stock adjustment with
a reason code or update the asset's status if it is serialised. TC-RET-21.

**Q: An overdue tool never alerted anyone.**
Overdue alerts fire when the Returnables list is **opened**, not on a timer.
Open the page. TC-RET-07.

**Q: My new endpoint refuses an auditor. Did I break something?**
No — that is the design. View-only is enforced by method, so anything new is
closed by default. Only add it to the allowlist if it genuinely changes nothing.
TC-SEC-03.

**Q: The Excel export has its header on row 6, not row 1.**
Correct. Rows 1–4 are the logo and meta band, row 5 the title bar. TC-RPT-02.

**Q: A cell in my export starts with an apostrophe that I did not type.**
That is formula defusing. Delete the apostrophe in your own copy if you want the
plain text. Numbers are never defused. TC-RPT-11 and TC-RPT-12.

**Q: How does this guide relate to the automated tests?**
They cover different risks. The automated gates (§17) prove the system still
does what it did; this guide proves it does what the business needs. Both are
required, neither replaces the other.

**Q: Do I need to run the whole guide every time?**
No. Run the section you changed, plus §13 (RBAC) if you added an endpoint or a
page, plus §14.3 (the four highest-value cases). Run the whole guide before a
release.

---

## 17. Reporting a bug, and the automated gates

### 17.1 Bug report template

```
Test case:      TC-QC-19
Role / account: store_keeper at CNCEC
Environment:    local / staging / (never production)
Steps:          1. ...
                2. ...
Expected:       (quote the Then clause from this guide)
Actual:         (what happened, with the exact message and status code)
Evidence:       screenshot / response body / audit log entry
Checked:        the Don'ts in §15 — this is not a documented ruling
```

The last line matters. A third of reported defects in this system have been
documented rulings.

### 17.2 The automated gates

Run these before and after any change. **A change that lowers any of them is a
regression, not a new normal.**

| Gate | Baseline | Command |
|---|---|---|
| Backend service tests | **1502 / 0** | `GI_DOTENV=0 .venv/bin/python -m backend.api.service_tests` |
| Legacy regression | **599 / 0** | `.venv/bin/python legacy/bug_check.py` |
| Playwright E2E | **90 / 90** | `cd tests/e2e && npm test` |
| SME TS↔PY parity | **1,313 comparisons** | `npm run parity:sme --prefix frontend` |
| SME UI math | **33 / 0** | `npm run test:ui-math --prefix frontend` |
| Navigation route coverage | **46 routes, all claimed** | `npm run test:nav --prefix frontend` |
| Frontend build | clean | `npm run build --prefix frontend` |
| Manual PDFs | **0 overlapping text pairs** | `.venv/bin/python build_manual_pdf.py --role all` |

> 🔄 **The backend suite no longer touches your database (2026-08-13).** It
> builds and runs against its own throwaway `gihub_svctest`, rebuilt from
> `gi_database.db` before the engine is created, because suites B…BX commit
> through the real ASGI app and cannot be rolled back. Running the tests used
> to leave thousands of rows — audit entries, mock PRs, notifications, test
> users — in whatever database `DATABASE_URL` named, which locally was the live
> one. `DATABASE_URL` now supplies only the cluster; its database is never
> opened, and provisioning **refuses to run** if the two resolve to the same
> name. Suite BW asserts all of this. `GI_TEST_DB=off` restores the old
> behaviour for debugging a failure that only reproduces against live data.

---

**End of guide.** Keep it current — see `PROJECT_HANDOVER.md` rule 13.
