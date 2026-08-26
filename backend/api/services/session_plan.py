"""
backend/api/services/session_plan.py — the SME session, costed in labour.

Phase 8 slice 8e. Answers the question the two modules could not answer
separately: *given the material actually on site, how much of this session's
work can we START, how much is the whole job, and how much is waiting?*

────────────────────────────────────────────────────────────────────────────
THE COMPOSITION, AND WHY THERE IS NO NEW ENGINE

`sme_engine.build_sqm_rollup()` already emits, per (tag, code):

    Remaining_SQM         the whole job, materials no object
    SQM_Achievable_Now    what the PHYSICAL stock supports — strict bottleneck
    SQM_Deficit           remaining − achievable, i.e. what procurement owes

and `services/planner.py` already turns an area into man-hours. So this module
is a JOIN, not a model. It adds no arithmetic that a second engine would have
to mirror, which matters: `codeStats` in session.ts has no Python twin, and an
official manpower document whose numbers came from a browser is exactly what
the SME Canon rejects.

⚠️ THE THREE COLUMNS ARE ONE NUMBER, SPLIT. Because

    SQM_Achievable_Now + SQM_Deficit == Remaining_SQM        (by definition)

and man-hours are LINEAR in area — every benchmark contributes
`share x manhours_per_sqm` and the shares are fixed by the selection, not by
how much area is left — the identity survives the multiplication:

    can_do_manhours + blocked_manhours == overall_manhours

That conservation is the property worth gating on, and suite CI does. It is
also why the module computes a per-job MAN-HOURS PER SQUARE METRE and applies
it three times, rather than costing three plans and hoping they reconcile.

⚠️ THE BLOCKED COLUMN CARRIES NO HEADCOUNT. Labour you cannot deploy because
the material has not landed is not a hiring requirement. Printing a headcount
beside it invites somebody to hire for it, and they would be idle on arrival.
It shows the SIZE OF THE DELAY — man-hours and crew-shifts — and the materials
responsible. The per-role gap is likewise measured against CAN-DO only, for
the same reason.

⚠️ THE CASCADE IS THE HEAVIEST READ IN THE CODEBASE, and none of it depends on
the deadline. Everything the deadline does not touch — the cascade, the
rollup, the benchmark selection, the per-m² coefficients — is computed once per
(site, order, codes) and cached for ~60 s (operator ruling Q8), so dragging
Target Days re-runs only the division. The roster is deliberately NOT cached:
it is one cheap grouped query, and a stale headcount is a worse answer than a
slow one.

⚠️ A CACHE ON A NUMBER PEOPLE DECIDE FROM MUST SAY IT IS A CACHE. Every
response carries `cascade.computed_at`, `cached` and `age_seconds`, and
`refresh=true` forces a recompute. A silent 60-second window is how a store
keeper's just-posted receipt turns into an argument about whose screen is
right.
"""
from __future__ import annotations

import math
import time
from typing import Optional

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import sme_engine
from ..manhours import ot_thresholds
from .jobs import job_label, system_names
from .ledger import _MD
from .planner import (SHIFT_WORKED_HOURS, _activity_to_codes, _all_norms,
                      _lining_codes, _plan_one, crew_shifts, manhours_per_sqm,
                      roster, unmapped_warning, shift_split)

norm_role_t = _MD.tables["sme_manpower_norm_role"]

# Column key → the field it fills on a per-job / per-role row. Spelled out
# because building these names with an f-string is how "Blocked_Manhours"
# quietly becomes "blocked_Manhours" and a column reads zero.
_COL_FIELD = {"can_do": "Can_Do_Manhours", "overall": "Overall_Manhours",
              "blocked": "Blocked_Manhours"}

# Operator ruling Q8. Long enough that dragging Target Days never re-cascades,
# short enough that a receipt posted while you are reading shows up on the next
# question you ask.
CASCADE_TTL_SECONDS = 60.0

# A guard, not a tuning knob: the cache is keyed by (site, order, codes) and a
# user exploring subsets could otherwise mint an entry per permutation. Expired
# entries are dropped first, so this only ever bites on genuinely live keys.
_CACHE_MAX_ENTRIES = 32

_cache: dict[tuple, tuple[float, dict]] = {}


def invalidate_cache() -> None:
    """Drop every cached selection. Used by the service suite, which mutates
    stock and asks again inside the TTL — the one caller for whom 'you asked
    within a minute' is not the situation the cache was built for."""
    _cache.clear()


def _cache_get(key: tuple) -> Optional[tuple[float, dict]]:
    now = time.monotonic()
    for k, (stamp, _) in list(_cache.items()):
        if now - stamp > CASCADE_TTL_SECONDS:
            _cache.pop(k, None)
    hit = _cache.get(key)
    return hit if hit is not None else None


def _cache_put(key: tuple, value: dict) -> float:
    if len(_cache) >= _CACHE_MAX_ENTRIES:
        oldest = min(_cache, key=lambda k: _cache[k][0])
        _cache.pop(oldest, None)
    stamp = time.monotonic()
    _cache[key] = (stamp, value)
    return stamp


# ── the deadline-independent half ────────────────────────────────────────────
async def _build_selection(session: AsyncSession, *, site_id: str,
                           order: list[str], codes: list[str]) -> dict:
    """Cascade → rollup → benchmark selection → man-hours PER SQUARE METRE.

    Nothing in here reads the deadline, the shift count or the roster, which is
    what makes it cacheable and what makes a Target Days change cost a
    division rather than a cascade.
    """
    from ..sme import _snapshot_rows

    warnings: list[str] = []
    snap = await _snapshot_rows(session, site_id)
    model = sme_engine.build_model(snap["equipment"], snap["recipes"],
                                   snap["materials"], snap["progress"])
    lines = sme_engine.cascade_allocate(model, order)
    rollup = sme_engine.build_sqm_rollup(model, lines, order)

    known = set(model["codes_by_tag"])
    missing = [t for t in order if t not in known]
    if missing:
        warnings.append(
            f"{len(missing)} tag(s) in the session are not in this site's "
            f"equipment master and were dropped rather than planned as empty: "
            + ", ".join(missing[:8])
            + (f" (+{len(missing) - 8} more)" if len(missing) > 8 else ""))

    wanted = {c.strip() for c in codes if c and c.strip()}
    units = [r for r in rollup
             if (not wanted or r["Lining_System_Code"] in wanted)
             and r["Remaining_SQM"] > 0]
    if wanted:
        found = {r["Lining_System_Code"] for r in units}
        absent = sorted(wanted - found)
        if absent:
            warnings.append(
                "no outstanding work on this session's equipment for: "
                + ", ".join(absent[:8]))
    if not units and not warnings:
        warnings.append(
            "every system on this session's equipment is already complete — "
            "there is no remaining area to cost")

    # Lines belonging to the units being planned, indexed for the bottleneck
    # and blocking-material passes below.
    by_unit: dict[tuple, list[dict]] = {}
    for ln in lines:
        by_unit.setdefault((ln["Equipment_Tag_No"], ln["Lining_System_Code"]),
                           []).append(ln)

    all_norms = await _all_norms(session)
    lining_codes = await _lining_codes(session)
    activity_to_codes = _activity_to_codes(all_norms, lining_codes)
    names = await system_names(session)

    planned: list[tuple[dict, dict]] = []      # (rollup row, _plan_one result)
    for u in units:
        p = await _plan_one(session, site_id=site_id,
                            tag=u["Equipment_Tag_No"],
                            code=u["Lining_System_Code"],
                            all_norms=all_norms, lining_codes=lining_codes,
                            activity_to_codes=activity_to_codes)
        planned.append((u, p))

    # ONE crew query for every benchmark across every job.
    norm_ids = sorted({int(n["id"]) for _, p in planned for n, _, _, _ in p["items"]})
    crew_by_norm: dict[int, dict] = {}
    if norm_ids:
        for nid, rc, head in (await session.execute(
                select(norm_role_t.c["Norm_ID"], norm_role_t.c["Role_Code"],
                       norm_role_t.c["Headcount"])
                .where(norm_role_t.c["Norm_ID"].in_(norm_ids)))).all():
            crew_by_norm.setdefault(int(nid), {})[str(rc)] = float(head or 0)

    jobs: list[dict] = []
    for u, p in planned:
        tag, code = u["Equipment_Tag_No"], u["Lining_System_Code"]
        label = job_label(tag, code, names, p.get("eq_type", ""))
        for w in p["warnings"]:
            warnings.append(f"{label['Short']}: {w}")

        # ⚠️ mh_per_sqm is the whole point. Each benchmark covers `share` of the
        # job's area at its own rate, so the job's rate is the share-weighted
        # sum — and it is INDEPENDENT of how much area is left. That is what
        # lets one coefficient cost all three columns and keeps them reconciled.
        mh_per_sqm = 0.0
        cs_per_sqm = 0.0
        role_per_sqm: dict[str, float] = {}
        for n, sqm, share, _src in p["items"]:
            rate = manhours_per_sqm(n)
            if rate is None:
                warnings.append(
                    f"{label['Short']}: {n['Execution_Sub_Activity_Code']} has "
                    f"no usable productivity figure — excluded from the "
                    f"requirement rather than counted as free")
                continue
            mh_per_sqm += share * rate
            cs = crew_shifts(n, share)          # share of 1 m², so: per m²
            if cs is not None:
                cs_per_sqm += cs
            crew = crew_by_norm.get(int(n["id"]), {})
            total_head = sum(crew.values())
            if total_head > 0:
                for rc, head in crew.items():
                    role_per_sqm[rc] = role_per_sqm.get(rc, 0.0) + \
                        share * rate * (head / total_head)
            else:
                warnings.append(
                    f"{label['Short']}: {n['Execution_Sub_Activity_Code']} has "
                    f"no crew composition, so its hours cannot be attributed "
                    f"to a role")

        # UNMODELLED IS NOT BLOCKED. `_achievable` scores a unit with no
        # positive-rate recipe line at 0 (SME ruling Q5), which arrives here as
        # "100% blocked". Saying so is the difference between "procurement owes
        # you material" and "nobody has written this recipe yet".
        if not u["Has_Recipe"]:
            warnings.append(
                f"{label['Short']}: no recipe line exists for this system, so "
                f"the SME engine scores it 0 m² achievable. That is "
                f"UNMODELLED, not blocked by stock — the hours below sit in "
                f"the blocked column because nothing says what it consumes")

        # The planner reads `sme_sqm_progress` directly; the SME engine folds
        # `Done_SQM_staged` in as well. They normally agree, and when they do
        # not the SME figure wins here (the three columns must come from ONE
        # arithmetic) — but a reader is told, because it means somebody has
        # staged production that is not yet approved.
        planner_rem = float(p["workload"]["remaining_sqm"])
        if abs(planner_rem - u["Remaining_SQM"]) > 0.01:
            warnings.append(
                f"{label['Short']}: the planner reads {planner_rem:g} m² "
                f"outstanding and the SME engine {u['Remaining_SQM']:g} m² — "
                f"the difference is production staged but not yet approved. "
                f"This report uses the SME figure")

        ulines = by_unit.get((tag, code), [])
        bottleneck = _bottleneck(ulines)
        jobs.append({
            "Equipment_Tag_No": tag,
            "Lining_System_Code": code,
            "Job_Label": label["Short"],
            "Job": label,
            "Name": u["Name"],
            "System_Name": u["System_Name"] or label["System_Name"],
            "Priority_Rank": order.index(tag) + 1 if tag in order else None,
            "Overall_SQM": u["Remaining_SQM"],
            "Can_Do_SQM": u["SQM_Achievable_Now"],
            "Blocked_SQM": u["SQM_Deficit"],
            "SQM_Achievable_With_Ordered": u["SQM_Achievable_With_Ordered"],
            "Coverage_Now_Pct": u["Coverage_Now_Pct"],
            "Coverage_With_Ordered_Pct": u["Coverage_With_Ordered_Pct"],
            "Has_Recipe": u["Has_Recipe"],
            "Manhours_Per_SQM": mh_per_sqm,
            "Crew_Shifts_Per_SQM": cs_per_sqm,
            "Role_Manhours_Per_SQM": role_per_sqm,
            "Bottleneck": bottleneck,
        })

    return {"jobs": jobs, "warnings": warnings,
            "materials": _blocking_materials(jobs, by_unit),
            "order_used": [t for t in order if t in known]}


def _bottleneck(ulines: list[dict]) -> Optional[dict]:
    """The material that decides this unit's achievable area.

    Mirrors `sme_engine._achievable`: the unit reaches the MINIMUM of
    `Alloc_Available / For_1_SQM` across its recipe, so the line holding that
    minimum is the one to buy. Zero-rate lines impose no ceiling and are
    excluded, exactly as the engine excludes them.
    """
    rated = [ln for ln in ulines if ln["For_1_SQM"] > 0]
    if not rated:
        return None
    worst = min(rated, key=lambda ln: ln["Alloc_Available"] / ln["For_1_SQM"])
    if worst["Shortfall_Available_Qty"] <= 0:
        return None
    return {"Material_Code": worst["Material_Code"],
            "SAP_Code": worst["SAP_Code"],
            "Material_Name": worst["Material_Name"],
            "UOM": worst["UOM"],
            "Shortfall_Available_Qty": worst["Shortfall_Available_Qty"],
            "Shortfall_Qty": worst["Shortfall_Qty"]}


def _blocking_materials(jobs: list[dict], by_unit: dict) -> list[dict]:
    """Every material short against the planned units, worst first.

    ⚠️ NO MAN-HOUR FIGURE IS ATTRIBUTED TO A MATERIAL. Several materials can be
    short on one unit while only the scarcest decides its achievable area, so
    "this material blocks N man-hours" would be an invented number that sums to
    more than the delay. What is true and useful is stated instead: how much is
    missing, how much survives the open purchase orders, and which jobs it
    stands in front of — with the bottleneck ones marked.
    """
    agg: dict[str, dict] = {}
    for j in jobs:
        key_unit = (j["Equipment_Tag_No"], j["Lining_System_Code"])
        bn = j.get("Bottleneck") or {}
        bn_key = (bn.get("Material_Code"), bn.get("SAP_Code"))
        for ln in by_unit.get(key_unit, []):
            if ln["Shortfall_Available_Qty"] <= 0:
                continue
            m = agg.setdefault(ln["Material_Key"], {
                "Material_Code": ln["Material_Code"],
                "SAP_Code": ln["SAP_Code"],
                "Material_Name": ln["Material_Name"],
                "UOM": ln["UOM"],
                "Short_Now_Qty": 0.0, "Short_Net_Qty": 0.0,
                "Blocks_Jobs": [], "Is_Bottleneck_For": []})
            m["Short_Now_Qty"] += ln["Shortfall_Available_Qty"]
            m["Short_Net_Qty"] += ln["Shortfall_Qty"]
            m["Blocks_Jobs"].append(j["Job_Label"])
            if (ln["Material_Code"], ln["SAP_Code"]) == bn_key:
                m["Is_Bottleneck_For"].append(j["Job_Label"])
    out = []
    for m in agg.values():
        out.append({**m,
                    "Short_Now_Qty": round(m["Short_Now_Qty"], 4),
                    "Short_Net_Qty": round(m["Short_Net_Qty"], 4),
                    "Blocks_Job_Count": len(m["Blocks_Jobs"]),
                    "Bottleneck_Job_Count": len(m["Is_Bottleneck_For"])})
    # Bottlenecks first: they are what actually has to be bought to move a job.
    out.sort(key=lambda m: (-m["Bottleneck_Job_Count"], -m["Short_Net_Qty"],
                            m["Material_Code"], m["SAP_Code"]))
    return out


# ── the deadline-dependent half ──────────────────────────────────────────────
def _column(basis: str, sqm: float, mh: float, cs: float, *,
            deadline_hours: float, shifts_per_day: int, roster_in_scope: int,
            headcount: bool, shares: tuple[float, float, str] = (1.0, 0.0, "day_only")) -> dict:
    """One of the three columns.

    `headcount=False` is the BLOCKED column, and the nulls are the point: see
    the module docstring. The workload measures — man-hours and crew-shifts —
    are still given, because "how big is the delay" is exactly the question the
    blocked column exists to answer.
    """
    out = {
        "Basis": basis,
        "SQM": round(sqm, 2),
        "Manhours": round(mh, 2),
        "Crew_Shifts": round(cs, 3),
    }
    if not headcount:
        out.update({
            "Required_Headcount": None,
            "Required_Headcount_Rounded": None,
            "Required_Day_Headcount": None,
            "Required_Night_Headcount": None,
            "Shift_Split_Basis": None,
            "Headcount_Per_Shift": None,
            "Days_With_Current_Roster": None,
            "Headcount_Note": "no headcount is shown for blocked work: you "
                              "cannot deploy labour against material that has "
                              "not arrived, and a number here would be hired "
                              "against",
        })
        return out
    need = mh / deadline_hours if deadline_hours else 0.0
    d_share, n_share, basis_name = shares
    day_need = math.ceil(need * d_share - 1e-9)
    night_need = math.ceil(need * n_share - 1e-9)
    out.update({
        "Required_Headcount": round(need, 2),
        "Required_Headcount_Rounded": math.ceil(need - 1e-9),
        "Required_Day_Headcount": day_need,
        "Required_Night_Headcount": night_need,
        "Shift_Split_Basis": basis_name,
        # The larger crew, not the total halved — see `planner.shift_split`.
        "Headcount_Per_Shift": max(day_need, night_need),
        "Days_With_Current_Roster": round(
            mh / (roster_in_scope * SHIFT_WORKED_HOURS), 2)
        if roster_in_scope > 0 and mh > 0 else None,
        "Headcount_Note": "",
    })
    return out


async def plan_session(session: AsyncSession, *, site_id: str,
                       priority_order: list[str],
                       lining_system_codes: Optional[list[str]] = None,
                       deadline_hours: Optional[float] = None,
                       target_days: Optional[float] = None,
                       shifts_per_day: Optional[int] = None,
                       refresh: bool = False) -> dict:
    """The SME session, costed three ways: can do · overall · blocked.

    ⚠️ SURFACE PREP IS NOT IN THIS REPORT. Blasting consumes no recipe line, so
    the SME engine has no opinion on whether it is materially blocked — putting
    it in would place hours in a column whose whole meaning is "material
    decides this". The ordinary planner (`/mh/planner`) costs prep, and says so
    on the page.
    """
    order = [t for t in (str(x).strip() for x in priority_order) if t]
    if not order:
        raise HTTPException(422, "the session is empty — add equipment in the "
                                 "SME Session Builder first")
    codes = sorted({str(c).strip() for c in (lining_system_codes or [])
                    if str(c).strip()})

    if deadline_hours is not None and target_days is not None:
        raise HTTPException(422, "give either target_days or deadline_hours, "
                                 "not both — they are the same quantity")
    if target_days is not None:
        if float(target_days) <= 0:
            raise HTTPException(422, "target_days must be greater than zero")
        deadline_hours = float(target_days) * SHIFT_WORKED_HOURS
    if deadline_hours is None:
        deadline_hours = SHIFT_WORKED_HOURS
    deadline_hours = float(deadline_hours)
    if deadline_hours <= 0:
        raise HTTPException(422, "deadline_hours must be greater than zero")
    if shifts_per_day is not None and int(shifts_per_day) not in (1, 2):
        raise HTTPException(422, "shifts_per_day must be 1 (day only) or 2 "
                                 "(day and night)")
    days = deadline_hours / SHIFT_WORKED_HOURS

    # ── the cached, deadline-independent half ───────────────────────────────
    key = (site_id, tuple(order), tuple(codes))
    hit = None if refresh else _cache_get(key)
    if hit is None:
        bundle = await _build_selection(session, site_id=site_id, order=order,
                                        codes=codes)
        stamp = _cache_put(key, bundle)
        cached, age = False, 0.0
    else:
        stamp, bundle = hit
        cached, age = True, time.monotonic() - stamp

    jobs = bundle["jobs"]
    warnings = list(bundle["warnings"])

    # ── the roster, always live ─────────────────────────────────────────────
    thresholds = await ot_thresholds(session)
    available, unmapped = await roster(session, site_id=site_id)
    if unmapped:
        warnings.append(unmapped_warning(unmapped))

    roles = sorted({rc for j in jobs for rc in j["Role_Manhours_Per_SQM"]})
    night_in_scope = sum(int(available.get(rc, {}).get("Night", 0))
                         for rc in roles) if roles else \
        sum(int(v.get("Night", 0)) for v in available.values())
    auto_shifts = 2 if night_in_scope > 0 else 1
    shifts_per_day = int(shifts_per_day) if shifts_per_day else auto_shifts
    shift_source = "operator" if shifts_per_day != auto_shifts else "roster"
    if shifts_per_day == 2 and night_in_scope == 0:
        warnings.append(
            "a two-shift plan was requested but no active worker in the "
            "required roles is on the night shift — the split below is what "
            "you would have to staff, not what exists")
    if shifts_per_day == 2 and night_in_scope == 0:
        warnings[-1] = (
            "a two-shift plan was requested but no active worker in the "
            "required roles is on the night shift. There is no roster to "
            "derive a day/night proportion from, so the split below is an "
            "assumed even one")
    roster_in_scope = sum(int(available.get(rc, {}).get("total", 0))
                          for rc in roles)

    # ⚠️ THE SAME SPLIT RULE AS `planner.plan_many` (Phase 9b, ruling Q10), from
    # the same helper. Two planners reading one roster and disagreeing about how
    # it splits would be worse than either being wrong, because only one of them
    # would ever be checked.
    site_night = sum(int(v.get("Night", 0)) for v in available.values())
    site_day = max(sum(int(v.get("total", 0)) for v in available.values())
                   - site_night, 0)
    site_shares = shift_split({"total": roster_in_scope, "Night": night_in_scope},
                              site_day=site_day, site_night=site_night,
                              shifts_per_day=shifts_per_day)

    # ── the three columns ───────────────────────────────────────────────────
    tot = {"can_do": [0.0, 0.0, 0.0], "overall": [0.0, 0.0, 0.0],
           "blocked": [0.0, 0.0, 0.0]}       # [sqm, manhours, crew_shifts]
    role_mh: dict[str, dict[str, float]] = {}
    role_jobs: dict[str, dict[str, dict]] = {}
    job_rows: list[dict] = []
    for j in jobs:
        rate, cs_rate = j["Manhours_Per_SQM"], j["Crew_Shifts_Per_SQM"]
        areas = {"can_do": j["Can_Do_SQM"], "overall": j["Overall_SQM"],
                 "blocked": j["Blocked_SQM"]}
        for col, area in areas.items():
            tot[col][0] += area
            tot[col][1] += area * rate
            tot[col][2] += area * cs_rate
        for rc, per_sqm in j["Role_Manhours_Per_SQM"].items():
            slot = role_mh.setdefault(rc, {"can_do": 0.0, "overall": 0.0,
                                           "blocked": 0.0})
            jslot = role_jobs.setdefault(rc, {}).setdefault(
                j["Job_Label"], {"Job": j["Job_Label"], "Can_Do_Manhours": 0.0,
                                 "Overall_Manhours": 0.0,
                                 "Blocked_Manhours": 0.0})
            for col, area in areas.items():
                slot[col] += area * per_sqm
                jslot[_COL_FIELD[col]] += area * per_sqm
        job_rows.append({
            **{k: v for k, v in j.items()
               if k not in ("Role_Manhours_Per_SQM",)},
            "Manhours_Per_SQM": round(rate, 4),
            "Crew_Shifts_Per_SQM": round(cs_rate, 6),
            "Can_Do_Manhours": round(j["Can_Do_SQM"] * rate, 2),
            "Overall_Manhours": round(j["Overall_SQM"] * rate, 2),
            "Blocked_Manhours": round(j["Blocked_SQM"] * rate, 2),
        })

    columns = {
        "can_do": _column("SQM_Achievable_Now", *tot["can_do"],
                          deadline_hours=deadline_hours,
                          shifts_per_day=shifts_per_day, shares=site_shares,
                          roster_in_scope=roster_in_scope, headcount=True),
        "overall": _column("Remaining_SQM", *tot["overall"],
                           deadline_hours=deadline_hours,
                           shifts_per_day=shifts_per_day, shares=site_shares,
                           roster_in_scope=roster_in_scope, headcount=True),
        "blocked": _column("SQM_Deficit", *tot["blocked"],
                           deadline_hours=deadline_hours,
                           shifts_per_day=shifts_per_day, shares=site_shares,
                           roster_in_scope=roster_in_scope, headcount=False),
    }

    # ── per role ────────────────────────────────────────────────────────────
    by_role = []
    for rc in sorted(set(role_mh) | set(available)):
        mh = role_mh.get(rc, {"can_do": 0.0, "overall": 0.0, "blocked": 0.0})
        have = available.get(rc, {})
        have_total = int(have.get("total", 0))
        can_need = mh["can_do"] / deadline_hours if deadline_hours else 0.0
        all_need = mh["overall"] / deadline_hours if deadline_hours else 0.0
        by_role.append({
            "Role_Code": rc,
            "Can_Do_Manhours": round(mh["can_do"], 2),
            "Overall_Manhours": round(mh["overall"], 2),
            "Blocked_Manhours": round(mh["blocked"], 2),
            "Can_Do_Headcount": round(can_need, 2),
            "Can_Do_Headcount_Rounded": math.ceil(can_need - 1e-9),
            # Same rule as `planner.plan_many`: the roster decides the split.
            "Can_Do_Per_Shift": max(
                math.ceil(can_need * site_shares[0] - 1e-9),
                math.ceil(can_need * site_shares[1] - 1e-9)),
            "Can_Do_Day_Headcount": math.ceil(can_need * site_shares[0] - 1e-9),
            "Can_Do_Night_Headcount": math.ceil(can_need * site_shares[1] - 1e-9),
            "Overall_Headcount": round(all_need, 2),
            "Overall_Headcount_Rounded": math.ceil(all_need - 1e-9),
            # Deliberately absent, not zero — see the module docstring.
            "Blocked_Headcount": None,
            "Available_Headcount": have_total,
            "Available_GI": int(have.get("GI", 0)),
            "Available_NON_GI": int(have.get("NON_GI", 0)),
            "Available_Day": int(have.get("Day", 0)),
            "Available_Night": int(have.get("Night", 0)),
            # ⚠️ THE GAP IS MEASURED AGAINST CAN-DO. Hiring for the overall
            # figure staffs up for work whose material has not landed; the
            # overall column is shown beside it so the eventual need is
            # visible, but the actionable number is this one.
            "To_Assign": max(math.ceil(can_need - have_total - 1e-9), 0),
            "Jobs": sorted(
                ({**v, "Can_Do_Manhours": round(v["Can_Do_Manhours"], 2),
                  "Overall_Manhours": round(v["Overall_Manhours"], 2),
                  "Blocked_Manhours": round(v["Blocked_Manhours"], 2)}
                 for v in role_jobs.get(rc, {}).values()),
                key=lambda r: -r["Overall_Manhours"]),
        })

    job_rows.sort(key=lambda r: (r["Priority_Rank"] or 10**6,
                                 r["Lining_System_Code"]))
    n_gi = sum(int(v.get("GI", 0)) for v in available.values())
    n_ng = sum(int(v.get("NON_GI", 0)) for v in available.values())
    return {
        "inputs": {
            "site_id": site_id,
            "priority_order": order,
            "lining_system_codes": codes,
            "deadline_hours": deadline_hours,
            "target_days": round(days, 3),
            "shifts_per_day": shifts_per_day,
            "shifts_per_day_source": shift_source,
            "shift_worked_hours": SHIFT_WORKED_HOURS,
            "ot_thresholds": thresholds,
        },
        "cascade": {
            "cached": cached,
            "age_seconds": round(age, 1),
            "ttl_seconds": CASCADE_TTL_SECONDS,
            "order_used": bundle["order_used"],
            "jobs_costed": len(jobs),
        },
        "columns": columns,
        "jobs": job_rows,
        "by_role": by_role,
        "materials_blocking": bundle["materials"],
        "roster": {"GI": n_gi, "NON_GI": n_ng, "Total": n_gi + n_ng,
                   "In_Scope": roster_in_scope,
                   "Night_In_Scope": night_in_scope,
                   "Unmapped": unmapped},
        "warnings": warnings,
    }


# ── the document ─────────────────────────────────────────────────────────────
def session_report_sheets(plan: dict) -> list[tuple]:
    """`[(sheet_title, columns, rows)]` for the shared report renderers.

    ⚠️ NOTHING HERE FORMATS A CELL. Values go out as the numbers and strings
    they are, and `reports.to_csv` / `xlsx_style.xl_val` do the rule-12
    defusing on the way to the file. Pre-formatting here would both duplicate
    that and turn every quantity into text, which is the exact failure the
    defusing rules were written to avoid.

    The SUMMARY sheet leads, because the three-column answer is the report and
    the per-job detail is the evidence for it.
    """
    cols = plan["columns"]
    inputs = plan["inputs"]

    summary_cols = ["Column", "Basis", "SQM", "Man-hours", "Crew-shifts",
                    "Headcount for the target", "Day crew", "Night crew",
                    "Days at current roster", "Note"]

    def _srow(label: str, key: str) -> list:
        c = cols[key]
        return [label, c["Basis"], c["SQM"], c["Manhours"], c["Crew_Shifts"],
                c["Required_Headcount"], c["Required_Day_Headcount"],
                c["Required_Night_Headcount"],
                c["Days_With_Current_Roster"], c["Headcount_Note"]]

    summary = [_srow("We can do now", "can_do"),
               _srow("Overall total", "overall"),
               _srow("Blocked by material", "blocked")]

    job_cols = ["Priority", "Equipment", "System", "System name", "Job",
                "Can-do m²", "Overall m²", "Blocked m²", "Coverage now %",
                "Man-hours per m²", "Can-do man-hours", "Overall man-hours",
                "Blocked man-hours", "Bottleneck material", "Bottleneck short"]
    job_rows = []
    for j in plan["jobs"]:
        bn = j.get("Bottleneck") or {}
        job_rows.append([
            j["Priority_Rank"], j["Equipment_Tag_No"], j["Lining_System_Code"],
            j["System_Name"], j["Job_Label"], j["Can_Do_SQM"],
            j["Overall_SQM"], j["Blocked_SQM"], j["Coverage_Now_Pct"],
            j["Manhours_Per_SQM"], j["Can_Do_Manhours"], j["Overall_Manhours"],
            j["Blocked_Manhours"],
            f'{bn.get("Material_Code", "")} {bn.get("SAP_Code", "")}'.strip()
            or "—",
            bn.get("Shortfall_Available_Qty"),
        ])

    role_cols = ["Role", "Can-do man-hours", "Overall man-hours",
                 "Blocked man-hours", "Can-do headcount", "Day crew",
                 "Night crew", "Overall headcount", "Blocked headcount",
                 "On the roster", "To assign"]
    role_rows = [[r["Role_Code"], r["Can_Do_Manhours"], r["Overall_Manhours"],
                  r["Blocked_Manhours"], r["Can_Do_Headcount"],
                  r["Can_Do_Day_Headcount"], r["Can_Do_Night_Headcount"],
                  r["Overall_Headcount"],
                  # Not 0 and not blank-by-accident: the reason is in the cell.
                  "not applicable — material, not labour",
                  r["Available_Headcount"], r["To_Assign"]]
                 for r in plan["by_role"]]

    mat_cols = ["Material", "SAP", "Name", "UOM", "Short now", "Short net",
                "Jobs blocked", "Bottleneck for"]
    mat_rows = [[m["Material_Code"], m["SAP_Code"], m["Material_Name"],
                 m["UOM"], m["Short_Now_Qty"], m["Short_Net_Qty"],
                 m["Blocks_Job_Count"], m["Bottleneck_Job_Count"]]
                for m in plan["materials_blocking"]]

    # The run's own parameters, ridden along under the three rows so the CSV —
    # which can only carry sheet 0 — still says what was asked for. Padded to
    # the sheet's width: a short row is how a renderer that zips cells to
    # headers silently drops the tail.
    def _pad(row: list) -> list:
        return list(row) + [None] * (len(summary_cols) - len(row))

    head = [_pad(r) for r in (
        [], ["Report inputs"],
        ["Site", inputs["site_id"]],
        ["Target days", inputs["target_days"]],
        ["Hours per person", inputs["deadline_hours"]],
        ["Shifts per day", inputs["shifts_per_day"]],
        ["Session (priority order)", ", ".join(inputs["priority_order"])],
        ["System codes", ", ".join(inputs["lining_system_codes"]) or "all"],
        ["Jobs costed", plan["cascade"]["jobs_costed"]],
    )]

    sheets = [("Summary", summary_cols, summary + head),
              ("Per job", job_cols, job_rows),
              ("Per role", role_cols, role_rows),
              ("Blocking materials", mat_cols, mat_rows)]
    if plan["warnings"]:
        sheets.append(("Read this first", ["Warning"],
                       [[w] for w in plan["warnings"]]))
    return sheets
