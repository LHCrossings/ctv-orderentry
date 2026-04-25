"""
Order queue routes: list, upload, move-to-used, history, restore, detail.
"""

import asyncio
import json
import shutil
import subprocess
import sys
import threading as _threading
import time as _time
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Body, HTTPException, Query, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.templating import Jinja2Templates

_src_path = Path(__file__).parent.parent.parent
if str(_src_path) not in sys.path:
    sys.path.insert(0, str(_src_path))

# ── Etere web session cache ──────────────────────────────────────────────────
# One persistent login shared across all traffic-assign calls.
# Re-logs in only after 20 minutes of idle so we never burn multiple seats.
_etere_session_lock = _threading.Lock()
_etere_session_state: dict = {"session": None, "born_at": 0.0}
_ETERE_SESSION_TTL = 20 * 60  # seconds


def _get_etere_session():
    from browser_automation.etere_direct_client import etere_web_login
    now = _time.monotonic()
    with _etere_session_lock:
        s = _etere_session_state
        if s["session"] is not None and (now - s["born_at"]) < _ETERE_SESSION_TTL:
            return s["session"]
        session = etere_web_login()
        s["session"] = session
        s["born_at"] = now
        return session


def _invalidate_etere_session():
    with _etere_session_lock:
        _etere_session_state["session"] = None
        _etere_session_state["born_at"] = 0.0

from business_logic.services.pdf_order_detector import PDFOrderDetector
from orchestration.config import ApplicationConfig
from orchestration.order_scanner import OrderScanner
from web.parser_bridge import get_order_detail, list_parsers

_ALLOWED_EXTENSIONS = {".pdf", ".xml", ".xlsx", ".xlsm", ".jpg", ".jpeg", ".png"}

# Market code → COD_USER integer (must match EtereClient.MARKET_CODES)
_MARKET_CODES = {
    "NYC": 1, "CMP": 2, "HOU": 3, "SFO": 4, "SEA": 5,
    "LAX": 6, "CVC": 7, "WDC": 8, "MMT": 9, "DAL": 10,
}
_VALID_DAYS = {"Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"}
_FPS_GLOBAL = 29.97

# Crossings TV language → list of (days_set, time_from HH:MM, time_to HH:MM)
# DAL (The Asian Channel) has different mappings — add separately when needed.
_WD  = {"Monday", "Tuesday", "Wednesday", "Thursday", "Friday"}
_WE  = {"Saturday", "Sunday"}
_ALL = {"Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"}
_MSA = {"Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"}

_CTV_LANG_WINDOWS: dict = {
    "Mandarin": [
        (_MSA, "06:00", "07:00"),
        (_ALL, "07:00", "08:00"),
        (_WD,  "20:00", "23:30"),
        (_WE,  "20:00", "23:59"),
    ],
    "Cantonese": [
        (_WD, "19:00", "20:00"),
        (_WD, "23:30", "23:59"),
    ],
    "Korean":     [(_ALL, "08:00", "10:00")],
    "Vietnamese": [(_ALL, "10:00", "13:00")],
    "Hindi": [
        (_WD, "13:00", "14:00"),
        (_WE, "13:00", "16:00"),
    ],
    "Punjabi":  [(_WD, "14:00", "16:00")],
    "Filipino": [
        (_WD, "16:00", "19:00"),
        (_WE, "16:00", "18:00"),
    ],
    "Hmong": [(_WE, "18:00", "20:00")],
}
_CTV_LANG_WINDOWS["Chinese"]    = _CTV_LANG_WINDOWS["Mandarin"] + _CTV_LANG_WINDOWS["Cantonese"]
_CTV_LANG_WINDOWS["SouthAsian"] = _CTV_LANG_WINDOWS["Hindi"]    + _CTV_LANG_WINDOWS["Punjabi"]

# The Asian Channel (DAL) — broadcast day runs 0600–0559 (wraps past midnight)
_DAL_LANG_WINDOWS: dict = {
    "Mandarin": [
        (_WD,  "06:00", "09:30"),
        (_WE,  "06:00", "10:00"),
        (_ALL, "13:00", "17:00"),
        (_ALL, "18:00", "22:00"),
        (_ALL, "00:00", "01:00"),
        (_WD,  "02:00", "05:30"),
        (_WE,  "02:00", "05:59"),
    ],
    "Cantonese": [
        (_WD,  "09:30", "10:00"),
        (_ALL, "17:00", "18:00"),
        (_ALL, "01:00", "02:00"),
        (_WD,  "05:30", "05:59"),
    ],
    "Vietnamese": [(_ALL, "10:00", "11:00")],
    "Korean": [
        (_WD,  "11:00", "12:00"),
        (_WE,  "11:00", "13:00"),
        (_WD,  "22:00", "23:00"),
        (_WE,  "22:00", "23:59"),
    ],
}
_DAL_LANG_WINDOWS["Chinese"] = _DAL_LANG_WINDOWS["Mandarin"] + _DAL_LANG_WINDOWS["Cantonese"]


def _hhmm_to_frames(hhmm: str) -> int:
    h, m = map(int, hhmm.split(":"))
    return round((h * 3600 + m * 60) * _FPS_GLOBAL)


def _build_spot_filter(filters: dict) -> str:
    """Return extra AND clauses for TPALINSE spot queries based on optional filter dict."""
    import re
    clauses = []
    if filters.get("date_from") and re.match(r"^\d{4}-\d{2}-\d{2}$", filters["date_from"]):
        clauses.append(f"tp.DATA >= '{filters['date_from']}'")
    if filters.get("date_to") and re.match(r"^\d{4}-\d{2}-\d{2}$", filters["date_to"]):
        clauses.append(f"tp.DATA <= '{filters['date_to']}'")
    if filters.get("days"):
        safe = [d for d in filters["days"] if d in _VALID_DAYS]
        if safe:
            clauses.append(f"DATENAME(weekday, tp.DATA) IN ({','.join(repr(d) for d in safe)})")
    if filters.get("time_from"):
        try:
            clauses.append(f"tp.ORA >= {_hhmm_to_frames(filters['time_from'])}")
        except (ValueError, AttributeError):
            pass
    if filters.get("time_to"):
        try:
            clauses.append(f"tp.ORA <= {_hhmm_to_frames(filters['time_to'])}")
        except (ValueError, AttributeError):
            pass
    if filters.get("durations"):
        frames = [int(f) for f in filters["durations"] if str(f).lstrip("-").isdigit()]
        if frames:
            clauses.append(f"cr.DURATA IN ({','.join(str(f) for f in frames)})")
    if filters.get("markets"):
        ids = [_MARKET_CODES[k] for k in filters["markets"] if k in _MARKET_CODES]
        if ids:
            clauses.append(f"tp.COD_USER IN ({','.join(str(i) for i in ids)})")
    if filters.get("languages"):
        # Use DAL windows if DAL is the only market selected; otherwise CTV windows
        mkts = filters.get("markets", [])
        lang_table = _DAL_LANG_WINDOWS if mkts == ["DAL"] else _CTV_LANG_WINDOWS
        lang_subclauses = []
        for lang in filters["languages"]:
            for days_set, t_from, t_to in lang_table.get(lang, []):
                day_list = ",".join(f"'{d}'" for d in sorted(days_set))
                f_from = _hhmm_to_frames(t_from)
                f_to   = _hhmm_to_frames(t_to)
                lang_subclauses.append(
                    f"(DATENAME(weekday, tp.DATA) IN ({day_list})"
                    f" AND tp.ORA >= {f_from} AND tp.ORA <= {f_to})"
                )
        if lang_subclauses:
            clauses.append("(" + " OR ".join(lang_subclauses) + ")")
    return (" AND " + " AND ".join(clauses)) if clauses else ""


def build_router(config: ApplicationConfig, templates: Jinja2Templates) -> APIRouter:
    router = APIRouter()

    used_dir = config.incoming_dir / "Used"

    def _ensure_used_dir():
        used_dir.mkdir(parents=True, exist_ok=True)

    def _make_scanner() -> OrderScanner:
        return OrderScanner(PDFOrderDetector(), config.incoming_dir)

    def _scan_dir(directory: Path) -> list[dict]:
        """Scan a directory for order files and return serializable list."""
        if not directory.exists():
            return []
        scanner = OrderScanner(PDFOrderDetector(), directory)
        orders = scanner.scan_for_orders()
        result = []
        for order in orders:
            stat = order.pdf_path.stat()
            result.append({
                "filename": order.pdf_path.name,
                "order_type": order.order_type.value if order.order_type else "Unknown",
                "customer_name": order.customer_name or "Unknown",
                "estimate_number": order.estimate_number,
                "size_kb": round(stat.st_size / 1024, 1),
                "modified": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M"),
            })
        return result

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------

    @router.get("/", response_class=HTMLResponse)
    async def portal(request: Request):
        return templates.TemplateResponse(request, "portal.html")

    @router.get("/orders", response_class=HTMLResponse)
    async def orders_hub(request: Request):
        return templates.TemplateResponse(request, "orders_hub.html")

    @router.get("/order-entry", response_class=HTMLResponse)
    async def index(request: Request):
        return templates.TemplateResponse(request, "index.html", {"parsers": list_parsers()})

    @router.get("/billing", response_class=HTMLResponse)
    async def billing(request: Request):
        return templates.TemplateResponse(request, "billing.html")

    @router.get("/billing/coop-invoicing", response_class=HTMLResponse)
    async def coop_invoicing(request: Request):
        return templates.TemplateResponse(request, "billing/coop_invoicing.html")

    @router.get("/scripts", response_class=HTMLResponse)
    async def scripts(request: Request):
        return templates.TemplateResponse(request, "scripts.html")

    @router.get("/scripts/block-refresh", response_class=HTMLResponse)
    async def block_refresh(request: Request):
        return templates.TemplateResponse(request, "scripts/block_refresh.html")

    @router.get("/scripts/separation", response_class=HTMLResponse)
    async def separation(request: Request):
        return templates.TemplateResponse(request, "scripts/separation.html")

    # ------------------------------------------------------------------
    # Scripts API — Separation
    # ------------------------------------------------------------------

    _FPS = 29.97

    def _frames_to_ampm(frames: int) -> str:
        if not frames:
            return "—"
        total_s = round(frames / _FPS)
        h, rem = divmod(total_s, 3600)
        m = rem // 60
        if h == 0:
            return f"12:{m:02d}a"
        if h < 12:
            return f"{h}:{m:02d}a"
        if h == 12:
            return f"12:{m:02d}p"
        return f"{h - 12}:{m:02d}p"

    def _frames_to_min(frames) -> int:
        return round(frames / _FPS / 60) if frames else 0

    def _frames_to_sec(frames) -> int:
        return round(frames / _FPS) if frames else 0

    def _days_str(lun, mar, mer, gio, ven, sab, dom) -> str:
        d = [bool(lun), bool(mar), bool(mer), bool(gio), bool(ven), bool(sab), bool(dom)]
        if all(d):
            return "M-Su"
        if d[:5] == [True] * 5 and not d[5] and not d[6]:
            return "M-F"
        if d[:6] == [True] * 6 and not d[6]:
            return "M-Sa"
        if not any(d[:5]) and d[5] and d[6]:
            return "Sa-Su"
        if d[5] and not any(d[:5]) and not d[6]:
            return "Sa"
        if d[6] and not any(d[:5]) and not d[5]:
            return "Su"
        abbr = ["M", "Tu", "W", "Th", "F", "Sa", "Su"]
        return "/".join(a for a, v in zip(abbr, d) if v) or "—"

    _MARKET_NAMES = {1:"NYC",2:"CMP",3:"HOU",4:"SFO",5:"SEA",6:"LAX",7:"CVC",8:"WDC",9:"MMT",10:"DAL"}

    @router.get("/api/scripts/separation/lines")
    async def get_separation_lines(contract_id: int = Query(..., gt=0)):
        try:
            project_root = Path(__file__).parent.parent.parent.parent
            if str(project_root) not in sys.path:
                sys.path.insert(0, str(project_root))
            from browser_automation.etere_direct_client import connect as _db_connect

            with _db_connect() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT ID_CONTRATTIRIGHE, DESCRIZIONE,
                           COALESCE(DATESTART, DATA_INIZIO), COALESCE(DATEEND, DATA_FINE),
                           COALESCE(ORA_INIZIOF, ORA_INIZIO), COALESCE(ORA_FINEF, ORA_FINE),
                           LUNEDI, MARTEDI, MERCOLEDI, GIOVEDI, VENERDI, SABATO, DOMENICA,
                           DURATA, PASSAGGI_SETTIMANALI,
                           Interv_Committente, INTERVALLO, INTERV_CONTRATTO,
                           COD_USER
                    FROM   CONTRATTIRIGHE
                    WHERE  ID_CONTRATTITESTATA = ?
                    ORDER  BY ID_CONTRATTIRIGHE
                """, [contract_id])
                rows = cursor.fetchall()

            if not rows:
                raise HTTPException(status_code=404, detail=f"No lines found for contract {contract_id}.")

            lines = []
            for row in rows:
                (line_id, desc, date_from, date_to, ora_in, ora_out,
                 lun, mar, mer, gio, ven, sab, dom,
                 durata, spots_pw,
                 sep_cust, sep_ord, sep_evt,
                 cod_user) = row
                lines.append({
                    "line_id":      line_id,
                    "description":  desc or "",
                    "market":       _MARKET_NAMES.get(cod_user, str(cod_user) if cod_user else "—"),
                    "date_from":    f"{date_from.month}/{date_from.day}/{date_from.year}" if date_from else "",
                    "date_to":      f"{date_to.month}/{date_to.day}/{date_to.year}" if date_to else "",
                    "time_from":    _frames_to_ampm(ora_in),
                    "time_to":      _frames_to_ampm(ora_out),
                    "days":         _days_str(lun, mar, mer, gio, ven, sab, dom),
                    "duration_sec": _frames_to_sec(durata),
                    "spots_pw":     spots_pw or 0,
                    "sep_customer": _frames_to_min(sep_cust),
                    "sep_order":    _frames_to_min(sep_ord),
                    "sep_event":    _frames_to_min(sep_evt),
                })
            return JSONResponse({"contract_id": contract_id, "lines": lines})

        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))

    @router.post("/api/scripts/separation/apply")
    async def apply_separation_lines(payload: dict = Body(...)):
        try:
            project_root = Path(__file__).parent.parent.parent.parent
            if str(project_root) not in sys.path:
                sys.path.insert(0, str(project_root))
            from browser_automation.etere_direct_client import connect as _db_connect

            line_ids = [int(x) for x in payload.get("line_ids", [])]
            customer = int(payload.get("customer", 0))
            order    = int(payload.get("order", 0))
            event    = int(payload.get("event", 0))

            if not line_ids:
                raise HTTPException(status_code=400, detail="No lines selected.")

            def _to_frames(minutes: int) -> int:
                return round(minutes * 60 * _FPS)

            placeholders = ",".join("?" * len(line_ids))
            with _db_connect() as conn:
                cursor = conn.cursor()
                # INTERVALLO = Order, INTERV_CONTRATTO = Event (old Etere web had these swapped)
                cursor.execute(f"""
                    UPDATE CONTRATTIRIGHE
                    SET    Interv_Committente = ?,
                           INTERVALLO         = ?,
                           INTERV_CONTRATTO   = ?
                    WHERE  ID_CONTRATTIRIGHE IN ({placeholders})
                """, [_to_frames(customer), _to_frames(order), _to_frames(event), *line_ids])
                conn.commit()
                updated = cursor.rowcount

            return JSONResponse({"updated": updated, "customer": customer, "order": order, "event": event})

        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))

    @router.get("/api/scripts/separation")
    async def run_separation(
        contract_id: int = Query(..., gt=0),
        customer: int = Query(0, ge=0),
        event: int = Query(0, ge=0),
        order: int = Query(0, ge=0),
    ):
        project_root = Path(__file__).parent.parent.parent.parent
        script_path = project_root / "scripts" / "update_separation_contract.py"

        python_exe = project_root / ".venv" / "Scripts" / "python.exe"
        if not python_exe.exists():
            python_exe = project_root / ".venv" / "bin" / "python"
        if not python_exe.exists():
            python_exe = Path(sys.executable)

        async def event_stream():
            process = await asyncio.create_subprocess_exec(
                str(python_exe), "-u", str(script_path), str(contract_id),
                "--customer", str(customer),
                "--event", str(event),
                "--order", str(order),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                cwd=str(project_root),
            )
            async for line in process.stdout:
                text = line.decode(errors="replace").rstrip()
                yield f"data: {text}\n\n"
            await process.wait()
            yield f"data: [EXIT:{process.returncode}]\n\n"

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @router.get("/scripts/reportsort", response_class=HTMLResponse)
    async def reportsort_page(request: Request):
        return templates.TemplateResponse(request, "scripts/reportsort.html")

    @router.get("/api/scripts/reportsort")
    async def run_reportsort(
        log_type: str = Query(...),
        date_from: str = Query(...),
        date_to: str = Query(...),
    ):
        project_root = Path(__file__).parent.parent.parent.parent
        script_path = project_root / "scripts" / "run_reportsort.py"

        python_exe = project_root / ".venv" / "Scripts" / "python.exe"
        if not python_exe.exists():
            python_exe = project_root / ".venv" / "bin" / "python"
        if not python_exe.exists():
            python_exe = Path(sys.executable)

        async def event_stream():
            process = await asyncio.create_subprocess_exec(
                str(python_exe), "-u", str(script_path),
                log_type, date_from, date_to,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                cwd=str(project_root),
            )
            async for line in process.stdout:
                text = line.decode(errors="replace").rstrip()
                yield f"data: {text}\n\n"
            await process.wait()
            yield f"data: [EXIT:{process.returncode}]\n\n"

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @router.get("/scripts/delete-spots", response_class=HTMLResponse)
    async def delete_spots_page(request: Request):
        return templates.TemplateResponse(request, "scripts/delete_spots.html")

    @router.get("/api/scripts/delete-spots")
    async def run_delete_spots(
        contract_id: int = Query(..., gt=0),
        date_from: str = Query(...),
        date_to: str = Query(...),
        confirm: str = Query("0"),
    ):
        project_root = Path(__file__).parent.parent.parent.parent
        script_path = project_root / "scripts" / "delete_scheduled_spots.py"

        python_exe = project_root / ".venv" / "Scripts" / "python.exe"
        if not python_exe.exists():
            python_exe = project_root / ".venv" / "bin" / "python"
        if not python_exe.exists():
            python_exe = Path(sys.executable)

        args = [str(python_exe), "-u", str(script_path), str(contract_id), date_from, date_to]
        if confirm == "1":
            args.append("--confirm")

        async def event_stream():
            process = await asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                cwd=str(project_root),
            )
            async for line in process.stdout:
                text = line.decode(errors="replace").rstrip()
                yield f"data: {text}\n\n"
            await process.wait()
            yield f"data: [EXIT:{process.returncode}]\n\n"

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @router.get("/scripts/unschedule", response_class=HTMLResponse)
    async def unschedule_page(request: Request):
        return templates.TemplateResponse(request, "scripts/unschedule.html")

    @router.get("/api/scripts/unschedule")
    async def run_unschedule(contract_id: int = Query(..., gt=0)):
        project_root = Path(__file__).parent.parent.parent.parent
        script_path = project_root / "scripts" / "unschedule_contract.py"

        python_exe = project_root / ".venv" / "Scripts" / "python.exe"
        if not python_exe.exists():
            python_exe = project_root / ".venv" / "bin" / "python"
        if not python_exe.exists():
            python_exe = Path(sys.executable)

        async def event_stream():
            process = await asyncio.create_subprocess_exec(
                str(python_exe), "-u", str(script_path), str(contract_id),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                cwd=str(project_root),
            )
            async for line in process.stdout:
                text = line.decode(errors="replace").rstrip()
                yield f"data: {text}\n\n"
            await process.wait()
            yield f"data: [EXIT:{process.returncode}]\n\n"

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    # ------------------------------------------------------------------
    # Fill Log Times
    # ------------------------------------------------------------------

    _fill_log_pending: dict = {}  # token -> (Path, filename)

    @router.get("/scripts/fill-log-times", response_class=HTMLResponse)
    async def fill_log_times_page(request: Request):
        return templates.TemplateResponse(request, "scripts/fill_log_times.html")

    @router.post("/api/scripts/fill-log-times")
    async def run_fill_log_times(file: UploadFile):
        import datetime as _dt
        import io
        import re
        import tempfile
        import uuid
        from collections import defaultdict

        MARKET_IDS = {
            "NYC": 1, "CMP": 2, "HOU": 3, "SFO": 4,
            "SEA": 5, "LAX": 6, "CVC": 7, "WDC": 8, "DAL": 10,
        }
        COL_DATE = 2
        COL_SHOW = 8
        COL_COMMENTS = 9
        COL_TYPE = 14
        FPS = 29.97

        filename = file.filename or "log.xlsm"
        name_upper = filename.upper()
        market_id = next((mid for code, mid in MARKET_IDS.items() if code in name_upper), None)
        if market_id is None:
            raise HTTPException(status_code=400,
                detail=f"Could not detect market from filename '{filename}'. "
                       f"Expected one of: {', '.join(MARKET_IDS)}.")

        raw = await file.read()

        try:
            import openpyxl
            wb = openpyxl.load_workbook(io.BytesIO(raw), keep_vba=True)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"Could not open workbook: {exc}")

        market_code = next(k for k, v in MARKET_IDS.items() if v == market_id)
        messages = [f"Market: {market_code} (station {market_id})"]
        total_filled = 0

        project_root = Path(__file__).parent.parent.parent.parent
        sys.path.insert(0, str(project_root))
        from browser_automation.etere_direct_client import connect

        with connect() as conn:
            for sheet_name in wb.sheetnames:
                ws = wb[sheet_name]
                pending = defaultdict(list)

                for row in ws.iter_rows(min_row=2):
                    spot_type = row[COL_TYPE - 1].value
                    if not spot_type or str(spot_type).upper() == "PRG":
                        continue
                    comment = row[COL_COMMENTS - 1].value
                    if comment:
                        continue
                    date_val = row[COL_DATE - 1].value
                    if not isinstance(date_val, _dt.datetime):
                        continue
                    show_name = row[COL_SHOW - 1].value or ""
                    m = re.match(r"^([^:]+):", show_name.strip())
                    if not m:
                        continue
                    asset_code = m.group(1).strip()
                    pending[(date_val.date(), asset_code)].append(row[0].row)

                if not pending:
                    continue

                cur = conn.cursor(as_dict=True)
                sheet_filled = 0
                for (date, asset_code), row_nums in pending.items():
                    cur.execute(
                        "SELECT ORA FROM TPALINSE"
                        " WHERE DATA = %s AND TITLE LIKE %s AND COD_USER = %d"
                        " ORDER BY ORA",
                        (date, f"%{asset_code}%", market_id),
                    )
                    oras = [r["ORA"] for r in cur.fetchall()]
                    if not oras:
                        messages.append(f"  No TPALINSE match: {asset_code} on {date}")
                        continue
                    if len(oras) < len(row_nums):
                        messages.append(
                            f"  WARNING: {asset_code} on {date} — "
                            f"{len(row_nums)} log rows but only {len(oras)} Etere entries"
                        )
                    for i, row_num in enumerate(row_nums):
                        if i >= len(oras):
                            break
                        secs = round(oras[i] / FPS)
                        ws.cell(row_num, COL_COMMENTS).value = _dt.timedelta(seconds=secs)
                        sheet_filled += 1

                if sheet_filled:
                    messages.append(f"  {sheet_name}: filled {sheet_filled} spot time(s)")
                total_filled += sheet_filled

        if total_filled == 0:
            return JSONResponse({"filled": 0, "messages": messages})

        out = io.BytesIO()
        wb.save(out)
        out.seek(0)

        token = str(uuid.uuid4())
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".xlsm")
        tmp.write(out.read())
        tmp.close()
        _fill_log_pending[token] = (Path(tmp.name), filename)

        return JSONResponse({"filled": total_filled, "messages": messages,
                             "token": token, "filename": filename})

    @router.get("/api/scripts/fill-log-times/download/{token}")
    async def download_filled_log(token: str):
        entry = _fill_log_pending.pop(token, None)
        if entry is None:
            raise HTTPException(status_code=404, detail="Download expired or not found.")
        tmp_path, filename = entry
        data = tmp_path.read_bytes()
        tmp_path.unlink(missing_ok=True)
        return StreamingResponse(
            iter([data]),
            media_type="application/vnd.ms-excel.sheet.macroEnabled.12",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    @router.get("/api/scripts/block-refresh")
    async def run_block_refresh(contract_id: int = Query(..., gt=0)):
        project_root = Path(__file__).parent.parent.parent.parent
        script_path = project_root / "scripts" / "refresh_blocks_contract.py"

        python_exe = project_root / ".venv" / "Scripts" / "python.exe"
        if not python_exe.exists():
            python_exe = project_root / ".venv" / "bin" / "python"
        if not python_exe.exists():
            python_exe = Path(sys.executable)

        async def event_stream():
            process = await asyncio.create_subprocess_exec(
                str(python_exe), "-u", str(script_path), str(contract_id),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                cwd=str(project_root),
            )
            async for line in process.stdout:
                text = line.decode(errors="replace").rstrip()
                yield f"data: {text}\n\n"
            await process.wait()
            yield f"data: [EXIT:{process.returncode}]\n\n"

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    # ------------------------------------------------------------------
    # Order run logs
    # ------------------------------------------------------------------

    @router.get("/logs", response_class=HTMLResponse)
    async def logs_page(request: Request):
        return templates.TemplateResponse(request, "logs.html")

    @router.get("/api/logs")
    async def list_logs():
        logs_dir = Path(__file__).parent.parent.parent.parent / "logs"
        if not logs_dir.exists():
            return JSONResponse({"files": []})

        files = sorted(
            logs_dir.glob("*.log"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )

        result = []
        for f in files:
            stat = f.stat()
            # Parse date/time from filename: YYYY-MM-DD_HH-MM-SS.log
            try:
                dt = datetime.strptime(f.stem, "%Y-%m-%d_%H-%M-%S")
                display_date = dt.strftime("%b %d, %Y")
                display_time = dt.strftime("%I:%M %p").lstrip("0")
            except ValueError:
                display_date = f.stem
                display_time = ""
            size_bytes = stat.st_size
            if size_bytes >= 1024:
                size_label = f"{size_bytes / 1024:.1f} KB"
            else:
                size_label = f"{size_bytes} B"
            result.append({
                "filename":     f.name,
                "display_date": display_date,
                "display_time": display_time,
                "size_label":   size_label,
                "mtime":        stat.st_mtime,
            })
        return JSONResponse({"files": result})

    @router.delete("/api/logs/cleanup")
    async def cleanup_logs():
        import time
        logs_dir = Path(__file__).parent.parent.parent.parent / "logs"
        if not logs_dir.exists():
            return JSONResponse({"deleted": 0})
        cutoff = time.time() - 30 * 24 * 3600
        deleted = 0
        for f in logs_dir.glob("*.log"):
            if f.stat().st_mtime < cutoff:
                f.unlink()
                deleted += 1
        return JSONResponse({"deleted": deleted})

    @router.get("/api/logs/{filename}")
    async def get_log(filename: str):
        # Prevent path traversal — only .log files, no slashes or dots in name
        if "/" in filename or "\\" in filename or not filename.endswith(".log"):
            raise HTTPException(status_code=400, detail="Invalid filename.")
        logs_dir = Path(__file__).parent.parent.parent.parent / "logs"
        log_path = (logs_dir / filename).resolve()
        if not str(log_path).startswith(str(logs_dir.resolve())):
            raise HTTPException(status_code=403, detail="Forbidden.")
        if not log_path.exists():
            raise HTTPException(status_code=404, detail="Log not found.")
        from fastapi.responses import PlainTextResponse
        return PlainTextResponse(log_path.read_text(encoding="utf-8", errors="replace"))

    @router.get("/scripts/clean-asterisks", response_class=HTMLResponse)
    async def clean_asterisks_page(request: Request):
        return templates.TemplateResponse(request, "scripts/clean_asterisks.html")

    @router.get("/api/scripts/clean-asterisks/lines")
    async def get_asterisk_lines(contract_id: int = Query(..., gt=0)):
        try:
            project_root = Path(__file__).parent.parent.parent.parent
            if str(project_root) not in sys.path:
                sys.path.insert(0, str(project_root))
            from browser_automation.etere_direct_client import connect as _db_connect

            with _db_connect() as conn:
                # pymssql uses %s paramstyle; pyodbc uses ?
                ph = '%s' if type(conn).__module__.startswith('pymssql') else '?'
                cursor = conn.cursor()
                cursor.execute(f"""
                    SELECT ID_CONTRATTIRIGHE, DESCRIZIONE
                    FROM   CONTRATTIRIGHE
                    WHERE  ID_CONTRATTITESTATA = {ph}
                      AND  DESCRIZIONE LIKE {ph}
                    ORDER  BY ID_CONTRATTIRIGHE
                """, [contract_id, '%*%'])
                rows = cursor.fetchall()

            lines = [
                {
                    "line_id":     row[0],
                    "description": row[1] or "",
                    "cleaned":     (row[1] or "").replace("*", "").strip(),
                }
                for row in rows
            ]
            return JSONResponse({"contract_id": contract_id, "lines": lines})

        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))

    @router.post("/api/scripts/clean-asterisks/apply")
    async def apply_clean_asterisks(payload: dict = Body(...)):
        try:
            project_root = Path(__file__).parent.parent.parent.parent
            if str(project_root) not in sys.path:
                sys.path.insert(0, str(project_root))
            from browser_automation.etere_direct_client import connect as _db_connect

            contract_id = int(payload.get("contract_id", 0))
            if not contract_id:
                raise HTTPException(status_code=400, detail="contract_id required.")

            with _db_connect() as conn:
                ph = '%s' if type(conn).__module__.startswith('pymssql') else '?'
                cursor = conn.cursor()
                cursor.execute(f"""
                    UPDATE CONTRATTIRIGHE
                    SET    DESCRIZIONE = RTRIM(LTRIM(REPLACE(DESCRIZIONE, '*', '')))
                    WHERE  ID_CONTRATTITESTATA = {ph}
                      AND  DESCRIZIONE LIKE {ph}
                """, [contract_id, '%*%'])
                conn.commit()
                updated = cursor.rowcount

            return JSONResponse({"updated": updated})

        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))

    # ------------------------------------------------------------------
    # Pending orders
    # ------------------------------------------------------------------

    @router.get("/api/orders")
    async def list_orders():
        return JSONResponse(content=_scan_dir(config.incoming_dir))

    @router.post("/api/orders/upload")
    async def upload_order(file: UploadFile):
        if not file.filename:
            raise HTTPException(status_code=400, detail="No filename provided.")

        suffix = Path(file.filename).suffix.lower()
        if suffix not in _ALLOWED_EXTENSIONS:
            raise HTTPException(
                status_code=400,
                detail=f"File type '{suffix}' not allowed. Accepted: {', '.join(_ALLOWED_EXTENSIONS)}"
            )

        dest = (config.incoming_dir / file.filename).resolve()
        if not str(dest).startswith(str(config.incoming_dir.resolve())):
            raise HTTPException(status_code=400, detail="Invalid filename.")

        contents = await file.read()
        dest.write_bytes(contents)
        return JSONResponse(content={"message": f"'{file.filename}' uploaded successfully.", "filename": file.filename})

    @router.delete("/api/orders/{filename:path}")
    async def move_to_used(filename: str):
        """Move a pending order to incoming/Used/ (soft delete)."""
        target = (config.incoming_dir / filename).resolve()
        if not str(target).startswith(str(config.incoming_dir.resolve())):
            raise HTTPException(status_code=400, detail="Invalid filename.")
        if not target.exists():
            raise HTTPException(status_code=404, detail="File not found.")

        _ensure_used_dir()
        dest = used_dir / target.name
        # If a file with the same name already exists in Used, append a timestamp
        if dest.exists():
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            dest = used_dir / f"{target.stem}_{ts}{target.suffix}"

        shutil.move(str(target), str(dest))
        return JSONResponse(content={"message": f"'{filename}' moved to Used."})

    # ------------------------------------------------------------------
    # Run queue
    # ------------------------------------------------------------------

    @router.post("/api/run")
    async def run_queue(body: Any = Body(default=[])):
        # Accept both legacy list format and new {files, overrides} format
        if isinstance(body, list):
            files: list[str] = body
            overrides: dict = {}
        else:
            files = body.get("files", [])
            overrides = body.get("overrides", {})

        _order_patterns = ("*.pdf", "*.xml", "*.xlsx", "*.xlsm", "*.jpg", "*.jpeg", "*.png")
        if not any(f for pat in _order_patterns for f in config.incoming_dir.glob(pat)):
            raise HTTPException(status_code=400, detail="No orders in queue.")

        # Write per-file override sidecars so the automation can consume them
        for filename, file_overrides in overrides.items():
            sidecar = config.incoming_dir / (filename + ".overrides.json")
            try:
                sidecar.write_text(json.dumps(file_overrides))
            except Exception:
                pass

        project_root = Path(__file__).parent.parent.parent.parent
        main_py = project_root / "main.py"

        # Prefer venv python, fall back to current interpreter
        python_exe = project_root / ".venv" / "Scripts" / "python.exe"  # Windows
        if not python_exe.exists():
            python_exe = project_root / ".venv" / "bin" / "python"       # Linux
        if not python_exe.exists():
            python_exe = Path(sys.executable)

        # Build --files args if specific files selected
        files_arg = ""
        if files:
            # Validate each filename stays within incoming/
            safe_files = []
            for f in files:
                p = (config.incoming_dir / f).resolve()
                if str(p).startswith(str(config.incoming_dir.resolve())) and p.exists():
                    safe_files.append(f)
            if safe_files:
                files_arg = " --files " + " ".join(f'"{f}"' for f in safe_files)

        if sys.platform == "win32":
            # cmd /k requires the entire inner command wrapped in an extra pair of quotes
            cmd = f'start "CTV Order Entry" cmd /k ""{python_exe}" "{main_py}"{files_arg}"'
            subprocess.Popen(cmd, shell=True, cwd=str(project_root))
            n = len(files) if files else "all"
            return JSONResponse({"message": f"Terminal opened — processing {n} order(s)."})
        else:
            cmd = f"uv run python main.py{files_arg}"
            return JSONResponse({"message": f"Run in your terminal: {cmd}", "manual": True})

    # ------------------------------------------------------------------
    # History (Used folder)
    # ------------------------------------------------------------------

    @router.get("/api/history")
    async def list_history():
        return JSONResponse(content=_scan_dir(used_dir))

    @router.post("/api/history/{filename:path}/restore")
    async def restore_order(filename: str):
        """Move a file from Used back to incoming/."""
        target = (used_dir / filename).resolve()
        if not str(target).startswith(str(used_dir.resolve())):
            raise HTTPException(status_code=400, detail="Invalid filename.")
        if not target.exists():
            raise HTTPException(status_code=404, detail="File not found.")

        dest = config.incoming_dir / target.name
        if dest.exists():
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            dest = config.incoming_dir / f"{target.stem}_{ts}{target.suffix}"

        shutil.move(str(target), str(dest))
        return JSONResponse(content={"message": f"'{filename}' restored to incoming."})

    # ------------------------------------------------------------------
    # Detail (works for both pending and history files)
    # ------------------------------------------------------------------

    def _resolve_file(filename: str) -> tuple[Path, str]:
        """Find the file in incoming/ or Used/ and return (path, order_type)."""
        # Try incoming/ first
        for search_dir in [config.incoming_dir, used_dir]:
            candidate = (search_dir / filename).resolve()
            base = str(search_dir.resolve())
            if not str(candidate).startswith(base):
                continue
            if candidate.exists():
                scanner = OrderScanner(PDFOrderDetector(), search_dir)
                for o in scanner.scan_for_orders():
                    if o.pdf_path.resolve() == candidate:
                        return candidate, o.order_type.value if o.order_type else "UNKNOWN"
                return candidate, "UNKNOWN"
        raise HTTPException(status_code=404, detail="File not found.")

    @router.get("/api/orders/{filename:path}/detail")
    async def order_detail(filename: str):
        path, order_type = _resolve_file(filename)
        detail = get_order_detail(path, order_type)
        detail["filename"] = filename
        detail["order_type"] = order_type
        return JSONResponse(content=detail)

    @router.get("/api/history/{filename:path}/detail")
    async def history_detail(filename: str):
        target = (used_dir / filename).resolve()
        if not str(target).startswith(str(used_dir.resolve())):
            raise HTTPException(status_code=400, detail="Invalid filename.")
        if not target.exists():
            raise HTTPException(status_code=404, detail="File not found.")

        scanner = OrderScanner(PDFOrderDetector(), used_dir)
        order_type = "UNKNOWN"
        for o in scanner.scan_for_orders():
            if o.pdf_path.resolve() == target:
                order_type = o.order_type.value if o.order_type else "UNKNOWN"
                break

        detail = get_order_detail(target, order_type)
        detail["filename"] = filename
        detail["order_type"] = order_type
        return JSONResponse(content=detail)

    # ------------------------------------------------------------------
    # Traffic
    # ------------------------------------------------------------------

    _MARKET_NAMES = {
        1: "NYC", 2: "CMP", 3: "HOU", 4: "SFO", 5: "SEA",
        6: "LAX", 7: "CVC", 8: "WDC", 9: "MMT", 10: "DAL",
    }

    @router.get("/traffic", response_class=HTMLResponse)
    async def traffic_page(request: Request):
        return templates.TemplateResponse(request, "traffic.html")

    @router.get("/api/traffic/diagnose-missing")
    async def diagnose_missing(
        date_from: str = Query(...),
        date_to:   str = Query(...),
    ):
        from datetime import datetime as _dt

        def _parse_date(s: str):
            for fmt in ("%m/%d/%Y", "%m/%d/%y", "%Y-%m-%d"):
                try:
                    return _dt.strptime(s.strip(), fmt)
                except ValueError:
                    pass
            raise ValueError(f"Unrecognized date: {s!r}")

        def _run():
            from browser_automation.etere_direct_client import connect as _db_connect
            out = {}
            dt_from = _parse_date(date_from)
            dt_to   = _parse_date(date_to)

            # --- connection 1: metadata only ---
            with _db_connect() as conn:
                cur = conn.cursor()
                cur.execute("SELECT DISTINCT cod_user, nome FROM users ORDER BY cod_user")
                stations = [(row[0], row[1]) for row in cur.fetchall()]
                out["stations"] = [{"cod_user": c, "nome": n} for c, n in stations]

                try:
                    cur2 = conn.cursor()
                    cur2.execute("SELECT SUSER_SNAME(), USER_NAME(), IS_MEMBER('db_owner'), IS_MEMBER('db_datareader')")
                    r = cur2.fetchone()
                    out["login"] = {"suser": str(r[0]), "user": str(r[1]), "db_owner": r[2], "db_datareader": r[3]}
                except Exception as e:
                    out["login_error"] = str(e)

                # Get FILMATI and FS_FILMATI column names so we know the real schema
                try:
                    cols_cur = conn.cursor()
                    cols_cur.execute("""
                        SELECT TABLE_NAME, COLUMN_NAME, DATA_TYPE
                        FROM INFORMATION_SCHEMA.COLUMNS
                        WHERE TABLE_NAME IN ('FILMATI','FS_FILMATI','CONTRATTIFILMATI')
                        ORDER BY TABLE_NAME, ORDINAL_POSITION
                    """)
                    schema = {}
                    for tbl, col, dtype in cols_cur.fetchall():
                        schema.setdefault(tbl, []).append(f"{col} ({dtype})")
                    out["material_schema"] = schema
                except Exception as e:
                    out["material_schema_error"] = str(e)

                # Direct spot count for CVC in range
                try:
                    cnt_cur = conn.cursor()
                    cnt_cur.execute("""
                        SELECT COUNT(*) as total_spots,
                               SUM(CASE WHEN ID_FILMATI IS NULL OR ID_FILMATI = 0 THEN 1 ELSE 0 END) as no_material,
                               SUM(CASE WHEN ID_FILMATI IS NOT NULL AND ID_FILMATI <> 0 THEN 1 ELSE 0 END) as has_material
                        FROM TPalinseSpotsInCluster
                        WHERE DATA BETWEEN ? AND ? AND COD_USER = 7
                    """, dt_from, dt_to)
                    r = cnt_cur.fetchone()
                    out["cvc_spot_counts"] = {"total": r[0], "no_material": r[1], "has_material": r[2]}
                except Exception as e:
                    out["cvc_spot_counts_error"] = str(e)

                # Check STATUS_MM distribution on linked FILMATI records for CVC
                try:
                    smm_cur = conn.cursor()
                    smm_cur.execute("""
                        SELECT f.STATUS_MM, COUNT(*) as cnt
                        FROM TPalinseSpotsInCluster s
                        JOIN FILMATI f ON f.ID_FILMATI = s.ID_FILMATI
                        WHERE s.DATA BETWEEN ? AND ? AND s.COD_USER = 7
                        GROUP BY f.STATUS_MM
                        ORDER BY cnt DESC
                    """, dt_from, dt_to)
                    out["cvc_status_mm"] = [{"status_mm": str(r[0]), "count": r[1]} for r in smm_cur.fetchall()]
                except Exception as e:
                    out["cvc_status_mm_error"] = str(e)

                # Check FS_FILMATI.FLAG_OFFLINE for CVC spots
                try:
                    off_cur = conn.cursor()
                    off_cur.execute("""
                        SELECT fs.FLAG_OFFLINE, COUNT(*) as cnt
                        FROM TPalinseSpotsInCluster s
                        JOIN FS_FILMATI fs ON fs.ID_FILMATI = s.ID_FILMATI
                        WHERE s.DATA BETWEEN ? AND ? AND s.COD_USER = 7
                        GROUP BY fs.FLAG_OFFLINE
                    """, dt_from, dt_to)
                    out["cvc_flag_offline"] = [{"flag_offline": str(r[0]), "count": r[1]} for r in off_cur.fetchall()]
                except Exception as e:
                    out["cvc_flag_offline_error"] = str(e)

                # Try connecting to the SSRS server (EC2AMAZ-6J2KLLI) directly
                # to test if it has different data than our Tailscale endpoint
                try:
                    import os as _os

                    import pyodbc as _pyodbc
                    user = _os.getenv("ETERE_DB_USER")
                    pwd  = _os.getenv("ETERE_DB_PASSWORD")
                    cs = (
                        f"DRIVER={{SQL Server}};SERVER=EC2AMAZ-6J2KLLI;"
                        f"DATABASE=Etere_crossing;UID={user};PWD={pwd};"
                        if user and pwd else
                        "DRIVER={SQL Server};SERVER=EC2AMAZ-6J2KLLI;"
                        "DATABASE=Etere_crossing;Trusted_Connection=yes;"
                    )
                    ec2_conn = _pyodbc.connect(cs, timeout=5)
                    ec2_cur = ec2_conn.cursor()
                    ec2_cur.execute(
                        "EXEC dbo.rpt_trf_missing_material_list "
                        "@cod_user=7, @startDate=?, @endDate=?, "
                        "@viewNM='1', @viewNA='1', @viewNR='1', @orderBy='3', @codeStart=NULL",
                        dt_from, dt_to,
                    )
                    while ec2_cur.description is None:
                        if not ec2_cur.nextset():
                            break
                    rows = ec2_cur.fetchall() if ec2_cur.description else []
                    out["ec2_sp_row_count"] = len(rows)
                    out["ec2_sp_first_row"] = [str(v) for v in rows[0]] if rows else None
                    ec2_conn.close()
                except Exception as e:
                    out["ec2_sp_error"] = str(e)

            # --- connection 2: SP calls with ARITHABORT ON (clean slate) ---
            out["markets"] = []
            with _db_connect() as sp_conn:
                _set = sp_conn.cursor()
                _set.execute("SET ARITHABORT ON")
                _set.execute("SET ANSI_NULLS ON")
                _set.execute("SET ANSI_WARNINGS ON")
                _set.execute("SET QUOTED_IDENTIFIER ON")
                for cod_user, nome in stations:
                    try:
                        mc = sp_conn.cursor()
                        mc.execute(
                            "EXEC dbo.rpt_trf_missing_material_list ?, ?, ?, ?, ?, ?, ?, ?",
                            cod_user, dt_from, dt_to, "1", "1", "1", "3", None
                        )
                        while mc.description is None:
                            if not mc.nextset():
                                break
                        if mc.description:
                            cols = [d[0] for d in mc.description]
                            rows = mc.fetchall()
                            out["markets"].append({
                                "cod_user": cod_user, "nome": nome,
                                "columns": cols,
                                "row_count": len(rows),
                                "first_row": [str(v) for v in rows[0]] if rows else None,
                            })
                        else:
                            out["markets"].append({"cod_user": cod_user, "nome": nome, "error": "no result set"})
                    except Exception as e:
                        out["markets"].append({"cod_user": cod_user, "nome": nome, "error": str(e)})
            return out

        result = await asyncio.to_thread(_run)
        return JSONResponse(content=result)

    @router.get("/traffic/missing-materials", response_class=HTMLResponse)
    async def traffic_missing_materials_page(request: Request):
        return templates.TemplateResponse(request, "traffic/missing_materials.html")

    @router.get("/api/traffic/missing-materials")
    async def get_missing_materials(
        date_from: str = Query(...),
        date_to:   str = Query(...),
    ):
        from datetime import datetime as _dt

        def _parse_date(s: str):
            for fmt in ("%m/%d/%Y", "%m/%d/%y", "%Y-%m-%d"):
                try:
                    return _dt.strptime(s.strip(), fmt)
                except ValueError:
                    pass
            raise ValueError(f"Unrecognized date: {s!r}")

        def _query():
            from browser_automation.etere_direct_client import connect as _db_connect
            dt_from = _parse_date(date_from)
            dt_to   = _parse_date(date_to)
            results = []
            errors  = []
            with _db_connect() as conn:
                cursor = conn.cursor()
                # Discover stations from users table (same source as the SSRS report)
                cursor.execute("SELECT DISTINCT cod_user, nome FROM users ORDER BY cod_user")
                stations = [(row[0], row[1]) for row in cursor.fetchall()]
                if not stations:
                    errors.append("No stations found in users table.")
                    return results, errors

                _set = conn.cursor()
                _set.execute("SET ARITHABORT ON")
                _set.execute("SET ANSI_NULLS ON")
                _set.execute("SET ANSI_WARNINGS ON")
                _set.execute("SET QUOTED_IDENTIFIER ON")
                for cod_user, nome in stations:
                    market_name = _MARKET_NAMES.get(cod_user, nome or str(cod_user))
                    try:
                        cur = conn.cursor()  # fresh cursor per market avoids stale result-set state
                        cur.execute(
                            "EXEC dbo.rpt_trf_missing_material_list ?, ?, ?, ?, ?, ?, ?, ?",
                            cod_user, dt_from, dt_to, "1", "1", "1", "3", None
                        )
                        # SP may produce multiple result sets; advance to one with columns
                        while cur.description is None:
                            if not cur.nextset():
                                break
                        if cur.description:
                            for row in cur.fetchall():
                                results.append({
                                    "market":        market_name,
                                    "customer":      row[0],
                                    "agency":        row[1],
                                    "salesman":      row[2],
                                    "description":   row[3],
                                    "duration":      row[4],
                                    "cod_progra":    row[5],
                                    "schedule_time": row[6].isoformat() if row[6] else None,
                                    "status":        row[7],
                                })
                    except Exception as e:
                        errors.append(f"{market_name} (cod_user={cod_user}): {e}")
            return results, errors

        rows, errors = await asyncio.to_thread(_query)
        if errors and not rows:
            raise HTTPException(status_code=500, detail="\n".join(errors))
        return JSONResponse(content={"rows": rows, "errors": errors})

    @router.get("/api/traffic/no-material")
    async def get_no_material(
        date_from: str = Query(...),
        date_to:   str = Query(...),
    ):
        from datetime import datetime as _dt

        def _parse_date(s: str):
            for fmt in ("%m/%d/%Y", "%m/%d/%y", "%Y-%m-%d"):
                try:
                    return _dt.strptime(s.strip(), fmt)
                except ValueError:
                    pass
            raise ValueError(f"Unrecognized date: {s!r}")

        def _query():
            from browser_automation.etere_direct_client import connect as _db_connect
            dt_from = _parse_date(date_from)
            dt_to   = _parse_date(date_to)
            with _db_connect() as conn:
                cur = conn.cursor()
                cur.execute("""
                    SELECT COD_USER, DATA, ORA, COD_PROGRA, TITLE, DURATION
                    FROM TPalinseSpotsInCluster
                    WHERE DATA BETWEEN ? AND ?
                      AND (ID_FILMATI IS NULL OR ID_FILMATI = 0)
                    ORDER BY DATA, COD_USER, ORA
                """, dt_from, dt_to)
                rows = []
                for row in cur.fetchall():
                    cod_user, data, ora, cod_progra, title, duration = row
                    market = _MARKET_NAMES.get(cod_user, str(cod_user))
                    date_str = (
                        f"{data.month}/{data.day:02d}/{str(data.year)[2:]}" if data else ""
                    )
                    rows.append({
                        "market":   market,
                        "date":     date_str,
                        "time":     _frames_to_ampm(ora),
                        "program":  cod_progra or "",
                        "title":    title or "",
                        "duration": _frames_to_sec(duration),
                    })
            return rows

        rows = await asyncio.to_thread(_query)
        return JSONResponse(content={"rows": rows, "total": len(rows)})

    # ------------------------------------------------------------------
    # Customer Database
    # ------------------------------------------------------------------

    _BILLING_DIR    = Path(r"C:\Users\usrjp\windev\billing")
    _BILLING_PYTHON = _BILLING_DIR / ".venv" / "Scripts" / "python.exe"
    _BILLING_MANAGE  = _BILLING_DIR / "manage_db.py"
    _BILLING_BACKFILL = _BILLING_DIR / "backfill.py"

    async def _run_manage_json(args: list) -> object:
        cmd = [str(_BILLING_PYTHON), str(_BILLING_MANAGE), "--json"] + [str(a) for a in args]
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(_BILLING_DIR),
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise HTTPException(status_code=500, detail=stderr.decode(errors="replace").strip())
        return json.loads(stdout.decode(errors="replace"))

    async def _run_manage_write(args: list) -> dict:
        cmd = [str(_BILLING_PYTHON), str(_BILLING_MANAGE)] + [str(a) for a in args]
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(_BILLING_DIR),
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            return {"ok": False, "error": (stderr.decode(errors="replace") or stdout.decode(errors="replace")).strip()}
        return {"ok": True}

    @router.get("/customers", response_class=HTMLResponse)
    async def customers_page(request: Request):
        return templates.TemplateResponse(request, "customers.html")

    @router.get("/customers/import-db", response_class=HTMLResponse)
    async def customers_import_db_page(request: Request):
        return templates.TemplateResponse(request, "customers/import_db.html")

    @router.post("/api/customers/import-db/preview")
    async def preview_import_db(file: UploadFile):
        """
        Compare an uploaded customers.db against the live one.
        Returns new records and conflicts (same PK, differing field values).
        """
        import os
        import sqlite3
        import tempfile
        from pathlib import Path

        _SKIP = {"created_at"}
        target_path = Path("data/customers.db")
        if not target_path.exists():
            from fastapi.responses import JSONResponse
            return JSONResponse(status_code=400, content={"error": f"Target database not found: {target_path.resolve()}"})

        # Save upload to temp file
        tmp_fd, tmp_path = tempfile.mkstemp(suffix=".db")
        try:
            with os.fdopen(tmp_fd, "wb") as f:
                f.write(await file.read())

            # Read source DB
            src_conn = sqlite3.connect(tmp_path)
            src_conn.row_factory = sqlite3.Row
            src_rows = src_conn.execute("SELECT * FROM customers").fetchall()
            src_cols = [d[0] for d in src_conn.execute("PRAGMA table_info(customers)").fetchall()]
            src_conn.close()

            # Read target DB
            tgt_conn = sqlite3.connect(str(target_path))
            tgt_conn.row_factory = sqlite3.Row
            tgt_cols = [d[0] for d in tgt_conn.execute("PRAGMA table_info(customers)").fetchall()]
            tgt_index = {
                (r["customer_name"], r["order_type"]): dict(r)
                for r in tgt_conn.execute("SELECT * FROM customers").fetchall()
            }
            tgt_conn.close()

            # Compare columns that exist in BOTH (skip metadata)
            common_cols = [c for c in src_cols if c in tgt_cols and c not in _SKIP]
            if "customer_name" not in common_cols or "order_type" not in common_cols:
                raise ValueError(f"Uploaded DB is missing required columns. Found: {src_cols}")

            new_records = []
            conflicts = []

            for src_row in src_rows:
                row_dict = {c: src_row[c] for c in src_cols if c in common_cols}
                key = (row_dict["customer_name"], row_dict["order_type"])

                if key not in tgt_index:
                    new_records.append(row_dict)
                else:
                    tgt_row = {c: tgt_index[key].get(c) for c in common_cols}
                    diff_fields = [
                        c for c in common_cols
                        if c not in ("customer_name", "order_type")
                        and str(row_dict.get(c) or "") != str(tgt_row.get(c) or "")
                    ]
                    if diff_fields:
                        conflicts.append({
                            "customer_name": key[0],
                            "order_type":    key[1],
                            "current":  tgt_row,
                            "incoming": row_dict,
                            "diff":     diff_fields,
                        })

            return {
                "new":       new_records,
                "conflicts": conflicts,
                "columns":   common_cols,
            }

        except Exception as exc:
            from fastapi.responses import JSONResponse
            return JSONResponse(status_code=500, content={"error": str(exc)})
        finally:
            os.unlink(tmp_path)

    @router.post("/api/customers/import-db/apply")
    async def apply_import_db(body: Any = Body(default={})):
        """
        Apply the user-resolved merge.
        Body: { insert: [row_dicts], upsert: [row_dicts] }
        """
        import sqlite3
        from pathlib import Path

        insert_rows = body.get("insert", [])
        upsert_rows = body.get("upsert", [])
        target_path = Path("data/customers.db")

        inserted = updated = 0
        with sqlite3.connect(str(target_path)) as conn:
            for row in insert_rows:
                cols = list(row.keys())
                placeholders = ",".join(["?"] * len(cols))
                conn.execute(
                    f"INSERT OR IGNORE INTO customers ({','.join(cols)}) VALUES ({placeholders})",
                    [row[c] for c in cols],
                )
                inserted += conn.total_changes

            for row in upsert_rows:
                cols = [c for c in row if c not in ("customer_name", "order_type")]
                if not cols:
                    continue
                set_clause = ", ".join(f"{c}=?" for c in cols)
                conn.execute(
                    f"UPDATE customers SET {set_clause} WHERE customer_name=? AND order_type=?",
                    [row[c] for c in cols] + [row["customer_name"], row["order_type"]],
                )
                updated += 1

        return {"inserted": inserted, "updated": updated}

    @router.get("/customers/backfill", response_class=HTMLResponse)
    async def customers_backfill_page(request: Request):
        return templates.TemplateResponse(request, "customers/backfill.html")

    @router.get("/api/customers/backfill")
    async def run_backfill(
        since: str = Query(...),
        dry_run: str = Query("0"),
    ):
        args = [str(_BILLING_PYTHON), "-u", str(_BILLING_BACKFILL), "--since", since]
        if dry_run == "1":
            args.append("--dry-run")

        async def event_stream():
            process = await asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                cwd=str(_BILLING_DIR),
            )
            async for line in process.stdout:
                text = line.decode(errors="replace").rstrip()
                yield f"data: {text}\n\n"
            await process.wait()
            yield f"data: [EXIT:{process.returncode}]\n\n"

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @router.get("/customers/manage", response_class=HTMLResponse)
    async def customers_manage_page(request: Request):
        return templates.TemplateResponse(request, "customers/manage.html")

    @router.get("/api/customers/manage/search")
    async def manage_search(q: str = Query(...)):
        return JSONResponse(content=await _run_manage_json(["search", q]))

    @router.get("/api/customers/manage/agencies")
    async def manage_agencies():
        return JSONResponse(content=await _run_manage_json(["list-agencies"]))

    @router.get("/api/customers/manage/advertisers")
    async def manage_advertisers():
        return JSONResponse(content=await _run_manage_json(["list-advertisers"]))

    @router.get("/api/customers/manage/order/{contract}")
    async def manage_order(contract: int):
        return JSONResponse(content=await _run_manage_json(["show", contract]))

    @router.get("/api/customers/manage/monthly/{contract}")
    async def manage_monthly(contract: int):
        return JSONResponse(content=await _run_manage_json(["monthly", contract]))

    @router.get("/api/customers/manage/affidavits")
    async def manage_affidavits(month: str = Query(...)):
        return JSONResponse(content=await _run_manage_json(["affidavit", "list", month]))

    @router.post("/api/customers/manage/set-agency")
    async def manage_set_agency(body: dict = Body(...)):
        args = ["set-agency", body.get("agency", "")]
        edi = body.get("edi")
        if edi is True:
            args.append("--edi")
        elif edi is False:
            args.append("--no-edi")
        notes = body.get("edi_notes")
        if notes is not None:
            args += ["--edi-notes", notes]
        return JSONResponse(content=await _run_manage_write(args))

    @router.post("/api/customers/manage/set-advertiser")
    async def manage_set_advertiser(body: dict = Body(...)):
        args = ["set-advertiser", body.get("advertiser", "")]
        args.append("--notarized" if body.get("notarized") else "--no-notarized")
        return JSONResponse(content=await _run_manage_write(args))

    @router.post("/api/customers/manage/affidavit-status")
    async def manage_affidavit_status(body: dict = Body(...)):
        args = ["affidavit", "status", body.get("number", ""), body.get("status", "")]
        return JSONResponse(content=await _run_manage_write(args))

    @router.post("/api/customers/manage/remove-order")
    async def manage_remove_order(body: dict = Body(...)):
        args = ["remove-order", "--confirm", str(body.get("contract_number", ""))]
        return JSONResponse(content=await _run_manage_write(args))

    # ── Order-entry customer database (data/customers.db) ─────────────────────

    def _cdb():
        import sqlite3 as _sq
        return _sq.connect(str(config.customer_db_path))

    @router.get("/orders/customers", response_class=HTMLResponse)
    async def order_customers_page(request: Request):
        return templates.TemplateResponse(request, "order_customers.html", {})

    @router.get("/api/orders/customers")
    async def list_order_customers(q: str = ""):
        conn = _cdb()
        try:
            if q:
                rows = conn.execute(
                    "SELECT * FROM customers WHERE LOWER(customer_name) LIKE ? OR LOWER(order_type) LIKE ? ORDER BY customer_name",
                    (f"%{q.lower()}%", f"%{q.lower()}%"),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM customers ORDER BY customer_name"
                ).fetchall()
            cols = [d[0] for d in conn.execute("SELECT * FROM customers LIMIT 0").description]
            return JSONResponse([dict(zip(cols, r)) for r in rows])
        finally:
            conn.close()

    @router.post("/api/orders/customers")
    async def create_order_customer(body: dict = Body(...)):
        import sqlite3 as _sq
        conn = _cdb()
        try:
            conn.execute(
                """INSERT INTO customers
                   (customer_id, customer_name, order_type, code_name, description_name,
                    billing_type, default_market, separation_customer, separation_event,
                    separation_order, include_market_in_code, abbreviation)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    body.get("customer_id", ""),
                    body.get("customer_name", ""),
                    body.get("order_type", ""),
                    body.get("code_name", ""),
                    body.get("description_name", ""),
                    body.get("billing_type", "agency"),
                    body.get("default_market") or None,
                    int(body.get("separation_customer", 15)),
                    int(body.get("separation_event", 0)),
                    int(body.get("separation_order", 0)),
                    int(bool(body.get("include_market_in_code", False))),
                    body.get("abbreviation", ""),
                ),
            )
            conn.commit()
        except _sq.IntegrityError as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        finally:
            conn.close()
        return JSONResponse({"ok": True})

    @router.put("/api/orders/customers")
    async def update_order_customer(customer_name: str, order_type: str, body: dict = Body(...)):
        conn = _cdb()
        try:
            conn.execute(
                """UPDATE customers SET
                   customer_id=?, code_name=?, description_name=?,
                   billing_type=?, default_market=?, separation_customer=?,
                   separation_event=?, separation_order=?, include_market_in_code=?,
                   abbreviation=?
                   WHERE customer_name=? AND order_type=?""",
                (
                    body.get("customer_id", ""),
                    body.get("code_name", ""),
                    body.get("description_name", ""),
                    body.get("billing_type", "agency"),
                    body.get("default_market") or None,
                    int(body.get("separation_customer", 15)),
                    int(body.get("separation_event", 0)),
                    int(body.get("separation_order", 0)),
                    int(bool(body.get("include_market_in_code", False))),
                    body.get("abbreviation", ""),
                    customer_name,
                    order_type,
                ),
            )
            conn.commit()
        finally:
            conn.close()
        return JSONResponse({"ok": True})

    @router.delete("/api/orders/customers")
    async def delete_order_customer(customer_name: str, order_type: str):
        conn = _cdb()
        try:
            conn.execute(
                "DELETE FROM customers WHERE customer_name=? AND order_type=?",
                (customer_name, order_type),
            )
            conn.commit()
        finally:
            conn.close()
        return JSONResponse({"ok": True})

    # ── Assign Traffic ─────────────────────────────────────────────────────────

    @router.get("/traffic/assign-assets", response_class=HTMLResponse)
    async def traffic_assign_assets_page(request: Request):
        return templates.TemplateResponse(request, "traffic/asset_assignment.html")

    @router.get("/api/traffic/contract-search")
    async def traffic_contract_search(q: str = Query("")):
        def _run():
            from browser_automation.etere_direct_client import connect as _db_connect
            with _db_connect() as conn:
                cur = conn.cursor(as_dict=True)
                term = f"%{q.upper()}%"
                id_clause = "OR ct.ID_CONTRATTITESTATA = %d" % int(q) if q.isdigit() else ""
                cur.execute(f"""
                    SELECT TOP 20
                        ct.ID_CONTRATTITESTATA                      AS id,
                        ct.COD_CONTRATTO                            AS code,
                        ct.DESCRIZIONE                              AS description,
                        CONVERT(VARCHAR(10), ct.DATA_INIZIO,   101) AS date_start,
                        CONVERT(VARCHAR(10), ct.DATA_TERMINE,  101) AS date_end
                    FROM CONTRATTITESTATA ct
                    WHERE UPPER(ct.COD_CONTRATTO) LIKE %s
                       OR UPPER(ct.DESCRIZIONE)   LIKE %s
                       {id_clause}
                    ORDER BY ct.DATA_INIZIO DESC
                """, (term, term))
                return [dict(r) for r in cur.fetchall()]
        try:
            rows = await asyncio.get_running_loop().run_in_executor(None, _run)
            return JSONResponse(rows)
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))

    @router.get("/api/traffic/contract/{contract_id}/assignment")
    async def traffic_contract_assignment(contract_id: int):
        def _run():
            from browser_automation.etere_direct_client import connect as _db_connect
            with _db_connect() as conn:
                cur = conn.cursor(as_dict=True)
                cur.execute("""
                    SELECT COD_CONTRATTO AS code, DESCRIZIONE AS description,
                           CONVERT(VARCHAR(10), DATA_INIZIO,  101) AS date_start,
                           CONVERT(VARCHAR(10), DATA_TERMINE, 101) AS date_end
                    FROM CONTRATTITESTATA WHERE ID_CONTRATTITESTATA = %d
                """ % contract_id)
                hdr = cur.fetchone()
                if not hdr:
                    return None

                cur.execute("""
                    SELECT cr.ID_CONTRATTIRIGHE AS line_id,
                           cr.DESCRIZIONE       AS description,
                           COUNT(tp.ID_TPALINSE) AS spot_count
                    FROM CONTRATTIRIGHE cr
                    JOIN trafficPalinse tpa ON tpa.id_contrattirighe = cr.ID_CONTRATTIRIGHE
                    JOIN TPALINSE tp        ON tp.ID_TPALINSE = tpa.id_tpalinse
                    WHERE cr.ID_CONTRATTITESTATA = %d
                    GROUP BY cr.ID_CONTRATTIRIGHE, cr.DESCRIZIONE
                    ORDER BY cr.ID_CONTRATTIRIGHE
                """ % contract_id)
                lines = [dict(r) for r in cur.fetchall()]

                cur.execute("""
                    SELECT f.ID_FILMATI AS filmati_id, f.COD_PROGRA AS code,
                           f.DESCRIZIO  AS title,      f.DURATA     AS durata,
                           COUNT(*)     AS count
                    FROM TPALINSE tp
                    JOIN trafficPalinse tpa ON tpa.id_tpalinse       = tp.ID_TPALINSE
                    JOIN CONTRATTIRIGHE cr  ON cr.ID_CONTRATTIRIGHE  = tpa.id_contrattirighe
                    LEFT JOIN FILMATI f     ON f.ID_FILMATI = tp.ID_FILMATI
                    WHERE cr.ID_CONTRATTITESTATA = %d
                    GROUP BY f.ID_FILMATI, f.COD_PROGRA, f.DESCRIZIO, f.DURATA
                    ORDER BY COUNT(*) DESC
                """ % contract_id)
                rotation_rows = cur.fetchall()
                total = sum(r["count"] for r in rotation_rows) if rotation_rows else 0

                cur.execute("""
                    SELECT DISTINCT cr.COD_USER AS cod_user
                    FROM CONTRATTIRIGHE cr
                    JOIN trafficPalinse tpa ON tpa.id_contrattirighe = cr.ID_CONTRATTIRIGHE
                    WHERE cr.ID_CONTRATTITESTATA = %d
                """ % contract_id)
                _code_by_id = {v: k for k, v in _MARKET_CODES.items()}
                markets = [
                    _code_by_id[r["cod_user"]]
                    for r in cur.fetchall()
                    if r["cod_user"] in _code_by_id
                ]

                cur.execute("""
                    SELECT DISTINCT cr.DURATA AS durata
                    FROM TPALINSE tp
                    JOIN trafficPalinse tpa ON tpa.id_tpalinse = tp.ID_TPALINSE
                    JOIN CONTRATTIRIGHE cr ON cr.ID_CONTRATTIRIGHE = tpa.id_contrattirighe
                    WHERE cr.ID_CONTRATTITESTATA = %d AND cr.DURATA > 0
                    ORDER BY cr.DURATA
                """ % contract_id)
                durations = [
                    {"frames": r["durata"], "label": f":{round(r['durata'] / _FPS_GLOBAL)}"}
                    for r in cur.fetchall()
                ]

                return {
                    "header": dict(hdr),
                    "lines": lines,
                    "markets": markets,
                    "durations": durations,
                    "rotation": [
                        {**dict(r), "pct": round(r["count"] / total * 100, 1) if total else 0}
                        for r in rotation_rows
                    ],
                    "total_spots": total,
                }

        result = await asyncio.get_running_loop().run_in_executor(None, _run)
        if result is None:
            raise HTTPException(status_code=404, detail="Contract not found")
        return JSONResponse(result)

    @router.get("/api/traffic/spots/search")
    async def traffic_spots_search(
        q: str = Query(""),
        duration: Optional[int] = Query(None),
    ):
        if len(q) < 2:
            return JSONResponse([])

        def _run():
            from browser_automation.etere_direct_client import connect as _db_connect
            with _db_connect() as conn:
                cur = conn.cursor(as_dict=True)
                term = f"{q.upper()}%"
                if duration is not None:
                    cur.execute("""
                        SELECT ID_FILMATI AS id, COD_PROGRA AS code,
                               DESCRIZIO AS title, DURATA AS durata
                        FROM FILMATI
                        WHERE UPPER(DESCRIZIO) LIKE %s
                          AND DURATA BETWEEN %s AND %s
                          AND TIPO = 'T'
                        ORDER BY DESCRIZIO
                    """, (term, duration - 5, duration + 5))
                else:
                    cur.execute("""
                        SELECT ID_FILMATI AS id, COD_PROGRA AS code,
                               DESCRIZIO AS title, DURATA AS durata
                        FROM FILMATI
                        WHERE UPPER(DESCRIZIO) LIKE %s
                          AND TIPO = 'T'
                          AND DURATA <= 1800
                        ORDER BY DESCRIZIO
                    """, (term,))
                rows = cur.fetchall()
            for r in rows:
                r["duration_sec"] = round(r["durata"] / 30) if r["durata"] else 0
            return rows

        try:
            rows = await asyncio.get_running_loop().run_in_executor(None, _run)
            return JSONResponse(rows)
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))

    @router.post("/api/traffic/contract/{contract_id}/assign")
    async def traffic_contract_assign(contract_id: int, body: dict = Body(...)):
        spots = body.get("spots", [])
        filters = body.get("filters", {})
        spots = [s for s in spots if s.get("weight", 0) > 0]
        if not spots:
            raise HTTPException(status_code=400, detail="No spots provided")
        total_weight = sum(s["weight"] for s in spots)
        if total_weight <= 0:
            raise HTTPException(status_code=400, detail="Weights must be > 0")

        def _run():
            from collections import defaultdict

            from browser_automation.etere_direct_client import (
                ETERE_WEB_URL,
            )
            from browser_automation.etere_direct_client import (
                connect as _db_connect,
            )

            filmati_ids = [s["id"] for s in spots]
            filter_sql = _build_spot_filter(filters)

            with _db_connect() as conn:
                cur = conn.cursor(as_dict=True)
                cur.execute(f"""
                    SELECT tpa.id_contrattirighe AS line_id,
                           tp.ID_TPALINSE        AS tp_id,
                           cr.ID_BOOKINGCODE     AS booking_code
                    FROM TPALINSE tp
                    JOIN trafficPalinse tpa ON tpa.id_tpalinse       = tp.ID_TPALINSE
                    JOIN CONTRATTIRIGHE cr  ON cr.ID_CONTRATTIRIGHE  = tpa.id_contrattirighe
                    WHERE cr.ID_CONTRATTITESTATA = {contract_id}
                    {filter_sql}
                    ORDER BY tp.DATA, tp.ORA
                """)
                all_rows = cur.fetchall()

                placeholders = ",".join(str(i) for i in filmati_ids)
                cur.execute(
                    f"SELECT ID_FILMATI, COD_PROGRA, DESCRIZIO FROM FILMATI WHERE ID_FILMATI IN ({placeholders})"
                )
                filmati_rows = cur.fetchall()
                filmati_cod_map = {r["ID_FILMATI"]: (r["COD_PROGRA"] or "") for r in filmati_rows}
                filmati_title_map = {r["ID_FILMATI"]: (r["DESCRIZIO"] or "") for r in filmati_rows}

                # Build SUPPORTO string per filmati from FS_FILMATI + FS_METADEVICE.
                # Format: LEGACY_BASESUPP + FILE_ID  (e.g. "0ETX      TOY30M1206")
                cur.execute(f"""
                    SELECT ff.ID_FILMATI, ff.FILE_ID, ff.VIDEOSTANDARD, ff.DUR,
                           ISNULL(d.LEGACY_BASESUPP,
                                  CAST(d.LEGACY_MEDIAID AS VARCHAR) + 'ETX      ') AS supporto_prefix
                    FROM FS_FILMATI ff
                    JOIN FS_METADEVICE d ON d.ID_METADEVICE = ff.ID_METADEVICE
                    WHERE ff.ID_FILMATI IN ({placeholders})
                      AND d.LEGACY_MEDIAID IS NOT NULL
                """)
                # VIDEOSTANDARD "D" (digital HD) and null both map to ASPECT "H"
                _VS_TO_ASPECT = {"D": "H"}
                filmati_supporto_map: dict = {}
                filmati_aspect_map: dict = {}
                filmati_duration_map: dict = {}
                for r in cur.fetchall():
                    fid = r["ID_FILMATI"]
                    if fid not in filmati_supporto_map:
                        filmati_supporto_map[fid] = (r["supporto_prefix"] or "") + (r["FILE_ID"] or "")
                        filmati_aspect_map[fid] = _VS_TO_ASPECT.get(r["VIDEOSTANDARD"], "H")
                        filmati_duration_map[fid] = r["DUR"] or 0

            if not all_rows:
                raise ValueError("No scheduled spots found matching the selected filters")

            _BOOKINGCODE_TO_NEWTYPE = {2: "COM", 10: "BNS"}
            line_tp_map = defaultdict(list)
            tp_newtype_map: dict = {}
            for r in all_rows:
                line_tp_map[r["line_id"]].append(r["tp_id"])
                tp_newtype_map[r["tp_id"]] = _BOOKINGCODE_TO_NEWTYPE.get(r["booking_code"], "COM")

            # Calculate rotation % per filmati (sum = 100, last spot absorbs rounding remainder)
            perc_map: dict = {}
            remaining = 100
            for i, s in enumerate(spots):
                if i == len(spots) - 1:
                    perc_map[s["id"]] = remaining
                else:
                    p = round(s["weight"] / total_weight * 100)
                    perc_map[s["id"]] = p
                    remaining -= p

            total_spots = len(all_rows)

            # Build chronologically-interleaved rotation list (Bresenham-style)
            # e.g. 50/50 → A,B,A,B,...  70/30 → A,A,A,B,A,A,A,B,...
            rotation_list = []
            accum = {s["id"]: 0.0 for s in spots}
            for _ in range(total_spots):
                for s in spots:
                    accum[s["id"]] += s["weight"] / total_weight
                chosen = max(accum, key=accum.__getitem__)
                rotation_list.append(chosen)
                accum[chosen] -= 1.0

            # Map each tp_id to its rotation filmati using global chronological order
            # so the A,B,A,B interleave is correct across the whole contract, not per-line
            tp_filmati_map = {row["tp_id"]: rotation_list[i] for i, row in enumerate(all_rows)}

            # Check which filmati are already in the pool so we don't create duplicates
            line_ids_str = ",".join(str(lid) for lid in line_tp_map.keys())
            with _db_connect() as conn_pool:
                cur_pool = conn_pool.cursor(as_dict=True)
                cur_pool.execute(
                    f"SELECT DISTINCT ID_FILMATI FROM CONTRATTIFILMATI"
                    f" WHERE ID_CONTRATTIRIGHE IN ({line_ids_str})"
                )
                existing_pool = {r["ID_FILMATI"] for r in cur_pool.fetchall()}

            session = _get_etere_session()
            lines_updated = spots_updated = 0
            tp_assignments = []  # (tp_id, filmati_id) pairs
            try:
                # Only add filmati not already in the pool — MaterialAddToAssetListC is not idempotent
                for fid in filmati_ids:
                    if fid in existing_pool:
                        continue
                    r = session.post(
                        f"{ETERE_WEB_URL}/Sales/MaterialAddToAssetListC",
                        json={"idFilmatiList": [fid], "idct": contract_id},
                        headers={"X-Requested-With": "XMLHttpRequest"},
                        timeout=30,
                    )
                    r.raise_for_status()
                    add_result = r.json()
                    if not add_result.get("IsOk"):
                        raise ValueError(f"MaterialAddToAssetListC failed for {fid}: {add_result}")

                for line_id, tp_ids in line_tp_map.items():
                    n = len(tp_ids)
                    slice_ = [tp_filmati_map[tp_id] for tp_id in tp_ids]
                    r = session.post(
                        f"{ETERE_WEB_URL}/Sales/MaterialAssignAssetRotation",
                        json={
                            "idp": list(tp_ids),
                            "idf": list(slice_),
                            "idcr": line_id,
                        },
                        headers={"X-Requested-With": "XMLHttpRequest"},
                        timeout=30,
                    )
                    r.raise_for_status()
                    assign_result = r.json()
                    if not assign_result.get("IsOk"):
                        raise ValueError(f"MaterialAssignAssetRotation line {line_id} failed: {assign_result}")
                    lines_updated += 1
                    spots_updated += n
                    tp_assignments.extend(zip(tp_ids, slice_))

            except Exception:
                # Invalidate cache so next call gets a fresh login
                _invalidate_etere_session()
                raise

            # Sync COD_PROGRA and PERCROTATION so the native app reflects the rotation
            if tp_assignments:
                with _db_connect() as conn:
                    cur = conn.cursor()
                    for tp_id, filmati_id in tp_assignments:
                        cod = filmati_cod_map.get(filmati_id, "")
                        title = filmati_title_map.get(filmati_id, "")
                        newtype = tp_newtype_map.get(tp_id, "COM")
                        supporto = filmati_supporto_map.get(filmati_id, "")
                        aspect = filmati_aspect_map.get(filmati_id, "H")
                        duration_p = filmati_duration_map.get(filmati_id, 0)
                        cur.execute(
                            "UPDATE TPALINSE SET COD_PROGRA = %s, TITLE = %s, ID_FILMATI = %d,"
                            " NEWTYPE = %s, SUPPORTO = %s, ASPECT = %s, DURATION_P = %d"
                            " WHERE ID_TPALINSE = %d",
                            (cod, title, filmati_id, newtype, supporto, aspect, duration_p, tp_id),
                        )
                    # Update PERCROTATION per (line, filmati) so native app shows correct %
                    for line_id in line_tp_map.keys():
                        for filmati_id, perc in perc_map.items():
                            cur.execute(
                                "UPDATE CONTRATTIFILMATI SET PERCROTATION = %d"
                                " WHERE ID_CONTRATTIRIGHE = %d AND ID_FILMATI = %d",
                                (perc, line_id, filmati_id),
                            )
                    # Remove spurious pool rows: MaterialAddToAssetListC adds each filmati
                    # to every line in the contract; delete rows where PERCROTATION=0
                    # so only lines that actually use the filmati remain in the pool.
                    if filmati_ids:
                        fid_str = ",".join(str(f) for f in filmati_ids)
                        cur.execute(
                            f"DELETE FROM CONTRATTIFILMATI"
                            f" WHERE ID_FILMATI IN ({fid_str})"
                            f" AND PERCROTATION = 0"
                            f" AND ID_CONTRATTIRIGHE IN ("
                            f"   SELECT ID_CONTRATTIRIGHE FROM CONTRATTIRIGHE"
                            f"   WHERE ID_CONTRATTITESTATA = {contract_id}"
                            f" )"
                        )
                    conn.commit()

            return {"ok": True, "spots_updated": spots_updated, "lines_updated": lines_updated}

        try:
            result = await asyncio.get_running_loop().run_in_executor(None, _run)
            return JSONResponse(result)
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))

    @router.post("/api/traffic/contract/{contract_id}/preview")
    async def traffic_contract_preview(contract_id: int, body: dict = Body({})):
        filters = body.get("filters", {})

        def _run():
            from browser_automation.etere_direct_client import connect as _db_connect
            filter_sql = _build_spot_filter(filters)
            with _db_connect() as conn:
                cur = conn.cursor(as_dict=True)
                cur.execute(f"""
                    SELECT COUNT(*) AS n
                    FROM TPALINSE tp
                    JOIN trafficPalinse tpa ON tpa.id_tpalinse      = tp.ID_TPALINSE
                    JOIN CONTRATTIRIGHE cr  ON cr.ID_CONTRATTIRIGHE = tpa.id_contrattirighe
                    WHERE cr.ID_CONTRATTITESTATA = {contract_id}
                    {filter_sql}
                """)
                return {"count": cur.fetchone()["n"]}

        try:
            result = await asyncio.get_running_loop().run_in_executor(None, _run)
            return JSONResponse(result)
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))

    @router.post("/api/traffic/contract/{contract_id}/clear")
    async def traffic_contract_clear(contract_id: int, body: dict = Body({})):
        filters = body.get("filters", {})

        def _run():
            from browser_automation.etere_direct_client import connect as _db_connect
            filter_sql = _build_spot_filter(filters)
            with _db_connect() as conn:
                cur = conn.cursor()
                cur.execute(f"""
                    UPDATE tp
                    SET tp.COD_PROGRA = '', tp.TITLE = cr.DESCRIZIONE, tp.ID_FILMATI = 0
                    FROM TPALINSE tp
                    JOIN trafficPalinse tpa ON tpa.id_tpalinse      = tp.ID_TPALINSE
                    JOIN CONTRATTIRIGHE cr  ON cr.ID_CONTRATTIRIGHE = tpa.id_contrattirighe
                    WHERE cr.ID_CONTRATTITESTATA = {contract_id}
                    {filter_sql}
                """)
                cleared = cur.rowcount
                conn.commit()
            return {"ok": True, "cleared": cleared}

        try:
            result = await asyncio.get_running_loop().run_in_executor(None, _run)
            return JSONResponse(result)
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))

    return router
