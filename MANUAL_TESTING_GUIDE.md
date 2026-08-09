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
| TC-P2P-27 | **Given** a DN containing a **controlled** material, **When** it is created **without a Material Test Certificate on file**, **Then** it is refused. → §5.2 ⛔ |
| TC-P2P-28 | **Given** a mixed R/L and B/L line set, **When** one DN is attempted for both, **Then** it is refused. ⛔ |

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

| Gate | Binds at | Demands | Blocks whom |
|---|---|---|---|
| **Material Test Certificate** | Delivery Note creation | A certificate on file | The warehouse clerk |
| **QC approval** | Issue to the field | An inspected, approved quantity | The Store Keeper |

⚠️ **Material may travel to site uninspected.** Do **not** raise a bug because a
DN was allowed for material with no inspection — that is the ruling. What must
never happen is uninspected material reaching a worker.

### 5.1 Scope: what is and is not controlled

| ID | Given / When / Then |
|---|---|
| TC-QC-01 | **Given** a material in the **Surface Shields** category (36 of 466), **Then** both gates apply. |
| TC-QC-02 | **Given** any other material, **Then** neither gate applies: no certificate is demanded, no inspection is opened, no issue is blocked. ✅ **Run this.** A quality gate that leaked onto the other 430 materials would halt the whole site. |

### 5.2 The certificate gate

| | |
|---|---|
| **Who** | Warehouse User |
| **What** | A Material Test Certificate is mandatory before controlled material is dispatched |
| **Where** | Entry → MTC upload; enforced at Delivery Note creation |
| **When** | At DN creation — **not** at receipt, and **not** at issue |
| **Why** | The certificate must travel with the material. Chasing it after the truck has gone never works. |

| ID | Given / When / Then |
|---|---|
| TC-QC-03 | **Given** controlled material with **no** certificate, **When** a DN is created for it, **Then** refused, and the message says a certificate is mandatory and where to upload it. ⛔ |
| TC-QC-04 | **Given** the certificate is then uploaded, **When** the DN is retried, **Then** it succeeds. ✅ |
| TC-QC-05 | **Given** an **uncontrolled** material with no certificate, **When** a DN is created, **Then** it succeeds — nothing is asked for. ✅ |
| TC-QC-06 | 🤖 **Given** a DN line, **Then** confirm the gate resolves the material correctly. *DN lines carry a material code, not a part number; a lookup that only understood part numbers would silently pass every DN line and the gate would be decorative.* Test with a DN, not only with an issue. |

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
| TC-QC-12 | **Given** a decided inspection, **When** the QC decides it again, **Then** refused. ⛔ |
| TC-QC-13 | **Given** an inspection, **When** a **Store Keeper** tries to decide it, **Then** refused — reading the queue is open, deciding is not. ⛔ |
| TC-QC-14 | **Given** a **site-bound** QC, **When** they open Inspections, **Then** they see their site's and no other's. |
| TC-QC-15 | **Given** a **warehouse-bound** QC, **When** they open Inspections, **Then** they see their warehouse's, and **no site rows at all**. |
| TC-QC-16 | **Given** a QC account with **neither** binding, **When** they open Inspections, **Then** the list is **empty** — never everything. ⛔ *This is the fail-closed test. If it ever shows all sites, stop and report immediately.* |

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
- **Don't report that a DN was allowed for uninspected material.** That is the
  ruling — the certificate binds at dispatch, approval binds at issue.
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
| Backend service tests | **1401 / 0** | `GI_DOTENV=0 .venv/bin/python -m backend.api.service_tests` |
| Legacy regression | **599 / 0** | `.venv/bin/python legacy/bug_check.py` |
| Playwright E2E | **57 / 57** | `cd tests/e2e && npm test` |
| SME TS↔PY parity | **1,313 comparisons** | `npm run parity:sme --prefix frontend` |
| SME UI math | **33 / 0** | `npm run test:ui-math --prefix frontend` |
| Frontend build | clean | `npm run build --prefix frontend` |
| Manual PDFs | **0 overlapping text pairs** | `.venv/bin/python build_manual_pdf.py --role all` |

---

**End of guide.** Keep it current — see `PROJECT_HANDOVER.md` rule 13.
