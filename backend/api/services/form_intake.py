"""
backend/api/services/form_intake.py — Phase 9d: a photographed form becomes a draft.

Between `ai/ocr_form.read_form()` (what the camera saw) and
`execution.open_entry()` (a row a supervisor can edit) sit four checks that all
have the same shape: refuse a sheet we cannot map, rather than map it wrongly.

    1. IS THIS A GI FORM?         the QR decodes, or the upload is refused
    2. IS IT THIS SITE'S?         a scoped user cannot file another site's paper
    3. HAS IT BEEN FILED ALREADY? `Form_UUID` is consumed exactly once
    4. IS THE PAPER STILL VALID?  `Recipe_Fingerprint` still matches

⚠️ CHECK 4 IS THE ONE PEOPLE WILL WANT TO SOFTEN, AND IT IS THE ONE THAT MATTERS
MOST. Row 3 of the handwriting maps to row 3 of the recipe. If a material was
added, removed or reordered after the sheet was printed, everything past that
point lands on the wrong material — and the result LOOKS FINE: plausible
quantities, against real materials, in a real system. There is no downstream
check that would catch it. Refusing a stale sheet is the only place this can be
stopped, so it refuses.

⚠️ THE ENTRY IS CREATED AT `DRAFT_SUPERVISOR`, NOT SUBMITTED. Extraction is the
machine's opinion; the supervisor reviews every figure and supplies the two
mandatory reasons before anything moves. Creating it already-submitted would
mean the model's reading of a digit could reach an approver untouched.
"""
from __future__ import annotations

import datetime as _dt
import json
import re
from typing import Optional

from fastapi import HTTPException
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..ai import handwritten as _hw
from ..ai import ocr_form as OF
from . import consumption_form as CF
from . import execution as X
from .ledger import _MD

form_t = _MD.tables["sme_consumption_form"]
entry_t = _MD.tables["sme_execution_entry"]
mat_t = _MD.tables["sme_execution_entry_material"]

_DATE_RX = [
    (re.compile(r"^(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{4})$"), None),
    (re.compile(r"^(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{2})$"), 2000),
]
# Handwritten digits the model most often mistakes for letters.
_DIGIT_FIX = str.maketrans({"l": "1", "I": "1", "O": "0", "o": "0", "S": "5"})


def parse_work_date(text: str, *, today: Optional[_dt.date] = None
                    ) -> tuple[Optional[str], Optional[str]]:
    """(iso_date, problem). DD/MM only — never swapped to MM/DD.

    ⚠️ NEVER GUESSED. An unreadable date returns None and the supervisor types
    it; a guessed one posts the work to the wrong day, and the day is what
    decides which progress row and which shift the consumption lands on. The
    ±1-year window catches a mis-read year rather than accepting 2062.

    Same DD/MM rule as `ai/handwritten.parse_form_date`, deliberately: two
    date parsers in one system that disagree about 03/04 is a bug waiting for
    the fourth of March. It now shares that function's shift-stripping too —
    a crew that writes `27/08/26 (Night)` in the box must not have its whole
    page refused for saying which shift it was. Read the shift itself with
    `ai/handwritten.parse_shift`.
    """
    today = today or _dt.date.today()
    s = _hw.strip_shift(str(text or "").strip()).translate(_DIGIT_FIX)
    for rx, century in _DATE_RX:
        m = rx.match(s)
        if not m:
            continue
        dd, mm, yy = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if century:
            yy += century
        try:
            d = _dt.date(yy, mm, dd)
        except ValueError:
            return None, f"{text!r} is not a real date"
        if not (today.year - 1 <= d.year <= today.year + 1):
            return None, f"{text!r} is outside the last year"
        if d > today + _dt.timedelta(days=1):
            return None, f"{text!r} is in the future"
        return d.isoformat(), None
    return None, (f"{text!r} could not be read as a date" if s
                  else "the date box was blank")


async def validate_sheet(session: AsyncSession, read: dict, *,
                         site_id: str) -> dict:
    """The four checks. Returns the registry row; raises with a plain reason.

    Every refusal names the fix, because the person holding the phone is
    standing in a plant and "invalid form" tells them nothing they can act on.
    """
    uuid = read["form_uuid"]
    reg = (await session.execute(select(form_t)
           .where(form_t.c["Form_UUID"] == uuid))).mappings().first()
    if reg is None:
        raise HTTPException(
            422, f"form {uuid} is not one this system printed. Download a fresh "
                 f"form from Execution Entries and use that — a photocopy or a "
                 f"hand-drawn sheet cannot be matched to a system's materials.")

    if reg["Site_ID"] != site_id:
        raise HTTPException(
            403, f"that form was printed for {reg['Site_ID']}, not {site_id}. "
                 f"Consumption is filed against the site the material left.")

    if reg["status"] == "consumed":
        raise HTTPException(
            409, f"form {uuid} has already been filed as entry "
                 f"{reg['consumed_entry_id']}. Each printed sheet is used once "
                 f"— if you meant to record a second day's work, print a new "
                 f"form.")
    if reg["status"] == "void":
        raise HTTPException(422, f"form {uuid} was voided and cannot be filed.")

    # ⚠️ CHECK 4 — see the module docstring for why this refuses rather than warns.
    rows = await CF.recipe_rows(session, code=reg["Lining_System_Code"],
                                esc=reg["Execution_Sub_Activity_Code"] or None)
    if CF.fingerprint(rows) != reg["Recipe_Fingerprint"]:
        raise HTTPException(
            409,
            f"the materials for {reg['Lining_System_Code']} have changed since "
            f"this form was printed, so the printed rows no longer line up with "
            f"the system's materials. Your quantities would be filed against "
            f"the wrong ones. Print a fresh form and copy the figures across.")
    return dict(reg) | {"_rows": rows}


def _match_rows(read_rows: list[dict], recipe: list[dict]) -> list[dict]:
    """Handwriting → materials, by printed row number.

    ⚠️ POSITIONAL, AND ONLY SAFE BECAUSE THE FINGERPRINT WAS CHECKED FIRST. A
    row number the form never printed is DROPPED rather than appended: a model
    that hallucinates row 7 on a six-row form must not be able to invent a
    seventh material.
    """
    by_row = {}
    for r in read_rows:
        n = int(r.get("row") or 0)
        if 1 <= n <= len(recipe):
            by_row.setdefault(n, r)      # first wins; a duplicate row is noise
    out = []
    for i, rec in enumerate(recipe):
        got = by_row.get(i + 1, {})
        out.append({
            "Row_Index": i,
            "Material_Code": rec["Material_Code"],
            "SAP_Code": rec.get("SAP_Code") or "",
            "UOM": rec.get("UOM"),
            "OCR_Qty": got.get("quantity"),
            "OCR_Qty_Text": got.get("qty_text") or "",
            "OCR_Lot_Text": got.get("lot_text") or "",
            # ⚠️ A NULL READING BECOMES A ZERO ON THE DRAFT, NOT A GUESS. The
            # supervisor sees the grey OCR column empty beside it and the raw
            # text they wrote, and types the real figure. Seeding the draft with
            # the model's uncertain number is how an unread digit becomes an
            # approved quantity.
            "Actual_Qty": float(got.get("quantity") or 0.0),
            "Lot_No": (got.get("lot_text") or "").strip() or None,
            "struck_through": bool(got.get("struck_through")),
        })
    return out


async def build_entry(session: AsyncSession, read: dict, *, site_id: str,
                      username: str, role: str, image_bytes: bytes,
                      job_id: Optional[int] = None) -> dict:
    """Validated sheet + extraction → a DRAFT_SUPERVISOR entry to review."""
    reg = await validate_sheet(session, read, site_id=site_id)
    recipe = reg.pop("_rows")
    lines = _match_rows(read.get("rows") or [], recipe)

    work_date, date_problem = parse_work_date(read.get("work_date_text") or "")
    equipment = (read.get("equipment_text") or "").strip()
    # ⚠️ THE SHIFT COMES FROM THE PAPER OR IT DOES NOT COME AT ALL (Q13,
    # 2026-09-02). Crews write `(Night)` beside the date; that is a statement by
    # the people who did the work, and ruling P10-9 objects to INFERRING a shift
    # from a filing timestamp, not to reading one somebody wrote down. No
    # marker → None → the column stays NULL, exactly as P10-9 requires.
    shift = _hw.parse_shift(read.get("work_date_text"))

    opened = await X.open_entry(
        session, username=username, role=role, site_id=site_id,
        # An unreadable date defaults to TODAY and is flagged — the supervisor
        # must confirm it. A null would fail the NOT NULL column; a silent
        # yesterday would be a lie.
        work_date=work_date or _dt.date.today().isoformat(),
        # Equipment is never fuzzy-matched to the master (see the plan's edge
        # case 5): a wrong tag posts area to the wrong vessel. Whatever the
        # model read is offered as text and the supervisor picks from a list.
        equipment_tag=equipment or "(unread — pick the equipment)",
        shift=shift,
        code=reg["Lining_System_Code"],
        esc=reg["Execution_Sub_Activity_Code"] or _first_esc(recipe),
        materials=lines, origin="ocr", form_uuid=reg["Form_UUID"])

    await session.execute(update(entry_t).where(entry_t.c["id"] == opened["id"])
                          .values(OCR_Job_ID=job_id, OCR_Image=image_bytes,
                                  OCR_Image_Mime="image/jpeg",
                                  OCR_Raw_JSON=json.dumps(
                                      {k: v for k, v in read.items()
                                       if k != "raw"}, ensure_ascii=False,
                                      default=str)[:60000],
                                  OCR_Model=read.get("model"),
                                  Actual_SQM=read.get("area_sqm")))
    # ⚠️ THE SHEET IS CONSUMED HERE, not at approval. A second photo of the same
    # paper must be refused while the first is still a draft — otherwise two
    # drafts of one sheet race to become two consumptions.
    await session.execute(update(form_t).where(form_t.c["id"] == reg["id"])
                          .values(status="consumed",
                                  consumed_entry_id=opened["id"],
                                  consumed_at=_dt.datetime.now(
                                      _dt.timezone.utc).replace(tzinfo=None)))

    problems = []
    if date_problem:
        problems.append(f"Date: {date_problem} — confirm it before submitting.")
    if not equipment:
        problems.append("The equipment box could not be read — pick it from "
                        "the list.")
    if read.get("area_sqm") is None:
        problems.append(f"Area: {read.get('area_text') or 'blank'} could not be "
                        f"read as a number — type it in.")
    unread = [ln["Row_Index"] + 1 for ln in lines
              if ln["OCR_Qty"] is None and ln["OCR_Qty_Text"]]
    if unread:
        problems.append(
            f"Row(s) {', '.join(map(str, unread))}: the handwriting was read "
            f"but the number was not certain. Check them against the photo.")
    struck = [ln["Row_Index"] + 1 for ln in lines if ln["struck_through"]]
    if struck:
        problems.append(f"Row(s) {', '.join(map(str, struck))} look crossed "
                        f"out — set them to 0 if that is right.")

    return {"entry_id": opened["id"], "Entry_No": opened["Entry_No"],
            "status": opened["status"], "Form_UUID": reg["Form_UUID"],
            "lines": len(lines), "problems": problems, "shift": shift,
            "model": read.get("model"), "provider": read.get("provider"),
            "work_date": work_date, "equipment_text": equipment,
            "area_sqm": read.get("area_sqm")}


def _first_esc(recipe: list[dict]) -> str:
    for r in recipe:
        if r.get("Execution_Sub_Activity_Code"):
            return str(r["Execution_Sub_Activity_Code"])
    return ""
