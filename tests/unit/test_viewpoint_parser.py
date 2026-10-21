"""
Viewpoint Ads political option sheet parser + line plan.

Both real proposals (Option 1 / Option 2, Mayor Lurie Proposition, SF, prepared
10/20/2026) are fixtures; each carries its own TOTAL row and budget lines, so it
is its own oracle. Negative tests mutate the extracted TEXT (the layer
`parse_viewpoint` feeds to `parse_viewpoint_text`) so a dropped cell, a changed
total, a wrong net cell and an unshapeable row each refuse, while a no-op
mutation still parses identically.
"""

import sys
from datetime import date
from pathlib import Path

import pytest

_root = Path(__file__).parent.parent.parent
for p in [str(_root), str(_root / "src")]:
    if p not in sys.path:
        sys.path.insert(0, p)

from browser_automation.parsers.viewpoint_parser import (  # noqa: E402
    ViewpointParseError,
    is_viewpoint_text,
    parse_viewpoint,
    parse_viewpoint_text,
)

FIX = _root / "tests" / "fixtures" / "viewpoint"
PDF1 = str(FIX / "viewpoint_lurie_sf_option1.pdf")
PDF2 = str(FIX / "viewpoint_lurie_sf_option2.pdf")
BASE = date(2026, 10, 20)  # the PDFs' creation date


@pytest.fixture(scope="module")
def text1(real_pdfplumber):
    with real_pdfplumber.open(PDF1) as pdf:
        return "\n".join(p.extract_text() or "" for p in pdf.pages)


@pytest.fixture(scope="module")
def text2(real_pdfplumber):
    with real_pdfplumber.open(PDF2) as pdf:
        return "\n".join(p.extract_text() or "" for p in pdf.pages)


@pytest.fixture(scope="module")
def opt1(real_pdfplumber):
    return parse_viewpoint(PDF1)


@pytest.fixture(scope="module")
def opt2(real_pdfplumber):
    return parse_viewpoint(PDF2)


# ── The real documents ───────────────────────────────────────────────────────


def test_header_option1(opt1):
    assert opt1.option == "Option 1"
    assert opt1.agency == "Viewpoint Ads"
    assert opt1.advertiser_raw == "TBD ( Mayor Lurie Proposition)"
    assert opt1.advertiser == "Mayor Lurie Proposition" and opt1.advertiser_tbd
    assert opt1.contact == "Maggie Hartzell"
    assert opt1.email == "maggie@viewpointads.com"
    assert opt1.phone == "262-806-2919"
    assert opt1.prepared_by == "Charmaine Lane"
    assert (opt1.start, opt1.end) == (date(2026, 10, 23), date(2026, 11, 3))
    assert opt1.end_cutoff == "07:00"
    assert opt1.billing == "Broadcast month"
    assert (opt1.gross_budget, opt1.net_budget) == (30340.0, 25789.0)
    assert opt1.length_sec == 30
    assert opt1.market_code == "SFO"
    assert opt1.year_source.startswith("the PDF's creation date 10/20/2026")
    assert opt1.rates_are_net is False
    assert opt1.notes == ["*Note: No separation requirement"]
    assert opt1.warnings == []


def test_lines_option1(opt1):
    rows = [
        (ln.days, ln.time_text, ln.program, ln.rate, ln.total_spots, ln.flight_end)
        for ln in opt1.lines
    ]
    nov2, nov3 = date(2026, 11, 2), date(2026, 11, 3)
    assert rows == [
        ("M-F", "6a-7a", "Mandarin News", 120.0, 24, nov3),  # ends 7am → airs election morning
        ("M-F", "7p-7:30p", "Cantonese News", 212.0, 14, nov2),
        ("M-F", "7:30p-8p", "Cantonese Talk", 212.0, 14, nov2),
        ("M-F", "8p-9p", "Mandarin News", 212.0, 22, nov2),
        ("M-F", "9p-10p", "Mandarin Drama", 212.0, 22, nov2),
        (
            "M-F",
            "10p-11:30p",
            "Mandarin Entertainment",
            212.0,
            21,
            nov2,
        ),  # 'Mandarin' wrapped above
        ("M-F", "11:30p-12a", "Cantonese News", 212.0, 12, nov2),
        ("Sa", "6a-7a", "Mandarin News", 90.0, 8, nov3),
        ("Sa-Su", "8p-9p", "Mandarin News", 140.0, 14, nov2),  # 'Sat- Sun'
        ("Sa-Su", "9p-12a", "Mandarin Entertainment", 140.0, 18, nov2),  # 'Sat& Sun'
    ]
    assert all(ln.flight_start == date(2026, 10, 23) for ln in opt1.lines)
    assert not any(ln.is_bonus for ln in opt1.lines)
    assert opt1.total_spots == 169
    assert (opt1.gross_airtime, opt1.net_airtime) == (30340.0, 25789.0)
    assert opt1.commission_pct == 15.0
    assert opt1.lines[0].language == "Mandarin" and opt1.lines[0].program_short == "News"
    assert opt1.lines[1].description == "M-F 7p-7:30p Cantonese News"
    assert opt1.lines[0].start_date == "10/23/2026" and opt1.lines[0].end_date == "11/03/2026"


def test_option2(opt2):
    assert opt2.option == "Option 2"
    assert opt2.billing == ""  # the Billing line is absent on Option 2
    assert (opt2.gross_budget, opt2.net_budget) == (21266.0, 18076.1)
    assert [ln.total_spots for ln in opt2.lines] == [10, 12, 12, 16, 14, 14, 10, 5, 10, 12]
    assert opt2.total_spots == 115
    assert (opt2.gross_airtime, opt2.net_airtime) == (21266.0, 18076.1)
    assert opt2.lines[-1].program == "Mandarin Entertainment"  # 'Mandarin Entertainm' completed
    assert opt2.warnings == ["program cell 'Mandarin Entertainm' read as 'Mandarin Entertainment'"]
    assert opt2.lines[5].program == "Mandarin Entertainment"  # 'Mandarin' / noise line / row
    assert opt2.market_code == "SFO"


def test_detector(text1, text2):
    assert is_viewpoint_text(text1) and is_viewpoint_text(text2)
    assert not is_viewpoint_text("2026 Crossings TV Media Proposal Client Snoqualmie Casino")
    assert not is_viewpoint_text("Viewpoint Ads invoice 2610")


# ── Guards: mutate the text the parser reads ─────────────────────────────────


def _mut(text, old, new):
    assert old in text, old
    return text.replace(old, new, 1)


def _parse(text):
    return parse_viewpoint_text(text, base_date=BASE)


def test_noop_mutation_parses_identically(text1, opt1):
    again = _parse(_mut(text1, "Sat- Sun 8p-9p", "Sat-Sun 8p-9p"))
    assert [(ln.days, ln.total_spots) for ln in again.lines] == [
        (ln.days, ln.total_spots) for ln in opt1.lines
    ]
    assert again.gross_airtime == opt1.gross_airtime


def test_dropped_unit_cell_refuses(text1):
    with pytest.raises(ViewpointParseError, match="cannot shape row"):
        _parse(_mut(text1, "$120 24 $2,880.00", "$120 $2,880.00"))


def test_rate_times_units_must_equal_gross(text1):
    with pytest.raises(ViewpointParseError, match="!= GROSS"):
        _parse(_mut(text1, "$120 24 $2,880.00", "$120 23 $2,880.00"))


def test_total_units_mismatch_refuses(text1):
    with pytest.raises(ViewpointParseError, match="TOTAL says"):
        _parse(_mut(text1, "TOTAL 169 $30,340.00", "TOTAL 168 $30,340.00"))


def test_gross_budget_mismatch_refuses(text1):
    with pytest.raises(ViewpointParseError, match="TOTAL GROSS BUDGET"):
        _parse(_mut(text1, "TOTAL GROSS BUDGET: $30,340.00", "TOTAL GROSS BUDGET: $30,440.00"))


def test_net_cell_misread_refuses(text1):
    # One row's net off → the net/gross ratio no longer matches the other rows.
    with pytest.raises(ViewpointParseError, match="NET / GROSS differs"):
        _parse(_mut(text1, "$2,880.00 $2,448.00", "$2,880.00 $2,440.00"))


def test_missing_total_row_refuses(text1):
    with pytest.raises(ViewpointParseError, match="no TOTAL row"):
        _parse(text1.split("TOTAL 169")[0])


def test_orphan_program_text_refuses(text1):
    with pytest.raises(ViewpointParseError, match="belongs to no row"):
        _parse(_mut(text1, "TOTAL 169", "Cantonese\nTOTAL 169"))


def test_unknown_market_cell_leaves_code_blank(text1):
    again = _parse(text1.replace("SAN FRANCISCO", "NOWHERE"))
    assert again.market_code == "" and again.lines[5].program == "Mandarin Entertainment"


def test_flight_without_cutoff_ends_every_line_on_the_last_day(text1):
    again = _parse(_mut(text1, "10/23 through 11/3 ends at 7am", "10/23 through 11/3"))
    assert again.end_cutoff == ""
    assert {ln.flight_end for ln in again.lines} == {date(2026, 11, 3)}


def test_year_rolls_forward_from_a_december_base(text1):
    jan = _mut(text1, "10/23 through 11/3 ends at 7am", "1/5 through 1/18")
    again = parse_viewpoint_text(jan, base_date=date(2026, 12, 15))
    assert (again.start, again.end) == (date(2027, 1, 5), date(2027, 1, 18))
    same = parse_viewpoint_text(text1, base_date=date(2026, 12, 15))
    assert same.start == date(2026, 10, 23)  # two months back is still this year


def test_sheet_year_wins_when_printed(text1):
    again = _parse(_mut(text1, "10/23 through 11/3 ends at 7am", "10/23/2027 through 11/3/2027"))
    assert (again.start, again.end) == (date(2027, 10, 23), date(2027, 11, 3))


# ── Line plan ────────────────────────────────────────────────────────────────


def test_windows_and_descriptions(opt1):
    from browser_automation.viewpoint_automation import line_description, line_window

    want = [
        ("M-F", "06:00-07:00", "M-F 6a-7a Mandarin News"),
        ("M-F", "19:00-19:30", "M-F 7p-7:30p Cantonese News"),
        ("M-F", "19:30-20:00", "M-F 7:30p-8p Cantonese Talk"),
        ("M-F", "20:00-21:00", "M-F 8p-9p Mandarin News"),
        ("M-F", "21:00-22:00", "M-F 9p-10p Mandarin Drama"),
        ("M-F", "22:00-23:30", "M-F 10p-11:30p Mandarin Entertainment"),
        ("M-F", "23:30-23:59", "M-F 11:30p-12a Cantonese News"),
        ("Sa", "06:00-07:00", "Sa 6a-7a Mandarin News"),
        ("Sa-Su", "20:00-21:00", "Sa-Su 8p-9p Mandarin News"),
        ("Sa-Su", "21:00-23:59", "Sa-Su 9p-12a Mandarin Entertainment"),
    ]
    for ln, (days, tr, desc) in zip(opt1.lines, want):
        d, t, label = line_window(ln)
        assert (d, t) == (days, tr), ln.program
        assert line_description(ln, d, label) == desc


def test_every_line_sits_in_its_language_window(opt1):
    from browser_automation.language_windows import check_language_window
    from browser_automation.viewpoint_automation import line_window

    for ln in opt1.lines:
        _d, tr, _l = line_window(ln)
        t_from, t_to = tr.split("-")
        assert check_language_window(ln.language, t_from, t_to, market="SFO") is None, (
            ln.description
        )


def test_plan_from_the_sheet_start(opt1):
    from browser_automation.viewpoint_automation import order_plan

    plan = order_plan(opt1, date(2026, 10, 23))  # a Friday
    assert sum(p["spots"] for p in plan) == 169 and not any(p["notes"] for p in plan)
    caps = [(p["active_days"], p["max_daily"], p["date_to"]) for p in plan]
    assert caps == [
        (8, 3, date(2026, 11, 3)),  # M-F 6a-7a: Fri + 5 + Mon + Tue → 24/8
        (7, 2, date(2026, 11, 2)),  # 14/7
        (7, 2, date(2026, 11, 2)),
        (7, 4, date(2026, 11, 2)),  # 22/7 → 4
        (7, 4, date(2026, 11, 2)),
        (7, 3, date(2026, 11, 2)),  # 21/7
        (7, 2, date(2026, 11, 2)),  # 12/7
        (2, 4, date(2026, 11, 3)),  # two Saturdays, 8 spots
        (4, 4, date(2026, 11, 2)),  # 14 over 4 weekend days
        (4, 5, date(2026, 11, 2)),  # 18 over 4
    ]
    assert all(p["date_from"] == date(2026, 10, 23) for p in plan)


def test_late_start_compresses_into_fewer_days(opt1):
    from browser_automation.viewpoint_automation import order_plan

    plan = order_plan(opt1, date(2026, 10, 26))  # the Monday
    assert sum(p["spots"] for p in plan) == 169
    assert (plan[0]["active_days"], plan[0]["max_daily"]) == (7, 4)  # 24 over 7 M-F days
    assert (plan[7]["active_days"], plan[7]["max_daily"]) == (1, 8)  # one Saturday left
    assert plan[0]["date_from"] == date(2026, 10, 26)


def test_start_after_the_last_saturday_drops_the_saturday_line(opt1):
    from browser_automation.viewpoint_automation import order_plan

    plan = order_plan(opt1, date(2026, 11, 2))
    sat = plan[7]
    assert sat["spots"] == 0 and sat["notes"] and "undeliverable" in sat["notes"][0]
    assert plan[0]["spots"] == 24 and plan[0]["active_days"] == 2  # Mon + Tue
    assert sum(p["spots"] for p in plan) == 169 - 8 - 14 - 18  # both weekend lines gone


def test_fit_hints_name_the_tight_lines(opt1):
    from browser_automation.viewpoint_automation import fit_hints, order_plan

    plan = order_plan(opt1, date(2026, 10, 23))
    tight = fit_hints(plan, 25)
    assert any(h.startswith("Sa 6a-7a Mandarin News: 4/day needs 75 min") for h in tight)
    assert any(h.startswith("Sa-Su 8p-9p Mandarin News: 4/day") for h in tight)
    assert fit_hints(plan, 0) == []
    assert not any("9p-12a" in h for h in fit_hints(plan, 15))  # 5/day in 3 h fits


def test_default_code_description_and_note(opt1):
    from browser_automation.viewpoint_automation import (
        default_code_desc,
        default_header_note,
        learn_prefixes,
    )

    assert default_code_desc(opt1, date(2026, 10, 23)) == (
        "Viewpoint Lurie 261023",
        "Mayor Lurie Proposition 261023-261103",
        "",
    )

    class _Cust:
        code_name = "Viewpoint Yes on X"
        description_name = "Yes on X"

    assert default_code_desc(opt1, date(2026, 10, 26), _Cust())[:2] == (
        "Viewpoint Yes on X 261026",
        "Yes on X 261026-261103",
    )
    assert learn_prefixes("Viewpoint Yes on X 261026", "Yes on X 261026-261103") == (
        "Viewpoint Yes on X",
        "Yes on X",
    )
    assert default_header_note(opt1) == (
        "Option 1 proposal; advertiser printed as TBD (Mayor Lurie Proposition); "
        "flight ends 07:00 on 11/03/2026; Note: No separation requirement"
    )
