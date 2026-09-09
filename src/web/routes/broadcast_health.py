"""Broadcast Health — Stirlitz IP Multiviewer alarm feed → Control Room.

Phase 1: a small, cached status endpoint that polls the Stirlitz alarm Web API
(key-only auth) and rolls it up into per-station on-air health for the global
header indicator and the health dashboard.

Design + decisions: tasks/broadcast-health.md
API reference:      .claude/documents/stirlitz-multiviewer-api.md

Auth: the monitor access key is read from STIRLITZ_MONITOR_KEY (env or
credentials.env) — never hardcoded. The device is HTTP-only; we proxy it
server-side (keeps the key off the client, avoids mixed content, and a single
cached device poll serves every Control Room user regardless of headcount).
"""

import asyncio
import datetime as _dt
import json as _json
import os
import time
import urllib.request
from pathlib import Path

from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from business_logic.services.health_events import EventLog, diff_events

# The single Stirlitz box (see stirlitz-multiviewer-api.md). Overridable via env
# for testing, mirroring the hardcoded AGENT_URL pattern in airchecks.py.
STIRLITZ_HOST = os.environ.get("STIRLITZ_HOST", "http://34.208.18.64").rstrip("/")
_ALARM_PATH = "/alarmsState/monitor"

# One device poll per this window serves all clients (they poll us every ~10s).
_CACHE_TTL = 5.0

_cache: dict = {"ts": 0.0, "data": None}
_lock = asyncio.Lock()


def _monitor_key() -> str:
    """Monitor access key from STIRLITZ_MONITOR_KEY (env first, then the same
    .env / credentials.env the rest of the app uses). Empty string if unset."""
    key = os.environ.get("STIRLITZ_MONITOR_KEY", "").strip()
    if key:
        return key
    try:
        import credential_loader  # reuse its .env parser + root discovery

        root = Path(credential_loader.__file__).parent
        for name in (".env", "credentials.env"):
            p = root / name
            if p.is_file():
                v = credential_loader._parse_env_file(p).get("STIRLITZ_MONITOR_KEY", "").strip()
                if v:
                    return v
    except Exception:
        pass
    return ""


def _is_alarm_of_interest(title: str) -> bool:
    """Which alarm titles count as an outage (Lee, 2026-07-21): video off /
    freeze / black / blue, and audio off. Low audio level is intentionally
    ignored. Title-matching so any new "Video ..." alarm type is auto-caught."""
    t = (title or "").strip()
    if "Video" in t:
        return True
    if t == "Audio level - no data":
        return True
    return False  # e.g. "Audio track level below threshold" → ignored


def _summarize(raw: dict) -> dict:
    """Roll the flat alarmLines map up into per-station off-air status."""
    lines = raw.get("alarmLines", {}) or {}
    all_ids: set = set()
    stations: dict = {}
    for info in lines.values():
        if not isinstance(info, dict):
            continue
        sid = info.get("stationId") or info.get("stationName")
        if sid:
            all_ids.add(sid)
        title = info.get("title", "")
        active = str(info.get("alarm", "0")).strip() not in ("0", "")
        if not (active and _is_alarm_of_interest(title)):
            continue
        key = sid or "?"
        st = stations.setdefault(key, {"stationName": info.get("stationName") or key, "alarms": []})
        st["alarms"].append({"title": title, "since": info.get("alarmSince")})

    offair = []
    for sid, st in stations.items():
        sinces = [a["since"] for a in st["alarms"] if a["since"]]
        offair.append(
            {
                "stationId": sid,
                "stationName": st["stationName"],
                "titles": sorted({a["title"] for a in st["alarms"]}),
                "since": min(sinces) if sinces else None,
            }
        )
    offair.sort(key=lambda x: x["stationName"])

    return {
        "state": "offair" if offair else "ok",
        "checked_at": raw.get("currentDate"),
        "offair": offair,
        "counts": {"stations": len(all_ids), "offair": len(offair)},
        "unreachable": False,
    }


def _fetch_alarms() -> dict:
    """Blocking poll of the Stirlitz alarm feed. Never raises — returns an
    'unknown/unreachable' payload on any failure so the UI can degrade gracefully."""
    key = _monitor_key()
    if not key:
        return {
            "state": "unknown",
            "unreachable": True,
            "error": "STIRLITZ_MONITOR_KEY not configured",
            "offair": [],
            "counts": {"stations": 0, "offair": 0},
        }
    url = f"{STIRLITZ_HOST}{_ALARM_PATH}?accessKey={key}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "ctv-control-room"})
        with urllib.request.urlopen(req, timeout=3) as resp:
            raw = _json.loads(resp.read())
    except Exception as exc:  # noqa: BLE001 - device blip must not break the UI
        return {
            "state": "unknown",
            "unreachable": True,
            "error": str(exc),
            "offair": [],
            "counts": {"stations": 0, "offair": 0},
        }
    return _summarize(raw)


async def _get_status() -> dict:
    """Cached accessor: at most one device poll per _CACHE_TTL across all clients."""
    now = time.monotonic()
    if _cache["data"] is not None and (now - _cache["ts"]) < _CACHE_TTL:
        return _cache["data"]
    async with _lock:
        now = time.monotonic()
        if _cache["data"] is not None and (now - _cache["ts"]) < _CACHE_TTL:
            return _cache["data"]
        data = await asyncio.to_thread(_fetch_alarms)
        _cache["data"] = data
        _cache["ts"] = time.monotonic()
        _record(data)
        return data


# ---------------------------------------------------------------------------
# Media integrity — nightly file-size check (2026-09-07 TheOne090726B freeze).
#
# The rules live in business_logic/services/media_integrity.py (shared with
# scripts/check_media_sizes.py). The web app has no scheduler, so the first
# status poll after startup starts one background task: it scans immediately,
# then again every day at _MEDIA_HOUR (server clock = Pacific, after the
# evening ingest and before the 06:00 broadcast day). Findings ride along in the
# status payload so the header indicator turns amber on every page.
# ---------------------------------------------------------------------------
_MEDIA_HOUR = 3
_MEDIA_DAYS = 2
_media: dict = {"data": None, "task": None}
_media_lock = asyncio.Lock()


def _run_media_scan() -> dict:
    """Blocking scan; never raises (an unreachable DB must not break the header)."""
    try:
        from browser_automation.etere_direct_client import connect
        from src.business_logic.services.media_integrity import scan

        with connect() as conn:
            return scan(conn, days=_MEDIA_DAYS)
    except Exception as exc:  # noqa: BLE001
        return {
            "state": "unknown",
            "error": str(exc),
            "checked_at": _dt.datetime.now().isoformat(timespec="seconds"),
            "findings": [],
            "assets": 0,
        }


def _seconds_until(hour: int) -> float:
    now = _dt.datetime.now()
    nxt = now.replace(hour=hour, minute=0, second=0, microsecond=0)
    if nxt <= now:
        nxt += _dt.timedelta(days=1)
    return (nxt - now).total_seconds()


async def _media_rescan() -> dict:
    async with _media_lock:
        _media["data"] = await asyncio.to_thread(_run_media_scan)
    _record(_cache["data"])
    return _media["data"]


async def _media_loop() -> None:
    while True:
        await _media_rescan()
        await asyncio.sleep(_seconds_until(_MEDIA_HOUR))


def _ensure_media_task() -> None:
    t = _media.get("task")
    if t is None or t.done():
        _media["task"] = asyncio.get_running_loop().create_task(_media_loop())


def _media_summary() -> dict:
    """Compact form for the header: one line per finding, soonest airing first."""
    d = _media.get("data")
    if not d:
        return {"state": "unknown", "count": 0, "findings": []}
    items = []
    for f in d.get("findings", []):
        first = f["airings"][0] if f.get("airings") else None
        items.append(
            {
                "id": f["id_filmati"],
                "code": f["code"],
                "kind": f["kind"],
                "first": f"{first['market']} {first['date'][5:]} {first['time']}" if first else "",
            }
        )
    return {
        "state": d.get("state", "unknown"),
        "checked_at": d.get("checked_at"),
        "count": len(items),
        "findings": items,
    }


# ---------------------------------------------------------------------------
# Event log — transitions of what the header dot shows (2026-09-09, Maija: "did it turn
# yellow and we missed it?"). Only changes are written; a quiet day writes nothing.
# data/ is gitignored and per host, so the Jumpbox keeps its own history.
# ---------------------------------------------------------------------------
_EVENTS_PATH = Path(__file__).resolve().parents[3] / "data" / "broadcast_health_events.jsonl"
_events = EventLog(_EVENTS_PATH)
_last_snapshot: dict = {"data": None}


def _snapshot(status: dict | None) -> dict:
    status = status or {}
    return {
        "unreachable": bool(status.get("unreachable")) or status.get("state") == "unknown",
        "error": status.get("error", ""),
        "offair": status.get("offair") or [],
        "media": _media_summary().get("findings", []),
    }


def _record(status: dict | None) -> None:
    """Diff the current dot state against the last one seen and append the transitions.
    Never raises — a full disk must not break the header."""
    try:
        snap = _snapshot(status)
        events = diff_events(_last_snapshot["data"], snap)
        _last_snapshot["data"] = snap
        _events.append(events)
    except Exception:  # noqa: BLE001
        pass


def build_broadcast_health_router(templates: Jinja2Templates) -> APIRouter:
    router = APIRouter()

    @router.get("/api/broadcast-health/status")
    async def broadcast_health_status():
        _ensure_media_task()
        data = dict(await _get_status())
        data["media"] = _media_summary()
        return JSONResponse(data)

    @router.get("/api/broadcast-health/media")
    async def broadcast_health_media():
        _ensure_media_task()
        return JSONResponse(_media.get("data") or {"state": "unknown", "findings": [], "assets": 0})

    @router.post("/api/broadcast-health/media/rescan")
    async def broadcast_health_media_rescan():
        _ensure_media_task()
        return JSONResponse(await _media_rescan())

    @router.get("/master-control/media-check", response_class=HTMLResponse)
    async def media_check_page(request: Request):
        return templates.TemplateResponse(
            request,
            "master_control/media_check.html",
            {"media_hour": f"{_MEDIA_HOUR:02d}:00", "days": _MEDIA_DAYS},
        )

    @router.get("/api/broadcast-health/events")
    async def broadcast_health_events(days: int = Query(7, ge=1, le=90)):
        return JSONResponse({"days": days, "events": _events.read(days=days)})

    @router.get("/master-control/health-events", response_class=HTMLResponse)
    async def health_events_page(request: Request):
        return templates.TemplateResponse(request, "master_control/health_events.html", {})

    return router
