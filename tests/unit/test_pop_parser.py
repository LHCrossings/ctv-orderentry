"""Prince of Peace sales-confirmation parser — real PDF fixture + tampering.

The fixture is the actual confirmation (carries its own totals). Negative tests
mutate the extracted (text, tables) by patching `pop_parser._extract` — the
layer that fails in the wild — and assert the parser REFUSES. A no-op mutation
must still parse, or the negatives pass for the wrong reason.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

for p in (str(Path(__file__).resolve().parents[2]),):
    if p not in sys.path:
        sys.path.insert(0, p)

import browser_automation.parsers.pop_parser as pp  # noqa: E402
from browser_automation.parsers.pop_parser import (  # noqa: E402
    POPOrder,
    POPParseError,
    is_pop_text,
    parse_pop,
    split_ordered,
)

FIXTURE = Path(__file__).parent.parent / "fixtures" / "pop" / "pop_klo_september_2026.pdf"


@pytest.fixture(scope="module", autouse=True)
def _use_real_pdfplumber(real_pdfplumber):
    """Every test here parses the real fixture, so take the shared escape hatch
    from the suite-wide pdfplumber MagicMock (see tests/conftest.py). The parser
    imports pdfplumber inside `_extract`, so the sys.modules swap is enough."""
    return real_pdfplumber


@pytest.fixture(scope="module")
def raw(real_pdfplumber):
    return pp._extract(str(FIXTURE))


@pytest.fixture(scope="module")
def order(real_pdfplumber) -> POPOrder:
    return parse_pop(str(FIXTURE))


# ─── Positive ─────────────────────────────────────────────────────────────────


def test_header(order):
    assert order.client == "Prince of Peace Enterprises, Inc."
    assert order.advertiser == "Kwan Loong Oil"
    assert order.contact == "Anderson Chang"
    assert order.estimate == "September"
    assert order.billing_type == "Calendar"
    assert order.market == "CVC"
    assert order.date_written == "8/27/2026"
    assert order.station_rep == "Charmaine Lane"
    assert order.revision == "0"
    assert order.rates_are_net is False
    assert order.description == "Kwan Loong Oil September"


def test_flight_and_totals(order):
    assert (order.flight_start, order.flight_end) == ("09/01/2026", "09/30/2026")
    assert order.gross_units == order.net_units == 48 == order.total_spots
    assert order.gross_total == order.net_total == 1440.0 == order.paid_total
    assert order.notes == "Vietnamese program- M-SUN 10A-1P"
    assert order.creative_link.startswith("https://www.dropbox.com/")


def test_lines(order):
    assert len(order.lines) == 3
    mf, ss, bns = order.lines
    assert (mf.language, mf.days, mf.time, mf.is_bonus, mf.total_spots, mf.rate) == (
        "Vietnamese",
        "M-F",
        "10A-1P",
        False,
        24,
        40.0,
    )
    assert mf.line_total == 960.0 and mf.length_sec == 30 and mf.market == "CVC"
    assert (ss.days, ss.total_spots, ss.line_total) == ("Sa-Su", 12, 480.0)
    assert bns.is_bonus and bns.rate == 0.0 and bns.total_spots == 12 and bns.days == ""
    assert mf.description == "M-F Vietnamese" and bns.description == "BNS Vietnamese ROS"
    assert mf.duration == "30"


def test_split_ordered():
    assert split_ordered("VIET M-F 10A-1P") == ("Vietnamese", "M-F", "10A-1P", False)
    assert split_ordered("VIET SAT - SUN 10A-1P") == ("Vietnamese", "Sa-Su", "10A-1P", False)
    assert split_ordered("VIET Various") == ("Vietnamese", "", "", True)
    assert split_ordered("Chinese M-Su 7P-12A") == ("Chinese", "M-Su", "7P-12A", False)
    with pytest.raises(POPParseError):
        split_ordered("English M-F 10A-1P")
    with pytest.raises(POPParseError):
        split_ordered("VIET Tue-Thu 10A-1P")


def test_detector():
    assert is_pop_text(
        "SALES CONFIRMATION - CROSSINGS TV\nClient Prince of Peace Enterprises, Inc."
    )
    assert not is_pop_text("SALES CONFIRMATION - CROSSINGS TV\nClient Some Other Client")
    assert not is_pop_text("Prince of Peace invoice")


# ─── Negative (tamper the extraction; the document is its own oracle) ─────────


def _patched(monkeypatch, raw, text_fn=lambda t: t, tables_fn=lambda tb: tb):
    text, tables = raw
    import copy

    monkeypatch.setattr(
        pp, "_extract", lambda _p: (text_fn(text), tables_fn(copy.deepcopy(tables)))
    )


def test_noop_mutation_still_parses(monkeypatch, raw):
    _patched(monkeypatch, raw)
    assert parse_pop("x.pdf").total_spots == 48


def _grid(tables):
    for t in tables:
        if any(c and str(c).startswith("Line Number") for c in t[0]):
            return t
    raise AssertionError("grid not found")


def test_units_changed_refuses(monkeypatch, raw):
    def mut(tables):
        g = _grid(tables)
        g[1][8] = "23"  # Total # of Units on line 1
        return tables

    _patched(monkeypatch, raw, tables_fn=mut)
    with pytest.raises(POPParseError):
        parse_pop("x.pdf")


def test_rate_blanked_refuses(monkeypatch, raw):
    def mut(tables):
        g = _grid(tables)
        g[1][10] = ""  # Gross Unit Rate
        return tables

    _patched(monkeypatch, raw, tables_fn=mut)
    with pytest.raises(POPParseError):
        parse_pop("x.pdf")


def test_footer_total_changed_refuses(monkeypatch, raw):
    _patched(
        monkeypatch,
        raw,
        text_fn=lambda t: t.replace(
            "Gross Amount 48 spots $ 1,440.00", "Gross Amount 48 spots $ 1,400.00"
        ),
    )
    with pytest.raises(POPParseError):
        parse_pop("x.pdf")


def test_dropped_line_refuses(monkeypatch, raw):
    def mut(tables):
        g = _grid(tables)
        del g[2]  # the Sa-Su line vanishes; footer still says 48
        return tables

    _patched(monkeypatch, raw, tables_fn=mut)
    with pytest.raises(POPParseError):
        parse_pop("x.pdf")


def test_renamed_column_refuses(monkeypatch, raw):
    def mut(tables):
        g = _grid(tables)
        g[0][8] = "Units"  # header label changed → must raise, never guess by position
        return tables

    _patched(monkeypatch, raw, tables_fn=mut)
    with pytest.raises(POPParseError):
        parse_pop("x.pdf")


def test_other_client_refuses(monkeypatch, raw):
    _patched(
        monkeypatch,
        raw,
        text_fn=lambda t: t.replace("Prince of Peace Enterprises, Inc.", "Someone Else LLC"),
    )
    with pytest.raises(POPParseError):
        parse_pop("x.pdf")
