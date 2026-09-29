"""S3 Cleanup page (/traffic/s3-cleanup): categories with one action each, apply acts only
on ids still in the requested category, progress streams line by line."""

import datetime as dt
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.templating import Jinja2Templates
from fastapi.testclient import TestClient

_root = Path(__file__).resolve().parents[2]
for p in [str(_root), str(_root / "src")]:
    if p not in sys.path:
        sys.path.insert(0, p)

from business_logic.services import s3_media_purge as smp  # noqa: E402
from web.routes import s3_cleanup  # noqa: E402


def _row(i, **kw):
    base = {
        "id_metafile": i,
        "id_filmati": 100 + i,
        "cod_progra": f"DTV-SHOW-01{i:02d}A",
        "descrizio": "x",
        "newtype": "PGM",
        "file_id": f"DTV-SHOW-01{i:02d}A",
        "file_name": f"DTV-SHOW-01{i:02d}A.mp4",
        "size": 10,
        "data_scad": dt.datetime(2024, 1, 1),
        "last_aired": None,
        "future_rows": 0,
    }
    base.update(kw)
    return base


ROWS = [
    _row(1),
    _row(2),
    _row(3, file_name="GONE-0101A.mp4", cod_progra="GONE-0101A"),
    _row(4, future_rows=1),
]
SIZES = {"DTV-SHOW-0101A.mp4": 10, "DTV-SHOW-0102A.mp4": 10, "DTV-SHOW-0104A.mp4": 10}


class Conn:
    def close(self):
        pass

    def cursor(self):
        class C:
            def execute(self, sql):
                pass

            def fetchone(self):
                return (5, 123)

        return C()


@pytest.fixture
def client(monkeypatch):
    calls = {}
    monkeypatch.setattr(s3_cleanup, "_connect", lambda: Conn())
    monkeypatch.setattr(s3_cleanup, "_s3_client", lambda: (object(), "bucket"))
    monkeypatch.setattr(smp, "fetch_expired", lambda conn, today=None: [dict(r) for r in ROWS])
    monkeypatch.setattr(smp, "probe_sizes", lambda s3, b, keys, workers=32: dict(SIZES))

    def fake_purge(conn, s3, bucket, rows, *, apply, restore_dir, log, **kw):
        calls["purge"] = [r["id_metafile"] for r in rows]
        log("batch 1: 2 object(s) deleted, 2 record(s) removed")
        return {
            "deleted_s3": len(rows),
            "deleted_db": len(rows),
            "bytes": 20,
            "s3_errors": [],
            "restore": "r.sql",
        }

    def fake_remove(conn, rows, *, apply, restore_dir, log):
        calls["remove"] = [r["id_metafile"] for r in rows]
        return {"deleted_db": len(rows), "restore": "d.sql"}

    monkeypatch.setattr(smp, "purge", fake_purge)
    monkeypatch.setattr(smp, "remove_references", fake_remove)
    monkeypatch.setattr(
        smp,
        "verify",
        lambda conn, s3, b, rows: {"ok": True, "fs_rows_left": {}, "objects_left": []},
    )
    app = FastAPI()
    app.include_router(
        s3_cleanup.build_s3_cleanup_router(
            Jinja2Templates(directory=str(_root / "src/web/templates"))
        )
    )
    c = TestClient(app)
    c.calls = calls
    return c


def test_page_and_scan_categories(client):
    assert client.get("/traffic/s3-cleanup").status_code == 200
    data = client.get("/api/traffic/s3-cleanup/scan").json()
    cats = {c["key"]: c for c in data["categories"]}
    assert [c["key"] for c in data["categories"]] == [
        "delete",
        "dangling",
        "size_mismatch",
        "booked",
    ]
    assert cats["delete"]["count"] == 2 and cats["delete"]["actionable"]
    assert cats["dangling"]["count"] == 1 and cats["dangling"]["actionable"]
    assert cats["booked"]["count"] == 1 and not cats["booked"]["actionable"]
    assert cats["delete"]["groups"][0]["family"] == "DTV-SHOW" and cats["delete"]["groups"][0][
        "ids"
    ] == [1, 2]
    assert data["short_form"] == {"count": 5, "bytes": 123}
    assert data["total"] == 4


def test_apply_acts_only_on_ids_still_in_the_category(client):
    # ids 1,2 are deletable; 3 is dangling; 4 is booked — the client sends all four
    r = client.post(
        "/api/traffic/s3-cleanup/apply", json={"category": "delete", "ids": [1, 2, 3, 4]}
    )
    assert r.status_code == 200
    lines = r.text.strip().splitlines()
    assert client.calls["purge"] == [1, 2]
    assert "2 of 4 selected file(s) still in category 'delete'; 2 skipped" in lines[0]
    assert lines[-1] == "[EXIT:0]" and any("[DONE]" in ln for ln in lines)

    r = client.post("/api/traffic/s3-cleanup/apply", json={"category": "dangling", "ids": [3, 1]})
    assert client.calls["remove"] == [3]
    assert r.text.strip().splitlines()[-1] == "[EXIT:0]"


def test_apply_refuses_bad_input(client):
    assert (
        client.post(
            "/api/traffic/s3-cleanup/apply", json={"category": "booked", "ids": [4]}
        ).status_code
        == 400
    )
    assert (
        client.post(
            "/api/traffic/s3-cleanup/apply", json={"category": "delete", "ids": []}
        ).status_code
        == 400
    )
    assert (
        client.post(
            "/api/traffic/s3-cleanup/apply", json={"category": "delete", "ids": ["x"]}
        ).status_code
        == 400
    )


def test_show_family_and_categorize():
    assert smp.show_family("DTV-YOUTHBOOKS01-0123A") == "DTV-YOUTHBOOKS"
    assert smp.show_family("DTV-YOUTHBOOKS02-0130C") == "DTV-YOUTHBOOKS", "episodes group together"
    assert smp.show_family("NEWSTODAY073126") == "NEWSTODAY"
    assert smp.show_family("V-NewsToday090323A") == "V-NewsToday"
    assert smp.show_family("Big010223C.mp4") == "Big"
    assert smp.show_family("FCI_010223_LAX") == "FCI", "market suffix is not a piece letter"
    assert smp.show_family("PTNEWS1027A") == "PTNEWS"
    assert smp.show_family("K-FILLER25-021") == "K-FILLER25"
    cats = smp.categorize(
        [dict(r) for r in ROWS] + [_row(5, size=99)], SIZES | {"DTV-SHOW-0105A.mp4": 10}
    )
    assert {k: [r["id_metafile"] for r in v] for k, v in cats.items()} == {
        "delete": [1, 2],
        "dangling": [3],
        "size_mismatch": [5],
        "booked": [4],
    }
