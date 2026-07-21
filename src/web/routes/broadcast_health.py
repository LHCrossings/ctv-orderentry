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
import json as _json
import os
import time
import urllib.request
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

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
        offair.append({
            "stationId": sid,
            "stationName": st["stationName"],
            "titles": sorted({a["title"] for a in st["alarms"]}),
            "since": min(sinces) if sinces else None,
        })
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
        return {"state": "unknown", "unreachable": True,
                "error": "STIRLITZ_MONITOR_KEY not configured",
                "offair": [], "counts": {"stations": 0, "offair": 0}}
    url = f"{STIRLITZ_HOST}{_ALARM_PATH}?accessKey={key}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "ctv-control-room"})
        with urllib.request.urlopen(req, timeout=3) as resp:
            raw = _json.loads(resp.read())
    except Exception as exc:  # noqa: BLE001 - device blip must not break the UI
        return {"state": "unknown", "unreachable": True, "error": str(exc),
                "offair": [], "counts": {"stations": 0, "offair": 0}}
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
        return data


def build_broadcast_health_router(templates: Jinja2Templates) -> APIRouter:
    router = APIRouter()

    @router.get("/api/broadcast-health/status")
    async def broadcast_health_status():
        return JSONResponse(await _get_status())

    @router.get("/multiviewer", response_class=HTMLResponse)
    async def multiviewer(request: Request):
        return templates.TemplateResponse(request, "multiviewer.html")

    return router
