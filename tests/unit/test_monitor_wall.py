"""Monitor Wall proxy: login, session reuse, one re-login on a 4xx, screen-name guard."""

import io
import json
import sys
import urllib.error
from pathlib import Path

import pytest

_root = Path(__file__).resolve().parents[2]
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from web.routes import monitor_wall as mw  # noqa: E402


class _Resp(io.BytesIO):
    def __init__(self, body: bytes, ctype: str):
        super().__init__(body)
        self.headers = {"Content-Type": ctype}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _fake_device(monkeypatch, valid_sessions):
    """urlopen stand-in: POST /sessions mints s1, s2, …; GETs need a valid session cookie."""
    calls = {"login": 0, "get": []}

    def urlopen(req, timeout=None):
        url = req.full_url
        if url.endswith("/sessions") and req.get_method() == "POST":
            calls["login"] += 1
            body = json.loads(req.data.decode())
            assert set(body) == {"user", "passhash"} and len(body["passhash"]) == 32
            return _Resp(json.dumps({"session": f"s{calls['login']}"}).encode(), "application/json")
        sid = req.get_header("Cookie", "").replace("session=", "")
        calls["get"].append((url.split(mw.STIRLITZ_HOST)[1], sid))
        if sid not in valid_sessions:
            raise urllib.error.HTTPError(url, 400, "Bad Request", {}, io.BytesIO(b""))
        if url.endswith("/live/screens"):
            return _Resp(b'{"0.0":{}, "stream-REALTIME":{}}', "application/json; charset=utf-8")
        return _Resp(b"\xff\xd8JPEG", "image/jpg; charset=utf-8")

    monkeypatch.setattr(mw.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(mw, "_credentials", lambda: ("u", "p"))
    monkeypatch.setattr(mw, "_session", {"id": None, "ts": 0.0})
    return calls


def test_first_frame_logs_in_once_and_reuses_session(monkeypatch):
    calls = _fake_device(monkeypatch, {"s1"})
    body, ctype = mw.fetch_frame("0.0")
    assert body.startswith(b"\xff\xd8") and ctype == "image/jpeg"
    mw.fetch_frame("0.0")
    assert calls["login"] == 1
    assert calls["get"] == [("/live/screens/0.0/preview", "s1")] * 2


def test_expired_session_relogs_once_and_retries(monkeypatch):
    calls = _fake_device(monkeypatch, {"s2"})  # s1 (first login) is rejected → re-login → s2
    body, _ = mw.fetch_frame("0.0")
    assert body.startswith(b"\xff\xd8")
    assert calls["login"] == 2
    assert [sid for _, sid in calls["get"]] == ["s1", "s2"]


def test_persistent_4xx_raises_after_one_retry(monkeypatch):
    calls = _fake_device(monkeypatch, set())
    with pytest.raises(urllib.error.HTTPError):
        mw.fetch_frame("0.0")
    assert calls["login"] == 2 and len(calls["get"]) == 2


def test_screen_name_guard(monkeypatch):
    _fake_device(monkeypatch, {"s1"})
    for bad in ("", "../x", "0.0/preview", "a b"):
        with pytest.raises(ValueError):
            mw.fetch_frame(bad)


def test_screens_list(monkeypatch):
    _fake_device(monkeypatch, {"s1"})
    assert mw.fetch_screens() == ["0.0", "stream-REALTIME"]


# --- Phoenix TV panel: audio meter pass-through and the 24 h level history ---------------

_VIDEO02 = _root / "video02_recorder" / "thumb.ps1"
_PAGE = _root / "src" / "web" / "templates" / "master_control" / "monitor_wall.html"


def _phoenix_fake(monkeypatch, payloads):
    """urlopen stand-in for VIDEO-02's thumb server: serves the named JSON documents."""

    def urlopen(url, timeout=None):
        name = url.rsplit("/", 1)[1]
        if name not in payloads:
            raise urllib.error.URLError("connection refused")
        return _Resp(json.dumps(payloads[name]).encode(), "application/json")

    monkeypatch.setattr(mw.urllib.request, "urlopen", urlopen)


def _endpoint(path):
    import asyncio

    router = mw.build_monitor_wall_router(templates=None)
    (route,) = [r for r in router.routes if getattr(r, "path", "") == path]
    return lambda: asyncio.run(route.endpoint())


def test_phoenix_status_passes_the_audio_reading_through(monkeypatch):
    audio = {
        "track": "aac",
        "window_s": 3,
        "L": {"rms": -26.6, "peak": -4.0},
        "R": {"rms": -26.6, "peak": -4.0},
        "silent": False,
    }
    _phoenix_fake(
        monkeypatch,
        {"live.json": {"captured": "2026-10-18T21:37:12-07:00", "ok": True, "audio": audio}},
    )
    meta = _endpoint("/api/master-control/phoenix/status")()
    assert meta["reachable"] is True
    assert meta["audio"] == audio


def test_phoenix_levels_returns_the_history_or_an_empty_list_on_502(monkeypatch):
    rows = [{"t": "2026-10-18T21:36:12-07:00", "l": -26.6, "lp": -4.0, "r": -26.5, "rp": -4.0}]
    _phoenix_fake(monkeypatch, {"levels.json": rows})
    assert _endpoint("/api/master-control/phoenix/levels")() == {"levels": rows}
    _phoenix_fake(monkeypatch, {})
    resp = _endpoint("/api/master-control/phoenix/levels")()
    assert resp.status_code == 502
    assert json.loads(resp.body)["levels"] == []


def test_thumb_measures_the_aac_track_never_the_silent_ac3():
    """The stream's AC-3 track is digital silence (10/17): a meter on it would cry NO AUDIO
    while the recording is fine. The grab maps 0:a:0 (optional, so a stream with no audio
    still yields a picture) and never 0:a:1."""
    ps1 = _VIDEO02.read_text()
    assert '"-map", "0:a:0?"' in ps1
    assert (
        "0:a:1" not in ps1.split("$Root   =")[1]
    )  # the header comment may name it; the code never does
    assert "measure_perchannel=RMS_level+Peak_level" in ps1
    assert "/levels.json" in ps1
    page = _PAGE.read_text()
    assert "/api/master-control/phoenix/levels" in page
    assert "renderAudio(d.audio)" in page
