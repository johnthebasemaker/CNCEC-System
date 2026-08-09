"""
backend/api/qc.py — the Quality Control role: accounts, transfers, inspections.

Part of QSEP (Quality · Safety · Employees · Procurement, 2026-08). Not
called "Phase 6" — that name already means the 2026-07-10 UAT work in
entry.py, notifications.py and warehouse.py.

Three surfaces, one router:

**Accounts.** A QC is created by the people who need one — an HOD for their
site, a warehouse user for their warehouse, logistics for any warehouse —
rather than by an admin who has never met them. `POST /admin/users` stays at
level 4 and is untouched; this route can create ONLY `role="qc"`, and only
inside the creator's own scope, so lowering the bar here cannot be turned
into a way to mint a second admin. Self-service still works too:
`/auth/register` with role=qc lands in `pending_users` for admin approval.

**Transfers.** An HOD raises, an admin decides. Two steps because approving
rewrites `users.Site_ID`, and role/site/warehouse ride INSIDE the 15-minute
access token (audit A03-F9) — so approval is also where the account's
refresh families are revoked. A one-step transfer would leave the inspector
holding their old site's authority for up to a quarter of an hour.

**Inspections.** The read + decide half of the `qc_inspections` ledger. Rows
are OPENED by triggers inside the warehouse-receive and DN-receive
transactions (services/quality.py), never by a human — an inspection nobody
created is an inspection nobody does.

Scoping is the interesting part. A QC belongs to a site OR to a warehouse,
so neither site_scope() nor warehouse_scope() answers the question alone;
`auth.qc_scope()` returns both axes and every read below funnels through
`_scope_filter`. It fails closed on '' exactly like the rest of the codebase
— a half-configured account sees nothing, never everything.
"""
from __future__ import annotations

from typing import Optional

import bcrypt
from fastapi import APIRouter, Body, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, insert, select, update
from sqlalchemy.exc import DataError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from .auth import (ROLE_META, get_current_user, normalize_phone, qc_scope,
                   require_level, require_roles, revoke_all_sessions)
from .db import get_session
from .services.ledger import _MD, write_audit
from .services.notifications import dispatch

router = APIRouter(prefix="/qc", tags=["quality control"])

users_t = _MD.tables["users"]
inspections_t = _MD.tables["qc_inspections"]
transfers_t = _MD.tables["qc_transfer_requests"]
sysset_t = _MD.tables["system_settings"]
warehouses_t = _MD.tables["warehouses"]


# ── helpers ───────────────────────────────────────────────────────────────────
def _scope_filter(user: dict, stmt):
    """Narrow a qc_inspections query to what this caller may read.

    `None` on an axis means unrestricted; a string (including '') means the
    column must equal it, and '' deliberately matches no row. Emitting the
    predicate rather than skipping it is the whole point — `if scope:` is how
    a site-less account ends up reading every site (audit Theme A).
    """
    s = qc_scope(user)
    site, wh = s["site"], s["warehouse"]
    if site is None and wh is None:
        return stmt                                   # admin / logistics / auditor
    if site is not None and wh is not None:
        # A QC: exactly one axis is meaningful, the other is ''. Match on the
        # one that is set, so a warehouse QC is not also asked to have a site.
        if site:
            return stmt.where(func.coalesce(inspections_t.c["Site_ID"], "") == site)
        if wh:
            return stmt.where(func.coalesce(inspections_t.c["Warehouse_ID"], "") == wh)
        return stmt.where(False)                      # neither → nothing
    if site is not None:                              # hod / supervisor / SK
        return stmt.where(func.coalesce(inspections_t.c["Site_ID"], "") == site)
    return stmt.where(func.coalesce(inspections_t.c["Warehouse_ID"], "") == wh)


async def _row_visible(user: dict, row) -> bool:
    """Row-level twin of _scope_filter, for a direct fetch by id. A direct
    fetch that skips the list's WHERE is the by-id IDOR class suite AU pins."""
    s = qc_scope(user)
    site, wh = s["site"], s["warehouse"]
    if site is None and wh is None:
        return True
    if site:
        return (row["Site_ID"] or "") == site
    if wh:
        return (row["Warehouse_ID"] or "") == wh
    return False


async def _admin_site_names(session: AsyncSession) -> list[str]:
    rows = (await session.execute(
        select(sysset_t.c["value"]).where(sysset_t.c["category"] == "Site")
        .order_by(sysset_t.c["id"]))).all()
    return [r[0] for r in rows]


async def _warehouse_exists(session: AsyncSession, wh: str) -> bool:
    return (await session.execute(select(func.count()).select_from(warehouses_t)
            .where(warehouses_t.c["Warehouse_ID"] == wh))).scalar_one() > 0


async def _qc_row(session: AsyncSession, username: str):
    row = (await session.execute(select(
        users_t.c["username"], users_t.c["role"], users_t.c["Site_ID"],
        users_t.c["Warehouse_ID"]).where(users_t.c["username"] == username))).first()
    return row


# ── accounts ──────────────────────────────────────────────────────────────────
class CreateQcIn(BaseModel):
    username: str
    password: str
    site_id: Optional[str] = None
    warehouse_id: Optional[str] = None
    phone_number: Optional[str] = None


@router.post("/accounts", status_code=201,
             summary="Create a QC account inside your own scope (HOD / Warehouse / Logistics)")
async def create_qc_account(
    body: CreateQcIn = Body(...),
    actor: dict = Depends(require_roles("hod", "warehouse_user", "logistics")),
    session: AsyncSession = Depends(get_session),
):
    """Create a `qc` user and nothing else.

    The role is hard-coded, not taken from the body: this endpoint exists to
    let a level-1 warehouse user create an account, and a role parameter
    would turn that into a privilege-escalation primitive on the first typo.

    Scope is taken from the ACTOR, never from the request, for the same
    reason. An HOD gets their own site; a warehouse user their own
    warehouse; logistics and admin may name any warehouse (they are the
    oversight roles that already read across all of them).
    """
    # One password policy for every credential path — see admin.MIN_PW.
    from .admin import assert_password_ok

    uname = (body.username or "").strip()
    if not uname:
        raise HTTPException(422, "username is required")
    assert_password_ok(body.password)

    role = actor["role"]
    site = wh = None
    if role == "hod":
        site = (actor.get("site_id") or "").strip()
        if not site:
            raise HTTPException(
                403, "your account is not bound to a site — ask an admin to assign one")
        if body.warehouse_id:
            raise HTTPException(422, "an HOD creates SITE quality inspectors; "
                                     "ask logistics for a warehouse one")
    elif role == "warehouse_user":
        wh = (actor.get("warehouse_id") or "").strip()
        if not wh:
            raise HTTPException(
                403, "your account is not bound to a warehouse — ask an admin to assign one")
        if body.site_id:
            raise HTTPException(422, "a warehouse user creates WAREHOUSE quality "
                                     "inspectors; ask the site HOD for a site one")
    else:                                     # logistics, or admin via require_roles
        site = (body.site_id or "").strip() or None
        wh = (body.warehouse_id or "").strip() or None
        if bool(site) == bool(wh):
            raise HTTPException(
                422, "name EXACTLY ONE of site_id or warehouse_id — a quality "
                     "inspector belongs to a site or to a warehouse, not both")
        if site and site not in await _admin_site_names(session):
            raise HTTPException(422, f"unknown site {site!r} — pick an admin-created site")
        if wh and not await _warehouse_exists(session, wh):
            raise HTTPException(422, f"unknown warehouse {wh!r}")

    pw_hash = bcrypt.hashpw(body.password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
    try:
        async with session.begin():
            if (await _qc_row(session, uname)) is not None:
                raise HTTPException(409, f"user {uname!r} already exists")
            await session.execute(insert(users_t).values(
                username=uname, password_hash=pw_hash, role="qc",
                Site_ID=site, Warehouse_ID=wh,
                Phone_Number=(normalize_phone(body.phone_number)
                              if body.phone_number else None)))
            await write_audit(session, actor["username"], "CREATE_QC_ACCOUNT", "users",
                              f"username={uname} site={site or '-'} warehouse={wh or '-'} "
                              f"by_role={role}")
    except HTTPException:
        raise
    except IntegrityError:
        raise HTTPException(409, f"user {uname!r} already exists")
    except DataError as e:
        raise HTTPException(400, f"DataError: {e.orig}")
    return {"created": True, "username": uname, "role": "qc",
            "site_id": site, "warehouse_id": wh}


@router.get("/accounts", summary="QC accounts visible to you")
async def list_qc_accounts(user: dict = Depends(require_roles(
        "hod", "warehouse_user", "logistics", "qc")),
        session: AsyncSession = Depends(get_session)):
    stmt = select(users_t.c["username"], users_t.c["role"], users_t.c["Site_ID"],
                  users_t.c["Warehouse_ID"], users_t.c["Phone_Number"],
                  users_t.c["created_at"]).where(users_t.c["role"] == "qc")
    s = qc_scope(user)
    if user["role"] == "hod":
        stmt = stmt.where(func.coalesce(users_t.c["Site_ID"], "")
                          == (user.get("site_id") or "").strip())
    elif user["role"] == "warehouse_user":
        stmt = stmt.where(func.coalesce(users_t.c["Warehouse_ID"], "")
                          == (user.get("warehouse_id") or "").strip())
    elif user["role"] == "qc":
        # A QC sees its own colleagues, on whichever axis binds it.
        if s["site"]:
            stmt = stmt.where(func.coalesce(users_t.c["Site_ID"], "") == s["site"])
        elif s["warehouse"]:
            stmt = stmt.where(func.coalesce(users_t.c["Warehouse_ID"], "") == s["warehouse"])
        else:
            return {"items": []}
    rows = (await session.execute(stmt.order_by(users_t.c["username"]))).mappings().all()
    return {"items": [dict(m) for m in rows]}


# ── transfers ─────────────────────────────────────────────────────────────────
class TransferIn(BaseModel):
    to_site: str
    reason: str = Field(..., min_length=3)


@router.post("/accounts/{username}/transfer", status_code=201,
             summary="Request a QC transfer to another site (HOD raises → Admin decides)")
async def request_qc_transfer(username: str, body: TransferIn = Body(...),
                              actor: dict = Depends(require_roles("hod")),
                              session: AsyncSession = Depends(get_session)):
    to_site = (body.to_site or "").strip()
    if not to_site:
        raise HTTPException(422, "to_site is required")
    async with session.begin():
        row = await _qc_row(session, username)
        if row is None or row.role != "qc":
            raise HTTPException(404, f"no QC account named {username!r}")
        from_site = (row.Site_ID or "").strip()
        if not from_site:
            raise HTTPException(
                409, f"{username} is bound to warehouse {row.Warehouse_ID or '—'}, "
                     "not to a site — a warehouse inspector is re-bound by an admin, "
                     "not transferred between sites")
        # An HOD may only move their OWN site's inspector. Without this any
        # HOD could reassign another site's staff.
        if from_site != (actor.get("site_id") or "").strip():
            raise HTTPException(403, f"{username} belongs to site {from_site} — "
                                     "you may only transfer your own site's inspectors")
        if to_site == from_site:
            raise HTTPException(422, f"{username} is already at {to_site}")
        if to_site not in await _admin_site_names(session):
            raise HTTPException(422, f"unknown site {to_site!r} — pick an admin-created site")
        open_req = (await session.execute(select(func.count()).select_from(transfers_t)
                    .where(transfers_t.c["username"] == username,
                           transfers_t.c["status"] == "pending_admin"))).scalar_one()
        if open_req:
            raise HTTPException(409, f"a transfer for {username} is already awaiting admin")
        tid = (await session.execute(insert(transfers_t).values(
            username=username, from_site=from_site, to_site=to_site,
            reason=body.reason.strip(), requested_by=actor["username"],
        ).returning(transfers_t.c["id"]))).scalar_one()
        await write_audit(session, actor["username"], "QC_TRANSFER_REQUEST",
                          "qc_transfer_requests",
                          f"id={tid} {username} {from_site}→{to_site}")
        await dispatch(session, event_key="qc_transfer_pending_admin",
                       recipient_role="admin", wa_template="action_required",
                       title=f"QC transfer awaiting approval — {username}",
                       body=f"{actor['username']} asks to move {username} from "
                            f"{from_site} to {to_site}. Reason: {body.reason.strip()}",
                       link_page="/qc/accounts", related_table="qc_transfer_requests",
                       related_ref=tid, created_by=actor["username"])
    return {"requested": True, "id": tid, "username": username,
            "from_site": from_site, "to_site": to_site, "status": "pending_admin"}


@router.get("/transfers", summary="QC transfer requests")
async def list_qc_transfers(status: Optional[str] = None,
                            user: dict = Depends(require_roles("hod", "logistics")),
                            session: AsyncSession = Depends(get_session)):
    stmt = select(transfers_t)
    if status:
        stmt = stmt.where(transfers_t.c["status"] == status)
    if user["role"] == "hod":
        site = (user.get("site_id") or "").strip()
        stmt = stmt.where((transfers_t.c["from_site"] == site)
                          | (transfers_t.c["to_site"] == site))
    rows = (await session.execute(stmt.order_by(transfers_t.c["id"].desc()))).mappings().all()
    return {"items": [dict(m) for m in rows]}


class TransferDecideIn(BaseModel):
    action: str = Field(..., pattern="^(approve|reject)$")
    notes: Optional[str] = None


@router.post("/transfers/{tid}/decide", summary="Admin approves or rejects a QC transfer")
async def decide_qc_transfer(tid: int, body: TransferDecideIn = Body(...),
                             actor: dict = Depends(require_level(4)),
                             session: AsyncSession = Depends(get_session)):
    async with session.begin():
        row = (await session.execute(select(transfers_t)
               .where(transfers_t.c["id"] == tid))).mappings().first()
        if row is None:
            raise HTTPException(404, f"transfer {tid} not found")
        if row["status"] != "pending_admin":
            raise HTTPException(409, f"transfer {tid} is already {row['status']}")
        uname, to_site = row["username"], row["to_site"]
        if body.action == "reject":
            await session.execute(update(transfers_t).where(transfers_t.c["id"] == tid)
                                  .values(status="rejected", decided_by=actor["username"],
                                          decided_at=func.now(),
                                          decision_notes=(body.notes or "").strip() or None))
            await write_audit(session, actor["username"], "QC_TRANSFER_REJECT",
                              "qc_transfer_requests", f"id={tid} {uname}")
            await dispatch(session, event_key="qc_transfer_decided",
                           recipient_user=row["requested_by"], wa_template="status_update",
                           title=f"QC transfer rejected — {uname}",
                           body=f"{actor['username']} rejected the move to {to_site}."
                                + (f" {body.notes.strip()}" if body.notes else ""),
                           link_page="/qc/accounts", related_table="qc_transfer_requests",
                           related_ref=tid, created_by=actor["username"])
            return {"decided": True, "id": tid, "status": "rejected"}

        # Approve: move the account, then KILL ITS SESSIONS. site_id is baked
        # into the access token and read straight back out with no database
        # lookup, so without this the inspector keeps reading their old site
        # for up to 15 minutes (audit A03-F9, the same fix admin.update_user
        # already carries).
        cur = await _qc_row(session, uname)
        if cur is None or cur.role != "qc":
            raise HTTPException(409, f"{uname} is no longer a QC account")
        await session.execute(update(users_t).where(users_t.c["username"] == uname)
                              .values(Site_ID=to_site))
        revoked = await revoke_all_sessions(session, uname, "qc-transfer")
        await session.execute(update(transfers_t).where(transfers_t.c["id"] == tid)
                              .values(status="approved", decided_by=actor["username"],
                                      decided_at=func.now(),
                                      decision_notes=(body.notes or "").strip() or None))
        await write_audit(session, actor["username"], "QC_TRANSFER_APPROVE",
                          "qc_transfer_requests",
                          f"id={tid} {uname} {row['from_site']}→{to_site} "
                          f"sessions_revoked={revoked}")
        for who in {row["requested_by"], uname}:
            await dispatch(session, event_key="qc_transfer_decided", severity="success",
                           recipient_user=who, wa_template="status_update",
                           title=f"QC transfer approved — {uname}",
                           body=f"{uname} now works at {to_site}. Sign in again to "
                                "pick up the new site.",
                           link_page="/qc/inspections", related_table="qc_transfer_requests",
                           related_ref=tid, created_by=actor["username"])
    return {"decided": True, "id": tid, "status": "approved",
            "username": uname, "to_site": to_site, "sessions_revoked": revoked}


# ── inspections ───────────────────────────────────────────────────────────────
@router.get("/inspections", summary="Quality inspections in your scope")
async def list_inspections(status: Optional[str] = Query(None),
                           sap_code: Optional[str] = Query(None),
                           limit: int = Query(200, ge=1, le=1000),
                           user: dict = Depends(require_roles(
                               "qc", "hod", "logistics", "warehouse_user",
                               "store_keeper", "auditor")),
                           session: AsyncSession = Depends(get_session)):
    stmt = select(inspections_t)
    if status:
        stmt = stmt.where(inspections_t.c["status"] == status)
    if sap_code:
        stmt = stmt.where(func.trim(inspections_t.c["SAP_Code"]) == sap_code.strip())
    stmt = _scope_filter(user, stmt)
    rows = (await session.execute(
        stmt.order_by(inspections_t.c["id"].desc()).limit(limit))).mappings().all()
    return {"items": [dict(m) for m in rows]}


@router.get("/inspections/{iid}", summary="One inspection")
async def get_inspection(iid: int, user: dict = Depends(require_roles(
        "qc", "hod", "logistics", "warehouse_user", "store_keeper", "auditor")),
        session: AsyncSession = Depends(get_session)):
    row = (await session.execute(select(inspections_t)
           .where(inspections_t.c["id"] == iid))).mappings().first()
    # 404 rather than 403 on a foreign row: a direct fetch should not confirm
    # that an inspection exists at a site the caller cannot see.
    if row is None or not await _row_visible(user, row):
        raise HTTPException(404, "no such inspection")
    return dict(row)


class DecideIn(BaseModel):
    approved_qty: float = Field(..., ge=0)
    reason: Optional[str] = None


@router.post("/inspections/{iid}/decide",
             summary="Approve / partially approve / reject an inspection (QC only)")
async def decide_inspection(iid: int, body: DecideIn = Body(...),
                            user: dict = Depends(require_roles("qc")),
                            session: AsyncSession = Depends(get_session)):
    """One decision endpoint, not three.

    Full approval, partial approval and rejection differ only in
    `approved_qty`, and modelling them as separate routes invites the state
    where two of the three forget to demand a reason. Here the rule is stated
    once: anything rejected needs a reason.
    """
    from .services import quality

    async with session.begin():
        row = (await session.execute(select(inspections_t)
               .where(inspections_t.c["id"] == iid).with_for_update())).mappings().first()
        if row is None or not await _row_visible(user, row):
            raise HTTPException(404, "no such inspection")
        if row["status"] != "pending":
            raise HTTPException(409, f"inspection {iid} is already {row['status']}")
        submitted = float(row["submitted_qty"] or 0)
        approved = float(body.approved_qty)
        if approved > submitted + 1e-9:
            raise HTTPException(
                422, f"cannot approve {approved:g} of {submitted:g} submitted — "
                     "a quality decision cannot create stock")
        rejected = round(submitted - approved, 6)
        reason = (body.reason or "").strip()
        if rejected > 1e-9 and not reason:
            raise HTTPException(
                422, "give a reason — you are rejecting "
                     f"{rejected:g} of {submitted:g}")
        status = quality.decision_status(submitted, approved)
        await session.execute(update(inspections_t).where(inspections_t.c["id"] == iid)
                              .values(approved_qty=approved, rejected_qty=rejected,
                                      status=status, decision_reason=reason or None,
                                      inspected_by=user["username"],
                                      inspected_at=func.now()))
        await write_audit(session, user["username"], "QC_DECIDE", "qc_inspections",
                          f"id={iid} {row['SAP_Code']} lot={row['Lot_Number'] or '-'} "
                          f"{approved:g}/{submitted:g} → {status}")
        place = row["Site_ID"] or row["Warehouse_ID"] or "-"
        # The Store Keeper is the person the decision actually constrains —
        # they are the one who will be refused at the issue form otherwise.
        await dispatch(
            session, event_key="qc_decision",
            severity="success" if status == "approved" else "warning",
            recipient_role="store_keeper", recipient_site=row["Site_ID"],
            wa_template="status_update",
            title=f"QC {status.replace('_', ' ')} — {row['SAP_Code']}",
            body=(f"Lot {row['Lot_Number'] or '—'} at {place}: {approved:g} of "
                  f"{submitted:g} approved for issue."
                  + (f" Reason: {reason}" if reason else "")),
            link_page="/qc/inspections", related_table="qc_inspections",
            related_ref=iid, created_by=user["username"])
    return {"decided": True, "id": iid, "status": status,
            "approved_qty": approved, "rejected_qty": rejected}


@router.get("/clearance", summary="How much of a material is cleared for issue")
async def clearance(sap_code: str = Query(...), site_id: Optional[str] = None,
                    lot: Optional[str] = None,
                    user: dict = Depends(get_current_user),
                    session: AsyncSession = Depends(get_session)):
    """What the issue form needs before it lets a Store Keeper submit.

    Returns the same numbers the hard block uses, so the UI can grey the
    button out with a truthful reason instead of letting the user find out on
    submit. The API remains the boundary — this is a courtesy.
    """
    from .auth import resolve_site_param
    from .services import quality

    site = resolve_site_param(user, site_id)
    if site == "":
        raise HTTPException(403, "your account is not bound to a site")
    return await quality.clearance_summary(session, sap_code=sap_code,
                                           site_id=site or "", lot=lot)
