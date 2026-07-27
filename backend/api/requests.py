"""
backend/api/requests.py — supervisor material requests (SMR).

  POST /requests                 — supervisor creates a request (→ pending_sk)
  GET  /requests                 — list (own for supervisor; site-pending for SK; all for admin)
  GET  /requests/{id}/items      — request lines
  POST /requests/{id}/approve    — SK approves → mirrors to pending_issues (→ HOD Approvals)
  POST /requests/{id}/reject     — SK rejects

Supervisor create is restricted to supervisor/admin; approve/reject to store_keeper/admin.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Body, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.exc import DataError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from .auth import (get_current_user, require_roles, resolve_site_param,
                   site_row_visible, site_scope)
from .db import get_session
from .services import supervisor as smr

router = APIRouter(prefix="/requests", tags=["material requests"])

_SUPERVISOR = require_roles("supervisor")
_SK = require_roles("store_keeper")


async def _guard_smr_site(session: AsyncSession, request_id: int, user: dict,
                          *, code: int = 403) -> None:
    """Refuse when a site-scoped caller touches another site's request.

    The bulk list is scoped, but every by-id route reached the row through the
    integer path parameter alone (audit A02-F3/A02-F4), so a store keeper at one
    site could read — and approve or reject — any other site's request simply by
    incrementing the id. Shaped like hod._guard_pending_site: unrestricted
    callers pass, a missing row passes through so the service raises its own
    not-found, and a site-less scoped caller matches nothing.

    Reads pass code=404 so a direct fetch doesn't confirm the id exists;
    mutations use 403, where the boundary should be visible to the actor.
    """
    scope = site_scope(user)
    if scope is None:
        return
    row_site = await smr.smr_site(session, request_id)
    if row_site is None:
        return
    if scope == "" or not site_row_visible(scope, row_site):
        raise HTTPException(code, "this request belongs to another site")


class SMRItemIn(BaseModel):
    SAP_Code: str
    Requested_Qty: float = Field(..., gt=0)
    Notes: Optional[str] = None


class CreateSMRIn(BaseModel):
    site_id: Optional[str] = None
    worker_id: str
    job_tank_place: str
    old_ppe_returned: bool = True
    no_return_reason: Optional[str] = None
    items: list[SMRItemIn]


class RejectIn(BaseModel):
    reason: Optional[str] = None


def _guard(res: dict) -> dict:
    if res.get("error"):
        raise HTTPException(409, res["error"])
    return res


@router.post("", status_code=201, summary="Create a material request")
async def create(body: CreateSMRIn = Body(...), user: dict = Depends(_SUPERVISOR),
                 session: AsyncSession = Depends(get_session)):
    site = resolve_site_param(user, body.site_id)
    if not site:
        raise HTTPException(422, "site_id is required")
    try:
        async with session.begin():
            res = await smr.create_smr(
                session, supervisor=user["username"], site_id=site, worker_id=body.worker_id,
                job_tank_place=body.job_tank_place, old_ppe_returned=int(body.old_ppe_returned),
                no_return_reason=body.no_return_reason,
                items=[i.model_dump() for i in body.items])
        return _guard(res)
    except HTTPException:
        raise
    except (IntegrityError, DataError) as e:
        raise HTTPException(400, f"{type(e).__name__}: {e.orig}")


@router.get("", summary="List material requests")
async def listing(mine: bool = False, site_id: Optional[str] = None, status: Optional[str] = None,
                  user: dict = Depends(get_current_user),
                  session: AsyncSession = Depends(get_session)):
    # Sensible defaults per role: supervisor → own; store_keeper → site pending.
    if mine or user["role"] == "supervisor":
        return {"items": await smr.list_smr(session, requested_by=user["username"], status=status)}
    scope = resolve_site_param(user, site_id)
    if scope == "":
        return {"items": []}
    if user["role"] == "store_keeper" and status is None:
        status = "pending_sk"
    return {"items": await smr.list_smr(session, site_id=scope, status=status)}


# --- Phase 6 supervisor parity -----------------------------------------------
@router.get("/intent-vs-actual", summary="Approved requests vs actual consumption + variance")
async def intent_vs_actual(days: int = 90, user: dict = Depends(_SUPERVISOR),
                           session: AsyncSession = Depends(get_session)):
    from .reports import rep_intent_vs_actual  # deferred (reports imports stock)
    site = site_scope(user)  # supervisor pinned to own site; admin unrestricted
    title, columns, rows = await rep_intent_vs_actual(session, site_id=site, days=days)
    return {"title": title, "columns": columns,
            "rows": [dict(zip(columns, r)) for r in rows]}


@router.get("/stock/{sap_code}", summary="Live stock at the supervisor's site (cart feedback)")
async def stock_check(sap_code: str, user: dict = Depends(_SUPERVISOR),
                      session: AsyncSession = Depends(get_session)):
    site = user["site_id"]
    if not site:
        raise HTTPException(422, "no site assigned to your account")
    qty = await smr._stock_snapshot(session, site, sap_code.strip())
    return {"sap_code": sap_code.strip(), "site_id": site, "current_stock": qty}


@router.post("/{request_id}/cancel", summary="Supervisor cancels own pending request")
async def cancel(request_id: int, user: dict = Depends(_SUPERVISOR),
                 session: AsyncSession = Depends(get_session)):
    async with session.begin():
        # cancel_smr already enforces the stricter "must be YOUR request" rule;
        # the site guard is belt-and-braces so every by-id route on this router
        # carries the same check.
        await _guard_smr_site(session, request_id, user)
        res = await smr.cancel_smr(session, supervisor=user["username"], request_id=request_id)
    return _guard(res)


@router.get("/{request_id}/items", summary="Request line items")
async def items(request_id: int, user: dict = Depends(get_current_user),
                session: AsyncSession = Depends(get_session)):
    await _guard_smr_site(session, request_id, user, code=404)
    return {"items": await smr.smr_items(session, request_id)}


class ApproveIn(BaseModel):
    # {item_id: qty} — SK's per-line adjustment; qty 0 withdraws the line.
    adjustments: Optional[dict[str, float]] = None


@router.post("/{request_id}/approve", summary="SK approves → stages issues for HOD")
async def approve(request_id: int, body: ApproveIn = Body(default=ApproveIn()),
                  user: dict = Depends(_SK),
                  session: AsyncSession = Depends(get_session)):
    if body.adjustments and any(v < 0 for v in body.adjustments.values()):
        raise HTTPException(422, "adjusted quantities must be ≥ 0 (0 withdraws the line)")
    try:
        async with session.begin():
            await _guard_smr_site(session, request_id, user)
            res = await smr.approve_smr(session, sk_username=user["username"],
                                        request_id=request_id,
                                        qty_overrides=body.adjustments)
        return _guard(res)
    except HTTPException:
        raise
    except (IntegrityError, DataError) as e:
        raise HTTPException(400, f"{type(e).__name__}: {e.orig}")


@router.post("/{request_id}/reject", summary="SK rejects a request")
async def reject(request_id: int, body: RejectIn = Body(default=RejectIn()),
                 user: dict = Depends(_SK), session: AsyncSession = Depends(get_session)):
    async with session.begin():
        await _guard_smr_site(session, request_id, user)
        res = await smr.reject_smr(session, sk_username=user["username"],
                                   request_id=request_id, reason=body.reason or "")
    return _guard(res)
