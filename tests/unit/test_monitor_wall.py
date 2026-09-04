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
