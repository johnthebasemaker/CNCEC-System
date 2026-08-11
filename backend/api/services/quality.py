"""
backend/api/services/quality.py — the MTC gate and the QC clearance gate.

Part of QSEP (Quality · Safety · Employees · Procurement, 2026-08).

Two rules live here, and they are deliberately SEPARATE — the operator ruled
on 2026-08-09 that they bind at different points in the chain:

  **The MTC rule is about PAPERWORK and it binds at DISPATCH.** A Material
  Test Certificate is mandatory when a Surface-Shield material is received
  into a warehouse and again when it is put on a Delivery Note. Without one
  the material may not be SENT to a site.

  **The QC rule is about INSPECTION and it binds at ISSUE.** Uninspected
  material MAY travel — it can sit at the site waiting for the inspector.
  What it may not do is reach the field: a Store Keeper cannot issue it
  until a QC has approved a quantity.

Keeping them apart matters. Fusing them ("nothing moves until QC signs")
would strand every delivery behind an inspector who is not at the warehouse
that day, which is exactly the failure the operator ruled against.

--------------------------------------------------------------------------
WHY THIS MODULE EXISTS AT ALL

The category test used to be two inlined copies in entry.py, wired into
exactly two call sites (`POST /entry/receipts` and the receipt branch of
`/entry/bulk`). Three paths walked straight past it:

  * `warehouse.receive()`      — warehouse goods-in against a PO assignment
  * `warehouse.create_dn()`    — so shields could be SHIPPED with no MTC
  * `warehouse.stage_dn_receipt()` — inserts pending_receipts directly

One definition, five call sites. A gate with copies is a gate with holes.

--------------------------------------------------------------------------
THE MATERIAL_CODE TRAP

`dn_items` carry `Material_Code` and **no SAP at all**. A category lookup
written only against `inventory."SAP_Code"` therefore matches nothing on the
DN path and passes every line silently — a gate that is present, green, and
doing nothing. Every function here takes either key and resolves both.

--------------------------------------------------------------------------
THIS MODULE MUST NEVER READ SME DATA

The arithmetic below ("how much is approved, how much is already gone")
looks like the SME estimator's shape and is not. Rule 1a: every SME number
comes from `sme_inventory_seed` and an ERP movement must not move one. This
module reads `inventory`, `consumption`, `pending_issues`, `mtc_documents`
and `qc_inspections` — and nothing whose name starts with `sme_`. Suite BM
greps this file for those table names.
"""
from __future__ import annotations

import datetime as _dt

from fastapi import HTTPException
from sqlalchemy import func, insert, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from .ledger import _MD, write_audit
from .notifications import dispatch

inventory_t = _MD.tables["inventory"]
mtc_t = _MD.tables["mtc_documents"]
inspections_t = _MD.tables["qc_inspections"]
consumption_t = _MD.tables["consumption"]
pending_issues_t = _MD.tables["pending_issues"]
settings_t = _MD.tables["app_settings"]

# Inspections that have released some quantity for issue. `pending` and
# `rejected` release nothing — which is the whole point of the gate.
_CLEARED = ("approved", "partially_approved")


# ── which materials are gated ─────────────────────────────────────────────────
async def controlled_category(session: AsyncSession) -> str:
    """The inventory Category that triggers the MTC + QC pipeline.

    Ported verbatim from entry.py's `_mtc_category`: an EXACT Category match
    (legacy config.py MTC_REQUIRED_CATEGORY = "Surface Shields"), never a
    description token — a token match would drag in every item whose
    description happens to mention shielding. 36 SAPs match today.
    """
    v = (await session.execute(text(
        "SELECT value FROM app_settings WHERE key = 'mtc_required_category'"))).scalar()
    return (v or "Surface Shields").strip()


async def is_controlled(session: AsyncSession, *, sap_code: str | None = None,
                        material_code: str | None = None) -> bool:
    """True when this material is in the controlled (Surface Shields) category.

    Accepts EITHER key. `dn_items` have only `Material_Code`, so a
    SAP-only signature is how the DN gate would silently pass everything.
    """
    cat = (await controlled_category(session)).lower()
    if sap_code:
        row = (await session.execute(text(
            'SELECT "Category" FROM inventory WHERE TRIM("SAP_Code") = TRIM(:s) LIMIT 1'
        ), {"s": str(sap_code)})).first()
        if row is not None:
            return str(row[0] or "").strip().lower() == cat
    if material_code:
        row = (await session.execute(text(
            'SELECT "Category" FROM inventory '
            'WHERE UPPER(TRIM("Material_Code")) = UPPER(TRIM(:m)) LIMIT 1'
        ), {"m": str(material_code)})).first()
        if row is not None:
            return str(row[0] or "").strip().lower() == cat
    return False


async def resolve_sap(session: AsyncSession, material_code: str | None) -> str | None:
    """Material_Code → SAP_Code, for the DN path which carries only the former."""
    if not material_code:
        return None
    row = (await session.execute(text(
        'SELECT "SAP_Code" FROM inventory '
        'WHERE UPPER(TRIM("Material_Code")) = UPPER(TRIM(:m)) LIMIT 1'
    ), {"m": str(material_code)})).first()
    return row[0] if row else None


# ── the MTC gate ──────────────────────────────────────────────────────────────
async def find_mtc(session: AsyncSession, *, sap_code: str | None = None,
                   material_code: str | None = None, site_id: str | None = None,
                   warehouse_id: str | None = None, po_item_id: int | None = None,
                   mtc_id: int | None = None) -> int | None:
    """The id of a certificate that covers this material here, or None.

    An explicit `mtc_id` is honoured only if it really is for this material —
    otherwise "attach any MTC you happen to have" becomes the bypass, and a
    mandatory document that accepts the wrong document is theatre.
    """
    c = mtc_t.c
    if mtc_id:
        row = (await session.execute(select(c["id"], c["SAP_Code"], c["Material_Code_Ref"])
               .where(c["id"] == int(mtc_id)))).first()
        if row is None:
            return None
        same_sap = sap_code and str(row[1] or "").strip() == str(sap_code).strip()
        same_mat = (material_code and str(row[2] or "").strip().upper()
                    == str(material_code).strip().upper())
        return row[0] if (same_sap or same_mat) else None

    conds = []
    if sap_code:
        conds.append(func.trim(c["SAP_Code"]) == str(sap_code).strip())
    elif material_code:
        conds.append(func.upper(func.trim(c["Material_Code_Ref"]))
                     == str(material_code).strip().upper())
    else:
        return None
    if warehouse_id:
        conds.append(func.coalesce(c["Warehouse_ID"], "") == warehouse_id)
    elif site_id:
        conds.append(func.coalesce(c["Site_ID"], "") == site_id)
    if po_item_id is not None:
        conds.append(c["po_item_id"] == int(po_item_id))
    stmt = select(c["id"])
    for cond in conds:
        stmt = stmt.where(cond)
    return (await session.execute(stmt.order_by(c["id"].desc()).limit(1))).scalar_one_or_none()


async def assert_mtc(session: AsyncSession, *, sap_code: str | None = None,
                     material_code: str | None = None, site_id: str | None = None,
                     warehouse_id: str | None = None, po_item_id: int | None = None,
                     mtc_id: int | None = None, where: str = "this movement") -> int | None:
    """Raise 422 unless a controlled material has a certificate. Returns its id.

    Uncontrolled materials return None and are never asked for anything —
    the gate covers 36 SAPs, not the 466-row master.
    """
    if not await is_controlled(session, sap_code=sap_code, material_code=material_code):
        return None
    found = await find_mtc(session, sap_code=sap_code, material_code=material_code,
                           site_id=site_id, warehouse_id=warehouse_id,
                           po_item_id=po_item_id, mtc_id=mtc_id)
    if found is None:
        label = sap_code or material_code
        raise HTTPException(
            422, f"{label} is a Surface Shield — a Material Test Certificate is "
                 f"mandatory before {where}. Upload it first (Entry → MTC), then retry.")
    return found


# ── the inspection ledger ─────────────────────────────────────────────────────
def decision_status(submitted: float, approved: float) -> str:
    """approved | partially_approved | rejected, from the two quantities.

    One definition so the route, the service and the tests cannot disagree
    about what "partially approved" means. The 1e-9 tolerance is the same
    float slack `warehouse.receive()` already uses on delivered quantities.
    """
    if approved <= 1e-9:
        return "rejected"
    if approved >= float(submitted) - 1e-9:
        return "approved"
    return "partially_approved"


async def open_inspection(session: AsyncSession, *, sap_code: str,
                          material_code: str | None, lot: str | None,
                          qty: float, source_type: str, source_ref: str,
                          site_id: str | None = None, warehouse_id: str | None = None,
                          mtc_document_id: int | None = None,
                          created_by: str = "system") -> int | None:
    """Open a pending inspection for controlled goods that just arrived.

    IDEMPOTENT by construction: `ON CONFLICT DO NOTHING` against the unique
    (source_type, source_ref, SAP_Code, Lot_Number). This is not defensive
    tidiness — the caller is inside a warehouse-receive or DN-receive
    transaction that can legitimately be retried, and a duplicate row would
    split the approved quantity across two ledger entries and let the
    issuance guard authorise the same physical units twice.

    Returns the new inspection id, or None when the material is not
    controlled or an inspection already existed.
    """
    if not await is_controlled(session, sap_code=sap_code, material_code=material_code):
        return None
    stmt = pg_insert(inspections_t).values(
        Site_ID=site_id or None, Warehouse_ID=warehouse_id or None,
        SAP_Code=str(sap_code).strip(), Material_Code=material_code,
        Lot_Number=(lot or None), source_type=source_type, source_ref=str(source_ref),
        mtc_document_id=mtc_document_id, submitted_qty=float(qty),
        approved_qty=0, rejected_qty=0, status="pending", created_by=created_by,
    ).on_conflict_do_nothing(constraint="uq_qc_inspection_source")
    new_id = (await session.execute(
        stmt.returning(inspections_t.c["id"]))).scalar_one_or_none()
    if new_id is None:
        return None
    await write_audit(session, created_by, "QC_INSPECTION_OPEN", "qc_inspections",
                      f"id={new_id} {sap_code} lot={lot or '-'} qty={float(qty):g} "
                      f"src={source_type}:{source_ref}")
    place = site_id or warehouse_id or "-"
    await dispatch(
        session, event_key="qc_inspection_required", severity="warning",
        recipient_role="qc", recipient_site=site_id or None,
        recipient_warehouse=warehouse_id or None, wa_template="action_required",
        title=f"Quality inspection needed — {sap_code}",
        body=(f"{float(qty):g} unit(s) of {sap_code} arrived at {place} "
              f"(lot {lot or '—'}). Check the material and its MTC, then approve "
              "or reject. Nothing can be issued to the field until you do."),
        link_page="/qc/inspections", related_table="qc_inspections",
        related_ref=new_id, created_by=created_by)
    return new_id


# ── the issuance gate ─────────────────────────────────────────────────────────
#
# ACCOUNTING NOTE, and the two judgement calls inside it.
#
# 1. Clearance pools at (Site_ID, SAP_Code), NOT at (site, SAP, lot).
#    At STAGE time the lot is frequently unknown: `stage_consumption` stores
#    whatever the SK typed, and a blank one is resolved by FEFO later, at
#    COMMIT (`post_consumption` → `fefo_lot`). A lot-keyed ledger would
#    therefore have to either block every un-lotted issue or wave it through,
#    and both are wrong. Pooling by material at the site is correct in both
#    cases and stays conservative: you can never issue more than QC approved.
#    The lot is still recorded on the inspection, and named in the refusal.
#
# 2. Only issues made ON OR AFTER quality control started for that material
#    at that site are counted against the approved pool. Without that
#    boundary the 1,133 rows of historical consumption already in the ledger
#    would exceed any approval on day one and block the material forever.
#    The boundary is the earliest inspection's date, and `consumption."Date"`
#    is a text ISO date, so a same-day issue entered BEFORE the inspection
#    was opened is counted too. That over-counts by at most one day's issues
#    and errs toward refusing — the safe direction for a quality gate.
#
# In-flight staged issues (`pending_issues` at pending_hod) count as well.
# They have to: two staged issues each checked only against committed
# consumption would both pass, and the second one is the over-issue. There
# is no allocation state to unwind, because a rejected pending row is
# deleted and its quantity frees itself.

async def _cleared_totals(session: AsyncSession, *, sap: str, site: str) -> dict:
    insp = inspections_t.c
    # ONE pass over qc_inspections for all four numbers. This used to be three
    # separate SELECTs with the SAME predicate — sum+min, then count, then a
    # filtered count — so the table was scanned three times to answer three
    # questions about one row set. It runs on the hot path (every issue of a
    # controlled material, at BOTH the staging and the approval gate) and once
    # per material inside the health probe, so it is worth the aggregate form.
    #
    # `COUNT(*) FILTER (WHERE …)` is Postgres' conditional aggregate, which is
    # exactly the third query folded in. The semantics are identical, and the
    # test below the fold compares the two forms rather than trusting that.
    approved, since, total_inspections, pending_inspections = (await session.execute(
        select(func.coalesce(func.sum(insp["approved_qty"]), 0.0),
               func.min(insp["created_at"]),
               func.count(),
               func.count().filter(insp["status"] == "pending"))
        .where(func.trim(insp["SAP_Code"]) == sap,
               func.coalesce(insp["Site_ID"], "") == site))).first()

    boundary = (since.date().isoformat() if isinstance(since, _dt.datetime)
                else (str(since)[:10] if since else None))
    issued = 0.0
    if boundary:
        cc = consumption_t.c
        issued = float((await session.execute(
            select(func.coalesce(func.sum(cc["Quantity"]), 0.0))
            .where(func.trim(cc["SAP_Code"]) == sap,
                   func.coalesce(cc["Site_ID"], "") == site,
                   cc["Date"] >= boundary))).scalar_one() or 0.0)
        pi = pending_issues_t.c
        issued += float((await session.execute(
            select(func.coalesce(func.sum(pi["Quantity"]), 0.0))
            .where(func.trim(pi["SAP_Code"]) == sap,
                   func.coalesce(pi["Site_ID"], "") == site,
                   pi["status"] == "pending_hod"))).scalar_one() or 0.0)
    approved = float(approved or 0.0)
    return {"approved_qty": approved, "issued_qty": issued,
            "available_for_issue": round(approved - issued, 6),
            "inspections": total_inspections,
            "pending_inspections": pending_inspections,
            "since": boundary}


async def clearance_summary(session: AsyncSession, *, sap_code: str, site_id: str,
                            lot: str | None = None) -> dict:
    """The numbers the issue form shows, and the gate below enforces."""
    sap = str(sap_code).strip()
    controlled = await is_controlled(session, sap_code=sap)
    if not controlled:
        return {"SAP_Code": sap, "Site_ID": site_id, "controlled": False,
                "blocked": False,
                "reason": "not a Surface Shield — no quality clearance needed"}
    t = await _cleared_totals(session, sap=sap, site=site_id)
    return {"SAP_Code": sap, "Site_ID": site_id, "controlled": True,
            "blocked": t["available_for_issue"] <= 1e-9, **t}


async def assert_qc_cleared(session: AsyncSession, *, sap_code: str, site_id: str,
                            qty: float, lot: str | None = None,
                            actor: str = "system") -> None:
    """HARD BLOCK: refuse to issue controlled material QC has not released.

    ⚠️ This is the first hard block on the issue path, and it does NOT
    overturn the standing "FEFO + over-issue stay allow-and-log" rule. That
    rule is about STOCK arithmetic — issuing more than the shelf holds is
    permitted and recorded, because the shelf is often right and the ledger
    often lags. This is about QUALITY STATUS, it was separately authorised
    by the operator on 2026-08-09, and it covers only the 36 SAPs in the
    controlled category. FEFO and over-issue behaviour on everything else is
    untouched, and this must never be implemented by promoting the existing
    FEFO warning to an error.
    """
    sap = str(sap_code).strip()
    if not await is_controlled(session, sap_code=sap):
        return
    t = await _cleared_totals(session, sap=sap, site=site_id)
    want = float(qty)
    if want <= t["available_for_issue"] + 1e-9:
        return

    if t["inspections"] == 0:
        detail = (f"{sap} is a Surface Shield and no quality inspection exists for "
                  f"it at {site_id}. It cannot be issued to the field until a QC "
                  "has checked the material and its MTC.")
    elif t["approved_qty"] <= 1e-9:
        detail = (f"{sap} is a Surface Shield awaiting quality approval at {site_id} "
                  f"({t['pending_inspections']} inspection(s) still pending, nothing "
                  "approved). It cannot be issued until a QC approves a quantity.")
    else:
        detail = (f"{sap} at {site_id}: QC has approved {t['approved_qty']:g} and "
                  f"{t['issued_qty']:g} is already issued or staged, leaving "
                  f"{t['available_for_issue']:g} — not enough for {want:g}. "
                  "Ask the QC to inspect the remaining stock.")
    if lot:
        detail += f" (lot {lot})"
    raise HTTPException(422, detail)
