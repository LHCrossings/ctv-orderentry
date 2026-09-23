"""IW Group / Covered California TELEVISION ORDER parser — real fixtures + tampering.

The three fixtures are the actual IOs (each carries its own totals four ways).
Negative tests mutate the extracted (text, words) by patching `iwcca_parser._extract`
— the layer that fails in the wild — and assert the parser REFUSES. A no-op mutation
must still parse, or the negatives pass for the wrong reason.
"""

from __future__ import annotations

import copy
import sys
from datetime import date
from pathlib import Path

import pytest

for p in (str(Path(__file__).resolve().parents[2]),):
    if p not in sys.path:
        sys.path.insert(0, p)

import browser_automation.parsers.iwcca_parser as ip  # noqa: E402
from browser_automation.parsers.iwcca_parser import (  # noqa: E402
    IWCCAParseError,
    broadcast_month,
    is_iwcca_text,
    language_from_filename,
    parse_iwcca,
)

FIX = Path(__file__).parent.parent / "fixtures" / "iwcca"
CHINESE = FIX / "cca_35382_chinese.pdf"
VIET = FIX / "cca_35393_vietnamese.pdf"
FILIPINO = FIX / "cca_35397_filipino.pdf"

WEEKS = [
    "10/05/2026",
    "10/12/2026",
    "10/19/2026",
    "10/26/2026",
    "11/02/2026",
    "11/09/2026",
    "11/16/2026",
    "11/23/2026",
    "11/30/2026",
    "12/07/2026",
    "12/14/2026",
    "12/21/2026",
]


@pytest.fixture(scope="module", autouse=True)
def _use_real_pdfplumber(real_pdfplumber):
    return real_pdfplumber


@pytest.fixture(scope="module")
def raw(real_pdfplumber):
    return ip._extract(str(CHINESE))


@pytest.fixture(scope="module")
def chinese(real_pdfplumber):
    return parse_iwcca(str(CHINESE))


# ─── Positive ─────────────────────────────────────────────────────────────────


def test_header(chinese):
    o = chinese
    assert o.order_no == "35382"
    assert o.io_date == "9/22/2026"
    assert (o.flight_start, o.flight_end, o.weeks_stated) == ("10/05/2026", "12/20/2026", 11)
    assert o.campaign == "FY26-27 Covered California Brannding - Awareness"
    assert o.description_io == "Crossings TV - Y26-27 Brand Awareness Ch"
    assert (
        o.notes
        == "FY26-27 Covered California Brannding - Awareness | Crossings TV - Y26-27 Brand Awareness Ch"
    )
    assert o.language == "Chinese"
    assert o.market_hint == ""  # 'Crossings TV' alone does not name a market
    assert o.rates_are_net is True
    assert o.estimate_number == "35382" and o.client == "Covered California"


def test_week_columns_and_dark_weeks(chinese):
    assert chinese.week_start_dates == WEEKS
    mand = chinese.lines[0]
    assert mand.weekly_spots == [10, 10, 10, 0, 0, 10, 10, 10, 0, 9, 8, 0]
    assert mand.total_spots == 77


def test_chinese_lines(chinese):
    o = chinese
    assert [ln.description for ln in o.lines] == [
        "M-F 8p-10p Mandarin :30",
        "BNS M-F 8p-12a Mandarin :30",
        "M-F 7p-8p Cantonese :30",
        "BNS M-F 7p-12a Cantonese :30",
    ]
    assert [ln.is_bonus for ln in o.lines] == [False, True, False, True]
    assert [ln.net_rate for ln in o.lines] == [50.0, 0.0, 50.0, 0.0]
    assert [ln.total_spots for ln in o.lines] == [77, 62, 38, 30]
    assert o.total_spots == 207 and o.total_net == 5750.0 and o.total_cost == 5750.0
    assert o.monthly == [
        ((2026, 10), 67, 2250.0),
        ((2026, 11), 90, 2250.0),
        ((2026, 12), 50, 1250.0),
    ]
    assert all(ln.length_sec == 30 for ln in o.lines)
    assert o.lines[1].description_text.startswith("Mandarin - :30")  # 'AV ' stripped


def test_vietnamese_and_filipino():
    v = parse_iwcca(str(VIET))
    assert v.market_hint == "CVC"  # 'KBTV (Crossings TV)'
    assert v.language == "Vietnamese" and v.order_no == "35393"
    assert [ln.description for ln in v.lines] == [
        "M-F 10a-1p Vietnamese :30",
        "BNS M-F 10a-1p Vietnamese :30",
    ]
    assert v.lines[0].dp == "P" and v.lines[0].net_rate == 35.0
    assert v.total_spots == 174 and v.total_net == 3360.0

    f = parse_iwcca(str(FILIPINO))
    assert f.language == "Filipino" and f.market_hint == "CVC"
    # the line names no language word — 'Taglish' in the description resolves it
    assert [ln.language for ln in f.lines] == ["Filipino", "Filipino"]
    assert [ln.description for ln in f.lines] == [
        "M-F 4p-7p Filipino :30",
        "BNS M-F 4p-7p Filipino :30",
    ]
    assert f.total_spots == 80 and f.total_net == 1600.0
    assert f.monthly == [((2026, 10), 30, 600.0), ((2026, 11), 30, 600.0), ((2026, 12), 20, 400.0)]


def test_detection_is_client_keyed(raw):
    text = raw[0][0]
    assert is_iwcca_text(text)
    assert not is_iwcca_text(text.replace("Covered California", "Lexus"))
    assert not is_iwcca_text("IW Group traffic instructions for Lexus OrderNo 1")
    assert language_from_filename("CCA_Crossings TV_000035382_Chinese.pdf") == "Chinese"
    assert language_from_filename("CCA_Crossings TV_000035397_Filipino.pdf") == "Filipino"
    assert language_from_filename("something.pdf") == ""


def test_broadcast_month():
    assert broadcast_month(date(2026, 10, 26)) == (2026, 11)  # week holds Nov 1
    assert broadcast_month(date(2026, 10, 19)) == (2026, 10)
    assert broadcast_month(date(2026, 11, 30)) == (2026, 12)
    assert broadcast_month(date(2026, 12, 28)) == (2027, 1)


# ─── Negative: tamper the word stream, the parser must refuse ─────────────────


def _mutate(monkeypatch, raw, fn):
    pages = copy.deepcopy(raw)
    fn(pages)
    monkeypatch.setattr(ip, "_extract", lambda path: pages)


def _words(pages):
    return pages[0][1]


def _row_words(pages, first_text, second_text):
    """Words of the data row whose first two tokens (by x) are the given texts."""
    ws = sorted(_words(pages), key=lambda w: (round(w["top"]), w["x0"]))
    for i, w in enumerate(ws):
        if w["text"] == first_text and i + 1 < len(ws) and ws[i + 1]["text"] == second_text:
            top = w["top"]
            return [x for x in _words(pages) if abs(x["top"] - top) <= 1.0]
    raise AssertionError("row not found")


def test_noop_mutation_still_parses(monkeypatch, raw):
    _mutate(monkeypatch, raw, lambda pages: None)
    assert parse_iwcca(str(CHINESE)).total_spots == 207


def test_dropped_week_cell_refuses(monkeypatch, raw):
    def drop(pages):
        row = _row_words(pages, "M-F", "8p-10p")
        cell = next(w for w in row if w["text"] == "10")
        _words(pages).remove(cell)

    _mutate(monkeypatch, raw, drop)
    with pytest.raises(IWCCAParseError, match="week cells sum"):
        parse_iwcca(str(CHINESE))


def test_cell_slid_between_columns_refuses(monkeypatch, raw):
    def slide(pages):
        row = _row_words(pages, "M-F", "8p-10p")
        cell = next(w for w in row if w["text"] == "10")
        cell["x0"] += 12.0
        cell["x1"] += 12.0

    _mutate(monkeypatch, raw, slide)
    with pytest.raises(IWCCAParseError, match="between week columns"):
        parse_iwcca(str(CHINESE))


def test_blanked_rate_refuses(monkeypatch, raw):
    def blank(pages):
        row = _row_words(pages, "M-F", "8p-10p")
        rate = next(w for w in row if w["text"] == "50.00")
        _words(pages).remove(rate)

    _mutate(monkeypatch, raw, blank)
    with pytest.raises(IWCCAParseError):
        parse_iwcca(str(CHINESE))


def test_av_line_with_money_refuses(monkeypatch, raw):
    def pay_the_bonus(pages):
        row = _row_words(pages, "M-F", "8p-12a")
        for w in row:
            if w["text"] == "0.00":
                w["text"] = "1.00"
                break

    _mutate(monkeypatch, raw, pay_the_bonus)
    with pytest.raises(IWCCAParseError):
        parse_iwcca(str(CHINESE))


def test_renamed_header_refuses(monkeypatch, raw):
    def rename(pages):
        for w in _words(pages):
            if w["text"] == "Len":
                w["text"] = "Length"

    _mutate(monkeypatch, raw, rename)
    with pytest.raises(IWCCAParseError, match="missing from the grid header"):
        parse_iwcca(str(CHINESE))


def test_monthly_summary_mismatch_refuses(monkeypatch, raw):
    def bump(pages):  # the summary prints on page 2 of this IO — mutate every page
        for i, (text, words) in enumerate(pages):
            pages[i] = (text.replace("Spots 67 90 50 207", "Spots 68 89 50 207"), words)

    _mutate(monkeypatch, raw, bump)
    with pytest.raises(IWCCAParseError, match="OCT '26"):
        parse_iwcca(str(CHINESE))


def test_other_client_refuses(monkeypatch, raw):
    def relabel(pages):  # the client name prints on both pages
        for i, (text, words) in enumerate(pages):
            pages[i] = (text.replace("Covered California", "Some Other Client"), words)

    _mutate(monkeypatch, raw, relabel)
    with pytest.raises(IWCCAParseError, match="not an IW Group"):
        parse_iwcca(str(CHINESE))
