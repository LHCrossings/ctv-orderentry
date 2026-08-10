"""Crispin official IO (Brand Time Schedule PDF) — column regimes, per-line
flights, the production charge, and the reconciliation guards.

We trained Crispin on the Excel *proposal*. The official IO is a Brand Time
Schedule, and it carries four traps that a text-flow reader gets silently wrong:

* the flight is split across **column regimes** of ~13 weeks, so every line is
  printed twice and must be merged by LINE#;
* page 3 carries **two regimes at once** (regime-1 summary, a `-----` divider,
  then regime-2's header mid-page), so columns are per-REGION not per-page;
* **zero cells are not printed** — a "3" only means SEP14 because of where it sits;
* flight dates are **per line** (M-F lines end 10/30, M-Su lines run to 11/01),
  and one grid row is not airtime at all (TRANSLATION COST → CONTRATTISPESE).

The fixture is the real revision-2 IO for BAAQMD 2026 (order 212735): 324 units
and $23,529.94, both printed on the document, so it is its own oracle.
"""

import sys
import types
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

_root = Path(__file__).parent.parent.parent
for _p in (_root, _root / "browser_automation"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from browser_automation.parsers import crispin_parser as cp  # noqa: E402
from browser_automation.parsers.crispin_parser import (  # noqa: E402
    parse_crispin,
    parse_crispin_pdf,
)

FIXTURE = str(_root / "tests" / "fixtures" / "crispin" / "crispin_io_212735.pdf")

# What the IO itself prints in its own totals — the reconciliation oracle.
IO_TOTAL_UNITS = 324
IO_TOTAL_DOLLARS = Decimal("23529.94")


@pytest.fixture(scope="module", autouse=True)
def _use_real_pdfplumber(real_pdfplumber):
    """Every test here parses the real fixture, so take the shared escape hatch
    from the suite-wide pdfplumber MagicMock (see tests/conftest.py). The parser
    imports pdfplumber inside the function, so a sys.modules swap is enough."""
    return real_pdfplumber


@pytest.fixture(scope="module")
def order():
    return parse_crispin_pdf(FIXTURE)


# ── Header ───────────────────────────────────────────────────────────────────

def test_header_fields(order):
    assert order.source_format == "pdf"
    assert order.order_number == "212735"
    assert order.estimate == "0001"
    assert order.revision == "2"
    assert order.client_code == "BAAQ"
    assert order.station_code == "3131CA"
    assert "SUMMERCAMPAIGN" in order.estimate_detail


def test_agency_is_a_single_token_not_the_page_title(order):
    """'CRISPIN AGENCY' shares an extracted text line with 'Brand Time Schedule
    - 3131CA', so a greedy match swallows the title."""
    assert order.agency == "Crispin LLC"


def test_market_comes_from_the_market_field_not_the_station_name(order):
    """The station record reads 'Optimum/College Station-Bryan,(CAB)' — the
    agency's own mislabel. Only `Market SF … SAN FRANCISCO-OAK-SAN JOSE` is real."""
    assert order.market_code == "SFO"


# ── Column regimes ───────────────────────────────────────────────────────────

def test_lines_are_merged_across_both_column_regimes(order):
    """Each line prints once per regime; the merge must union the week columns."""
    by_no = {ln.line_number: ln for ln in order.lines}
    v = by_no["001"]
    assert len(v.week_dates) == 12                      # 3 in regime 1 + 9 in regime 2
    assert v.week_dates[0] == date(2026, 8, 10)
    assert v.week_dates[-1] == date(2026, 10, 26)
    assert v.total_spots == 40 == v.total_spots_stated


def test_week_columns_are_seven_days_apart(order):
    for ln in order.lines:
        for a, b in zip(ln.week_dates, ln.week_dates[1:]):
            assert (b - a).days == 7, f"line {ln.line_number}: {a} → {b}"


def test_zero_cells_are_read_positionally_not_by_text_order(order):
    """Line 006 steps 4/wk → 3/wk one week LATER than line 007. A text-flow read
    (which sees only the printed non-zero digits) cannot tell them apart."""
    by_no = {ln.line_number: ln for ln in order.lines}
    assert by_no["006"].week_spots == [4, 4, 4, 4, 4, 3, 3, 3, 3, 3, 3, 3]
    assert by_no["007"].week_spots == [4, 4, 4, 4, 3, 3, 3, 3, 3, 3, 3, 3]


# ── Per-line flight dates ────────────────────────────────────────────────────

def test_flight_dates_are_per_line(order):
    """M-F lines end Friday 10/30; M-Su lines run to Sunday 11/01. An order-level
    end date would stretch the M-F lines by two days."""
    by_no = {ln.line_number: ln for ln in order.lines}
    for no in ("001", "006", "007"):
        assert by_no[no].date_to == date(2026, 11, 1), no
    for no in ("008", "009"):
        assert by_no[no].date_to == date(2026, 10, 30), no
    assert all(ln.date_from == date(2026, 8, 10) for ln in order.lines)


def test_order_flight_spans_the_line_extremes(order):
    assert order.flight_start == "08/10/2026"
    assert order.flight_end == "11/01/2026"


# ── Airtime vs charges ───────────────────────────────────────────────────────

def test_translation_cost_is_a_charge_not_an_airtime_line(order):
    assert [ln.line_number for ln in order.lines] == \
        ["001", "002", "003", "004", "006", "007", "008", "009"]
    assert len(order.charges) == 1
    ch = order.charges[0]
    assert ch.line_number == "005"
    assert "TRANSLATION" in ch.description.upper()
    assert ch.amount == pytest.approx(2447.06)


def test_bonus_lines_are_ros_fifteens(order):
    bonus = order.bonus_lines
    assert len(bonus) == 4
    assert {ln.length_sec for ln in bonus} == {15}
    assert {ln.daypart for ln in bonus} == {"ROS"}
    assert {ln.base_language for ln in bonus} == \
        {"Vietnamese", "Filipino", "Cantonese", "Mandarin"}


def test_paid_dayparts_keep_their_own_windows(order):
    dayparts = {ln.base_language: ln.daypart for ln in order.paid_lines}
    assert dayparts == {
        "Vietnamese": "M-Su 11a-1p",
        "Mandarin": "M-Su 8-9p",
        "Filipino": "M-F 6-7p",
        "Cantonese": "M-F 7-8p",
    }


def test_io_rates_are_gross_so_no_gross_up_is_owed(order):
    """The IO quotes net ÷ 0.85 ($120 → 141.18). The ANAGRAF agency commission
    nets it back down, so nothing downstream may gross it up again."""
    assert order.rates_are_net is False
    rates = sorted({ln.rate for ln in order.paid_lines})
    assert rates == [117.65, 141.18]


# ── Reconciliation against the IO's own arithmetic ───────────────────────────

def test_totals_reconcile_with_the_documents_own_grand_total(order):
    units = sum(ln.total_spots for ln in order.lines) + len(order.charges)
    assert units == IO_TOTAL_UNITS

    money = sum(Decimal(str(ln.rate)) * ln.total_spots for ln in order.lines)
    money += sum(Decimal(str(c.amount)) for c in order.charges)
    assert money == IO_TOTAL_DOLLARS


# ── The guards must REFUSE, not degrade ──────────────────────────────────────

def _parse_with_mutated_words(mutate):
    """Re-parse the fixture with extract_words() output rewritten.

    Cheaper and far more legible than authoring a broken PDF, and it targets
    exactly the layer that fails in the wild: the coordinates.
    """
    import pdfplumber as real

    class _Page:
        def __init__(self, p):
            self._p = p

        def extract_words(self, *a, **k):
            return mutate([dict(w) for w in self._p.extract_words(*a, **k)])

        def extract_text(self, *a, **k):
            return self._p.extract_text(*a, **k)

    class _Pdf:
        def __init__(self, p):
            self._p = p
            self.pages = [_Page(x) for x in p.pages]

        def __enter__(self):
            return self

        def __exit__(self, *a):
            self._p.close()

    shim = types.ModuleType("pdfplumber")
    shim.open = lambda path: _Pdf(real.open(path))
    saved = sys.modules.get("pdfplumber")
    sys.modules["pdfplumber"] = shim
    try:
        return parse_crispin_pdf(FIXTURE)
    finally:
        if saved is not None:
            sys.modules["pdfplumber"] = saved
        else:
            del sys.modules["pdfplumber"]


def test_the_shim_itself_parses_the_untouched_fixture():
    """Guard the guards: a no-op mutation must still parse, or the negative
    tests below would pass for the wrong reason."""
    o = _parse_with_mutated_words(lambda ws: ws)
    assert sum(ln.total_spots for ln in o.lines) == 323


def _first_match(is_target, apply):
    """A mutator that edits the FIRST matching word in the document.

    The mutator is handed each page's words in turn, so it must be stateful:
    "target not found" is only true after every page has been offered. Returns
    (mutator, hits) — assert `hits` after the parse so a fixture change surfaces
    as "target not found" rather than as a guard that silently never fired.
    """
    hits = []

    def mutate(ws):
        if hits:
            return ws
        for i, w in enumerate(ws):
            if is_target(w):
                hits.append(w["text"])
                return apply(ws, i)
        return ws

    return mutate, hits


def test_a_dropped_week_cell_is_refused():
    mutate, hits = _first_match(
        lambda w: w["text"] == "3" and 320 < w["x0"] < 335 and 325 < w["top"] < 340,
        lambda ws, i: ws[:i] + ws[i + 1:],
    )
    with pytest.raises(ValueError, match=r"week columns sum to 37 but .* TOT says 40"):
        _parse_with_mutated_words(mutate)
    assert hits, "fixture changed — target cell not found"


def test_a_cell_in_the_wrong_column_is_refused():
    def slide(ws, i):
        ws[i]["x0"] += 19.3
        ws[i]["x1"] += 19.3
        return ws

    mutate, hits = _first_match(
        lambda w: w["text"] == "4" and 305 < w["x0"] < 315 and 290 < w["top"] < 305,
        slide,
    )
    with pytest.raises(ValueError, match="week columns sum to"):
        _parse_with_mutated_words(mutate)
    assert hits, "fixture changed — target cell not found"


def test_a_renamed_column_fails_loudly_instead_of_guessing_by_position():
    def rename(ws):
        for w in ws:
            if w["text"] == "DATES":
                w["text"] = "FLIGHT"
        return ws

    with pytest.raises(ValueError, match=r"missing column\(s\) \['DATES'\]"):
        _parse_with_mutated_words(rename)


def test_an_unreadable_rate_is_refused_never_treated_as_zero():
    """A zero rate is a legitimate value (bonus), so it can never double as
    'couldn't read it' — the DART $0 lesson."""
    def blank(ws):
        return [w for w in ws if w["text"] != "117.65"]

    with pytest.raises(ValueError, match="could not read the TOT/COST columns"):
        _parse_with_mutated_words(blank)


def test_an_unclassifiable_grid_row_is_refused():
    """A row naming neither a language nor a production cost could be airtime or
    a charge; entering it either way would be wrong."""
    def rename_program(ws):
        for w in ws:
            if w["text"] == "TRANSLATION":
                w["text"] = "Widgets"
            elif w["text"] == "COST" and w["x0"] > 200:
                w["text"] = "Sponsorship"
        return ws

    with pytest.raises(ValueError, match="names no language"):
        _parse_with_mutated_words(rename_program)


# ── Dispatcher ───────────────────────────────────────────────────────────────

def test_dispatcher_routes_pdf_to_the_io_reader():
    assert parse_crispin(FIXTURE).source_format == "pdf"


def test_dispatcher_routes_workbooks_to_the_proposal_reader(monkeypatch):
    seen = {}
    monkeypatch.setattr(cp, "parse_crispin_xlsx",
                        lambda p: seen.setdefault("path", p))
    for ext in (".xlsx", ".xlsm"):
        seen.clear()
        cp.parse_crispin(f"/tmp/proposal{ext}")
        assert seen["path"] == f"/tmp/proposal{ext}"
