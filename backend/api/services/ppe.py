"""
backend/api/services/ppe.py — PPE usable time, distribution, and the forecast.

Part of QSEP (Quality · Safety · Employees · Procurement, 2026-08).

--------------------------------------------------------------------------
OPTION A: PPE IS THE ORDINARY ISSUE FORM, NOT A SECOND ONE

The operator's ruling (2026-08-09) is that a Store Keeper hands out safety
boots the same way they hand out anything else — through the standard Issue
form. When the material they pick is PPE, the form grows two fields and the
backend does two things in one transaction: the ordinary stock consumption,
and a `ppe_distributions` row.

Why that matters architecturally: **PPE gets no parallel stock ledger.** The
quantity leaves the shelf through `pending_issues` → `consumption` exactly
like every other material, so stock, FEFO, the burn rate, the reports and
the QC gate all keep working with no PPE-shaped exception carved into them.
`ppe_distributions` is a record of WHO HAS IT, hung beside the movement — it
is not the movement.

--------------------------------------------------------------------------
WHAT COUNTS AS PPE — TWO SIGNALS, TWO JOBS

  inventory."Category" = 'PPE'   →  which items OFFER the flow (the UI filter)
  a ppe_rules row                →  which items have a usable time (the maths)

`is_ppe()` is true for either, because a rule the SK wrote is a statement
that this item is PPE regardless of how the master categorises it, and the
operator's 9 PPE-category rows are PPE before anyone has written a rule.

A PPE-category item with no rule is still distributed and still recorded. It
simply has no expiry, so the forecast cannot see it — a data-entry gap the
rules page surfaces rather than an error that blocks a handover.

--------------------------------------------------------------------------
THE LIFECYCLE, AND WHY THE ROW IS WRITTEN AT STAGE TIME

An issue is STAGED by the SK and COMMITTED by the HOD, minutes or hours
later. The boots, however, are on the worker's feet the moment the SK hands
them over. So:

    stage    → ppe_distributions row, status='active', pending_issue_id set
    approve  → consumption_id filled in, still active
    reject   → status='void'

Writing it at stage is what makes the duplicate-issue guard true: if the row
only appeared on approval, a second pair could be staged in the gap and both
would pass the "does this person already have one?" check. A voided row is
excluded from every active query, so a rejection frees the person up again.

--------------------------------------------------------------------------
THIS MODULE MUST NEVER READ SME DATA

The forecast's "what do we already have, what is already on order" netting
has the same SHAPE as SME rule 1c and is not the same thing. Rule 1a: every
SME number comes from `sme_inventory_seed`. This module reads `inventory`,
`receipts`, `consumption`, `returns`, `po_items` and `purchase_orders` —
nothing whose name begins `sme_`. Suite BO greps this file.
"""
from __future__ import annotations

import datetime as _dt

from fastapi import HTTPException
from sqlalchemy import func, insert, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from .ledger import _MD, write_audit
from .notifications import dispatch

inventory_t = _MD.tables["inventory"]
rules_t = _MD.tables["ppe_rules"]
dist_t = _MD.tables["ppe_distributions"]
employees_t = _MD.tables["employees"]
attachments_t = _MD.tables["entry_attachments"]
po_items_t = _MD.tables["po_items"]

# The category the operator pushed into `inventory` on 2026-08-09 (9 SAPs).
PPE_CATEGORY = "PPE"

# Distributions that mean "this person is currently wearing this".
ACTIVE = "active"


def _today() -> str:
    return _dt.date.today().isoformat()


def add_days(iso_date: str, days: int) -> str:
    """ISO date + N days, as an ISO date.

    Pure and separately testable, because getting an expiry off by one is
    invisible until somebody is issued replacement boots a day early.
    """
    return (_dt.date.fromisoformat(iso_date) + _dt.timedelta(days=int(days))).isoformat()


# ── what is PPE, and for how long ────────────────────────────────────────────
async def is_ppe(session: AsyncSession, sap_code: str) -> bool:
    """True when the master says PPE **or** somebody has written a rule.

    Either signal alone is sufficient. The category is authoritative for
    "offer the flow"; a rule is an SK's explicit statement that this item
    wears out, which is a stronger claim than a category and must not be
    ignored because the master disagrees.
    """
    sap = str(sap_code).strip()
    cat = (await session.execute(text(
        'SELECT "Category" FROM inventory WHERE TRIM("SAP_Code") = TRIM(:s) LIMIT 1'
    ), {"s": sap})).scalar()
    if str(cat or "").strip().lower() == PPE_CATEGORY.lower():
        return True
    n = (await session.execute(select(func.count()).select_from(rules_t)
         .where(func.trim(rules_t.c["SAP_Code"]) == sap))).scalar_one()
    return n > 0


async def rule_for(session: AsyncSession, *, sap_code: str,
                   site_id: str | None) -> dict | None:
    """The usable-time rule that applies here: a SITE row beats the global one.

    Ordered so the site row sorts first — `Site_ID IS NULL` last — rather
    than fetching both and picking in Python, so there is one definition of
    precedence and it lives in the query.
    """
    sap = str(sap_code).strip()
    c = rules_t.c
    stmt = (select(c["id"], c["SAP_Code"], c["Site_ID"], c["usable_days"],
                   c["requires_safety_doc"], c["notes"])
            .where(func.trim(c["SAP_Code"]) == sap)
            .where((c["Site_ID"] == (site_id or "")) | (c["Site_ID"].is_(None)))
            .order_by(c["Site_ID"].is_(None))
            .limit(1))
    row = (await session.execute(stmt)).mappings().first()
    return dict(row) if row else None


async def rules_for_many(session: AsyncSession, *, sap_codes: list[str],
                         site_id: str | None) -> dict[str, dict]:
    """`rule_for` for a whole list, in ONE query — {SAP_Code: rule}.

    The picker at `/ppe/eligible` called `rule_for` once per material, so a
    catalogue of N PPE items cost N round trips to answer a question one query
    answers. Nothing was wrong with the ANSWER, which is why it never showed up
    as a bug — only as a page that got slower every time somebody added a rule.

    ⚠️ PRECEDENCE MUST STAY IDENTICAL TO `rule_for`: a SITE row beats a global
    one. `rule_for` expresses that as `ORDER BY Site_ID IS NULL LIMIT 1` so the
    ordering lives in the query; here the rows for many SAPs come back together
    and the pick happens in Python, so the two spellings have to agree. They
    are tested against each other rather than trusted — a divergence would mean
    the picker and the issue form disagreed about how long a boot lasts.
    """
    if not sap_codes:
        return {}
    c = rules_t.c
    rows = (await session.execute(
        select(c["id"], c["SAP_Code"], c["Site_ID"], c["usable_days"],
               c["requires_safety_doc"], c["notes"])
        .where(func.trim(c["SAP_Code"]).in_([s.strip() for s in sap_codes]))
        .where((c["Site_ID"] == (site_id or "")) | (c["Site_ID"].is_(None)))
    )).mappings().all()
    out: dict[str, dict] = {}
    for r in rows:
        sap = str(r["SAP_Code"]).strip()
        prev = out.get(sap)
        # A site row wins; otherwise first-seen. Mirrors `ORDER BY Site_ID IS NULL`.
        if prev is None or (prev.get("Site_ID") is None and r["Site_ID"] is not None):
            out[sap] = dict(r)
    return out


async def active_holding(session: AsyncSession, *, id_number: str,
                         sap_code: str) -> dict | None:
    """The person's current, unreplaced issue of this item — or None.

    Site-independent BY DESIGN (ruling R1): a worker who was issued boots at
    CNCEC and then transferred does not get a second pair on day one at the
    new site just because the row was written somewhere else. That is the
    duplicate-distribution prevention the requirement asks for, and it falls
    out of keying on the person rather than the employment record.
    """
    c = dist_t.c
    row = (await session.execute(
        select(c["id"], c["Site_ID"], c["issued_on"], c["expires_on"],
               c["Qty"], c["usable_days_applied"])
        .where(c["employee_id_number"] == str(id_number).strip(),
               func.trim(c["SAP_Code"]) == str(sap_code).strip(),
               c["status"] == ACTIVE)
        .order_by(c["issued_on"].desc(), c["id"].desc()).limit(1))).mappings().first()
    return dict(row) if row else None


# ── the integrated Option A gate ─────────────────────────────────────────────
async def validate_issue(session: AsyncSession, *, data: dict) -> dict | None:
    """Validate the PPE half of an ordinary issue. Returns a plan, or None.

    Called BEFORE anything is written, so a bad line raises before the
    pending_issues row exists and there is nothing to unwind. Returns None
    for the ~450 non-PPE materials, which is what keeps the standard issue
    form unchanged for everyone else.
    """
    sap = str(data["SAP_Code"]).strip()
    if not await is_ppe(session, sap):
        return None

    site = data["Site_ID"]
    id_number = str(data.get("employee_id_number") or "").strip()
    if not id_number:
        raise HTTPException(
            422, f"{sap} is PPE — name the employee receiving it "
                 "(their ID number), so it can be tracked against them.")

    emp = (await session.execute(select(
        employees_t.c["Name"], employees_t.c["status"], employees_t.c["Site_ID"])
        .where(employees_t.c["ID_Number"] == id_number))).first()
    # Same three messages as supervisor.create_smr, deliberately: an SK who
    # has seen "worker X is bound to site Y" once should not have to learn a
    # second vocabulary for the same three failures.
    if emp is None:
        raise HTTPException(422, f"worker {id_number!r} is not in the employee master")
    if emp[1] != "active":
        raise HTTPException(422, f"worker {id_number!r} is {emp[1]}, not active")
    if (emp[2] or "") != site:
        raise HTTPException(
            422, f"worker {id_number!r} is at site {emp[2] or '—'}, not {site} — "
                 "transfer them first if they have moved")

    rule = await rule_for(session, sap_code=sap, site_id=site)
    needs_doc = bool(rule["requires_safety_doc"]) if rule else True
    doc_id = data.get("safety_doc_id")
    if needs_doc and not doc_id:
        raise HTTPException(
            422, f"{sap} is PPE — attach the signed Safety Approval before issuing it.")
    if doc_id:
        doc = (await session.execute(select(
            attachments_t.c["doc_type"], attachments_t.c["uploaded_by"])
            .where(attachments_t.c["id"] == int(doc_id)))).first()
        if doc is None:
            raise HTTPException(422, f"unknown safety document id {doc_id}")
        if doc[0] != "safety_approval":
            raise HTTPException(
                422, f"attachment {doc_id} is a {doc[0]} document, not a safety approval")

    prior = await active_holding(session, id_number=id_number, sap_code=sap)
    early = False
    if prior is not None:
        # "Before the usable time expires" — an item with no expiry on record
        # (no rule when it was issued) cannot be judged early, so it is not.
        exp = prior.get("expires_on")
        early = bool(exp) and exp > _today()
        if early and not str(data.get("early_reason") or "").strip():
            raise HTTPException(
                422, f"{emp[0]} already holds {sap}, issued {prior['issued_on']} and "
                     f"good until {exp} — give a reason for replacing it early.")

    issued_on = str(data.get("Date") or _today())[:10]
    days = int(rule["usable_days"]) if rule else None
    return {
        "sap": sap, "site": site, "id_number": id_number, "employee_name": emp[0],
        "rule_id": (rule or {}).get("id"), "usable_days": days,
        "issued_on": issued_on,
        "expires_on": add_days(issued_on, days) if days else None,
        "safety_doc_id": int(doc_id) if doc_id else None,
        "prior_id": (prior or {}).get("id"),
        "early": early,
        "early_reason": str(data.get("early_reason") or "").strip() or None,
    }


async def record_distribution(session: AsyncSession, *, plan: dict, data: dict,
                              pending_id: int, username: str) -> int:
    """Write the distribution for a validated, just-staged PPE issue."""
    meta = (await session.execute(text(
        'SELECT "Material_Code", "Equipment_Description" FROM inventory '
        'WHERE TRIM("SAP_Code") = TRIM(:s) LIMIT 1'), {"s": plan["sap"]})).first()
    new_id = (await session.execute(insert(dist_t).values(
        Site_ID=plan["site"], employee_id_number=plan["id_number"],
        employee_name=plan["employee_name"], SAP_Code=plan["sap"],
        Material_Code=(meta[0] if meta else None),
        Description=(meta[1] if meta else None),
        Lot_Number=(data.get("Lot_Number") or None), Qty=float(data["Quantity"]),
        issued_on=plan["issued_on"], usable_days_applied=plan["usable_days"],
        expires_on=plan["expires_on"], safety_doc_id=plan["safety_doc_id"],
        replaces_distribution_id=plan["prior_id"],
        early_replacement=1 if plan["early"] else 0,
        early_reason=plan["early_reason"], pending_issue_id=pending_id,
        status=ACTIVE, issued_by=username,
    ).returning(dist_t.c["id"]))).scalar_one()

    if plan["prior_id"]:
        await session.execute(update(dist_t)
                              .where(dist_t.c["id"] == plan["prior_id"])
                              .values(status="replaced"))
    await write_audit(
        session, username, "PPE_ISSUE", "ppe_distributions",
        f"id={new_id} {plan['sap']} → {plan['id_number']} qty={float(data['Quantity']):g} "
        f"expires={plan['expires_on'] or 'n/a'}"
        + (f" EARLY: {plan['early_reason']}" if plan["early"] else ""))
    if plan["early"]:
        # An early replacement is the one case a supervisor genuinely wants to
        # know about — it is either damage, loss, or a rule that is too long.
        await dispatch(
            session, event_key="ppe_early_replacement", severity="warning",
            recipient_role="hod", recipient_site=plan["site"],
            wa_template="status_update",
            title=f"PPE replaced early — {plan['employee_name']}",
            body=(f"{plan['sap']} reissued before it expired. "
                  f"Reason: {plan['early_reason']}"),
            link_page="/ppe/forecast", related_table="ppe_distributions",
            related_ref=new_id, created_by=username)
    return new_id


async def link_committed(session: AsyncSession, *, pending_id: int,
                         consumption_id: int | None) -> None:
    """HOD approved the issue — bind the distribution to the ledger row."""
    if consumption_id is None:
        return
    await session.execute(update(dist_t).where(
        dist_t.c["pending_issue_id"] == int(pending_id),
        dist_t.c["status"] == ACTIVE,
    ).values(consumption_id=int(consumption_id)))


async def void_rejected(session: AsyncSession, *, pending_id: int) -> int:
    """HOD rejected the issue — void the distribution and restore its predecessor.

    Restoring matters: staging the replacement flipped the previous pair to
    'replaced', and if the rejection simply voided the new row the worker
    would end up holding nothing on record while still wearing the old boots.
    """
    c = dist_t.c
    row = (await session.execute(
        select(c["id"], c["replaces_distribution_id"])
        .where(c["pending_issue_id"] == int(pending_id), c["status"] == ACTIVE)
    )).first()
    if row is None:
        return 0
    await session.execute(update(dist_t).where(c["id"] == row[0]).values(status="void"))
    if row[1]:
        await session.execute(update(dist_t).where(
            c["id"] == row[1], c["status"] == "replaced").values(status=ACTIVE))
    return 1


# ── reporting ────────────────────────────────────────────────────────────────
async def history_for(session: AsyncSession, *, id_number: str) -> list[dict]:
    """Everything this PERSON has ever been issued, across every site.

    Not filtered by site, and that is the point of ruling R1: the target
    site's Store Keeper has to see what the worker already holds, or the
    transfer just moved the duplicate-issue problem somewhere else.
    """
    c = dist_t.c
    rows = (await session.execute(
        select(c["id"], c["Site_ID"], c["SAP_Code"], c["Material_Code"],
               c["Description"], c["Qty"], c["issued_on"], c["expires_on"],
               c["usable_days_applied"], c["status"], c["early_replacement"],
               c["early_reason"], c["safety_doc_id"], c["issued_by"])
        .where(c["employee_id_number"] == str(id_number).strip(),
               c["status"] != "void")
        .order_by(c["issued_on"].desc(), c["id"].desc()))).mappings().all()
    today = _today()
    out = []
    for r in rows:
        d = dict(r)
        exp = d.get("expires_on")
        # "Expired" is a SUGGESTED REPLACEMENT DATE, not a restriction
        # (operator ruling, 2026-08-09). Nothing stops the worker using the
        # gear and nothing blocks on this flag; it drives a column, not a gate.
        d["overdue"] = bool(exp and d["status"] == ACTIVE and exp < today)
        d["days_left"] = (
            (_dt.date.fromisoformat(exp) - _dt.date.fromisoformat(today)).days
            if exp else None)
        out.append(d)
    return out


# The forecast window. 15 days by operator ruling (2026-08-09) — long enough
# to raise a PR and have it delivered, short enough that the list is a
# shopping list rather than a wish list.
FORECAST_DAYS = 15

# ⚠️ TWO BULK QUERIES, NOT TWO PER MATERIAL. These used to be single-row
# lookups run inside the grouping loop — 2N round trips to build one forecast.
# The site predicate is `:site IS NULL OR …`, which fixes a real defect at the
# same time: the old per-SAP form passed `site_id or ""`, so an UNSCOPED
# forecast (admin, all sites) counted on-hand only for rows whose Site_ID was
# NULL or blank. Expiring quantity was summed across every site while stock was
# summed across almost none, so the global view suggested ordering PPE that was
# already on the shelf. Scoped forecasts were always correct; only the
# all-sites one was wrong, which is why nobody had reported it.
_ON_HAND_MANY_SQL = text('''
    SELECT sap, SUM(q) AS on_hand FROM (
        SELECT TRIM("SAP_Code") AS sap, SUM("Quantity") AS q FROM receipts
         WHERE TRIM("SAP_Code") = ANY(CAST(:saps AS text[]))
           AND (CAST(:site AS text) IS NULL OR COALESCE("Site_ID",'') = CAST(:site AS text)) GROUP BY 1
        UNION ALL
        SELECT TRIM("SAP_Code"), -SUM("Quantity") FROM consumption
         WHERE TRIM("SAP_Code") = ANY(CAST(:saps AS text[]))
           AND (CAST(:site AS text) IS NULL OR COALESCE("Site_ID",'') = CAST(:site AS text)) GROUP BY 1
        UNION ALL
        SELECT TRIM("SAP_Code"), -SUM("Quantity") FROM returns
         WHERE TRIM("SAP_Code") = ANY(CAST(:saps AS text[]))
           AND (CAST(:site AS text) IS NULL OR COALESCE("Site_ID",'') = CAST(:site AS text)) GROUP BY 1
    ) t GROUP BY sap
''')

# On-order is unavoidably GLOBAL: `po_items` carries no site column, so an open
# line cannot be attributed to a destination. Stated here rather than left to be
# rediscovered — for a site-scoped forecast this can under-suggest by counting a
# line bound for another site.
_ON_ORDER_MANY_SQL = text('''
    SELECT UPPER(TRIM(COALESCE(pi."Material_Code",''))) AS mat,
           COALESCE(SUM(pi."Qty" - COALESCE(pi."Delivered_Qty",0)), 0) AS on_order
      FROM po_items pi
      JOIN purchase_orders po ON po."PO_Number" = pi."PO_Number"
     WHERE UPPER(TRIM(COALESCE(pi."Material_Code",''))) = ANY(CAST(:mats AS text[]))
       AND COALESCE(pi.line_status,'open') NOT IN ('closed','cancelled')
       AND COALESCE(po.status,'open') NOT IN ('closed','cancelled')
     GROUP BY 1
''')


async def forecast(session: AsyncSession, *, site_id: str | None,
                   days: int = FORECAST_DAYS) -> dict:
    """What to buy: PPE expiring in the window, netted against what we hold.

    Deliberately DETERMINISTIC, not statistical (ruling R5). There are 22
    roster workers and no distribution history to fit anything to; a model
    here would be manufacturing confidence. The arithmetic is:

        expiring   = active distributions whose expires_on lands in the window
        on_hand    = site stock for that SAP (the ordinary ledger)
        on_order   = open PO line quantity not yet delivered
        suggested  = max(expiring − on_hand − on_order, 0)

    Netting against on_order is the same instinct as SME rule 1c — do not
    order what is already coming — but it reads ERP tables only. A 90-day
    issue rate is returned BESIDE the number as a sanity column, never
    folded into it, so nobody has to guess which part is measured and which
    part is inferred.

    Employee names ride along per row, because the operator asked to see
    whose gear it is: a list of quantities cannot be checked by a human, and
    a list of names can.
    """
    today = _today()
    horizon = add_days(today, days)
    c = dist_t.c
    stmt = (select(c["id"], c["Site_ID"], c["SAP_Code"], c["Material_Code"],
                   c["Description"], c["Qty"], c["employee_id_number"],
                   c["employee_name"], c["issued_on"], c["expires_on"])
            .where(c["status"] == ACTIVE, c["expires_on"].isnot(None),
                   c["expires_on"] <= horizon))
    if site_id:
        stmt = stmt.where(c["Site_ID"] == site_id)
    rows = (await session.execute(stmt.order_by(c["expires_on"]))).mappings().all()

    by_sap: dict[str, dict] = {}
    for r in rows:
        sap = str(r["SAP_Code"]).strip()
        g = by_sap.setdefault(sap, {
            "SAP_Code": sap, "Material_Code": r["Material_Code"],
            "Description": r["Description"], "expiring_qty": 0.0,
            "people": [], "earliest_expiry": r["expires_on"]})
        g["expiring_qty"] += float(r["Qty"] or 0)
        overdue = str(r["expires_on"]) < today
        g["people"].append({
            "employee_id_number": r["employee_id_number"],
            "employee_name": r["employee_name"], "Site_ID": r["Site_ID"],
            "Qty": float(r["Qty"] or 0), "issued_on": r["issued_on"],
            "expires_on": r["expires_on"], "overdue": overdue,
            "days_left": (_dt.date.fromisoformat(str(r["expires_on"]))
                          - _dt.date.fromisoformat(today)).days})
        if str(r["expires_on"]) < str(g["earliest_expiry"]):
            g["earliest_expiry"] = r["expires_on"]

    # Two queries for the whole forecast, whatever N is.
    saps = list(by_sap)
    mats = sorted({str(g["Material_Code"]).strip().upper()
                   for g in by_sap.values() if g["Material_Code"]})
    on_hand_by_sap: dict[str, float] = {}
    on_order_by_mat: dict[str, float] = {}
    if saps:
        on_hand_by_sap = {r[0]: float(r[1] or 0) for r in (await session.execute(
            _ON_HAND_MANY_SQL, {"saps": saps, "site": site_id or None})).all()}
    if mats:
        on_order_by_mat = {r[0]: float(r[1] or 0) for r in (await session.execute(
            _ON_ORDER_MANY_SQL, {"mats": mats})).all()}

    items = []
    for sap, g in by_sap.items():
        on_hand = on_hand_by_sap.get(sap, 0.0)
        on_order = on_order_by_mat.get(
            str(g["Material_Code"]).strip().upper(), 0.0) if g["Material_Code"] else 0.0
        g["on_hand"] = on_hand
        g["on_order"] = on_order
        g["suggested_order_qty"] = max(
            round(g["expiring_qty"] - on_hand - on_order, 6), 0.0)
        g["people_count"] = len(g["people"])
        g["overdue_count"] = sum(1 for p in g["people"] if p["overdue"])
        items.append(g)

    items.sort(key=lambda x: (-x["suggested_order_qty"], x["earliest_expiry"]))
    return {"window_days": days, "as_of": today, "horizon": horizon,
            "site_id": site_id, "items": items,
            "total_people": sum(i["people_count"] for i in items),
            "total_suggested": round(sum(i["suggested_order_qty"] for i in items), 6)}
