"""
backend/api/services/warehouse.py — the warehouse receiving + DN flow.

Ports the warehouse side from database.py:
  * assignments_for   — list_assignments_for_warehouse() (PRICES never joined)
  * acknowledge       — acknowledge_assignment()
  * receive           — record_warehouse_receipt()  (bump Delivered_Qty, roll status)
  * create_dn         — create_delivery_note()       (RL/BL separation + available guard)
  * ship_dn           — mark a DN in_transit (outbound)

Prices (Unit_Price / Total_Price) are never returned to the warehouse role.
"""
from __future__ import annotations

import datetime as _dt

from sqlalchemy import case, func, insert, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from . import quality
from .ledger import _MD, write_audit
from .notifications import dispatch, notify

po_assignments_t = _MD.tables["po_assignments"]
purchase_orders_t = _MD.tables["purchase_orders"]
po_items_t = _MD.tables["po_items"]
delivery_notes_t = _MD.tables["delivery_notes"]
dn_items_t = _MD.tables["dn_items"]
pending_receipts_t = _MD.tables["pending_receipts"]
inventory_t = _MD.tables["inventory"]


def _rows(res):
    return [dict(m) for m in res.mappings().all()]


# --- reads -------------------------------------------------------------------
async def assignments_for(session: AsyncSession, warehouse_id: str, statuses: list[str] | None):
    where = 'a."Warehouse_ID" = :wh'
    params: dict = {"wh": warehouse_id}
    if statuses:
        keys = ",".join(f":s{i}" for i in range(len(statuses)))
        where += f" AND a.status IN ({keys})"
        params.update({f"s{i}": s for i, s in enumerate(statuses)})
    sql = text(f'''
        SELECT a.id AS assignment_id, a."PO_Number", a."Expected_Delivery",
               a.assigned_by, a.assigned_at, a.acknowledged_at, a.status, a.notes,
               po."PR_Number", po."Site_ID", po."Vendor_Name", po."PO_Date"
        FROM po_assignments a
        JOIN purchase_orders po ON po."PO_Number" = a."PO_Number"
        WHERE {where}
        ORDER BY a.assigned_at DESC''')
    return _rows(await session.execute(sql, params))


async def assignment_items(session: AsyncSession, assignment_id: int):
    """PO items for the assignment (no prices)."""
    a = (await session.execute(select(po_assignments_t.c["PO_Number"])
         .where(po_assignments_t.c["id"] == assignment_id))).first()
    if a is None:
        return []
    stmt = select(
        po_items_t.c["id"], po_items_t.c["line_no"], po_items_t.c["Material_Code"],
        po_items_t.c["Description"], po_items_t.c["Qty"], po_items_t.c["UOM"],
        po_items_t.c["Delivered_Qty"], po_items_t.c["Returned_Qty"],
        po_items_t.c["rl_bl_family"], po_items_t.c["line_status"],
    ).where(po_items_t.c["PO_Number"] == a[0]).order_by(po_items_t.c["line_no"])
    return _rows(await session.execute(stmt))


async def dns_for(session: AsyncSession, warehouse_id: str | None, status: str | None):
    conds, params = [], {}
    if warehouse_id:
        conds.append('"Warehouse_ID" = :wh')
        params["wh"] = warehouse_id
    if status:
        conds.append("status = :st")
        params["st"] = status
    where = ("WHERE " + " AND ".join(conds)) if conds else ""
    sql = text(f'''
        SELECT "DN_Number", "PO_Number", "Warehouse_ID", "Site_ID", rl_bl_family,
               "DN_Date", "Vehicle_No", "Driver_Name", status, created_by
        FROM delivery_notes {where}
        ORDER BY "DN_Number" DESC LIMIT 500''')
    return _rows(await session.execute(sql, params))


async def dn_site(session: AsyncSession, dn_number: str) -> str | None:
    """Destination Site_ID of one DN, or None when the DN doesn't exist — the
    lookup the site-side scope guard needs before serving its lines."""
    return (await session.execute(select(delivery_notes_t.c["Site_ID"])
            .where(delivery_notes_t.c["DN_Number"] == dn_number))).scalar_one_or_none()


async def dn_lines(session: AsyncSession, dn_number: str):
    stmt = select(
        dn_items_t.c["id"], dn_items_t.c["po_item_id"], dn_items_t.c["Material_Code"],
        dn_items_t.c["Description"], dn_items_t.c["Qty"], dn_items_t.c["UOM"],
        dn_items_t.c["Lot_Number"], dn_items_t.c["Expiry_Date"],
        dn_items_t.c["rl_bl_family"], dn_items_t.c["status"],
    ).where(dn_items_t.c["DN_Number"] == dn_number).order_by(dn_items_t.c["id"])
    return _rows(await session.execute(stmt))


# --- mutations ---------------------------------------------------------------
async def acknowledge(session: AsyncSession, *, username: str, assignment_id: int) -> dict:
    res = await session.execute(update(po_assignments_t).where(
        (po_assignments_t.c["id"] == assignment_id)
        & (po_assignments_t.c["status"] == "assigned")
    ).values(status="acknowledged", acknowledged_at=func.now(), acknowledged_by=username))
    if res.rowcount == 0:
        return {"error": "assignment not found or already acknowledged"}
    await write_audit(session, username, "ACK_ASSIGNMENT", "po_assignments",
                      f"id={assignment_id}")
    return {"acknowledged": True, "id": assignment_id}


async def receive(session: AsyncSession, *, username: str, assignment_id: int,
                  received_map: dict, mtc_map: dict | None = None) -> dict:
    """Record goods arriving at the warehouse against a PO assignment.

    QSEP added two things to this function, both for controlled
    (Surface-Shield) lines only — the other 430 materials in the master go
    through exactly as before:

      * **A Material Test Certificate is recorded when there is one.**
        `mtc_map` maps the same PO line ids as `received_map` to an
        `mtc_documents.id`. ⚠️ It is NOT mandatory here, and that is the
        2026-08-12 ruling: this function briefly refused uncertified shields
        and that turned out to be a hard workflow blocker — the truck is in
        the yard, the certificate is in somebody's inbox, and refusing the
        receipt makes real material invisible to the whole system. An
        uncertified line is booked in and Logistics is told to chase the
        document. The block now sits at ISSUE (`assert_mtc_for_issue`), which
        is the moment the certificate actually protects a worker.
      * **A quality inspection is opened**, so the warehouse QC is told there
        is material to look at. Opening it does NOT gate the delivery: the
        operator ruled on 2026-08-09 that uninspected material may travel to
        site, and only ISSUE is blocked. Stranding a truck behind an inspector
        who is not on shift is the failure that ruling avoids.
    """
    a = (await session.execute(select(
        po_assignments_t.c["PO_Number"], po_assignments_t.c["status"],
        po_assignments_t.c["Warehouse_ID"])
        .where(po_assignments_t.c["id"] == assignment_id))).first()
    if a is None:
        return {"error": "assignment not found"}
    if a[1] in ("closed", "cancelled"):
        return {"error": f"assignment is {a[1]}"}
    po_number, warehouse_id = a[0], a[2]
    mtc_map = mtc_map or {}

    affected = 0
    inspections: list[int] = []
    received_lines: list[dict] = []
    missing_mtc: list[str] = []
    for raw_id, raw_qty in received_map.items():
        try:
            item_id, qty = int(raw_id), float(raw_qty)
        except (TypeError, ValueError):
            continue
        if qty <= 0:
            continue
        line = (await session.execute(select(
            po_items_t.c["Qty"], po_items_t.c["Delivered_Qty"], po_items_t.c["Returned_Qty"],
            po_items_t.c["Material_Code"], po_items_t.c["Description"],
            po_items_t.c["rl_bl_family"])
            .where((po_items_t.c["id"] == item_id) & (po_items_t.c["PO_Number"] == po_number)))).first()
        if line is None:
            continue
        ordered, already, returned = float(line[0] or 0), float(line[1] or 0), float(line[2] or 0)
        new_delivered = already + qty
        if new_delivered - returned > ordered + 1e-9:
            return {"error": f"cannot receive {qty}: over-delivers line {item_id} "
                             f"(ordered {ordered}, already {already})"}
        # MTC. Recorded, never demanded — see the 2026-08-12 ruling in
        # services/quality.py. Returns the covering certificate's id when one
        # exists, and None for an uncertified shield as well as for the other
        # 430 materials; the two cases are told apart below.
        material_code = line[3]
        mtc_id = await quality.note_mtc(
            session, material_code=material_code, warehouse_id=warehouse_id,
            po_item_id=item_id, mtc_id=mtc_map.get(str(item_id), mtc_map.get(item_id)))
        # `None` means "no certificate" for a shield and "never asked" for
        # everything else, so the controlled test has to be re-run rather than
        # inferred — otherwise the audit line and the chase-up notification
        # would name all 430 ordinary materials.
        if mtc_id is None and await quality.is_controlled(
                session, material_code=material_code):
            missing_mtc.append(str(material_code))
        new_status = "delivered" if new_delivered - returned >= ordered - 1e-9 else "partially_delivered"
        await session.execute(update(po_items_t).where(po_items_t.c["id"] == item_id)
                              .values(Delivered_Qty=new_delivered, line_status=new_status))
        sap = await quality.resolve_sap(session, material_code)
        if sap:
            iid = await quality.open_inspection(
                session, sap_code=sap, material_code=material_code, lot=None, qty=qty,
                source_type="warehouse_receipt", source_ref=f"{assignment_id}:{item_id}",
                warehouse_id=warehouse_id, mtc_document_id=mtc_id, created_by=username)
            if iid:
                inspections.append(iid)
        received_lines.append({"po_item_id": item_id, "qty": qty,
                               "rl_bl_family": line[5],
                               "warehouse_id": warehouse_id})
        affected += 1

    if affected == 0:
        return {"error": "no valid line items in received map"}

    agg = (await session.execute(select(
        func.count(),
        func.sum(case((po_items_t.c["line_status"] == "delivered", 1), else_=0)),
    ).where(po_items_t.c["PO_Number"] == po_number))).first()
    total, done = agg[0], (agg[1] or 0)
    if done and total == done:
        await session.execute(update(po_assignments_t).where(po_assignments_t.c["id"] == assignment_id).values(status="received"))
        await session.execute(update(purchase_orders_t).where(purchase_orders_t.c["PO_Number"] == po_number).values(status="delivered"))
    else:
        await session.execute(update(po_assignments_t).where(po_assignments_t.c["id"] == assignment_id).values(status="partial"))
        await session.execute(update(purchase_orders_t).where(purchase_orders_t.c["PO_Number"] == po_number).values(status="partially_delivered"))

    await write_audit(session, username, "WAREHOUSE_RECEIVE", "po_items",
                      f"PO={po_number} assignment={assignment_id} lines={affected}"
                      + (f" qc_inspections={len(inspections)}" if inspections else "")
                      + (f" mtc_missing={','.join(sorted(set(missing_mtc)))}"
                         if missing_mtc else ""))
    # The goods are in. Whoever can get the certificate is now the only person
    # who can unblock them, and they are not in this room.
    for mat in sorted(set(missing_mtc)):
        await quality.warn_mtc_missing(
            session, sap_code=None, material_code=mat,
            where=f"goods-in on PO {po_number}", warehouse_id=warehouse_id,
            created_by=username)

    # QSEP slice 6 — draft the DNs the destination site is waiting for. The
    # PO's own Site_ID is the destination; a PO with no site is a warehouse
    # restock and has nowhere to send anything, so nothing is drafted.
    dest = (await session.execute(select(purchase_orders_t.c["Site_ID"])
            .where(purchase_orders_t.c["PO_Number"] == po_number))).scalar()
    auto_dns = await auto_draft_dns(
        session, username=username, po_number=po_number,
        assignment_id=assignment_id, site_id=dest, received=received_lines)
    # QSEP notification gap #2: goods physically arriving was audited and
    # otherwise silent, so nobody downstream learned the PO had landed.
    await dispatch(session, event_key="po_goods_received", severity="success",
                   recipient_role="logistics", wa_template="status_update",
                   title=f"Goods received at {warehouse_id} — PO {po_number}",
                   body=(f"{username} booked in {affected} line(s)."
                         + (f" {len(inspections)} awaiting quality inspection."
                            if inspections else "")),
                   link_page="/logistics", related_table="po_items",
                   related_ref=po_number, created_by=username)
    return {"received": True, "po_number": po_number, "lines": affected,
            "qc_inspections": inspections, "auto_drafted_dns": auto_dns}


async def _setting_on(session: AsyncSession, key: str, default: str = "1") -> bool:
    v = (await session.execute(text(
        "SELECT value FROM app_settings WHERE key = :k"), {"k": key})).scalar()
    return (v if v is not None else default).strip() != "0"


async def auto_draft_dns(session: AsyncSession, *, username: str, po_number: str,
                         assignment_id: int, site_id: str | None,
                         received: list[dict]) -> list[str]:
    """After goods-in, draft the Delivery Notes the site is waiting for.

    QSEP slice 6. It calls the EXISTING `create_dn()` once per group rather
    than writing its own INSERTs, and that is the whole design: create_dn
    already enforces RL/BL strict separation, the over-shipment guard
    (delivered − returned − already on live DNs), the MTC gate for
    Surface Shields and the DN numbering. Reimplementing any of those here
    would give the automated path different rules from the manual one, and
    the automated path is the one nobody watches.

    Grouped by `rl_bl_family` because create_dn REFUSES a mixed DN — one
    call per family is not an optimisation, it is the only shape that passes.

    Output is `status='draft'`. A human still submits it: the warehouse has
    to physically load a vehicle and name a driver, and a system that
    submitted on their behalf would be asserting a truck exists.

    ⚠️ Never raises. A failure here must not roll back the goods receipt —
    the stock genuinely arrived, and losing that because a downstream
    convenience failed would be a far worse outcome than a missing draft.
    The reason is recorded in the audit log and the clerk cuts the DN by hand.
    """
    if not site_id or not received:
        return []
    if not await _setting_on(session, "auto_draft_dn"):
        return []

    by_family: dict[str | None, list[dict]] = {}
    for line in received:
        by_family.setdefault(line.get("rl_bl_family"), []).append(line)

    created: list[str] = []
    for family, lines in by_family.items():
        try:
            res = await create_dn(
                session, username=username, po_number=po_number,
                warehouse_id=lines[0]["warehouse_id"], site_id=site_id,
                line_items=[{"po_item_id": ln["po_item_id"], "Qty": ln["qty"]}
                            for ln in lines],
                header={"Remarks": f"Auto-drafted from goods receipt "
                                   f"(assignment {assignment_id})"})
            if res.get("error"):
                await write_audit(
                    session, username, "AUTO_DN_SKIPPED", "delivery_notes",
                    f"PO={po_number} family={family or '-'} reason={res['error']}")
                continue
            dn = res["dn_number"]
            await session.execute(update(delivery_notes_t)
                                  .where(delivery_notes_t.c["DN_Number"] == dn)
                                  .values(auto_generated=1,
                                          source_assignment_id=assignment_id))
            created.append(dn)
        except Exception as e:  # noqa: BLE001 — see the docstring
            await write_audit(
                session, username, "AUTO_DN_FAILED", "delivery_notes",
                f"PO={po_number} family={family or '-'} "
                f"{type(e).__name__}: {str(e)[:200]}")

    if created:
        await write_audit(session, username, "AUTO_DN_CREATED", "delivery_notes",
                          f"PO={po_number} assignment={assignment_id} "
                          f"site={site_id} dns={','.join(created)}")
        await dispatch(
            session, event_key="dn_auto_drafted", recipient_role="warehouse_user",
            recipient_warehouse=received[0]["warehouse_id"],
            wa_template="action_required",
            title=f"{len(created)} Delivery Note(s) drafted for {site_id}",
            body=(f"Prepared from the goods receipt on PO {po_number}: "
                  f"{', '.join(created)}. Add the vehicle and driver, then submit."),
            link_page="/warehouse", related_table="delivery_notes",
            related_ref=created[0], created_by=username)
    return created


async def _generate_dn_number(session: AsyncSession, warehouse_id: str) -> str:
    today = _dt.date.today().isoformat().replace("-", "")
    prefix = f"DN-{warehouse_id}-{today}-"
    cnt = (await session.execute(select(func.count()).select_from(delivery_notes_t)
           .where(delivery_notes_t.c["DN_Number"].like(prefix + "%")))).scalar_one()
    return f"{prefix}{cnt + 1:03d}"


async def create_dn(session: AsyncSession, *, username: str, po_number: str, warehouse_id: str,
                    site_id: str, line_items: list[dict], header: dict | None = None) -> dict:
    if not line_items:
        return {"error": "at least one line item is required"}
    ids = [int(li["po_item_id"]) for li in line_items if li.get("po_item_id") is not None]
    if not ids:
        return {"error": "po_item_id missing on every line"}

    rows = (await session.execute(select(
        po_items_t.c["id"], po_items_t.c["Material_Code"], po_items_t.c["Description"],
        po_items_t.c["UOM"], po_items_t.c["rl_bl_family"], po_items_t.c["Qty"],
        po_items_t.c["Delivered_Qty"], po_items_t.c["Returned_Qty"],
    ).where((po_items_t.c["PO_Number"] == po_number) & (po_items_t.c["id"].in_(ids))))).all()
    by_id = {r[0]: r for r in rows}
    if len(by_id) != len(set(ids)):
        return {"error": "one or more line items not found on this PO"}

    families = {by_id[i][4] for i in ids}
    if len(families - {None}) > 1:
        return {"error": "RL/BL strict separation violated — prepare one DN per family"}
    family = next(iter(families - {None})) if families - {None} else None

    for li in line_items:
        iid = int(li["po_item_id"])
        qty = float(li.get("Qty") or 0)
        if qty <= 0:
            return {"error": f"Qty must be > 0 on line {iid}"}
        delivered, returned = float(by_id[iid][6] or 0), float(by_id[iid][7] or 0)
        shipped = (await session.execute(text(
            'SELECT COALESCE(SUM(di."Qty"),0) FROM dn_items di '
            'JOIN delivery_notes dn ON dn."DN_Number" = di."DN_Number" '
            "WHERE di.po_item_id = :iid AND dn.status NOT IN ('rejected','cancelled')"),
            {"iid": iid})).scalar_one()
        available = delivered - returned - float(shipped or 0)
        if qty > available + 1e-9:
            return {"error": f"line {iid}: shipping {qty} exceeds available {available:g} "
                             f"(delivered {delivered}, returned {returned}, on live DNs {float(shipped or 0):g})"}

    # QSEP — the MTC is RECORDED here, not demanded.
    #
    # ⚠️ This used to be a hard gate: a Surface Shield could not be put on a
    # Delivery Note without a certificate. The 2026-08-12 ruling moved the
    # block to issue, and moving it out of here specifically is the point —
    # leaving it would have reproduced the same workflow blocker one hop
    # later. The warehouse would be able to receive the material and then be
    # unable to send it anywhere, which is a stall with an extra step.
    #
    # Nothing about the TRAVEL of controlled material is gated now: neither
    # its certificate nor its inspection status (`assert_qc_cleared` is
    # deliberately absent here too). It is stopped once, at the site store
    # keeper's hand-over to a worker.
    #
    # Matching is on Material_Code because that is all a DN line has. A lookup
    # written against SAP_Code alone would find nothing here and silently
    # stamp no certificate on any note.
    mtc_ids: dict[int, int] = {}
    for li in line_items:
        iid = int(li["po_item_id"])
        found = await quality.note_mtc(
            session, material_code=by_id[iid][1], warehouse_id=warehouse_id,
            po_item_id=iid, mtc_id=li.get("mtc_document_id"))
        if found is not None:
            mtc_ids[iid] = found

    dn_number = await _generate_dn_number(session, warehouse_id)
    h = header or {}
    await session.execute(insert(delivery_notes_t).values(
        DN_Number=dn_number, PO_Number=po_number, Warehouse_ID=warehouse_id, Site_ID=site_id,
        rl_bl_family=family, DN_Date=h.get("DN_Date") or _dt.date.today().isoformat(),
        Vehicle_No=h.get("Vehicle_No"), Driver_Name=h.get("Driver_Name"),
        Driver_Phone=h.get("Driver_Phone"), Prepared_By=h.get("Prepared_By") or username,
        Remarks=h.get("Remarks"), status="draft", created_by=username))

    for li in line_items:
        iid = int(li["po_item_id"])
        base = by_id[iid]
        await session.execute(insert(dn_items_t).values(
            DN_Number=dn_number, po_item_id=iid, Material_Code=base[1], Description=base[2],
            Qty=float(li.get("Qty") or 0), UOM=base[3], Lot_Number=li.get("Lot_Number"),
            Expiry_Date=li.get("Expiry_Date"), Remarks=li.get("Remarks"),
            rl_bl_family=base[4], status="pending"))

    # Stamp the DN onto every certificate that cleared it, so "which note did
    # this material travel on" is answerable from the document itself.
    for mid in set(mtc_ids.values()):
        await session.execute(update(_MD.tables["mtc_documents"])
                              .where(_MD.tables["mtc_documents"].c["id"] == mid)
                              .values(DN_Number=dn_number))

    await write_audit(session, username, "CREATE_DN", "delivery_notes",
                      f"DN={dn_number} PO={po_number} site={site_id} lines={len(line_items)}"
                      + (f" mtc={sorted(set(mtc_ids.values()))}" if mtc_ids else ""))
    return {"created": True, "dn_number": dn_number, "lines": len(line_items),
            "mtc_documents": sorted(set(mtc_ids.values()))}


# --- DN multi-stage approval (Phase 6) --------------------------------------
# draft → (WH submit) pending_logistics → (Logistics vets date/logistics)
# pending_hod → (HOD vets content) hod_approved → (WH ship) in_transit →
# (SK receipt) received. A reject at either gate → 'rejected' (WH can resubmit).
async def _dn_row(session: AsyncSession, dn_number: str):
    return (await session.execute(select(
        delivery_notes_t.c["Site_ID"], delivery_notes_t.c["PO_Number"],
        delivery_notes_t.c["Warehouse_ID"], delivery_notes_t.c["created_by"],
        delivery_notes_t.c["status"],
    ).where(delivery_notes_t.c["DN_Number"] == dn_number))).first()


async def submit_dn(session: AsyncSession, *, username: str, dn_number: str) -> dict:
    res = await session.execute(update(delivery_notes_t).where(
        (delivery_notes_t.c["DN_Number"] == dn_number)
        & (delivery_notes_t.c["status"].in_(["draft", "prepared", "rejected"]))
    ).values(status="pending_logistics", rejection_reason=None))
    if res.rowcount == 0:
        return {"error": "DN not found or not in a submittable state"}
    await write_audit(session, username, "SUBMIT_DN", "delivery_notes", f"DN={dn_number}")
    await dispatch(session, event_key="dn_pending_logistics", recipient_role="logistics",
                   wa_template="action_required",
                   title=f"DN {dn_number} awaiting logistics approval",
                   body="Review the delivery date / logistics details.", link_page="/logistics",
                   related_table="delivery_notes", related_ref=dn_number, created_by=username)
    return {"submitted": True, "dn_number": dn_number, "status": "pending_logistics"}


async def decide_dn_logistics(session: AsyncSession, *, username: str, dn_number: str,
                              action: str, reason: str = "") -> dict:
    if action not in ("approve", "reject"):
        return {"error": "action must be approve or reject"}
    row = await _dn_row(session, dn_number)
    if row is None:
        return {"error": f"DN {dn_number} not found"}
    if row[4] != "pending_logistics":
        return {"error": f"DN {dn_number} is {row[4]} — not awaiting logistics"}
    if action == "approve":
        await session.execute(update(delivery_notes_t).where(delivery_notes_t.c["DN_Number"] == dn_number)
            .values(status="pending_hod", logistics_decided_at=func.now(),
                    logistics_decided_by=username, logistics_decision="approved"))
        await dispatch(session, event_key="dn_pending_hod", recipient_role="hod",
                       recipient_site=row[0], wa_template="action_required",
                       title=f"DN {dn_number} awaiting HOD approval",
                       body="Logistics approved the delivery — review the DN content.",
                       link_page="/hod/approvals", related_table="delivery_notes",
                       related_ref=dn_number, created_by=username)
        new = "pending_hod"
    else:
        await session.execute(update(delivery_notes_t).where(delivery_notes_t.c["DN_Number"] == dn_number)
            .values(status="rejected", logistics_decided_at=func.now(),
                    logistics_decided_by=username, logistics_decision="rejected",
                    rejection_reason=reason or None))
        # recipient_role is REQUIRED alongside the warehouse narrow — notify()
        # no-ops without a user/role, so a warehouse-only target would send
        # WhatsApp but silently skip the in-app twin (found by the QA sweep).
        await dispatch(session, event_key="dn_rejected", recipient_role="warehouse_user",
                       recipient_warehouse=row[2],
                       severity="warning", wa_template="status_update",
                       title=f"DN {dn_number} rejected by logistics",
                       body=f"Reason: {reason or 'not given'}", link_page="/warehouse",
                       related_table="delivery_notes", related_ref=dn_number, created_by=username)
        new = "rejected"
    await write_audit(session, username, f"DN_LOGISTICS_{action.upper()}", "delivery_notes", f"DN={dn_number}")
    return {"decided": new, "dn_number": dn_number}


async def decide_dn_hod(session: AsyncSession, *, username: str, dn_number: str,
                        action: str, reason: str = "") -> dict:
    if action not in ("approve", "reject"):
        return {"error": "action must be approve or reject"}
    row = await _dn_row(session, dn_number)
    if row is None:
        return {"error": f"DN {dn_number} not found"}
    if row[4] != "pending_hod":
        return {"error": f"DN {dn_number} is {row[4]} — not awaiting HOD"}
    if action == "approve":
        await session.execute(update(delivery_notes_t).where(delivery_notes_t.c["DN_Number"] == dn_number)
            .values(status="hod_approved", hod_decided_at=func.now(), hod_decided_by=username))
        await dispatch(session, event_key="dn_hod_approved", recipient_role="warehouse_user",
                       recipient_warehouse=row[2],
                       severity="success", wa_template="status_update",
                       title=f"DN {dn_number} approved — ready to ship",
                       body="HOD approved the DN content. Ship it from the Warehouse portal.",
                       link_page="/warehouse", related_table="delivery_notes",
                       related_ref=dn_number, created_by=username)
        new = "hod_approved"
    else:
        await session.execute(update(delivery_notes_t).where(delivery_notes_t.c["DN_Number"] == dn_number)
            .values(status="rejected", hod_decided_at=func.now(), hod_decided_by=username,
                    rejection_reason=reason or None))
        await dispatch(session, event_key="dn_rejected", recipient_role="warehouse_user",
                       recipient_warehouse=row[2],
                       severity="warning", wa_template="status_update",
                       title=f"DN {dn_number} rejected by HOD",
                       body=f"Reason: {reason or 'not given'}", link_page="/warehouse",
                       related_table="delivery_notes", related_ref=dn_number, created_by=username)
        new = "rejected"
    await write_audit(session, username, f"DN_HOD_{action.upper()}", "delivery_notes", f"DN={dn_number}")
    return {"decided": new, "dn_number": dn_number}


async def ship_dn(session: AsyncSession, *, username: str, dn_number: str) -> dict:
    # Gate: a DN may only ship once it has cleared BOTH approval stages.
    res = await session.execute(update(delivery_notes_t).where(
        (delivery_notes_t.c["DN_Number"] == dn_number)
        & (delivery_notes_t.c["status"] == "hod_approved")
    ).values(status="in_transit"))
    if res.rowcount == 0:
        return {"error": "DN not found or not HOD-approved yet (submit → logistics → HOD first)"}
    await write_audit(session, username, "SHIP_DN", "delivery_notes", f"DN={dn_number}")
    dest = (await session.execute(select(
        delivery_notes_t.c["Site_ID"], delivery_notes_t.c["PO_Number"]
    ).where(delivery_notes_t.c["DN_Number"] == dn_number))).first()
    if dest is not None:
        await dispatch(session, event_key="dn_shipped", recipient_role="store_keeper",
                       recipient_site=dest[0], wa_template="status_update",
                       title=f"Delivery {dn_number} incoming",
                       body=f"DN for PO {dest[1] or '—'} is in transit — receive it under Incoming Deliveries.",
                       link_page="/site/incoming", related_table="delivery_notes",
                       related_ref=dn_number, created_by=username)
    return {"shipped": True, "dn_number": dn_number, "status": "in_transit"}


# --- site side: incoming DNs → stage receipts (closes the loop) --------------
async def incoming_dns(session: AsyncSession, site_id: str | None):
    """In-transit DNs headed to a site (what the site SK is about to receive)."""
    conds = ['status = \'in_transit\'']
    params: dict = {}
    if site_id:
        conds.append('"Site_ID" = :site')
        params["site"] = site_id
    sql = text(f'''
        SELECT "DN_Number", "PO_Number", "Warehouse_ID", "Site_ID", rl_bl_family,
               "DN_Date", "Vehicle_No", "Driver_Name", status
        FROM delivery_notes WHERE {" AND ".join(conds)}
        ORDER BY "DN_Number" DESC''')
    return _rows(await session.execute(sql, params))


async def stage_dn_receipt(session: AsyncSession, *, username: str, dn_number: str,
                           actor_site: str | None) -> dict:
    """Site receives an in-transit DN → stage one pending_receipts row per line
    (status=pending_hod) at the destination site, so it flows into the HOD
    Approvals → Receipts queue (approve → commit_receipt → ledger). Maps
    Material_Code → SAP_Code via inventory (ports sk_mark_dn_received's mapping),
    then flips the DN to 'received'."""
    dn = (await session.execute(select(
        delivery_notes_t.c["PO_Number"], delivery_notes_t.c["Site_ID"],
        delivery_notes_t.c["Warehouse_ID"], delivery_notes_t.c["status"],
    ).where(delivery_notes_t.c["DN_Number"] == dn_number))).first()
    if dn is None:
        return {"error": "DN not found"}
    po_no, site_id, wh_id, status = dn
    if status != "in_transit":
        return {"error": f"DN status is {status} — only in_transit DNs can be received"}
    # Site scoping: a site user can only receive DNs for their own site (admin
    # any). `is not None`, not truthiness — '' is a site-less scoped account,
    # which must match no DN rather than skipping the check.
    if actor_site is not None and site_id != actor_site:
        return {"error": f"DN is for site {site_id}, not your site ({actor_site})"}

    items = await dn_lines(session, dn_number)
    if not items:
        return {"error": "DN has no items"}

    staged = 0
    inspections: list[int] = []
    for it in items:
        qty = float(it.get("Qty") or 0)
        if qty <= 0:
            continue
        mat = it.get("Material_Code")
        sap_row = (await session.execute(select(inventory_t.c["SAP_Code"])
                   .where(inventory_t.c["Material_Code"] == mat).limit(1))).first()
        sap = sap_row[0] if sap_row else mat
        pid = (await session.execute(insert(pending_receipts_t).values(
            Date=_dt.date.today().isoformat(), SAP_Code=sap, Quantity=qty, Site_ID=site_id,
            Supplier="WAREHOUSE", DN_No=dn_number, DN_Number=dn_number, Warehouse_ID=wh_id,
            PO_Number_Source=po_no, Lot_Number=it.get("Lot_Number"),
            Expiry_Date=it.get("Expiry_Date"), Remarks=f"Received via DN {dn_number}",
            status="pending_hod").returning(pending_receipts_t.c["id"]))).scalar_one()
        await session.execute(update(dn_items_t).where(dn_items_t.c["id"] == it["id"])
                              .values(status="received", sk_received_qty=qty))
        # QSEP — controlled material has now physically reached the site, so
        # the SITE's QC gets an inspection. The source_ref is the DN line id
        # rather than the pending receipt, because the DN line is what a
        # retry would replay; the unique constraint then makes it idempotent.
        #
        # The certificate comes WITH the goods (2026-08-12): whatever
        # Logistics attached to the PO line or the warehouse attached to this
        # DN is what the site's QC should be reading, so it is resolved down
        # the chain and stamped on the inspection rather than left for the
        # store keeper to re-upload a second copy of.
        paper = await quality.visible_mtc(
            session, sap_code=str(sap), material_code=mat, site_id=site_id)
        iid = await quality.open_inspection(
            session, sap_code=str(sap), material_code=mat,
            lot=it.get("Lot_Number"), qty=qty, source_type="dn_receipt",
            source_ref=f"{dn_number}:{it['id']}", site_id=site_id,
            mtc_document_id=(int(paper["id"]) if paper else None),
            created_by=username)
        if iid:
            inspections.append(iid)
        _ = pid
        staged += 1

    await session.execute(update(delivery_notes_t).where(delivery_notes_t.c["DN_Number"] == dn_number)
                          .values(status="received", sk_received_at=func.now(), sk_received_by=username))
    await write_audit(session, username, "DN_RECEIVE_STAGED", "pending_receipts",
                      f"DN={dn_number} PO={po_no} site={site_id} lines={staged}"
                      + (f" qc_inspections={len(inspections)}" if inspections else ""))
    # QSEP notification gap #3. This hop wrote an audit row and nothing else:
    # N receipts landed in the HOD's approval queue and the HOD was never
    # told, while the warehouse never learned its DN had arrived.
    await dispatch(session, event_key="dn_receipt_staged", recipient_role="hod",
                   recipient_site=site_id, wa_template="action_required",
                   title=f"DN {dn_number} received — {staged} receipt(s) to approve",
                   body=(f"{username} received DN {dn_number} from {wh_id}."
                         + (f" {len(inspections)} line(s) await quality inspection "
                            "before they can be issued." if inspections else "")),
                   link_page="/hod/approvals", related_table="delivery_notes",
                   related_ref=dn_number, created_by=username)
    await dispatch(session, event_key="dn_receipt_staged", severity="success",
                   recipient_role="warehouse_user", recipient_warehouse=wh_id,
                   wa_template="status_update",
                   title=f"DN {dn_number} delivered to {site_id}",
                   body=f"{username} confirmed receipt of {staged} line(s).",
                   link_page="/warehouse", related_table="delivery_notes",
                   related_ref=dn_number, created_by=username)
    return {"received": True, "dn_number": dn_number, "staged": staged, "site_id": site_id,
            "qc_inspections": inspections,
            "message": f"Staged {staged} receipt(s) from DN {dn_number} for HOD approval"}
