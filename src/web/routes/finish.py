"""Fill & Finish — Control Room page + JSON endpoints (spec: tasks/finish-hour.md).

Thin HTTP layer over `finish_service`: the page previews `plan_window()` and the
Finish button calls `apply_window(apply=True)` — one code path with the CLI.
A program window whose programming is not placed (`state == "unplaced"`) never
gets a Finish option (Lee 8/28: "you can never finish a show until the
programming has been set up").
"""

from __future__ import annotations

import asyncio
import logging
import logging.handlers
import random
import time
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

logger = logging.getLogger("ctv.finish")
_ERROR_LOG = Path(__file__).resolve().parents[3] / "logs" / "server-errors.log"


def _ensure_error_log() -> None:
    """Tracebacks survive somewhere: the Jumpbox runs the server from a scheduled task
    with no console, so uvicorn's stderr is lost (Ashe 9/11 — two 500s, no trace)."""
    if any(getattr(h, "_ctv_error_log", False) for h in logger.handlers):
        return
    try:
        _ERROR_LOG.parent.mkdir(parents=True, exist_ok=True)
        h = logging.handlers.RotatingFileHandler(_ERROR_LOG, maxBytes=2_000_000, backupCount=3)
        h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        h._ctv_error_log = True  # type: ignore[attr-defined]
        logger.addHandler(h)
        logger.setLevel(logging.INFO)
    except OSError:  # read-only checkout etc. — never let logging break the endpoint
        pass


async def run_json(what: str, fn):
    """Run the blocking DB work off the event loop. An exception becomes a JSON 500
    `{status: 'error', message}` and a logged traceback instead of uvicorn's bare
    text "Internal Server Error", which the page cannot parse or show."""
    try:
        return await asyncio.get_running_loop().run_in_executor(None, fn)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 — the operator needs the message, we need the trace
        _ensure_error_log()
        logger.exception("fill & finish %s failed", what)
        return JSONResponse(
            status_code=500,
            content={"status": "error", "message": f"{type(exc).__name__}: {exc}"[:600]},
        )


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


READ_RETRY_ATTEMPTS = 3


def read_retrying(what: str, fn, attempts: int = READ_RETRY_ATTEMPTS, sleep=time.sleep):
    """Run a read-only unit of work, retrying when SQL Server makes it the 1205 victim.

    The day list and the plan preview hold no transaction state, so a fresh attempt is
    exactly the original request. Finish writes retry inside apply_window_retrying; the
    reads had nothing (Maija 10/13: the WDC day list showed the raw OperationalError 1205
    because the filler inventory read lost to a Daily Programming thread). Anything that is
    not a 1205 propagates on the first attempt."""
    from src.business_logic.services.daily_programming_run import _is_deadlock

    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001 — only a deadlock is retried; the rest propagates
            if not _is_deadlock(exc) or attempt == attempts:
                raise
            delay = 0.5 * attempt + random.uniform(0.1, 0.9)
            logger.warning(
                "fill & finish %s: deadlock victim (1205) on read attempt %d/%d; retrying in %.1fs",
                what,
                attempt,
                attempts,
                delay,
            )
            sleep(delay)


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

            def _read():
                with connect() as conn:
                    return list_programs(conn.cursor(), mid, date)

            return read_retrying(f"day {market} {date}", _read)

        programs = await run_json(f"day {market} {date}", _run)
        if isinstance(programs, JSONResponse):
            return programs
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

            def _read():
                with connect() as conn:
                    return plan_window(conn.cursor(), mid, date, lo, hi, refill=refill)

            r = read_retrying(f"plan {market} {date} {lo}-{hi}", _read)
            return {k: v for k, v in r.items() if not k.startswith("_")}

        return await run_json(f"plan {market} {date} {lo}-{hi}", _run)

    @router.post("/api/master-control/finish/apply")
    async def finish_apply(body: FinishApplyBody):
        mid = _market_id(body.market)

        def _run():
            from browser_automation.etere_direct_client import connect
            from src.business_logic.services.finish_service import apply_window_retrying

            log: list[str] = []
            r = apply_window_retrying(
                connect, mid, body.date, body.lo, body.hi, True, log=log.append, refill=body.refill
            )
            r["log"] = log
            return r

        return await run_json(f"apply {body.market} {body.date} {body.lo}-{body.hi}", _run)

    return router
