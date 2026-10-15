"""
Snoqualmie Casino house-proposal parser + line plan.

The real proposal (Entertainment :15, revised 10/7/2026) is the fixture; it
carries its own totals, so it is its own oracle. Negative tests mutate the
extracted TEXT (the layer `parse_snoqualmie` feeds to `parse_snoqualmie_text`)
so a dropped cell, a blanked rate, a slid column and a wrong total each refuse,
while a no-op mutation still parses identically.
"""

import sys
from datetime import date
from pathlib import Path

import pytest

_root = Path(__file__).parent.parent.parent
for p in [str(_root), str(_root / "src")]:
    if p not in sys.path:
        sys.path.insert(0, p)

from browser_automation.parsers.snoqualmie_parser import (  # noqa: E402
    SnoqualmieParseError,
    is_snoqualmie_text,
    parse_snoqualmie,
    parse_snoqualmie_text,
)

PDF = str(_root / "tests" / "fixtures" / "snoqualmie" / "snoqualmie_entertainment_2610_revised.pdf")
ORIGINAL_NAME = "Crossings TV Snoqualmie_2026 Proposal_Entertainment_15secs_ Revised.pdf"


@pytest.fixture(scope="module")
def text(real_pdfplumber):
    with real_pdfplumber.open(PDF) as pdf:
        return "\n".join(p.extract_text() or "" for p in pdf.pages)


@pytest.fixture(scope="module")
def order(real_pdfplumber):
    return parse_snoqualmie(PDF)


# ── The real document ────────────────────────────────────────────────────────


def test_header(order):
    assert order.client == "Snoqualmie Casino & Hotel"
    assert order.contact == "Amanda Beltran"
    assert order.email == "abeltran@snocasino.com"
    assert order.billing_cycle == "Broadcast"
    assert order.market_code == "SEA"
    assert order.estimate == ""
    assert order.order_date == date(2026, 10, 7)
    assert order.length_sec == 15
    assert (order.start, order.end) == (date(2026, 10, 12), date(2026, 11, 11))
    assert order.flight_start == "10/12/2026" and order.flight_end == "11/11/2026"
    assert order.rates_are_net is True
    assert order.week_dates == [
        date(2026, 10, 12),
        date(2026, 10, 19),
        date(2026, 10, 26),
        date(2026, 11, 2),
        date(2026, 11, 9),
    ]


def test_lines(order):
    assert len(order.lines) == 15
    assert [ln.total_spots for ln in order.paid_lines] == [
        29,
        16,
        11,
        39,
        16,
        15,
        15,
        10,
        25,
        20,
        10,
    ]
    assert [ln.total_spots for ln in order.bonus_lines] == [28, 28, 28, 28]
    first = order.lines[0]
    assert first.program == "Mandarin/Cantonese News/Talk"
    assert (first.days, first.time_text) == ("M-F", "7p-9p")
    assert (first.rate, first.value_rate) == (22.0, 30.0)  # PROMO entered, NET is the value rate
    assert first.week_spots == [7, 7, 7, 7, 1]
    assert first.language == "Chinese" and first.program_short == "News/Talk"
    fil = order.lines[3]
    assert fil.time_ranges == ["4p-5p", "6p-7p"]
    assert fil.program_short == "News/Talk"  # 'News/talk' on the sheet
    assert order.lines[2].days == "Sa-Su" and order.lines[7].days == "Sa-Su"  # 'Sat- Sun'
    bonus = order.lines[11]
    assert bonus.is_bonus and bonus.rate == 0.0 and bonus.value_rate == 24.0
    assert bonus.language == "Chinese"
    assert all(ln.flight_start == date(2026, 10, 12) for ln in order.lines)


def test_money(order):
    assert order.net_airtime == 4430.0
    assert [(c.description, c.quantity, c.list_amount, c.amount) for c in order.charges] == [
        ("Subtitle (Raina)-Chinese, Viet, Korean", 3, 375.0, 285.0),
        ("Subtitle (Chef)- Chinese, Viet,Korean", 3, 375.0, 285.0),
    ]
    assert order.charges_total == 570.0


def test_notes(order):
    assert order.notes == [
        "*we will use English spot to run during Filipino programming",
        "Traffic information (ROTATE 3 SPOTS EVENLY)",
        "Sacred Riana should end 10/30",
        "Chef Ji-Sun should end 11/5",
        "Xodiac should end 11/12",
    ]


def test_campaign_from_the_file_name(text):
    assert parse_snoqualmie_text(text, source_path=ORIGINAL_NAME).campaign == "Entertainment"
    assert parse_snoqualmie_text(text, source_path="x.pdf").campaign == ""


def test_detector(text):
    assert is_snoqualmie_text(text)
    assert not is_snoqualmie_text("2026 Crossings TV Media Proposal Client Illinois Lottery")
    assert not is_snoqualmie_text("Snoqualmie Casino affidavit")


# ── Guards: mutate the text the parser reads ─────────────────────────────────


def _mut(text, old, new):
    assert old in text, old
    return text.replace(old, new, 1)


def test_noop_mutation_parses_identically(text, order):
    again = parse_snoqualmie_text(_mut(text, "Sat- Sun 10a-12p", "Sat-Sun 10a-12p"))
    assert [ln.week_spots for ln in again.lines] == [ln.week_spots for ln in order.lines]
    assert again.net_airtime == order.net_airtime


def test_dropped_cell_refuses(text):
    with pytest.raises(SnoqualmieParseError, match="number cells|a cell was dropped"):
        parse_snoqualmie_text(_mut(text, "$ 22.00 7 7 7 7 1 29", "$ 22.00 7 7 7 1 29"))


def test_wrong_total_refuses(text):
    with pytest.raises(SnoqualmieParseError, match="Total Units"):
        parse_snoqualmie_text(_mut(text, "7 7 7 7 1 29 $ 8 70.00", "7 7 7 7 1 28 $ 8 70.00"))


def test_blanked_promo_rate_refuses(text):
    with pytest.raises(SnoqualmieParseError):
        parse_snoqualmie_text(_mut(text, "M-F 9p-10p $ 30.00 $ 22.00", "M-F 9p-10p $ 30.00 $ -"))


def test_promo_times_units_must_equal_net(text):
    with pytest.raises(SnoqualmieParseError, match="PROMO"):
        parse_snoqualmie_text(_mut(text, "$ 638.00", "$ 640.00"))


def test_slid_header_column_refuses(text):
    with pytest.raises(SnoqualmieParseError):
        parse_snoqualmie_text(_mut(text, "26-Oct 2-Nov", "2-Nov"))


def test_paid_footer_mismatch_refuses(text):
    with pytest.raises(SnoqualmieParseError, match="Paid"):
        parse_snoqualmie_text(_mut(text, "Paid 53 53 48 46 6 206", "Paid 53 53 47 46 6 206"))


def test_final_cost_mismatch_refuses(text):
    with pytest.raises(SnoqualmieParseError, match="Final cost"):
        parse_snoqualmie_text(_mut(text, "$ 5,000.00", "$ 5,100.00"))


def test_charge_without_amount_refuses(text):
    with pytest.raises(SnoqualmieParseError, match="discounted amount"):
        parse_snoqualmie_text(
            _mut(
                text,
                "Korean 3 $ 375.00 $ 285.00\nSubtitle (Chef)",
                "Korean 3 $ 375.00 $ -\nSubtitle (Chef)",
            )
        )


def test_paid_row_without_window_refuses(text):
    with pytest.raises(SnoqualmieParseError, match="never defaulted"):
        parse_snoqualmie_text(_mut(text, "Mandarin Drama M-F 9p-10p", "Mandarin Drama"))


def test_non_monday_week_refuses(text):
    with pytest.raises(SnoqualmieParseError, match="not a Monday"):
        parse_snoqualmie_text(_mut(text, "12-Oct 19-Oct", "13-Oct 19-Oct"))


def test_week_year_comes_from_the_title(text):
    # Drop the year from the title: the revision date (10/7/2026) still dates the columns.
    again = parse_snoqualmie_text(
        _mut(text, "2026 Crossings TV Media Proposal", "Crossings TV Media Proposal")
    )
    assert again.week_dates[0] == date(2026, 10, 12)


def test_flight_outside_the_grid_refuses(text):
    with pytest.raises(SnoqualmieParseError, match="first week column"):
        parse_snoqualmie_text(
            _mut(text, "week of 10/12 through 11/11", "week of 10/5 through 11/11")
        )


# ── Line plan: late Thursday start, short last week ──────────────────────────


def _entered(plan):
    return sum(r["spots_per_week"] * r["weeks"] for *_rest, ranges, _n in plan for r in ranges)


def test_windows_and_descriptions(order):
    from browser_automation.snoqualmie_automation import line_description, line_window

    want = [
        ("M-F", "19:00-21:00", "M-F 7p-9p Chinese News/Talk"),
        ("M-F", "21:00-22:00", "M-F 9p-10p Mandarin Drama"),
        ("Sa-Su", "21:00-23:59", "Sa-Su 9p-12a Mandarin Entertainment"),
        ("M-F", "16:00-19:00", "M-F 4p-7p Filipino News/Talk"),  # union of 4p-5p + 6p-7p
        ("M-F", "17:00-18:00", "M-F 5p-6p Filipino Drama"),
        ("M-F", "11:00-12:00", "M-F 11a-12p Viet News/Variety"),
        ("M-F", "12:00-13:00", "M-F 12p-1p Viet Drama"),
        ("Sa-Su", "10:00-12:00", "Sa-Su 10a-12p Viet Drama"),
        ("M-F", "08:00-09:00", "M-F 8a-9a Korean News"),
        ("M-F", "09:00-10:00", "M-F 9a-10a Korean Drama"),
        ("Sa-Su", "08:00-10:00", "Sa-Su 8a-10a Korean Drama"),
        ("M-Su", "06:00-23:59", "BNS M-Su Chinese ROS"),
        ("M-Su", "16:00-19:00", "BNS M-Su Filipino ROS"),
        ("M-Su", "10:00-13:00", "BNS M-Su Viet ROS"),
        ("M-Su", "08:00-10:00", "BNS M-Su Korean ROS"),
    ]
    for ln, (days, tr, desc) in zip(order.lines, want):
        d, t, label = line_window(ln)
        assert (d, t) == (days, tr), ln.program
        assert line_description(ln, d, label) == desc


def test_thursday_start_compresses_the_first_week(order):
    from browser_automation.snoqualmie_automation import order_plan

    plan = order_plan(order, date(2026, 10, 15))
    assert _entered(plan) == 206 + 112  # nothing dropped: every pattern has a day left
    assert not any(n for *_r, n in plan)
    chinese = plan[0][4]  # 7/wk M-F: Thu+Fri → 4/day, then 2/day, then the 1-spot tail
    assert [
        (r["date_from"], r["date_to"], r["spots_per_week"], r["max_daily"]) for r in chinese
    ] == [
        (date(2026, 10, 15), date(2026, 10, 18), 7, 4),
        (date(2026, 10, 19), date(2026, 11, 8), 7, 2),
        (date(2026, 11, 9), date(2026, 11, 11), 1, 1),
    ]
    filipino = plan[3][4]  # 10/wk → 5/day on Thu+Fri, then 9/wk
    assert filipino[0]["max_daily"] == 5 and filipino[0]["weeks"] == 1
    weekend = plan[2][4]  # Sa-Su: untouched by a Thursday start
    assert weekend[0]["date_from"] == date(2026, 10, 15) and weekend[0]["max_daily"] == 2
    bonus = plan[11][4]  # 7/wk M-Su over Thu-Sun → 2/day, then 1/day
    assert bonus[0]["max_daily"] == 2 and bonus[1]["max_daily"] == 1


def test_monday_start_needs_no_split(order):
    from browser_automation.snoqualmie_automation import order_plan

    plan = order_plan(order, date(2026, 10, 19))
    entered = _entered(plan)
    assert entered == 206 + 112 - (53 + 28)  # week 1 dropped
    assert all(any("dropped" in n for n in notes) for *_r, notes in plan)
    assert plan[0][4][0]["date_from"] == date(2026, 10, 19)


def test_default_code_description_and_note(order, text):
    from browser_automation.snoqualmie_automation import default_code_desc, default_line_note

    assert default_code_desc(order, date(2026, 10, 15))[:2] == (
        "Snoqualmie 2610",
        "Snoqualmie Casino 2026",
    )
    named = parse_snoqualmie_text(text, source_path=ORIGINAL_NAME)
    assert default_code_desc(named, date(2026, 10, 15)) == (
        "Snoqualmie 2610",
        "Snoqualmie Casino Entertainment 2026",
        "Entertainment 2026",
    )
    assert default_line_note(order) == (
        "we will use English spot to run during Filipino programming; "
        "ROTATE 3 SPOTS EVENLY; Sacred Riana should end 10/30; "
        "Chef Ji-Sun should end 11/5; Xodiac should end 11/12"
    )
