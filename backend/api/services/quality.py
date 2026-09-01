"""
backend/api/services/quality.py — the MTC gate and the QC clearance gate.

Part of QSEP (Quality · Safety · Employees · Procurement, 2026-08).

Two rules live here, and they are deliberately SEPARATE — but note that BOTH
now bind at the same end of the chain, and that is a deliberate 2026-08-12
change, not an accident:

  **The MTC rule is about PAPERWORK and it binds at ISSUE.** A Material Test
  Certificate is mandatory before a Store Keeper hands a Surface-Shield
  material to the field. Goods may be RECEIVED — into a warehouse, or at a
  site, from Logistics or straight from a vendor — with no certificate at
  all, and they may be put on a Delivery Note and travel.

  **The QC rule is about INSPECTION and it also binds at ISSUE.** A Store
  Keeper cannot issue controlled material until a QC has approved a quantity.

--------------------------------------------------------------------------
WHY THE MTC GATE MOVED (2026-08-12)

It used to bind at DISPATCH: mandatory at warehouse goods-in and again at DN
creation. That is where the certificate rule *reads* most naturally — paper
should travel with the goods — and in live use it was a hard workflow
blocker. A truck arrives from the vendor; the certificate is still in
somebody's inbox; the warehouse physically has the material and the system
refuses to admit it exists. The stock is then invisible everywhere: not on
the shelf report, not on a DN, not in anyone's plan, while it sits on a
pallet in the yard.

Refusing to RECORD something that has physically happened is the one thing
an inventory system must never do. The material arrived. What the system
gets to control is what happens NEXT, and that is exactly where the gate
sits now: the moment before it goes to a worker, which is the moment the
certificate actually protects anyone.

So the chain is now:

    receive (no MTC needed) → travel (no MTC needed) → ISSUE ← both gates

and the material is loudly visible as blocked the whole way — every receipt
of a controlled item with no certificate notifies Logistics to chase it,
rather than silently refusing the receipt.

--------------------------------------------------------------------------
CERTIFICATES ARE SHARED DOWN THE CHAIN, NOT RE-UPLOADED

The corollary of moving the gate to issue is that the person who hits the
gate (the site Store Keeper) is usually NOT the person holding the document
(Logistics, who got it with the PO, or the warehouse clerk who got it with
the delivery). Making the SK upload their own copy would mean the same PDF
stored three times, three different "the" certificates for one lot, and an
SK blocked by paperwork that demonstrably exists upstream.

`visible_mtc()` therefore resolves a certificate along the supply chain that
actually reaches this site — uploaded against the PO line, against the DN,
at the warehouse that shipped it, or at the site itself. See its docstring
for the precedence and for why it is not simply "any MTC for this material".

--------------------------------------------------------------------------
WHY THIS MODULE EXISTS AT ALL

The category test used to be two inlined copies in entry.py, wired into
exactly two call sites (`POST /entry/receipts` and the receipt branch of
`/entry/bulk`). Three paths walked straight past it:

  * `warehouse.receive()`      — warehouse goods-in against a PO assignment
  * `warehouse.create_dn()`    — so shields could be SHIPPED with no MTC
  * `warehouse.stage_dn_receipt()` — inserts pending_receipts directly

One definition, five call sites. A gate with copies is a gate with holes.
Those paths still call in here — they just record and warn now instead of
refusing.

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
                        material_code: str | None = None,
                        category: str | None = None) -> bool:
    """True when this material is in the controlled (Surface Shields) category.

    Accepts EITHER key. `dn_items` have only `Material_Code`, so a
    SAP-only signature is how the DN gate would silently pass everything.

    `category` lets a caller that is looping over many materials read
    `controlled_category()` ONCE and hand it in. Without it every iteration
    re-read the same `app_settings` row — a value that cannot change inside the
    loop — which doubled the query count of `execution.qsep_check` for no
    information. Omitted, the behaviour is exactly as it was.
    """
    cat = (category if category is not None
           else await controlled_category(session)).lower()
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


# The certificate a SITE can see, following the chain the goods travelled.
#
# ⚠️ NOT "any MTC for this material anywhere". A certificate attests to a
# specific batch from a specific mill run. Matching on material alone would
# mean one upload in Riyadh permanently clears every shipment of that SAP to
# every site in the country, which is a gate that opens once and never closes
# again. Every branch below is a real, recorded link between the document and
# THIS site: the document names the site, or it names a DN sent here, or it
# names a PO line destined here, or it names a PO line that a DN carried here,
# or it sits at a warehouse that has actually shipped this material here.
#
# Ranked, first hit wins, so the answer also explains ITSELF — a store keeper
# who is told "cleared by the certificate on DN-WH-01-20260812-003" can go and
# look at that note. "Cleared by some certificate" is not an audit trail.
_VISIBLE_MTC_SQL = """
WITH m AS (
    SELECT id, "Site_ID", "Warehouse_ID", "DN_Number", po_item_id,
           mtc_number, "Lot_Number", file_name
      FROM mtc_documents
     WHERE TRIM("SAP_Code") = TRIM(CAST(:sap AS text))
        OR UPPER(TRIM(COALESCE("Material_Code_Ref", "Material_Code", '')))
           = UPPER(TRIM(CAST(:mat AS text)))
)
SELECT id, mtc_number, "Lot_Number", file_name, "DN_Number", po_item_id,
       "Site_ID", "Warehouse_ID", source, rank
  FROM (
    SELECT m.*, 1 AS rank, 'site' AS source
      FROM m
     WHERE COALESCE(m."Site_ID", '') = CAST(:site AS text)
    UNION ALL
    SELECT m.*, 2, 'dn'
      FROM m JOIN delivery_notes dn ON dn."DN_Number" = m."DN_Number"
     WHERE COALESCE(dn."Site_ID", '') = CAST(:site AS text)
    UNION ALL
    SELECT m.*, 3, 'po'
      FROM m JOIN po_items pi ON pi.id = m.po_item_id
             JOIN purchase_orders po ON po."PO_Number" = pi."PO_Number"
     WHERE COALESCE(po."Site_ID", '') = CAST(:site AS text)
    UNION ALL
    SELECT m.*, 4, 'dn_line'
      FROM m JOIN dn_items di ON di.po_item_id = m.po_item_id
             JOIN delivery_notes dn2 ON dn2."DN_Number" = di."DN_Number"
     WHERE COALESCE(dn2."Site_ID", '') = CAST(:site AS text)
    UNION ALL
    SELECT m.*, 5, 'warehouse'
      FROM m JOIN delivery_notes dn3
                  ON COALESCE(dn3."Warehouse_ID", '') = COALESCE(m."Warehouse_ID", '')
             JOIN dn_items di3 ON di3."DN_Number" = dn3."DN_Number"
     WHERE m."Warehouse_ID" IS NOT NULL
       AND COALESCE(dn3."Site_ID", '') = CAST(:site AS text)
       AND UPPER(TRIM(COALESCE(di3."Material_Code", '')))
           = UPPER(TRIM(CAST(:mat AS text)))
  ) x
 ORDER BY rank, id DESC
 LIMIT 1
"""

_MTC_SOURCE_LABEL = {
    "site": "uploaded at this site",
    "dn": "attached to Delivery Note {dn}",
    "po": "attached to the purchase order line for this site",
    "dn_line": "attached to the PO line delivered on Delivery Note {dn}",
    "warehouse": "held at warehouse {wh}, which shipped this material here",
}


async def visible_mtc(session: AsyncSession, *, sap_code: str | None = None,
                      material_code: str | None = None,
                      site_id: str | None = None) -> dict | None:
    """The certificate this SITE may rely on, and where it came from.

    This is the "don't upload it twice" rule from the 2026-08-12 ruling:
    Logistics attaches the MTC to the PO, or the warehouse attaches it to the
    DN, and the site Store Keeper inherits it. Returns a dict with `id` and
    `source` (see `_MTC_SOURCE_LABEL`), or None.

    Fails CLOSED on a missing site. `site_id=''` is a scoped account with no
    site of its own, and `COALESCE("Site_ID",'') = ''` would happily match
    every certificate that names no site — turning an unbound account into
    the one account that can issue anything.
    """
    site = (site_id or "").strip()
    if not site:
        return None
    if not sap_code and not material_code:
        return None
    sap = str(sap_code).strip() if sap_code else None
    mat = str(material_code).strip() if material_code else None
    if mat is None and sap:
        mat = await _material_code(session, sap)
    if sap is None and mat:
        sap = await resolve_sap(session, mat)
    row = (await session.execute(text(_VISIBLE_MTC_SQL),
                                 {"sap": sap, "mat": mat, "site": site})).mappings().first()
    if row is None:
        return None
    d = dict(row)
    d["label"] = _MTC_SOURCE_LABEL[d["source"]].format(
        dn=d.get("DN_Number") or "—", wh=d.get("Warehouse_ID") or "—")
    return d


async def _material_code(session: AsyncSession, sap_code: str) -> str | None:
    row = (await session.execute(text(
        'SELECT "Material_Code" FROM inventory '
        'WHERE TRIM("SAP_Code") = TRIM(:s) LIMIT 1'), {"s": str(sap_code)})).first()
    return row[0] if row else None


async def note_mtc(session: AsyncSession, *, sap_code: str | None = None,
                   material_code: str | None = None, site_id: str | None = None,
                   warehouse_id: str | None = None, po_item_id: int | None = None,
                   mtc_id: int | None = None) -> int | None:
    """Link the certificate if one exists here. **Never raises.**

    The non-blocking half of the 2026-08-12 ruling, used by every RECEIVE and
    DISPATCH path. Goods move whether or not the paper has caught up; all this
    does is record which certificate covered the movement, so the document can
    later be followed from the receipt or the DN it cleared.
    """
    if not await is_controlled(session, sap_code=sap_code, material_code=material_code):
        return None
    return await find_mtc(session, sap_code=sap_code, material_code=material_code,
                          site_id=site_id, warehouse_id=warehouse_id,
                          po_item_id=po_item_id, mtc_id=mtc_id)


async def warn_mtc_missing(session: AsyncSession, *, sap_code: str | None,
                           material_code: str | None, where: str,
                           site_id: str | None = None,
                           warehouse_id: str | None = None,
                           created_by: str = "system") -> bool:
    """Controlled goods arrived with no certificate — tell Logistics to chase it.

    This is what the old hard block is replaced BY, and the replacement has to
    be loud or the ruling just deletes a control. The material is now sitting
    somewhere unissuable, and the only people who can fix that are the ones who
    can get the document from the supplier. Returns True when it warned.
    """
    if not await is_controlled(session, sap_code=sap_code, material_code=material_code):
        return False
    label = sap_code or material_code
    place = site_id or warehouse_id or "—"
    await dispatch(
        session, event_key="mtc_missing", severity="warning",
        recipient_role="logistics", wa_template="action_required",
        title=f"MTC outstanding — {label}",
        body=(f"{label} is a Surface Shield and arrived at {place} ({where}) with "
              "no Material Test Certificate on file. The goods are booked in, but "
              "no store keeper can issue them to the field until the certificate "
              "is uploaded. Please obtain it from the supplier."),
        link_page="/logistics", related_table="mtc_documents",
        created_by=created_by)
    return True


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


async def assert_mtc_for_issue(session: AsyncSession, *, sap_code: str,
                               site_id: str, actor: str = "system") -> int | None:
    """HARD BLOCK: refuse to issue controlled material with no certificate.

    The 2026-08-12 ruling put the MTC gate here and nowhere else. Receiving is
    never refused; this is. Returns the id of the certificate that cleared the
    issue, so the caller can record which document authorised it.

    The refusal names every place a certificate WOULD have been found, because
    the store keeper standing at the gate is usually not the person holding the
    document — telling them only "no MTC" sends them hunting for a PDF that
    Logistics may already have.
    """
    sap = str(sap_code).strip()
    if not await is_controlled(session, sap_code=sap):
        return None
    found = await visible_mtc(session, sap_code=sap, site_id=site_id)
    if found is not None:
        return int(found["id"])
    raise HTTPException(
        422,
        f"{sap} is a Surface Shield and no Material Test Certificate covers it at "
        f"{site_id or '—'}. It may be received and stored without one, but it "
        "cannot be issued to the field until the certificate is on file. "
        "Logistics can attach it to the purchase order, the warehouse can attach "
        "it to the delivery note, or you can upload it here — any of the three "
        "clears this material for issue at this site.")


async def mtc_status(session: AsyncSession, *, sap_code: str,
                     site_id: str) -> dict:
    """Whether the paperwork half of the issue gate is satisfied, and by what."""
    sap = str(sap_code).strip()
    if not await is_controlled(session, sap_code=sap):
        return {"controlled": False, "mtc_ok": True, "mtc_document_id": None,
                "mtc_source": None, "mtc_label": None}
    found = await visible_mtc(session, sap_code=sap, site_id=site_id)
    if found is None:
        return {"controlled": True, "mtc_ok": False, "mtc_document_id": None,
                "mtc_source": None,
                "mtc_label": "no Material Test Certificate on file for this site"}
    return {"controlled": True, "mtc_ok": True,
            "mtc_document_id": int(found["id"]),
            "mtc_number": found.get("mtc_number"),
            "mtc_source": found["source"], "mtc_label": found["label"]}


async def clearance_summary(session: AsyncSession, *, sap_code: str, site_id: str,
                            lot: str | None = None) -> dict:
    """The numbers the issue form shows, and the gates below enforce.

    Carries BOTH halves of the issue gate, because a form that greys out the
    button for one reason and then fails on the other is worse than no form
    hint at all — the store keeper fixes the inspection, retries, and gets
    refused again for a document nobody mentioned.
    """
    sap = str(sap_code).strip()
    controlled = await is_controlled(session, sap_code=sap)
    if not controlled:
        return {"SAP_Code": sap, "Site_ID": site_id, "controlled": False,
                "blocked": False, "mtc_ok": True, "mtc_document_id": None,
                "mtc_source": None, "mtc_label": None,
                "reason": "not a Surface Shield — no quality clearance needed"}
    t = await _cleared_totals(session, sap=sap, site=site_id)
    paper = await mtc_status(session, sap_code=sap, site_id=site_id)
    return {"SAP_Code": sap, "Site_ID": site_id, "controlled": True,
            "blocked": t["available_for_issue"] <= 1e-9 or not paper["mtc_ok"],
            **paper, **t}


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
