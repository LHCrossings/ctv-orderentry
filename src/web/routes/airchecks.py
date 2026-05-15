import asyncio
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

_PT = ZoneInfo("America/Los_Angeles")

# Etere stores air times in each market's local timezone
MARKET_TZ = {
    "NYC": ZoneInfo("America/New_York"),
    "WDC": ZoneInfo("America/New_York"),
    "MMT": ZoneInfo("America/New_York"),
    "CMP": ZoneInfo("America/Chicago"),
    "HOU": ZoneInfo("America/Chicago"),
    "DAL": ZoneInfo("America/Chicago"),
    "SFO": ZoneInfo("America/Los_Angeles"),
    "SEA": ZoneInfo("America/Los_Angeles"),
    "LAX": ZoneInfo("America/Los_Angeles"),
    "CVC": ZoneInfo("America/Los_Angeles"),
}

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
                f.COD_PROGRA   AS isci_code,
                tp.COD_USER    AS market_id,
                tp.DATA        AS air_date,
                tp.ORA         AS air_ora,
                dbo.tcFrames2Msec(dbo.getVideoStandard(tp.COD_USER), tp.ORA) AS air_ms,
                cr.DURATA      AS duration_frames,
                ct.CUSTOMERREF AS customer_ref,
                a.RAG_SOCIAL   AS client_name,
                ROW_NUMBER() OVER (
                    PARTITION BY f.COD_PROGRA, tp.COD_USER
                    ORDER BY tp.DATA, tp.ORA
                ) AS rn
            FROM TPALINSE tp
            JOIN trafficPalinse tpa  ON tpa.id_tpalinse      = tp.ID_TPALINSE
            JOIN CONTRATTIRIGHE cr   ON cr.ID_CONTRATTIRIGHE = tpa.id_contrattirighe
            JOIN FILMATI f           ON f.ID_FILMATI          = tp.ID_FILMATI
            JOIN CONTRATTITESTATA ct ON ct.ID_CONTRATTITESTATA = cr.ID_CONTRATTITESTATA
            LEFT JOIN ANAGRAF a      ON a.ID_ANAGRAF           = ct.COMMITTENTE
            WHERE cr.ID_CONTRATTITESTATA = %d
              AND DATEADD(MILLISECOND, dbo.tcFrames2Msec(dbo.getVideoStandard(tp.COD_USER), tp.ORA) %% 86400000,
                          CAST(tp.DATA AS DATETIME)) >= DATEADD(MINUTE, 10, GETDATE())
              AND f.COD_PROGRA IS NOT NULL
              AND f.COD_PROGRA != ''
              AND tp.NEWTYPE = 'COM'
        )
        SELECT isci_code, market_id, air_date, air_ora, air_ms, duration_frames, customer_ref, client_name
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

            air_date  = row["air_date"]
            if hasattr(air_date, "date"):
                air_date = air_date.date()
            air_secs   = int(row["air_ms"]) / 1000
            market_tz  = MARKET_TZ.get(network, _PT)
            air_dt_loc = datetime.combine(air_date, datetime.min.time(), tzinfo=market_tz) + timedelta(seconds=air_secs)
            air_dt     = air_dt_loc.astimezone(_PT).replace(tzinfo=None)  # naive PT for Datamover

            tz_abbr   = air_dt_loc.strftime("%Z")
            h12       = air_dt_loc.hour % 12 or 12
            ampm      = "AM" if air_dt_loc.hour < 12 else "PM"
            local_display = (
                f"{air_dt_loc.month}/{air_dt_loc.day} at "
                f"{h12}:{air_dt_loc.strftime('%M:%S')}{ampm} {tz_abbr}"
            )

            dur_secs = max(5, int(row["duration_frames"] or 0) // 30)

            client_name  = (row.get("client_name") or "").strip() or None
            customer_ref = (row.get("customer_ref") or "").strip() or None

            results.append({
                "isci_code":                row["isci_code"].strip(),
                "air_ora":                  int(row["air_ora"]),
                "client_name":              client_name,
                "customer_ref":             customer_ref,
                "network":                  network,
                "air_datetime":             air_dt.isoformat(),
                "air_datetime_local":       local_display,
                "duration_seconds":         dur_secs,
                "capture_start":            (air_dt - timedelta(seconds=PRE_ROLL_SECS)).isoformat(),
                "capture_duration_seconds": dur_secs + PRE_ROLL_SECS + POST_ROLL_SECS,
            })

    return results


AGENT_URL = "http://100.102.206.113:8765"


def _fetch_agent_status() -> dict:
    import json as _json
    import urllib.request
    try:
        with urllib.request.urlopen(f"{AGENT_URL}/captures", timeout=3) as resp:
            caps = _json.loads(resp.read())
        return {
            "complete":  sum(1 for c in caps if c.get("status") == "complete"),
            "scheduled": sum(1 for c in caps if c.get("status") in ("scheduled", "pending", "recording")),
            "unreachable": False,
        }
    except Exception:
        return {"complete": 0, "scheduled": 0, "unreachable": True}


def build_airchecks_router(templates: Jinja2Templates) -> APIRouter:
    router = APIRouter()

    @router.get("/api/airchecks/status")
    async def aircheck_status():
        status = await asyncio.get_running_loop().run_in_executor(None, _fetch_agent_status)
        return JSONResponse(status)

    @router.get("/airchecks", response_class=HTMLResponse)
    async def airchecks(request: Request):
        return templates.TemplateResponse(request, "airchecks.html")

    @router.get("/api/airchecks/etere-spots")
    async def etere_spots(contract_id: int):
        import traceback
        try:
            spots = await asyncio.get_running_loop().run_in_executor(
                None, lambda: _fetch_etere_spots(contract_id)
            )
        except Exception as exc:
            traceback.print_exc()
            raise HTTPException(status_code=500, detail=str(exc))
        return JSONResponse(spots)

    return router
