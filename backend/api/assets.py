"""
backend/api/assets.py — serialised asset tracking, and where things are.

    GET    /assets                    list / search units
    POST   /assets                    register a unit                 (write)
    GET    /assets/resolve             a scan → the unit, or the choice
    GET    /assets/{id}                one unit + its recent movements
    PATCH  /assets/{id}/location       move it (rack, free text, and/or GPS)
    PATCH  /assets/{id}                edit status / holder / notes
    DELETE /assets/{id}                retire a unit                  (write)
    POST   /assets/{id}/transfer       ask to move it to another site
    GET    /assets/transfers           the transfer queue
    POST   /assets/transfers/{id}/decide   source HOD approves / rejects

THE PROBLEM THIS SOLVES. Two hammers share one SAP code, so scanning either
label resolves to the same inventory row. `asset_units` is one row per physical
thing, keyed **`(SAP_Code, serial_no)` GLOBALLY**, and `/assets/resolve` is what
turns a scan into "this exact hammer" — or, when the sticker only carries the
SAP, into "which of these three?".

⚠️ THE KEY USED TO INCLUDE `Site_ID`, and that was wrong. Site in the key
means hammer #A-1042 can exist at CNCEC *and* somewhere else at the same
time: two rows, two custody chains, two GPS fixes, one physical hammer. A
serial is stamped on the object, not issued per yard. `Site_ID` remains on
the row because it is WHERE THE THING IS — data, not identity — and it moves
only through an APPROVED TRANSFER, never a silent update (alembic
a3c17e9b25d4).

WHY THE SOURCE SITE APPROVES. The site LOSING the asset is the one with
something at stake and the only one that can confirm it physically left. A
receiving site that could pull an asset across on its own say-so would make
"where is it" a question of who edited last.

GPS IS BEST-EFFORT, ALWAYS.
`lat`/`lng` arrive from the browser's `navigator.geolocation` and are optional
on every request. A denied permission, a device without a fix, an indoor
warehouse with no signal — all of these still record the move, with the
coordinates NULL. Location capture must never block a location UPDATE, because
the update is the thing with operational value and the coordinate is the bonus.

⚠️ These coordinates are where an EMPLOYEE was standing. They are visible to
the roles that can already see the asset, the Auditor's write guard keeps them
read-only, and the movement log is append-only so a position cannot be quietly
rewritten. Said out loud here because this is the first genuinely personal data
the system stores.

Roles: reads are open to any authenticated user (a store keeper is level 0 and
is who scans things). Registering, moving and retiring are `require_level(1)`.
Auditors are refused every write by `readonly.py`'s method-keyed middleware,
which is not modified here and to which nothing is added.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, insert, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from .auth import get_current_user, require_level, resolve_site_param, site_scope
from .db import get_session
from .services.ledger import _MD, write_audit
from .services.notifications import dispatch

router = APIRouter(prefix="/assets", tags=["asset tracking"])

unit_t = _MD.tables["asset_units"]
move_t = _MD.tables["asset_movements"]
loc_t = _MD.tables["storage_locations"]
inventory_t = _MD.tables["inventory"]
transfer_t = _MD.tables["asset_transfers"]
sysset_t = _MD.tables["system_settings"]

# Coordinates outside these are not a place on Earth; a bad sensor reading
# should be rejected at the door rather than stored and plotted later.
_LAT = (-90.0, 90.0)
_LNG = (-180.0, 180.0)

# 2026-08-05 — the vocabulary widened on the operator's own words. The original
# five are CUSTODY (where the thing is in the stores workflow); `working`,
# `not_in_use` and `repair` are CONDITION (what state the thing is in), which is
# what someone standing in front of a hammer actually knows.
#
# ONE field, not two, deliberately: the operator asked for a single status and
# in practice records whichever fact they have. Splitting it would put a column
# on the screen that is empty on every row somebody did not think to fill, and
# `asset_movements.status` already gives the history of how it changed.
_STATUS_VALUES = ("in_stock", "issued", "returned", "lost", "scrapped",
                  "working", "not_in_use", "repair")
_STATUS_RE = "^(" + "|".join(_STATUS_VALUES) + ")$"


def _rows(res) -> list[dict]:
    return [dict(r) for r in res.mappings().all()]


def _write_site(user: dict, site_id: Optional[str]) -> str:
    scope = site_scope(user)
    if scope is None:
        site = (site_id or "").strip()
        if not site:
            raise HTTPException(422, "site_id is required")
        return site
    if site_id is not None and site_id.strip() and site_id.strip() != scope:
        raise HTTPException(403, "you may only modify data for your own site")
    if not scope:
        raise HTTPException(403, "your account has no site; ask an admin")
    return scope


class GpsFix(BaseModel):
    """An optional position. Every field may be absent — see the module note on
    why capture must never gate the update."""
    lat: Optional[float] = None
    lng: Optional[float] = None
    accuracy_m: Optional[float] = Field(default=None, ge=0)

    def clean(self) -> dict:
        if self.lat is None or self.lng is None:
            return {}
        if not (_LAT[0] <= self.lat <= _LAT[1] and _LNG[0] <= self.lng <= _LNG[1]):
            raise HTTPException(422, "lat/lng are not a point on Earth")
        return {"lat": self.lat, "lng": self.lng, "accuracy_m": self.accuracy_m}


async def _enrich(session: AsyncSession, units: list[dict]) -> list[dict]:
    """Attach the human-readable rack and the material description."""
    if not units:
        return units
    loc_ids = {u["current_location_id"] for u in units if u["current_location_id"]}
    locs = {}
    if loc_ids:
        locs = {r["id"]: r for r in _rows(await session.execute(
            select(loc_t).where(loc_t.c["id"].in_(loc_ids))))}
    saps = {u["SAP_Code"] for u in units}
    descs = {r["SAP_Code"]: r for r in _rows(await session.execute(
        select(inventory_t.c["SAP_Code"], inventory_t.c["Equipment_Description"],
               inventory_t.c["Material_Code"], inventory_t.c["UOM"])
        .where(inventory_t.c["SAP_Code"].in_(saps))))} if saps else {}
    for u in units:
        loc = locs.get(u["current_location_id"])
        u["location_code"] = loc["code"] if loc else None
        d = descs.get(u["SAP_Code"]) or {}
        u["Equipment_Description"] = d.get("Equipment_Description")
        u["Material_Code"] = d.get("Material_Code")
        u["UOM"] = d.get("UOM")
        # One string a UI can show without deciding: a rack when there is one,
        # otherwise the free-text place, otherwise an honest blank.
        u["where"] = u["location_code"] or u["location_note"] or None
        u["maps_url"] = (
            f"https://www.google.com/maps?q={u['current_lat']},{u['current_lng']}"
            if u["current_lat"] is not None and u["current_lng"] is not None else None)
    return units


# ─── list / register ──────────────────────────────────────────────────────────
@router.get("", summary="List / search asset units")
async def list_assets(site_id: Optional[str] = None,
                      q: Optional[str] = Query(None, max_length=80),
                      sap: Optional[str] = Query(None, max_length=60),
                      status: Optional[str] = Query(None, pattern=_STATUS_RE),
                      limit: int = Query(100, ge=1, le=500),
                      user: dict = Depends(get_current_user),
                      session: AsyncSession = Depends(get_session)):
    site = resolve_site_param(user, site_id)
    stmt = select(unit_t)
    if site is not None:
        stmt = stmt.where(unit_t.c["Site_ID"] == site)
    if sap:
        stmt = stmt.where(func.trim(unit_t.c["SAP_Code"]) == sap.strip())
    if status:
        stmt = stmt.where(unit_t.c["status"] == status)
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(or_(unit_t.c["serial_no"].ilike(like),
                              unit_t.c["asset_tag"].ilike(like),
                              unit_t.c["SAP_Code"].ilike(like),
                              unit_t.c["holder"].ilike(like)))
    items = await _enrich(session, _rows(await session.execute(
        stmt.order_by(unit_t.c["SAP_Code"], unit_t.c["serial_no"]).limit(limit))))
    return {"items": items}


class UnitCreate(BaseModel):
    SAP_Code: str = Field(min_length=1, max_length=60)
    serial_no: str = Field(min_length=1, max_length=80)
    asset_tag: Optional[str] = Field(default=None, max_length=80)
    # The condition, asked for at registration: the workbook has no Status
    # column, so the app is where it gets said. Absent → the table default.
    status: Optional[str] = Field(default=None, pattern=_STATUS_RE)
    current_location_id: Optional[int] = None
    location_note: Optional[str] = Field(default=None, max_length=200)
    holder: Optional[str] = Field(default=None, max_length=120)
    notes: Optional[str] = Field(default=None, max_length=400)
    site_id: Optional[str] = None


@router.post("", status_code=201, summary="Register one physical unit")
async def create_asset(body: UnitCreate,
                       user: dict = Depends(require_level(1)),
                       session: AsyncSession = Depends(get_session)):
    site = _write_site(user, body.site_id)
    sap = body.SAP_Code.strip()
    known = (await session.execute(
        select(func.count()).select_from(inventory_t)
        .where(func.trim(inventory_t.c["SAP_Code"]) == sap))).scalar_one()
    if not known:
        raise HTTPException(422, f"no inventory item with SAP code {sap!r}")
    vals = body.model_dump(exclude={"site_id", "SAP_Code"}, exclude_none=True)
    vals |= {"Site_ID": site, "SAP_Code": sap,
             "serial_no": body.serial_no.strip(), "created_by": user["username"],
             "last_seen_at": func.now(), "last_seen_by": user["username"]}
    try:
        new_id = (await session.execute(
            insert(unit_t).values(**vals).returning(unit_t.c["id"]))).scalar_one()
    except IntegrityError:
        await session.rollback()
        # The uniqueness is GLOBAL now (alembic a3c17e9b25d4), so name where
        # the existing one actually is — "already exists at your site" was
        # the old message and would now be a lie whenever it is elsewhere.
        where = (await session.execute(
            select(unit_t.c["Site_ID"], unit_t.c["status"], unit_t.c["holder"])
            .where(func.trim(unit_t.c["SAP_Code"]) == sap,
                   func.trim(unit_t.c["serial_no"]) == body.serial_no.strip()))).first()
        if where is None:
            raise HTTPException(409, f"{sap} serial {body.serial_no!r} already exists")
        raise HTTPException(
            409, f"{sap} serial {body.serial_no!r} is already registered at "
                 f"{where[0]} ({where[1]}{', held by ' + where[2] if where[2] else ''}). "
                 "A serial number identifies ONE physical item — if it has moved "
                 "here, request a transfer from that site rather than registering "
                 "it again.")
    # The registration IS the first movement — otherwise the history starts
    # with a gap and "where has this been" cannot answer for the first leg.
    await session.execute(insert(move_t).values(
        asset_unit_id=new_id, moved_by=user["username"],
        to_location_id=body.current_location_id, to_note=body.location_note,
        source="register", status=body.status or "in_stock", note="registered"))
    await write_audit(session, user["username"], "ASSET_REGISTER", "asset_units",
                      f"{site}/{sap}/{body.serial_no} id={new_id}")
    await session.commit()
    return {"created": True, "id": new_id}


# ─── the scan path ────────────────────────────────────────────────────────────
@router.get("/resolve", summary="A scan → this exact unit, or the choice of units")
async def resolve(scan: str = Query(min_length=1, max_length=200),
                  site_id: Optional[str] = None,
                  user: dict = Depends(get_current_user),
                  session: AsyncSession = Depends(get_session)):
    """Turn a decoded label into something actionable.

    Three outcomes, in order, and the ORDER is the design:

      `unit`      the scan named one physical thing — a serial or an asset tag.
                  Answer immediately; there is nothing to choose.
      `choice`    the scan named a SAP that has several units. This is the
                  two-hammers case: return them all and let the human say which
                  one is in their hand.
      `material`  the SAP has no registered units. Fall through to the ordinary
                  material behaviour rather than inventing an asset.

    A serial is tried BEFORE a SAP because it is the more specific claim: a
    string that is both would otherwise resolve to the vaguer answer.
    """
    site = resolve_site_param(user, site_id)
    s = scan.strip()

    base = select(unit_t)
    if site is not None:
        base = base.where(unit_t.c["Site_ID"] == site)

    exact = await _enrich(session, _rows(await session.execute(
        base.where(or_(func.trim(unit_t.c["serial_no"]) == s,
                       func.trim(unit_t.c["asset_tag"]) == s)))))
    if len(exact) == 1:
        return {"kind": "unit", "unit": exact[0]}
    if len(exact) > 1:
        # Same serial re-used across two SAPs. Rare, and a real data problem —
        # say so rather than silently picking the first.
        return {"kind": "choice", "reason": "serial_ambiguous", "units": exact}

    units = await _enrich(session, _rows(await session.execute(
        base.where(func.trim(unit_t.c["SAP_Code"]) == s)
        .order_by(unit_t.c["serial_no"]))))
    if len(units) == 1:
        return {"kind": "unit", "unit": units[0]}
    if units:
        return {"kind": "choice", "reason": "several_units", "SAP_Code": s,
                "units": units}
    return {"kind": "material", "SAP_Code": s}


# ⚠️ DECLARED BEFORE `/{unit_id}`. FastAPI matches in declaration order, so a
# literal path that sits after a parameterised sibling is unreachable —
# GET /assets/transfers would resolve to get_asset(unit_id="transfers") and
# answer 422 "not a valid integer". `/resolve` above is here for the same
# reason; keep any new literal route in this block.
@router.get("/transfers", summary="Asset transfer requests")
async def list_transfers(status: Optional[str] = None,
                         user: dict = Depends(get_current_user),
                         session: AsyncSession = Depends(get_session)):
    stmt = select(transfer_t)
    if status:
        stmt = stmt.where(transfer_t.c["status"] == status)
    scope = site_scope(user)
    if scope is not None:
        # Both ends see it: the site losing the asset has to decide, and the
        # site expecting it needs to know whether it is coming.
        stmt = stmt.where((transfer_t.c["from_site"] == scope)
                          | (transfer_t.c["to_site"] == scope))
    rows = (await session.execute(
        stmt.order_by(transfer_t.c["id"].desc()).limit(300))).mappings().all()
    return {"items": [dict(r) for r in rows]}


@router.get("/{unit_id}", summary="One unit + its recent movements")
async def get_asset(unit_id: int, limit: int = Query(20, ge=1, le=200),
                    user: dict = Depends(get_current_user),
                    session: AsyncSession = Depends(get_session)):
    rows = await _enrich(session, _rows(await session.execute(
        select(unit_t).where(unit_t.c["id"] == unit_id))))
    scope = site_scope(user)
    if not rows or (scope is not None and rows[0]["Site_ID"] != scope):
        raise HTTPException(404, "asset unit not found")
    moves = _rows(await session.execute(
        select(move_t).where(move_t.c["asset_unit_id"] == unit_id)
        .order_by(move_t.c["moved_at"].desc(), move_t.c["id"].desc()).limit(limit)))
    for m in moves:
        m["maps_url"] = (f"https://www.google.com/maps?q={m['lat']},{m['lng']}"
                         if m["lat"] is not None and m["lng"] is not None else None)
    return {"unit": rows[0], "movements": moves}


# ─── move it ──────────────────────────────────────────────────────────────────
class MoveBody(BaseModel):
    location_id: Optional[int] = None
    location_note: Optional[str] = Field(default=None, max_length=200)
    holder: Optional[str] = Field(default=None, max_length=120)
    status: Optional[str] = Field(default=None, pattern=_STATUS_RE)
    note: Optional[str] = Field(default=None, max_length=400)
    gps: Optional[GpsFix] = None
    source: str = Field(default="manual", max_length=20)


@router.patch("/{unit_id}/location", summary="Record where a unit is now")
async def move_asset(unit_id: int, body: MoveBody,
                     user: dict = Depends(require_level(1)),
                     session: AsyncSession = Depends(get_session)):
    """One call does the whole job: history row + cached current position.

    Both in ONE transaction, so the cache can never disagree with the log it
    summarises. If the GPS is absent the move still lands — see the module note.
    """
    cur = (await session.execute(
        select(unit_t).where(unit_t.c["id"] == unit_id))).mappings().first()
    scope = site_scope(user)
    if cur is None or (scope is not None and cur["Site_ID"] != scope):
        raise HTTPException(404, "asset unit not found")

    if body.location_id is not None:
        rack = (await session.execute(
            select(loc_t.c["Site_ID"]).where(loc_t.c["id"] == body.location_id))).first()
        if rack is None or rack[0] != cur["Site_ID"]:
            raise HTTPException(
                422, f"no storage location {body.location_id} at {cur['Site_ID']}")

    gps = body.gps.clean() if body.gps else {}
    await session.execute(insert(move_t).values(
        asset_unit_id=unit_id, moved_by=user["username"],
        from_location_id=cur["current_location_id"],
        to_location_id=body.location_id,
        from_note=cur["location_note"], to_note=body.location_note,
        source=body.source, status=body.status or cur["status"],
        note=body.note, **gps))

    vals = {"current_location_id": body.location_id,
            "location_note": body.location_note,
            "last_seen_at": func.now(), "last_seen_by": user["username"]}
    if body.holder is not None:
        vals["holder"] = body.holder
    if body.status:
        vals["status"] = body.status
    if gps:
        vals |= {"current_lat": gps["lat"], "current_lng": gps["lng"],
                 "gps_accuracy_m": gps.get("accuracy_m")}
    await session.execute(update(unit_t).where(unit_t.c["id"] == unit_id).values(**vals))
    await write_audit(session, user["username"], "ASSET_MOVE", "asset_units",
                      f"{cur['SAP_Code']}/{cur['serial_no']} → "
                      f"{body.location_id or body.location_note or '—'}"
                      + (" +gps" if gps else " (no gps)"))
    await session.commit()
    out = await _enrich(session, _rows(await session.execute(
        select(unit_t).where(unit_t.c["id"] == unit_id))))
    return {"updated": True, "unit": out[0], "gps_recorded": bool(gps)}


# ─── site transfers (approved by the site giving the asset away) ──────────────
class TransferIn(BaseModel):
    to_site: str
    reason: str = Field(..., min_length=3)


class TransferDecideIn(BaseModel):
    action: str = Field(..., pattern="^(approve|reject)$")
    notes: Optional[str] = None


async def _site_names(session: AsyncSession) -> list[str]:
    rows = (await session.execute(
        select(sysset_t.c["value"]).where(sysset_t.c["category"] == "Site")
        .order_by(sysset_t.c["id"]))).all()
    return [r[0] for r in rows]


@router.post("/{unit_id}/transfer", status_code=201,
             summary="Request that this asset move to another site")
async def request_transfer(unit_id: int, body: TransferIn,
                           user: dict = Depends(require_level(1)),
                           session: AsyncSession = Depends(get_session)):
    """Anyone who can MOVE an asset may ask to transfer it; the source HOD agrees.

    `require_level(1)` matches the rest of this module — registering, moving
    and retiring are all level 1 — and asking for a transfer is the same
    class of act. It sits deliberately BELOW the approval so that requesting
    and releasing are two different people: a supervisor or warehouse user
    raises it, and only the source site's HOD (level 2) can complete it.

    (Store keepers are level 0 here and cannot raise one, consistent with not
    being able to register or move a unit either — see the module docstring.)
    """
    to_site = (body.to_site or "").strip()
    cur = (await session.execute(
        select(unit_t).where(unit_t.c["id"] == unit_id))).mappings().first()
    if cur is None:
        raise HTTPException(404, "asset unit not found")
    if to_site == cur["Site_ID"]:
        raise HTTPException(422, f"this asset is already at {to_site}")
    if to_site not in await _site_names(session):
        raise HTTPException(422, f"unknown site {to_site!r} — pick an admin-created site")
    try:
        tid = (await session.execute(insert(transfer_t).values(
            asset_unit_id=unit_id, SAP_Code=cur["SAP_Code"],
            serial_no=cur["serial_no"], from_site=cur["Site_ID"], to_site=to_site,
            reason=body.reason.strip(), requested_by=user["username"],
        ).returning(transfer_t.c["id"]))).scalar_one()
    except IntegrityError:
        # ux_asset_transfer_open — one open request per asset, enforced in the
        # database so two sites cannot both hold a claim on the same hammer.
        await session.rollback()
        raise HTTPException(
            409, "this asset already has a transfer awaiting the source site's HOD")
    await write_audit(session, user["username"], "ASSET_TRANSFER_REQUEST",
                      "asset_transfers",
                      f"id={tid} {cur['SAP_Code']}/{cur['serial_no']} "
                      f"{cur['Site_ID']}→{to_site}")
    await dispatch(
        session, event_key="asset_transfer_requested",
        recipient_role="hod", recipient_site=cur["Site_ID"],
        wa_template="action_required",
        title=f"Asset transfer needs your approval — {cur['SAP_Code']}",
        body=(f"{user['username']} asks to move serial {cur['serial_no']} from "
              f"{cur['Site_ID']} to {to_site}. Reason: {body.reason.strip()}. "
              "It stays on your books until you approve."),
        link_page="/assets", related_table="asset_transfers",
        related_ref=tid, created_by=user["username"])
    await session.commit()
    return {"requested": True, "id": tid, "from_site": cur["Site_ID"],
            "to_site": to_site, "status": "pending_source_hod"}


@router.post("/transfers/{tid}/decide",
             summary="Source site's HOD approves or rejects an asset transfer")
async def decide_transfer(tid: int, body: TransferDecideIn,
                          user: dict = Depends(require_level(2)),
                          session: AsyncSession = Depends(get_session)):
    """Approval is the ONLY thing that moves `asset_units.Site_ID`.

    The whole point of the workflow: an asset does not change hands because
    somebody typed a different site into a form. It changes hands because the
    site that had it said so, and the movement log records who.
    """
    row = (await session.execute(select(transfer_t)
           .where(transfer_t.c["id"] == tid))).mappings().first()
    if row is None:
        raise HTTPException(404, f"transfer {tid} not found")
    if row["status"] != "pending_source_hod":
        raise HTTPException(409, f"transfer {tid} is already {row['status']}")
    scope = site_scope(user)
    # The SOURCE site decides. A destination HOD approving their own incoming
    # transfer would be exactly the self-service move this prevents.
    if scope is not None and row["from_site"] != scope:
        raise HTTPException(
            403, f"only {row['from_site']}'s HOD can release this asset")

    if body.action == "reject":
        await session.execute(update(transfer_t).where(transfer_t.c["id"] == tid)
                              .values(status="rejected", decided_by=user["username"],
                                      decided_at=func.now(),
                                      decision_notes=(body.notes or "").strip() or None))
        await write_audit(session, user["username"], "ASSET_TRANSFER_REJECT",
                          "asset_transfers", f"id={tid}")
        await dispatch(session, event_key="asset_transfer_decided",
                       recipient_user=row["requested_by"], wa_template="status_update",
                       title=f"Asset transfer rejected — {row['SAP_Code']}",
                       body=(f"{user['username']} kept serial {row['serial_no']} at "
                             f"{row['from_site']}."
                             + (f" {body.notes.strip()}" if body.notes else "")),
                       link_page="/assets", related_table="asset_transfers",
                       related_ref=tid, created_by=user["username"])
        await session.commit()
        return {"decided": True, "id": tid, "status": "rejected"}

    cur = (await session.execute(select(unit_t)
           .where(unit_t.c["id"] == row["asset_unit_id"]))).mappings().first()
    if cur is None:
        raise HTTPException(409, "the asset no longer exists")
    # The move is logged BEFORE the row is updated, and both in one
    # transaction, so the cached position can never disagree with its history
    # — the same discipline move_asset() uses.
    mid = (await session.execute(insert(move_t).values(
        asset_unit_id=cur["id"], moved_by=user["username"],
        from_location_id=cur["current_location_id"], to_location_id=None,
        from_note=cur["location_note"],
        to_note=f"transferred to {row['to_site']}",
        source="site_transfer", status=cur["status"],
        note=f"approved by {user['username']}: {row['reason']}",
    ).returning(move_t.c["id"]))).scalar_one()
    await session.execute(update(unit_t).where(unit_t.c["id"] == cur["id"]).values(
        Site_ID=row["to_site"],
        # The rack belonged to the OLD site, so it cannot survive the move —
        # leaving it would point at a shelf in another yard.
        current_location_id=None, location_note=f"in transit from {row['from_site']}",
        last_seen_at=func.now(), last_seen_by=user["username"]))
    await session.execute(update(transfer_t).where(transfer_t.c["id"] == tid).values(
        status="approved", decided_by=user["username"], decided_at=func.now(),
        decision_notes=(body.notes or "").strip() or None, movement_id=mid))
    await write_audit(session, user["username"], "ASSET_TRANSFER_APPROVE",
                      "asset_transfers",
                      f"id={tid} {cur['SAP_Code']}/{cur['serial_no']} "
                      f"{row['from_site']}→{row['to_site']} movement={mid}")
    for site, title in ((row["to_site"], "Asset arriving"),
                        (row["from_site"], "Asset released")):
        await dispatch(session, event_key="asset_transfer_decided", severity="success",
                       recipient_role="store_keeper", recipient_site=site,
                       wa_template="status_update",
                       title=f"{title} — {cur['SAP_Code']} #{cur['serial_no']}",
                       body=(f"{user['username']} approved the move "
                             f"{row['from_site']} → {row['to_site']}. "
                             "Scan it in on arrival to set its rack."),
                       link_page="/assets", related_table="asset_transfers",
                       related_ref=tid, created_by=user["username"])
    await session.commit()
    return {"decided": True, "id": tid, "status": "approved",
            "to_site": row["to_site"], "movement_id": mid}


class UnitPatch(BaseModel):
    asset_tag: Optional[str] = None
    holder: Optional[str] = None
    notes: Optional[str] = None
    status: Optional[str] = Field(default=None, pattern=_STATUS_RE)


@router.patch("/{unit_id}", summary="Edit a unit's details")
async def patch_asset(unit_id: int, body: UnitPatch,
                      user: dict = Depends(require_level(1)),
                      session: AsyncSession = Depends(get_session)):
    changes = body.model_dump(exclude_unset=True)
    if not changes:
        raise HTTPException(422, "no fields to update")
    stmt = update(unit_t).where(unit_t.c["id"] == unit_id)
    scope = site_scope(user)
    if scope is not None:
        stmt = stmt.where(unit_t.c["Site_ID"] == scope)
    if (await session.execute(stmt.values(**changes))).rowcount != 1:
        raise HTTPException(404, "asset unit not found")
    await write_audit(session, user["username"], "ASSET_UPDATE", "asset_units",
                      f"id={unit_id} fields={sorted(changes)}")
    await session.commit()
    return {"updated": True}


@router.delete("/{unit_id}", summary="Delete a unit (its movement history goes too)")
async def delete_asset(unit_id: int,
                       user: dict = Depends(require_level(1)),
                       session: AsyncSession = Depends(get_session)):
    row = (await session.execute(
        select(unit_t.c["Site_ID"], unit_t.c["SAP_Code"], unit_t.c["serial_no"])
        .where(unit_t.c["id"] == unit_id))).first()
    scope = site_scope(user)
    if row is None or (scope is not None and row[0] != scope):
        raise HTTPException(404, "asset unit not found")
    n = (await session.execute(
        delete(move_t).where(move_t.c["asset_unit_id"] == unit_id))).rowcount
    await session.execute(delete(unit_t).where(unit_t.c["id"] == unit_id))
    await write_audit(session, user["username"], "ASSET_DELETE", "asset_units",
                      f"{row[0]}/{row[1]}/{row[2]} id={unit_id} (+{n} movement(s))")
    await session.commit()
    return {"deleted": True, "movements_removed": n}
