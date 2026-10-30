"""
Harrah's Northern California — workbook parser + broadcast-month planner.

The real workbook (Charmaine's Option 1, 10/1/2026) is the fixture; it carries
its own totals, so it is its own oracle. Negative tests mutate the CELL GRID
(the layer `parse_harrahs` feeds to `parse_harrahs_rows`) so a dropped rate, a
wrong footer, a renamed header and a slid column each refuse, while a no-op
mutation (an inserted column) still parses identically.
"""

import copy
import sys
from datetime import date
from pathlib import Path

import pytest

_root = Path(__file__).parent.parent.parent
for p in [str(_root), str(_root / "src")]:
    if p not in sys.path:
        sys.path.insert(0, p)

from browser_automation.harrahs_automation import (  # noqa: E402
    default_code_desc,
    default_start,
    line_description,
    line_window,
    month_ranges,
    order_plan,
)
from browser_automation.parsers.harrahs_parser import (  # noqa: E402
    HarrahsParseError,
    is_harrahs_text,
    parse_harrahs,
    parse_harrahs_rows,
    workbook_rows,
)

XLSX = str(_root / "tests" / "fixtures" / "harrahs" / "harrahs_nocal_option1_2611.xlsx")


@pytest.fixture(scope="module")
def rows():
    return workbook_rows(XLSX)


@pytest.fixture(scope="module")
def order():
    return parse_harrahs(XLSX)


# ── The real workbook ────────────────────────────────────────────────────────


def test_header(order):
    assert order.client == "Harrah's Northen California"  # the sheet's own spelling
    assert order.contact == "Forest Chen"
    assert order.market_label == "Central Valley of California"
    assert order.market_code == "CVC"
    assert order.flight_label == "Monthly Estimate"
    assert order.proposal_date == date(2026, 10, 1)
    assert order.option == "OPTION 1"
    assert order.length_sec == 30
    assert order.months == 6
    assert order.rates_are_net is True


def test_rows(order):
    got = [
        (
            ln.description,
            ln.days,
            ln.time_text,
            ln.rate,
            ln.value_rate,
            ln.monthly_spots,
            ln.is_bonus,
        )
        for ln in order.lines
    ]
    assert got == [
        ("M-F 7p-9p Chinese News/Talk", "M-F", "7p-9p", 46.0, 51.0, 25, False),
        ("Sa-Su 9p-12a Mandarin Entertainment", "Sa-Su", "9p-12a", 25.0, 34.0, 6, False),
        ("M-F 11a-12p Vietnamese News/Variety", "M-F", "11a-12p", 36.0, 42.5, 20, False),
        ("M-F 6p-7p Filipino News & Talk", "M-F", "6p-7p", 36.0, 42.5, 15, False),
        ("BNS Sa-Su 6p-8p Hmong", "Sa-Su", "6p-8p", 0.0, 42.5, 12, True),
        ("BNS Cantonese ROS", "", "", 0.0, 34.0, 10, True),
        ("BNS Mandarin ROS", "", "", 0.0, 34.0, 10, True),
        ("BNS Vietnamese ROS", "", "", 0.0, 34.0, 12, True),
        ("BNS Filipino ROS", "", "", 0.0, 34.0, 8, True),
    ]
    hmong = order.lines[4]
    assert hmong.promo_printed == 36.0 and hmong.rate == 0.0  # BONUS flag wins over the Promo cell


def test_totals(order):
    assert order.paid_spots_per_month == 66 == order.stated_paid_spots
    assert order.bonus_spots_per_month == 52 == order.stated_bonus_spots
    assert order.net_per_month == 2560.0 == order.stated_net_total
    assert order.stated_value_total == 4836.5
    assert abs(order.stated_discount_rate - (1 - 2560 / 4836.5)) < 1e-4  # cells round to 4 dp
    assert any("6 months contract" in n for n in order.notes)


def test_detection_is_client_keyed():
    assert is_harrahs_text("Crossings TV Media Proposal Client Harrah's Northen California")
    assert is_harrahs_text("Harrah's Northern California")
    assert not is_harrahs_text("Harrahs Resort Southern California media proposal")
    assert not is_harrahs_text("Snoqualmie Casino & Hotel")


def test_scan_pdf_is_refused(tmp_path):
    pdf = tmp_path / "Approved Proposal- Crossing TV - Signed.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    with pytest.raises(HarrahsParseError, match="scan"):
        parse_harrahs(str(pdf))


# ── Planner ──────────────────────────────────────────────────────────────────


def test_month_ranges_from_a_mid_month_start():
    assert month_ranges(date(2026, 11, 2), 6) == [
        (date(2026, 11, 2), date(2026, 11, 29), "2611"),
        (date(2026, 11, 30), date(2026, 12, 27), "2612"),
        (date(2026, 12, 28), date(2027, 1, 31), "2701"),
        (date(2027, 2, 1), date(2027, 2, 28), "2702"),
        (date(2027, 3, 1), date(2027, 3, 28), "2703"),
        (date(2027, 3, 29), date(2027, 4, 25), "2704"),
    ]


def test_month_ranges_from_a_broadcast_month_monday():
    assert month_ranges(date(2026, 10, 26), 2) == [
        (date(2026, 10, 26), date(2026, 11, 29), "2611"),
        (date(2026, 11, 30), date(2026, 12, 27), "2612"),
    ]


def test_default_start_is_the_next_monday():
    assert default_start(date(2026, 10, 30)) == date(2026, 11, 2)
    assert default_start(date(2026, 11, 2)) == date(2026, 11, 2)


def test_plan_is_one_rotation_line_per_row_per_month(order):
    plan = order_plan(order, date(2026, 11, 2), 6)
    assert len(plan) == 9 * 6
    assert sum(p["total_spots"] for p in plan) == (66 + 52) * 6 == 708
    assert plan[0]["description"] == "M-F 7p-9p Chinese News/Talk"
    assert (plan[0]["date_from"], plan[0]["date_to"]) == (date(2026, 11, 2), date(2026, 11, 29))
    assert plan[0]["max_daily"] == 2  # 25 spots over 20 weekdays
    assert plan[5]["yymm"] == "2704" and plan[5]["date_to"] == date(2027, 4, 25)
    weekend = [p for p in plan if p["description"] == "Sa-Su 9p-12a Mandarin Entertainment"]
    assert weekend[0]["active_days"] == 8 and weekend[0]["max_daily"] == 1


def test_bonus_windows(order):
    by = {ln.description: ln for ln in order.lines}
    assert line_window(by["BNS Sa-Su 6p-8p Hmong"]) == ("Sa-Su", "18:00-20:00", "6p-8p")
    assert line_window(by["BNS Vietnamese ROS"]) == ("M-Su", "10:00-13:00", "10a-1p")
    assert line_window(by["BNS Mandarin ROS"])[0:2] == ("M-Su", "06:00-23:59")  # Chinese ROS
    assert line_description(by["BNS Vietnamese ROS"], "M-Su", "10a-1p") == "BNS Viet ROS"
    assert (
        line_description(by["M-F 11a-12p Vietnamese News/Variety"], "M-F", "11a-12p")
        == "M-F 11a-12p Viet News/Variety"
    )


def test_naming_defaults():
    assert default_code_desc(date(2026, 11, 2), 6) == (
        "Harrahs NoCal 2611",
        "Harrah's Northern CA 2611-2704",
        "",
    )


# ── Tampered grids ───────────────────────────────────────────────────────────


def _find(rows, text):
    for i, r in enumerate(rows):
        for j, v in enumerate(r):
            if isinstance(v, str) and v.strip() == text:
                return i, j
    raise AssertionError(text)


def test_inserted_column_is_a_no_op(rows):
    hdr_i, _ = _find(rows, "Language Block")
    mutated = copy.deepcopy(rows)
    for i in range(hdr_i, len(mutated)):
        mutated[i].insert(6, "Creative" if i == hdr_i else "TBD")
    a, b = parse_harrahs_rows(rows), parse_harrahs_rows(mutated)
    assert [vars(x) for x in a.lines] == [vars(x) for x in b.lines]


def test_renamed_header_refuses(rows):
    mutated = copy.deepcopy(rows)
    i, j = _find(mutated, "Monthly")
    mutated[i][j] = "Units"
    with pytest.raises(HarrahsParseError, match="Language Block"):
        parse_harrahs_rows(mutated)


def test_blanked_promo_refuses(rows):
    mutated = copy.deepcopy(rows)
    i, j = _find(mutated, "Promo")
    mutated[i + 1][j] = None
    with pytest.raises(HarrahsParseError, match="no Promo rate"):
        parse_harrahs_rows(mutated)


def test_wrong_row_net_refuses(rows):
    mutated = copy.deepcopy(rows)
    i, j = _find(mutated, "Promo")
    mutated[i + 1][j] = 45  # 45 × 25 != 1150
    with pytest.raises(HarrahsParseError, match="!= NET"):
        parse_harrahs_rows(mutated)


def test_wrong_footer_refuses(rows):
    mutated = copy.deepcopy(rows)
    i, j = _find(mutated, "Total Paid")
    mutated[i][j + 5] = 65
    with pytest.raises(HarrahsParseError, match="Total Paid spots"):
        parse_harrahs_rows(mutated)


def test_dropped_bonus_row_refuses(rows):
    mutated = copy.deepcopy(rows)
    i, _ = _find(mutated, "Hmong")
    del mutated[i]
    with pytest.raises(HarrahsParseError, match="Total Paid value"):
        parse_harrahs_rows(mutated)


def test_missing_time_window_refuses(rows):
    mutated = copy.deepcopy(rows)
    i, j = _find(mutated, "M-F 7p-9p")
    mutated[i][j] = "M-F"
    with pytest.raises(HarrahsParseError, match="no day pattern / time window"):
        parse_harrahs_rows(mutated)


# ── Parser bridge: the backwrite's view of the workbook ───────────────────────
# Contract 3231 (2026-10-30): the generic normalizer stored ONE month (66 paid
# spots, $2,560) and the one-click backwrite reconciled it against Etere's six
# (396, $15,360). The bridge walks `order_plan` with the gathered answers now.


def _bridge():
    from web.parser_bridge import get_order_detail

    return get_order_detail


def test_bridge_detail_is_the_entered_plan_when_the_gather_answers_are_known():
    d = _bridge()(
        Path(XLSX), "HARRAHS", user_inputs={"start_date_override": "11/02/2026", "months": 6}
    )
    assert len(d["lines"]) == 54
    assert d["total_spots"] == 708 and d["total_cost"] == 15360.0
    assert (d["flight_start"], d["flight_end"]) == ("11/02/2026", "04/25/2027")
    paid = sum(ln["total_spots"] for ln in d["lines"] if not ln["is_bonus"] and ln["rate"] > 0)
    assert paid == 396
    assert d["markets"] == ["CVC"] and d["rates_are_net"] is True
    first, last = d["lines"][0], d["lines"][-1]
    assert first["description"] == "M-F 7p-9p Chinese News/Talk"
    assert (first["start_date"], first["end_date"], first["rate"]) == (
        "11/02/2026",
        "11/29/2026",
        46.0,
    )
    assert (last["description"], last["start_date"], last["end_date"]) == (
        "BNS Filipino ROS",
        "03/29/2027",
        "04/25/2027",
    )
    assert last["rate"] == 0.0 and last["is_bonus"]
    # Line numbers are the SHEET row, shared by a row's six months.
    assert sorted({ln["line_number"] for ln in d["lines"]}) == list(range(1, 10))


def test_bridge_detail_without_answers_uses_the_gather_defaults_and_says_so():
    d = _bridge()(Path(XLSX), "HARRAHS")
    start = default_start()
    assert len(d["lines"]) == 54  # the sheet's own "6 months contract"
    assert d["flight_start"] == start.strftime("%m/%d/%Y")
    assert any("no flight dates" in w for w in d["warnings"])


def test_bridge_detail_honours_a_different_start_and_month_count():
    d = _bridge()(
        Path(XLSX), "HARRAHS", user_inputs={"start_date_override": "12/28/2026", "months": 2}
    )
    assert len(d["lines"]) == 18
    assert (d["flight_start"], d["flight_end"]) == ("12/28/2026", "02/28/2027")
    assert d["total_cost"] == 5120.0


def test_manifest_detail_with_no_dates_anywhere_is_re_derived():
    """A manifest written by the generic normalizer (one undated month) must be
    refreshed from the IO, the way a $0 detail is."""
    from web.routes.orders import _io_detail_is_blank, _io_detail_is_undated

    undated = {
        "flight_start": "",
        "flight_end": "",
        "lines": [{"rate": 46.0, "total_spots": 25, "start_date": "", "end_date": ""}],
    }
    assert _io_detail_is_undated(undated) and not _io_detail_is_blank(undated)
    dated = _bridge()(
        Path(XLSX), "HARRAHS", user_inputs={"start_date_override": "11/02/2026", "months": 6}
    )
    assert not _io_detail_is_undated(dated)
    assert not _io_detail_is_undated({"flight_start": "11/02/2026", "lines": [{"rate": 1}]})
    assert not _io_detail_is_undated({"lines": []})
