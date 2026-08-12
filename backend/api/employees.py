"""
backend/api/employees.py — site transfers, movement history, admin tracking.

Part of QSEP (Quality · Safety · Employees · Procurement, 2026-08).

RULING R1 (accepted 2026-08-09) IS THE WHOLE DESIGN

`employees.ID_Number` is the PERSON — globally unique, one row per human
being, regardless of how many sites they have worked at. `mh_employees` is
the per-site EMPLOYMENT RECORD, keyed on (Site_ID, Employee_Code), so a
transfer necessarily creates a second row there.

Everything downstream follows from that. A transfer:

  * moves `employees.Site_ID` — one row, one update;
  * writes an `employee_movements` row so the history is a series, not a
    single current value;
  * UPSERTS an `mh_employees` row at the destination and **leaves the source
    row in place**, because `mh_timesheets` is keyed on
    (Site_ID, Employee_Code, Work_Date) and moving the roster row would
    orphan every hour the person has already worked;
  * moves NO PPE, because `ppe_distributions.employee_id_number` was never
    site-keyed in the first place. The history arrives at the new site for
    free — which is the requirement, and the reason R1 was needed to
    implement it at all.

The destination Store Keeper is notified with a summary of what the worker
already holds. That notification IS the duplicate-issue prevention the
requirement asks for; the hard part (making the history visible) is the
keying, and the soft part is telling somebody.

Transfers are HOD-initiated and IMMEDIATE (ruling R4). Only the QC *user*
transfer needs an admin's second signature — that one rewrites an
authentication row and has to revoke sessions (see qc.py). An employee is
not an account.
"""
from __future__ import annotations

import datetime as _dt
from typing import Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, insert, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import DataError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from .auth import (get_current_user, require_level, require_roles,
                   resolve_site_param, site_scope)
from .db import get_session
from .services import ppe as ppe_svc
from .services.ledger import _MD, write_audit
from .services.notifications import dispatch

router = APIRouter(prefix="/hr", tags=["employees"])

employees_t = _MD.tables["employees"]
movements_t = _MD.tables["employee_movements"]
mh_employees_t = _MD.tables["mh_employees"]
timesheets_t = _MD.tables["mh_timesheets"]
sysset_t = _MD.tables["system_settings"]


async def _admin_site_names(session: AsyncSession) -> list[str]:
    rows = (await session.execute(
        select(sysset_t.c["value"]).where(sysset_t.c["category"] == "Site")
        .order_by(sysset_t.c["id"]))).all()
    return [r[0] for r in rows]


# ── transfers ────────────────────────────────────────────────────────────────
class TransferIn(BaseModel):
    to_site: str
    reason: Optional[str] = None
    effective_date: Optional[str] = Field(None, description="ISO date; defaults to today")


@router.post("/employees/{id_number}/transfer", status_code=201,
             summary="Move an employee to another site (HOD, immediate)")
async def transfer_employee(id_number: str, body: TransferIn = Body(...),
                            user: dict = Depends(require_roles("hod")),
                            session: AsyncSession = Depends(get_session)):
    idn = id_number.strip()
    to_site = (body.to_site or "").strip()
    if not to_site:
        raise HTTPException(422, "to_site is required")
    try:
        async with session.begin():
            emp = (await session.execute(select(
                employees_t.c["ID_Number"], employees_t.c["Name"],
                employees_t.c["Site_ID"], employees_t.c["status"],
                employees_t.c["Designation"], employees_t.c["Worker_Type"],
                employees_t.c["Company"])
                .where(employees_t.c["ID_Number"] == idn))).mappings().first()
            if emp is None:
                raise HTTPException(404, f"no employee with ID {idn!r}")
            from_site = (emp["Site_ID"] or "").strip()
            # An HOD moves their OWN site's people. Without this any HOD
            # could reassign another site's workforce.
            scope = site_scope(user)
            if scope is not None and from_site != scope:
                raise HTTPException(
                    403, f"{idn} is at site {from_site or '—'} — you may only "
                         "transfer your own site's employees")
            if to_site == from_site:
                raise HTTPException(422, f"{idn} is already at {to_site}")
            if to_site not in await _admin_site_names(session):
                raise HTTPException(
                    422, f"unknown site {to_site!r} — pick an admin-created site")

            eff = (body.effective_date or _dt.date.today().isoformat())[:10]
            await session.execute(update(employees_t)
                                  .where(employees_t.c["ID_Number"] == idn)
                                  .values(Site_ID=to_site,
                                          updated_at=_dt.datetime.now(
                                              _dt.timezone.utc).replace(tzinfo=None)))
            mid = (await session.execute(insert(movements_t).values(
                employee_id_number=idn, from_site=(from_site or None),
                to_site=to_site, effective_date=eff,
                reason=(body.reason or "").strip() or None,
                moved_by=user["username"], status="applied",
            ).returning(movements_t.c["id"]))).scalar_one()

            # A roster row at the destination, so timesheets can be entered
            # there from day one. The SOURCE row is deliberately left alone:
            # mh_timesheets is keyed on (Site_ID, Employee_Code, Work_Date),
            # so moving it would orphan every hour already worked.
            stmt = pg_insert(mh_employees_t).values(
                Site_ID=to_site, Employee_Code=idn, Name=emp["Name"],
                Designation=emp["Designation"], Company=emp["Company"],
                Worker_Type=(emp["Worker_Type"] or "OWN"),
                linked_id_number=idn, status="active", created_by=user["username"])
            await session.execute(stmt.on_conflict_do_update(
                index_elements=["Site_ID", "Employee_Code"],
                set_={"Name": stmt.excluded.Name, "status": "active",
                      "linked_id_number": idn,
                      "updated_at": _dt.datetime.now(_dt.timezone.utc).replace(tzinfo=None)}))

            held = [d for d in await ppe_svc.history_for(session, id_number=idn)
                    if d["status"] == ppe_svc.ACTIVE]
            await write_audit(session, user["username"], "EMPLOYEE_TRANSFER",
                              "employees",
                              f"{idn} {from_site or '—'}→{to_site} eff={eff} "
                              f"ppe_active={len(held)} movement={mid}")

            # THE notification of this whole slice. The history already
            # travels (it is keyed on the person); this is what makes the
            # destination Store Keeper aware of it, so they do not hand out
            # a second pair of boots on the worker's first morning.
            if held:
                summary = "; ".join(
                    f"{h['SAP_Code']} until {h['expires_on'] or 'n/a'}" for h in held[:6])
                extra = f" (+{len(held) - 6} more)" if len(held) > 6 else ""
                body_txt = (f"{emp['Name']} ({idn}) transfers from "
                            f"{from_site or '—'} on {eff} already holding "
                            f"{len(held)} item(s) of PPE: {summary}{extra}. "
                            "Do not reissue these.")
            else:
                body_txt = (f"{emp['Name']} ({idn}) transfers from "
                            f"{from_site or '—'} on {eff} with no PPE on record.")
            await dispatch(
                session, event_key="employee_transferred",
                recipient_role="store_keeper", recipient_site=to_site,
                wa_template="status_update",
                title=f"Employee arriving — {emp['Name']}",
                body=body_txt, link_page=f"/hr/employees/{idn}",
                related_table="employee_movements", related_ref=mid,
                created_by=user["username"])
            if from_site:
                await dispatch(
                    session, event_key="employee_transferred",
                    recipient_role="hod", recipient_site=to_site,
                    wa_template="status_update",
                    title=f"Employee arriving — {emp['Name']}",
                    body=f"Transferred in from {from_site} by {user['username']}.",
                    link_page=f"/hr/employees/{idn}",
                    related_table="employee_movements", related_ref=mid,
                    created_by=user["username"])
    except HTTPException:
        raise
    except (IntegrityError, DataError) as e:
        raise HTTPException(400, f"{type(e).__name__}: {e.orig}")
    return {"transferred": True, "id_number": idn, "from_site": from_site or None,
            "to_site": to_site, "effective_date": eff, "movement_id": mid,
            "ppe_carried_over": len(held)}


# ⚠️ THE ROSTER IS PII. Names, phone numbers, departments, employer.
#
# This was `get_current_user` — ANY signed-in account could list every worker
# they shared a site with, including roles with no business in the staff list
# at all. The nav manifest hid the page from the store keeper and showed it to
# warehouse users, QC inspectors and Logistics; the API showed it to everyone
# regardless, so the manifest was never the control it looked like.
#
# Four roles, each for a stated reason (operator ruling, 2026-08-12):
#   store_keeper  issues PPE against an employee ID and must be able to find one
#   supervisor    raises material requests naming workers
#   hod           owns the site's people and is the only role that may transfer
#   auditor       reads everything, writes nothing
# Warehouse, QC and Logistics are deliberately absent: none of them manages,
# moves or equips a person.
#
# Site scoping below is unchanged and still does the real narrowing — a scoped
# caller sees their own site, and '' (no site of their own) matches nothing.
_ROSTER_ROLES = require_roles("store_keeper", "supervisor", "hod", "auditor")


@router.get("/employees", summary="Employees you may see")
async def list_employees(site_id: Optional[str] = None, q: Optional[str] = None,
                         user: dict = Depends(_ROSTER_ROLES),
                         session: AsyncSession = Depends(get_session)):
    site = resolve_site_param(user, site_id)
    if site == "":
        return {"items": []}
    c = employees_t.c
    stmt = select(c["ID_Number"], c["Name"], c["Site_ID"], c["Department"],
                  c["Designation"], c["Worker_Type"], c["Company"],
                  c["Phone_Number"], c["status"])
    if site:
        stmt = stmt.where(func.coalesce(c["Site_ID"], "") == site)
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(c["ID_Number"].ilike(like) | c["Name"].ilike(like))
    rows = (await session.execute(stmt.order_by(c["Name"]).limit(2000))).mappings().all()
    return {"items": [dict(r) for r in rows]}


@router.get("/employees/{id_number}/timeline",
            summary="Where this person has worked, and what they hold (admin tracking)")
async def timeline(id_number: str,
                   user: dict = Depends(_ROSTER_ROLES),
                   session: AsyncSession = Depends(get_session)):
    """Current site + every recorded move + PPE + man-hour presence.

    ⚠️ Gated on `_ROSTER_ROLES`, NOT on a level, and the change is in both
    directions. It was `require_level(2)`, which admitted Logistics — now
    excluded with the rest of the roster — and excluded the store keeper, who
    opens this panel from the roster page to see what PPE somebody already
    holds before issuing more. An HOD chasing a worker who "used to be at
    CNCEC" still gets the same answer an admin does, and the row itself is
    site-scoped below for anyone under logistics.

    The movement series is SYNTHESISED with an opening segment when the
    person predates the movements table — otherwise a worker hired before
    2026-08-10 renders as a chart with no beginning, which reads as missing
    data rather than as "they have always been here".
    """
    idn = id_number.strip()
    emp = (await session.execute(select(
        employees_t.c["ID_Number"], employees_t.c["Name"], employees_t.c["Site_ID"],
        employees_t.c["Department"], employees_t.c["Designation"],
        employees_t.c["Worker_Type"], employees_t.c["Company"],
        employees_t.c["status"], employees_t.c["created_at"])
        .where(employees_t.c["ID_Number"] == idn))).mappings().first()
    scope = site_scope(user)
    if emp is None or (scope is not None and (emp["Site_ID"] or "") != scope):
        raise HTTPException(404, "no such employee")

    m = movements_t.c
    moves = [dict(r) for r in (await session.execute(
        select(m["id"], m["from_site"], m["to_site"], m["effective_date"],
               m["reason"], m["moved_by"], m["status"], m["created_at"])
        .where(m["employee_id_number"] == idn, m["status"] == "applied")
        .order_by(m["effective_date"], m["id"]))).mappings().all()]

    # Turn the moves into closed segments the chart can draw directly, rather
    # than making four callers each re-derive "from when to when".
    segments: list[dict] = []
    first_site = (moves[0]["from_site"] if moves else emp["Site_ID"]) or None
    opened = (str(emp["created_at"])[:10] if emp["created_at"]
              else (moves[0]["effective_date"] if moves else None))
    if first_site:
        segments.append({"site": first_site, "from": opened,
                         "to": (moves[0]["effective_date"] if moves else None),
                         "origin": "opening"})
    for i, mv in enumerate(moves):
        nxt = moves[i + 1]["effective_date"] if i + 1 < len(moves) else None
        segments.append({"site": mv["to_site"], "from": mv["effective_date"],
                         "to": nxt, "reason": mv["reason"],
                         "moved_by": mv["moved_by"], "origin": "transfer"})

    ppe_items = await ppe_svc.history_for(session, id_number=idn)
    worked = [dict(r) for r in (await session.execute(text('''
        SELECT "Site_ID" AS site,
               MIN("Work_Date") AS first_day,
               MAX("Work_Date") AS last_day,
               COUNT(*)         AS days,
               COALESCE(SUM("Total_Hours"), 0) AS hours
          FROM mh_timesheets
         WHERE "Employee_Code" = :code
         GROUP BY "Site_ID" ORDER BY MIN("Work_Date")
    '''), {"code": idn})).mappings().all()]

    return {"employee": dict(emp), "movements": moves, "segments": segments,
            "ppe": ppe_items,
            "ppe_active": [p for p in ppe_items if p["status"] == ppe_svc.ACTIVE],
            "worked_at": worked}


@router.get("/movements", summary="Recent site transfers")
async def movements(site_id: Optional[str] = None,
                    limit: int = Query(200, ge=1, le=1000),
                    user: dict = Depends(require_level(2)),
                    session: AsyncSession = Depends(get_session)):
    site = resolve_site_param(user, site_id)
    m = movements_t.c
    stmt = select(m["id"], m["employee_id_number"], m["from_site"], m["to_site"],
                  m["effective_date"], m["reason"], m["moved_by"], m["created_at"])
    if site:
        stmt = stmt.where((m["from_site"] == site) | (m["to_site"] == site))
    elif site == "":
        return {"items": []}
    rows = (await session.execute(
        stmt.order_by(m["id"].desc()).limit(limit))).mappings().all()
    names = dict((await session.execute(select(
        employees_t.c["ID_Number"], employees_t.c["Name"]))).all())
    return {"items": [{**dict(r), "Name": names.get(r["employee_id_number"])}
                      for r in rows]}


@router.get("/data-quality",
            summary="Employee records that cannot be used, and why")
async def data_quality(user: dict = Depends(require_level(2)),
                       session: AsyncSession = Depends(get_session)):
    """Report the broken rows; never guess a fix.

    A site-less employee is invisible to EVERY supervisor material request —
    `create_smr` tests `(w[2] or "") != site_id`, which no site satisfies —
    and nothing anywhere says so. One such row existed (30816) and was
    backfilled to CNCEC on the operator's instruction in alembic
    d2f84b19e57c. Any future one is surfaced here rather than assigned by a
    heuristic, the same discipline as the Consumption-Log row that carries a
    Location and no serial.

    Also reports roster rows with no `employees` counterpart — the state
    that made the attendance import and the SMR worker list disagree before
    this programme wired `linked_id_number` up.
    """
    siteless = [dict(r) for r in (await session.execute(text('''
        SELECT "ID_Number", "Name", "Department", status
          FROM employees WHERE COALESCE("Site_ID",'') = '' ORDER BY "ID_Number"
    '''))).mappings().all()]
    unlinked = [dict(r) for r in (await session.execute(text('''
        SELECT m."Site_ID", m."Employee_Code", m."Name"
          FROM mh_employees m
         WHERE NOT EXISTS (SELECT 1 FROM employees e
                            WHERE e."ID_Number" = m."Employee_Code")
         ORDER BY m."Site_ID", m."Employee_Code"
    '''))).mappings().all()]
    return {
        "siteless_employees": siteless,
        "roster_without_master": unlinked,
        "note": ("A site-less employee cannot be named on ANY supervisor "
                 "material request. Assign a site on the Employees page — "
                 "nothing here is guessed for you."),
    }
