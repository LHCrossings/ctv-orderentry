"""
Reports routes: placement-by-week, as-run, and future report pages.
"""

import asyncio
import io
from collections import defaultdict
from datetime import date, timedelta

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.templating import Jinja2Templates

from business_logic.services.order_lookup import (
    language_codes_for,
    language_name,
    summarize,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _week_start(d) -> date:
    if isinstance(d, str):
        d = date.fromisoformat(d)
    elif hasattr(d, "date"):
        d = d.date()
    return d - timedelta(days=d.weekday())


def _fmt_week(ws: date) -> str:
    we = ws + timedelta(days=6)
    return f"{ws.strftime('%b')} {ws.day}–{we.strftime('%b')} {we.day}"


def _mdy_key(s):
    """Sort key for a CONVERT(..., 101) 'mm/dd/yyyy' string."""
    m, d, y = s.split("/")
    return (int(y), int(m), int(d))


def _sql_min_date(*vals):
    vals = [v for v in vals if v]
    return min(vals, key=_mdy_key) if vals else None


def _sql_max_date(*vals):
    vals = [v for v in vals if v]
    return max(vals, key=_mdy_key) if vals else None


def _fetch_placements_sync(contract_id: int):
    from browser_automation.etere_direct_client import connect as _db_connect

    with _db_connect() as conn:
        cur = conn.cursor(as_dict=True)
        cur.execute(
            """
            SELECT ID_CONTRATTITESTATA AS id,
                   COD_CONTRATTO       AS code,
                   DESCRIZIONE         AS description,
                   CONVERT(VARCHAR(10), DATA_INIZIO,  101) AS date_start,
                   CONVERT(VARCHAR(10), DATA_TERMINE, 101) AS date_end
            FROM CONTRATTITESTATA
            WHERE ID_CONTRATTITESTATA = %d
        """
            % contract_id
        )
        hdr = cur.fetchone()
        if not hdr:
            return None, None
        cur.execute(
            """
            SELECT cr.DESCRIZIONE        AS description,
                   cr.OMAGGIO            AS is_bonus,
                   CAST(tp.DATA AS DATE) AS air_date,
                   COUNT(*)              AS spots
            FROM trafficPalinse tpp
            JOIN CONTRATTIRIGHE cr ON tpp.ID_ContrattiRighe = cr.ID_CONTRATTIRIGHE
            JOIN TPALINSE tp       ON tpp.id_tpalinse = tp.ID_TPALINSE
            WHERE cr.ID_CONTRATTITESTATA = %d
            GROUP BY cr.DESCRIZIONE, cr.OMAGGIO, CAST(tp.DATA AS DATE)
            ORDER BY CAST(tp.DATA AS DATE), cr.DESCRIZIONE
        """
            % contract_id
        )
        return dict(hdr), cur.fetchall()


def _build_pivot(hdr: dict, daily_rows) -> dict:
    week_data: dict = defaultdict(lambda: defaultdict(int))
    bonus_set: set = set()
    desc_order: dict = {}

    for row in daily_rows:
        desc = row["description"]
        ws = _week_start(row["air_date"])
        week_data[ws][desc] += row["spots"]
        if row["is_bonus"]:
            bonus_set.add(desc)
        if desc not in desc_order:
            desc_order[desc] = len(desc_order)

    all_weeks = sorted(week_data.keys())
    desc_list = sorted(desc_order, key=lambda d: desc_order[d])
    week_labels = [_fmt_week(ws) for ws in all_weeks]

    rows_out = []
    week_totals = [0] * len(all_weeks)
    grand_total = 0

    for desc in desc_list:
        spots = [week_data[ws].get(desc, 0) for ws in all_weeks]
        total = sum(spots)
        grand_total += total
        for i, v in enumerate(spots):
            week_totals[i] += v
        rows_out.append(
            {
                "description": desc,
                "spots": spots,
                "total": total,
                "is_bonus": desc in bonus_set,
            }
        )

    return {
        "header": hdr,
        "weeks": week_labels,
        "rows": rows_out,
        "week_totals": week_totals,
        "grand_total": grand_total,
    }


def _build_excel(pivot: dict) -> bytes:
    import openpyxl
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter

    hdr = pivot["header"]
    weeks = pivot["weeks"]
    rows = pivot["rows"]
    week_totals = pivot["week_totals"]
    grand_total = pivot["grand_total"]

    NAVY = "1F3864"
    BLUE = "2E75B6"
    LTBLUE = "D6E4F0"
    YELLOW = "FFF2CC"
    GREY = "F2F2F2"
    WHITE = "FFFFFF"

    def fill(c):
        return PatternFill("solid", fgColor=c)

    def bdr():
        s = Side(style="thin")
        return Border(left=s, right=s, top=s, bottom=s)

    ctr = Alignment(horizontal="center", vertical="center")
    lft = Alignment(horizontal="left", vertical="center")

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Placement by Week"

    total_col = len(weeks) + 2
    last_col = get_column_letter(total_col)

    ws.merge_cells(f"A1:{last_col}1")
    c = ws["A1"]
    c.value = f"{hdr.get('code', '')} — {hdr.get('description', '')} · Order Placement by Week"
    c.font = Font(bold=True, size=13, color="FFFFFF")
    c.fill = fill(NAVY)
    c.alignment = ctr
    ws.row_dimensions[1].height = 22
    ws.row_dimensions[2].height = 6

    r = 3
    ws.row_dimensions[r].height = 18

    c = ws.cell(r, 1, "Description")
    c.font = Font(bold=True, color="FFFFFF")
    c.fill = fill(BLUE)
    c.alignment = lft

    for i, wl in enumerate(weeks):
        c = ws.cell(r, i + 2, wl)
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = fill(BLUE)
        c.alignment = ctr

    c = ws.cell(r, total_col, "TOTAL")
    c.font = Font(bold=True, color="FFFFFF")
    c.fill = fill(NAVY)
    c.alignment = ctr

    for ri, row in enumerate(rows):
        dr = r + 1 + ri
        is_bns = row["is_bonus"]
        bg = YELLOW if is_bns else (GREY if ri % 2 == 0 else WHITE)

        c = ws.cell(dr, 1, row["description"])
        c.fill = fill(bg)
        c.alignment = lft
        c.border = bdr()
        if is_bns:
            c.font = Font(italic=True)

        for i, v in enumerate(row["spots"]):
            c = ws.cell(dr, i + 2, v if v else "—")
            c.fill = fill(bg)
            c.alignment = ctr
            c.border = bdr()
            if is_bns and v:
                c.font = Font(italic=True)

        c = ws.cell(dr, total_col, row["total"])
        c.font = Font(bold=True)
        c.fill = fill(LTBLUE)
        c.alignment = ctr
        c.border = bdr()

    tr = r + 1 + len(rows)

    c = ws.cell(tr, 1, "TOTAL")
    c.font = Font(bold=True, color="FFFFFF")
    c.fill = fill(NAVY)
    c.alignment = lft
    c.border = bdr()

    for i, v in enumerate(week_totals):
        c = ws.cell(tr, i + 2, v)
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = fill(NAVY)
        c.alignment = ctr
        c.border = bdr()

    c = ws.cell(tr, total_col, grand_total)
    c.font = Font(bold=True, color="FFFFFF")
    c.fill = fill(NAVY)
    c.alignment = ctr
    c.border = bdr()

    ws.column_dimensions["A"].width = 38
    for i in range(len(weeks)):
        ws.column_dimensions[get_column_letter(i + 2)].width = 14
    ws.column_dimensions[last_col].width = 8
    ws.freeze_panes = "B4"

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------


def _build_asrun_excel(payload: dict) -> bytes:
    """As-run-by-contract → one flat, filterable sheet.

    Deliberately flat rather than a replica of the page's per-market tables:
    a Market column with an autofilter is what makes the export useful in
    Excel. Styling follows _build_excel above, including BNS = yellow italic.
    """
    import openpyxl
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter

    NAVY = "1F3864"
    BLUE = "2E75B6"
    LTBLUE = "D6E4F0"
    YELLOW = "FFF2CC"
    GREY = "F2F2F2"
    WHITE = "FFFFFF"

    def fill(c):
        return PatternFill("solid", fgColor=c)

    def bdr():
        s = Side(style="thin")
        return Border(left=s, right=s, top=s, bottom=s)

    ctr = Alignment(horizontal="center", vertical="center")
    lft = Alignment(horizontal="left", vertical="center")

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "As-Run"

    COLS = ["Market", "Date", "Time", "Spot Code", "Title", "Type", "Rate"]
    last_col = get_column_letter(len(COLS))

    ws.merge_cells(f"A1:{last_col}1")
    c = ws["A1"]
    c.value = (
        f"{payload['contract_code']} — {payload['contract_description'] or ''} · As-Run Report"
    )
    c.font = Font(bold=True, size=13, color="FFFFFF")
    c.fill = fill(NAVY)
    c.alignment = ctr
    ws.row_dimensions[1].height = 22

    ws.merge_cells(f"A2:{last_col}2")
    c = ws["A2"]
    n_mkts = len([m for m in payload["markets"] if m["count"]])
    c.value = (
        f"{payload['date_from']} — {payload['date_to']}  ·  "
        f"{payload['total']} airing(s) across {n_mkts} market(s)"
    )
    c.font = Font(size=10, color="FFFFFF")
    c.fill = fill(BLUE)
    c.alignment = ctr
    ws.row_dimensions[3].height = 6

    hr = 4
    for i, label in enumerate(COLS, start=1):
        c = ws.cell(hr, i, label)
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = fill(BLUE)
        c.alignment = lft if label in ("Title", "Spot Code") else ctr
        c.border = bdr()

    r = hr
    total_rate = 0.0
    ri = 0
    for mkt in payload["markets"]:
        for a in mkt["airings"]:
            r += 1
            is_bns = a["type"] == "BNS"
            bg = YELLOW if is_bns else (GREY if ri % 2 == 0 else WHITE)
            ri += 1
            rate = float(a["rate"] or 0)
            total_rate += rate

            # Real date/number types so Excel can sort and sum them.
            try:
                d_val = date.fromisoformat(a["date"])
            except (ValueError, TypeError):
                d_val = a["date"]

            values = [
                mkt["market"],
                d_val,
                a["time"],
                a["spot_code"],
                a["spot_title"],
                a["type"],
                rate,
            ]
            for i, v in enumerate(values, start=1):
                c = ws.cell(r, i, v)
                c.fill = fill(bg)
                c.border = bdr()
                c.alignment = lft if i in (4, 5) else ctr
                if is_bns:
                    c.font = Font(italic=True)
                if i == 2:
                    c.number_format = "M/D/YY"
                if i == 7:
                    c.number_format = "$#,##0.00"

    if r > hr:
        ws.auto_filter.ref = f"A{hr}:{last_col}{r}"

    tr = r + 1
    c = ws.cell(tr, 1, "TOTAL")
    c.font = Font(bold=True, color="FFFFFF")
    c.fill = fill(NAVY)
    c.alignment = lft
    c.border = bdr()
    for i in range(2, len(COLS) + 1):
        c = ws.cell(tr, i)
        c.fill = fill(NAVY)
        c.border = bdr()
    c = ws.cell(tr, 2, payload["total"])
    c.font = Font(bold=True, color="FFFFFF")
    c.fill = fill(NAVY)
    c.alignment = ctr
    c.border = bdr()
    c = ws.cell(tr, len(COLS), total_rate)
    c.font = Font(bold=True, color="FFFFFF")
    c.fill = fill(NAVY)
    c.alignment = ctr
    c.border = bdr()
    c.number_format = "$#,##0.00"

    for col, width in zip("ABCDEFG", (10, 11, 10, 18, 42, 8, 12)):
        ws.column_dimensions[col].width = width
    ws.freeze_panes = f"A{hr + 1}"

    # Per-market summary alongside, so the counts on the page are in the file too.
    ws2 = wb.create_sheet("Summary")
    for i, label in enumerate(("Market", "Airings"), start=1):
        c = ws2.cell(1, i, label)
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = fill(BLUE)
        c.alignment = ctr
        c.border = bdr()
    sr = 1
    for mkt in payload["markets"]:
        if not mkt["count"]:
            continue
        sr += 1
        for i, v in enumerate((mkt["market"], mkt["count"]), start=1):
            c = ws2.cell(sr, i, v)
            c.alignment = ctr
            c.border = bdr()
            c.fill = fill(LTBLUE if i == 2 else WHITE)
    sr += 1
    for i, v in enumerate(("TOTAL", payload["total"]), start=1):
        c = ws2.cell(sr, i, v)
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = fill(NAVY)
        c.alignment = ctr
        c.border = bdr()
    ws2.column_dimensions["A"].width = 12
    ws2.column_dimensions["B"].width = 10

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def build_reports_router(templates: Jinja2Templates) -> APIRouter:
    router = APIRouter()

    @router.get("/reports", response_class=HTMLResponse)
    async def reports_hub(request: Request):
        return templates.TemplateResponse(request, "reports.html")

    @router.get("/reports/placement-by-week", response_class=HTMLResponse)
    async def placement_by_week_page(request: Request):
        return templates.TemplateResponse(request, "reports/placement_by_week.html")

    @router.get("/api/reports/placement-by-week/{contract_id}")
    async def placement_by_week_data(contract_id: int):
        try:
            hdr, daily = await asyncio.get_running_loop().run_in_executor(
                None, lambda: _fetch_placements_sync(contract_id)
            )
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))
        if hdr is None:
            raise HTTPException(status_code=404, detail="Contract not found")
        return JSONResponse(_build_pivot(hdr, daily))

    @router.get("/api/reports/placement-by-week/{contract_id}/excel")
    async def placement_by_week_excel(contract_id: int):
        try:
            hdr, daily = await asyncio.get_running_loop().run_in_executor(
                None, lambda: _fetch_placements_sync(contract_id)
            )
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))
        if hdr is None:
            raise HTTPException(status_code=404, detail="Contract not found")
        pivot = _build_pivot(hdr, daily)
        excel_bytes = _build_excel(pivot)
        filename = f"{hdr['code'].replace(' ', '_')}_PlacementByWeek.xlsx"
        return StreamingResponse(
            io.BytesIO(excel_bytes),
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    # ── As-Run Report ────────────────────────────────────────────────────────

    MARKET_MAP = {
        1: "NYC",
        2: "CMP",
        3: "HOU",
        4: "SFO",
        5: "SEA",
        6: "LAX",
        7: "CVC",
        8: "WDC",
        9: "MMT",
        10: "DAL",
    }
    FPS = 29.97

    def _frames_to_time(frames: int) -> str:
        total_sec = round(frames / FPS)
        h = total_sec // 3600
        m = (total_sec % 3600) // 60
        s = total_sec % 60
        return f"{h:02d}:{m:02d}:{s:02d}"

    def _fetch_asrun_sync(spot: str, date_from: date, date_to: date, market_ids: list[int]):
        from browser_automation.etere_direct_client import connect as _db_connect

        results = []
        like_pat = f"%{spot}%"
        with _db_connect() as conn:
            cur = conn.cursor(as_dict=True)
            for mkt_id in market_ids:
                cur.execute(
                    """
                    SELECT CAST(DATA AS DATE) AS air_date, ORA, TITLE
                    FROM TPALINSE
                    WHERE COD_USER = %s
                      AND DATA >= %s
                      AND DATA <= %s
                      AND TITLE LIKE %s
                    ORDER BY DATA, ORA
                    """,
                    (mkt_id, date_from.isoformat(), date_to.isoformat(), like_pat),
                )
                rows = cur.fetchall()
                airings = [
                    {
                        "date": str(r["air_date"]),
                        "time": _frames_to_time(r["ORA"]),
                        "title": r["TITLE"] or "",
                    }
                    for r in rows
                ]
                results.append(
                    {
                        "market": MARKET_MAP.get(mkt_id, str(mkt_id)),
                        "count": len(airings),
                        "airings": airings,
                    }
                )
        return results

    @router.get("/reports/as-run", response_class=HTMLResponse)
    async def as_run_page(request: Request):
        return templates.TemplateResponse(request, "reports/as_run.html")

    @router.get("/api/reports/as-run")
    async def as_run_data(
        spot: str = Query(...),
        date_from: str = Query(...),
        date_to: str = Query(...),
        markets: str = Query(...),
    ):
        try:
            d_from = date.fromisoformat(date_from)
            d_to = date.fromisoformat(date_to)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid date format")

        try:
            mkt_ids = [int(x) for x in markets.split(",") if x.strip()]
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid market IDs")

        try:
            results = await asyncio.get_running_loop().run_in_executor(
                None, lambda: _fetch_asrun_sync(spot, d_from, d_to, mkt_ids)
            )
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))

        return JSONResponse(
            {
                "spot_query": spot,
                "date_from": date_from,
                "date_to": date_to,
                "results": results,
                "total": sum(m["count"] for m in results),
            }
        )

    async def _asrun_contract_payload(contract_id: int, date_from: str, date_to: str) -> dict:
        """Shared by the JSON view and the Excel export so they can't drift."""
        try:
            d_from = date.fromisoformat(date_from)
            d_to = date.fromisoformat(date_to)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid date format")

        def _run():
            from browser_automation.etere_direct_client import connect as _db_connect

            with _db_connect() as conn:
                cur = conn.cursor(as_dict=True)
                cur.execute(
                    """
                    SELECT COD_CONTRATTO AS code, DESCRIZIONE AS description
                    FROM CONTRATTITESTATA
                    WHERE ID_CONTRATTITESTATA = %s
                """,
                    (contract_id,),
                )
                hdr = cur.fetchone()
                if not hdr:
                    return None, []

                cur.execute(
                    """
                    SELECT
                        tp.COD_USER                        AS market_id,
                        CAST(tp.DATA AS DATE)              AS air_date,
                        tp.ORA                             AS time_frames,
                        ISNULL(f.COD_PROGRA, tp.TITLE)     AS spot_code,
                        ISNULL(f.DESCRIZIO, '')            AS spot_title,
                        ISNULL(cr.IMPORTO, 0)              AS rate
                    FROM TPALINSE tp
                    JOIN trafficPalinse tpa
                        ON tpa.id_tpalinse = tp.ID_TPALINSE
                    JOIN CONTRATTIRIGHE cr
                        ON cr.ID_CONTRATTIRIGHE = tpa.id_contrattirighe
                    JOIN CONTRATTITESTATA ct
                        ON ct.ID_CONTRATTITESTATA = cr.ID_CONTRATTITESTATA
                    LEFT JOIN FILMATI f
                        ON f.ID_FILMATI = tp.ID_FILMATI AND tp.ID_FILMATI > 0
                    WHERE ct.ID_CONTRATTITESTATA = %s
                      AND tp.DATA >= %s
                      AND tp.DATA <= %s
                      AND tp.LIVELLO = 0
                    ORDER BY tp.COD_USER, tp.DATA, tp.ORA
                """,
                    (contract_id, d_from.isoformat(), d_to.isoformat()),
                )
                rows = cur.fetchall()
            return hdr, rows

        try:
            hdr, rows = await asyncio.get_running_loop().run_in_executor(None, _run)
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))

        if hdr is None:
            raise HTTPException(status_code=404, detail="Contract not found")

        by_market = {}
        for r in rows:
            mid = r["market_id"]
            mcode = MARKET_MAP.get(mid, str(mid))
            if mcode not in by_market:
                by_market[mcode] = []
            rate = float(r["rate"] or 0)
            by_market[mcode].append(
                {
                    "date": str(r["air_date"]),
                    "time": _frames_to_time(r["time_frames"]),
                    "spot_code": r["spot_code"] or "",
                    "spot_title": r["spot_title"] or "",
                    "type": "BNS" if rate == 0 else "COM",
                    "rate": rate,
                }
            )

        markets = [
            {"market": m, "airings": a, "count": len(a)} for m, a in sorted(by_market.items())
        ]
        return {
            "contract_code": hdr["code"],
            "contract_description": hdr["description"],
            "date_from": date_from,
            "date_to": date_to,
            "markets": markets,
            "total": len(rows),
        }

    @router.get("/api/reports/as-run-by-contract")
    async def as_run_by_contract(
        contract_id: int = Query(...),
        date_from: str = Query(...),
        date_to: str = Query(...),
    ):
        return JSONResponse(await _asrun_contract_payload(contract_id, date_from, date_to))

    @router.get("/api/reports/as-run-by-contract/excel")
    async def as_run_by_contract_excel(
        contract_id: int = Query(...),
        date_from: str = Query(...),
        date_to: str = Query(...),
    ):
        payload = await _asrun_contract_payload(contract_id, date_from, date_to)
        excel_bytes = await asyncio.get_running_loop().run_in_executor(
            None, lambda: _build_asrun_excel(payload)
        )
        safe_code = "".join(
            ch if ch.isalnum() or ch in "-_" else "_" for ch in payload["contract_code"]
        )
        filename = f"{safe_code}_AsRun_{date_from}_{date_to}.xlsx"
        return StreamingResponse(
            io.BytesIO(excel_bytes),
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    @router.get("/api/reports/spot-search")
    async def spot_search(q: str = Query("")):
        if len(q) < 2:
            return JSONResponse([])

        def _run():
            from browser_automation.etere_direct_client import connect as _db_connect

            with _db_connect() as conn:
                cur = conn.cursor(as_dict=True)
                term = f"%{q.upper()}%"
                cur.execute(
                    """
                    SELECT TOP 25 ID_FILMATI AS id, COD_PROGRA AS code,
                           DESCRIZIO AS title, DURATA AS durata
                    FROM FILMATI
                    WHERE TIPO = 'T'
                      AND DURATA <= 1800
                      AND (UPPER(COD_PROGRA) LIKE %s OR UPPER(DESCRIZIO) LIKE %s)
                    ORDER BY COD_PROGRA
                """,
                    (term, term),
                )
                rows = cur.fetchall()
            for r in rows:
                r["duration_sec"] = round(r["durata"] / 30) if r["durata"] else 0
            return rows

        try:
            rows = await asyncio.get_running_loop().run_in_executor(None, _run)
            return JSONResponse(rows)
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))

    # ── Order Lookup (AE self-service: is it in, is it scheduled, which creatives) ──

    _LOOKUP_SELECT = """
        SELECT ct.ID_CONTRATTITESTATA                        AS id,
               ct.COD_CONTRATTO                              AS code,
               ct.DESCRIZIONE                                AS description,
               ISNULL(ct.CUSTOMERREF, '')                    AS customer_ref,
               ISNULL(c.RAG_SOCIAL, '')                      AS customer,
               ISNULL(a.RAG_SOCIAL, '')                      AS agency,
               LTRIM(RTRIM(ISNULL(ae.Nome, '') + ' ' + ISNULL(ae.RAG_SOCIAL, ''))) AS ae,
               CONVERT(VARCHAR(10), ct.DATA_INIZIO,       101) AS date_start,
               CONVERT(VARCHAR(10), ct.DATA_TERMINE,      101) AS date_end,
               CONVERT(VARCHAR(10), ct.DATA_ACQUISIZIONE, 101) AS entered_on,
               ISNULL(CAST(ct.NOTE AS nvarchar(max)), '')    AS notes,
               (SELECT ISNULL(SUM(r.N_PASSAGGI), 0) FROM CONTRATTIRIGHE r
                 WHERE r.ID_CONTRATTITESTATA = ct.ID_CONTRATTITESTATA) AS ordered,
               (SELECT COUNT(*) FROM trafficPalinse tp
                 JOIN CONTRATTIRIGHE r ON r.ID_CONTRATTIRIGHE = tp.ID_ContrattiRighe
                 JOIN TPALINSE t ON t.ID_TPALINSE = tp.id_tpalinse
                 WHERE r.ID_CONTRATTITESTATA = ct.ID_CONTRATTITESTATA AND t.LIVELLO = 0) AS scheduled,
               (SELECT COUNT(*) FROM trafficPalinse tp
                 JOIN CONTRATTIRIGHE r ON r.ID_CONTRATTIRIGHE = tp.ID_ContrattiRighe
                 JOIN TPALINSE t ON t.ID_TPALINSE = tp.id_tpalinse
                 WHERE r.ID_CONTRATTITESTATA = ct.ID_CONTRATTITESTATA AND t.LIVELLO = 0
                   AND t.ID_FILMATI > 0) AS with_creative
        FROM CONTRATTITESTATA ct
        LEFT JOIN ANAGRAF c  ON c.ID_ANAGRAF  = ct.COMMITTENTE
        LEFT JOIN ANAGRAF a  ON a.ID_ANAGRAF  = ct.AGENZIA
        LEFT JOIN ANAGRAF ae ON ae.ID_ANAGRAF = ct.AGENTE1
    """

    # One token must match ANY of these; every token must match (AND across tokens).
    _LOOKUP_HAYSTACK = [
        "UPPER(ct.COD_CONTRATTO)",
        "UPPER(ct.DESCRIZIONE)",
        "UPPER(ISNULL(ct.CUSTOMERREF, ''))",
        "UPPER(ISNULL(CAST(ct.NOTE AS nvarchar(max)), ''))",
        "UPPER(ISNULL(c.RAG_SOCIAL, ''))",
        "UPPER(ISNULL(a.RAG_SOCIAL, ''))",
        "UPPER(ISNULL(ae.RAG_SOCIAL, ''))",
    ]

    def _like_token(tok: str) -> str:
        # Escape LIKE metacharacters so an estimate like "06-MD10" or "[x]" is literal.
        tok = tok.replace("[", "[[]").replace("%", "[%]").replace("_", "[_]")
        return f"%{tok.upper()}%"

    def _lookup_where(q: str):
        tokens = [t for t in q.replace(",", " ").split() if t]
        if not tokens:
            return "WHERE ct.DATA_TERMINE >= CAST(GETDATE() AS DATE)", ()
        clauses, params = [], []
        for tok in tokens:
            like = _like_token(tok)
            ors = [f"{col} LIKE %s" for col in _LOOKUP_HAYSTACK]
            params.extend([like] * len(_LOOKUP_HAYSTACK))
            ors.append(
                "EXISTS (SELECT 1 FROM CONTRATTIRIGHE r2 WHERE r2.ID_CONTRATTITESTATA = ct.ID_CONTRATTITESTATA "
                "AND UPPER(r2.DESCRIZIONE) LIKE %s)"
            )
            params.append(like)
            if tok.isdigit():
                ors.append("ct.ID_CONTRATTITESTATA = %s")
                params.append(int(tok))
            codes = sorted(language_codes_for(tok))
            if codes:
                # "vietnamese" is nowhere on an Admerasia contract; the entry-time
                # language catalog is where that answer lives.
                ors.append(
                    "EXISTS (SELECT 1 FROM CTV_LineLanguage ll "
                    "JOIN CONTRATTIRIGHE r3 ON r3.ID_CONTRATTIRIGHE = ll.ID_CONTRATTIRIGHE "
                    "WHERE r3.ID_CONTRATTITESTATA = ct.ID_CONTRATTITESTATA AND ll.LANG IN ("
                    + ", ".join(["%s"] * len(codes))
                    + "))"
                )
                params.extend(codes)
            clauses.append("(" + " OR ".join(ors) + ")")
        return "WHERE " + " AND ".join(clauses), tuple(params)

    def _lookup_languages(cur, contract_ids):
        """{contract_id: [language name, ...]} from the entry-time catalog."""
        ids = [int(i) for i in contract_ids]
        if not ids:
            return {}
        cur.execute(
            "SELECT r.ID_CONTRATTITESTATA AS cid, ll.LANG AS lang "
            "FROM CTV_LineLanguage ll JOIN CONTRATTIRIGHE r ON r.ID_CONTRATTIRIGHE = ll.ID_CONTRATTIRIGHE "
            "WHERE r.ID_CONTRATTITESTATA IN (" + ", ".join(["%s"] * len(ids)) + ") "
            "GROUP BY r.ID_CONTRATTITESTATA, ll.LANG",
            tuple(ids),
        )
        out: dict = {}
        for r in cur.fetchall():
            out.setdefault(r["cid"], set()).add(language_name(r["lang"]))
        return {k: sorted(v) for k, v in out.items()}

    @router.get("/reports/order-lookup", response_class=HTMLResponse)
    async def order_lookup_page(request: Request):
        return templates.TemplateResponse(request, "reports/order_lookup.html")

    @router.get("/api/reports/order-lookup/search")
    async def order_lookup_search(q: str = Query("")):
        q = q.strip()
        if 0 < len(q) < 2:
            return JSONResponse({"query": q, "results": [], "mode": "search"})

        def _run():
            from browser_automation.etere_direct_client import connect as _db_connect

            where, params = _lookup_where(q)
            sql = _LOOKUP_SELECT.replace("SELECT ct.", "SELECT TOP 50 ct.", 1) + where
            order = " ORDER BY "
            if q.isdigit():  # an exact contract ID outranks codes that merely contain the digits
                order += "CASE WHEN ct.ID_CONTRATTITESTATA = %s THEN 0 ELSE 1 END, "
                params = params + (int(q),)
            sql += order + "ct.DATA_INIZIO DESC, ct.ID_CONTRATTITESTATA DESC"
            with _db_connect() as conn:
                cur = conn.cursor(as_dict=True)
                cur.execute(sql, params)
                rows = cur.fetchall()
                langs = _lookup_languages(cur, [r["id"] for r in rows])
            for r in rows:
                r["languages"] = langs.get(r["id"], [])
            return rows

        try:
            rows = await asyncio.get_running_loop().run_in_executor(None, _run)
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))
        return JSONResponse({"query": q, "mode": "search" if q else "current", "results": rows})

    @router.get("/api/reports/order-lookup/{contract_id}")
    async def order_lookup_detail(contract_id: int):
        def _run():
            from browser_automation.etere_direct_client import connect as _db_connect

            with _db_connect() as conn:
                cur = conn.cursor(as_dict=True)
                cur.execute(_LOOKUP_SELECT + " WHERE ct.ID_CONTRATTITESTATA = %s", (contract_id,))
                hdr = cur.fetchone()
                if not hdr:
                    return None, [], []
                cur.execute(
                    """
                    SELECT r.ID_CONTRATTIRIGHE AS id,
                           r.DESCRIZIONE       AS description,
                           r.COD_USER          AS market_id,
                           CONVERT(VARCHAR(10), ISNULL(r.DATESTART, r.DATA_INIZIO), 101) AS date_start,
                           CONVERT(VARCHAR(10), ISNULL(r.DATEEND,   r.DATA_FINE),   101) AS date_end,
                           r.N_PASSAGGI        AS ordered,
                           r.OMAGGIO           AS is_bonus,
                           r.DURATA            AS durata,
                           ll.LANG             AS language,
                           ISNULL(x.scheduled, 0)       AS scheduled,
                           ISNULL(x.with_creative, 0)   AS with_creative,
                           ISNULL(x.aired, 0)           AS aired,
                           CONVERT(VARCHAR(10), x.first_air, 101)        AS first_air,
                           CONVERT(VARCHAR(10), x.last_air, 101)         AS last_air,
                           CONVERT(VARCHAR(10), x.first_unassigned, 101) AS first_unassigned
                    FROM CONTRATTIRIGHE r
                    OUTER APPLY (
                        SELECT COUNT(*) AS scheduled,
                               SUM(CASE WHEN t.ID_FILMATI > 0 THEN 1 ELSE 0 END) AS with_creative,
                               SUM(CASE WHEN t.STATUS IN ('Q', 'D') THEN 1 ELSE 0 END) AS aired,
                               MIN(t.DATA) AS first_air,
                               MAX(t.DATA) AS last_air,
                               MIN(CASE WHEN t.ID_FILMATI > 0 THEN NULL ELSE t.DATA END) AS first_unassigned
                        FROM trafficPalinse tp
                        JOIN TPALINSE t ON t.ID_TPALINSE = tp.id_tpalinse
                        WHERE tp.ID_ContrattiRighe = r.ID_CONTRATTIRIGHE AND t.LIVELLO = 0
                    ) x
                    LEFT JOIN CTV_LineLanguage ll ON ll.ID_CONTRATTIRIGHE = r.ID_CONTRATTIRIGHE
                    WHERE r.ID_CONTRATTITESTATA = %s
                    ORDER BY r.ID_CONTRATTIRIGHE
                    """,
                    (contract_id,),
                )
                lines = cur.fetchall()
                cur.execute(
                    """
                    SELECT CASE WHEN t.ID_FILMATI > 0 THEN t.ID_FILMATI ELSE 0 END AS id,
                           ISNULL(f.COD_PROGRA, '')  AS isci,
                           ISNULL(f.DESCRIZIO, '')   AS title,
                           ISNULL(f.DURATA, 0)       AS durata,
                           t.COD_USER                AS market_id,
                           COUNT(*)                  AS spots,
                           SUM(CASE WHEN t.STATUS IN ('Q', 'D') THEN 1 ELSE 0 END) AS aired,
                           CONVERT(VARCHAR(10), MIN(t.DATA), 101) AS first_air,
                           CONVERT(VARCHAR(10), MAX(t.DATA), 101) AS last_air
                    FROM trafficPalinse tp
                    JOIN CONTRATTIRIGHE r ON r.ID_CONTRATTIRIGHE = tp.ID_ContrattiRighe
                    JOIN TPALINSE t       ON t.ID_TPALINSE = tp.id_tpalinse
                    LEFT JOIN FILMATI f   ON f.ID_FILMATI = t.ID_FILMATI AND t.ID_FILMATI > 0
                    WHERE r.ID_CONTRATTITESTATA = %s AND t.LIVELLO = 0
                    GROUP BY CASE WHEN t.ID_FILMATI > 0 THEN t.ID_FILMATI ELSE 0 END,
                             f.COD_PROGRA, f.DESCRIZIO, f.DURATA, t.COD_USER
                    """,
                    (contract_id,),
                )
                return hdr, lines, cur.fetchall()

        try:
            hdr, lines, cre_rows = await asyncio.get_running_loop().run_in_executor(None, _run)
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))
        if hdr is None:
            raise HTTPException(status_code=404, detail="Contract not found")

        for ln in lines:
            ln["market"] = MARKET_MAP.get(ln.pop("market_id"), "?")
            ln["language_name"] = language_name(ln.get("language"))
            ln["duration_sec"] = round(ln["durata"] / 30) if ln.get("durata") else 0
            ln["is_bonus"] = bool(ln.get("is_bonus"))

        # Fold the per-market creative rows into one row per creative.
        creatives: dict = {}
        for r in cre_rows:
            c = creatives.setdefault(
                r["id"],
                {
                    "id": r["id"],
                    "isci": r["isci"],
                    "title": r["title"],
                    "duration_sec": round(r["durata"] / 30) if r.get("durata") else 0,
                    "spots": 0,
                    "aired": 0,
                    "first_air": None,
                    "last_air": None,
                    "markets": set(),
                    "assigned": r["id"] > 0,
                },
            )
            c["spots"] += r["spots"]
            c["aired"] += r["aired"] or 0
            c["first_air"] = _sql_min_date(c["first_air"], r["first_air"])
            c["last_air"] = _sql_max_date(c["last_air"], r["last_air"])
            c["markets"].add(MARKET_MAP.get(r["market_id"], "?"))
        creative_list = sorted(creatives.values(), key=lambda c: (not c["assigned"], c["isci"]))
        for c in creative_list:
            c["markets"] = sorted(c["markets"])

        hdr["first_air"] = _sql_min_date(*[ln.get("first_air") for ln in lines]) if lines else None
        hdr["last_air"] = _sql_max_date(*[ln.get("last_air") for ln in lines]) if lines else None
        hdr["first_unassigned"] = (
            _sql_min_date(*[ln.get("first_unassigned") for ln in lines]) if lines else None
        )
        summary = summarize(hdr, lines, [c for c in creative_list if c["assigned"]])
        return JSONResponse(
            {"header": hdr, "summary": summary, "lines": lines, "creatives": creative_list}
        )

    return router
