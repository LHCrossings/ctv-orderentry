"""CA Alliance of Family Owned Businesses PAC house proposal — real PDF fixture + tampering.

The fixture is the actual 10/5-11/3 2026 proposal (hand-entered by Maija as contract
3155 after the AI fallback misread it). It carries its own TOTAL row, so every guard is
exercised by mutating the table cells pdfplumber hands the parser.
"""

from __future__ import annotations

import copy
import sys
from datetime import date
from pathlib import Path

import pytest

_root = Path(__file__).resolve().parents[2]
for _p in [str(_root), str(_root / "src")]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

from browser_automation.parsers import cafob_parser as cp  # noqa: E402

FIXTURE = _root / "tests" / "fixtures" / "cafob" / "cafob_pac_2610.pdf"
TODAY = date(2026, 10, 6)


@pytest.fixture(scope="module")
def real(real_pdfplumber):
    cp.pdfplumber = real_pdfplumber
    try:
        yield real_pdfplumber
    finally:
        cp.pdfplumber = sys.modules["pdfplumber"]


@pytest.fixture(scope="module")
def order(real):
    return cp.parse_cafob(str(FIXTURE), today=TODAY)


def test_header(order):
    assert order.client == "California Alliance of Family Owned Businesses PAC"
    assert order.agency_label == "National Media"
    assert order.contact == "Mike Adam" and order.email == "mikea@natmedia.com"
    assert order.billing_cycle == "Broadcast"
    assert order.market == "SFO"  # 'SF BAY AREA'
    assert order.flight_start == "10/05/2026" and order.flight_end == "11/03/2026"
    assert order.length_sec == 30 and order.separation_minutes == 30
    assert order.rates_are_net is False
    assert order.notes == "30 minute separation"


def test_grid(order):
    assert order.week_dates == [date(2026, 10, d) for d in (5, 12, 19, 26)] + [date(2026, 11, 2)]
    assert [ln.description for ln in order.lines] == [
        "M-F Vietnamese News 11A-11:30A",
        "M-F Vietnamese Talk/Variety 11:30A-12P",
        "M-F Vietnamese Drama 12P-1P",
    ]
    assert [ln.weekly_spots for ln in order.lines] == [
        [3, 5, 5, 5, 2],
        [2, 3, 3, 3, 2],
        [2, 2, 2, 2, 0],
    ]
    assert [ln.total_spots for ln in order.lines] == [20, 13, 8]
    assert all(ln.gross_rate == 120.0 and not ln.is_bonus for ln in order.lines)
    assert (order.total_spots, order.total_cost) == (41, 4920.0)


def test_detection_is_client_keyed():
    assert cp.is_cafob_text(
        "Client National Media: California Alliance of Family Owned Businesses PAC"
    )
    assert not cp.is_cafob_text("Client National Media: Some Other Advertiser")


def test_year_inference_prefers_a_monday_near_today():
    assert cp.infer_year(10, 5, date(2026, 10, 6)) == 2026  # 10/5/2026 is a Monday
    assert cp.infer_year(10, 5, date(2026, 1, 15)) == 2026
    assert cp.infer_year(1, 4, date(2026, 12, 20)) == 2027  # 1/4/2027 is a Monday


def test_automation_defaults(order):
    from browser_automation.cafob_automation import (
        _line_plan,
        default_code_desc,
        line_description,
        separation_for,
    )

    assert default_code_desc(date(2026, 10, 7)) == (
        "Matson CAFOB 2610",
        "CA Alliance of Family Owned Businesses PAC 2610",
    )
    assert separation_for(30) == (25, 0, 0) and separation_for(15) == (15, 0, 0)
    assert separation_for(None) == (25, 0, 0)
    assert line_description(order.lines[0], "11:00", "11:30") == "M-F Viet News 11a-11:30a"
    # Maija's 10/7 start: short first week split off, equal weeks consolidated, 41/41
    plan = _line_plan(order, date(2026, 10, 7))
    ranges = [
        (d, r["date_from"], r["date_to"], r["spots_per_week"], r["weeks"], r["max_daily"])
        for _l, _d, _t, d, rs, _n in plan
        for r in rs
    ]
    assert len(ranges) == 7
    assert sum(r[3] * r[4] for r in ranges) == 41
    assert ranges[0] == ("M-F Viet News 11a-11:30a", date(2026, 10, 7), date(2026, 10, 11), 3, 1, 1)
    assert ranges[-1] == ("M-F Viet Drama 12p-1p", date(2026, 10, 7), date(2026, 11, 1), 2, 4, 1)
    assert all(not n for *_, n in plan)


# ─── tampering: every guard must refuse ──────────────────────────────────────


def _tampered(real, mutate):
    """Parse the fixture with one table cell mutated by `mutate(table)`."""
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
        return cp.parse_cafob(str(FIXTURE), today=TODAY)
    finally:
        cp.pdfplumber = real


def _grid(tables):
    for t in tables:
        for row in t:
            if any(c and "Language Block" in c for c in row):
                return t
    raise AssertionError("grid not found")


def test_noop_mutation_still_parses(real):
    o = _tampered(real, lambda tables: None)
    assert o.total_spots == 41


def test_dropped_week_cell_refuses(real):
    def mutate(tables):
        _grid(tables)[2][3] = ""  # VIETNAMESE NEWS, 5-Oct: 3 → blank

    with pytest.raises(cp.CAFOBParseError, match="week cells sum"):
        _tampered(real, mutate)


def test_blanked_rate_refuses(real):
    def mutate(tables):
        _grid(tables)[2][2] = ""

    with pytest.raises(cp.CAFOBParseError, match="unreadable units/rate/gross"):
        _tampered(real, mutate)


def test_wrong_line_gross_refuses(real):
    def mutate(tables):
        _grid(tables)[2][9] = "$ 2,500.00"

    with pytest.raises(cp.CAFOBParseError, match="≠ GROSS"):
        _tampered(real, mutate)


def test_total_row_mismatch_refuses(real):
    def mutate(tables):
        _grid(tables)[5][8] = "40"  # TOTAL units

    with pytest.raises(cp.CAFOBParseError, match="TOTAL units"):
        _tampered(real, mutate)


def test_renamed_header_refuses(real):
    def mutate(tables):
        g = _grid(tables)
        g[1] = [("Units" if c and "Total Unit" in c else c) for c in g[1]]

    with pytest.raises(cp.CAFOBParseError, match="not found|missing"):
        _tampered(real, mutate)


def test_unknown_language_refuses(real):
    def mutate(tables):
        _grid(tables)[2][0] = "GENERAL NEWS"

    with pytest.raises(cp.CAFOBParseError, match="no language word"):
        _tampered(real, mutate)
