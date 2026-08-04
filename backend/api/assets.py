"""
backend/api/assets.py — serialised asset tracking, and where things are.

    GET    /assets                    list / search units
    POST   /assets                    register a unit                 (write)
    GET    /assets/resolve             a scan → the unit, or the choice
    GET    /assets/{id}                one unit + its recent movements
    PATCH  /assets/{id}/location       move it (rack, free text, and/or GPS)
    PATCH  /assets/{id}                edit status / holder / notes
    DELETE /assets/{id}                retire a unit                  (write)

THE PROBLEM THIS SOLVES. Two hammers share one SAP code, so scanning either
label resolves to the same inventory row. `asset_units` is one row per physical
thing, keyed `(Site_ID, SAP_Code, serial_no)`, and `/assets/resolve` is what
turns a scan into "this exact hammer" — or, when the sticker only carries the
SAP, into "which of these three?".

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

router = APIRouter(prefix="/assets", tags=["asset tracking"])

unit_t = _MD.tables["asset_units"]
move_t = _MD.tables["asset_movements"]
loc_t = _MD.tables["storage_locations"]
inventory_t = _MD.tables["inventory"]

# Coordinates outside these are not a place on Earth; a bad sensor reading
# should be rejected at the door rather than stored and plotted later.
_LAT = (-90.0, 90.0)
_LNG = (-180.0, 180.0)


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
                      status: Optional[str] = None,
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
        raise HTTPException(409, f"{sap} serial {body.serial_no!r} already exists at {site}")
    # The registration IS the first movement — otherwise the history starts
    # with a gap and "where has this been" cannot answer for the first leg.
    await session.execute(insert(move_t).values(
        asset_unit_id=new_id, moved_by=user["username"],
        to_location_id=body.current_location_id, to_note=body.location_note,
        source="register", status="in_stock", note="registered"))
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
    status: Optional[str] = Field(
        default=None, pattern="^(in_stock|issued|returned|lost|scrapped)$")
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


class UnitPatch(BaseModel):
    asset_tag: Optional[str] = None
    holder: Optional[str] = None
    notes: Optional[str] = None
    status: Optional[str] = Field(
        default=None, pattern="^(in_stock|issued|returned|lost|scrapped)$")


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
