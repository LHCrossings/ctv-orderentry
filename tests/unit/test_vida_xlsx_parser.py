"""
The Vida Agency proposal as an EXCEL workbook — WA Health Plan Finder Open
Enrollment (REV1, 11/2/2026), advertiser WA Health Benefit Exchange.

The real workbook is the fixture and carries its own totals (PAID / BONUSES /
Total rows, the Investment Summary), so it is its own oracle. Negative tests
copy the workbook with openpyxl and edit ONE cell, so a dropped cell, an
edited net, a renamed header, a wrong rate and a non-Monday week each refuse,
while a no-op edit parses identically.

Oracle for the automation: contracts 1576 `Vida WAHBE 2411` "WA Health Benefit
Exchange Phase 1" and 1577 `… 2412` "… Phase 2" (CUSTOMERREF "Phase N",
P_AGENZIA 15, every line Rotation, the Cantonese "7p-8p; 11:30p-12a" row as
ONE 7p-12a line carrying only "C -" blocks).
"""

import shutil
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

_root = Path(__file__).parent.parent.parent
for p in [str(_root), str(_root / "src")]:
    if p not in sys.path:
        sys.path.insert(0, p)

from browser_automation.parsers.vida_parser import (  # noqa: E402
    VidaParseError,
    is_vida_xlsx_cell,
    parse_vida,
)

XLSX = _root / "tests" / "fixtures" / "vida" / "vida_wahbe_oe_2611_rev1.xlsx"
REAL_NAME = "Crossings TV Media Proposal_ The Vida Agency_WA Health Plan Finder_REV1_ for Lee.xlsx"


@pytest.fixture(scope="module")
def order():
    return parse_vida(str(XLSX))


def _edited(tmp_path: Path, **cells) -> str:
    """A copy of the fixture with the given cells overwritten (value None deletes)."""
    import openpyxl

    dst = tmp_path / "edited.xlsx"
    shutil.copy(XLSX, dst)
    wb = openpyxl.load_workbook(dst, data_only=True)  # values only: a re-save drops formula caches
    ws = wb.active
    for ref, val in cells.items():
        ws[ref].value = val
    wb.save(dst)
    wb.close()
    return str(dst)


def _shape(o) -> list:
    return [
        (
            fl.phase,
            fl.start,
            fl.end,
            [(ln.program, ln.rate, ln.net_rate, ln.week_spots) for ln in fl.lines],
        )
        for fl in o.flights
    ]


# ── The real workbook ────────────────────────────────────────────────────────


def test_header_and_money_basis(order):
    assert order.agency == "The Vida Agency"
    assert order.project == "WA Health Plan Finder Open Enrollment"
    assert order.market_code == "SEA"
    assert order.order_date == date(2026, 11, 2)  # the REVISION cell
    assert order.length_sec == 30
    assert order.rates_are_net is False  # DISCOUNTED is a GROSS rate
    assert order.agency_pct == 15.0  # 1 - Net / Gross on the PAID row
    assert order.flight_start == "11/16/2026" and order.flight_end == "01/15/2027"
    assert len(order.notes) == 1 and "Interview" in order.notes[0] and "$0" in order.notes[0]


def test_two_phases_one_contract_each(order):
    assert [(fl.phase, fl.start, fl.end, len(fl.week_dates)) for fl in order.flights] == [
        ("Phase 1", date(2026, 11, 16), date(2026, 12, 13), 4),
        ("Phase 2", date(2026, 12, 14), date(2027, 1, 15), 5),
    ]
    assert order.flights[0].label == "Phase 1 11/16-12/13/26"


def test_lines_and_totals(order):
    p1, p2 = order.flights
    assert [ln.program for ln in p1.lines] == [
        "Cantonese News & Talk",
        "Mandarin News",
        "Mandarin Drama",
        "Mandarin Entertainment",
        "Bonus Chinese ROS",
        "Vietnamese News /Variety",
        "Vietnamese Drama",
        "Bonus Vietnamese ROS",
    ]
    assert [ln.program for ln in p2.lines] == [ln.program for ln in p1.lines]
    assert [ln.is_bonus for ln in p1.lines] == [False] * 4 + [True] + [False] * 2 + [True]
    # Rates enter as printed (gross); the net per spot is the sheet's 15% off.
    assert [ln.rate for ln in p1.lines] == [55, 55, 55, 55, 0, 45, 45, 0]
    assert [ln.net_per_spot for ln in p1.lines] == [46.75] * 4 + [0] + [38.25] * 2 + [0]
    assert [ln.rate_card for ln in p1.lines] == [70, 70, 70, 70, 50, 60, 60, 40]
    # Week cells: phase 2's last week (1/11-1/15) is short.
    assert p1.lines[0].week_spots == [5, 5, 5, 5]
    assert p2.lines[0].week_spots == [5, 5, 5, 5, 2]
    assert p2.lines[5].week_spots == [8, 8, 8, 8, 4]
    paid = sum(ln.total_spots for fl in order.flights for ln in fl.paid_lines)
    bonus = sum(ln.total_spots for fl in order.flights for ln in fl.bonus_lines)
    assert (paid, bonus) == (288, 166)  # the PAID / BONUSES footer
    assert (p1.entered_total, p1.net_total) == (6840.0, 5814.0)
    assert (p2.entered_total, p2.net_total) == (7640.0, 6494.0)
    assert (order.entered_airtime, order.net_airtime) == (14480.0, 12308.0)


def test_production_box_is_the_gross_and_the_sheet_net_is_kept(order):
    (ch,) = order.charges
    assert ch.description == "Production"
    assert ch.amount == 3176.47  # Proposed GROSS → the Production box (Etere nets 15%)
    assert ch.net_amount == 2700.0 and ch.billed == 2700.0
    assert ch.list_amount == 4650.0


def test_dual_dayparts_and_ros_windows_follow_1577(order):
    from browser_automation.vida_automation import line_description, line_window

    cant, mnews, _md, _me, cros, vnews, vdrama, vros = order.flights[0].lines
    assert cant.time_ranges == ["7p-8p", "11:30p-12a"]
    assert line_window(cant)[:2] == ("M-F", "19:00-23:59")  # ONE union line (1577 line 43819)
    assert line_description(cant, *line_window(cant)[::2]) == "M-F 7p-11:59p Cantonese News & Talk"
    assert line_window(mnews)[:2] == ("M-Su", "20:00-21:00")
    assert line_window(cros)[:2] == ("M-Su", "19:00-23:59")  # Chinese ROS = 1577 line 43827
    assert line_description(cros, *line_window(cros)[::2]) == "BNS M-Su Chinese ROS"
    assert line_window(vnews)[:2] == ("M-Su", "11:00-12:00")
    assert line_window(vdrama)[:2] == ("M-Su", "10:00-13:00")
    assert line_window(vros)[:2] == ("M-Su", "10:00-13:00")


def test_blocks_are_pruned_to_the_lines_language():
    from browser_automation.vida_automation import LANGUAGE_BLOCK_PREFIXES, blocks_to_drop

    loaded = {
        1: "C - Cantonese Primetime News",
        2: "M - Primetime News REV",
        3: "M - Mandarin Drama",
        4: "C - Cantonese Late News NEW",
        5: "J - Japan Hour",
    }
    assert blocks_to_drop(loaded, LANGUAGE_BLOCK_PREFIXES["Cantonese"]) == ([1, 4], [2, 3, 5])
    assert blocks_to_drop(loaded, LANGUAGE_BLOCK_PREFIXES["Chinese"]) == ([1, 2, 3, 4], [5])
    assert blocks_to_drop(loaded, LANGUAGE_BLOCK_PREFIXES["Mandarin"]) == ([2, 3], [1, 4, 5])
    # 'Hm - ' must not match 'H…' of another prefix, and vice versa
    assert blocks_to_drop({7: "Hm - Hmong Hour", 8: "H - Nothing"}, ("Hm",)) == ([7], [8])


def _active_days(days: str, a: date, b: date) -> int:
    pattern = {"M-F": range(0, 5), "M-Su": range(0, 7), "Sa-Su": (5, 6)}[days]
    return sum(1 for i in range((b - a).days + 1) if (a + timedelta(days=i)).weekday() in pattern)


def test_plan_phase_2_short_last_week_gets_its_own_cap(order):
    from browser_automation.vida_automation import flight_plan

    for fl in order.flights:
        plan = flight_plan(fl, fl.start)
        entered = sum(r["spots_per_week"] * r["weeks"] for *_x, ranges, _n in plan for r in ranges)
        assert entered == sum(ln.total_spots for ln in fl.lines)
        for _ln, days, _tr, _desc, ranges, notes in plan:
            assert not notes
            for r in ranges:
                room = r["max_daily"] * _active_days(days, r["date_from"], r["date_to"])
                assert r["spots_per_week"] * r["weeks"] <= room, (days, r)
    plan2 = flight_plan(order.flights[1], date(2026, 12, 14))
    cant = plan2[0][4]
    assert [
        (r["date_from"], r["date_to"], r["spots_per_week"], r["weeks"], r["max_daily"])
        for r in cant
    ] == [
        (date(2026, 12, 14), date(2027, 1, 10), 5, 4, 1),
        (date(2027, 1, 11), date(2027, 1, 15), 2, 1, 1),
    ]
    vnews = plan2[5][4]
    assert (vnews[-1]["date_from"], vnews[-1]["date_to"], vnews[-1]["spots_per_week"]) == (
        date(2027, 1, 11),
        date(2027, 1, 15),
        4,
    )
    assert all(r["date_to"] <= date(2027, 1, 15) for *_x, ranges, _n in plan2 for r in ranges)


def test_naming_comes_from_the_customers_prior_contracts(order):
    from browser_automation.vida_automation import default_code_desc, naming_from_contracts

    wahbe = [
        ("Vida WAHBE 2412", "WA Health Benefit Exchange Phase 2"),
        ("Vida WAHBE 2411", "WA Health Benefit Exchange Phase 1"),
    ]
    assert naming_from_contracts(wahbe) == ("WAHBE", "WA Health Benefit Exchange")
    pse = [
        ("Vida PSE 2703", "Puget Sound Energy - Flex 2703"),
        ("Vida PSE 2411", "Puget Sound Energy - Weatherization Program 2411"),
        ("Vida PSE 2410", "Puget Sound Energy - Small Change Big Impact 2410"),
    ]
    assert naming_from_contracts(pse) == ("PSE", "Puget Sound Energy -")
    assert naming_from_contracts([]) == ("", "")

    prec = {
        "short": "WAHBE",
        "desc_prefix": "WA Health Benefit Exchange",
        "customer_name": "WA Health Benefit Exchange Campaign",
    }
    f1, f2 = order.flights
    assert default_code_desc(order, f1, f1.start, None, prec)[:2] == (
        "Vida WAHBE 2611",
        "WA Health Benefit Exchange Phase 1",
    )
    assert default_code_desc(order, f2, f2.start, None, prec)[:2] == (
        "Vida WAHBE 2612",
        "WA Health Benefit Exchange Phase 2",
    )
    # No precedent and no customer row: initials of the project, the project as
    # the prefix, phase kept — a prompt default, never written unasked.
    assert default_code_desc(order, f1, f1.start)[:2] == (
        "Vida WHPFO 2611",
        "WA Health Plan Finder Open Enrollment Phase 1",
    )


def test_pse_naming_unchanged_with_a_precedent(real_pdfplumber):
    """The PSE PDF keeps 'Puget Sound Energy - Flex 2610' when the precedent is
    the generic prefix (the project tail fills the description)."""
    from browser_automation.vida_automation import default_code_desc

    pdf = parse_vida(str(_root / "tests" / "fixtures" / "vida" / "vida_pse_flex_2610_revised.pdf"))
    prec = {
        "short": "PSE",
        "desc_prefix": "Puget Sound Energy -",
        "customer_name": "Puget Sound Energy",
    }
    assert default_code_desc(pdf, pdf.flights[0], date(2026, 10, 13), None, prec)[:2] == (
        "Vida PSE 2610",
        "Puget Sound Energy - Flex 2610",
    )
    assert pdf.agency_pct == 0.0 and pdf.rates_are_net is True
    assert pdf.charges[0].billed == pdf.charges[0].amount == 1000.0


def test_detection():
    from orchestration.order_scanner import _detect_xlsx_content
    from src.business_logic.services.order_detection_service import detect_from_filename
    from src.domain.enums import OrderType

    assert is_vida_xlsx_cell("The Vida Agency")
    assert not is_vida_xlsx_cell("Crossings TV")
    # .value: the enum module is importable under two names (src.domain / domain)
    assert detect_from_filename(REAL_NAME).value == OrderType.VIDA.value == "vida"
    assert _detect_xlsx_content(XLSX).value == "vida"


def test_bridge_detail_is_gross_with_no_gross_up(order):
    from web.parser_bridge import _normalize_order

    d = _normalize_order(order)
    assert d["rates_are_net"] is False
    lines = d["lines"]
    assert len(lines) == 16
    assert sum(ln["total_spots"] for ln in lines) == 454
    assert {ln["rate"] for ln in lines if ln["rate"]} == {55.0, 45.0}


# ── Tampered copies refuse; a no-op copy parses identically ──────────────────


def test_noop_edit_parses_identically(tmp_path, order):
    same = parse_vida(_edited(tmp_path, G14=5, S14=1963.5))
    assert _shape(same) == _shape(order)
    assert same.agency_pct == order.agency_pct and same.charges == order.charges


def test_dropped_week_cell_refuses(tmp_path):
    with pytest.raises(VidaParseError, match="Total Units"):
        parse_vida(_edited(tmp_path, G14=None))


def test_edited_net_refuses_as_a_commission_disagreement(tmp_path):
    with pytest.raises(VidaParseError, match="commission"):
        parse_vida(_edited(tmp_path, S14=2000))


def test_edited_rate_refuses(tmp_path):
    with pytest.raises(VidaParseError, match="Proposed GROSS"):
        parse_vida(_edited(tmp_path, F14=60))


def test_renamed_header_refuses_by_name(tmp_path):
    with pytest.raises(VidaParseError, match="Total Units"):
        parse_vida(_edited(tmp_path, P13="Units"))


def test_non_monday_week_refuses(tmp_path):
    from datetime import datetime

    with pytest.raises(VidaParseError, match="not a Monday"):
        parse_vida(_edited(tmp_path, G13=datetime(2026, 11, 17)))


def test_unreadable_phase_window_refuses(tmp_path):
    with pytest.raises(VidaParseError, match="flight window"):
        parse_vida(_edited(tmp_path, L12="Phase 2 - the rest"))


def test_paid_footer_edit_refuses(tmp_path):
    with pytest.raises(VidaParseError, match="PAID"):
        parse_vida(_edited(tmp_path, P22=289))


def test_production_net_not_at_the_sheets_commission_refuses(tmp_path):
    with pytest.raises(VidaParseError, match="PRODUCTION"):
        parse_vida(_edited(tmp_path, S25=2600))
