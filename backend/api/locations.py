"""
backend/api/locations.py — the warehouse rack locator.

"Which rack is this in?" A store keeper types a material name, a SAP code, or
scans a QR, and gets back the shelf to walk to.

    GET  /locations                   list / search racks
    POST /locations                   create a rack                  (write)
    PATCH/DELETE /locations/{id}      edit / retire a rack           (write)
    GET  /locations/lookup            material → rack(s)   ← THE HOT PATH
    PUT  /locations/material          assign a material to a rack    (write)
    DELETE /locations/material/{id}   unassign                       (write)
    GET  /locations/{code}/contents   scan a RACK → what is in it

ON "BLAZINGLY FAST". `inventory` is 452 rows at this site and
`storage_locations` will be a few hundred. The lookup is an index scan on
`(SAP_Code, Site_ID)` against a table that fits in a single page of shared
buffers, and it is measured in the run log rather than asserted. No cache, no
search engine, no denormalisation: adding one would repeat exactly the mistake
rule 11 exists to prevent, where four candidate indexes were benchmarked and
REJECTED because the planner never used them.

What actually makes it feel instant is the frontend, and that already exists —
the ⌘K palette debounces, searches live stock, and scopes by site server-side.
The locator becomes one more section in it.

THE REVERSE DIRECTION IS FREE. `GET /locations/{code}/contents` answers "what
is supposed to be on this shelf" from the same index, which is what turns a
stock count from a hunt into a checklist.

Roles: reads are open to any authenticated user — a store keeper is level 0 and
is precisely who needs this. Writes are `require_level(1)` (warehouse and up),
because deciding where stock lives is a supervisory act. Auditors are refused
every write by `readonly.py`'s method-keyed middleware, which is not modified
here and to which nothing is added.
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

router = APIRouter(prefix="/locations", tags=["warehouse locator"])

loc_t = _MD.tables["storage_locations"]
mat_loc_t = _MD.tables["material_locations"]
inventory_t = _MD.tables["inventory"]


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


def _label(r) -> str:
    """Human-readable place, from whichever parts are filled in.

    The `code` is the identifier; this is what a person reads out loud on the
    warehouse floor ("Zone A · Rack 03 · Row 2"). Built from the parts rather
    than stored, so editing a rack cannot leave a stale label behind.
    """
    bits = []
    if r.get("zone"):
        bits.append(f"Zone {r['zone']}")
    if r.get("rack_no"):
        bits.append(f"Rack {r['rack_no']}")
    if r.get("row_no"):
        bits.append(f"Row {r['row_no']}")
    if r.get("bin_no"):
        bits.append(f"Bin {r['bin_no']}")
    return " · ".join(bits) or (r.get("description") or r.get("code") or "")


# ─── racks ────────────────────────────────────────────────────────────────────
@router.get("", summary="List / search storage locations")
async def list_locations(site_id: Optional[str] = None,
                         q: Optional[str] = Query(None, max_length=80),
                         include_retired: bool = False,
                         user: dict = Depends(get_current_user),
                         session: AsyncSession = Depends(get_session)):
    site = resolve_site_param(user, site_id)
    stmt = select(loc_t)
    if site is not None:
        stmt = stmt.where(loc_t.c["Site_ID"] == site)
    if not include_retired:
        stmt = stmt.where(loc_t.c["status"] == "active")
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(or_(loc_t.c["code"].ilike(like),
                              loc_t.c["zone"].ilike(like),
                              loc_t.c["rack_no"].ilike(like),
                              loc_t.c["description"].ilike(like)))
    items = _rows(await session.execute(stmt.order_by(
        loc_t.c["zone"], loc_t.c["rack_no"], loc_t.c["row_no"], loc_t.c["code"])))
    for it in items:
        it["label"] = _label(it)
    return {"items": items}


class LocationCreate(BaseModel):
    code: str = Field(min_length=1, max_length=60)
    zone: Optional[str] = Field(default=None, max_length=40)
    rack_no: Optional[str] = Field(default=None, max_length=40)
    row_no: Optional[str] = Field(default=None, max_length=40)
    bin_no: Optional[str] = Field(default=None, max_length=40)
    description: Optional[str] = Field(default=None, max_length=200)
    site_id: Optional[str] = None


class LocationPatch(BaseModel):
    zone: Optional[str] = None
    rack_no: Optional[str] = None
    row_no: Optional[str] = None
    bin_no: Optional[str] = None
    description: Optional[str] = None
    status: Optional[str] = Field(default=None, pattern="^(active|retired)$")


@router.post("", status_code=201, summary="Create a storage location")
async def create_location(body: LocationCreate,
                          user: dict = Depends(require_level(1)),
                          session: AsyncSession = Depends(get_session)):
    site = _write_site(user, body.site_id)
    vals = body.model_dump(exclude={"site_id"}, exclude_none=True)
    vals["code"] = vals["code"].strip()
    vals |= {"Site_ID": site, "created_by": user["username"]}
    try:
        new_id = (await session.execute(
            insert(loc_t).values(**vals).returning(loc_t.c["id"]))).scalar_one()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(409, f"location {vals['code']!r} already exists at {site}")
    await write_audit(session, user["username"], "LOCATION_CREATE",
                      "storage_locations", f"{site}/{vals['code']} id={new_id}")
    await session.commit()
    return {"created": True, "id": new_id}


@router.patch("/{loc_id}", summary="Edit or retire a storage location")
async def patch_location(loc_id: int, body: LocationPatch,
                         user: dict = Depends(require_level(1)),
                         session: AsyncSession = Depends(get_session)):
    changes = body.model_dump(exclude_unset=True)
    if not changes:
        raise HTTPException(422, "no fields to update")
    stmt = update(loc_t).where(loc_t.c["id"] == loc_id)
    scope = site_scope(user)
    if scope is not None:
        stmt = stmt.where(loc_t.c["Site_ID"] == scope)
    res = await session.execute(stmt.values(**changes))
    if res.rowcount != 1:
        raise HTTPException(404, "location not found")
    await write_audit(session, user["username"], "LOCATION_UPDATE",
                      "storage_locations", f"id={loc_id} fields={sorted(changes)}")
    await session.commit()
    return {"updated": True}


@router.delete("/{loc_id}", summary="Delete a storage location (and its assignments)")
async def delete_location(loc_id: int,
                          user: dict = Depends(require_level(1)),
                          session: AsyncSession = Depends(get_session)):
    row = (await session.execute(
        select(loc_t.c["Site_ID"], loc_t.c["code"])
        .where(loc_t.c["id"] == loc_id))).first()
    scope = site_scope(user)
    if row is None or (scope is not None and row[0] != scope):
        raise HTTPException(404, "location not found")
    # Assignments go with it. A material_locations row pointing at a rack that
    # no longer exists would render as a blank shelf in the lookup — worse than
    # no answer, because it looks like an answer.
    n = (await session.execute(
        delete(mat_loc_t).where(mat_loc_t.c["location_id"] == loc_id))).rowcount
    await session.execute(delete(loc_t).where(loc_t.c["id"] == loc_id))
    await write_audit(session, user["username"], "LOCATION_DELETE",
                      "storage_locations",
                      f"{row[0]}/{row[1]} id={loc_id} (+{n} assignment(s))")
    await session.commit()
    return {"deleted": True, "assignments_removed": n}


# ─── the hot path ─────────────────────────────────────────────────────────────
@router.get("/lookup", summary="Material → the rack(s) to walk to")
async def lookup(q: Optional[str] = Query(None, max_length=80),
                 sap: Optional[str] = Query(None, max_length=60),
                 site_id: Optional[str] = None,
                 limit: int = Query(20, ge=1, le=100),
                 user: dict = Depends(get_current_user),
                 session: AsyncSession = Depends(get_session)):
    """Find where a material lives.

    `sap` is the exact-code path (a scan, or a code typed in full). `q` is the
    human path — a partial code or any part of the description.

    Results are ordered primary-first, so the answer to "where do I go" is the
    first row even when a material is stocked in three places. Materials with
    NO assigned rack are still returned, with an empty `locations` list and a
    `located: false` flag: "we stock it but nobody has said where" is a real
    answer and a silent omission would read as "we don't stock it".
    """
    site = resolve_site_param(user, site_id)
    if not q and not sap:
        raise HTTPException(422, "pass q= or sap=")

    inv = inventory_t.c
    stmt = select(inv["SAP_Code"], inv["Material_Code"],
                  inv["Equipment_Description"], inv["UOM"], inv["Category"])
    if sap:
        stmt = stmt.where(func.trim(inv["SAP_Code"]) == sap.strip())
    else:
        like = f"%{q.strip()}%"
        stmt = stmt.where(or_(inv["SAP_Code"].ilike(like),
                              inv["Material_Code"].ilike(like),
                              inv["Equipment_Description"].ilike(like)))
    items = _rows(await session.execute(stmt.limit(limit)))
    if not items:
        return {"items": []}

    saps = [i["SAP_Code"] for i in items]
    lstmt = (select(mat_loc_t.c["id"].label("assignment_id"),
                    mat_loc_t.c["SAP_Code"], mat_loc_t.c["is_primary"],
                    mat_loc_t.c["note"], loc_t)
             .join(loc_t, loc_t.c["id"] == mat_loc_t.c["location_id"])
             .where(mat_loc_t.c["SAP_Code"].in_(saps)))
    if site is not None:
        lstmt = lstmt.where(mat_loc_t.c["Site_ID"] == site)
    by_sap: dict[str, list[dict]] = {}
    for r in _rows(await session.execute(
            lstmt.order_by(mat_loc_t.c["is_primary"].desc(), loc_t.c["code"]))):
        r["label"] = _label(r)
        by_sap.setdefault(r["SAP_Code"], []).append(r)

    for it in items:
        locs = by_sap.get(it["SAP_Code"], [])
        it["locations"] = locs
        it["located"] = bool(locs)
        it["primary_location"] = locs[0]["code"] if locs else None
        it["primary_label"] = locs[0]["label"] if locs else None
    return {"items": items}


@router.get("/{code}/contents", summary="Scan a RACK → what is meant to be in it")
async def rack_contents(code: str, site_id: Optional[str] = None,
                        user: dict = Depends(get_current_user),
                        session: AsyncSession = Depends(get_session)):
    """The reverse direction, and it costs nothing extra.

    Scanning the shelf's own QR answers "what is supposed to be here", which is
    what turns a stock count from a hunt into a checklist.
    """
    site = resolve_site_param(user, site_id)
    stmt = select(loc_t).where(func.trim(loc_t.c["code"]) == code.strip())
    if site is not None:
        stmt = stmt.where(loc_t.c["Site_ID"] == site)
    rack = (await session.execute(stmt)).mappings().first()
    if rack is None:
        raise HTTPException(404, f"no storage location {code!r}")
    rack = dict(rack)
    rack["label"] = _label(rack)

    inv = inventory_t.c
    items = _rows(await session.execute(
        select(mat_loc_t.c["id"].label("assignment_id"), mat_loc_t.c["SAP_Code"],
               mat_loc_t.c["is_primary"], mat_loc_t.c["note"],
               inv["Material_Code"], inv["Equipment_Description"], inv["UOM"])
        .select_from(mat_loc_t)
        .outerjoin(inventory_t, inv["SAP_Code"] == mat_loc_t.c["SAP_Code"])
        .where(mat_loc_t.c["location_id"] == rack["id"])
        .order_by(mat_loc_t.c["SAP_Code"])))
    return {"location": rack, "items": items}


# ─── assignment ───────────────────────────────────────────────────────────────
class AssignMaterial(BaseModel):
    SAP_Code: str = Field(min_length=1, max_length=60)
    location_id: int
    is_primary: bool = True
    note: Optional[str] = Field(default=None, max_length=200)
    site_id: Optional[str] = None


@router.put("/material", summary="Put a material in a rack (idempotent)")
async def assign_material(body: AssignMaterial,
                          user: dict = Depends(require_level(1)),
                          session: AsyncSession = Depends(get_session)):
    site = _write_site(user, body.site_id)
    sap = body.SAP_Code.strip()

    known = (await session.execute(
        select(func.count()).select_from(inventory_t)
        .where(func.trim(inventory_t.c["SAP_Code"]) == sap))).scalar_one()
    if not known:
        raise HTTPException(422, f"no inventory item with SAP code {sap!r}")
    rack = (await session.execute(
        select(loc_t.c["Site_ID"], loc_t.c["code"])
        .where(loc_t.c["id"] == body.location_id))).first()
    if rack is None or rack[0] != site:
        raise HTTPException(422, f"no storage location {body.location_id} at {site}")

    # One primary per (site, SAP): promoting a rack demotes the others, so
    # "where do I go first" always has exactly one answer.
    if body.is_primary:
        await session.execute(update(mat_loc_t)
                              .where(mat_loc_t.c["Site_ID"] == site,
                                     mat_loc_t.c["SAP_Code"] == sap)
                              .values(is_primary=False))

    vals = {"Site_ID": site, "SAP_Code": sap, "location_id": body.location_id,
            "is_primary": body.is_primary, "note": body.note,
            "updated_by": user["username"], "updated_at": func.now()}
    from sqlalchemy.dialects.postgresql import insert as pg_insert
    stmt = pg_insert(mat_loc_t).values(**vals)
    await session.execute(stmt.on_conflict_do_update(
        constraint="uq_material_locations_site_sap_loc",
        set_={"is_primary": stmt.excluded["is_primary"],
              "note": stmt.excluded["note"],
              "updated_by": stmt.excluded["updated_by"],
              "updated_at": stmt.excluded["updated_at"]}))
    await write_audit(session, user["username"], "LOCATION_ASSIGN",
                      "material_locations",
                      f"{site}/{sap} → {rack[1]}"
                      + (" (primary)" if body.is_primary else ""))
    await session.commit()
    return {"assigned": True, "SAP_Code": sap, "location": rack[1]}


@router.delete("/material/{assignment_id}", summary="Take a material out of a rack")
async def unassign_material(assignment_id: int,
                            user: dict = Depends(require_level(1)),
                            session: AsyncSession = Depends(get_session)):
    row = (await session.execute(
        select(mat_loc_t.c["Site_ID"], mat_loc_t.c["SAP_Code"])
        .where(mat_loc_t.c["id"] == assignment_id))).first()
    scope = site_scope(user)
    if row is None or (scope is not None and row[0] != scope):
        raise HTTPException(404, "assignment not found")
    await session.execute(delete(mat_loc_t)
                          .where(mat_loc_t.c["id"] == assignment_id))
    await write_audit(session, user["username"], "LOCATION_UNASSIGN",
                      "material_locations", f"{row[0]}/{row[1]} id={assignment_id}")
    await session.commit()
    return {"deleted": True}
