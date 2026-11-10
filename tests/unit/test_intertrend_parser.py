"""Intertrend / CA State Lottery IO — real fixture (order 802, est 0031, contract 3250).

Pins the cover-page Order Comments and the header defaults Lee wrote by hand on 3250
(2026-11-10): CUSTOMERREF 'Est 31, Order 802' and a NOTE carrying the product plus the
per-month Net / Gross amounts Intertrend invoices against.
"""

from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

import pytest

_root = Path(__file__).resolve().parents[2]
for _p in [str(_root), str(_root / "src")]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

from browser_automation.parsers import intertrend_parser as ip  # noqa: E402

FIXTURE = _root / "tests" / "fixtures" / "intertrend" / "order_802_est_0031_hosc.pdf"

# Lee's hand edit on contract 3250, read back from CONTRATTITESTATA.NOTE (11/10).
NOTE_3250 = (
    "HOSC Holiday Scratchers\n"
    "CRTV\n"
    "Flight Period: 11/16/26 thru 12/25/26\n"
    "November: Net $3,240 / Gross $3,811.80.\n"
    "December: Net $3,635 Gross $4,276.47."
)


@pytest.fixture(scope="module")
def real(real_pdfplumber):
    ip.pdfplumber = real_pdfplumber
    try:
        yield real_pdfplumber
    finally:
        ip.pdfplumber = sys.modules["pdfplumber"]


@pytest.fixture(scope="module")
def order(real):
    return ip.parse_intertrend_pdf(str(FIXTURE))


def test_header_fields(order):
    assert order.order_number == "802"
    assert order.estimate == "0031"
    assert (order.client, order.client_code) == ("California State Lottery", "CSL")
    assert (order.product, order.product_code) == ("Holiday Scratchers", "HOSC")
    assert order.market == "SFO"
    assert (order.flight_start, order.flight_end) == ("2026-11-16", "2026-12-25")
    assert order.week_start_dates == ["Nov 16", "Nov 23", "Nov 30", "Dec 7", "Dec 14", "Dec 21"]


def test_lines_foot_to_the_schedule_totals(order):
    assert [ln.line_number for ln in order.lines] == ["1", "2", "3", "4"]
    assert sum(ln.total_spots for ln in order.lines) == 91
    paid = [ln for ln in order.lines if not ln.is_bonus]
    assert sum(ln.net_rate * ln.total_spots for ln in paid) == pytest.approx(6875.00)
    assert [ln.is_bonus for ln in order.lines] == [False, True, False, True]


def test_order_comments_are_the_cover_page_block_verbatim(order):
    assert order.order_comments == [
        "CRTV",
        "Flight Period: From Monday, 11/16/26 thru Friday, 12/25/26",
        "November: Net amount $3,240 / Gross amount $3,811.80.",
        "December: Net amount $3,635 Gross amount $4,276.47.",
        'Creative Name: "A RECIPE FOR PLAY"',
    ]


def test_default_customer_ref_is_est_then_order(order):
    assert ip.default_customer_ref(order) == "Est 31, Order 802"


def test_default_note_is_what_lee_wrote_on_3250(order):
    assert ip.default_contract_note(order) == NOTE_3250


def test_note_without_comments_is_just_the_product():
    o = _bare(order_comments=[])
    assert ip.default_contract_note(o) == "HOSC Holiday Scratchers"


def test_comments_block_stops_at_the_terms():
    text = (
        "Order# 00000802\nClient X\nProduct Y\nEstimate 0031\n"
        "Order Comments :\nCRTV\n\nNovember: Net amount $1 / Gross amount $2.\n"
        "Terms & Conditions:\n- never in the note\n"
    )
    assert ip._extract_order_comments(text) == [
        "CRTV",
        "November: Net amount $1 / Gross amount $2.",
    ]
    assert ip._extract_order_comments("Order# 1\nClient X\nTerms & Conditions:\n") == []


def _bare(**kw):
    base = ip.IntertrendOrder(
        order_number="802",
        client="California State Lottery",
        client_code="CSL",
        product="Holiday Scratchers",
        product_code="HOSC",
        estimate="0031",
        market="SFO",
        flight_start="2026-11-16",
        flight_end="2026-12-25",
        week_start_dates=[],
        lines=[],
    )
    return replace(base, **kw)
