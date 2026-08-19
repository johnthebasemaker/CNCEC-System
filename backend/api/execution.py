"""
backend/api/execution.py — Phase 5 consumption workflow endpoints.

Routing mirrors who holds the knowledge, not who is senior:

  POST /execution/entries            store keeper OR supervisor (see below)
  POST /execution/entries/{id}/submit          store keeper → supervisor
  POST /execution/entries/{id}/supervisor      supervisor   → HOD
  POST /execution/entries/{id}/decision        HOD approve / reject
  GET  /execution/entries            the queue, filtered by status
  GET  /execution/entries/{id}       one entry with its variance
  GET  /execution/activities         what a supervisor may open directly

⚠️ The supervisor route accepts NO material fields. That is the control, not an
omission: a supervisor is measured against the consumption a store keeper
counted, and letting them edit it would let a bad number be tidied by the
person it reflects on. Only the HOD can change both sides, and only with a
justification the supervisor is notified of.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .auth import get_current_user, require_roles, resolve_site_param, site_scope
from .db import get_session
from .services import execution as X
from .services.ledger import _MD

router = APIRouter(prefix="/execution", tags=["execution"])

norm_t = _MD.tables["sme_manpower_norm"]
recipe_t = _MD.tables["sme_recipe"]


def _write_site(user: dict, requested: Optional[str]) -> str:
    """The site a write lands on. Below logistics a user is pinned to their own;
    above it, one must be named — a global write with no site is how a row ends
    up belonging to nobody."""
    own = site_scope(user)
    if own:
        return own
    sid = (requested or "").strip()
    if not sid:
        raise HTTPException(422, "site_id is required for a global role")
    return sid


class MaterialIn(BaseModel):
    Material_Code: str = Field(min_length=1)
    SAP_Code: Optional[str] = ""
    Actual_Qty: float = Field(ge=0)
    UOM: Optional[str] = None
    Lot_No: Optional[str] = None


class OpenIn(BaseModel):
    work_date: str
    equipment_tag: str
    # '' or omitted = system-agnostic (surface prep belongs to no lining
    # system). Sent as the empty string, never null — see models.SmeExecutionEntry.
    lining_system_code: str = ""
    execution_sub_activity_code: str
    variant_key: str = ""
    materials: list[MaterialIn] = []
    site_id: Optional[str] = None


class ManpowerIn(BaseModel):
    Role_Code: str = Field(min_length=1)
    Headcount: float = Field(ge=0)
    Hours: float = Field(ge=0)


class SupervisorIn(BaseModel):
    actual_sqm: float = Field(gt=0)
    manpower: list[ManpowerIn]
    material_variance_reason: str = Field(min_length=1)
    manpower_variance_reason: str = Field(min_length=1)
    execution_sub_activity_code: Optional[str] = None
    variant_key: Optional[str] = None
    site_id: Optional[str] = None


class MaterialEdit(BaseModel):
    id: int
    Actual_Qty: float = Field(ge=0)


class ManpowerEdit(BaseModel):
    id: int
    Headcount: Optional[float] = Field(default=None, ge=0)
    Hours: Optional[float] = Field(default=None, ge=0)


class DecisionIn(BaseModel):
    approve: bool
    reject_reason: str = ""
    justification: str = ""
    actual_sqm: Optional[float] = Field(default=None, gt=0)
    materials: list[MaterialEdit] = []
    manpower: list[ManpowerEdit] = []
    site_id: Optional[str] = None


@router.get("/activities", summary="Sub-activities, and who may open each")
async def activities(site_id: Optional[str] = None,
                     user: dict = Depends(get_current_user),
                     session: AsyncSession = Depends(get_session)):
    """The picker behind both entry forms.

    `manpower_only` is what the UI keys on: those activities consume no Surface
    Shield, so a supervisor opens them directly AND the lining-system dropdown
    is hidden — surface prep belongs to no system, and forcing a choice would
    trap the hours under whichever one was guessed.
    """
    material_keys = {(str(a), str(b)) for a, b in (await session.execute(
        select(recipe_t.c["Lining_System_Code"],
               recipe_t.c["Execution_Sub_Activity_Code"]).distinct())).all()}
    rows = (await session.execute(
        select(norm_t.c["Lining_System_Code"],
               norm_t.c["Execution_Sub_Activity_Code"], norm_t.c["Activity"],
               norm_t.c["Sub_Activity"], norm_t.c["Variant_Key"],
               norm_t.c["Type"], norm_t.c["Crew_Size"],
               norm_t.c["Standard_Productivity_Per_Shift"])
        .order_by(norm_t.c["Type"], norm_t.c["Lining_System_Code"],
                  norm_t.c["Execution_Sub_Activity_Code"]))).mappings().all()
    out = []
    for r in rows:
        code, esc = str(r["Lining_System_Code"]), str(r["Execution_Sub_Activity_Code"])
        only = (code, esc) not in material_keys
        out.append({
            **{k: r[k] for k in r.keys()},
            "manpower_only": only,
            # A norm whose system column holds an ESC code describes work that
            # belongs to no lining system; entries for it store ''.
            "system_agnostic": only and not code.startswith("LSC"),
        })
    return {"items": out}


@router.post("/entries", status_code=201, summary="Open an execution entry")
async def open_entry(body: OpenIn = Body(...),
                     user: dict = Depends(require_roles("store_keeper",
                                                        "supervisor", "hod")),
                     session: AsyncSession = Depends(get_session)):
    sid = _write_site(user, body.site_id)
    res = await X.open_entry(
        session, username=user["username"], role=user["role"], site_id=sid,
        work_date=body.work_date, equipment_tag=body.equipment_tag,
        code=body.lining_system_code, esc=body.execution_sub_activity_code,
        variant=body.variant_key,
        materials=[m.model_dump() for m in body.materials])
    await session.commit()
    return res


@router.post("/entries/{entry_id}/submit", summary="SK → supervisor")
async def sk_submit(entry_id: int, site_id: Optional[str] = None,
                    user: dict = Depends(require_roles("store_keeper", "hod")),
                    session: AsyncSession = Depends(get_session)):
    res = await X.sk_submit(session, username=user["username"],
                            entry_id=entry_id,
                            site_id=resolve_site_param(user, site_id))
    await session.commit()
    return res


@router.post("/entries/{entry_id}/supervisor", summary="Supervisor → HOD")
async def supervisor_submit(entry_id: int, body: SupervisorIn = Body(...),
                            user: dict = Depends(require_roles("supervisor", "hod")),
                            session: AsyncSession = Depends(get_session)):
    res = await X.supervisor_submit(
        session, username=user["username"], entry_id=entry_id,
        site_id=resolve_site_param(user, body.site_id),
        actual_sqm=body.actual_sqm,
        manpower=[m.model_dump() for m in body.manpower],
        material_reason=body.material_variance_reason,
        manpower_reason=body.manpower_variance_reason,
        esc=body.execution_sub_activity_code, variant=body.variant_key)
    await session.commit()
    return res


@router.post("/entries/{entry_id}/decision", summary="HOD approve / reject")
async def hod_decision(entry_id: int, body: DecisionIn = Body(...),
                       user: dict = Depends(require_roles("hod")),
                       session: AsyncSession = Depends(get_session)):
    res = await X.hod_decide(
        session, username=user["username"], entry_id=entry_id,
        site_id=resolve_site_param(user, body.site_id), approve=body.approve,
        reject_reason=body.reject_reason, justification=body.justification,
        edits={"Actual_SQM": body.actual_sqm,
               "materials": [m.model_dump() for m in body.materials],
               "manpower": [m.model_dump() for m in body.manpower]})
    await session.commit()
    return res


@router.get("/entries", summary="The execution queue")
async def list_entries(status: Optional[str] = Query(default=None),
                       site_id: Optional[str] = None,
                       user: dict = Depends(get_current_user),
                       session: AsyncSession = Depends(get_session)):
    statuses = [s.strip() for s in (status or "").split(",") if s.strip()]
    return {"items": await X.list_entries(
        session, site_id=resolve_site_param(user, site_id),
        statuses=statuses or None)}


@router.get("/entries/{entry_id}", summary="One entry, with its variance")
async def get_entry(entry_id: int, site_id: Optional[str] = None,
                    user: dict = Depends(get_current_user),
                    session: AsyncSession = Depends(get_session)):
    return await X.get_entry(session, entry_id,
                             resolve_site_param(user, site_id))
