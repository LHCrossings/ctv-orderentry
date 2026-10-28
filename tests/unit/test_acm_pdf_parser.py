"""ACM reads Charmaine's ACoM proposal PDF for any non-SOS client (Asian Law Caucus,
2026-10-28, held pending approval). Real fixture; the sheet carries its own totals
(Paid row, per-language summary, Total Airtime, Production, Total Contract)."""

import copy
import sys
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from browser_automation import acm_automation as aa  # noqa: E402
from browser_automation.parsers import cbm_parser as cp  # noqa: E402
from browser_automation.parsers.acm_parser import parse_acm  # noqa: E402
from browser_automation.parsers.cbm_parser import CBMParseError  # noqa: E402

PDF = ROOT / "tests" / "fixtures" / "acm" / "acom_asian_law_caucus_2026.pdf"
SOS = ROOT / "tests" / "fixtures" / "cbm" / "sos_c3_proposal_2611.pdf"


@pytest.fixture(scope="module", autouse=True)
def _real(real_pdfplumber):
    # cbm_parser bound `pdfplumber` at import time, under the suite's MagicMock
    cp.pdfplumber = real_pdfplumber
    try:
        yield real_pdfplumber
    finally:
        cp.pdfplumber = sys.modules["pdfplumber"]


@pytest.fixture(scope="module")
def order(_real):
    return parse_acm(str(PDF))


def test_header_and_flight(order):
    assert order.agency == "American Community Media" and order.client_name == "Asian Law Caucus"
    assert (order.flight_start, order.flight_end) == (
        "10/30/2026",
        "11/03/2026",
    )  # a date-RANGE column
    assert order.billing_cycle == "Broadcast" and order.no_separation is True
    assert order.markets == ["CVC"] and order.rates_are_net is True


def test_lines_production_and_totals(order):
    rows = [
        (ln.description, ln.days, ln.time, ln.total_spots, ln.rate, ln.is_bonus)
        for ln in order.lines
    ]
    assert rows == [
        ("Punjabi News M-F 2p-4p", "M-F", "2p-4p", 33, 43.0, False),
        (
            "Filipino News /Drama M-F 4p-7p",
            "M-F",
            "4p-7p",
            40,
            51.0,
            False,
        ),  # sheet spelt it 'Filpino'
        ("Filipino Drama Sat-Sun 4p-6p", "Sa-Su", "4p-6p", 18, 42.5, False),
    ]
    assert [ln.week_dates for ln in order.lines] == [[date(2026, 10, 30)]] * 3
    assert all(ln.week_end == date(2026, 11, 3) for ln in order.lines)
    assert order.production == [("Production services :30s Filipino & Punjabi", 2, 3000.0)]
    assert order.production_total == 3000.0
    assert sum(ln.total_spots * ln.rate for ln in order.lines) == 4224.0


def test_detection_routes_acom_clients_to_acm_and_sos_to_cbm():
    from business_logic.services.order_detection_service import OrderDetectionService
    from domain.enums import OrderType

    svc = OrderDetectionService()
    import pdfplumber

    with pdfplumber.open(str(PDF)) as pdf:
        assert svc.detect_from_text(pdf.pages[0].extract_text()) == OrderType.ACM
    with pdfplumber.open(str(SOS)) as pdf:
        assert svc.detect_from_text(pdf.pages[0].extract_text()) == OrderType.CBM


def test_cbm_gate_still_refuses_a_non_sos_proposal():
    with pytest.raises(CBMParseError, match="SOS"):
        cp.parse_cbm_proposal(str(PDF))


# ─── Lee's cutoff rule: the day before runs its full window, the last day to the cutoff ───


def test_cutoff_splits_a_line_that_runs_past_five():
    rngs = aa.plan_cutoff_ranges(
        date(2026, 10, 30), date(2026, 11, 3), "M-F", "16:00", "19:00", 40, "17:00"
    )
    assert [
        (r["date_from"], r["date_to"], r["time_to"], r["spots"], r["max_daily"]) for r in rngs
    ] == [
        (date(2026, 10, 30), date(2026, 11, 2), "19:00", 27, 14),
        (date(2026, 11, 3), date(2026, 11, 3), "17:00", 13, 13),
    ]
    assert sum(r["spots"] for r in rngs) == 40


def test_cutoff_leaves_lines_that_end_before_it_whole():
    rngs = aa.plan_cutoff_ranges(
        date(2026, 10, 30), date(2026, 11, 3), "M-F", "14:00", "16:00", 33, "17:00"
    )
    assert len(rngs) == 1 and rngs[0]["spots"] == 33 and rngs[0]["max_daily"] == 11  # Fri, Mon, Tue
    weekend = aa.plan_cutoff_ranges(
        date(2026, 10, 30), date(2026, 11, 3), "Sa-Su", "16:00", "18:00", 18, "17:00"
    )
    assert (
        len(weekend) == 1 and weekend[0]["max_daily"] == 9
    )  # 11/3 is a Tuesday: not this line's day


def test_no_cutoff_is_one_range_with_a_cap_that_fits():
    rngs = aa.plan_cutoff_ranges(
        date(2026, 10, 30), date(2026, 11, 3), "M-F", "16:00", "19:00", 40, ""
    )
    assert len(rngs) == 1 and rngs[0]["max_daily"] * 3 >= 40


def test_parse_cutoff_reads_clock_times():
    assert aa.parse_cutoff("5p") == "17:00" and aa.parse_cutoff("17:00") == "17:00"
    assert aa.parse_cutoff("") == "" and aa.parse_cutoff("none") == ""


# ─── tampering: the sheet is its own oracle ───


def _mutated(monkeypatch, fn):
    import pdfplumber

    real_open = pdfplumber.open

    class _Page:
        def __init__(self, pg):
            self._text = pg.extract_text() or ""
            self._tables = [copy.deepcopy(t) for t in (pg.extract_tables() or [])]

        def extract_text(self):
            return self._text

        def extract_tables(self):
            return self._tables

    class _Pdf:
        def __init__(self, path):
            with real_open(path) as pdf:
                self.pages = [_Page(pg) for pg in pdf.pages]
            fn(self.pages)

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(cp.pdfplumber, "open", lambda path: _Pdf(path))


def _cell(pages, needle):
    for pg in pages:
        for t in pg._tables:
            for row in t:
                for i, c in enumerate(row):
                    if c and needle in c:
                        return row, i
    raise AssertionError(needle)


def test_noop_mutation_still_parses(monkeypatch):
    _mutated(monkeypatch, lambda pages: None)
    assert parse_acm(str(PDF)).production_total == 3000.0


def test_changed_production_amount_refuses(monkeypatch):
    def tamper(pages):
        row, i = _cell(pages, "Production services")
        row[-1] = "$ 2,000.00"

    _mutated(monkeypatch, tamper)
    with pytest.raises(CBMParseError, match="Paid NET"):
        parse_acm(str(PDF))


def test_changed_rate_refuses(monkeypatch):
    def tamper(pages):
        row, i = _cell(pages, "Punjabi News")
        row[i + 2] = "$ 44.00"

    _mutated(monkeypatch, tamper)
    with pytest.raises(CBMParseError):
        parse_acm(str(PDF))
