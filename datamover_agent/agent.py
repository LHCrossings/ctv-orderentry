"""
Datamover Aircheck Agent
Runs on the Datamover Windows machine.
Accepts capture requests from Control Room, runs FFmpeg, serves downloads.

Run: python agent.py
     (or: uvicorn agent:app --host 0.0.0.0 --port 8765)
"""
import asyncio
import json
import logging
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

# ── CONFIG ───────────────────────────────────────────────────────────────────
FFMPEG     = "ffmpeg"                    # update if not in PATH: r"C:\ffmpeg\bin\ffmpeg.exe"
OUTPUT_DIR = Path(r"C:\Airchecks")
DB_FILE    = OUTPUT_DIR / "captures.json"
SRT_HOST   = "44.235.103.12"
PORT       = 8765

ETERE_DB_SERVER   = "100.85.38.72"      # Etere SQL Server (internal 10.0.0 or Tailscale)
ETERE_DB_NAME     = "Etere_crossing"
POLL_INTERVAL_SEC = 600                 # 10 minutes
RESCHEDULE_MIN_SHIFT_SEC = 30           # ignore sub-30-second drift

# ── OneDrive auto-upload ──────────────────────────────────────────────────────
ONEDRIVE_ROOT           = Path(r"C:\Users\usrdm1\OneDrive - crossingstv.com\Airchecks")
ONEDRIVE_RETENTION_DAYS = 90        # overridden at runtime by /settings PATCH
SETTINGS_FILE           = OUTPUT_DIR / "settings.json"

# Client name → agency subfolder inside ONEDRIVE_ROOT
CLIENT_AGENCY: dict[str, str] = {
    "McDonald's":           "Admerasia",
    "Lexus Dealer Association": "Lexus",
}

NETWORK_PORTS: dict[str, int] = {
    "NYC":     6014,
    "WDC":     6017,
    "CMP":     6010,
    "HOU":     6012,
    "SEA":     6002,
    "SFO":     6004,
    "CVC":     6006,
    "LAX":     6008,
    "DAL":     6015,
    "MMT":     6019,
    "SFO OTA": 6016,
    "CVC OTA": 6018,
}

# Etere COD_USER integer for each network — used for tcFrames2Msec conversion
NETWORK_COD_USER: dict[str, int] = {
    "NYC": 1, "CMP": 2, "HOU": 3, "SFO": 4,
    "SEA": 5, "LAX": 6, "CVC": 7, "WDC": 8,
    "MMT": 9, "DAL": 10, "SFO OTA": 4, "CVC OTA": 7,
}

# Hours to ADD to market-local time to arrive at PT (agent runs in PT).
# ET is always 3h ahead of PT; CT always 2h ahead; PT markets are 0.
NETWORK_TO_PT_HOURS: dict[str, int] = {
    "NYC": -3, "WDC": -3, "MMT": -3,
    "CMP": -2, "HOU": -2, "DAL": -2,
    "SFO": 0, "SEA": 0, "LAX": 0, "CVC": 0,
    "SFO OTA": 0, "CVC OTA": 0,
}
# ─────────────────────────────────────────────────────────────────────────────

app = FastAPI(title="Datamover Aircheck Agent", docs_url=None, redoc_url=None)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

captures: dict[str, dict] = {}
_tasks:   dict[str, asyncio.Task] = {}
_poll_state: dict = {
    "last_attempted": None,
    "last_succeeded": None,
    "last_error":     None,
    "candidate_count": 0,
}


def _load_settings() -> None:
    global ONEDRIVE_RETENTION_DAYS
    if SETTINGS_FILE.exists():
        try:
            data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
            ONEDRIVE_RETENTION_DAYS = int(data.get("onedrive_retention_days", ONEDRIVE_RETENTION_DAYS))
        except Exception:
            pass


def _save_settings() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    SETTINGS_FILE.write_text(json.dumps({"onedrive_retention_days": ONEDRIVE_RETENTION_DAYS}, indent=2), encoding="utf-8")


def _load_db() -> None:
    if DB_FILE.exists():
        try:
            data: dict = json.loads(DB_FILE.read_text(encoding="utf-8"))
            for cap in data.values():
                if cap.get("status") == "recording":
                    # Was actively recording when agent stopped — genuinely lost
                    cap["status"] = "error"
                    cap["error"] = "Agent restarted during capture"
                # backfill fields added after initial deployment
                cap.setdefault("last_polled_at", None)
                cap.setdefault("reschedule_history", [])
                # pending/scheduled captures are re-queued in startup()
            captures.update(data)
        except Exception:
            pass


async def _requeue_scheduled() -> None:
    now = datetime.now()
    for cap_id, cap in list(captures.items()):
        if cap.get("status") not in ("pending", "scheduled"):
            continue
        start_dt = datetime.fromisoformat(cap["start_time"])
        window_end = start_dt + timedelta(seconds=cap.get("duration_seconds", 0))
        if window_end < now:
            # Window has fully passed — missed it
            cap["status"] = "error"
            cap["error"] = "Missed: agent was offline during scheduled window"
        else:
            # Still time to record — re-queue (delay=0 if start already passed)
            delay = max(0.0, (start_dt - now).total_seconds())
            cap["status"] = "pending"
            _tasks[cap_id] = asyncio.create_task(_schedule(cap_id, delay))
    _save_db()


@app.on_event("startup")
async def startup() -> None:
    _load_settings()
    _load_db()
    await _requeue_scheduled()
    asyncio.create_task(_poll_spots())
    asyncio.create_task(_onedrive_cleanup_loop())


async def _onedrive_cleanup_loop() -> None:
    """Once a day: delete files from OneDrive older than ONEDRIVE_RETENTION_DAYS."""
    while True:
        cutoff = datetime.now() - timedelta(days=ONEDRIVE_RETENTION_DAYS)
        deleted = 0
        try:
            for f in ONEDRIVE_ROOT.rglob("*.mp4"):
                try:
                    if datetime.fromtimestamp(f.stat().st_mtime) < cutoff:
                        f.unlink()
                        deleted += 1
                        # Remove empty parent dirs (but not ONEDRIVE_ROOT itself)
                        try:
                            if f.parent != ONEDRIVE_ROOT and not any(f.parent.iterdir()):
                                f.parent.rmdir()
                        except Exception:
                            pass
                except Exception as exc:
                    logging.warning("OneDrive cleanup: could not remove %s: %s", f, exc)
            if deleted:
                logging.info("OneDrive cleanup: removed %d file(s) older than %d days", deleted, ONEDRIVE_RETENTION_DAYS)
        except Exception as exc:
            logging.warning("OneDrive cleanup: scan failed: %s", exc)
        await asyncio.sleep(86400)  # run daily


def _save_db() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    DB_FILE.write_text(json.dumps(captures, indent=2), encoding="utf-8")


def _safe(s: str) -> str:
    return "".join(c for c in s if c.isalnum() or c in "-_").strip() or "capture"

def _cap_path(cap: dict) -> Path:
    subfolder = cap.get("subfolder", "")
    return (OUTPUT_DIR / subfolder / cap["filename"]) if subfolder else (OUTPUT_DIR / cap["filename"])


# ── Schema ────────────────────────────────────────────────────────────────────

class CaptureRequest(BaseModel):
    client: str
    network: str
    duration_seconds: int
    start_time: Optional[str] = None  # ISO local datetime string; None = start immediately
    notes: str = ""
    isci_code: Optional[str] = None   # spot code; enables Etere polling to track schedule moves
    original_ora: Optional[int] = None  # TPALINSE ORA value (frames) at time of scheduling

class CaptureUpdate(BaseModel):
    start_time: Optional[str] = None
    duration_seconds: Optional[int] = None
    notes: Optional[str] = None


# ── OneDrive upload ───────────────────────────────────────────────────────────

def _onedrive_upload(cap: dict) -> None:
    import shutil
    agency    = CLIENT_AGENCY.get(cap.get("client", ""), "")
    subfolder = cap.get("subfolder", "")
    parts     = [p for p in (agency, subfolder) if p]
    dest_dir  = ONEDRIVE_ROOT.joinpath(*parts) if parts else ONEDRIVE_ROOT
    try:
        dest_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(str(_cap_path(cap)), str(dest_dir / cap["filename"]))
        logging.info("OneDrive: copied %s → %s", cap["filename"], dest_dir)
    except Exception as exc:
        logging.warning("OneDrive: upload failed for %s: %s", cap.get("filename"), exc)


# ── FFmpeg runner ─────────────────────────────────────────────────────────────

async def _run(cap_id: str) -> None:
    cap = captures[cap_id]
    cap["status"] = "recording"
    _save_db()

    port   = NETWORK_PORTS[cap["network"]]
    output = _cap_path(cap)
    dur    = cap["duration_seconds"]

    # Match the exact pattern the user tested:
    # ffmpeg -i "srt://host:port?mode=caller" -c copy -t DURATION output.mp4
    cmd = [
        FFMPEG,
        "-i", f"srt://{SRT_HOST}:{port}?mode=caller",
        "-c", "copy",
        "-t", str(dur),
        "-y", str(output),
    ]

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()

        if proc.returncode == 0 and output.exists():
            cap["status"]     = "complete"
            cap["size_bytes"] = output.stat().st_size
            cap["ended_at"]   = datetime.now().isoformat()
            _onedrive_upload(cap)
        else:
            cap["status"] = "error"
            cap["error"]  = stderr.decode(errors="replace")[-600:]
    except Exception as exc:
        cap["status"] = "error"
        cap["error"]  = str(exc)

    _save_db()


async def _schedule(cap_id: str, delay: float) -> None:
    if delay > 0:
        captures[cap_id]["status"] = "scheduled"
        _save_db()
        try:
            await asyncio.sleep(delay)
        except asyncio.CancelledError:
            return  # cancelled by edit or delete
    await _run(cap_id)


# ── Etere polling ─────────────────────────────────────────────────────────────

def _etere_connect():
    import pymssql
    db_server = ETERE_DB_SERVER
    db_user = db_pass = None
    try:
        env_path = Path(__file__).parent / ".env"
        if env_path.exists():
            for line in env_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if "=" not in line or line.startswith("#"):
                    continue
                key, _, val = line.partition("=")
                key = key.strip()
                val = val.strip()
                if key == "ETERE_DB_SERVER":
                    db_server = val
                elif key == "ETERE_DB_USER":
                    db_user = val
                elif key == "ETERE_DB_PASSWORD":
                    db_pass = val
    except Exception:
        pass
    return pymssql.connect(server=db_server, user=db_user, password=db_pass, database=ETERE_DB_NAME)


async def _do_poll() -> dict:
    """Run one poll cycle. Returns a summary dict. Called by the loop and the /poll endpoint."""
    now = datetime.now()
    candidates = [
        cap for cap in list(captures.values())
        if cap.get("isci_code")
        and cap.get("original_ora") is not None
        and cap.get("status") in ("pending", "scheduled")
        and datetime.fromisoformat(cap["start_time"]) > now + timedelta(minutes=2)
    ]
    _poll_state["last_attempted"] = now.isoformat()
    _poll_state["candidate_count"] = len(candidates)
    _poll_state["last_error"] = None

    if not candidates:
        _poll_state["last_succeeded"] = now.isoformat()
        return {"polled": 0, "rescheduled": 0, "candidates": 0}

    loop = asyncio.get_event_loop()
    try:
        conn = await loop.run_in_executor(None, _etere_connect)
    except Exception as exc:
        msg = str(exc)
        _poll_state["last_error"] = f"DB connect failed: {msg}"
        logging.warning("Etere poll: DB connect failed: %s", msg)
        return {"error": _poll_state["last_error"], "candidates": len(candidates)}

    polled = 0
    rescheduled = 0
    try:
        cur = conn.cursor()
        for cap in candidates:
            isci      = cap["isci_code"]
            start_dt  = datetime.fromisoformat(cap["start_time"])
            query_date = (start_dt + timedelta(seconds=20)).date()
            try:
                cur.execute(
                    "SELECT TOP 1 ORA, DATA, "
                    "dbo.tcFrames2Msec(dbo.getVideoStandard(COD_USER), ORA) AS air_ms "
                    "FROM TPALINSE "
                    "WHERE COD_PROGRA = %s AND DATA = %s AND ORA > 0 "
                    "ORDER BY ORA",
                    (isci, query_date),
                )
                row = cur.fetchone()
            except Exception as exc:
                logging.warning("Poll: TPALINSE query failed for %s: %s", isci, exc)
                continue

            cap["last_polled_at"] = datetime.now().isoformat()
            polled += 1

            if row is None:
                logging.warning("Poll: %s not found in TPALINSE on %s — spot may have been pulled", isci, query_date)
                continue

            new_ora  = row[0]
            air_date = row[1]
            air_ms   = row[2]
            orig_ora = cap["original_ora"]
            shift_sec = (new_ora - orig_ora) / 30  # approximate delta for threshold check

            if abs(shift_sec) < RESCHEDULE_MIN_SHIFT_SEC:
                logging.debug("Poll: %s unchanged (shift %.1fs < threshold) — %s still at %s", isci, shift_sec, cap["id"], start_dt)
                continue

            cap_id = cap["id"]
            air_date_only = air_date.date() if hasattr(air_date, "date") else air_date
            naive_local = datetime.combine(air_date_only, datetime.min.time()) + timedelta(milliseconds=air_ms)
            new_air_pt  = naive_local + timedelta(hours=NETWORK_TO_PT_HOURS.get(cap["network"], 0))
            new_start   = new_air_pt - timedelta(seconds=20)
            cap.setdefault("reschedule_history", []).append({
                "detected_at": datetime.now().isoformat(),
                "old_ora":     orig_ora,
                "new_ora":     new_ora,
                "shift_sec":   round(shift_sec, 1),
                "old_start":   start_dt.isoformat(),
                "new_start":   new_start.isoformat(),
            })
            logging.info("Poll: %s shifted %+.0fs — rescheduling %s to %s", isci, shift_sec, cap_id, new_start)

            task = _tasks.pop(cap_id, None)
            if task:
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass

            cap["start_time"]  = new_start.isoformat()
            cap["original_ora"] = new_ora
            net_s    = _safe(cap["network"].replace(" ", "_"))
            client_s = _safe(cap["client"].replace(" ", "_"))
            cap["filename"] = f"{net_s}_{client_s}_{new_start.strftime('%Y%m%d_%H%M')}.mp4"
            delay = max(0.0, (new_start - datetime.now()).total_seconds())
            cap["status"] = "pending"
            _save_db()
            _tasks[cap_id] = asyncio.create_task(_schedule(cap_id, delay))
            rescheduled += 1
    finally:
        conn.close()

    _poll_state["last_succeeded"] = datetime.now().isoformat()
    _save_db()
    return {"polled": polled, "rescheduled": rescheduled, "candidates": len(candidates)}


async def _poll_spots() -> None:
    """Every 10 min: re-check TPALINSE for any contract-linked captures that have moved."""
    while True:
        await asyncio.sleep(POLL_INTERVAL_SEC)
        await _do_poll()


# ── Routes ────────────────────────────────────────────────────────────────────

@app.post("/captures", status_code=201)
async def create_capture(req: CaptureRequest):
    if req.network not in NETWORK_PORTS:
        raise HTTPException(400, f"Unknown network: {req.network}")
    if req.duration_seconds < 5 or req.duration_seconds > 7200:
        raise HTTPException(400, "Duration must be 5–7200 seconds")

    now    = datetime.now()
    cap_id = uuid.uuid4().hex[:8]
    subfolder = _safe(req.notes) if req.notes.strip() else ""
    cap_dir   = OUTPUT_DIR / subfolder if subfolder else OUTPUT_DIR
    cap_dir.mkdir(parents=True, exist_ok=True)

    if req.start_time:
        start_dt = datetime.fromisoformat(req.start_time)
        if start_dt.tzinfo is not None:
            start_dt = start_dt.astimezone().replace(tzinfo=None)
        delay = max(0.0, (start_dt - now).total_seconds())
    else:
        start_dt = now
        delay    = 0.0

    net_s    = _safe(req.network.replace(" ", "_"))
    client_s = _safe(req.client.replace(" ", "_"))
    ts       = start_dt.strftime("%Y%m%d_%H%M")
    filename = f"{net_s}_{client_s}_{ts}.mp4"

    captures[cap_id] = {
        "id":                  cap_id,
        "client":              req.client,
        "network":             req.network,
        "duration_seconds":    req.duration_seconds,
        "start_time":          start_dt.isoformat(),
        "subfolder":           subfolder,
        "filename":            filename,
        "notes":               req.notes,
        "isci_code":           req.isci_code or None,
        "original_ora":        req.original_ora,
        "status":              "pending",
        "created_at":          now.isoformat(),
        "ended_at":            None,
        "size_bytes":          None,
        "error":               None,
        "last_polled_at":      None,
        "reschedule_history":  [],
    }
    _save_db()

    _tasks[cap_id] = asyncio.create_task(_schedule(cap_id, delay))
    return captures[cap_id]


@app.get("/captures")
async def list_captures():
    return sorted(captures.values(), key=lambda c: c["created_at"], reverse=True)


@app.get("/captures/{cap_id}")
async def get_capture(cap_id: str):
    if cap_id not in captures:
        raise HTTPException(404, "Not found")
    return captures[cap_id]


@app.get("/captures/{cap_id}/download")
async def download_capture(cap_id: str):
    if cap_id not in captures:
        raise HTTPException(404, "Not found")
    cap = captures[cap_id]
    if cap["status"] != "complete":
        raise HTTPException(400, "Capture not complete yet")
    path = _cap_path(cap)
    if not path.exists():
        raise HTTPException(404, "File missing from disk")
    return FileResponse(str(path), media_type="video/mp4", filename=cap["filename"])


@app.patch("/captures/{cap_id}")
async def update_capture(cap_id: str, req: CaptureUpdate):
    if cap_id not in captures:
        raise HTTPException(404, "Not found")
    cap = captures[cap_id]
    if cap["status"] not in ("scheduled", "pending"):
        raise HTTPException(400, "Can only edit scheduled or pending captures")

    task = _tasks.pop(cap_id, None)
    if task:
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass

    now = datetime.now()
    if req.start_time is not None:
        start_dt = datetime.fromisoformat(req.start_time)
        if start_dt.tzinfo is not None:
            start_dt = start_dt.astimezone().replace(tzinfo=None)
        cap["start_time"] = start_dt.isoformat()
        net_s    = _safe(cap["network"].replace(" ", "_"))
        client_s = _safe(cap["client"].replace(" ", "_"))
        cap["filename"] = f"{net_s}_{client_s}_{start_dt.strftime('%Y%m%d_%H%M')}.mp4"
    else:
        start_dt = datetime.fromisoformat(cap["start_time"])

    if req.duration_seconds is not None:
        if not (5 <= req.duration_seconds <= 7200):
            raise HTTPException(400, "Duration must be 5–7200 seconds")
        cap["duration_seconds"] = req.duration_seconds
    if req.notes is not None:
        cap["notes"] = req.notes

    delay = max(0.0, (start_dt - now).total_seconds())
    cap["status"] = "pending"
    _save_db()
    _tasks[cap_id] = asyncio.create_task(_schedule(cap_id, delay))
    return cap


@app.delete("/captures/{cap_id}")
async def delete_capture(cap_id: str):
    if cap_id not in captures:
        raise HTTPException(404, "Not found")
    task = _tasks.pop(cap_id, None)
    if task:
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass
    cap = captures.pop(cap_id)
    path = _cap_path(cap)
    if path.exists():
        try:
            path.unlink()
        except Exception:
            pass
    _save_db()
    return {"deleted": cap_id}


class SettingsUpdate(BaseModel):
    onedrive_retention_days: Optional[int] = None

@app.get("/settings")
async def get_settings():
    return {"onedrive_retention_days": ONEDRIVE_RETENTION_DAYS}

@app.patch("/settings")
async def update_settings(req: SettingsUpdate):
    global ONEDRIVE_RETENTION_DAYS
    if req.onedrive_retention_days is not None:
        if not (1 <= req.onedrive_retention_days <= 3650):
            raise HTTPException(400, "Retention must be 1–3650 days")
        ONEDRIVE_RETENTION_DAYS = req.onedrive_retention_days
        _save_settings()
    return {"onedrive_retention_days": ONEDRIVE_RETENTION_DAYS}


@app.post("/deploy")
async def deploy():
    import shutil as _sh
    import subprocess as _sp
    repo_dir = Path(r"C:\windev\ctv-orderentry")
    src      = repo_dir / "datamover_agent" / "agent.py"
    dst      = Path(r"C:\datamover_agent\agent.py")
    nssm     = Path(r"C:\datamover_agent\nssm.exe")
    svc_name = "AirchecksAgentSvc"

    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(
        None,
        lambda: _sp.run(["git", "pull"], cwd=str(repo_dir), capture_output=True, text=True),
    )
    if result.returncode != 0:
        raise HTTPException(500, f"git pull failed: {result.stderr.strip()}")
    try:
        _sh.copy2(str(src), str(dst))
    except Exception as exc:
        raise HTTPException(500, f"File copy failed: {exc}")

    # Restart via detached process — agent dies after this fires
    _sp.Popen(
        ["cmd", "/c", f"timeout /t 3 /nobreak && {nssm} restart {svc_name}"],
        creationflags=_sp.DETACHED_PROCESS | _sp.CREATE_NEW_PROCESS_GROUP,
        close_fds=True,
    )
    return {"status": "deploying", "git_output": result.stdout.strip()}


@app.post("/poll")
async def manual_poll():
    """Trigger an immediate Etere poll cycle and return the result."""
    return await _do_poll()


@app.get("/health")
async def health():
    return {"ok": True, "captures": len(captures), "poll": _poll_state}


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    _load_db()
    uvicorn.run(app, host="0.0.0.0", port=PORT)
