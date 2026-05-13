"""
Datamover Aircheck Agent
Runs on the Datamover Windows machine.
Accepts capture requests from Control Room, runs FFmpeg, serves downloads.

Run: python agent.py
     (or: uvicorn agent:app --host 0.0.0.0 --port 8765)
"""
import asyncio
import json
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel

# ── CONFIG ───────────────────────────────────────────────────────────────────
FFMPEG     = "ffmpeg"                    # update if not in PATH: r"C:\ffmpeg\bin\ffmpeg.exe"
OUTPUT_DIR = Path(r"C:\Airchecks")
DB_FILE    = OUTPUT_DIR / "captures.json"
SRT_HOST   = "44.235.103.12"
PORT       = 8765

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


def _load_db() -> None:
    if DB_FILE.exists():
        try:
            data: dict = json.loads(DB_FILE.read_text(encoding="utf-8"))
            for cap in data.values():
                if cap.get("status") == "recording":
                    # Was actively recording when agent stopped — genuinely lost
                    cap["status"] = "error"
                    cap["error"] = "Agent restarted during capture"
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
    await _requeue_scheduled()


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

class CaptureUpdate(BaseModel):
    start_time: Optional[str] = None
    duration_seconds: Optional[int] = None
    notes: Optional[str] = None


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
        "id":               cap_id,
        "client":           req.client,
        "network":          req.network,
        "duration_seconds": req.duration_seconds,
        "start_time":       start_dt.isoformat(),
        "subfolder":        subfolder,
        "filename":         filename,
        "notes":            req.notes,
        "status":           "pending",
        "created_at":       now.isoformat(),
        "ended_at":         None,
        "size_bytes":       None,
        "error":            None,
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


@app.get("/health")
async def health():
    return {"ok": True, "captures": len(captures)}


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    _load_db()
    uvicorn.run(app, host="0.0.0.0", port=PORT)
