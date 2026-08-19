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


# ─── Phase 6: reporting + exports ────────────────────────────────────────────
# ⚠️ RULE 12. Every export routes through `reports.to_csv` / `reports.to_xlsx`,
# which apply `_defuse` (csv) and `xl_val` (xlsx). These reports carry
# `Material_Variance_Reason`, `Manpower_Variance_Reason` and
# `HOD_Edit_Justification` — FREE TEXT typed by a supervisor and opened in
# Excel by an HOD, which is exactly the shape the rule exists for. Never hand
# rows to `csv.writer` or openpyxl directly here.
_EXPORT_MEDIA = {
    "csv": ("text/csv", "csv"),
    "xlsx": ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
             "xlsx"),
}


def _export(fmt: str, title: str, columns: list[str], rows: list[list],
            username: str):
    from fastapi.responses import StreamingResponse

    from .reports import to_csv, to_xlsx
    fmt = (fmt or "xlsx").lower()
    if fmt not in _EXPORT_MEDIA:
        raise HTTPException(422, f"format must be one of {sorted(_EXPORT_MEDIA)}")
    media, ext = _EXPORT_MEDIA[fmt]
    data = (to_csv(title, columns, rows, username) if fmt == "csv"
            else to_xlsx(title, columns, rows, username))
    import io as _io
    fname = f"{title.lower().replace(' ', '_')}.{ext}"
    return StreamingResponse(_io.BytesIO(data), media_type=media,
                             headers={"Content-Disposition":
                                      f'attachment; filename="{fname}"'})


_VARIANCE_COLUMNS = [
    "Entry_No", "Work_Date", "Equipment_Tag_No", "Lining_System_Code",
    "Execution_Sub_Activity_Code", "Variant_Key", "status", "Actual_SQM",
    "Material_Actual", "Material_Benchmark", "Material_Variance",
    "Material_Variance_Pct", "Manpower_Actual_Manhours",
    "Manpower_Benchmark_Manhours", "Manpower_Variance_Manhours",
    "Manpower_Variance_Pct", "Actual_Headcount", "Benchmark_Crew_Size",
    "Material_Variance_Reason", "Manpower_Variance_Reason",
    "supervisor_username", "hod_username", "hod_edited",
    "HOD_Edit_Justification",
]

_REASON_COLUMNS = [
    "Entry_No", "Work_Date", "Equipment_Tag_No",
    "Execution_Sub_Activity_Code", "status", "supervisor_username",
    "Material_Variance_Reason", "Manpower_Variance_Reason", "hod_username",
    "hod_edited", "Changed", "HOD_Edit_Justification", "Reject_Reason",
]

_PREP_COLUMNS = [
    "Equipment_Tag_No", "Execution_Sub_Activity_Code", "Variant_Key",
    "Activity", "Done_SQM", "Equipment_Area_SQM", "Coverage_Pct",
    "Entry_Count", "Last_Entry_No",
]


@router.get("/report/variance", summary="Actual vs benchmark, per entry")
async def report_variance(date_from: Optional[str] = None,
                          date_to: Optional[str] = None,
                          status: Optional[str] = None,
                          site_id: Optional[str] = None,
                          format: Optional[str] = Query(default=None),
                          user: dict = Depends(require_roles("hod", "supervisor",
                                                             "auditor")),
                          session: AsyncSession = Depends(get_session)):
    statuses = [s.strip() for s in (status or "").split(",") if s.strip()]
    data = await X.variance_report(
        session, site_id=resolve_site_param(user, site_id),
        date_from=date_from, date_to=date_to, statuses=statuses or None)
    if not format:
        return data
    rows = [[r.get(c) for c in _VARIANCE_COLUMNS] for r in data["items"]]
    return _export(format, "Execution Variance", _VARIANCE_COLUMNS, rows,
                   user["username"])


@router.get("/report/reasons", summary="Stated reasons and HOD corrections")
async def report_reasons(site_id: Optional[str] = None,
                         format: Optional[str] = Query(default=None),
                         user: dict = Depends(require_roles("hod", "auditor")),
                         session: AsyncSession = Depends(get_session)):
    items = await X.reason_log(session,
                               site_id=resolve_site_param(user, site_id))
    if not format:
        return {"items": items}
    rows = [[r.get(c) for c in _REASON_COLUMNS] for r in items]
    return _export(format, "Variance Reason Log", _REASON_COLUMNS, rows,
                   user["username"])


@router.get("/report/surface-prep", summary="Surface-prep area, kept apart "
                                            "from lining progress")
async def report_surface_prep(site_id: Optional[str] = None,
                              format: Optional[str] = Query(default=None),
                              user: dict = Depends(get_current_user),
                              session: AsyncSession = Depends(get_session)):
    data = await X.surface_prep_report(
        session, site_id=resolve_site_param(user, site_id))
    if not format:
        return data
    rows = [[r.get(c) for c in _PREP_COLUMNS] for r in data["items"]]
    return _export(format, "Surface Prep Progress", _PREP_COLUMNS, rows,
                   user["username"])
