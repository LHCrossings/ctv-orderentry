"""Crispin Brand Time Schedule printed with Mediaocean's NET money marker.

Order 216085 (BAAQMD Winter 2026, Lee 11/6): every rate prints as 'N120.00' = net
$120 per spot ("just one way Mediaocean writes Net 100 per spot"); the IO's own
PTS/DOLS totals are the per-spot GROSS at 15%, rounded to cents (141.18 × 30).
The reader keeps the NET rate, flags rates_are_net, reconciles the sheet against
itself with the same rounding, and the automation grosses up at entry with the
ANAGRAF commission. Two more layout changes ride along: a four-week column regime
(February) and a charge row spelt 'Transaltion Services'.
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

from browser_automation import crispin_automation as ca  # noqa: E402
from browser_automation.parsers import crispin_parser as cp  # noqa: E402
from browser_automation.parsers.crispin_parser import parse_crispin_pdf  # noqa: E402

FIXTURE = str(_root / "tests" / "fixtures" / "crispin" / "crispin_io_216085_net.pdf")
GROSS_FIXTURE = str(_root / "tests" / "fixtures" / "crispin" / "crispin_io_212735.pdf")

IO_TOTAL_UNITS = 159  # 'KQTA TOT 159 23506.40' on page 6 (158 airtime + the 1-unit charge)
IO_TOTAL_DOLLARS = Decimal("23506.40")


@pytest.fixture(scope="module", autouse=True)
def _use_real_pdfplumber(real_pdfplumber):
    return real_pdfplumber


@pytest.fixture(scope="module")
def order():
    return parse_crispin_pdf(FIXTURE)


def test_header_and_flight(order):
    assert (order.order_number, order.estimate, order.station_code) == ("216085", "0002", "KQTA")
    assert order.revision == "ORIGINAL"
    assert order.market_code == "SFO"
    assert (order.flight_start, order.flight_end) == (
        "11/23/2026",
        "02/28/2027",
    )  # crosses New Year


def test_n_money_is_net_per_spot(order):
    assert order.rates_are_net is True
    rates = {ln.line_number: ln.rate for ln in order.lines}
    assert rates == {"001": 120.0, "002": 120.0, "003": 100.0, "004": 100.0}
    assert all(not ln.is_bonus for ln in order.lines)


def test_four_week_february_regime_is_merged_not_folded_into_november(order):
    by = {ln.line_number: ln for ln in order.lines}
    assert by["001"].total_spots == 42 and by["001"].total_spots_stated == 42
    assert by["004"].total_spots == 32 and by["004"].week_spots == [3, 3, 3, 3] + [2] * 10
    assert by["001"].week_dates[0] == date(2026, 11, 23)
    assert by["001"].week_dates[-1] == date(2027, 2, 22)
    assert (by["001"].date_from, by["001"].date_to) == (date(2026, 11, 23), date(2027, 2, 26))
    assert (by["002"].date_from, by["002"].date_to) == (date(2026, 11, 23), date(2027, 2, 28))


def test_misspelt_translation_row_is_a_net_charge(order):
    assert [ln.line_number for ln in order.lines] == ["001", "002", "003", "004"]
    assert len(order.charges) == 1
    ch = order.charges[0]
    assert (ch.line_number, ch.description, ch.amount) == ("005", "Transaltion Services", 2500.0)


def test_totals_reconcile_with_the_documents_own_gross_totals(order):
    units = sum(ln.total_spots for ln in order.lines) + len(order.charges)
    assert units == IO_TOTAL_UNITS
    gross = sum(cp._io_gross(Decimal(str(ln.rate))) * ln.total_spots for ln in order.lines)
    gross += sum(cp._io_gross(Decimal(str(c.amount))) for c in order.charges)
    assert gross == IO_TOTAL_DOLLARS
    assert cp._io_gross(Decimal("120")) == Decimal("141.18")
    assert cp._io_gross(Decimal("2500")) == Decimal("2941.18")


def test_gross_printed_io_is_unchanged():
    o = parse_crispin_pdf(GROSS_FIXTURE)
    assert o.rates_are_net is False
    assert {ln.rate for ln in o.paid_lines} == {117.65, 141.18}


# ── Tampered word streams must refuse ───────────────────────────────────────


def _parse_with_mutated_words(mutate, path=FIXTURE):
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
    shim.open = lambda p: _Pdf(real.open(p))
    saved = sys.modules.get("pdfplumber")
    sys.modules["pdfplumber"] = shim
    try:
        return parse_crispin_pdf(path)
    finally:
        if saved is not None:
            sys.modules["pdfplumber"] = saved
        else:
            del sys.modules["pdfplumber"]


def _edit_all(text_is, new_text):
    """Rewrite EVERY word equal to ``text_is`` — a line prints once per column
    regime, so editing one copy only trips the regime-merge guard."""
    hits = []

    def mutate(ws):
        for w in ws:
            if w["text"] == text_is:
                hits.append(w["text"])
                w["text"] = new_text
        return ws

    return mutate, hits


def test_the_shim_parses_the_untouched_fixture():
    o = _parse_with_mutated_words(lambda ws: ws)
    assert sum(ln.total_spots for ln in o.lines) == 158


def test_a_mix_of_net_and_gross_money_is_refused():
    mutate, hits = _edit_all("N100.00", "117.65")
    with pytest.raises(ValueError, match="NET money"):
        _parse_with_mutated_words(mutate)
    assert hits


def test_a_net_rate_that_breaks_the_printed_total_is_refused():
    mutate, hits = _edit_all("N120.00", "N121.00")
    with pytest.raises(ValueError, match="summary|TOT"):
        _parse_with_mutated_words(mutate)
    assert hits


# ── Entry grosses up, by the ANAGRAF commission ──────────────────────────────


def test_entry_rate_grosses_a_net_io_and_leaves_a_gross_io_alone(order):
    assert ca.gross_rate(120.0, 15.0) == 141.18
    assert ca.gross_rate(100.0, 15.0) == 117.65
    assert ca.gross_rate(2500.0, 15.0) == 2941.18
    assert ca.gross_rate(0.0, 15.0) == 0.0
    assert ca.entry_rate(order, 120.0, 15.0) == 141.18
    gross_io = parse_crispin_pdf(GROSS_FIXTURE)
    assert ca.entry_rate(gross_io, 141.18, 15.0) == 141.18


def test_line_plan_keeps_every_ordered_spot(order):
    plan = ca._line_plan(order, date(2026, 11, 23), order.flight_end)
    assert len(plan) == 4
    entered = sum(
        r["spots_per_week"] * r["weeks"] for _, _, _, _, ranges, _ in plan for r in ranges
    )
    assert entered == 158
    descs = [d for _, _, _, d, _, _ in plan]
    assert descs[0].startswith("Cantonese News M-F 7-8p")
