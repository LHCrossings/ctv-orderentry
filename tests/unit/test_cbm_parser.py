"""California Black Media SOS proposal — real PDF fixtures + tampering.

The fixtures are the actual Campaign 3 pair (10/26-11/2 2026): Charmaine's
"Crossings TV Media Proposal" (the order, three market grids + summary) and CBM's
Docusign Insertion Order (campaign name, counts, total; no lines). Every guard is
exercised by mutating the table cells pdfplumber hands the parser.
"""

from __future__ import annotations

import copy
import shutil
import sys
from datetime import date
from pathlib import Path

import pytest

_root = Path(__file__).resolve().parents[2]
for _p in [str(_root), str(_root / "src")]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

from browser_automation.parsers import cbm_parser as cp  # noqa: E402

FIXTURES = _root / "tests" / "fixtures" / "cbm"
PROPOSAL = FIXTURES / "sos_c3_proposal_2611.pdf"
IO = FIXTURES / "sos_c3_io_2611.pdf"
TODAY = date(2026, 10, 26)


@pytest.fixture(scope="module")
def real(real_pdfplumber):
    cp.pdfplumber = real_pdfplumber
    try:
        yield real_pdfplumber
    finally:
        cp.pdfplumber = sys.modules["pdfplumber"]


@pytest.fixture(scope="module")
def order(real):
    return cp.parse_cbm(str(PROPOSAL), today=TODAY)


def test_header(order):
    assert order.client == "California Black Media"
    assert (order.agency_label, order.campaign_tag) == ("AMERICAN COMMUNITY MEDIA", "SOS")
    assert order.contact == "DANA LEVINE" and order.email == "LEVINE.S.DANA@GMAIL.COM"
    assert order.billing_cycle == "Broadcast"
    assert order.market_text == "LA, SF, CVC" and order.markets == ["LAX", "SFO", "CVC"]
    assert order.week_dates == [date(2026, 10, 26), date(2026, 11, 2)]
    assert order.rates_are_net is True


def test_grids(order):
    assert [(ln.market, ln.description, ln.weekly_spots, ln.net_rate) for ln in order.lines] == [
        ("LAX", "Hindi News/Talk M-F 1p-2p", [5, 2], 102.0),
        ("LAX", "Hindi Entertainment Sat-Sun 1p-4p", [4, 0], 85.0),
        ("LAX", "BNS Hindi ROS", [5, 1], 0.0),
        ("SFO", "Hindi News/Talk M-F 1p-2p", [5, 1], 102.0),
        ("SFO", "Hindi Entertainment Sat-Sun 1p-4p", [1, 0], 85.0),
        ("SFO", "BNS Hindi ROS", [4, 1], 0.0),
        ("CVC", "Hindi News/Talk M-F 1p-2p", [4, 1], 43.0),
        ("CVC", "Hindi Entertainment Sat-Sun 1p-4p", [1, 0], 34.0),
        ("CVC", "BNS Hindi ROS", [4, 1], 0.0),
    ]
    # the sheet splits digits in the NET column ('$ 7 14.00')
    assert [ln.net_total for ln in order.paid_lines] == [714.0, 340.0, 612.0, 85.0, 215.0, 34.0]
    assert [(s.paid_units, s.bonus_units, s.paid_net) for s in order.sections] == [
        (11, 6, 1054.0),
        (7, 5, 697.0),
        (6, 5, 249.0),
    ]
    assert (order.total_spots, order.total_paid_units, order.total_bonus_units) == (40, 24, 16)
    assert order.total_cost == 2000.0
    # bonus rides the language's ROS window; paid days normalise ('Sat-Sun' → 'Sa-Su')
    bns, ent = order.lines[2], order.lines[1]
    assert (bns.days, bns.time, bns.is_bonus) == ("M-Su", "1p-4p", True)
    assert (ent.days, ent.time, ent.language, ent.program) == (
        "Sa-Su",
        "1p-4p",
        "Hindi",
        "Entertainment",
    )
    assert all(ln.duration == "30" for ln in order.lines)


def test_io_beside_the_proposal_supplies_campaign_and_flight(order):
    assert order.io is not None
    assert order.io.campaign == "SOS Vote Education Campaign 3"
    assert order.io.io_date == "10/24/2026"
    assert (order.io.flight_start, order.io.flight_end) == (date(2026, 10, 26), date(2026, 11, 2))
    assert order.io.length_sec == 30 and order.io.language == "Hindi"
    assert order.io.market_counts == {"LAX": (11, 6), "SFO": (7, 5), "CVC": (6, 5)}
    assert order.io.total == 2000.0
    assert order.campaign_name == "SOS Vote Education Campaign 3"
    assert (order.flight_start, order.flight_end) == ("10/26/2026", "11/02/2026")
    assert Path(order.companion_path) == IO
    assert order.description == (
        "California Black Media - SOS Vote Education Campaign 3 10/26/2026-11/02/2026"
    )


def test_dropping_the_io_parses_the_proposal_beside_it(real):
    o = cp.parse_cbm(str(IO), today=TODAY)
    assert Path(o.source_path) == IO and Path(o.companion_path) == PROPOSAL
    assert o.total_spots == 40 and o.io is not None and len(o.lines) == 9


def test_proposal_alone_defaults_from_the_sheet(real, tmp_path):
    alone = tmp_path / "Crossings TV ACoM_ SOS 2026.pdf"
    shutil.copy(PROPOSAL, alone)
    o = cp.parse_cbm(str(alone), today=TODAY)
    assert o.io is None and o.companion_path == ""
    assert o.campaign_name == "SOS"
    assert (o.flight_start, o.flight_end) == ("10/26/2026", "11/08/2026")  # last week's Sunday
    assert o.length_sec == 30 and o.total_spots == 40


def test_io_alone_refuses_with_a_pointer_to_the_proposal(real, tmp_path):
    alone = tmp_path / "SOS_C3_IO_Crossings_TV.docx.pdf"
    shutil.copy(IO, alone)
    with pytest.raises(cp.CBMParseError, match="carries no lines"):
        cp.parse_cbm(str(alone), today=TODAY)


def test_a_disagreeing_io_is_not_attached(real, tmp_path):
    """The proposal beside a DIFFERENT campaign's IO stays an order; the IO is ignored."""
    shutil.copy(PROPOSAL, tmp_path / "proposal.pdf")
    io = cp.parse_cbm_io(str(IO))
    io.market_counts["LAX"] = (10, 7)
    with pytest.raises(cp.CBMParseError, match="per-market counts"):
        cp.reconcile_with_io(cp.parse_cbm(str(tmp_path / "proposal.pdf"), today=TODAY), io)
    io = cp.parse_cbm_io(str(IO))
    io.total = 2100.0
    with pytest.raises(cp.CBMParseError, match="IO total"):
        cp.reconcile_with_io(cp.parse_cbm(str(PROPOSAL), today=TODAY), io)


def test_detection():
    assert cp.is_cbm_proposal_text(
        "2026 Crossings TV Media Proposal\nAgency: AMERICAN COMMUNITY MEDIA: SOS\nContact: DANA LEVINE"
    )
    assert not cp.is_cbm_proposal_text(
        "2026 Crossings TV Media Proposal\nAgency: AMERICAN COMMUNITY MEDIA\nContact: DANA LEVINE"
    )  # an ACM order on the same template is NOT CBM
    assert cp.is_cbm_io_text("Advertising Insertion Order ... Signature (CBM Representative)")
    assert not cp.is_cbm_io_text("Advertising Insertion Order ... Signature (Media Representative)")

    from business_logic.services.order_detection_service import (
        OrderDetectionService,
        detect_from_filename,
    )
    from domain.enums import OrderType

    svc = OrderDetectionService()
    assert (
        svc.detect_from_text("Crossings TV Media Proposal Agency: AMERICAN COMMUNITY MEDIA: SOS")
        == OrderType.CBM
    )
    assert (
        svc.detect_from_text(
            "Advertising Insertion Order Insertion Order: X Signature (CBM Representative)"
        )
        == OrderType.CBM
    )
    assert detect_from_filename("SOS_C3_IO_Crossings_TV.docx.pdf") == OrderType.CBM
    assert detect_from_filename("Crossings TV ACoM_ SOS 2026.pdf") == OrderType.CBM
    assert detect_from_filename("Crossings TV Proposal_ HPSJ_DSNP.pdf") == OrderType.HPSJ


def test_io_text_parser_guards():
    with pytest.raises(cp.CBMParseError, match="not a California Black Media"):
        cp.parse_cbm_io_text("Advertising Insertion Order Signature (Media Representative)")
    base = (
        "Advertising Insertion Order Date: 10/24/2026 Insertion Order: SOS Vote Education Campaign 3 "
        "Parent Company Name (if applicable): Crossings TV Run Date: 10/26/26-11/2/26 AAPI TV — "
        "40 × :30 Hindi TV spots (24 paid + 16 bonus): $2,000.00 Los Angeles 17 (11+6) "
        "San Francisco 12 (7+5) Central Valley 11 (6+5). TOTAL: $2,000.00 "
        "ADDED VALUE: 16 bonus Signature (CBM Representative)"
    )
    io = cp.parse_cbm_io_text(base)
    assert io.market_counts == {"LAX": (11, 6), "SFO": (7, 5), "CVC": (6, 5)} and io.total == 2000.0
    with pytest.raises(cp.CBMParseError, match="≠ 11\\+7"):
        cp.parse_cbm_io_text(base.replace("17 (11+6)", "17 (11+7)"))
    with pytest.raises(cp.CBMParseError, match="TOTAL"):
        cp.parse_cbm_io_text(base.replace("TOTAL: $2,000.00", "TOTAL: TBD"))


def test_automation_defaults(order):
    from browser_automation.cbm_automation import _line_plan, default_code_desc, fit_campaign

    assert default_code_desc(date(2026, 10, 26), "SOS Vote Education Campaign 3") == (
        "CBM 2611",
        "CA Black Media - SOS Vote Education C3 2611",  # 51 chars does not fit the header's 50
    )
    assert default_code_desc(date(2026, 10, 26), "SOS") == ("CBM 2611", "CA Black Media - SOS 2611")
    assert len(fit_campaign("CA Black Media", "SOS " * 30, "2611")) <= 50
    assert fit_campaign("CA Black Media", "SOS " * 30, "2611").endswith(" 2611")

    # 10/26 start, IO end 11/2: the Monday-only second week gets its own line and cap
    plan = _line_plan(order, date(2026, 10, 26), date(2026, 11, 2))
    ranges = [
        (
            ln.market,
            d,
            r["date_from"],
            r["date_to"],
            r["spots_per_week"],
            r["weeks"],
            r["max_daily"],
        )
        for ln, _days, _t, d, rs, _n in plan
        for r in rs
    ]
    assert len(ranges) == 15
    assert sum(r[4] * r[5] for r in ranges) == 40
    assert ranges[0] == (
        "LAX",
        "Hindi News/Talk M-F 1p-2p",
        date(2026, 10, 26),
        date(2026, 11, 1),
        5,
        1,
        1,
    )
    assert ranges[1] == (
        "LAX",
        "Hindi News/Talk M-F 1p-2p",
        date(2026, 11, 2),
        date(2026, 11, 2),
        2,
        1,
        2,
    )
    assert ranges[2] == (
        "LAX",
        "Hindi Entertainment Sat-Sun 1p-4p",
        date(2026, 10, 26),
        date(2026, 11, 1),
        4,
        1,
        2,
    )
    assert ranges[3] == ("LAX", "BNS Hindi ROS", date(2026, 10, 26), date(2026, 11, 1), 5, 1, 1)
    assert all(not n for *_, n in plan)
    assert [(days, t) for _l, days, t, *_ in plan][:3] == [
        ("M-F", "13:00-14:00"),
        ("Sa-Su", "13:00-16:00"),
        ("M-Su", "13:00-16:00"),
    ]


# ─── tampering: every guard must refuse ──────────────────────────────────────


def _tampered(real, mutate):
    """Parse the proposal (alone, so the IO cannot mask a grid defect) with one
    table cell mutated by `mutate(tables)`."""
    import pdfplumber as real_mod

    class _Page:
        def __init__(self, pg):
            self._pg = pg

        def extract_text(self):
            return self._pg.extract_text()

        def extract_tables(self):
            tables = copy.deepcopy(self._pg.extract_tables())
            mutate(tables)
            return tables

    class _PDF:
        def __init__(self, path):
            self._pdf = real_mod.open(path)
            self.pages = [_Page(p) for p in self._pdf.pages]

        def __enter__(self):
            return self

        def __exit__(self, *a):
            self._pdf.close()

    class _Shim:
        open = staticmethod(_PDF)

    cp.pdfplumber = _Shim
    try:
        return cp.parse_cbm_proposal(str(PROPOSAL), today=TODAY)
    finally:
        cp.pdfplumber = real


def _grid(tables, n=0):
    k = 0
    for t in tables:
        if any(c and "Language Block" in c for r in t for c in r):
            if k == n:
                return t
            k += 1
    raise AssertionError("grid not found")


def _summary(tables):
    for t in tables:
        if any(c and "Paid+Bonus" in c for r in t for c in r):
            return t
    raise AssertionError("summary not found")


def test_noop_mutation_still_parses(real):
    assert _tampered(real, lambda tables: None).total_spots == 40


def test_dropped_week_cell_refuses(real):
    def mutate(tables):
        _grid(tables)[2][3] = ""  # LAX News/Talk 26-Oct: 5 → blank

    with pytest.raises(cp.CBMParseError, match="week cells sum"):
        _tampered(real, mutate)


def test_blanked_rate_refuses(real):
    def mutate(tables):
        _grid(tables)[2][2] = ""

    with pytest.raises(cp.CBMParseError, match="unreadable units/rate/net"):
        _tampered(real, mutate)


def test_wrong_line_net_refuses(real):
    def mutate(tables):
        _grid(tables)[2][6] = "$ 7 24.00"

    with pytest.raises(cp.CBMParseError, match="≠ NET"):
        _tampered(real, mutate)


def test_paid_footer_mismatch_refuses(real):
    def mutate(tables):
        _grid(tables, 1)[5][5] = "8"  # SFO Paid units 7 → 8

    with pytest.raises(cp.CBMParseError, match="Paid units"):
        _tampered(real, mutate)


def test_summary_mismatch_refuses(real):
    def mutate(tables):
        _summary(tables)[3][2] = "6"  # San Francisco bonus units 5 → 6

    with pytest.raises(cp.CBMParseError, match="summary SFO"):
        _tampered(real, mutate)


def test_total_airtime_mismatch_refuses(real):
    def mutate(tables):
        _summary(tables)[5][4] = "$ 2,100.00"

    with pytest.raises(cp.CBMParseError, match="Total Airtime"):
        _tampered(real, mutate)


def test_renamed_header_refuses(real):
    def mutate(tables):
        g = _grid(tables)
        g[1] = [("Units" if c and "Total Unit" in c else c) for c in g[1]]

    with pytest.raises(cp.CBMParseError, match="missing"):
        _tampered(real, mutate)


def test_unknown_language_refuses(real):
    def mutate(tables):
        _grid(tables)[2][0] = "General News/Talk"

    with pytest.raises(cp.CBMParseError, match="no language word"):
        _tampered(real, mutate)


def test_paid_row_with_no_rate_refuses(real):
    def mutate(tables):
        _grid(tables)[2][2] = "$ -"
        _grid(tables)[2][6] = "$ -"

    with pytest.raises(cp.CBMParseError, match="paid row with no rate"):
        _tampered(real, mutate)


def test_unknown_market_banner_refuses(real):
    def mutate(tables):
        _grid(tables)[0][0] = "California-Fresno Xfinity 1"

    with pytest.raises(cp.CBMParseError, match="market not recognised"):
        _tampered(real, mutate)
