"""San Joaquin County proposal parser — real workbook fixture + tampering.

The fixture is the actual proposal; it carries its own totals so every guard
is exercised against real arithmetic. Negative tests mutate a copy of the
workbook (openpyxl) and assert the parser REFUSES — a no-op mutation must
still parse, or the negatives pass for the wrong reason.
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pytest

for p in (str(Path(__file__).resolve().parents[2]),):
    if p not in sys.path:
        sys.path.insert(0, p)

from browser_automation.parsers.sjcounty_parser import (
    SJCountyOrder,
    base_language,
    parse_sjcounty,
)

FIXTURE = (
    Path(__file__).parent.parent
    / "fixtures"
    / "sjcounty"
    / "sjcounty_voter_registration_general_2026.xlsm"
)


@pytest.fixture(scope="module")
def order() -> SJCountyOrder:
    return parse_sjcounty(str(FIXTURE))


# ─── Positive ─────────────────────────────────────────────────────────────────


def test_header(order):
    assert order.client.startswith("San Joaquin County Voter")
    assert order.contact == "Stephanie Yoder"
    assert order.flight_start == "09/14/2026"
    assert order.flight_end == "11/03/2026"
    assert order.market_code == "CVC"
    assert order.rates_are_net is False


def test_grid_shape(order):
    assert len(order.paid_lines) == 5
    assert len(order.bonus_lines) == 5
    assert [d.isoformat() for d in order.week_dates][::7] == ["2026-09-14", "2026-11-02"]
    assert len(order.week_dates) == 8
    chinese = order.paid_lines[0]
    assert chinese.insertion == "Chinese News (Mandarin & Cantonese)"
    assert chinese.daypart == "M-Sun 7p-9p/ M-F 11:30p-12a"
    assert chinese.rate == 50.0 and chinese.length_sec == 30
    assert chinese.week_spots == [5, 5, 5, 5, 5, 5, 5, 2] and chinese.units == 37
    hmong = order.paid_lines[3]
    assert hmong.week_spots[-1] == 0  # no Sat-Sun in the Mon–Tue stub week


def test_bonus_rows_are_ros_with_zero_rate(order):
    for ln in order.bonus_lines:
        assert ln.is_bonus and ln.rate == 0.0 and ln.value > 0
        assert ln.daypart == "ROS Bonus"
    assert [ln.base_language for ln in order.bonus_lines] == [
        "Chinese",
        "Filipino",
        "Vietnamese",
        "Hmong",
        "Punjabi",
    ]


def test_totals_reconcile(order):
    assert sum(ln.total_spots for ln in order.paid_lines) == 141
    assert sum(ln.total_spots for ln in order.bonus_lines) == 113
    assert order.paid_total == 6010.00
    assert order.production_total == 2650.00
    assert order.total_cost == 8660.00
    assert order.paid_units_stated == 141 and order.bonus_units_stated == 113
    assert order.summary_airtime == 6010 and order.summary_total == 8660


def test_production_is_a_charge_not_a_line(order):
    assert len(order.charges) == 1
    ch = order.charges[0]
    assert ch.amount == 2650.00
    assert "voiceover" in ch.description.lower()
    assert all("voiceover" not in ln.insertion.lower() for ln in order.lines)


def test_base_language():
    assert base_language("South Asian News (Punjabi)") == "South Asian"
    assert base_language("Chinese News (Mandarin &  Cantonese)") == "Chinese"
    assert base_language("Punjabi") == "Punjabi"
    assert base_language("Cantonese Drama") == "Cantonese"


def test_bridge_aliases(order):
    ln = order.paid_lines[1]
    assert ln.weekly_spots == ln.week_spots and ln.length == 30 and ln.duration == "30"
    assert ln.days == "M-F" and ln.time == "4p-5p; 6p-7p"
    assert order.advertiser == order.client and order.market == "CVC"


# ─── Tampering ────────────────────────────────────────────────────────────────
# The grid's Units/TOTAL cells are formulas; openpyxl drops cached results on
# save, so tamper the loaded row grid through the parser's `_load_rows` seam.


def _tampered(monkeypatch, mutate) -> str:
    import browser_automation.parsers.sjcounty_parser as mod

    real = mod._load_rows

    def fake(path):
        rows = real(path)
        mutate(rows)
        return rows

    monkeypatch.setattr(mod, "_load_rows", fake)
    return str(FIXTURE)


def _find(rows, text: str) -> tuple[int, int]:
    for ri, row in enumerate(rows):
        for ci, v in enumerate(row):
            if isinstance(v, str) and " ".join(v.split()).lower().startswith(text.lower()):
                return ri, ci
    raise AssertionError(f"cell starting {text!r} not found")


def test_noop_mutation_still_parses(monkeypatch):
    def m(rows):
        rows[-1][0] = "note"

    o = parse_sjcounty(_tampered(monkeypatch, m))
    assert o.total_cost == 8660.00


def test_blank_rate_refuses(monkeypatch):
    def m(rows):
        r, c = _find(rows, "Filipino New/Talk")
        rows[r][c + 2] = None  # Value

    with pytest.raises(ValueError, match="Value"):
        parse_sjcounty(_tampered(monkeypatch, m))


def test_dropped_week_cell_refuses(monkeypatch):
    def m(rows):
        r, c = _find(rows, "Vietnamese News")
        rows[r][c + 4] = None  # a week cell → sum != Units

    with pytest.raises(ValueError, match="Units"):
        parse_sjcounty(_tampered(monkeypatch, m))


def test_slid_week_cell_refuses(monkeypatch):
    def m(rows):
        r, c = _find(rows, "Vietnamese News")
        rows[r][c + 3], rows[r][c + 4] = (
            rows[r][c + 4] + 1,
            rows[r][c + 3] - 1,
        )  # same sum, footer breaks

    with pytest.raises(ValueError, match="Total Paid"):
        parse_sjcounty(_tampered(monkeypatch, m))


def test_wrong_line_total_refuses(monkeypatch):
    def m(rows):
        r, c = _find(rows, "South Asian News")
        rows[r][c + 12] = 900  # TOTAL should be 920

    with pytest.raises(ValueError, match="TOTAL"):
        parse_sjcounty(_tampered(monkeypatch, m))


def test_wrong_footer_refuses(monkeypatch):
    def m(rows):
        r, c = _find(rows, "Total Paid")
        rows[r][c + 9] = 140  # Units

    with pytest.raises(ValueError, match="Total Paid"):
        parse_sjcounty(_tampered(monkeypatch, m))


def test_renamed_column_refuses(monkeypatch):
    def m(rows):
        r, c = _find(rows, "Units")
        rows[r][c] = "Qty"

    with pytest.raises(ValueError, match="header"):
        parse_sjcounty(_tampered(monkeypatch, m))


def test_added_column_is_a_noop(monkeypatch):
    def m(rows):
        for row in rows:
            row.insert(4, None)  # a blank column between Value and the weeks
        r, c = _find(rows, "Insertion")
        rows[r][4] = "Length"

    o = parse_sjcounty(_tampered(monkeypatch, m))
    assert o.total_cost == 8660.00 and len(o.week_dates) == 8


def test_unclassified_production_money_refuses(monkeypatch):
    def m(rows):
        r, c = _find(rows, "Voiceover translation fees")
        rows[r][c] = "Mystery fee: $2,650"

    with pytest.raises(ValueError, match="classify"):
        parse_sjcounty(_tampered(monkeypatch, m))


def test_blanked_production_refuses(monkeypatch):
    def m(rows):
        r, c = _find(rows, "Voiceover translation fees")
        rows[r][c] = None  # charge gone, summary still says $2,650

    with pytest.raises(ValueError, match="production"):
        parse_sjcounty(_tampered(monkeypatch, m))


def test_summary_total_mismatch_refuses(monkeypatch):
    def m(rows):
        r, c = _find(rows, "Total Airtime")
        rows[r][c + 1] = 6000

    with pytest.raises(ValueError, match="Airtime"):
        parse_sjcounty(_tampered(monkeypatch, m))


def test_dropped_line_refuses(monkeypatch):
    def m(rows):
        r, _c = _find(rows, "Hmong Variety")
        del rows[r]

    with pytest.raises(ValueError):
        parse_sjcounty(_tampered(monkeypatch, m))


# ─── Automation planner ───────────────────────────────────────────────────────


def test_line_plan_on_time_start(order):
    from browser_automation.sjcounty_automation import _line_plan

    plan = _line_plan(order, date(2026, 9, 14), order.flight_end)
    assert len(plan) == 10
    ln, days, time_range, desc, ranges, notes = plan[0]
    assert days == "M-Sun" and time_range == "19:00-23:59"  # dual daypart → union
    assert [(r["spots_per_week"], r["weeks"]) for r in ranges] == [(5, 7), (2, 1)]
    assert ranges[1]["date_from"] == date(2026, 11, 2) and ranges[1]["date_to"] == date(2026, 11, 3)
    assert not notes
    hmong = plan[3]
    assert [(r["spots_per_week"], r["weeks"]) for r in hmong[4]] == [
        (4, 7)
    ]  # zero stub week dropped
    entered = sum(r["spots_per_week"] * r["weeks"] for p in plan for r in p[4])
    assert entered == 254 == order.total_spots


def test_line_plan_bonus_uses_ros_window(order):
    from browser_automation.sjcounty_automation import _line_plan

    plan = _line_plan(order, date(2026, 9, 14), order.flight_end)
    bonus = [p for p in plan if p[0].is_bonus]
    assert bonus[0][3] == "BNS Chinese ROS" and bonus[0][1] == "M-Su"
    assert bonus[3][1] == "Sa-Su"  # Hmong ROS is weekend-only


def test_line_plan_late_start_keeps_caps_valid(order):
    from browser_automation.etere_direct_client import parse_day_bits
    from browser_automation.sjcounty_automation import _line_plan

    plan = _line_plan(order, date(2026, 9, 16), order.flight_end)
    for ln, days, _t, _d, ranges, notes in plan:
        bits = parse_day_bits(days)
        for r in ranges:
            avail = sum(
                1
                for i in range((r["date_to"] - r["date_from"]).days + 1)
                if list(bits.values())[(r["date_from"].weekday() + i) % 7]
            )
            assert r["max_daily"] * avail >= r["spots_per_week"], (ln.insertion, r)
