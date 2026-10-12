"""
The Vida Agency / Puget Sound Energy proposal parser + line plan.

The real proposal (Puget Sound Flex, revised 9/23/2026) is the fixture; it
carries its own totals, so it is its own oracle. Negative tests mutate the
extracted page TEXT (the layer `parse_vida` feeds to `parse_vida_pages`) so a
dropped cell, a blanked rate, a slid column and a wrong total each refuse,
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

from browser_automation.parsers.vida_parser import (  # noqa: E402
    VidaParseError,
    is_vida_text,
    parse_vida,
    parse_vida_pages,
)

PDF = str(_root / "tests" / "fixtures" / "vida" / "vida_pse_flex_2610_revised.pdf")


@pytest.fixture(scope="module")
def pages(real_pdfplumber):
    with real_pdfplumber.open(PDF) as pdf:
        return [p.extract_text() or "" for p in pdf.pages]


@pytest.fixture(scope="module")
def order(real_pdfplumber):
    return parse_vida(PDF)


# ── The real document ────────────────────────────────────────────────────────


def test_header(order):
    assert order.agency == "The Vida Agency"
    assert order.project == "Puget Sound Flex"
    assert order.market_code == "SEA"
    assert order.order_date == date(2026, 9, 23)
    assert order.length_sec == 30
    assert order.rates_are_net is True
    assert order.flight_start == "10/12/2026"
    assert order.flight_end == "03/31/2027"


def test_three_flights_one_per_contract(order):
    assert [(fl.start, fl.end) for fl in order.flights] == [
        (date(2026, 10, 12), date(2026, 11, 8)),
        (date(2026, 12, 1), date(2026, 12, 31)),
        (date(2027, 3, 1), date(2027, 3, 31)),
    ]
    assert [len(fl.week_dates) for fl in order.flights] == [4, 5, 5]
    assert order.flights[1].week_dates[0] == date(
        2026, 11, 30
    )  # Dec opens Tue 12/1 on a 11/30 week
    assert order.flights[2].week_dates[-1] == date(2027, 3, 29)
    assert all(w.weekday() == 0 for fl in order.flights for w in fl.week_dates)


def test_lines_and_totals(order):
    want = [  # (paid spots, bonus spots, net)
        ((75, 75), 45, 6000.0),
        ((98, 77), 63, 7000.0),
        ((91, 84), 64, 7000.0),
    ]
    for fl, (paid, bonus, net) in zip(order.flights, want):
        assert tuple(ln.total_spots for ln in fl.paid_lines) == paid
        assert sum(ln.total_spots for ln in fl.bonus_lines) == bonus
        assert fl.net_total == net
        assert all(ln.rate == 40.0 for ln in fl.paid_lines)
        assert all(ln.rate == 0.0 and ln.value_rate == 40.0 for ln in fl.bonus_lines)
    assert order.net_airtime == 20000.0
    f1 = order.flights[0]
    assert f1.lines[0].program == "Vietnamese News /Variety"
    assert (f1.lines[0].days, f1.lines[0].time_text) == ("M-Su", "11a-12p")
    assert f1.lines[1].time_ranges == ["10a-11a", "12p-1p"]
    assert f1.lines[1].week_spots == [20, 19, 18, 18]
    assert f1.lines[2].is_bonus and f1.lines[2].week_spots == [10, 11, 12, 12]
    # Page 2 wraps the row names onto their own line — still read
    assert order.flights[2].lines[0].week_spots == [22, 20, 20, 20, 9]


def test_production_is_the_billed_net_amount(order):
    assert [(c.description, c.amount, c.list_amount) for c in order.charges] == [
        ("Production", 1000.0, 1950.0)
    ]


def test_detector(pages):
    assert is_vida_text(pages[0])
    assert not is_vida_text("Crossings TV Media Proposal Ntooitive L.A. Care Flight schedule")


# ── Guards: mutate the text the parser reads ─────────────────────────────────


def _mut(pages, old, new, page=0):
    assert old in pages[page], old
    out = list(pages)
    out[page] = pages[page].replace(old, new, 1)
    return out


def test_noop_mutation_parses_identically(pages, order):
    again = parse_vida_pages(_mut(pages, "M- Sun 11a-12p", "M-Sun 11a-12p"))
    assert [ln.week_spots for fl in again.flights for ln in fl.lines] == [
        ln.week_spots for fl in order.flights for ln in fl.lines
    ]
    assert again.net_airtime == order.net_airtime


def test_dropped_cell_refuses(pages):
    with pytest.raises(VidaParseError, match="number cells|a cell was dropped"):
        parse_vida_pages(
            _mut(
                pages,
                "$ 40.00 20 19 18 18 22 22 22 22 10 173",
                "$ 40.00 20 19 18 22 22 22 22 10 173",
            )
        )


def test_wrong_total_refuses(pages):
    with pytest.raises(VidaParseError, match="Total Units"):
        parse_vida_pages(
            _mut(pages, "22 22 22 22 10 173 $ 6,920.00", "22 22 22 22 10 172 $ 6,920.00")
        )


def test_blanked_paid_rate_refuses(pages):
    with pytest.raises(VidaParseError):
        parse_vida_pages(_mut(pages, "M- Sun 11a-12p $ 40.00", "M- Sun 11a-12p $ -"))


def test_slid_header_column_refuses(pages):
    with pytest.raises(VidaParseError):
        parse_vida_pages(_mut(pages, "26-Oct 2-Nov", "2-Nov"))


def test_paid_footer_mismatch_refuses(pages):
    with pytest.raises(VidaParseError, match="PAID"):
        parse_vida_pages(_mut(pages, "PAID 40 38 36 36", "PAID 40 38 35 36"))


def test_net_amount_mismatch_refuses(pages):
    with pytest.raises(VidaParseError, match="Net amount"):
        parse_vida_pages(_mut(pages, "Net amount $ 1 4,000.00", "Net amount $ 1 5,000.00"))


def test_paid_row_without_window_refuses(pages):
    with pytest.raises(VidaParseError, match="never defaulted"):
        parse_vida_pages(_mut(pages, "Vietnamese Drama M- Sun 10a-11a/12p-1p", "Vietnamese Drama"))


def test_week_year_rolls_forward_without_a_title_year(pages):
    # "March 2027 Flight schedule" → "March Flight schedule": the proposal is dated
    # 9/23/2026, so a March column must still land in 2027.
    again = parse_vida_pages(
        _mut(pages, "March 2027 Flight schedule", "March Flight schedule", page=1)
    )
    assert again.flights[2].week_dates[0] == date(2027, 3, 1)


def test_non_monday_week_refuses(pages):
    with pytest.raises(VidaParseError, match="not a Monday"):
        parse_vida_pages(_mut(pages, "12-Oct 19-Oct", "13-Oct 19-Oct"))


# ── Line plan: late Tuesday start, mid-week December start, short March tail ──


def _plan_for(order, i, start):
    from browser_automation.vida_automation import flight_plan

    return flight_plan(order.flights[i], start)


def _entered(plan):
    return sum(r["spots_per_week"] * r["weeks"] for *_rest, ranges, _n in plan for r in ranges)


def test_windows_and_descriptions(order):
    from browser_automation.vida_automation import line_description, line_window

    news, drama, bonus = order.flights[0].lines
    assert line_window(news)[:2] == ("M-Su", "11:00-12:00")
    assert line_window(drama)[:2] == ("M-Su", "10:00-13:00")  # union of 10a-11a + 12p-1p
    assert line_window(bonus)[:2] == ("M-Su", "10:00-13:00")  # Vietnamese ROS window
    assert line_description(news, *line_window(news)[::2]) == "M-Su 11a-12p Viet News/Variety"
    assert line_description(drama, *line_window(drama)[::2]) == "M-Su 10a-1p Viet Drama"
    assert line_description(bonus, *line_window(bonus)[::2]) == "BNS M-Su Viet ROS"


def test_tuesday_start_gives_the_short_week_its_own_cap(order):
    plan = _plan_for(order, 0, date(2026, 10, 13))
    assert _entered(plan) == 75 + 75 + 45  # nothing dropped
    news_ranges = plan[0][4]
    first = news_ranges[0]
    assert (first["date_from"], first["date_to"]) == (date(2026, 10, 13), date(2026, 10, 18))
    assert first["spots_per_week"] == 20 and first["max_daily"] == 4  # 20 in 6 days
    assert news_ranges[1]["date_from"] == date(2026, 10, 19)
    assert news_ranges[1]["max_daily"] == 3
    assert news_ranges[-1]["date_to"] == date(2026, 11, 8)
    assert not any(n for *_r, n in plan)


def test_december_opens_tuesday_on_its_own_grid(order):
    plan = _plan_for(order, 1, date(2026, 12, 1))
    assert _entered(plan) == 98 + 77 + 63
    first = plan[0][4][0]
    # 22 in the 6 days left of the 11/30 week needs 4/day — the same cap as a full
    # 22-spot week (ceil 22/7), so the short week merges into the 12/1-12/27 line.
    assert (first["date_from"], first["date_to"]) == (date(2026, 12, 1), date(2026, 12, 27))
    assert first["max_daily"] == 4 and first["weeks"] == 4
    last = plan[0][4][-1]
    assert (last["date_from"], last["date_to"]) == (date(2026, 12, 28), date(2026, 12, 31))
    assert last["max_daily"] == 3  # 10 in 4 days


def test_march_ends_wednesday(order):
    plan = _plan_for(order, 2, date(2027, 3, 1))
    assert _entered(plan) == 91 + 84 + 64
    assert all(r["date_to"] <= date(2027, 3, 31) for *_x, ranges, _n in plan for r in ranges)
    assert plan[0][4][-1]["date_to"] == date(2027, 3, 31)


def test_default_code_and_description(order):
    from browser_automation.vida_automation import default_code_desc

    f = order.flights
    assert default_code_desc(order, f[0], date(2026, 10, 13))[:2] == (
        "Vida PSE 2610",
        "Puget Sound Energy - Flex 2610",
    )
    assert default_code_desc(order, f[1], date(2026, 12, 1))[:2] == (
        "Vida PSE 2612",
        "Puget Sound Energy - Flex 2612",
    )
    assert default_code_desc(order, f[2], date(2027, 3, 1))[:2] == (
        "Vida PSE 2703",
        "Puget Sound Energy - Flex 2703",
    )
