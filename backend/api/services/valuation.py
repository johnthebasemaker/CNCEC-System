"""
backend/api/services/valuation.py — site-wide stock valuation and 30-day burn.

Track 3 of Phase 10. Answers two questions for a board: what is the material on
our shelves worth, and what rate are we consuming it at.

────────────────────────────────────────────────────────────────────────────
⚠️ THE UN-COSTED PROBLEM IS THE WHOLE DESIGN, AND IT IS NOT A ROUNDING ISSUE.

`inventory."Unit_Cost"` defaults to 0. Multiplying quantity by a zero cost and
summing produces a number that is arithmetically correct and factually a lie:
a site holding 400 uncosted drums reports the same valuation as one holding
none. A board acts on that number.

So an un-costed line is NEVER summed as zero. It is counted, separated, and
reported on its own line — "Not Valued (N items)" — with a footnote. The
valuation figure is then honestly labelled as covering only the priced portion,
and the reader can see how much of the estimate is missing. Operator ruling
Q3.2, slice 10b.

────────────────────────────────────────────────────────────────────────────
⚠️ ERP AND SME NUMBERS ARE PRESENTED SIDE BY SIDE AND NEVER ADDED (rule 1a).

`inventory`/`consumption` are the live ERP ledger. The `sme_*` tables are a
frozen estimator seed and an ERP movement must not move one — the same rule
`services/quality.py` carries and suite BM greps for. They measure different
things about the same site: what is on the shelf today, and what the estimate
said would be needed. Summing them would double-count material that appears in
both, and the total would be meaningless in a way nobody could spot from the
figure alone.

This module therefore returns them as two separate blocks, and the renderer
prints them in two separate tables with a note saying why.
"""
from __future__ import annotations

import datetime as _dt

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

CURRENCY = "SAR"
BURN_WINDOW_DAYS = 30


def _site_clause(site: str | None, col: str) -> tuple[str, dict]:
    """⚠️ `None` is unrestricted; `''` matches NOTHING.

    Same fail-closed rule as `health_monitor._site_clause` and the read scoping:
    collapsing the two with `if site:` is how a site-less account ends up
    reading every site.
    """
    if site is None:
        return "", {}
    return f" AND {col} = :vsite", {"vsite": site}


async def stock_valuation(session: AsyncSession, *,
                          site: str | None) -> dict:
    """On-hand quantity × unit cost, with the un-costed portion held out.

    Derived stock is receipts − consumption − returns, exactly as
    `v_site_stock` computes it, so the valuation cannot disagree with the stock
    report it sits beside.
    """
    clause, params = _site_clause(site, 'a."Site_ID"')
    rows = (await session.execute(text(f"""
        WITH activity AS (
            SELECT TRIM("SAP_Code") AS sap, COALESCE("Site_ID",'HQ') AS "Site_ID",
                   SUM("Quantity") AS rec, 0 AS con, 0 AS ret
              FROM receipts    GROUP BY 1, 2
            UNION ALL
            SELECT TRIM("SAP_Code"), COALESCE("Site_ID",'HQ'), 0, SUM("Quantity"), 0
              FROM consumption GROUP BY 1, 2
            UNION ALL
            SELECT TRIM("SAP_Code"), COALESCE("Site_ID",'HQ'), 0, 0, SUM("Quantity")
              FROM returns     GROUP BY 1, 2
        )
        SELECT a."Site_ID" AS site, a.sap,
               MAX(i."Equipment_Description") AS name,
               MAX(i."Category")  AS category,
               MAX(COALESCE(i."Unit_Cost", 0)) AS unit_cost,
               SUM(a.rec) - SUM(a.con) - SUM(a.ret) AS on_hand
          FROM activity a
          JOIN inventory i ON TRIM(i."SAP_Code") = a.sap
         WHERE 1 = 1 {clause}
         GROUP BY a."Site_ID", a.sap
        HAVING SUM(a.rec) - SUM(a.con) - SUM(a.ret) > 0
    """), params)).mappings().all()

    per_site: dict[str, dict] = {}
    for r in rows:
        s = per_site.setdefault(r["site"], {
            "site": r["site"], "valued_items": 0, "valued_qty": 0.0,
            "value": 0.0, "unvalued_items": 0, "unvalued_qty": 0.0})
        qty = float(r["on_hand"] or 0)
        cost = float(r["unit_cost"] or 0)
        if cost > 0:
            s["valued_items"] += 1
            s["valued_qty"] += qty
            s["value"] += qty * cost
        else:
            # ⚠️ COUNTED, NOT SUMMED AS ZERO. See the module docstring.
            s["unvalued_items"] += 1
            s["unvalued_qty"] += qty

    sites = sorted(per_site.values(), key=lambda x: -x["value"])
    return {
        "sites": sites,
        "total_value": round(sum(s["value"] for s in sites), 2),
        "valued_items": sum(s["valued_items"] for s in sites),
        "unvalued_items": sum(s["unvalued_items"] for s in sites),
        "unvalued_qty": round(sum(s["unvalued_qty"] for s in sites), 2),
        # What proportion of the LINES carry a price. Printed on the report so
        # the reader can weigh the total rather than trusting it.
        "coverage_pct": round(
            100.0 * sum(s["valued_items"] for s in sites)
            / max(1, sum(s["valued_items"] + s["unvalued_items"] for s in sites)), 1),
    }


async def burn_value(session: AsyncSession, *, site: str | None,
                     days: int = BURN_WINDOW_DAYS) -> dict:
    """What was consumed in the rolling window, valued the same way.

    ⚠️ THE DIVISOR IS THE WINDOW, NOT THE NUMBER OF DAYS THAT HAD ACTIVITY.
    Dividing by "days on which something was consumed" flatters the daily rate
    on a site that worked eight days out of thirty, and a board reading it as a
    run rate would plan against a number that only holds while the crew is
    there. Suite CO exists because Phase 9e made the same mistake in reverse.
    """
    days = max(1, int(days))
    since = (_dt.date.today() - _dt.timedelta(days=days)).isoformat()
    clause, params = _site_clause(site, 'c."Site_ID"')
    rows = (await session.execute(text(f"""
        SELECT COALESCE(c."Site_ID",'HQ') AS site,
               TRIM(c."SAP_Code")         AS sap,
               MAX(i."Equipment_Description") AS name,
               MAX(COALESCE(i."Unit_Cost", 0)) AS unit_cost,
               SUM(c."Quantity")          AS qty
          FROM consumption c
          JOIN inventory i ON TRIM(i."SAP_Code") = TRIM(c."SAP_Code")
         WHERE c."Date" >= :since {clause}
         GROUP BY 1, 2
    """), {"since": since, **params})).mappings().all()

    per_site: dict[str, dict] = {}
    lines: list[dict] = []
    for r in rows:
        qty = float(r["qty"] or 0)
        cost = float(r["unit_cost"] or 0)
        s = per_site.setdefault(r["site"], {
            "site": r["site"], "value": 0.0, "qty": 0.0,
            "unvalued_items": 0, "unvalued_qty": 0.0})
        s["qty"] += qty
        if cost > 0:
            s["value"] += qty * cost
            lines.append({"site": r["site"], "sap": r["sap"], "name": r["name"],
                          "qty": qty, "unit_cost": cost,
                          "value": round(qty * cost, 2)})
        else:
            s["unvalued_items"] += 1
            s["unvalued_qty"] += qty

    total = round(sum(s["value"] for s in per_site.values()), 2)
    lines.sort(key=lambda x: -x["value"])
    return {
        "days": days, "since": since,
        "sites": sorted(per_site.values(), key=lambda x: -x["value"]),
        "total_value": total,
        "daily_value": round(total / days, 2),
        "unvalued_items": sum(s["unvalued_items"] for s in per_site.values()),
        "unvalued_qty": round(sum(s["unvalued_qty"]
                                  for s in per_site.values()), 2),
        "top_lines": lines[:15],
    }


async def sme_demand(session: AsyncSession, *, site: str | None) -> dict:
    """The ESTIMATOR's outstanding demand — reported beside, never added.

    ⚠️ THIS READS `sme_*` AND NOTHING ELSE, and the caller must keep it apart
    from the ERP figures above (rule 1a). It answers "what does the estimate
    still say we need", which is a forecast; the valuation answers "what is on
    the shelf", which is a fact. Adding a forecast to a fact produces a number
    with no meaning that looks exactly like a bigger fact.
    """
    # ⚠️ THE SEED IS NOT SITE-SCOPED, and pretending otherwise would be worse
    # than saying so. `sme_inventory_seed` is a frozen project-wide snapshot
    # with no Site_ID column; the equipment that consumes it is per-site, but
    # the seed itself is one list. So a site-scoped caller gets the same
    # project figure with `scope: "project"` on it, and the renderer labels it
    # that way rather than implying the number belongs to their site alone.
    del site
    try:
        rows = (await session.execute(text("""
            SELECT COUNT(*)                                     AS lines,
                   SUM(COALESCE(s."Initial_Available_Qty", 0))  AS on_hand,
                   SUM(COALESCE(s."Initial_Ordered_Qty", 0))    AS on_order
              FROM sme_inventory_seed s
        """))).mappings().first()
    except Exception:                                       # noqa: BLE001
        # The estimator seed is optional on a bare database. A missing SME
        # block must not take the valuation report down with it.
        return {"available": False, "scope": "project", "lines": 0}
    if rows is None:
        return {"available": False, "scope": "project", "lines": 0}
    return {"available": True, "scope": "project",
            "lines": int(rows["lines"] or 0),
            "seed_on_hand": float(rows["on_hand"] or 0),
            "seed_on_order": float(rows["on_order"] or 0)}


async def build_valuation(session: AsyncSession, *, site: str | None,
                          days: int = BURN_WINDOW_DAYS) -> dict:
    """The whole brief, as data. The PDF renderer is a pure function of this."""
    stock = await stock_valuation(session, site=site)
    burn = await burn_value(session, site=site, days=days)
    sme = await sme_demand(session, site=site)
    months = (stock["total_value"] / burn["daily_value"] / 30.0
              if burn["daily_value"] > 0 else None)
    return {
        "generated_at": _dt.datetime.now().isoformat(timespec="seconds"),
        "site": site, "currency": CURRENCY,
        "stock": stock, "burn": burn, "sme": sme,
        # ⚠️ None WHEN THERE IS NO BURN, not 0 and not "infinite". A site that
        # consumed nothing in the window has no meaningful cover figure, and
        # printing one would invent a runway out of a division by zero.
        "months_cover": round(months, 1) if months is not None else None,
    }
