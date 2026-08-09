"""
backend/api/ppe.py — PPE rules, per-person history, and the order forecast.

Part of QSEP (Quality · Safety · Employees · Procurement, 2026-08).

Note what is NOT here: an endpoint that issues PPE. Option A (operator
ruling, 2026-08-09) is that PPE goes out through the ORDINARY issue form —
`POST /entry/consumption`, which grows two fields when the material is PPE
and writes the distribution alongside the staged issue. A second issue
endpoint would be a second stock path, and the whole point of the ruling is
that there is only one.

So this module is the configuration and the reporting:

  /ppe/eligible          which materials offer the flow, and their rule
  /ppe/rules             the SK sets usable days (per material, per site)
  /ppe/employees/{id}    one person's history, across every site they have
                         worked at — the read that makes a transfer safe
  /ppe/forecast          what to buy in the next 15 days, with names
"""
from __future__ import annotations

import datetime as _dt
from typing import Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, insert, select, text, update
from sqlalchemy.exc import DataError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from .auth import (get_current_user, require_level, require_roles,
                   resolve_site_param, resolve_site_write, site_scope)
from .db import get_session
from .services import ppe as svc
from .services.ledger import _MD, write_audit

router = APIRouter(prefix="/ppe", tags=["ppe"])

rules_t = _MD.tables["ppe_rules"]
dist_t = _MD.tables["ppe_distributions"]
employees_t = _MD.tables["employees"]
inventory_t = _MD.tables["inventory"]


# ── eligible materials ───────────────────────────────────────────────────────
@router.get("/eligible", summary="Materials that offer the PPE flow, with their rule")
async def eligible(site_id: Optional[str] = None,
                   user: dict = Depends(get_current_user),
                   session: AsyncSession = Depends(get_session)):
    """The union of the PPE category and the materials someone wrote a rule for.

    The two signals do different jobs — the category says "offer the flow",
    a rule says "and it wears out after N days" — so the picker needs both,
    and it needs to show which of the two each row has. A PPE-category item
    with no rule is listed with `usable_days: null`; it can still be issued
    and recorded, it just has no expiry and the forecast cannot see it.
    """
    site = resolve_site_param(user, site_id)
    rows = (await session.execute(text('''
        SELECT i."SAP_Code", i."Material_Code", i."Equipment_Description", i."UOM",
               i."Category"
          FROM inventory i
         WHERE UPPER(TRIM(COALESCE(i."Category",''))) = UPPER(:cat)
            OR EXISTS (SELECT 1 FROM ppe_rules r
                        WHERE TRIM(r."SAP_Code") = TRIM(i."SAP_Code"))
         ORDER BY i."SAP_Code"
    '''), {"cat": svc.PPE_CATEGORY})).mappings().all()
    # One query for every rule, not one per material. `rules_for_many` applies
    # the same site-beats-global precedence `rule_for` does — see its docstring
    # for why the two spellings are held to each other by a test rather than by
    # good intentions.
    rules = await svc.rules_for_many(
        session, sap_codes=[str(r["SAP_Code"]) for r in rows], site_id=site)
    out = []
    for r in rows:
        rule = rules.get(str(r["SAP_Code"]).strip())
        out.append({**dict(r),
                    "usable_days": (rule or {}).get("usable_days"),
                    "requires_safety_doc": bool((rule or {}).get("requires_safety_doc", 1)),
                    "rule_scope": ("site" if rule and rule.get("Site_ID")
                                   else "global" if rule else None)})
    return {"items": out, "category": svc.PPE_CATEGORY}


# ── rules ────────────────────────────────────────────────────────────────────
class RuleIn(BaseModel):
    SAP_Code: str
    usable_days: int = Field(..., gt=0, le=3650)
    site_id: Optional[str] = Field(
        None, description="blank = the global default for this material")
    requires_safety_doc: bool = True
    notes: Optional[str] = None


@router.get("/rules", summary="Usable-time rules")
async def list_rules(user: dict = Depends(get_current_user),
                     session: AsyncSession = Depends(get_session)):
    scope = site_scope(user)
    c = rules_t.c
    stmt = select(c["id"], c["SAP_Code"], c["Site_ID"], c["usable_days"],
                  c["requires_safety_doc"], c["notes"], c["created_by"],
                  c["updated_at"])
    if scope is not None:
        # A scoped user sees their own site's rules AND the global defaults —
        # the globals are what actually applies to them when no site row
        # exists, so hiding them would show the SK a rule set that is not the
        # one being enforced.
        stmt = stmt.where((c["Site_ID"] == scope) | (c["Site_ID"].is_(None)))
    rows = (await session.execute(
        stmt.order_by(c["SAP_Code"], c["Site_ID"].is_(None)))).mappings().all()
    names = {r[0]: (r[1], r[2]) for r in (await session.execute(select(
        inventory_t.c["SAP_Code"], inventory_t.c["Material_Code"],
        inventory_t.c["Equipment_Description"]))).all()}
    return {"items": [
        {**dict(r),
         "Material_Code": names.get(str(r["SAP_Code"]).strip(), (None, None))[0],
         "Description": names.get(str(r["SAP_Code"]).strip(), (None, None))[1]}
        for r in rows]}


@router.post("/rules", status_code=201, summary="Set a usable time (SK)")
async def upsert_rule(body: RuleIn = Body(...),
                      user: dict = Depends(require_roles("store_keeper", "hod")),
                      session: AsyncSession = Depends(get_session)):
    """Upsert on (SAP_Code, COALESCE(Site_ID,'')).

    A blank site_id writes the GLOBAL default. That is allowed from any site
    on purpose: usable time is a property of the product ("safety shoes last
    six months"), not of the yard, and forcing every site to restate it is
    how half of them end up with no rule and no expiry.
    """
    sap = body.SAP_Code.strip()
    if not sap:
        raise HTTPException(422, "SAP_Code is required")
    exists = (await session.execute(select(func.count()).select_from(inventory_t)
              .where(func.trim(inventory_t.c["SAP_Code"]) == sap))).scalar_one()
    if not exists:
        raise HTTPException(404, f"SAP_Code {sap!r} is not in the inventory master")
    site = (body.site_id or "").strip() or None
    if site is not None:
        site = resolve_site_write(user, site)
    try:
        async with session.begin():
            c = rules_t.c
            prior = (await session.execute(select(c["id"], c["usable_days"]).where(
                func.trim(c["SAP_Code"]) == sap,
                func.coalesce(c["Site_ID"], "") == (site or "")))).first()
            if prior is None:
                rid = (await session.execute(insert(rules_t).values(
                    SAP_Code=sap, Site_ID=site, usable_days=body.usable_days,
                    requires_safety_doc=1 if body.requires_safety_doc else 0,
                    notes=(body.notes or "").strip() or None,
                    created_by=user["username"],
                ).returning(c["id"]))).scalar_one()
                action, was = "PPE_RULE_ADD", None
            else:
                rid, was = prior[0], prior[1]
                await session.execute(update(rules_t).where(c["id"] == rid).values(
                    usable_days=body.usable_days,
                    requires_safety_doc=1 if body.requires_safety_doc else 0,
                    notes=(body.notes or "").strip() or None,
                    updated_at=_dt.datetime.now(_dt.timezone.utc).replace(tzinfo=None)))
                action = "PPE_RULE_EDIT"
            await write_audit(session, user["username"], action, "ppe_rules",
                              f"id={rid} {sap} site={site or 'GLOBAL'} "
                              f"days={body.usable_days}"
                              + (f" (was {was})" if was is not None else ""))
    except (IntegrityError, DataError) as e:
        raise HTTPException(400, f"{type(e).__name__}: {e.orig}")
    # Stated in the response because it is the one thing people expect and
    # do not get: shortening a rule does not move anyone's existing expiry.
    return {"saved": True, "id": rid, "SAP_Code": sap,
            "site_id": site, "usable_days": body.usable_days,
            "note": "applies to FUTURE issues — gear already handed out keeps "
                    "the usable time it was issued under"}


@router.delete("/rules/{rid}", summary="Remove a usable-time rule")
async def delete_rule(rid: int,
                      user: dict = Depends(require_roles("store_keeper", "hod")),
                      session: AsyncSession = Depends(get_session)):
    async with session.begin():
        row = (await session.execute(select(rules_t.c["SAP_Code"], rules_t.c["Site_ID"])
               .where(rules_t.c["id"] == rid))).first()
        if row is None:
            raise HTTPException(404, f"rule {rid} not found")
        if row[1] is not None:
            resolve_site_write(user, row[1])
        await session.execute(delete(rules_t).where(rules_t.c["id"] == rid))
        await write_audit(session, user["username"], "PPE_RULE_DELETE", "ppe_rules",
                          f"id={rid} {row[0]} site={row[1] or 'GLOBAL'}")
    return {"deleted": True, "id": rid}


# ── history ──────────────────────────────────────────────────────────────────
@router.get("/employees/{id_number}", summary="One person's full PPE history")
async def employee_history(id_number: str,
                           user: dict = Depends(get_current_user),
                           session: AsyncSession = Depends(get_session)):
    """Every issue this PERSON has ever had, at every site.

    Site scoping is applied to the PERSON, not to the rows: a Store Keeper
    may look up somebody at their own site, and then sees that worker's whole
    history including the sites they came from. That is the point — the
    target SK has to know what the worker already holds, or a transfer just
    moves the duplicate-issue problem somewhere else (ruling R1).
    """
    emp = (await session.execute(select(
        employees_t.c["ID_Number"], employees_t.c["Name"], employees_t.c["Site_ID"],
        employees_t.c["Department"], employees_t.c["status"])
        .where(employees_t.c["ID_Number"] == id_number.strip()))).mappings().first()
    scope = site_scope(user)
    if emp is None or (scope is not None and (emp["Site_ID"] or "") != scope):
        # 404 rather than 403: a direct fetch should not confirm that a
        # worker exists at a site the caller cannot see.
        raise HTTPException(404, "no such employee")
    items = await svc.history_for(session, id_number=id_number.strip())
    return {"employee": dict(emp), "items": items,
            "active": [i for i in items if i["status"] == svc.ACTIVE]}


@router.get("/distributions", summary="PPE issued at a site")
async def distributions(site_id: Optional[str] = None, status: Optional[str] = None,
                        limit: int = Query(500, ge=1, le=2000),
                        user: dict = Depends(get_current_user),
                        session: AsyncSession = Depends(get_session)):
    site = resolve_site_param(user, site_id)
    if site == "":
        return {"items": []}
    c = dist_t.c
    stmt = select(c).where(c["status"] != "void")
    if site:
        stmt = stmt.where(c["Site_ID"] == site)
    if status:
        stmt = stmt.where(c["status"] == status)
    rows = (await session.execute(
        stmt.order_by(c["issued_on"].desc(), c["id"].desc()).limit(limit))).mappings().all()
    today = _dt.date.today().isoformat()
    return {"items": [
        {**dict(r),
         "overdue": bool(r["expires_on"] and r["status"] == svc.ACTIVE
                         and str(r["expires_on"]) < today)}
        for r in rows]}


# ── forecast ─────────────────────────────────────────────────────────────────
@router.get("/forecast", summary="PPE expiring in the next N days, netted for ordering")
async def forecast(site_id: Optional[str] = None,
                   days: int = Query(svc.FORECAST_DAYS, ge=1, le=180),
                   user: dict = Depends(require_level(0)),
                   session: AsyncSession = Depends(get_session)):
    """The bulk-order list, with the names attached.

    Deterministic, not statistical (ruling R5): expiring − on hand − already
    on order. The `people` array on each row is not decoration — a list of
    quantities cannot be sanity-checked by a human and a list of names can,
    and it is also how the SK knows whose gear to go and look at.

    "Expired" here is a SUGGESTED REPLACEMENT DATE. Nothing blocks the
    worker from using the gear, and this endpoint refuses nothing; it is a
    shopping list.
    """
    site = resolve_site_param(user, site_id)
    if site == "":
        return {"window_days": days, "items": [], "total_people": 0,
                "total_suggested": 0.0,
                "note": "your account is not bound to a site"}
    return await svc.forecast(session, site_id=site, days=days)


# ── exports ──────────────────────────────────────────────────────────────────
# ⚠️ RULE 12. `early_reason` is free text typed by a STORE KEEPER (level 0)
# and read by an HOD in Excel — the exact privilege path the 2026-08-06 audit
# closed. These go through `reports._FORMATS`, whose writers all route cells
# through `_defuse`, and NOT through a hand-rolled csv.writer. Quantities and
# day-counts are handed over as int/float so `_defuse` leaves them alone: a
# defused number is a number that stops summing.
@router.get("/export/{key}", summary="Export PPE history or the forecast (xlsx | csv | pdf)")
async def export_ppe(key: str, format: str = Query("xlsx"),
                     site_id: Optional[str] = None,
                     id_number: Optional[str] = None,
                     days: int = Query(svc.FORECAST_DAYS, ge=1, le=180),
                     user: dict = Depends(require_level(1)),
                     session: AsyncSession = Depends(get_session)):
    import io

    from fastapi.responses import StreamingResponse

    from .reports import _FORMATS

    fmt = format.lower()
    if fmt not in _FORMATS:
        raise HTTPException(400, f"format must be one of {sorted(_FORMATS)}")

    if key == "history":
        if not id_number:
            raise HTTPException(422, "id_number is required for the history export")
        data = await employee_history(id_number, user, session)
        title = f"PPE History — {data['employee']['Name']} ({id_number})"
        columns = ["Site", "SAP", "Material", "Description", "Qty", "Issued",
                   "Usable days", "Replace by", "Status", "Early", "Reason",
                   "Issued by"]
        rows = [[r["Site_ID"], r["SAP_Code"], r["Material_Code"], r["Description"],
                 float(r["Qty"] or 0), r["issued_on"],
                 int(r["usable_days_applied"]) if r["usable_days_applied"] else None,
                 r["expires_on"], r["status"],
                 "yes" if r["early_replacement"] else "", r["early_reason"],
                 r["issued_by"]] for r in data["items"]]
    elif key == "forecast":
        site = resolve_site_param(user, site_id)
        if site == "":
            raise HTTPException(403, "your account is not bound to a site")
        data = await svc.forecast(session, site_id=site, days=days)
        title = f"PPE Order Forecast — next {days} days"
        columns = ["SAP", "Material", "Description", "Expiring qty", "People",
                   "Overdue", "On hand", "On order", "Suggested order",
                   "Earliest expiry", "Who"]
        rows = [[i["SAP_Code"], i["Material_Code"], i["Description"],
                 float(i["expiring_qty"]), int(i["people_count"]),
                 int(i["overdue_count"]), float(i["on_hand"]), float(i["on_order"]),
                 float(i["suggested_order_qty"]), i["earliest_expiry"],
                 ", ".join(f"{p['employee_name']} ({p['expires_on']})"
                           for p in i["people"])] for i in data["items"]]
    else:
        raise HTTPException(404, f"unknown export {key!r} (history | forecast)")

    render, media = _FORMATS[fmt]
    blob = render(title, columns, rows, user["username"])
    fname = f"ppe-{key}.{fmt}"
    return StreamingResponse(io.BytesIO(blob), media_type=media,
                             headers={"Content-Disposition": f'attachment; filename="{fname}"'})
