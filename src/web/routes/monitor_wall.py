"""Monitor Wall — the Stirlitz IP Multiviewer's 0.0 screen inside Control Room.

Lee (2026-09-04): the Stirlitz index page always opens with BOTH "Preview 0.0" and
"Preview stream-REALTIME" selected — two identical monitor walls — and turning the
second off by hand every time is a nuisance. The device offers no URL/preference
for that, and its single-screen page (preview.html#screen=0.0) renders blank unless
the login cookie happens to be present. So Control Room shows the wall itself: this
module logs into the device with STIRLITZ_USER / STIRLITZ_PASS (env, then
credentials.env — never hardcoded), keeps the session, and proxies
`/live/screens/<screen>/preview` (a ~450 KB 1920x1080 JPEG) to the page, which
refreshes it a few times a second. Credentials and the session never reach the
browser. The alarm feed (broadcast_health.py) keeps using the key-only monitor API.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.templating import Jinja2Templates

STIRLITZ_HOST = os.environ.get("STIRLITZ_HOST", "http://34.208.18.64").rstrip("/")
DEFAULT_SCREEN = "0.0"
_SCREEN_RE = re.compile(r"^[A-Za-z0-9._-]{1,40}$")
_TIMEOUT = 6.0

_session: dict = {"id": None, "ts": 0.0}
_lock = threading.Lock()


def _credentials() -> tuple[str, str]:
    """(user, password) from env first, then .env / credentials.env next to credential_loader."""
    user = os.environ.get("STIRLITZ_USER", "").strip()
    pw = os.environ.get("STIRLITZ_PASS", "").strip()
    if user and pw:
        return user, pw
    try:
        import credential_loader

        root = Path(credential_loader.__file__).parent
        for name in (".env", "credentials.env"):
            p = root / name
            if p.is_file():
                env = credential_loader._parse_env_file(p)
                u, w = env.get("STIRLITZ_USER", "").strip(), env.get("STIRLITZ_PASS", "").strip()
                if u and w:
                    return u, w
    except Exception:
        pass
    return "", ""


def _login() -> str:
    """POST /sessions {user, passhash=md5(password)} → session id. Raises on failure."""
    user, pw = _credentials()
    if not (user and pw):
        raise RuntimeError("STIRLITZ_USER / STIRLITZ_PASS not configured")
    body = json.dumps({"user": user, "passhash": hashlib.md5(pw.encode()).hexdigest()}).encode()
    req = urllib.request.Request(
        f"{STIRLITZ_HOST}/sessions",
        data=body,
        headers={"Content-Type": "text/plain"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
        data = json.loads(resp.read().decode("utf-8", "replace") or "{}")
    sid = data.get("session")
    if not sid:
        raise RuntimeError("Stirlitz login returned no session")
    return sid


def _session_id(force: bool = False) -> str:
    with _lock:
        if force or not _session["id"]:
            _session["id"] = _login()
            _session["ts"] = time.time()
        return _session["id"]


def _get(path: str) -> tuple[bytes, str]:
    """GET a device path with the cached session; on a 4xx (expired/absent session)
    log in once more and retry. Returns (body, content-type)."""
    for attempt in (0, 1):
        sid = _session_id(force=attempt == 1)
        req = urllib.request.Request(f"{STIRLITZ_HOST}{path}", headers={"Cookie": f"session={sid}"})
        try:
            with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
                return resp.read(), resp.headers.get("Content-Type", "application/octet-stream")
        except urllib.error.HTTPError as exc:
            if attempt == 0 and 400 <= exc.code < 500:
                continue
            raise
    raise RuntimeError("unreachable")


def fetch_frame(screen: str = DEFAULT_SCREEN) -> tuple[bytes, str]:
    if not _SCREEN_RE.match(screen or ""):
        raise ValueError("bad screen name")
    body, ctype = _get(f"/live/screens/{screen}/preview")
    return body, ("image/jpeg" if "jp" in ctype.lower() else ctype.split(";")[0])


def fetch_screens() -> list[str]:
    body, _ = _get("/live/screens")
    return sorted(json.loads(body.decode("utf-8", "replace") or "{}").keys())


def build_monitor_wall_router(templates: Jinja2Templates) -> APIRouter:
    router = APIRouter()

    @router.get("/master-control/monitor-wall", response_class=HTMLResponse)
    async def monitor_wall_page(request: Request):
        return templates.TemplateResponse(
            request,
            "master_control/monitor_wall.html",
            {"stirlitz_url": f"{STIRLITZ_HOST}/files/index.html", "screen": DEFAULT_SCREEN},
        )

    @router.get("/api/master-control/monitor-wall/frame")
    async def monitor_wall_frame(screen: str = Query(DEFAULT_SCREEN)):
        try:
            body, ctype = await asyncio.to_thread(fetch_frame, screen)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        except Exception as exc:  # noqa: BLE001 - surface device errors to the page
            return JSONResponse({"error": f"multiviewer: {exc}"}, status_code=502)
        return Response(body, media_type=ctype, headers={"Cache-Control": "no-store"})

    @router.get("/api/master-control/monitor-wall/screens")
    async def monitor_wall_screens():
        try:
            return {"screens": await asyncio.to_thread(fetch_screens)}
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"error": f"multiviewer: {exc}", "screens": []}, status_code=502)

    return router
