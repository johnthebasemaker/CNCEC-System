"""
backend/api/dashboard.py — aggregate metrics for the main Dashboard (Phase 5
visual parity): total valuation KPI + chart series for stock-vs-min, burn
forecast, and top-consumed. Read-only; site-scoped for low roles (the same
`resolve_site_param` pin the rest of the app uses). Visible to supervisor+
(level ≥ 1), matching the Dashboard's nav gate.
"""
from __future__ import annotations

import datetime as _dt
from typing import Optional

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .auth import require_level, resolve_site_param, site_filter_applies
from .db import get_session
from .stock import SQL_SITE_STOCK

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


def _cutoff(days: int) -> str:
    return (_dt.date.today() - _dt.timedelta(days=days)).isoformat()


@router.get("/metrics", summary="Dashboard KPIs + chart series (valuation, stock-vs-min, burn, top-consumed)")
async def metrics(site_id: Optional[str] = None,
                  user: dict = Depends(require_level(1)),
                  session: AsyncSession = Depends(get_session)):
    site_id = resolve_site_param(user, site_id)
    scoped = site_filter_applies(site_id)
    sfilter = ' AND s."Site_ID" = :site' if scoped else ''
    csite = ' AND COALESCE(c."Site_ID", \'HQ\') = :site' if scoped else ''
    p: dict = {"site": site_id} if scoped else {}
    pc: dict = {**p, "cutoff": _cutoff(30)}

    async def rows(sql: str, params: dict):
        return [dict(m) for m in (await session.execute(text(sql), params)).mappings().all()]

    async def scalar(sql: str, params: dict):
        return (await session.execute(text(sql), params)).scalar_one()

    valuation = await scalar(f'''
        SELECT COALESCE(ROUND(CAST(SUM(s."Current_Stock"*COALESCE(i."Unit_Cost",0)) AS NUMERIC),2),0)
        FROM ({SQL_SITE_STOCK}) s
        LEFT JOIN inventory i ON TRIM(i."SAP_Code") = s."SAP_Code"
        WHERE 1=1 {sfilter}''', p)

    stock_vs_min = await rows(f'''
        SELECT s."SAP_Code" AS sap, COALESCE(s."Equipment_Description",'') AS name,
               s."Current_Stock" AS current, s."Minimum_Qty" AS minimum
        FROM ({SQL_SITE_STOCK}) s
        WHERE s."Minimum_Qty" > 0 {sfilter}
        ORDER BY (s."Current_Stock" / NULLIF(s."Minimum_Qty",0)) ASC
        LIMIT 10''', p)

    top_consumed = await rows(f'''
        SELECT TRIM(c."SAP_Code") AS sap, MAX(i."Equipment_Description") AS name,
               ROUND(CAST(SUM(c."Quantity") AS NUMERIC),3) AS consumed
        FROM consumption c
        LEFT JOIN inventory i ON TRIM(i."SAP_Code") = TRIM(c."SAP_Code")
        WHERE c."Date" >= :cutoff {csite}
        GROUP BY TRIM(c."SAP_Code") ORDER BY consumed DESC LIMIT 10''', pc)

    burn_forecast = await rows(f'''
        WITH burn AS (
          SELECT TRIM(c."SAP_Code") AS sap, SUM(c."Quantity")/30.0 AS daily
          FROM consumption c WHERE c."Date" >= :cutoff {csite}
          GROUP BY TRIM(c."SAP_Code")),
        stock AS (
          SELECT s."SAP_Code" AS sap, SUM(s."Current_Stock") AS cur
          FROM ({SQL_SITE_STOCK}) s WHERE 1=1 {sfilter} GROUP BY s."SAP_Code")
        SELECT b.sap AS sap,
               ROUND(CAST(b.daily AS NUMERIC),3) AS daily_avg,
               ROUND(CAST(COALESCE(st.cur,0) AS NUMERIC),3) AS current,
               CASE WHEN b.daily > 0
                    THEN ROUND(CAST(COALESCE(st.cur,0)/b.daily AS NUMERIC),1)
                    ELSE NULL END AS days_remaining
        FROM burn b LEFT JOIN stock st ON st.sap = b.sap
        WHERE b.daily > 0
        ORDER BY days_remaining ASC NULLS LAST LIMIT 10''', pc)

    # ── Top 5 Expiring (2026-08-05) ───────────────────────────────────────────
    # From `lots`, which already carries the FEFO expiry data. Consistent with
    # the standing rule that FEFO is ALLOW-AND-LOG: this is a WARNING widget,
    # never a block, and it deliberately includes lots that have already
    # expired (negative days) because those are the ones somebody needs to look
    # at today. Open lots only — a closed lot is not on a shelf.
    lsite = ' AND COALESCE(l."Site_ID", \'HQ\') = :site' if scoped else ''
    top_expiring = await rows(f'''
        SELECT l."Lot_Number" AS lot, TRIM(l."SAP_Code") AS sap,
               COALESCE(MAX(i."Equipment_Description"), '') AS name,
               l."Expiry_Date" AS expiry_date,
               (l."Expiry_Date"::date - CURRENT_DATE) AS days_left
        FROM lots l
        LEFT JOIN inventory i ON TRIM(i."SAP_Code") = TRIM(l."SAP_Code")
        WHERE l."Expiry_Date" IS NOT NULL AND l."Expiry_Date" <> ''
          AND COALESCE(l."Status", 'open') = 'open' {lsite}
        GROUP BY l."Lot_Number", TRIM(l."SAP_Code"), l."Expiry_Date"
        ORDER BY l."Expiry_Date"::date ASC
        LIMIT 5''', p)

    # ── Highest Value (2026-08-05) ────────────────────────────────────────────
    # ⚠️ `Unit_Cost` DEFAULTS TO 0, so this is a partial picture by
    # construction. The widget therefore reports its own COVERAGE — "N of M
    # items have a unit cost" — rather than presenting a confidently wrong
    # total. Same principle as the ruling that keeps a GRAND TOTAL off the
    # generic xlsx path: a number stated without its caveat is worse than no
    # number, because it gets quoted.
    highest_value = await rows(f'''
        SELECT s."SAP_Code" AS sap, COALESCE(s."Equipment_Description",'') AS name,
               s."Current_Stock" AS qty, COALESCE(i."Unit_Cost",0) AS unit_cost,
               ROUND(CAST(s."Current_Stock"*COALESCE(i."Unit_Cost",0) AS NUMERIC),2) AS value
        FROM ({SQL_SITE_STOCK}) s
        LEFT JOIN inventory i ON TRIM(i."SAP_Code") = s."SAP_Code"
        WHERE COALESCE(i."Unit_Cost",0) > 0 {sfilter}
        ORDER BY value DESC
        LIMIT 5''', p)
    value_coverage = await rows(f'''
        SELECT COUNT(*) AS total,
               COUNT(*) FILTER (WHERE COALESCE(i."Unit_Cost",0) > 0) AS priced
        FROM ({SQL_SITE_STOCK}) s
        LEFT JOIN inventory i ON TRIM(i."SAP_Code") = s."SAP_Code"
        WHERE 1=1 {sfilter}''', p)
    cov = value_coverage[0] if value_coverage else {"total": 0, "priced": 0}

    return {"valuation_total": float(valuation or 0), "site_id": site_id,
            "stock_vs_min": stock_vs_min, "top_consumed": top_consumed,
            "burn_forecast": burn_forecast,
            "top_expiring": top_expiring,
            "highest_value": highest_value,
            # The caveat travels WITH the data, so a consumer cannot render the
            # figures without it.
            "value_coverage": {"priced": int(cov.get("priced") or 0),
                               "total": int(cov.get("total") or 0)}}
