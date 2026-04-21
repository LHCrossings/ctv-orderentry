"""
Order queue routes: list, upload, move-to-used, history, restore, detail.
"""

import asyncio
import json
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Body, HTTPException, Query, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.templating import Jinja2Templates

_src_path = Path(__file__).parent.parent.parent
if str(_src_path) not in sys.path:
    sys.path.insert(0, str(_src_path))

from business_logic.services.pdf_order_detector import PDFOrderDetector
from orchestration.config import ApplicationConfig
from orchestration.order_scanner import OrderScanner
from web.parser_bridge import get_order_detail, list_parsers

_ALLOWED_EXTENSIONS = {".pdf", ".xml", ".xlsx", ".xlsm", ".jpg", ".jpeg", ".png"}


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
        import sqlite3, tempfile, os
        from pathlib import Path

        _SKIP = {"created_at"}
        target_path = Path("data/customers.db")

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

        finally:
            os.unlink(tmp_path)

        return {
            "new":       new_records,
            "conflicts": conflicts,
            "columns":   common_cols,
        }

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

    return router
