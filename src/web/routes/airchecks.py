import asyncio
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

_ET = ZoneInfo("America/New_York")
_PT = ZoneInfo("America/Los_Angeles")

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

# market_id (COD_USER) → network label
MARKET_NAMES = {
    1: "NYC", 2: "CMP", 3: "HOU", 4: "SFO",
    5: "SEA", 6: "LAX", 7: "CVC", 8: "WDC",
    9: "MMT", 10: "DAL",
}

PRE_ROLL_SECS  = 20
POST_ROLL_SECS = 20


def _fetch_etere_spots(contract_id: int) -> list[dict]:
    from browser_automation.etere_direct_client import connect as _db_connect

    sql = """
        WITH ranked AS (
            SELECT
                f.COD_PROGRA  AS isci_code,
                tp.COD_USER   AS market_id,
                tp.DATA       AS air_date,
                tp.ORA        AS air_ora,
                cr.DURATA     AS duration_frames,
                ROW_NUMBER() OVER (
                    PARTITION BY f.COD_PROGRA, tp.COD_USER
                    ORDER BY tp.DATA, tp.ORA
                ) AS rn
            FROM TPALINSE tp
            JOIN trafficPalinse tpa ON tpa.id_tpalinse      = tp.ID_TPALINSE
            JOIN CONTRATTIRIGHE cr  ON cr.ID_CONTRATTIRIGHE = tpa.id_contrattirighe
            JOIN FILMATI f          ON f.ID_FILMATI          = tp.ID_FILMATI
            WHERE cr.ID_CONTRATTITESTATA = %d
              AND DATEADD(SECOND, tp.ORA/30, CAST(tp.DATA AS DATETIME)) >= DATEADD(HOUR, 4, GETDATE())
              AND f.COD_PROGRA IS NOT NULL
              AND f.COD_PROGRA != ''
              AND tp.NEWTYPE = 'COM'
        )
        SELECT isci_code, market_id, air_date, air_ora, duration_frames
        FROM ranked
        WHERE rn = 1
        ORDER BY isci_code, market_id
    """ % contract_id

    results = []
    with _db_connect() as conn:
        cur = conn.cursor(as_dict=True)
        cur.execute(sql)
        for row in cur.fetchall():
            network = MARKET_NAMES.get(int(row["market_id"]), f"MKT{row['market_id']}")

            # Etere stores times in Eastern Time — convert to Pacific for Datamover
            air_date  = row["air_date"]
            if hasattr(air_date, "date"):
                air_date = air_date.date()
            air_secs  = int(row["air_ora"]) // 30
            air_dt_et = datetime.combine(air_date, datetime.min.time(), tzinfo=_ET) + timedelta(seconds=air_secs)
            air_dt    = air_dt_et.astimezone(_PT).replace(tzinfo=None)  # naive PT for Datamover

            dur_secs = max(5, int(row["duration_frames"] or 0) // 30)

            results.append({
                "isci_code":              row["isci_code"].strip(),
                "network":                network,
                "air_datetime":           air_dt.isoformat(),
                "duration_seconds":       dur_secs,
                "capture_start":          (air_dt - timedelta(seconds=PRE_ROLL_SECS)).isoformat(),
                "capture_duration_seconds": dur_secs + PRE_ROLL_SECS + POST_ROLL_SECS,
            })

    return results


def build_airchecks_router(templates: Jinja2Templates) -> APIRouter:
    router = APIRouter()

    @router.get("/airchecks", response_class=HTMLResponse)
    async def airchecks(request: Request):
        return templates.TemplateResponse(request, "airchecks.html")

    @router.get("/api/airchecks/etere-spots")
    async def etere_spots(contract_id: int):
        try:
            spots = await asyncio.get_running_loop().run_in_executor(
                None, lambda: _fetch_etere_spots(contract_id)
            )
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))
        return JSONResponse(spots)

    return router
