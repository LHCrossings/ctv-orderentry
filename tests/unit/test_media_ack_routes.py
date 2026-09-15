"""Media Check dismiss / undo endpoints and their effect on the header summary (2026-09-15)."""

import json

import pytest
from fastapi import FastAPI
from fastapi.templating import Jinja2Templates
from fastapi.testclient import TestClient

from business_logic.services.health_events import EventLog
from business_logic.services.media_acks import MediaAcks
from web.routes import broadcast_health as bh


def _finding(fid, code, size):
    return {
        "id_filmati": fid,
        "code": code,
        "kind": "truncated",
        "newtype": "COM",
        "durata": 211,
        "detail": "small",
        "copies": [{"device": "CIB3", "size": size, "codec": "MP4"}],
        "airings": [{"market": "SEA", "date": "2026-09-15", "time": "11:41", "status": "I"}],
        "siblings": [],
    }


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setattr(bh, "_acks", MediaAcks(tmp_path / "acks.json"))
    monkeypatch.setattr(bh, "_events", EventLog(tmp_path / "events.jsonl"))
    monkeypatch.setattr(bh, "_last_snapshot", {"data": None})
    monkeypatch.setattr(bh, "_ensure_media_task", lambda: None)  # no background scan in tests
    monkeypatch.setitem(
        bh._media,
        "data",
        {
            "state": "ok",
            "checked_at": "2026-09-15T03:00:00",
            "assets": 2,
            "findings": [
                _finding(149053, "MD07BBV418", 2252180),
                _finding(149052, "MD06BBM418", 2119898),
            ],
        },
    )
    app = FastAPI()
    app.include_router(
        bh.build_broadcast_health_router(Jinja2Templates(directory="src/web/templates"))
    )
    return TestClient(app), tmp_path


def test_dismiss_removes_from_header_but_keeps_it_on_the_page(client):
    c, tmp = client
    assert bh._media_summary()["count"] == 2

    r = c.post("/api/broadcast-health/media/ack", json={"id": 149053, "note": "static BB, viewed"})
    assert r.status_code == 200
    page = r.json()
    assert page["dismissed"] == 1
    by_code = {f["code"]: f for f in page["findings"]}
    assert by_code["MD07BBV418"]["ack"]["note"] == "static BB, viewed"
    assert by_code["MD06BBM418"]["ack"] is None
    assert [f["code"] for f in page["findings"]] == ["MD06BBM418", "MD07BBV418"]  # active first

    s = bh._media_summary()
    assert s["count"] == 1 and s["dismissed"] == 1
    assert [f["code"] for f in s["findings"]] == ["MD06BBM418"]

    kinds = [json.loads(line)["kind"] for line in (tmp / "events.jsonl").read_text().splitlines()]
    assert "media_ack" in kinds
    assert c.get("/api/broadcast-health/media").json()["dismissed"] == 1


def test_undo_and_error_paths(client):
    c, tmp = client
    assert c.delete("/api/broadcast-health/media/ack/149053").status_code == 404  # nothing to undo
    assert (
        c.post("/api/broadcast-health/media/ack", json={"id": 1}).status_code == 404
    )  # not a finding
    assert c.post("/api/broadcast-health/media/ack", json={"note": "x"}).status_code == 400

    c.post("/api/broadcast-health/media/ack", json={"id": 149053})
    r = c.delete("/api/broadcast-health/media/ack/149053")
    assert r.status_code == 200 and r.json()["dismissed"] == 0
    assert bh._media_summary()["count"] == 2
    kinds = [json.loads(line)["kind"] for line in (tmp / "events.jsonl").read_text().splitlines()]
    # undo is logged, then the differ sees the finding back on the dot and logs it flagged again
    assert kinds[-2:] == ["media_unack", "media"]
    assert c.get("/master-control/media-check").status_code == 200
