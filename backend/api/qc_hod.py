"""
backend/api/qc_hod.py — the Head of Qualities' portal.

⚠️ EVERY ROUTE IS `require_roles("qc_hod")`, NEVER `require_level`. That is the
whole reason the role sits at level 2 rather than 3: cross-site reads come from
the named exemption in `auth.QC_OVERSIGHT_ROLES`, so the level ladder grants it
nothing, and its surface is ENUMERABLE — this file, and nothing else. Level 3
would have handed it every endpoint gated by `require_level(0..3)`, which is
ninety-seven of them.

⚠️ EVERY READ IS FILTERED TO THE CONTROLLED CATEGORY in
`services/qc_oversight.py`, in SQL, per function. A cross-site account with no
category filter is a window onto PPE, tools, consumables and every price on
every purchase order.

⚠️ ONE WRITE, AND IT IS A MESSAGE. `POST /qc-hod/escalations` asks somebody who
can act to act; `POST /qc-hod/escalations/{id}/resolve` closes one; `PUT
/qc-hod/settings` tunes this role's own thresholds. Those three paths are the
entire allowlist in readonly.py — the middleware refuses every other mutating
verb from this role, including any future one added here by accident.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from .auth import require_roles
from .db import get_session
from .health_monitor import missing_mtc_rows
from .services import qc_oversight as qcx

router = APIRouter(prefix="/qc-hod", tags=["qc-hod"])

# admin reaches every workspace for support; qc_hod is the role itself.
_GATE = require_roles("qc_hod")


@router.get("/overview", summary="Quality oversight KPIs (cross-site)")
async def overview(user: dict = Depends(_GATE),
                   session: AsyncSession = Depends(get_session)):
    return await qcx.overview(session)


@router.get("/surface-shield/pos",
            summary="Surface Shield PO lines, with MTC presence per line")
async def ss_pos(site_id: Optional[str] = None, user: dict = Depends(_GATE),
                 session: AsyncSession = Depends(get_session)):
    """`site_id` NARROWS a cross-site view; it does not unlock one. There is no
    `resolve_site_param` here because the role has no site of its own to
    resolve against — an unfiltered read is the correct default for oversight."""
    return {"items": await qcx.surface_shield_pos(session, site_id)}


@router.get("/mtc", summary="Certificate register for controlled material")
async def mtc(site_id: Optional[str] = None, limit: int = Query(500, le=2000),
              user: dict = Depends(_GATE),
              session: AsyncSession = Depends(get_session)):
    return {"items": await qcx.mtc_register(session, site_id, limit)}


@router.get("/missing-mtc",
            summary="Controlled material on hand with no certificate")
async def missing(user: dict = Depends(_GATE),
                  session: AsyncSession = Depends(get_session)):
    """The same rows the daily sweep reports, on demand.

    Deliberately the SAME function `dispatch_missing_mtc` uses. A dashboard
    that reimplemented "has a certificate" would eventually disagree with the
    alert, and then nobody would trust either."""
    return {"items": await missing_mtc_rows(session, None)}


@router.get("/usage", summary="Which sites are consuming controlled material")
async def usage(user: dict = Depends(_GATE),
                session: AsyncSession = Depends(get_session)):
    return {"items": await qcx.usage_by_site(session)}


@router.get("/stagnation",
            summary="Controlled lots sitting still, or running out of time")
async def stagnation(user: dict = Depends(_GATE),
                     session: AsyncSession = Depends(get_session)):
    return await qcx.stagnation(session)


@router.get("/escalations", summary="The comms log")
async def escalations(status: Optional[str] = None,
                      user: dict = Depends(_GATE),
                      session: AsyncSession = Depends(get_session)):
    return {"items": await qcx.list_escalations(session, status)}


class EscalationIn(BaseModel):
    target_role: str = Field(min_length=1)
    kind: str = Field(min_length=1)
    message: str = Field(min_length=1, max_length=4000)
    # ⚠️ EXACTLY ONE (operator ruling Q12). Neither, or both, is a 422 from the
    # service — a message aimed at everywhere is one nobody owns.
    target_site: Optional[str] = None
    target_warehouse: Optional[str] = None
    sap_code: Optional[str] = None
    material_code: Optional[str] = None
    lot_number: Optional[str] = None
    po_number: Optional[str] = None


@router.post("/escalations", status_code=201,
             summary="Ask a site QC, a warehouse or Logistics to act")
async def raise_escalation(body: EscalationIn = Body(...),
                           user: dict = Depends(_GATE),
                           session: AsyncSession = Depends(get_session)):
    async with session.begin():
        res = await qcx.raise_escalation(
            session, username=user["username"], target_role=body.target_role,
            target_site=body.target_site, target_warehouse=body.target_warehouse,
            kind=body.kind, message=body.message, sap_code=body.sap_code,
            material_code=body.material_code, lot_number=body.lot_number,
            po_number=body.po_number)
        if res.get("error"):
            raise HTTPException(422, res["error"])
    return res


class ResolveIn(BaseModel):
    note: str = Field(min_length=1, max_length=2000)


@router.post("/escalations/{esc_id}/resolve", summary="Close an escalation")
async def resolve(esc_id: int, body: ResolveIn = Body(...),
                  user: dict = Depends(_GATE),
                  session: AsyncSession = Depends(get_session)):
    async with session.begin():
        res = await qcx.resolve_escalation(session, username=user["username"],
                                           esc_id=esc_id, note=body.note)
        if res.get("error"):
            raise HTTPException(409, res["error"])
    return res


@router.get("/settings", summary="Stagnation and expiry thresholds")
async def get_settings(user: dict = Depends(_GATE),
                       session: AsyncSession = Depends(get_session)):
    return await qcx.thresholds(session)


class SettingsIn(BaseModel):
    stagnant_days: int = Field(ge=1, le=3650)
    expiry_warn_days: int = Field(ge=1, le=3650)


@router.put("/settings", summary="Retune the thresholds")
async def put_settings(body: SettingsIn = Body(...),
                       user: dict = Depends(_GATE),
                       session: AsyncSession = Depends(get_session)):
    """A policy, not a constant — the same reasoning as the overtime
    thresholds. 90 days is the operator's number and changing it must not be a
    release."""
    async with session.begin():
        return await qcx.set_thresholds(
            session, username=user["username"],
            stagnant_days=body.stagnant_days,
            expiry_warn_days=body.expiry_warn_days)
