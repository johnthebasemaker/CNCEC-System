"""
backend/api/services/execution.py — Phase 5: the consumption workflow.

    DRAFT_SK ─┐
              ├─→ PENDING_SUPERVISOR ─→ PENDING_HOD ─→ APPROVED
    (bypass) ─┘                                     └─→ REJECTED

Three people, three different pieces of knowledge, and none of them holds all
of it:

  * the STORE KEEPER knows what physically left the store — materials and lots,
    against an equipment tag. They do not know what area it covered.
  * the SUPERVISOR knows the sub-activity, the area actually done, and who did
    it. They must NOT be able to edit the material lines: the store keeper
    counted those, and a supervisor whose numbers look bad has both the motive
    and the opportunity to adjust the consumption they are being measured
    against.
  * the HOD can edit BOTH, and pays for it with a mandatory justification and a
    notification back to the supervisor. An approval that silently rewrote the
    figures would leave the supervisor answering for numbers they never typed.

⚠️ THE MANPOWER-ONLY BYPASS IS A SECOND FRONT DOOR, NOT AN EXCEPTION.
Blasting and buffing consume no Surface Shield, so there is no material for a
store keeper to record and no draft for them to raise. A supervisor opens those
entries directly at PENDING_SUPERVISOR. Modelling it as "an SK draft with zero
material lines" would put a store keeper's signature on a step nobody
performed, and their queue would fill with drafts they cannot action.

⚠️ APPROVAL IS WHAT MOVES STOCK. Nothing before PENDING_HOD → APPROVED deducts
a quantity, which is what makes an HOD edit safe: they are correcting a
proposal, not reversing a posting.
"""
from __future__ import annotations

import datetime as _dt
from typing import Optional

from fastapi import HTTPException
from sqlalchemy import delete, func, insert, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from .ledger import _MD, write_audit
from .notifications import dispatch

entry_t = _MD.tables["sme_execution_entry"]
mat_t = _MD.tables["sme_execution_entry_material"]
man_t = _MD.tables["sme_execution_entry_manpower"]
norm_t = _MD.tables["sme_manpower_norm"]
recipe_t = _MD.tables["sme_recipe"]

DRAFT_SK = "DRAFT_SK"
PENDING_SUPERVISOR = "PENDING_SUPERVISOR"
PENDING_HOD = "PENDING_HOD"
APPROVED = "APPROVED"
REJECTED = "REJECTED"

# Every legal move. Written as data so an illegal one is a lookup miss with a
# readable message, not an `if` somebody forgets to add.
TRANSITIONS = {
    DRAFT_SK: {PENDING_SUPERVISOR},
    PENDING_SUPERVISOR: {PENDING_HOD},
    PENDING_HOD: {APPROVED, REJECTED},
    APPROVED: set(),
    REJECTED: set(),
}


def _now() -> _dt.datetime:
    return _dt.datetime.now(_dt.timezone.utc).replace(tzinfo=None)


def assert_transition(current: str, target: str) -> None:
    if target not in TRANSITIONS.get(current, set()):
        raise HTTPException(409, f"an entry in {current} cannot move to "
                                 f"{target} (allowed: "
                                 f"{sorted(TRANSITIONS.get(current, set())) or 'nothing — it is final'})")


async def is_manpower_only(session: AsyncSession, code: str, esc: str) -> bool:
    """True when no Surface Shield recipe exists for this system + sub-activity.

    Computed, never stored: it is a fact ABOUT the recipe table and would go
    stale the moment a recipe line is added. A system-agnostic entry (code '')
    can never have a recipe, so it is manpower-only by construction.
    """
    if not (code or "").strip():
        return True
    n = (await session.execute(
        select(func.count()).select_from(recipe_t)
        .where(recipe_t.c["Lining_System_Code"] == code,
               recipe_t.c["Execution_Sub_Activity_Code"] == esc))).scalar_one()
    return n == 0


async def next_entry_no(session: AsyncSession, site_id: str) -> str:
    """EXE-YYYYMMDD-N, site-scoped.

    Scoped to the site on purpose: an unscoped sequence is a date plus a small
    integer, which lets anyone holding one number enumerate every other site's
    entries by guessing neighbours.
    """
    day = _dt.date.today().strftime("%Y%m%d")
    prefix = f"EXE-{day}-"
    n = (await session.execute(
        select(func.count()).select_from(entry_t)
        .where(entry_t.c["Site_ID"] == site_id,
               entry_t.c["Entry_No"].like(prefix + "%")))).scalar_one()
    return f"{prefix}{int(n) + 1}"


def compute_variance(entry: dict, materials: list[dict],
                     manpower: list[dict]) -> dict:
    """Actual vs the SNAPSHOTTED benchmark. Never re-reads master data.

    Material — each line's expectation is `Bench_For_1_SQM x Actual_SQM`.
    Manpower — the benchmark is stated per shift, so:
        shifts   = Actual_SQM / Bench_Productivity_Per_Shift
        expected = shifts x Bench_Manhours_Per_Shift
    Actual man-hours are `headcount x hours` summed, kept unmultiplied in the
    table so a corrected headcount cannot silently carry the old hours.

    A benchmark of zero yields `None`, not infinity and not 0%: "we cannot
    compare this" and "this matched perfectly" must never render the same.
    """
    sqm = float(entry.get("Actual_SQM") or 0.0)

    mat_lines, mat_exp_total, mat_act_total = [], 0.0, 0.0
    for m in materials:
        per = m.get("Bench_For_1_SQM")
        act = float(m.get("Actual_Qty") or 0.0)
        exp = (float(per) * sqm) if per is not None else None
        pct = (round((act - exp) / exp * 100.0, 2)
               if exp not in (None, 0) else None)
        mat_lines.append({
            "Material_Code": m.get("Material_Code"),
            "SAP_Code": m.get("SAP_Code"),
            "UOM": m.get("UOM"),
            "Actual_Qty": round(act, 4),
            "Benchmark_Qty": None if exp is None else round(exp, 4),
            "Variance_Qty": None if exp is None else round(act - exp, 4),
            "Variance_Pct": pct,
        })
        if exp is not None:
            mat_exp_total += exp
            mat_act_total += act

    prod = entry.get("Bench_Productivity_Per_Shift")
    mh_shift = entry.get("Bench_Manhours_Per_Shift")
    exp_mh = None
    if prod not in (None, 0) and mh_shift is not None:
        exp_mh = (sqm / float(prod)) * float(mh_shift)
    act_mh = sum(float(r.get("Headcount") or 0.0) * float(r.get("Hours") or 0.0)
                 for r in manpower)
    act_heads = sum(float(r.get("Headcount") or 0.0) for r in manpower)

    return {
        "materials": mat_lines,
        "material_total": {
            "Actual": round(mat_act_total, 4),
            "Benchmark": round(mat_exp_total, 4),
            "Variance": round(mat_act_total - mat_exp_total, 4),
            "Variance_Pct": (round((mat_act_total - mat_exp_total)
                                   / mat_exp_total * 100.0, 2)
                             if mat_exp_total else None),
        },
        "manpower": {
            "Actual_Manhours": round(act_mh, 2),
            "Actual_Headcount": round(act_heads, 2),
            "Benchmark_Manhours": None if exp_mh is None else round(exp_mh, 2),
            "Benchmark_Crew_Size": entry.get("Bench_Crew_Size"),
            "Variance_Manhours": (None if exp_mh is None
                                  else round(act_mh - exp_mh, 2)),
            "Variance_Pct": (round((act_mh - exp_mh) / exp_mh * 100.0, 2)
                             if exp_mh else None),
        },
    }


async def _lines(session: AsyncSession, entry_id: int) -> tuple[list, list]:
    mats = [dict(r) for r in (await session.execute(
        select(mat_t).where(mat_t.c["Entry_ID"] == entry_id)
        .order_by(mat_t.c["Material_Code"]))).mappings().all()]
    mans = [dict(r) for r in (await session.execute(
        select(man_t).where(man_t.c["Entry_ID"] == entry_id)
        .order_by(man_t.c["Role_Code"]))).mappings().all()]
    return mats, mans


async def get_entry(session: AsyncSession, entry_id: int,
                    site_id: Optional[str]) -> dict:
    stmt = select(entry_t).where(entry_t.c["id"] == entry_id)
    if site_id is not None:
        stmt = stmt.where(entry_t.c["Site_ID"] == site_id)
    row = (await session.execute(stmt)).mappings().first()
    if row is None:
        raise HTTPException(404, "execution entry not found")
    entry = dict(row)
    mats, mans = await _lines(session, entry_id)
    entry["materials"] = mats
    entry["manpower"] = mans
    entry["variance"] = compute_variance(entry, mats, mans)
    return entry


# ─── SK: open a draft, or a supervisor opens one directly ────────────────────
async def open_entry(session: AsyncSession, *, username: str, role: str,
                     site_id: str, work_date: str, equipment_tag: str,
                     code: str, esc: str, variant: str = "",
                     materials: list[dict] | None = None) -> dict:
    """Create an entry.

    A store keeper creates it at DRAFT_SK with the material lines. A supervisor
    may create it directly at PENDING_SUPERVISOR — but ONLY for a
    manpower-only activity, because an activity that consumes Surface Shield
    has a physical draw that a store keeper has to have counted.
    """
    code = (code or "").strip()
    esc = (esc or "").strip()
    if not esc:
        raise HTTPException(422, "Execution_Sub_Activity_Code is required")
    if not (equipment_tag or "").strip():
        raise HTTPException(422, "Equipment_Tag_No is required")

    manpower_only = await is_manpower_only(session, code, esc)
    if role == "supervisor":
        if not manpower_only:
            raise HTTPException(
                409, f"{esc} consumes materials, so a store keeper has to "
                     f"record the physical draw first. The direct route is "
                     f"only for labour-only activities such as blasting.")
        status, sk_user, sk_at = PENDING_SUPERVISOR, None, None
    else:
        status, sk_user, sk_at = DRAFT_SK, username, _now()

    entry_no = await next_entry_no(session, site_id)
    new_id = (await session.execute(insert(entry_t).values(
        Site_ID=site_id, Entry_No=entry_no, Work_Date=str(work_date)[:10],
        Equipment_Tag_No=equipment_tag.strip(), Lining_System_Code=code,
        Execution_Sub_Activity_Code=esc, Variant_Key=(variant or "").strip(),
        status=status, sk_username=sk_user, sk_submitted_at=sk_at,
        created_by=username).returning(entry_t.c["id"]))).scalar_one()

    for m in (materials or []):
        await session.execute(insert(mat_t).values(
            Entry_ID=new_id, Material_Code=str(m["Material_Code"]).strip(),
            SAP_Code=str(m.get("SAP_Code") or "").strip(),
            Actual_Qty=float(m.get("Actual_Qty") or 0.0),
            Original_Qty=float(m.get("Actual_Qty") or 0.0),
            UOM=m.get("UOM"), Lot_No=m.get("Lot_No")))

    await write_audit(session, username, "SME_EXEC_OPEN", "sme_execution_entry",
                      f"{entry_no} {equipment_tag}/{code or '(none)'}/{esc} "
                      f"status={status}")
    if status == DRAFT_SK:
        await dispatch(session, event_key="sme_exec_drafted",
                       title="Execution entry raised",
                       body=f"{entry_no}: {equipment_tag} {esc} awaiting your "
                            f"area and crew figures.",
                       recipient_role="supervisor", recipient_site=site_id,
                       link_page="/supervisor", related_table="sme_execution_entry",
                       related_ref=str(new_id), created_by=username)
    return {"id": new_id, "Entry_No": entry_no, "status": status,
            "manpower_only": manpower_only}


async def sk_submit(session: AsyncSession, *, username: str, entry_id: int,
                    site_id: Optional[str]) -> dict:
    entry = await get_entry(session, entry_id, site_id)
    assert_transition(entry["status"], PENDING_SUPERVISOR)
    if not entry["materials"]:
        raise HTTPException(422, "record at least one material line, or open "
                                 "the entry as a labour-only activity")
    await session.execute(update(entry_t).where(entry_t.c["id"] == entry_id)
                          .values(status=PENDING_SUPERVISOR,
                                  sk_username=entry.get("sk_username") or username,
                                  sk_submitted_at=_now(), updated_at=_now()))
    await write_audit(session, username, "SME_EXEC_SK_SUBMIT",
                      "sme_execution_entry", entry["Entry_No"])
    await dispatch(session, event_key="sme_exec_to_supervisor",
                   title="Execution entry ready for your figures",
                   body=f"{entry['Entry_No']}: {entry['Equipment_Tag_No']} "
                        f"{entry['Execution_Sub_Activity_Code']}.",
                   recipient_role="supervisor",
                   recipient_site=entry["Site_ID"], link_page="/supervisor",
                   related_table="sme_execution_entry",
                   related_ref=str(entry_id), created_by=username)
    return {"id": entry_id, "status": PENDING_SUPERVISOR}


async def supervisor_submit(session: AsyncSession, *, username: str,
                            entry_id: int, site_id: Optional[str],
                            actual_sqm: float, manpower: list[dict],
                            material_reason: str, manpower_reason: str,
                            esc: Optional[str] = None,
                            variant: Optional[str] = None) -> dict:
    """Report the area and the crew, and snapshot the benchmark.

    ⚠️ BOTH REASONS ARE MANDATORY, whatever the variance (operator ruling). A
    reason demanded only past a threshold trains people to aim just under it,
    and a zero-variance entry carrying a stated reason is evidence the
    supervisor actually looked at the comparison.

    ⚠️ The supervisor may change the SUB-ACTIVITY but never the material lines.
    They are measured against that consumption; letting them edit it would let
    a bad number be tidied by the person it reflects on.
    """
    entry = await get_entry(session, entry_id, site_id)
    assert_transition(entry["status"], PENDING_HOD)
    if float(actual_sqm or 0) <= 0:
        raise HTTPException(422, "actual SQM must be greater than zero")
    if not (material_reason or "").strip():
        raise HTTPException(422, "a material variance reason is required on "
                                 "every entry, including a zero variance")
    if not (manpower_reason or "").strip():
        raise HTTPException(422, "a manpower variance reason is required on "
                                 "every entry, including a zero variance")
    if not manpower:
        raise HTTPException(422, "record the crew that did the work")

    esc = (esc or entry["Execution_Sub_Activity_Code"]).strip()
    variant = (variant if variant is not None
               else entry.get("Variant_Key") or "").strip()
    code = entry["Lining_System_Code"]

    # ── the benchmark snapshot ──────────────────────────────────────────────
    norm = (await session.execute(
        select(norm_t).where(
            norm_t.c["Lining_System_Code"] == code,
            norm_t.c["Execution_Sub_Activity_Code"] == esc,
            norm_t.c["Variant_Key"] == variant))).mappings().first()
    if norm is None:
        # Fall back to the sub-activity alone: a system-agnostic entry stores
        # code '' while the blasting benchmark is filed under the workbook's
        # 'ESC1'. Matching on the sub-activity is what bridges the two.
        norm = (await session.execute(
            select(norm_t).where(
                norm_t.c["Execution_Sub_Activity_Code"] == esc,
                norm_t.c["Variant_Key"] == variant)
            .order_by(norm_t.c["id"]).limit(1))).mappings().first()

    snap = {}
    if norm is not None:
        snap = {
            "Norm_ID": norm["id"],
            "Bench_Crew_Size": norm["Crew_Size"],
            "Bench_Hours_Per_Shift": norm["Hours_Per_Shift"],
            "Bench_Manhours_Per_Shift": norm["Manhours_Per_Shift"],
            "Bench_Productivity_Per_Shift": norm["Standard_Productivity_Per_Shift"],
            "Bench_SQM_Per_Hour_Per_Person": norm["SQM_Per_Hour_Per_Person"],
        }
    # Snapshot the recipe side too, per material line.
    for m in entry["materials"]:
        per = (await session.execute(
            select(recipe_t.c["For_1_SQM"]).where(
                recipe_t.c["Lining_System_Code"] == code,
                recipe_t.c["Execution_Sub_Activity_Code"] == esc,
                recipe_t.c["Material_Code"] == m["Material_Code"],
                recipe_t.c["SAP_Code"] == (m["SAP_Code"] or None)))).scalar()
        if per is None:
            per = (await session.execute(
                select(recipe_t.c["For_1_SQM"]).where(
                    recipe_t.c["Lining_System_Code"] == code,
                    recipe_t.c["Execution_Sub_Activity_Code"] == esc,
                    recipe_t.c["Material_Code"] == m["Material_Code"])
                .limit(1))).scalar()
        await session.execute(update(mat_t).where(mat_t.c["id"] == m["id"])
                              .values(Bench_For_1_SQM=per))

    await session.execute(delete(man_t).where(man_t.c["Entry_ID"] == entry_id))
    bench_crew = {}
    if norm is not None:
        bench_crew = {str(r[0]): float(r[1]) for r in (await session.execute(
            select(_MD.tables["sme_manpower_norm_role"].c["Role_Code"],
                   _MD.tables["sme_manpower_norm_role"].c["Headcount"])
            .where(_MD.tables["sme_manpower_norm_role"].c["Norm_ID"]
                   == norm["id"]))).all()}
    for r in manpower:
        rc = str(r["Role_Code"]).strip()
        head = float(r.get("Headcount") or 0.0)
        hours = float(r.get("Hours") or 0.0)
        await session.execute(insert(man_t).values(
            Entry_ID=entry_id, Role_Code=rc, Headcount=head, Hours=hours,
            Bench_Headcount=bench_crew.get(rc),
            Original_Headcount=head, Original_Hours=hours))

    await session.execute(update(entry_t).where(entry_t.c["id"] == entry_id)
                          .values(status=PENDING_HOD, Actual_SQM=float(actual_sqm),
                                  Execution_Sub_Activity_Code=esc,
                                  Variant_Key=variant,
                                  supervisor_username=username,
                                  supervisor_submitted_at=_now(),
                                  Material_Variance_Reason=material_reason.strip(),
                                  Manpower_Variance_Reason=manpower_reason.strip(),
                                  Bench_Snapshot_At=_now(), updated_at=_now(),
                                  **snap))
    await write_audit(session, username, "SME_EXEC_SUPERVISOR_SUBMIT",
                      "sme_execution_entry",
                      f"{entry['Entry_No']} sqm={actual_sqm} "
                      f"norm={snap.get('Norm_ID')}")
    await dispatch(session, event_key="sme_exec_to_hod",
                   title="Execution entry awaiting approval",
                   body=f"{entry['Entry_No']}: {entry['Equipment_Tag_No']} "
                        f"{esc}, {actual_sqm} m² reported.",
                   recipient_role="hod", recipient_site=entry["Site_ID"],
                   link_page="/hod", related_table="sme_execution_entry",
                   related_ref=str(entry_id), created_by=username)
    return await get_entry(session, entry_id, site_id)


# ─── HOD: approve (optionally correcting), or reject ─────────────────────────
async def hod_decide(session: AsyncSession, *, username: str, entry_id: int,
                     site_id: Optional[str], approve: bool,
                     reject_reason: str = "",
                     edits: dict | None = None,
                     justification: str = "") -> dict:
    """Approve or reject, with the HOD's power to correct either side.

    ⚠️ AN EDIT COSTS A JUSTIFICATION AND A NOTIFICATION. The HOD may change the
    store keeper's material quantities and the supervisor's area and crew — but
    the moment any number differs, `justification` is required and the
    supervisor is told what changed. Without that the supervisor is left
    answering for figures they never entered, and the variance report shows a
    number with no author.

    Original_* columns keep what was first written. An audit line saying "a
    quantity changed" without saying from what is not an audit trail.
    """
    entry = await get_entry(session, entry_id, site_id)
    assert_transition(entry["status"], APPROVED if approve else REJECTED)

    if not approve:
        if not (reject_reason or "").strip():
            raise HTTPException(422, "a rejection needs a reason — the "
                                     "supervisor has to know what to fix")
        await session.execute(update(entry_t).where(entry_t.c["id"] == entry_id)
                              .values(status=REJECTED, hod_username=username,
                                      hod_decided_at=_now(),
                                      Reject_Reason=reject_reason.strip(),
                                      updated_at=_now()))
        await write_audit(session, username, "SME_EXEC_REJECT",
                          "sme_execution_entry", entry["Entry_No"])
        await dispatch(session, event_key="sme_exec_rejected",
                       title="Execution entry rejected", severity="warning",
                       body=f"{entry['Entry_No']}: {reject_reason.strip()[:180]}",
                       recipient_user=entry.get("supervisor_username"),
                       recipient_role=None if entry.get("supervisor_username")
                       else "supervisor",
                       recipient_site=entry["Site_ID"], link_page="/supervisor",
                       related_table="sme_execution_entry",
                       related_ref=str(entry_id), created_by=username)
        return await get_entry(session, entry_id, site_id)

    # ── apply the edits, recording what actually changed ────────────────────
    edits = edits or {}
    changed: list[str] = []

    if "Actual_SQM" in edits and edits["Actual_SQM"] is not None:
        new_sqm = float(edits["Actual_SQM"])
        if abs(new_sqm - float(entry.get("Actual_SQM") or 0)) > 1e-9:
            changed.append(f"SQM {entry.get('Actual_SQM')} → {new_sqm}")
            await session.execute(update(entry_t)
                                  .where(entry_t.c["id"] == entry_id)
                                  .values(Actual_SQM=new_sqm))

    for m in (edits.get("materials") or []):
        row = next((x for x in entry["materials"] if x["id"] == m.get("id")), None)
        if row is None:
            raise HTTPException(422, f"material line {m.get('id')} is not on "
                                     f"this entry")
        new_q = float(m.get("Actual_Qty"))
        if abs(new_q - float(row["Actual_Qty"] or 0)) > 1e-9:
            changed.append(f"{row['Material_Code']} {row['Actual_Qty']} → {new_q}")
            await session.execute(update(mat_t).where(mat_t.c["id"] == row["id"])
                                  .values(Actual_Qty=new_q))

    for r in (edits.get("manpower") or []):
        row = next((x for x in entry["manpower"] if x["id"] == r.get("id")), None)
        if row is None:
            raise HTTPException(422, f"crew line {r.get('id')} is not on this entry")
        vals, bits = {}, []
        if r.get("Headcount") is not None and \
                abs(float(r["Headcount"]) - float(row["Headcount"] or 0)) > 1e-9:
            vals["Headcount"] = float(r["Headcount"])
            bits.append(f"{row['Headcount']} → {r['Headcount']} head")
        if r.get("Hours") is not None and \
                abs(float(r["Hours"]) - float(row["Hours"] or 0)) > 1e-9:
            vals["Hours"] = float(r["Hours"])
            bits.append(f"{row['Hours']} → {r['Hours']} h")
        if vals:
            changed.append(f"{row['Role_Code']} " + ", ".join(bits))
            await session.execute(update(man_t).where(man_t.c["id"] == row["id"])
                                  .values(**vals))

    if changed and not (justification or "").strip():
        raise HTTPException(
            422, "you changed " + "; ".join(changed[:3])
                 + (" and more" if len(changed) > 3 else "")
                 + " — a justification is required, and the supervisor is "
                   "notified of it")

    await session.execute(update(entry_t).where(entry_t.c["id"] == entry_id)
                          .values(status=APPROVED, hod_username=username,
                                  hod_decided_at=_now(),
                                  hod_edited=bool(changed),
                                  HOD_Edit_Justification=(justification.strip()
                                                          if changed else None),
                                  updated_at=_now()))
    await write_audit(session, username, "SME_EXEC_APPROVE",
                      "sme_execution_entry",
                      f"{entry['Entry_No']}"
                      + (f" edited: {'; '.join(changed)}" if changed else ""))

    if changed:
        # The supervisor is told WHAT changed and WHY, not merely that it did.
        await dispatch(session, event_key="sme_exec_hod_edited",
                       title="Your execution entry was corrected",
                       severity="warning",
                       body=f"{entry['Entry_No']}: {'; '.join(changed[:3])}"
                            + (" …" if len(changed) > 3 else "")
                            + f" — {justification.strip()[:140]}",
                       recipient_user=entry.get("supervisor_username"),
                       recipient_role=None if entry.get("supervisor_username")
                       else "supervisor",
                       recipient_site=entry["Site_ID"], link_page="/supervisor",
                       related_table="sme_execution_entry",
                       related_ref=str(entry_id), created_by=username)
    return await get_entry(session, entry_id, site_id)


async def list_entries(session: AsyncSession, *, site_id: Optional[str],
                       statuses: list[str] | None = None,
                       limit: int = 200) -> list[dict]:
    stmt = select(entry_t)
    if site_id is not None:
        stmt = stmt.where(entry_t.c["Site_ID"] == site_id)
    if statuses:
        stmt = stmt.where(entry_t.c["status"].in_(statuses))
    rows = [dict(r) for r in (await session.execute(
        stmt.order_by(entry_t.c["id"].desc()).limit(limit))).mappings().all()]
    if not rows:
        return []
    ids = [r["id"] for r in rows]
    mats: dict[int, list] = {}
    for m in (await session.execute(
            select(mat_t).where(mat_t.c["Entry_ID"].in_(ids)))).mappings().all():
        mats.setdefault(m["Entry_ID"], []).append(dict(m))
    mans: dict[int, list] = {}
    for m in (await session.execute(
            select(man_t).where(man_t.c["Entry_ID"].in_(ids)))).mappings().all():
        mans.setdefault(m["Entry_ID"], []).append(dict(m))
    for r in rows:
        r["materials"] = mats.get(r["id"], [])
        r["manpower"] = mans.get(r["id"], [])
        r["variance"] = compute_variance(r, r["materials"], r["manpower"])
    return rows
