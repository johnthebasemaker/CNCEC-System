"""
backend/api/services/jobs.py — how a job is NAMED, in one place.

A "job" is a piece of work on one equipment tag: either a lining system, or the
surface prep that belongs to no system. Four screens across two portals show
them, and before this module each built its own label from whatever columns it
happened to have — so the same job read `LSC4`, `LSC4 (Carbon Brick Lining
30mm)`, `CBL30` and `Carbon Brick lining 30mm` depending on where you looked.

⚠️ THE BACKEND ASSEMBLES THE LABEL; THE FRONTEND RENDERS IT (operator ruling
Q4, 2026-08-20). The alternative — a mirrored TypeScript formatter — would have
made this the THIRD dual-implementation surface after the SME engine and the
sort key, and the first two are only safe because a parity gate proves them
equal on every build. A label does not earn that machinery. The API ships the
assembled strings AND their parts, so a screen that wants to lay the pieces out
differently still can, without re-deriving them.

⚠️ THE SYSTEM NAME COMES FROM `sme_recipe."Lining_System"` — the column the
operator edits in `For_1_SQM.xlsx`. NOT from `Lining_System_Name`, which
despite its name holds the SHORT CODE (`RLCB4`, `CBL30`, `PUL1`). Reading the
wrong one is easy and produces something that looks like a name.

⚠️ CV/ME IS A PROPERTY OF THE (TAG, CODE) ROW, NOT OF THE CODE. `LSC1` is CV on
nine concrete rows and ME on nineteen tank/vessel rows in the live master, so
a chip reading `LSC1 [CV]` on a screen that aggregates across equipment is
simply false. `code_types` returns the SET, and `code_chip` prints `CV/ME` when
a code spans both rather than picking the first one it met.
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy import distinct, select
from sqlalchemy.ext.asyncio import AsyncSession

from .ledger import _MD

recipe_t = _MD.tables["sme_recipe"]
eq_t = _MD.tables["sme_equipment"]

PREP_LABEL = "Surface prep"


def _clean(v) -> str:
    """Collapse runs of whitespace. The workbook holds `Rubber Lining  4mm`
    (two spaces) on one LSC3 row and `Rubber Lining 4mm` on another; they are
    one name typed twice, and left alone they render as two systems."""
    return " ".join(str(v or "").split())


async def system_names(session: AsyncSession) -> dict[str, str]:
    """`Lining_System_Code` → the full system name, from the recipe workbook.

    Where a code carries several spellings the most frequent wins, breaking
    ties alphabetically so the answer is stable across runs rather than
    dependent on row order.
    """
    rows = (await session.execute(select(
        recipe_t.c["Lining_System_Code"], recipe_t.c["Lining_System"],
        recipe_t.c["Lining_System_Name"]))).all()
    tally: dict[str, dict[str, int]] = {}
    fallback: dict[str, str] = {}
    for code, full, short in rows:
        c = _clean(code)
        if not c:
            continue
        name = _clean(full)
        if name:
            tally.setdefault(c, {})[name] = tally.setdefault(c, {}).get(name, 0) + 1
        elif _clean(short):
            fallback.setdefault(c, _clean(short))
    out = {c: max(sorted(v), key=lambda n: (v[n], n)) for c, v in tally.items()}
    for c, s in fallback.items():
        out.setdefault(c, s)
    return out


async def code_types(session: AsyncSession,
                     site_id: Optional[str] = None) -> dict[str, list[str]]:
    """`Lining_System_Code` → the discipline(s) it is used in at this site.

    A LIST, never a single value, precisely because `LSC1` is both. Callers
    that have a specific (tag, code) should use `equipment_types` instead and
    show the exact one; this is for aggregate views, where the honest answer
    is "both".
    """
    stmt = select(distinct(eq_t.c["Lining_System_Code"]), eq_t.c["Type"])
    if site_id:
        stmt = stmt.where(eq_t.c["Site_ID"] == site_id)
    out: dict[str, set] = {}
    for code, typ in (await session.execute(stmt)).all():
        t = _clean(typ).upper()
        if _clean(code) and t:
            out.setdefault(_clean(code), set()).add(t)
    return {c: sorted(v) for c, v in out.items()}


async def equipment_types(session: AsyncSession,
                          site_id: Optional[str] = None) -> dict:
    """`(tag, code)` → its exact CV/ME, and `(tag, '')` → the tag's set."""
    stmt = select(eq_t.c["Equipment_Tag_No"], eq_t.c["Lining_System_Code"],
                  eq_t.c["Type"])
    if site_id:
        stmt = stmt.where(eq_t.c["Site_ID"] == site_id)
    exact: dict[tuple, str] = {}
    by_tag: dict[str, set] = {}
    for tag, code, typ in (await session.execute(stmt)).all():
        t = _clean(typ).upper()
        if not t:
            continue
        exact[(_clean(tag), _clean(code))] = t
        by_tag.setdefault(_clean(tag), set()).add(t)
    for tag, types in by_tag.items():
        # The prep job for a tag covers every surface on it, so its discipline
        # is the set — 'CV/ME' where a tag has both, never whichever was read
        # first.
        exact[(tag, "")] = "/".join(sorted(types))
    return exact


def code_chip(code: str, types) -> str:
    """`LSC4 [CV]`, or `LSC1 [CV/ME]` where the code spans both.

    `types` may be a string, a list or a set — the callers hold all three
    shapes and normalising here beats three near-identical branches.
    """
    c = _clean(code)
    if not c:
        return PREP_LABEL
    if isinstance(types, str):
        parts = [p for p in types.replace(",", "/").split("/") if p.strip()]
    else:
        parts = [str(t) for t in (types or []) if str(t).strip()]
    parts = sorted({p.strip().upper() for p in parts})
    return f"{c} [{'/'.join(parts)}]" if parts else c


def job_label(tag: str, code: str, names: dict, eq_type: str = "",
              *, esc: str = "", activity: str = "",
              sub_activity: str = "") -> dict:
    """Everything a screen needs to name one job, assembled once.

    `Short` is the one-line form for a table cell or a select option; `Full`
    adds the sub-activity detail for a header. The parts are published beside
    them so a screen that wants its own layout does not have to re-derive
    anything.
    """
    t = _clean(tag)
    c = _clean(code)
    chip = code_chip(c, eq_type)
    name = _clean(names.get(c, "")) if c else ""
    if c:
        short = f"{t} · {chip}" + (f" — {name}" if name else "")
    else:
        short = f"{t} · {PREP_LABEL}" + (f" [{_clean(eq_type)}]" if eq_type else "")
    detail = " · ".join(p for p in (_clean(esc), _clean(activity)) if p)
    if _clean(sub_activity):
        detail = f"{detail} → {_clean(sub_activity)}" if detail \
            else _clean(sub_activity)
    return {
        "Equipment_Tag_No": t,
        "Lining_System_Code": c,
        "Type": _clean(eq_type),
        "Code_Chip": chip,
        "System_Name": name,
        "Execution_Sub_Activity_Code": _clean(esc),
        "Activity": _clean(activity),
        "Sub_Activity": _clean(sub_activity),
        "System_Agnostic": not c,
        "Short": short,
        "Full": f"{short}\n{detail}" if detail else short,
    }
