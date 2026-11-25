"""Crossings TV Media Plan (Marquice Wong's house template), any advertiser.

Fixture: the real Soroudi OPTION 1 sheet (Lee 2026-11-25): Punjabi M-F 2p-4p,
45 x $80 = $3,600, 45 bonus ROS, 12/1/2026-1/1/2027, Los Angeles, and a $600
Translation Services fee. The sheet's own Subtotal row is the oracle.
"""

import copy
import sys
from datetime import date
from pathlib import Path

import pytest

_root = Path(__file__).resolve().parents[2]
for _p in (_root, _root / "src"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from browser_automation import mediaplan_automation as auto  # noqa: E402
from browser_automation.parsers import mediaplan_parser as mp  # noqa: E402

FIXTURE = _root / "tests" / "fixtures" / "mediaplan" / "soroudi_2612_option1.pdf"


@pytest.fixture(scope="module")
def sheet(real_pdfplumber):
    with real_pdfplumber.open(str(FIXTURE)) as pdf:
        page = pdf.pages[0]
        return page.extract_text(), page.extract_tables()


def _order(sheet, mutate=None, text=None):
    tables = copy.deepcopy(sheet[1])
    if mutate:
        grid = next(t for t in tables if any("Language Block" in str(c) for r in t for c in r if c))
        mutate(grid)
    return mp.build_order(text if text is not None else sheet[0], tables)


def test_soroudi_sheet(sheet):
    o = _order(sheet)
    assert (o.advertiser, o.acct_rep, o.market, o.option) == (
        "Soroudi",
        "Marquice Wong",
        "LAX",
        "OPTION 1",
    )
    assert o.order_date == date(2026, 11, 20)
    assert o.client_email == "mahsaa@soroudivision.com" and o.client_phone == "310.751.2733"
    paid, bonus = o.lines
    assert (paid.language, paid.days, paid.time_str) == ("Punjabi", "M-F", "2p-4p")
    assert (paid.total_spots, paid.rate, paid.is_bonus) == (45, 80.0, False)
    assert (paid.start_date, paid.end_date) == (date(2026, 12, 1), date(2027, 1, 1))
    assert paid.duration_seconds == 30
    # ROS BONUS takes the language's house ROS window.
    assert (bonus.days, bonus.time_str, bonus.total_spots, bonus.rate) == ("M-F", "2p-4p", 45, 0.0)
    assert bonus.is_bonus
    assert o.airtime_total == 3600.0
    assert o.production == 600.0 and o.production_note == "Translation: Punjabi"
    assert o.rates_are_net is False


def test_template_is_detected_and_sierra_keeps_its_own_type(sheet):
    from business_logic.services.order_detection_service import OrderDetectionService
    from domain.enums import OrderType

    svc = OrderDetectionService()
    assert mp.is_mediaplan_text(sheet[0])
    assert svc.detect_from_text(sheet[0]) == OrderType.MEDIAPLAN
    sierra = sheet[0].replace("Adertiser: Soroudi", "Adertiser: Sierra Donor Services")
    assert svc.detect_from_text(sierra) == OrderType.SIERRADONOR
    assert not mp.is_mediaplan_text("Crossings TV Media Proposal\nLanguage Block")


@pytest.mark.parametrize(
    "raw, want",
    [
        ("M-Fri(2p-4p)", ("M-F", "2p-4p")),
        ("M-Sun (11a-1p)", ("M-Su", "11a-1p")),
        ("Sat-Sun 8p-12a", ("Sa-Su", "8p-12a")),
        ("Sa-Su (6p-8p)", ("Sa-Su", "6p-8p")),
        ("M-F 6:30a-7am", ("M-F", "6:30a-7a")),
        ("Sat 9a-10a", ("Sa", "9a-10a")),
    ],
)
def test_dayparts(raw, want):
    assert mp.parse_daypart(raw) == want


def test_days_are_readable_by_the_line_writer():
    from browser_automation.etere_direct_client import parse_day_bits

    for raw in ("M-Fri(2p-4p)", "M-Sun (11a-1p)", "Sat-Sun 8p-12a"):
        assert any(parse_day_bits(mp.parse_daypart(raw)[0]).values())


def test_two_dayparts_on_one_row_refuse():
    with pytest.raises(ValueError, match="two dayparts"):
        mp.parse_daypart("M-F (7p-12a), Sat-Sun 8p-12a")


def _set(row_i, col_i, value):
    def f(grid):
        grid[row_i][col_i] = value

    return f


@pytest.mark.parametrize(
    "mutate, word",
    [
        (_set(2, 6, "44"), "Total Airtime Cost"),  # spots changed: 44 x 80 != 3,600
        (_set(2, 7, "$ 90.00"), "Total Airtime Cost"),  # rate changed
        (_set(2, 7, "TBD"), "cannot read the spot cost"),  # unreadable money is never $0
        (_set(3, 6, "40"), "Subtotal"),  # bonus count no longer foots
        (_set(4, 8, "$ 3,700.00"), "Subtotal"),  # subtotal money
        (_set(2, 5, "XYZ"), "Spot Type"),
        (_set(2, 3, "12/1"), "Start Date"),
        (_set(3, 0, "Klingon"), "ROS window"),
        (_set(1, 7, "Something"), "no column for rate"),  # renamed column refuses
        (_set(3, 7, "$ 5.00"), "bonus row carries money"),
    ],
)
def test_a_tampered_sheet_refuses(sheet, mutate, word):
    with pytest.raises(ValueError, match=word):
        _order(sheet, mutate)


def test_an_inserted_column_is_a_no_op(sheet):
    def insert(grid):
        for row in grid:
            row.insert(2, "Notes" if row is grid[1] else None)

    o = _order(sheet, insert)
    assert o.airtime_total == 3600.0 and [ln.total_spots for ln in o.lines] == [45, 45]


def test_translation_section_without_a_fee_refuses(sheet):
    with pytest.raises(ValueError, match="Fee"):
        _order(sheet, text=sheet[0].replace("Fee: $600", "Fee: to be agreed"))


def test_no_translation_section_means_no_production(sheet):
    text = sheet[0].replace("Translation Services", "Other Services").replace("Fee: $600", "")
    assert _order(sheet, text=text).production == 0.0


def test_plan_is_one_rotation_line_per_row_with_a_placeable_cap(sheet):
    o = _order(sheet)
    plan = auto.order_plan(o, o.start)
    assert [(p["description"], p["days"], p["time_range"]) for p in plan] == [
        ("M-F 2p-4p Punjabi", "M-F", "14:00-16:00"),
        ("BNS M-F Punjabi ROS", "M-F", "14:00-16:00"),
    ]
    for p in plan:
        assert p["active_days"] == 24  # 23 weekdays in December + Fri 1/1
        assert p["max_daily"] == 2 and p["max_daily"] * p["active_days"] >= p["total_spots"]
    # A late start keeps the count and raises the cap.
    late = auto.order_plan(o, date(2026, 12, 21))
    assert late[0]["date_from"] == date(2026, 12, 21) and late[0]["total_spots"] == 45
    assert late[0]["active_days"] == 10 and late[0]["max_daily"] == 5
    with pytest.raises(ValueError, match="no M-F day"):
        auto.order_plan(o, date(2027, 1, 2))


def test_default_code_and_description(sheet):
    o = _order(sheet)
    assert auto.default_code_desc(o, o.start, "Broadcast", "Soroudi Vision") == (
        "Soroudi 2612",
        "Soroudi Vision Dec 2026",
    )
    # 12/28/2026 is in broadcast January 2027; a Calendar contract stays December.
    assert auto.default_code_desc(o, date(2026, 12, 28), "Broadcast", "Soroudi Vision")[0] == (
        "Soroudi 2701"
    )
    assert auto.default_code_desc(o, date(2026, 12, 28), "Calendar", "Soroudi Vision") == (
        "Soroudi 2612",
        "Soroudi Vision Dec 2026",
    )


def test_bridge_detail_matches_the_sheet(real_pdfplumber, monkeypatch):
    monkeypatch.setitem(sys.modules, "pdfplumber", real_pdfplumber)
    from web.parser_bridge import (
        _DIRECT_DB_KEYS,
        _DIRECT_DB_TESTED_KEYS,
        _REGISTRY,
        _normalize_order,
    )

    assert "MEDIAPLAN" in _REGISTRY and "MEDIAPLAN" in _DIRECT_DB_KEYS
    assert "MEDIAPLAN" in _DIRECT_DB_TESTED_KEYS
    d = _normalize_order(mp.parse_mediaplan(str(FIXTURE)))
    lines = d["lines"]
    assert sum(ln["total_spots"] for ln in lines) == 90
    assert round(sum(ln["total_spots"] * ln["rate"] for ln in lines), 2) == 3600.0
    assert d.get("rates_are_net") in (False, None)
