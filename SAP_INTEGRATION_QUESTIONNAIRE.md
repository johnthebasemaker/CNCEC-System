# SAP ERP Integration — Technical Questionnaire

**To:** IT / SAP Basis team
**From:** GI Hub project team
**Subject:** Read-only master-data feed for three entities — Employee, Equipment, Material
**Version:** 1.0 · 2026-09-01

---

## 1. What we are asking for, and what we are not

GI Hub is the site-level inventory, quality and execution system already in use
at the project sites. It currently holds its own copies of the employee roster,
the equipment register and the material master, which are maintained by hand.
That hand-maintenance is the problem we are trying to remove.

**We are asking for read access to three master-data entities.** Nothing else.

| | |
|---|---|
| **We need** | Employee, Equipment and Material **master records** — the descriptive attributes, not the transactions |
| **Direction** | **One-way, SAP → GI Hub.** We write nothing back |
| **Access** | **Read-only.** No SAP user we are given needs any write, delete or execute authority |
| **Frequency** | Once daily is sufficient. Hourly is a bonus, not a requirement |
| **We are NOT asking for** | Postings, goods movements, stock quantities, financial data, HR/payroll compensation data, or any change to an existing SAP process, screen, transaction or workflow |

The last row matters and is the reason this document is short. We have
deliberately scoped this to the smallest thing that removes the manual work.
**If any answer below is "that would require development work in SAP", tell us —
we will almost certainly drop that field rather than ask you to build anything.**

---

## 2. Transport — how the data physically reaches us

We can consume any of the following. They are listed in **our** order of
preference, but *your* order of least effort should win; please tell us which is
already available.

- [ ] **A. OData / REST service** (SAP Gateway, S/4HANA API, or an existing
      published service)
- [ ] **C. Scheduled flat-file extract** — CSV or fixed-width, dropped to
      **SFTP** on a schedule
- [ ] **B. SOAP web service** (PI/PO, or an RFC exposed as a service)
- [ ] **D. A read-only database view** (HANA, or a replicated reporting layer),
      reachable from our application server
- [ ] **E. Something else you already run for other consumers** — please
      describe

**Q2.1** Which of the above is already in place and serving other consumers
today? We would strongly prefer to be the second consumer of an existing feed
rather than the reason a new one is built.

**Q2.2** If **A/B (service)**: what is the base URL / service endpoint, and what
is the authentication method — basic, OAuth 2.0 client credentials, X.509 client
certificate, or an API key?

**Q2.3** If **C (flat file)**: which host, which path, which key exchange
(SSH key or password), what filename convention, and at what time does the job
run? Is the file a **full snapshot** or **only the changes**?

**Q2.4** If **D (database view)**: which system and schema, and is connectivity
from our application host to that port already permitted, or does it need a
firewall change?

**Q2.5** Is the source system **ECC** or **S/4HANA**, and which release? This
decides whether the standard OData APIs (`API_BUSINESS_PARTNER`,
`API_PRODUCT_SRV`, etc.) are available to us at all.

---

## 3. Entity 1 — Employee

**What we use it for:** attendance, man-hour recording against work orders, PPE
issue history, and naming the person who signs a consumption sheet.

**Q3.1 — The key.** What is the stable, unique identifier for a person?
(SAP Personnel Number `PERNR`, an ID/Iqama number, or something else?) We need
one field that never changes for the life of the person's employment, because
every historical record we already hold is keyed on it.

**Q3.2 — The fields.** For each field below: is it available, and what is its
SAP field name?

| We need | Why | Available? | SAP field |
|---|---|---|---|
| Unique person ID | The key — see Q3.1 | | |
| Full name | Shown on every screen and printed form | | |
| National ID / Iqama number | How the site identifies a person at the gate | | |
| Designation / job title | Drives the man-hour rate category | | |
| Department | Reporting grouping | | |
| Company / employer | We track GI staff and subcontractor staff separately | | |
| Site / plant / work location | Scopes what each record belongs to | | |
| Employment status (active / inactive) | So leavers stop appearing in dropdowns | | |
| Date of joining | Only if trivially available — we can live without it | | |

**Q3.3 — What we do NOT want, and please confirm it is excluded:** salary,
bank details, grade, appraisal data, next of kin, medical records, passport
scans, or any other personal data beyond the rows above. If your extract cannot
easily exclude these, tell us and we will define a view together — we would
rather have fewer fields than hold data we have no business holding.

**Q3.4 — Leavers.** When somebody leaves, does the record **disappear** from the
extract, or does it **remain with a status flag**? This changes what we do: a
record that vanishes must not delete the person's history on our side.

**Q3.5 — Scope.** Roughly how many employee records, and can the feed be
filtered to only the plants/sites relevant to this project?

---

## 4. Entity 2 — Equipment

**What we use it for:** identifying the tank, vessel or line a lining or coating
job is performed on. Every consumption record and every progress entry is filed
against one equipment tag.

**Q4.1 — The key.** What is the unique identifier — SAP Equipment Number
(`EQUNR`), Functional Location (`TPLNR`), or a plant tag number? **If both an
equipment number and a functional-location tag exist, we need both**, because
our site records were built using the tag people say out loud and SAP's internal
number is what will keep them stable.

**Q4.2 — The fields.**

| We need | Why | Available? | SAP field |
|---|---|---|---|
| Equipment number / unique key | The key — see Q4.1 | | |
| Tag number as used on site | What is painted on the object and written on paper | | |
| Description / name | Shown on screen | | |
| Functional location | Where it sits in the plant hierarchy | | |
| Plant / site | Scoping | | |
| Equipment type or category | Grouping | | |
| Status (in service / decommissioned) | So retired assets leave the dropdowns | | |
| Parent / superior equipment | Only if a hierarchy exists — for grouping | | |

**Q4.3 — Dimensions.** Does SAP hold any **physical dimension or surface-area**
data for these objects (diameter, height, area in m²)? We currently maintain
surface areas by hand from drawings. If SAP has them, that is the single highest-
value field in this entire document. If it does not, we will carry on as we are —
please just say so rather than investigating.

**Q4.4 — Scope.** How many equipment records, and can the feed be filtered by
plant or by functional-location hierarchy?

---

## 5. Entity 3 — Material

**What we use it for:** the material master behind every stock movement, receipt,
issue and purchase requisition in GI Hub. This is the entity where a mismatch
costs us the most, because our records are already keyed on the SAP code.

**Q5.1 — The key.** We currently store a code we call the **SAP Code** and a
second one we call the **Material Code**. Please confirm which SAP field each
corresponds to — we expect `MATNR` for one of them. **If our codes turn out not
to match `MATNR`, that is the most important thing this exercise can discover,
and we would like to know early.**

**Q5.2 — The fields.**

| We need | Why | Available? | SAP field |
|---|---|---|---|
| Material number (`MATNR`) | The key | | |
| Material description | Shown everywhere; also what our OCR matches against | | |
| Base unit of measure | Our stock arithmetic depends on it | | |
| Alternative UoMs + conversion factors | We receive in drums and issue in litres | | |
| Material type / group | Category grouping | | |
| Plant(s) the material is extended to | Scoping | | |
| Deletion / blocked flag | So obsolete materials leave the dropdowns | | |
| Standard or moving-average price | Valuing stock reports — **optional**, see Q5.4 | | |

**Q5.3 — Descriptions.** Are descriptions maintained in more than one language,
and if so which one should we treat as authoritative? Our site paperwork is in
English.

**Q5.4 — Price.** Is unit price/valuation available in the same extract, or does
it sit behind a separate authorisation? If it is even slightly awkward, **leave
it out** — we can value stock from our own purchase-order history and would
rather not touch a costing object.

**Q5.5 — Scope.** How many material records, and can the feed be filtered to the
material types and plants relevant to this project? We expect to need a few
thousand, not the whole master.

---

## 6. Data mechanics — the questions that decide our design

These apply to all three entities. Please answer once unless the answers differ.

**Q6.1 — Full or delta?** Does each run deliver the **complete** current set, or
**only records changed since the last run**? A full snapshot is simpler for us
and we are happy to take it if the volumes above allow.

**Q6.2 — Change timestamp.** Is there a reliable "last changed on/at" field we
can use to detect what moved? (`AEDAT`/`AENAM`, or equivalent.) Without one we
must compare full snapshots, which is fine at these volumes but worth knowing.

**Q6.3 — Deletions.** How is a deleted or archived record represented — absent
from the extract, or present with a deletion flag? (Same question as Q3.4, asked
here for the other two entities.) **We will never hard-delete on our side**; we
need to know which records to mark inactive.

**Q6.4 — Character encoding and format.** For a file feed: UTF-8? Delimiter?
Quoting convention for fields containing commas? Date format? Decimal separator?

**Q6.5 — Empty extract.** If the job produces an empty or truncated file, is that
detectable — a control record, a row count, a manifest, a checksum? We need to
distinguish "nothing changed" from "the job failed", and we will not import a
file we cannot tell apart from a failure.

---

## 7. Access, security and approvals

**Q7.1 — The account.** Can a **dedicated, read-only technical/service account**
be created for this interface, rather than reusing a person's credentials? What
is your process and expected lead time?

**Q7.2 — Network path.** Our application server will initiate the connection
(or receive the file). Which direction does traffic flow in your preferred
option, and does it require a firewall rule, a VPN, or a jump host?

**Q7.3 — Credential rotation.** What is the expected rotation period for the
credential or certificate, and how will we be notified before it expires?

**Q7.4 — Non-production.** Is there a **QA or sandbox** client we can develop
against? We would prefer never to point development code at production, and we
will not begin against production without your explicit sign-off.

**Q7.5 — Approval.** Who must approve this access, and what do they need from
us? If a short data-flow or privacy note would help that approval, tell us the
format and we will write it.

**Q7.6 — Support.** Who is the named contact if the feed stops, and what is the
expected response path? A daily interface that fails silently is worse for us
than no interface at all.

---

## 8. What happens next

1. You return this document with the sections above filled in — **partial answers
   are useful**; please do not hold it back for completeness.
2. We confirm the field mapping and flag anything that would require development
   on your side, so it can be **removed from scope** before anyone estimates it.
3. We build and test against the non-production client (Q7.4).
4. We agree a cutover date and a rollback position.

**Our commitment:** we will not ask for a change to any existing SAP process,
transaction, screen or workflow. If a field we have asked for is not readily
available, our default answer is to drop it.

---

**Return to:** *(GI Hub project team — contact to be filled in before sending)*
**Questions on this document:** *(same)*
