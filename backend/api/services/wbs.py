"""
backend/api/services/wbs.py — Phase 9a: which WBS number does this charge to?

The operator's complaint was that the `WBS #` column is "mostly blank". It is
blank in every one of the 1,674 live consumption rows, and the cause is precise:
`wbs_master`, `entry_docs.assert_wbs()` and the three HOD endpoints that manage
them have existed since the parity build, and **nothing in the frontend ever
called them**. With zero rows the gate is a permanent no-op, so no entry was
ever asked for a WBS and none ever carried one. Phase 9a opens that tap and adds
the dimension the operator actually plans by: the work type.

────────────────────────────────────────────────────────────────────────────
THE RESOLUTION ORDER, stated once so it can be checked (ruling Q14/Q16):

    1. an explicit WBS on the entry          — a human chose it; never overridden
    2. the work-type map for this site       — the new rule
    3. the equipment master's WBS_No         — where the SME workbook knows it
    4. nothing                               — and that stays legal

⚠️ STEP 1 WINS, ALWAYS. The map is a DEFAULT, not a correction. A store keeper
who picked a WBS on the form picked it for a reason the table does not know, and
silently replacing it would make the form a suggestion box. `resolve_wbs`
therefore returns the explicit value untouched and reports `source='explicit'`.

⚠️ STEP 3 IS CURRENTLY ALWAYS EMPTY, ON PURPOSE. `sme_equipment.WBS_No` exists
and is populated in 0 of 85 rows. It is in the chain because that is where the
number belongs when the SME workbook finally carries it, and a chain written now
is one nobody has to rediscover later. It costs one indexed lookup that misses.

⚠️ TWO WORK TYPES ARE RESERVED AND ARE NOT WORK TYPES AT ALL.
`supervisor.approve_smr` writes `Work_Type='SUPERVISOR_REQUEST'` and
`ledger.stage_adjustment` writes `'STOCK_ADJUSTMENT'`. Both are MARKERS written
by code — `reports.rep_intent_vs_actual` joins on the first — not descriptions
of work anybody did. They must never appear in the HOD's dropdown, must never be
mappable to a WBS, and must never be refused by the strict-dropdown gate. A gate
that blocked them would break stock adjustments, which is a long way from where
anyone would look.

────────────────────────────────────────────────────────────────────────────
NORMALISATION. `Work_Type_Norm` is `lower(collapse_whitespace(trim(s)))`. That
merges the four case-collisions the live ledger actually contains — `civil`/
`Civil`, `coating`/`Coating`, `In yard`/`In Yard`, `others`/`Others` — and
nothing else. `Arrangement` and `Site Arrangement` are different strings and
stay different; merging those is a judgement about the work, and it belongs to
the HOD in the UI, not to a regex here.
"""
from __future__ import annotations

import re
from typing import Optional

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .ledger import _MD

map_t = _MD.tables["wbs_work_type_map"]
wbs_t = _MD.tables["wbs_master"]
equip_t = _MD.tables["sme_equipment"]

# Markers, not work types. See the module docstring.
RESERVED_WORK_TYPES = frozenset({"SUPERVISOR_REQUEST", "STOCK_ADJUSTMENT"})

_WS = re.compile(r"\s+")


def normalise(work_type: str | None) -> str:
    """The identity of a work type. Case-folded, trimmed, whitespace-collapsed.

    Collapsing internal whitespace matters as much as the case fold: `In  yard`
    typed with a double space is the same work as `In yard`, and a UNIQUE
    constraint on the raw string would happily store both.
    """
    return _WS.sub(" ", str(work_type or "").strip()).lower()


def is_reserved(work_type: str | None) -> bool:
    return str(work_type or "").strip().upper() in RESERVED_WORK_TYPES


async def active_numbers(session: AsyncSession, site_id: str) -> list[str]:
    """The site's open WBS numbers. Moved here from `entry_docs` in Phase 9a so
    that the gate below and the resolver above read the same list from the same
    place; `entry_docs.active_wbs` now delegates here."""
    rows = (await session.execute(
        select(wbs_t.c["WBS_Number"])
        .where((wbs_t.c["Site_ID"] == site_id) & (wbs_t.c["status"] == "active"))
        .order_by(wbs_t.c["WBS_Number"]))).all()
    return [r[0] for r in rows]


async def assert_wbs(session: AsyncSession, *, site_id: str,
                     wbs: str | None) -> None:
    """Legacy rule (hod_portal.py WBS Manager): once a site has ACTIVE WBS
    numbers, consumption and receipt entries must carry one of them. Sites with
    none configured are unaffected.

    ⚠️ ON THE ISSUE PATH THIS RUNS *AFTER* `resolve_wbs`, NOT BEFORE. The order
    is the whole point of Phase 9a: the resolver exists to fill in a WBS the
    form left blank, and a gate that refused the blank first would make the
    work-type map unreachable — every issue would be rejected for want of the
    number the map was about to supply. `stage_consumption` therefore resolves,
    then asserts what it is actually going to store. Receipts have no work type
    to resolve from and still call this directly from the router.
    """
    options = await active_numbers(session, site_id)
    if not options:
        return
    if not (wbs or "").strip():
        raise HTTPException(422, f"site {site_id} requires a WBS Number "
                                 f"({len(options)} active) — pick one on the form")
    if wbs.strip() not in options:
        raise HTTPException(422, f"WBS {wbs!r} is not an active WBS for {site_id}")


async def active_work_types(session: AsyncSession, site_id: str) -> list[dict]:
    """The site's canonical dropdown, in display order.

    Returns the DISPLAY spelling plus its mapped WBS, because every caller that
    wants one wants the other — the entry form shows the work type and stores
    the WBS it resolves to.
    """
    rows = (await session.execute(
        select(map_t.c["Work_Type"], map_t.c["Work_Type_Norm"],
               map_t.c["WBS_Number"], map_t.c["Description"])
        .where((map_t.c["Site_ID"] == site_id) & (map_t.c["status"] == "active"))
        .order_by(map_t.c["Work_Type"]))).mappings().all()
    return [dict(r) for r in rows]


async def assert_work_type(session: AsyncSession, *, site_id: str,
                           work_type: str | None) -> None:
    """CONDITIONAL GATE, exactly like `entry_docs.assert_wbs`.

    ⚠️ A SITE WITH NO CANONICAL LIST IS UNAFFECTED. This is the whole reason
    Phase 9a can ship without a flag day: the table starts empty, the gate does
    nothing, and every existing form and test behaves as it did yesterday. The
    HOD turns the rule on for their site by adding the first work type — which
    makes enabling it an act, visible in the audit log, rather than a release.
    """
    if is_reserved(work_type):
        return
    options = await active_work_types(session, site_id)
    if not options:
        return
    if not str(work_type or "").strip():
        raise HTTPException(
            422, f"site {site_id} has a fixed list of {len(options)} work "
                 f"types — pick one on the form")
    norm = normalise(work_type)
    if norm not in {o["Work_Type_Norm"] for o in options}:
        names = ", ".join(sorted(o["Work_Type"] for o in options)[:8])
        raise HTTPException(
            422, f"{work_type!r} is not a work type at {site_id}. The list is "
                 f"managed by your HOD and currently holds: {names}"
                 + (" …" if len(options) > 8 else ""))


async def display_spelling(session: AsyncSession, *, site_id: str,
                           work_type: str | None) -> Optional[str]:
    """The HOD's chosen casing for a work type the user typed or picked.

    Storing what the canonical list says rather than what arrived is what stops
    the ledger re-growing the `civil`/`Civil` split one entry at a time. A work
    type not on the list (or a reserved marker) is returned unchanged — the gate
    above decides whether that is allowed, not this.
    """
    if is_reserved(work_type) or not str(work_type or "").strip():
        return (work_type or None)
    norm = normalise(work_type)
    hit = (await session.execute(
        select(map_t.c["Work_Type"]).where(
            (map_t.c["Site_ID"] == site_id)
            & (map_t.c["Work_Type_Norm"] == norm)
            & (map_t.c["status"] == "active")))).scalar()
    return hit or work_type


async def _wbs_for_work_type(session: AsyncSession, *, site_id: str,
                             work_type: str | None) -> Optional[str]:
    if is_reserved(work_type) or not str(work_type or "").strip():
        return None
    return (await session.execute(
        select(map_t.c["WBS_Number"]).where(
            (map_t.c["Site_ID"] == site_id)
            & (map_t.c["Work_Type_Norm"] == normalise(work_type))
            & (map_t.c["status"] == "active")))).scalar()


async def _wbs_for_equipment(session: AsyncSession, *, site_id: str,
                             tag: str | None) -> Optional[str]:
    if not str(tag or "").strip():
        return None
    return (await session.execute(
        select(equip_t.c["WBS_No"]).where(
            (equip_t.c["Site_ID"] == site_id)
            & (equip_t.c["Equipment_Tag_No"] == str(tag).strip()))
        .limit(1))).scalar()


async def resolve_wbs(session: AsyncSession, *, site_id: str,
                      work_type: str | None = None,
                      equipment_tag: str | None = None,
                      explicit: str | None = None) -> dict:
    """Which WBS this entry charges to, and — as importantly — WHY.

    Returns `{"wbs": str|None, "source": "explicit"|"work_type"|"equipment"|None}`.

    ⚠️ THE SOURCE IS NOT DECORATION. A WBS that turns out to be wrong is a
    misposted cost, and the first question asked is always "who chose this?".
    Returning the number alone would make an inherited default and a deliberate
    pick indistinguishable the moment they are stored.
    """
    if str(explicit or "").strip():
        return {"wbs": str(explicit).strip(), "source": "explicit"}

    by_type = await _wbs_for_work_type(session, site_id=site_id,
                                       work_type=work_type)
    if str(by_type or "").strip():
        return {"wbs": str(by_type).strip(), "source": "work_type"}

    by_equip = await _wbs_for_equipment(session, site_id=site_id,
                                        tag=equipment_tag)
    if str(by_equip or "").strip():
        return {"wbs": str(by_equip).strip(), "source": "equipment"}

    return {"wbs": None, "source": None}


async def usage_suggestions(session: AsyncSession, *, site_id: str,
                            limit: int = 60) -> list[dict]:
    """Work types the ledger has actually seen here, merged and counted.

    The bootstrap for a site with an empty list. It reads the DISTINCT
    normalised `Work_Type` from `consumption`, so the four case-collisions
    arrive already merged — the HOD adopts `Civil` once instead of discovering
    two of it later. Reserved markers are excluded; so is anything already on
    the list, because a suggestion you cannot act on is noise.

    The most frequent spelling wins the display casing, on the reasoning that
    the form people used most is the one they recognise.
    """
    cons_t = _MD.tables["consumption"]
    rows = (await session.execute(
        select(cons_t.c["Work_Type"])
        .where((cons_t.c["Site_ID"] == site_id)
               & (cons_t.c["Work_Type"].is_not(None))))).all()

    tally: dict[str, dict] = {}
    for (raw,) in rows:
        if is_reserved(raw):
            continue
        norm = normalise(raw)
        if not norm:
            continue
        slot = tally.setdefault(norm, {"norm": norm, "count": 0, "spellings": {}})
        slot["count"] += 1
        slot["spellings"][str(raw).strip()] = \
            slot["spellings"].get(str(raw).strip(), 0) + 1

    have = {o["Work_Type_Norm"] for o in await active_work_types(session, site_id)}
    out = []
    for norm, slot in tally.items():
        if norm in have:
            continue
        best = max(slot["spellings"].items(), key=lambda kv: (kv[1], kv[0]))[0]
        out.append({
            "Work_Type": best,
            "Work_Type_Norm": norm,
            "count": slot["count"],
            # Surfaced so the HOD can SEE that they are adopting one entry for
            # what the ledger currently spells two ways.
            "variants": sorted(slot["spellings"]),
        })
    out.sort(key=lambda r: (-r["count"], r["Work_Type"]))
    return out[:limit]
