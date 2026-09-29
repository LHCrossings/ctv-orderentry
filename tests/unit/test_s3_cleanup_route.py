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
    calls["flags"] = []

    def fake_expired(conn, today=None, include_short_form=False):
        calls["flags"].append(("expired", include_short_form))
        return [dict(r) for r in ROWS]

    monkeypatch.setattr(smp, "fetch_expired", fake_expired)
    monkeypatch.setattr(
        smp,
        "object_sizes",
        lambda s3, b, keys, workers=32: dict(SIZES) | {"AVS010325A.mp4": 10, "AVS010425A.mp4": 10},
    )
    unexpired = [
        _row(
            7,
            id_metafile=None,
            id_filmati=107,
            file_name=None,
            size=0,
            data_scad=None,
            cod_progra="STUB-0101A",
            other_copies=0,
        ),
        _row(
            8,
            id_metafile=None,
            id_filmati=108,
            file_name=None,
            size=0,
            data_scad=None,
            cod_progra="STUB-0102A",
            other_copies=1,
            future_rows=1,
        ),
    ]
    monkeypatch.setattr(
        smp,
        "fetch_unexpired",
        lambda conn, today=None, include_short_form=False: [dict(r) for r in unexpired],
    )
    aged = [
        _row(
            21,
            id_filmati=121,
            cod_progra="AVS010325A",
            file_name="AVS010325A.mp4",
            data_scad=None,
            last_aired=dt.datetime(2025, 1, 3),
        ),
        _row(
            22,
            id_filmati=122,
            cod_progra="AVS010425A",
            file_name="AVS010425A.mp4",
            data_scad=None,
            last_aired=dt.datetime(2025, 1, 4),
            future_rows=1,
        ),
    ]
    calls["aged_ranges"] = []

    def fake_aged(conn, today, d_from, d_to, include_short_form=False):
        calls["aged_ranges"].append((d_from, d_to))
        calls["flags"].append(("aged", include_short_form))
        return [dict(r) for r in aged]

    monkeypatch.setattr(smp, "fetch_aged", fake_aged)

    def fake_purge(conn, s3, bucket, rows, *, apply, restore_dir, log, **kw):
        calls["purge"] = [r["id_metafile"] for r in rows]
        log("batch 1: 2 object(s) deleted, 2 record(s) removed")
        return {
            "deleted_s3": len(rows),
            "deleted_db": len(rows),
            "expired_stamped": 0,
            "bytes": 20,
            "s3_errors": [],
            "restore": "r.sql",
        }

    def fake_remove(conn, rows, *, apply, restore_dir, log, **kw):
        calls["remove"] = [r["id_metafile"] for r in rows]
        return {"deleted_db": len(rows), "expired_stamped": 0, "restore": "d.sql"}

    def fake_mark(conn, rows, *, apply, restore_dir, log, **kw):
        calls["mark"] = [r["id_filmati"] for r in rows]
        return {"expired_stamped": len(rows), "restore": "e.sql"}

    monkeypatch.setattr(smp, "purge", fake_purge)
    monkeypatch.setattr(smp, "remove_references", fake_remove)
    monkeypatch.setattr(smp, "mark_expired", fake_mark)
    monkeypatch.setattr(
        smp,
        "verify",
        lambda conn, s3, b, rows, **kw: {
            "ok": True,
            "fs_rows_left": {},
            "objects_left": [],
            "not_expired": 0,
        },
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
        "aged",
        "dangling",
        "unexpired",
        "size_mismatch",
        "booked",
    ]
    assert cats["delete"]["count"] == 2 and cats["delete"]["actionable"]
    assert cats["dangling"]["count"] == 1 and cats["dangling"]["actionable"]
    assert cats["unexpired"]["count"] == 1 and cats["unexpired"]["actionable"]
    assert cats["unexpired"]["groups"][0]["ids"] == [107]
    assert cats["unexpired"]["groups"][0]["expired_from"] is None
    assert cats["booked"]["count"] == 2 and not cats["booked"]["actionable"], (
        "booked-ahead from both sweeps"
    )
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
    assert "2 of 4 selected item(s) still in category 'delete'; 2 skipped" in lines[0]
    assert lines[-1] == "[EXIT:0]" and any("[DONE]" in ln for ln in lines)

    r = client.post("/api/traffic/s3-cleanup/apply", json={"category": "dangling", "ids": [3, 1]})
    assert client.calls["remove"] == [3]
    assert r.text.strip().splitlines()[-1] == "[EXIT:0]"

    r = client.post(
        "/api/traffic/s3-cleanup/apply", json={"category": "unexpired", "ids": [107, 108]}
    )
    assert client.calls["mark"] == [107], "the booked-ahead asset is not stamped"
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
        "unexpired": [],
        "aged": [],
    }


def test_scan_without_a_to_date_skips_old_programming(client):
    data = client.get("/api/traffic/s3-cleanup/scan").json()
    cats = {c["key"]: c for c in data["categories"]}
    assert cats["aged"]["count"] == 0 and data["aged_range"] == [None, None]
    assert client.calls["aged_ranges"] == []


def test_scan_with_range_adds_old_programming_and_apply_stamps_it(client):
    data = client.get("/api/traffic/s3-cleanup/scan?aged_from=2025-01-01&aged_to=2025-12-31").json()
    cats = {c["key"]: c for c in data["categories"]}
    assert client.calls["aged_ranges"] == [(dt.date(2025, 1, 1), dt.date(2025, 12, 31))]
    assert data["aged_range"] == ["2025-01-01", "2025-12-31"]
    g = cats["aged"]["groups"]
    assert cats["aged"]["count"] == 1 and g[0]["family"] == "AVS" and g[0]["ids"] == [21]
    assert g[0]["aired_from"] == "2025-01-03" and g[0]["expired_from"] is None
    assert cats["booked"]["count"] == 3, "the booked-ahead AVS joins the report bucket"
    assert any(
        "not expired" in f["problem"] for grp in cats["booked"]["groups"] for f in grp["files"]
    )
    assert cats["delete"]["count"] == 2 and cats["dangling"]["count"] == 1, (
        "the expired sweep is unchanged"
    )

    r = client.post(
        "/api/traffic/s3-cleanup/apply",
        json={"category": "aged", "ids": [21, 22], "aged_to": "2025-12-31"},
    )
    assert r.status_code == 200 and client.calls["purge"] == [21]
    assert (
        client.post(
            "/api/traffic/s3-cleanup/apply", json={"category": "aged", "ids": [21]}
        ).status_code
        == 400
    )
    assert (
        client.get(
            "/api/traffic/s3-cleanup/scan?aged_from=2025-12-31&aged_to=2025-01-01"
        ).status_code
        == 400
    )


def test_aged_sql_and_categorize():
    sql = smp.aged_candidates_sql(dt.date(2026, 9, 29), dt.date(2025, 1, 1), dt.date(2025, 6, 30))
    assert "(f.DATA_SCAD IS NULL OR f.DATA_SCAD >= '2026-09-30')" in sql, "not expired only"
    assert "COALESCE(air.last_aired, f.CREATIONDATE) < '2025-07-01'" in sql
    assert "COALESCE(air.last_aired, f.CREATIONDATE) >= '2025-01-01'" in sql
    open_sql = smp.aged_candidates_sql(dt.date(2026, 9, 29), None, dt.date(2025, 6, 30))
    assert "COALESCE(air.last_aired, f.CREATIONDATE) >=" not in open_sql
    assert "f.COD_PROGRA NOT LIKE '%HIATUS%'" in sql and "m.ID_METADEVICE = 6" in sql
    rows = [_row(1), _row(2, file_name="X.mp4"), _row(3, future_rows=2)]
    cats = smp.categorize_aged(rows, {"DTV-SHOW-0101A.mp4": 10, "DTV-SHOW-0103A.mp4": 10})
    assert [r["id_metafile"] for r in cats["aged"]] == [1], (
        "the clean rows are the action, never clobbered"
    )
    assert set(cats) == {"aged", "dangling", "size_mismatch", "booked"}
    assert cats["dangling"][0]["problem"].endswith("asset not expired")
    assert cats["booked"][0]["problem"].endswith("asset not expired")


def test_object_sizes_switches_to_a_listing_for_many_keys(monkeypatch):
    seen = {}
    monkeypatch.setattr(
        smp,
        "probe_sizes",
        lambda s3, b, keys, workers=32: seen.setdefault("head", list(keys)) and {},
    )
    monkeypatch.setattr(
        smp, "list_bucket", lambda s3, b: seen.setdefault("list", True) and {"K1": 1, "K2": 2}
    )
    assert smp.object_sizes(None, "b", ["K1", "K1", None]) == {} and seen == {"head": ["K1"]}
    seen.clear()
    monkeypatch.setattr(smp, "LISTING_THRESHOLD", 1)
    assert smp.object_sizes(None, "b", ["K1", "K2", "K9"]) == {"K1": 1, "K2": 2} and seen == {
        "list": True
    }


def test_short_form_switch_flows_through_scan_and_apply(client):
    data = client.get("/api/traffic/s3-cleanup/scan").json()
    assert data["include_short_form"] is False and client.calls["flags"] == [("expired", False)]
    client.calls["flags"].clear()
    data = client.get("/api/traffic/s3-cleanup/scan?short_form=1&aged_to=2025-12-31").json()
    assert data["include_short_form"] is True
    assert client.calls["flags"] == [("expired", True), ("aged", True)]
    client.calls["flags"].clear()
    r = client.post(
        "/api/traffic/s3-cleanup/apply", json={"category": "delete", "ids": [1], "short_form": True}
    )
    assert "short-form INCLUDED" in r.text and client.calls["flags"] == [("expired", True)]
    client.calls["flags"].clear()
    client.post("/api/traffic/s3-cleanup/apply", json={"category": "delete", "ids": [1]})
    assert client.calls["flags"] == [("expired", False)], "off unless the page said so"


def test_short_form_switch_drops_only_the_type_filter():
    on = smp.expired_candidates_sql(dt.date(2026, 9, 29), include_short_form=True)
    off = smp.expired_candidates_sql(dt.date(2026, 9, 29))
    assert "f.NEWTYPE IN ('PGM', 'PGMX')" in off and "f.NEWTYPE IN" not in on
    assert "1 = 1" in on
    for sql in (on, off):
        assert "f.COD_PROGRA NOT LIKE '%HIATUS%'" in sql, "HIATUS stays guarded either way"
    assert "f.NEWTYPE IN" not in smp.aged_candidates_sql(
        dt.date(2026, 9, 29), None, dt.date(2025, 12, 31), True
    )
    assert "f.NEWTYPE IN" not in smp.unexpired_candidates_sql(dt.date(2026, 9, 29), True)
    assert "f.NEWTYPE IN ('PGM', 'PGMX')" in smp.unexpired_candidates_sql(dt.date(2026, 9, 29))
