"""
opAD IO parser on the real NYSDOH 3180 order (2026-10-15).

Two defects that run surfaced: the client regex ran to the end of the line
("NYS Department of Health Estimate: 3180"), and the day pattern was a list of
the patterns seen so far, so the week-of-12/14 rows that say "M-Tu" entered as
Monday only. The IO carries its own totals (175 spots, $5,625), so it is its own
oracle.
"""

import sys
from pathlib import Path

import pytest

_root = Path(__file__).parent.parent.parent
for p in [str(_root), str(_root / "src"), str(_root / "browser_automation")]:
    if p not in sys.path:
        sys.path.insert(0, p)

import browser_automation.parsers.opad_parser as opad_parser  # noqa: E402

PDF = str(_root / "tests" / "fixtures" / "opad" / "nysdoh_nysoh_3180.pdf")


@pytest.fixture(scope="module")
def order(real_pdfplumber):
    # The parser binds `pdfplumber` at import, before the suite-wide mock is
    # swapped out — point it at the real library for this module, then restore.
    mocked = opad_parser.pdfplumber
    opad_parser.pdfplumber = real_pdfplumber
    try:
        yield opad_parser.parse_opad_pdf(PDF)
    finally:
        opad_parser.pdfplumber = mocked


def test_header(order):
    assert order.client == "NYS Department of Health"  # not "... Estimate: 3180"
    assert order.estimate_number == "3180"
    assert order.description == "NYSOH Open Enrollment 4q26 TV"
    assert order.product == "OEP/ARPA"
    assert order.market == "New York"
    assert (order.flight_start, order.flight_end) == ("11/09/2026", "12/20/2026")
    assert len(order.week_start_dates) == 6


def test_totals_foot(order):
    assert len(order.lines) == 16
    assert sum(ln.total_spots for ln in order.lines) == 175
    assert sum(ln.total_cost for ln in order.lines) == 5625.0
    # the IO's own per-week footer: 27 27 34 38 42 7
    cols = [sum(ln.weekly_spots[i] for ln in order.lines) for i in range(6)]
    assert cols == [27, 27, 34, 38, 42, 7]


def test_m_tu_rows_keep_tuesday(order):
    tail = [(ln.days, ln.time, ln.duration, ln.total_spots) for ln in order.lines[-6:]]
    assert tail == [
        ("M-Tu", "6:00a-7:00a", 15, 1),
        ("M-Tu", "7:00p-8:00p", 30, 1),
        ("M-Tu", "8:00p-10:00p", 15, 2),
        ("M-Tu", "8:00p-10:00p", 30, 1),
        ("M", "11:30p-12:00a", 30, 1),
        ("M", "7:00p-12:00a", 15, 1),
    ]
    assert [ln.days for ln in order.lines[:10]] == [
        "M-Sa", "M-Sa", "M-F", "M-F", "M-Su", "M-Su", "M-F", "M-F", "M-Su", "M-Su"
    ]


def test_bonus_rows(order):
    assert [ln.is_bonus() for ln in order.lines].count(True) == 3
    assert all(ln.program == "GOODWILL" for ln in order.lines if ln.is_bonus())


def test_detection_client_stops_at_estimate():
    from src.business_logic.services.order_detection_service import OrderDetectionService

    svc = OrderDetectionService()
    text = "Client: NYS Department of Health Estimate: 3180\nMedia: TV"
    assert svc._extract_opad_client(text) == "NYS Department of Health"
    assert svc._extract_opad_client("Client: Acme Co\nMedia: TV") == "Acme Co"


def test_precedent_naming_defaults(monkeypatch):
    """The customer's own prior contracts shape the defaults: short name from the
    latest code, description prefix shared by most prior descriptions."""
    import browser_automation.opad_automation as oa

    rows = [
        (44, "NY State Department of Health", "opAD NYDOH 2824", "NYDOH OE 1Q26 Est 2824"),
        (22, "NYC Dept of Health and Mental Hygiene", "opAD NYDHMH 2881", "NYC DOHMH Est 2881"),
        (44, "NY State Department of Health", "opAD NYDOH 2823", "NYS Dept of Health Est 2823"),
        (44, "NY State Department of Health", "opAD NYSOH 2321", "NYS Dept of Health Est 2321"),
        (44, "NY State Department of Health", "opAD NYDOH 2319", "NYS Dept of Health Est 2319 Stay Connected"),
    ]

    class _Cur:
        def execute(self, *_a):
            pass

        def fetchall(self):
            return rows

    class _Conn:
        def cursor(self):
            return _Cur()

        def close(self):
            pass

    import browser_automation.etere_direct_client as edc

    monkeypatch.setattr(edc, "connect", lambda: _Conn())
    prec = oa._precedent(client="NYS Department of Health")
    assert prec["customer_id"] == 44
    assert prec["short"] == "NYDOH"
    assert prec["desc_prefix"] == "NYS Dept of Health"
    assert prec["recent"][0] == ("opAD NYDOH 2824", "NYDOH OE 1Q26 Est 2824")
    assert oa._precedent(customer_id=22)["short"] == "NYDHMH"
    assert oa._precedent(client="Totally Unrelated Advertiser") is None


def test_contract_note_is_the_io_description_line():
    """Lee 10/15: CONTRATTITESTATA.NOTE carries the IO's Description line
    ("NYSOH Open Enrollment 4q26 TV"), never the typed contract description
    with its estimate number (2824 had that; 2823, 2321, 2320 and 3178 did not)."""
    src = (_root / "browser_automation" / "opad_automation.py").read_text()
    assert "note=order.description," in src
    assert "note=description," not in src
