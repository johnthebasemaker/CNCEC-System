"""
backend/api/services/mh_analytics.py — Phase 9e: how efficient was this job, day by day?

THE QUESTION THIS ANSWERS, in the operator's own words: for one lining system,
how much manpower went into Equipment A against Equipment B — normalised, so a
400 m² tank and a 40 m² vessel can be compared at all.

Man-hours per square metre IS that normalisation. It is the only figure on this
page that survives a change of size.

────────────────────────────────────────────────────────────────────────────
⚠️ THE DAILY RATIO IS UNSTABLE AND OFTEN UNDEFINED. THE CUMULATIVE ONE IS THE
COMPARISON (ruling Q11).

Mobilisation, scaffolding, curing and inspection all book HOURS against ZERO
square metres. Plot `hours / sqm` per day and those become a division by zero;
give them a large number instead and one bar dwarfs the whole chart. Neither
is a reading of anything.

`cum_hours / cum_sqm` has none of that. It converges, it is defined the moment
any area exists, and it is the number an HOD actually argues about — "this tank
is running at 3.1 man-hours a metre and that one at 1.9". The daily ratio is
still returned, as `null` where it cannot be computed, because it belongs in a
tooltip and because a caller that wants it should not have to re-derive it.

⚠️ AND THE CUMULATIVE LINE HAS A REAL GAP AT THE START. Before the first square
metre is recorded, `cum_sqm` is 0 and the ratio is undefined for everyone —
that is not an edge case to paper over, it is a week of mobilisation showing up
as what it was. It renders as a gap, and the point carries why.

⚠️ THE REASON IS READ, NOT INVENTED (ruling Q12). A day with hours and no area
carries whatever the timekeeper wrote in `Remarks`. There is no taxonomy of
"mobilisation / scaffolding / curing" in this database and inventing one here
would put a label on somebody's day that they never chose. Where nothing was
written the point says so — "no reason recorded" is itself actionable, and a
guess is not.
"""
from __future__ import annotations

import datetime as _dt
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .ledger import _MD

ts_t = _MD.tables["mh_timesheets"]
prod_t = _MD.tables["mh_production"]

# Above this, a day's man-hours-per-m² is almost certainly a data-entry error
# rather than a bad day: it means an entire crew produced a few centimetres.
# Advisory only — it colours a point, it never drops one.
_ABSURD_MH_PER_SQM = 200.0


def _key(tag: str, code: str) -> str:
    return f"{tag} · {code}" if code else str(tag)


async def daily_efficiency(session: AsyncSession, *, site_id: Optional[str],
                           system_code: Optional[str] = None,
                           equipment: Optional[list[str]] = None,
                           date_from: Optional[str] = None,
                           date_to: Optional[str] = None) -> dict:
    """One series per (equipment, system), one point per calendar day.

    ⚠️ EVERY DAY BETWEEN THE FIRST AND LAST OBSERVATION IS EMITTED, including
    the ones with nothing on them. A bar chart whose x-axis skips the quiet
    days compresses a fortnight of drift into what looks like a steady run —
    the gaps are the shape of the work and hiding them changes the reading.

    The window is the observed range, not the requested one: padding the edges
    with empty days says "we worked here and produced nothing", which is a
    different and false claim.
    """
    hours_q = select(
        ts_t.c["Work_Date"], ts_t.c["Equipment_Tag"], ts_t.c["System_Code"],
        func.sum(ts_t.c["Total_Hours"]).label("hours"),
        func.count().label("entries"),
    ).where(ts_t.c["Equipment_Tag"].is_not(None),
            ts_t.c["System_Code"].is_not(None)
            ).group_by(ts_t.c["Work_Date"], ts_t.c["Equipment_Tag"],
                       ts_t.c["System_Code"])
    sqm_q = select(
        prod_t.c["Work_Date"], prod_t.c["Equipment_Tag"], prod_t.c["System_Code"],
        func.sum(prod_t.c["SQM_Done"]).label("sqm"),
    ).group_by(prod_t.c["Work_Date"], prod_t.c["Equipment_Tag"],
               prod_t.c["System_Code"])
    # The remarks a timekeeper actually wrote — the only honest source for
    # "why was there no area today". Distinct, so a crew of twelve carrying the
    # same note does not print it twelve times.
    note_q = select(
        ts_t.c["Work_Date"], ts_t.c["Equipment_Tag"], ts_t.c["System_Code"],
        ts_t.c["Remarks"],
    ).where(ts_t.c["Remarks"].is_not(None), ts_t.c["Remarks"] != "").distinct()

    if site_id is not None:
        hours_q = hours_q.where(ts_t.c["Site_ID"] == site_id)
        sqm_q = sqm_q.where(prod_t.c["Site_ID"] == site_id)
        note_q = note_q.where(ts_t.c["Site_ID"] == site_id)
    if system_code:
        hours_q = hours_q.where(ts_t.c["System_Code"] == system_code)
        sqm_q = sqm_q.where(prod_t.c["System_Code"] == system_code)
        note_q = note_q.where(ts_t.c["System_Code"] == system_code)
    if equipment:
        hours_q = hours_q.where(ts_t.c["Equipment_Tag"].in_(equipment))
        sqm_q = sqm_q.where(prod_t.c["Equipment_Tag"].in_(equipment))
        note_q = note_q.where(ts_t.c["Equipment_Tag"].in_(equipment))
    if date_from:
        hours_q = hours_q.where(ts_t.c["Work_Date"] >= date_from)
        sqm_q = sqm_q.where(prod_t.c["Work_Date"] >= date_from)
        note_q = note_q.where(ts_t.c["Work_Date"] >= date_from)
    if date_to:
        hours_q = hours_q.where(ts_t.c["Work_Date"] <= date_to)
        sqm_q = sqm_q.where(prod_t.c["Work_Date"] <= date_to)
        note_q = note_q.where(ts_t.c["Work_Date"] <= date_to)

    cells: dict[tuple, dict] = {}
    for r in (await session.execute(hours_q)).mappings():
        k = (str(r["Equipment_Tag"]), str(r["System_Code"] or ""),
             str(r["Work_Date"])[:10])
        cells.setdefault(k, {"hours": 0.0, "sqm": 0.0, "entries": 0})
        cells[k]["hours"] += float(r["hours"] or 0)
        cells[k]["entries"] += int(r["entries"] or 0)
    for r in (await session.execute(sqm_q)).mappings():
        k = (str(r["Equipment_Tag"]), str(r["System_Code"] or ""),
             str(r["Work_Date"])[:10])
        cells.setdefault(k, {"hours": 0.0, "sqm": 0.0, "entries": 0})
        cells[k]["sqm"] += float(r["sqm"] or 0)

    notes: dict[tuple, list[str]] = {}
    for r in (await session.execute(note_q)).mappings():
        k = (str(r["Equipment_Tag"]), str(r["System_Code"] or ""),
             str(r["Work_Date"])[:10])
        txt = str(r["Remarks"] or "").strip()
        if txt and txt not in notes.setdefault(k, []):
            notes[k].append(txt)

    if not cells:
        return {"site_id": site_id, "system_code": system_code,
                "series": [], "days": [], "warnings": [
                    "No timesheets or production have been recorded for this "
                    "selection, so there is nothing to compare yet. The chart "
                    "fills in as the daily timesheet and the team SQM are "
                    "entered."]}

    all_dates = sorted({k[2] for k in cells})
    span = _date_span(all_dates[0], all_dates[-1])

    by_series: dict[tuple, dict] = {}
    for (tag, code, _d) in cells:
        by_series.setdefault((tag, code), {})

    warnings: list[str] = []
    series = []
    for (tag, code) in sorted(by_series):
        cum_h = cum_s = 0.0
        points, absurd = [], 0
        for day in span:
            c = cells.get((tag, code, day), {"hours": 0.0, "sqm": 0.0,
                                             "entries": 0})
            h, sq = float(c["hours"]), float(c["sqm"])
            cum_h += h
            cum_s += sq
            # ⚠️ BOTH GUARDS, AND THEY ARE DIFFERENT QUESTIONS. `sq > 0` is the
            # daily divide-by-zero; `cum_s > 0` is whether the job has produced
            # anything AT ALL yet. A run of mobilisation days fails the second
            # while every later day passes it.
            daily = round(h / sq, 3) if sq > 0 else None
            cumul = round(cum_h / cum_s, 3) if cum_s > 0 else None
            if daily is not None and daily > _ABSURD_MH_PER_SQM:
                absurd += 1
            worked = h > 0
            points.append({
                "date": day,
                "hours": round(h, 2),
                "sqm": round(sq, 2),
                "cum_hours": round(cum_h, 2),
                "cum_sqm": round(cum_s, 2),
                "daily_mh_per_sqm": daily,
                "cum_mh_per_sqm": cumul,
                # `gap` means "hours went in and no area came out" — the case
                # ruling Q12 is about. A day with NEITHER is simply idle and is
                # not a gap in anything.
                "gap": bool(worked and sq <= 0),
                "idle": not worked and sq <= 0,
                "reason": ("; ".join(notes.get((tag, code, day), []))
                           or None) if worked and sq <= 0 else None,
                "entries": int(c["entries"]),
            })
        gaps = [p for p in points if p["gap"]]
        unexplained = [p for p in gaps if not p["reason"]]
        if unexplained:
            warnings.append(
                f"{_key(tag, code)}: {len(unexplained)} day(s) booked hours "
                f"with no area and no note. The chart shows the gap; only the "
                f"timekeeper can say what the day was spent on.")
        if absurd:
            warnings.append(
                f"{_key(tag, code)}: {absurd} day(s) exceed "
                f"{_ABSURD_MH_PER_SQM:g} man-hours per m², which is more likely "
                f"a mistyped area than a bad day.")
        series.append({
            "key": _key(tag, code),
            "Equipment_Tag": tag,
            "System_Code": code,
            "points": points,
            "Total_Hours": round(cum_h, 2),
            "Total_SQM": round(cum_s, 2),
            # The one number that compares two jobs of different sizes.
            "MH_per_SQM": round(cum_h / cum_s, 3) if cum_s > 0 else None,
            "Days_Worked": sum(1 for p in points if p["hours"] > 0),
            "Days_Without_Area": len(gaps),
        })

    # ⚠️ COMPARABILITY IS A PROPERTY OF THE SELECTION, NOT OF THE CHART. Two
    # tanks running different lining systems have different benchmarks, so
    # putting their man-hours per m² on one axis invites a conclusion the
    # numbers do not support. Say so rather than refusing to draw it.
    codes = {s["System_Code"] for s in series}
    if len(codes) > 1:
        warnings.append(
            "These series run DIFFERENT lining systems (" +
            ", ".join(sorted(c or "—" for c in codes)) +
            "). Man-hours per m² is not comparable across them — a tile lining "
            "and a coat are different work. Filter to one system to compare "
            "equipment against equipment.")

    return {"site_id": site_id, "system_code": system_code,
            "days": span, "series": series, "warnings": warnings}


def _date_span(first: str, last: str) -> list[str]:
    """Every calendar day from `first` to `last`, inclusive.

    Capped at two years: the axis is one bar per day, and a bad date in the
    data (a typo'd year) would otherwise ask the browser to draw 700,000 of
    them.
    """
    try:
        a = _dt.date.fromisoformat(first)
        b = _dt.date.fromisoformat(last)
    except ValueError:
        return sorted({first, last})
    if b < a:
        a, b = b, a
    days = min((b - a).days, 730)
    return [(a + _dt.timedelta(days=i)).isoformat() for i in range(days + 1)]


async def scope_options(session: AsyncSession, *, site_id: Optional[str]
                        ) -> dict:
    """The equipment and systems that HAVE timesheet hours.

    Offering a tag with no hours produces an empty chart and a shrug; the
    picker only lists what can actually be drawn.
    """
    q = select(ts_t.c["Equipment_Tag"], ts_t.c["System_Code"]).where(
        ts_t.c["Equipment_Tag"].is_not(None),
        ts_t.c["System_Code"].is_not(None)).distinct()
    if site_id is not None:
        q = q.where(ts_t.c["Site_ID"] == site_id)
    rows = (await session.execute(q)).all()
    by_code: dict[str, list[str]] = {}
    for tag, code in rows:
        by_code.setdefault(str(code or ""), []).append(str(tag))
    return {"systems": [{"System_Code": c, "equipment": sorted(set(t))}
                        for c, t in sorted(by_code.items())]}
