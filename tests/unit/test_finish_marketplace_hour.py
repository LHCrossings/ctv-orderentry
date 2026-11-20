"""DAL Marketplace in Fill & Finish (Maija / Lee 11/20).

The hour is two 28:30 infomercials. No station ID at the half hour: the first break is 1:30 of
commercials / PIs, the second holds the rest, the PSAs and the hour's one ID. The grid reader
splits the hour in two for Daily Programming; Finish must see ONE show."""

import sys
from pathlib import Path

_root = Path(__file__).resolve().parents[2]
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from business_logic.services import finish_service as fs  # noqa: E402
from business_logic.services.finish_plan import FPS  # noqa: E402

Filler = fs.Filler
H12, H13 = 12 * 3600.0, 13 * 3600.0
LF = 1710.0  # 28:30


def _row(i, ora_s, dur_s, newtype, event_type="T", desc="", line=None):
    return (i, int(ora_s * FPS), int(dur_s * FPS), newtype, event_type, 1000 + i, desc, line, i * 10)


def _inv():
    return [
        Filler(9001, "PI-901-060: A", 60.0, "PI", "PI-901", 0),
        Filler(9002, "PI-902-060: B", 60.0, "PI", "PI-902", 1),
        Filler(9003, "PI-903-030: C", 30.0, "PI", "PI-903", 2),
        Filler(9004, "PI-904-030: D", 30.0, "PI", "PI-904", 3),
        Filler(9011, "PSA-911-015: E", 15.0, "PSA", "PSA-911", 0),
        Filler(9012, "PSA-912-010: F", 10.0, "PSA", "PSA-912", 1),
    ]


def _plan(rows, monkeypatch):
    monkeypatch.setattr(fs, "load_inventory", lambda cur, market, date: _inv())
    monkeypatch.setattr(fs, "campaign_psa_for", lambda *a, **k: ([], None))  # no DB in this test
    return fs.plan_window(None, 10, "2026-11-21", H12, H13, rows=rows)


def _hour(first, second):
    return [
        _row(1, H12, LF, "PGM", "F", first),
        _row(2, H12 + LF, LF, "PGM", "T", second),
        _row(9, H13, 1800, "PGM", "F", "NEXT"),
    ]


def _breaks(r):
    out = {}
    for t in r["timeline"]:
        if "break" in t:
            out.setdefault(t["break"], []).append(t)
    return out


def test_marketplace_hour_gets_one_id_and_a_ninety_second_first_break(monkeypatch):
    r = _plan(_hour("WLPI-LF-0002: Ruvio", "PI-LF-0011: Ellipse Deluxe"), monkeypatch)
    assert r["ok"] and r["state"] == "ready", r.get("notes")
    b = _breaks(r)
    first, second = b[0], b[1]
    assert [t["type"] for t in first] == ["PI", "PI"], "commercials / PIs only between the two"
    assert abs(sum(t["dur"] for t in first) - 90.0) < 1.0
    assert sum(1 for t in r["timeline"] if t["type"] == "ID") == 1
    assert second[-1]["type"] == "ID", "the one ID closes the hour"
    assert {t["type"] for t in second} == {"PI", "PSA", "ID"}
    second_lf = next(t for t in r["timeline"] if t["desc"].startswith("PI-LF-0011"))
    assert abs(second_lf["ora"] - (H12 + 1800)) < 2.0, "second infomercial starts at the half hour"


def test_a_normal_two_piece_show_keeps_the_even_fill_rule(monkeypatch):
    r = _plan(_hour("KD-SHOW-1121A", "KD-SHOW-1121B"), monkeypatch)
    assert r["ok"], r.get("notes")
    first = _breaks(r)[0]
    assert abs(sum(t["dur"] for t in first) - 120.0) < 1.0  # unchanged: no 1:30 cap


def test_finish_lists_the_marketplace_hour_as_one_show(monkeypatch):
    from src.business_logic.services import programming_grid as pg

    grid = {
        "found": True,
        "programs": [
            {"start": "11:00", "end": "12:00", "title": "Phoenix News", "language": "Mandarin"},
            {"start": "12:00", "end": "12:30", "title": "Marketplace", "language": ""},
            {"start": "12:30", "end": "13:00", "title": "Marketplace", "language": ""},
            {"start": "13:00", "end": "13:30", "title": "Drama", "language": "Mandarin"},
            {"start": "23:00", "end": "23:30", "title": "Marketplace", "language": ""},
            {"start": "23:30", "end": "00:00", "title": "Marketplace", "language": ""},
        ],
    }
    monkeypatch.setattr(pg, "get_day_programs", lambda network, d: grid)
    w = fs.day_windows(10, "2026-11-21", [])
    assert [(x["lo"] / 3600, x["hi"] / 3600, x["title"]) for x in w] == [
        (11, 12, "Phoenix News"),
        (12, 13, "Marketplace"),
        (13, 13.5, "Drama"),
        (23, 24, "Marketplace"),
    ]
