"""
Parity A1/A4 — the legacy entry-document system + per-site WBS master,
rebuilt for the new stack.

Documents (`entry_attachments`, BLOB-authoritative — the table and 35 legacy
files were already migrated; this module finally gives them endpoints):
  · SK uploads supporting documents (hand-written notes / delivery notes /
    photos) per batch from the Issue / Receive / Return forms.
  · doc_number defaults to DDMMYY of the submission date (legacy rule);
    receipts may override it with a DN number.
  · The admin setting **require_entry_documents** ('1' by default) turns the
    upload into a HARD GATE: Issue / Receipt / Return submissions are refused
    without at least one attached document. ('0' restores the legacy-optional
    behaviour for issue/receipt.)
  · HODs browse everything in the Document Library (legacy HOD TAB 12).

WBS (`wbs_master`, migrated): legacy blocked SK consumption/receipts without
an active WBS *when the site has WBS numbers configured* — same semantics
here: the gate only bites once an HOD adds WBS rows for the site.

Work types (`wbs_work_type_map`, Phase 9a): the same CONDITIONAL shape, one
level up. An HOD curates a canonical list per site and hangs a WBS number off
each entry; `services.wbs.resolve_wbs` then stamps that number onto issues that
did not name one. Empty list → the gate does nothing and the entry forms keep
their free-text input, so turning the rule on is the HOD's act rather than a
release. Every rule lives in `services/wbs.py`; the endpoints below are
transport, site scoping and the audit line.
"""
from __future__ import annotations

import datetime as _dt
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import delete, insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from .auth import (get_current_user, require_roles, resolve_site_param,
                   resolve_site_write, site_filter_applies, site_row_visible,
                   site_scope)
from .db import get_session
from .services import wbs as wbs_svc
from .services.ledger import _MD, write_audit

router = APIRouter(tags=["entry-docs"])

attachments_t = _MD.tables["entry_attachments"]
wbs_t = _MD.tables["wbs_master"]
wt_t = _MD.tables["wbs_work_type_map"]
settings_t = _MD.tables["app_settings"]

# `safety_approval` (QSEP slice 4) is the PPE distribution's mandatory
# document. It reuses this store rather than getting its own: the BLOB
# handling, the 15 MB cap, the MIME allowlist, the site scoping, the
# uploader-only delete and the audit row are all already correct here, and a
# second document table would have to re-earn every one of them.
#
# It is deliberately NOT in the `assert_entry_docs` batch gate below — that
# gate is per-BATCH ("one note covers this shift's entries"), while a safety
# approval is per-PERSON-per-item. services/ppe.py validates it per line.
# `pr_scan` / `po_scan` (QSEP slice 6) are written by /ai/extract/{pr,po},
# not uploaded through this endpoint — they are listed so the Document
# Library filter and the download route recognise them. They are NOT in the
# `_UPLOADABLE` set below: a purchase-document scan enters through the
# extract endpoint so it is parsed and linked, never as a loose attachment.
# `delivery_note` (2026-08-13) is the scanned physical DN a warehouse must
# attach before a shipment may leave. Like `safety_approval` it reuses this
# store rather than getting its own table, and like it, it is NOT part of the
# per-BATCH `assert_entry_docs` gate below — it is per-SHIPMENT, and
# services/warehouse.ship_dn validates it against the DN being shipped.
_DOC_TYPES = ("consumption", "receipt", "return", "safety_approval",
              "delivery_note", "pr_scan", "po_scan")
_UPLOADABLE = ("consumption", "receipt", "return", "safety_approval",
               "delivery_note")
_MAX_FILE_MB = 15
_ALLOWED_MIME_PREFIXES = ("image/", "application/pdf",
                          "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


# ── settings-driven gate ──────────────────────────────────────────────────────
async def docs_required(session: AsyncSession) -> bool:
    """require_entry_documents app setting — DEFAULT ON (approved parity plan:
    stricter than legacy, where only Return uploads were mandatory)."""
    v = (await session.execute(select(settings_t.c["value"]).where(
        settings_t.c["key"] == "require_entry_documents"))).scalar_one_or_none()
    return (v if v is not None else "1").strip() != "0"


async def assert_entry_docs(session: AsyncSession, *, doc_type: str,
                            attachment_ids: list[int] | None, username: str) -> list[int]:
    """The submit gate: when required, at least one attachment must exist,
    belong to the submitter, and carry the right doc_type."""
    ids = [int(i) for i in (attachment_ids or [])]
    if not ids:
        if await docs_required(session):
            raise HTTPException(
                422, f"a supporting document (hand-written note / delivery note) "
                     f"must be attached before submitting a {doc_type} entry")
        return []
    rows = (await session.execute(select(
        attachments_t.c["id"], attachments_t.c["doc_type"], attachments_t.c["uploaded_by"]
    ).where(attachments_t.c["id"].in_(ids)))).all()
    found = {r.id for r in rows}
    if missing := [i for i in ids if i not in found]:
        raise HTTPException(422, f"unknown attachment id(s): {missing}")
    for r in rows:
        if r.doc_type != doc_type:
            raise HTTPException(422, f"attachment {r.id} is a {r.doc_type} document, not {doc_type}")
        if r.uploaded_by != username:
            raise HTTPException(403, f"attachment {r.id} was uploaded by someone else")
    return ids


async def link_attachments(session: AsyncSession, ids: list[int], *,
                           entry_table: str, entry_date: str | None) -> None:
    if ids:
        await session.execute(update(attachments_t)
                              .where(attachments_t.c["id"].in_(ids))
                              .values(entry_table=entry_table, entry_date=entry_date))


# ── WBS gate ──────────────────────────────────────────────────────────────────
# Both now live in `services/wbs.py`, so the gate and the resolver that has to
# run before it read the same list from one place. Kept as names here because
# three call sites and the legacy vocabulary both point at `entry_docs`.
active_wbs = wbs_svc.active_numbers
assert_wbs = wbs_svc.assert_wbs


# ── attachment endpoints ─────────────────────────────────────────────────────
@router.post("/entry/attachments", status_code=201,
             summary="Upload a supporting document for an entry batch (SK)")
async def upload_attachment(file: UploadFile = File(...),
                            doc_type: str = Form(...),
                            site_id: str = Form(...),
                            doc_number: Optional[str] = Form(None),
                            entry_date: Optional[str] = Form(None),
                            user: dict = Depends(require_roles("store_keeper")),
                            session: AsyncSession = Depends(get_session)):
    if doc_type not in _UPLOADABLE:
        raise HTTPException(422, f"doc_type must be one of {_UPLOADABLE}")
    mime = (file.content_type or "").lower()
    if mime and not any(mime.startswith(p) for p in _ALLOWED_MIME_PREFIXES):
        raise HTTPException(422, "only images, PDFs and XLSX files are accepted")
    blob = await file.read()
    if len(blob) > _MAX_FILE_MB * 1024 * 1024:
        raise HTTPException(413, f"file exceeds {_MAX_FILE_MB} MB")
    if not blob:
        raise HTTPException(422, "empty file")
    site = resolve_site_write(user, site_id.strip())
    # legacy default doc number: DDMMYY of the submission date
    doc_no = (doc_number or "").strip() or _dt.date.today().strftime("%d%m%y")
    aid = (await session.execute(insert(attachments_t).values(
        Site_ID=site, doc_type=doc_type, doc_number=doc_no,
        entry_date=(entry_date or None), file_name=file.filename or "document",
        mime_type=mime or None, file_size=len(blob), file_blob=blob,
        uploaded_by=user["username"],
    ).returning(attachments_t.c["id"]))).scalar_one()
    await write_audit(session, user["username"], "ENTRY_DOC_UPLOAD", "entry_attachments",
                      f"#{aid} {doc_type} {doc_no} {file.filename}")
    await session.commit()
    return {"id": aid, "file_name": file.filename, "doc_number": doc_no}


@router.get("/entry/attachments", summary="Browse entry documents (Document Library)")
async def list_attachments(doc_type: Optional[str] = Query(None),
                           site_id: Optional[str] = Query(None),
                           doc_number: Optional[str] = Query(None),
                           date_from: Optional[str] = Query(None),
                           date_to: Optional[str] = Query(None),
                           mine: bool = Query(False, description="only my uploads (any role)"),
                           limit: int = Query(200, le=1000),
                           user: dict = Depends(get_current_user),
                           session: AsyncSession = Depends(get_session)):
    # SKs may always list their OWN uploads (the form needs it); the full
    # library is level ≥2 (hod / logistics / admin), site-scoped.
    if not mine and user["level"] < 2:
        raise HTTPException(403, "the Document Library is for HOD/logistics/admin")
    stmt = select(attachments_t.c["id"], attachments_t.c["Site_ID"],
                  attachments_t.c["doc_type"], attachments_t.c["doc_number"],
                  attachments_t.c["entry_table"], attachments_t.c["entry_date"],
                  attachments_t.c["file_name"], attachments_t.c["mime_type"],
                  attachments_t.c["file_size"], attachments_t.c["uploaded_by"],
                  attachments_t.c["uploaded_at"])
    if mine:
        stmt = stmt.where(attachments_t.c["uploaded_by"] == user["username"])
    else:
        site = resolve_site_param(user, site_id)
        if site == "":
            return {"items": []}
        if site:
            stmt = stmt.where(attachments_t.c["Site_ID"] == site)
    if doc_type:
        stmt = stmt.where(attachments_t.c["doc_type"] == doc_type)
    if doc_number:
        stmt = stmt.where(attachments_t.c["doc_number"].ilike(f"%{doc_number.strip()}%"))
    if date_from:
        stmt = stmt.where(attachments_t.c["uploaded_at"] >= date_from)
    if date_to:
        stmt = stmt.where(attachments_t.c["uploaded_at"] < date_to + " 23:59:59")
    rows = (await session.execute(
        stmt.order_by(attachments_t.c["id"].desc()).limit(limit))).mappings().all()
    return {"items": [dict(r) for r in rows]}


@router.get("/entry/attachments/{aid}/download", summary="Download / preview one document")
async def download_attachment(aid: int, inline: bool = Query(False),
                              user: dict = Depends(get_current_user),
                              session: AsyncSession = Depends(get_session)):
    row = (await session.execute(select(
        attachments_t.c["Site_ID"], attachments_t.c["file_name"],
        attachments_t.c["mime_type"], attachments_t.c["file_blob"],
        attachments_t.c["uploaded_by"]
    ).where(attachments_t.c["id"] == aid))).first()
    if row is None:
        raise HTTPException(404, "no such document")
    if user["level"] < 2 and row.uploaded_by != user["username"]:
        raise HTTPException(403, "not your document")
    scope = site_scope(user)
    if not site_row_visible(scope, row.Site_ID) and row.uploaded_by != user["username"]:
        raise HTTPException(403, "document belongs to another site")
    import io
    disp = "inline" if inline else "attachment"
    return StreamingResponse(
        io.BytesIO(row.file_blob or b""),
        media_type=row.mime_type or "application/octet-stream",
        headers={"Content-Disposition": f'{disp}; filename="{row.file_name}"'})


@router.delete("/entry/attachments/{aid}", summary="Remove an UNLINKED upload (uploader only)")
async def delete_attachment(aid: int, user: dict = Depends(get_current_user),
                            session: AsyncSession = Depends(get_session)):
    row = (await session.execute(select(
        attachments_t.c["uploaded_by"], attachments_t.c["entry_table"]
    ).where(attachments_t.c["id"] == aid))).first()
    if row is None:
        raise HTTPException(404, "no such document")
    if row.uploaded_by != user["username"] and user["level"] < 4:
        raise HTTPException(403, "only the uploader can remove it")
    if row.entry_table:
        raise HTTPException(409, "already linked to a submitted entry — cannot remove")
    await session.execute(delete(attachments_t).where(attachments_t.c["id"] == aid))
    await session.commit()
    return {"deleted": aid}


# ── WBS endpoints ────────────────────────────────────────────────────────────
@router.get("/entry/wbs", summary="Active WBS numbers for a site (entry-form options)")
async def wbs_options(site_id: str = Query(...),
                      user: dict = Depends(get_current_user),
                      session: AsyncSession = Depends(get_session)):
    # A scoped user always reads their own site's WBS list; a site-less scoped
    # user has no WBS numbers at all rather than the caller-named site's.
    site = resolve_site_param(user, None)
    if site is None:
        site = site_id
    return {"site_id": site, "wbs": await active_wbs(session, site or "")}


class WbsIn(BaseModel):
    WBS_Number: str
    Description: Optional[str] = None
    site_id: Optional[str] = None


@router.get("/hod/site-config/wbs", summary="All WBS rows for the site (HOD manager)")
async def wbs_all(site_id: Optional[str] = Query(None),
                  user: dict = Depends(require_roles("hod")),
                  session: AsyncSession = Depends(get_session)):
    site = resolve_site_param(user, site_id)
    stmt = select(wbs_t)
    if site_filter_applies(site):
        stmt = stmt.where(wbs_t.c["Site_ID"] == site)
    rows = (await session.execute(
        stmt.order_by(wbs_t.c["status"], wbs_t.c["WBS_Number"]))).mappings().all()
    return {"items": [{k: v for k, v in dict(r).items() if k != "created_at"}
                      | {"created_at": str(dict(r).get("created_at") or "")} for r in rows]}


@router.post("/hod/site-config/wbs", status_code=201, summary="Add a WBS number (HOD)")
async def wbs_add(body: WbsIn, user: dict = Depends(require_roles("hod")),
                  session: AsyncSession = Depends(get_session)):
    site = resolve_site_param(user, body.site_id)
    if not site:
        raise HTTPException(422, "site_id required")
    number = body.WBS_Number.strip()
    if not number:
        raise HTTPException(422, "WBS Number cannot be empty")
    exists = (await session.execute(select(wbs_t.c["id"]).where(
        (wbs_t.c["WBS_Number"] == number) & (wbs_t.c["Site_ID"] == site)))).first()
    if exists:
        raise HTTPException(409, f"WBS {number!r} already exists at {site}")
    wid = (await session.execute(insert(wbs_t).values(
        WBS_Number=number, Description=(body.Description or "").strip(),
        Site_ID=site, status="active", created_by=user["username"],
    ).returning(wbs_t.c["id"]))).scalar_one()
    await write_audit(session, user["username"], "WBS_ADD", "wbs_master", f"{number}@{site}")
    await session.commit()
    return {"id": wid, "WBS_Number": number, "Site_ID": site}


@router.patch("/hod/site-config/wbs/{wid}", summary="Open/close a WBS number (HOD)")
async def wbs_status(wid: int, status: str = Query(..., pattern="^(active|closed)$"),
                     user: dict = Depends(require_roles("hod")),
                     session: AsyncSession = Depends(get_session)):
    row = (await session.execute(select(wbs_t.c["Site_ID"]).where(wbs_t.c["id"] == wid))).first()
    if row is None:
        raise HTTPException(404, "no such WBS row")
    if not site_row_visible(site_scope(user), row.Site_ID):
        raise HTTPException(403, "WBS belongs to another site")
    await session.execute(update(wbs_t).where(wbs_t.c["id"] == wid).values(status=status))
    await write_audit(session, user["username"], "WBS_STATUS", "wbs_master", f"#{wid}→{status}")
    await session.commit()
    return {"id": wid, "status": status}


# ── Work types + WBS mapping (Phase 9a) ──────────────────────────────────────
# The dimension the operator plans by. `services.wbs` owns every rule; this
# section is transport, site scoping and the audit line.


class WorkTypeIn(BaseModel):
    Work_Type: str
    WBS_Number: Optional[str] = None
    Description: Optional[str] = None
    site_id: Optional[str] = None


class WorkTypePatch(BaseModel):
    Work_Type: Optional[str] = None
    WBS_Number: Optional[str] = None      # '' clears the mapping; None leaves it
    Description: Optional[str] = None
    status: Optional[str] = None


@router.get("/entry/work-types",
            summary="The site's work-type dropdown (entry forms)")
async def work_type_options(site_id: str = Query(...),
                            user: dict = Depends(get_current_user),
                            session: AsyncSession = Depends(get_session)):
    """Same scoping rule as `/entry/wbs`: a scoped user always reads their OWN
    site's list, never the one named in the query string."""
    site = resolve_site_param(user, None)
    if site is None:
        site = site_id
    items = await wbs_svc.active_work_types(session, site or "")
    # `enforced` tells the form whether to render a Select or keep the free-text
    # Input. The gate is conditional, so the frontend must not assume either.
    return {"site_id": site, "items": items, "enforced": bool(items)}


@router.get("/hod/site-config/work-types",
            summary="Work types and their WBS mapping (HOD manager)")
async def work_types_all(site_id: Optional[str] = Query(None),
                         user: dict = Depends(require_roles("hod")),
                         session: AsyncSession = Depends(get_session)):
    site = resolve_site_param(user, site_id)
    stmt = select(wt_t)
    if site_filter_applies(site):
        stmt = stmt.where(wt_t.c["Site_ID"] == site)
    rows = (await session.execute(
        stmt.order_by(wt_t.c["status"], wt_t.c["Work_Type"]))).mappings().all()
    items = []
    for r in rows:
        d = dict(r)
        items.append({k: v for k, v in d.items()
                      if k not in ("created_at", "updated_at")}
                     | {"created_at": str(d.get("created_at") or ""),
                        "updated_at": str(d.get("updated_at") or "")})
    return {"items": items}


@router.get("/hod/site-config/work-types/suggestions",
            summary="Work types the ledger has seen here, merged and counted")
async def work_type_suggestions(site_id: Optional[str] = Query(None),
                                user: dict = Depends(require_roles("hod")),
                                session: AsyncSession = Depends(get_session)):
    """The bootstrap for an empty list — see `wbs.usage_suggestions`. Nothing is
    seeded by migration on purpose; adopting a spelling is a decision."""
    site = resolve_site_param(user, site_id)
    if not site:
        raise HTTPException(422, "site_id required")
    return {"site_id": site,
            "items": await wbs_svc.usage_suggestions(session, site_id=site)}


@router.post("/hod/site-config/work-types", status_code=201,
             summary="Add a work type, optionally mapped to a WBS (HOD)")
async def work_type_add(body: WorkTypeIn,
                        user: dict = Depends(require_roles("hod")),
                        session: AsyncSession = Depends(get_session)):
    site = resolve_site_param(user, body.site_id)
    if not site:
        raise HTTPException(422, "site_id required")
    name = (body.Work_Type or "").strip()
    if not name:
        raise HTTPException(422, "Work Type cannot be empty")
    if wbs_svc.is_reserved(name):
        raise HTTPException(
            422, f"{name!r} is a system marker written by the app itself, not a "
                 f"work type. It cannot be added to the list or given a WBS.")
    norm = wbs_svc.normalise(name)
    dup = (await session.execute(select(wt_t.c["id"], wt_t.c["Work_Type"]).where(
        (wt_t.c["Site_ID"] == site) & (wt_t.c["Work_Type_Norm"] == norm)))).first()
    if dup:
        raise HTTPException(
            409, f"{site} already lists this work type as {dup.Work_Type!r} — "
                 f"the two differ only in spacing or case")
    wbs_no = await _checked_wbs(session, site, body.WBS_Number)
    wid = (await session.execute(insert(wt_t).values(
        Site_ID=site, Work_Type=name, Work_Type_Norm=norm, WBS_Number=wbs_no,
        Description=(body.Description or "").strip() or None, status="active",
        created_by=user["username"]).returning(wt_t.c["id"]))).scalar_one()
    await write_audit(session, user["username"], "WORK_TYPE_ADD",
                      "wbs_work_type_map",
                      f"{name}@{site} wbs={wbs_no or '-'}")
    await session.commit()
    return {"id": wid, "Work_Type": name, "Work_Type_Norm": norm,
            "WBS_Number": wbs_no, "Site_ID": site}


@router.patch("/hod/site-config/work-types/{wid}",
              summary="Rename, re-map or retire a work type (HOD)")
async def work_type_patch(wid: int, body: WorkTypePatch,
                          user: dict = Depends(require_roles("hod")),
                          session: AsyncSession = Depends(get_session)):
    row = (await session.execute(select(wt_t).where(wt_t.c["id"] == wid))
           ).mappings().first()
    if row is None:
        raise HTTPException(404, "no such work type")
    if not site_row_visible(site_scope(user), row["Site_ID"]):
        raise HTTPException(403, "that work type belongs to another site")

    vals: dict = {}
    if body.Work_Type is not None:
        name = body.Work_Type.strip()
        if not name:
            raise HTTPException(422, "Work Type cannot be empty")
        if wbs_svc.is_reserved(name):
            raise HTTPException(422, f"{name!r} is a system marker, not a work type")
        norm = wbs_svc.normalise(name)
        if norm != row["Work_Type_Norm"]:
            clash = (await session.execute(select(wt_t.c["Work_Type"]).where(
                (wt_t.c["Site_ID"] == row["Site_ID"])
                & (wt_t.c["Work_Type_Norm"] == norm)
                & (wt_t.c["id"] != wid)))).first()
            if clash:
                raise HTTPException(
                    409, f"{row['Site_ID']} already lists {clash.Work_Type!r}, "
                         f"which is the same work type spelled differently")
        vals["Work_Type"], vals["Work_Type_Norm"] = name, norm
    if body.WBS_Number is not None:
        # '' is meaningful and different from absent: it CLEARS the mapping.
        vals["WBS_Number"] = await _checked_wbs(session, row["Site_ID"],
                                                body.WBS_Number)
    if body.Description is not None:
        vals["Description"] = body.Description.strip() or None
    if body.status is not None:
        if body.status not in ("active", "retired"):
            raise HTTPException(422, "status must be 'active' or 'retired'")
        vals["status"] = body.status
    if not vals:
        raise HTTPException(422, "nothing to change")

    vals["updated_at"] = _dt.datetime.now(_dt.timezone.utc).replace(tzinfo=None)
    await session.execute(update(wt_t).where(wt_t.c["id"] == wid).values(**vals))
    await write_audit(session, user["username"], "WORK_TYPE_PATCH",
                      "wbs_work_type_map",
                      f"#{wid} " + ", ".join(f"{k}={v}" for k, v in vals.items()
                                             if k != "updated_at"))
    await session.commit()
    return {"id": wid, **{k: v for k, v in vals.items() if k != "updated_at"}}


@router.delete("/hod/site-config/work-types/{wid}",
               summary="Remove a work type from the list (HOD)")
async def work_type_delete(wid: int, user: dict = Depends(require_roles("hod")),
                           session: AsyncSession = Depends(get_session)):
    """⚠️ DELETING THE OPTION DOES NOT TOUCH THE LEDGER. Rows already posted
    keep the work type they were posted with — they record what happened, and
    an option removed today cannot un-happen last month's issue. Retiring
    (`status='retired'`) is usually the better move and is what the UI offers
    first; delete exists for a row added by mistake."""
    row = (await session.execute(select(wt_t.c["Site_ID"], wt_t.c["Work_Type"])
           .where(wt_t.c["id"] == wid))).first()
    if row is None:
        raise HTTPException(404, "no such work type")
    if not site_row_visible(site_scope(user), row.Site_ID):
        raise HTTPException(403, "that work type belongs to another site")
    await session.execute(delete(wt_t).where(wt_t.c["id"] == wid))
    await write_audit(session, user["username"], "WORK_TYPE_DELETE",
                      "wbs_work_type_map", f"{row.Work_Type}@{row.Site_ID}")
    await session.commit()
    return {"deleted": wid}


async def _checked_wbs(session: AsyncSession, site: str,
                       number: str | None) -> str | None:
    """A mapping may only point at a WBS that exists and is ACTIVE at this site.

    Without this the screen would happily accept a typo and the resolver would
    stamp it onto every issue of that work type — a wrong cost centre is far
    harder to notice than a missing one, because the report still balances.
    """
    n = str(number or "").strip()
    if not n:
        return None
    ok = (await session.execute(select(wbs_t.c["id"]).where(
        (wbs_t.c["WBS_Number"] == n) & (wbs_t.c["Site_ID"] == site)
        & (wbs_t.c["status"] == "active")))).first()
    if not ok:
        raise HTTPException(
            422, f"WBS {n!r} is not an active WBS number at {site} — add it "
                 f"under WBS Numbers first, or reopen it if it was closed")
    return n
