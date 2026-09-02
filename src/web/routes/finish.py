"""Fill & Finish — Control Room page + JSON endpoints (spec: tasks/finish-hour.md).

Thin HTTP layer over `finish_service`: the page previews `plan_window()` and the
Finish button calls `apply_window(apply=True)` — one code path with the CLI.
A program window whose programming is not placed (`state == "unplaced"`) never
gets a Finish option (Lee 8/28: "you can never finish a show until the
programming has been set up").
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

# Market code -> COD_USER (same table as EtereClient.MARKET_CODES / data-reference.md)
MARKET_IDS = {
    "NYC": 1,
    "CMP": 2,
    "HOU": 3,
    "SFO": 4,
    "SEA": 5,
    "LAX": 6,
    "CVC": 7,
    "WDC": 8,
    "MMT": 9,
    "DAL": 10,
}


class FinishApplyBody(BaseModel):
    market: str
    date: str
    lo: float
    hi: float
    refill: bool = False  # strip existing PI/PSA/ID and fill from scratch (Lee 9/1)


def _market_id(code: str) -> int:
    mid = MARKET_IDS.get((code or "").upper())
    if not mid:
        raise HTTPException(status_code=400, detail=f"unknown market {code!r}")
    return mid


def build_finish_router(templates: Jinja2Templates) -> APIRouter:
    router = APIRouter()

    @router.get("/master-control/finish")
    async def finish_page(request: Request):
        return templates.TemplateResponse(request, "master_control/finish.html")

    @router.get("/api/master-control/finish/day")
    async def finish_day(market: str = Query(...), date: str = Query(...)):
        mid = _market_id(market)

        def _run():
            from browser_automation.etere_direct_client import connect
            from src.business_logic.services.finish_service import list_programs

            with connect() as conn:
                return list_programs(conn.cursor(), mid, date)

        programs = await asyncio.get_running_loop().run_in_executor(None, _run)
        return {"market": market.upper(), "cod_user": mid, "date": date, "programs": programs}

    @router.get("/api/master-control/finish/plan")
    async def finish_plan(
        market: str = Query(...),
        date: str = Query(...),
        lo: float = Query(...),
        hi: float = Query(...),
        refill: bool = Query(False),
    ):
        mid = _market_id(market)

        def _run():
            from browser_automation.etere_direct_client import connect
            from src.business_logic.services.finish_service import plan_window

            with connect() as conn:
                r = plan_window(conn.cursor(), mid, date, lo, hi, refill=refill)
            return {k: v for k, v in r.items() if not k.startswith("_")}

        return await asyncio.get_running_loop().run_in_executor(None, _run)

    @router.post("/api/master-control/finish/apply")
    async def finish_apply(body: FinishApplyBody):
        mid = _market_id(body.market)

        def _run():
            from browser_automation.etere_direct_client import connect
            from src.business_logic.services.finish_service import apply_window

            log: list[str] = []
            with connect() as conn:
                r = apply_window(
                    conn, mid, body.date, body.lo, body.hi, True, log=log.append, refill=body.refill
                )
            r["log"] = log
            return r

        return await asyncio.get_running_loop().run_in_executor(None, _run)

    return router
