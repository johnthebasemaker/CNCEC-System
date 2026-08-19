"""
backend/api/services/planner.py — Phase 7: the overtime-minimising planner.

Answers one question: *to finish this equipment by the deadline, how many of
each role do I need, how many do I have, and what should I hire?*

THE MODEL, stated once so the arithmetic can be checked:

    shifts        = deadline_hours / SHIFT_WORKED_HOURS      (may be fractional)
    per person    = threshold x shifts   NORMAL hours
                  + (11 - threshold) x shifts   OVERTIME hours
                  = 11 x shifts = deadline_hours             (they reconcile)

`deadline_hours` is therefore HOURS AVAILABLE PER PERSON in the window, which
is the reading that makes the operator's own formula
(`headcount = manhours / deadline_hours`) come out right.

⚠️ MINIMISING OVERTIME IS A CAPACITY QUESTION, NOT A PREFERENCE. Overtime is
whatever will not fit inside the workforce's NORMAL capacity, so the way to
reduce it is to raise that capacity. A non-GI worker absorbs 10 normal hours
against a GI worker's 8 — 25% more — which is why the recommendation prefers
them. It is arithmetic, not a policy about who to employ.

⚠️ THIS FUNCTION MUTATES NOTHING. It reads progress, benchmarks and the roster
and returns advice. The operator's ruling: a suggestion, never a forced
assignment.
"""
from __future__ import annotations

import math
from typing import Optional

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..manhours import ot_thresholds
from .ledger import _MD

norm_t = _MD.tables["sme_manpower_norm"]
norm_role_t = _MD.tables["sme_manpower_norm_role"]
roles_t = _MD.tables["mh_roles"]
emp_t = _MD.tables["mh_employees"]
eq_t = _MD.tables["sme_equipment"]
progress_t = _MD.tables["sme_sqm_progress"]
prep_t = _MD.tables["sme_surface_prep_progress"]

# The worked half of the 12-hour shift (11 worked + 1 hour lunch).
SHIFT_WORKED_HOURS = 11.0


def manhours_per_sqm(norm: dict) -> Optional[float]:
    """Man-hours needed per m² for one benchmark.

    ⚠️ DERIVED FROM THE EXACT FIGURES, not from the workbook's
    `SQ. Mtr/Hr./Person` column. That column is rounded to two decimals and the
    error is not academic: AR tile lining ships 0.13, while the exact
    99 man-hours / 13.33 m² per shift is 7.427 man-hours per m² against the
    rounded 7.692 — a 3.6% overstatement on every tile plan. The rounded column
    is used only when the exact pair is missing.
    """
    prod = float(norm.get("Standard_Productivity_Per_Shift") or 0)
    mh = float(norm.get("Manhours_Per_Shift") or 0)
    if prod > 0 and mh > 0:
        return mh / prod
    per_person = float(norm.get("SQM_Per_Hour_Per_Person") or 0)
    if per_person > 0:
        return 1.0 / per_person
    return None


async def _role_lookup(session: AsyncSession) -> dict:
    """Every spelling of a role → its canonical Role_Code.

    The roster types a free-text `Designation` while the benchmarks cite a
    `Role_Code`, so they have to be reconciled. Matching is case- and
    separator-insensitive on both the code and the printed name; anything that
    still does not match is REPORTED rather than silently counted as zero
    availability, because "no masons on site" and "nobody wrote down that they
    are masons" call for completely different actions.
    """
    out: dict[str, str] = {}
    for code, name in (await session.execute(
            select(roles_t.c["Role_Code"], roles_t.c["Name"]))).all():
        for spelling in (code, name):
            key = str(spelling or "").strip().lower().replace(" ", "_")
            if key:
                out[key] = str(code)
    return out


async def _remaining_sqm(session: AsyncSession, *, site_id: str, tag: str,
                         code: str) -> dict:
    """How much area is still to do, and where the figure came from."""
    if code:
        row = (await session.execute(select(progress_t).where(
            progress_t.c["Site_ID"] == site_id,
            progress_t.c["Equipment_Tag_No"] == tag,
            progress_t.c["Lining_System_Code"] == code))).mappings().first()
        if row is None:
            return {"remaining_sqm": 0.0, "original_sqm": 0.0, "done_sqm": 0.0,
                    "source": "sme_sqm_progress",
                    "note": f"no progress row for {tag} / {code} at this site"}
        orig = float(row["Original_SQM"] or 0)
        done = float(row["Done_SQM"] or 0)
        return {"remaining_sqm": round(max(orig - done, 0.0), 2),
                "original_sqm": round(orig, 2), "done_sqm": round(done, 2),
                "source": "sme_sqm_progress", "note": ""}

    # Surface prep has no planned area of its own — the area to prepare is the
    # equipment's, which sme_equipment already states. See
    # models.SmeSurfacePrepProgress for why there is no Original_SQM twin.
    area = float((await session.execute(
        select(func.coalesce(func.sum(eq_t.c["Surface_Area_SQM"]), 0.0))
        .where(eq_t.c["Site_ID"] == site_id,
               eq_t.c["Equipment_Tag_No"] == tag))).scalar() or 0)
    done = float((await session.execute(
        select(func.coalesce(func.sum(prep_t.c["Done_SQM"]), 0.0))
        .where(prep_t.c["Site_ID"] == site_id,
               prep_t.c["Equipment_Tag_No"] == tag))).scalar() or 0)
    return {"remaining_sqm": round(max(area - done, 0.0), 2),
            "original_sqm": round(area, 2), "done_sqm": round(done, 2),
            "source": "sme_equipment - sme_surface_prep_progress",
            "note": "" if area else f"no equipment area recorded for {tag}"}


async def _applicable_norms(session: AsyncSession, code: str) -> list[dict]:
    """The benchmarks that make up this job.

    For lining work, every sub-activity filed under the system: finishing it
    means doing ALL of them (primer AND screed AND buffing), so their man-hours
    add rather than compete.

    For surface prep (code ''), the SYSTEM-AGNOSTIC benchmarks.

    ⚠️ "System-agnostic" is decided by the DATA, not by how the code is spelt.
    This first read as `NOT LIKE 'LSC%'` — a naming convention, and wrong: any
    lining system whose code was not spelt `LSC…` would silently be planned as
    surface prep. A norm is system-agnostic when NO RECIPE LINE names its
    system, which is the same test `/execution/activities` uses for
    `manpower_only`. One definition, two callers.
    """
    if code:
        stmt = select(norm_t).where(norm_t.c["Lining_System_Code"] == code)
        return [dict(r) for r in (await session.execute(
            stmt.order_by(norm_t.c["Execution_Sub_Activity_Code"],
                          norm_t.c["Variant_Key"]))).mappings().all()]

    recipe_t = _MD.tables["sme_recipe"]
    lining_codes = {str(c) for (c,) in (await session.execute(
        select(recipe_t.c["Lining_System_Code"]).distinct())).all() if c}
    rows = [dict(r) for r in (await session.execute(
        select(norm_t).order_by(norm_t.c["Execution_Sub_Activity_Code"],
                                norm_t.c["Variant_Key"]))).mappings().all()]
    return [r for r in rows
            if str(r["Lining_System_Code"]) not in lining_codes]


async def plan(session: AsyncSession, *, site_id: str, equipment_tag: str,
               lining_system_code: str = "", deadline_hours: float = 11.0,
               ) -> dict:
    """The whole plan: workload → requirement → roster → gap → strategy."""
    tag = (equipment_tag or "").strip()
    code = (lining_system_code or "").strip()
    if not tag:
        raise HTTPException(422, "equipment_tag is required")
    if float(deadline_hours or 0) <= 0:
        raise HTTPException(422, "deadline_hours must be greater than zero")
    deadline_hours = float(deadline_hours)
    shifts = deadline_hours / SHIFT_WORKED_HOURS

    workload = await _remaining_sqm(session, site_id=site_id, tag=tag, code=code)
    remaining = workload["remaining_sqm"]
    norms = await _applicable_norms(session, code)
    warnings: list[str] = []
    if workload["note"]:
        warnings.append(workload["note"])
    if not norms:
        warnings.append(
            f"no manpower benchmark exists for "
            f"{code or 'system-agnostic surface prep'} — the requirement "
            f"cannot be computed, and a zero here means 'unknown', not 'none'")

    # ── 1. workload → required man-hours, per sub-activity ──────────────────
    norm_ids = [n["id"] for n in norms]
    crew_rows = (await session.execute(
        select(norm_role_t.c["Norm_ID"], norm_role_t.c["Role_Code"],
               norm_role_t.c["Headcount"])
        .where(norm_role_t.c["Norm_ID"].in_(norm_ids or [-1])))).all()
    crew_by_norm: dict[int, dict] = {}
    for nid, rc, head in crew_rows:
        crew_by_norm.setdefault(int(nid), {})[str(rc)] = float(head or 0)

    activities: list[dict] = []
    role_manhours: dict[str, float] = {}
    total_manhours = 0.0
    for n in norms:
        per_sqm = manhours_per_sqm(n)
        if per_sqm is None:
            warnings.append(
                f"{n['Execution_Sub_Activity_Code']} has no usable productivity "
                f"figure — excluded from the requirement rather than counted "
                f"as free")
            continue
        mh = remaining * per_sqm
        total_manhours += mh
        crew = crew_by_norm.get(int(n["id"]), {})
        crew_total = sum(crew.values())
        split = {}
        if crew_total > 0:
            # Distribute this activity's man-hours in the crew's own
            # proportions. Multiplying man-hours BY a headcount (the literal
            # reading) is dimensionally wrong — it yields person²·hours.
            for rc, head in crew.items():
                share = head / crew_total
                split[rc] = round(mh * share, 2)
                role_manhours[rc] = role_manhours.get(rc, 0.0) + mh * share
        else:
            warnings.append(
                f"{n['Execution_Sub_Activity_Code']} has no crew composition, "
                f"so its {round(mh, 2)} man-hours cannot be attributed to a "
                f"role")
        activities.append({
            "Execution_Sub_Activity_Code": n["Execution_Sub_Activity_Code"],
            "Activity": n["Activity"], "Sub_Activity": n["Sub_Activity"],
            "Variant_Key": n["Variant_Key"] or "",
            "Type": n["Type"],
            "Benchmark_Crew_Size": n["Crew_Size"],
            "Standard_Productivity_Per_Shift": n["Standard_Productivity_Per_Shift"],
            "Manhours_Per_SQM": round(per_sqm, 4),
            "Required_Manhours": round(mh, 2),
            "Required_Headcount": round(mh / deadline_hours, 2),
            "Role_Manhours": split,
        })

    # ── 2. the roster ───────────────────────────────────────────────────────
    lookup = await _role_lookup(session)
    thresholds = await ot_thresholds(session)
    emp_rows = (await session.execute(
        select(emp_t.c["Designation"], emp_t.c["Worker_Type"], emp_t.c["Shift"],
               func.count())
        .where(emp_t.c["Site_ID"] == site_id, emp_t.c["status"] == "active")
        .group_by(emp_t.c["Designation"], emp_t.c["Worker_Type"],
                  emp_t.c["Shift"]))).all()

    available: dict[str, dict] = {}
    unmapped: dict[str, int] = {}
    for desig, wtype, shift, cnt in emp_rows:
        key = str(desig or "").strip().lower().replace(" ", "_")
        rc = lookup.get(key)
        if rc is None:
            label = str(desig or "").strip() or "(no designation recorded)"
            unmapped[label] = unmapped.get(label, 0) + int(cnt)
            continue
        slot = available.setdefault(rc, {"GI": 0, "NON_GI": 0, "Day": 0,
                                         "Night": 0, "total": 0})
        wt = str(wtype or "GI")
        slot[wt] = slot.get(wt, 0) + int(cnt)
        slot[str(shift or "Day")] = slot.get(str(shift or "Day"), 0) + int(cnt)
        slot["total"] += int(cnt)
    if unmapped:
        warnings.append(
            "roster designations that match no role: "
            + ", ".join(f"{k} x{v}" for k, v in sorted(unmapped.items()))
            + " — these workers are NOT counted as available. 'nobody wrote "
              "down that they are masons' is a different problem from 'there "
              "are no masons', so they are reported rather than assumed")

    # ── 3. the gap, per role ────────────────────────────────────────────────
    gap_rows = []
    for rc in sorted(set(role_manhours) | set(available)):
        mh = role_manhours.get(rc, 0.0)
        need_exact = mh / deadline_hours
        have = available.get(rc, {})
        have_total = int(have.get("total", 0))
        gap = need_exact - have_total
        gap_rows.append({
            "Role_Code": rc,
            "Required_Manhours": round(mh, 2),
            "Required_Headcount": round(need_exact, 2),
            "Required_Headcount_Rounded": math.ceil(need_exact - 1e-9),
            "Available_Headcount": have_total,
            "Available_GI": int(have.get("GI", 0)),
            "Available_NON_GI": int(have.get("NON_GI", 0)),
            "Available_Day": int(have.get("Day", 0)),
            "Available_Night": int(have.get("Night", 0)),
            "Gap_Headcount": round(gap, 2),
            "To_Procure": max(math.ceil(gap - 1e-9), 0),
        })

    # ── 4. the overtime strategy ────────────────────────────────────────────
    gi_thr = float(thresholds.get("GI", 8.0))
    ng_thr = float(thresholds.get("NON_GI", 10.0))
    n_gi = sum(int(v.get("GI", 0)) for v in available.values())
    n_ng = sum(int(v.get("NON_GI", 0)) for v in available.values())

    normal_capacity = (n_gi * gi_thr + n_ng * ng_thr) * shifts
    ot_capacity = (n_gi * (SHIFT_WORKED_HOURS - gi_thr)
                   + n_ng * (SHIFT_WORKED_HOURS - ng_thr)) * shifts
    normal_used = min(total_manhours, normal_capacity)
    ot_used = min(max(total_manhours - normal_capacity, 0.0), ot_capacity)
    unmet = max(total_manhours - normal_capacity - ot_capacity, 0.0)
    overflow = max(total_manhours - normal_capacity, 0.0)

    # To ERASE the overtime you have to raise NORMAL capacity, and each hire
    # raises it by their own threshold. A non-GI worker brings 10 hours where a
    # GI worker brings 8, so fewer of them clear the same overflow — that is
    # the whole of the "prefer non-GI" advice, and it is arithmetic.
    ng_needed = math.ceil(overflow / (ng_thr * shifts) - 1e-9) if overflow > 0 and ng_thr > 0 else 0
    gi_needed = math.ceil(overflow / (gi_thr * shifts) - 1e-9) if overflow > 0 and gi_thr > 0 else 0

    return {
        "inputs": {
            "site_id": site_id, "equipment_tag": tag,
            "lining_system_code": code,
            "system_agnostic": not code,
            "deadline_hours": deadline_hours,
            "shifts_in_window": round(shifts, 3),
            "shift_worked_hours": SHIFT_WORKED_HOURS,
            "ot_thresholds": thresholds,
        },
        "workload": workload,
        "activities": activities,
        "requirement": {
            "Total_Required_Manhours": round(total_manhours, 2),
            "Total_Required_Headcount": round(
                total_manhours / deadline_hours, 2) if deadline_hours else None,
            "Activities": len(activities),
        },
        "roster": {
            "GI": n_gi, "NON_GI": n_ng, "Total": n_gi + n_ng,
            "Unmapped": unmapped,
        },
        "gap": gap_rows,
        "strategy": {
            "Normal_Capacity_Manhours": round(normal_capacity, 2),
            "Overtime_Capacity_Manhours": round(ot_capacity, 2),
            "Normal_Hours_Used": round(normal_used, 2),
            "Overtime_Hours_Incurred": round(ot_used, 2),
            "Unmet_Manhours": round(unmet, 2),
            "Feasible": unmet <= 1e-9,
            "Hire_NON_GI_To_Clear_Overtime": ng_needed,
            "Hire_GI_To_Clear_Overtime": gi_needed,
            "Recommendation": _recommend(total_manhours, normal_capacity,
                                         unmet, ng_needed, gi_needed,
                                         ng_thr, gi_thr),
        },
        "warnings": warnings,
    }


def _recommend(required: float, normal_capacity: float, unmet: float,
               ng: int, gi: int, ng_thr: float, gi_thr: float) -> str:
    if required <= 0:
        return ("Nothing to plan — there is no remaining area, or no benchmark "
                "to measure it with. Check the warnings before reading this "
                "as 'finished'.")
    if required <= normal_capacity:
        return (f"The current roster absorbs all {round(required)} man-hours "
                f"inside normal time. No overtime and no hiring needed.")
    parts = [
        f"{round(required - normal_capacity)} man-hours fall outside normal "
        f"capacity."]
    if ng:
        parts.append(
            f"Adding {ng} non-GI worker(s) clears it inside normal time — "
            f"each absorbs {ng_thr:g} h against a GI worker's {gi_thr:g} h, so "
            f"{gi} GI worker(s) would be needed for the same result.")
    if unmet > 0:
        parts.append(
            f"⚠️ {round(unmet)} man-hours cannot be covered even with everyone "
            f"on full overtime — the deadline is not reachable with this "
            f"workforce.")
    return " ".join(parts)
