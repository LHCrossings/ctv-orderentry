"""Health Plan of San Joaquin house-proposal parser — real PDF fixture + tampering.

The fixture is the actual DSNP 4Q26 revised proposal; it carries its own totals, so
every guard is exercised by mutating the word stream pdfplumber hands the parser.
"""

import sys
from datetime import date
from pathlib import Path

import pytest

_root = Path(__file__).resolve().parents[2]
for _p in [str(_root), str(_root / "src")]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

from browser_automation.parsers import hpsj_parser as hp  # noqa: E402

FIXTURE = _root / "tests" / "fixtures" / "hpsj" / "hpsj_dsnp_4q26_revised.pdf"


@pytest.fixture(scope="module")
def real(real_pdfplumber):
    hp.pdfplumber = real_pdfplumber  # the module bound conftest's mock at import
    try:
        yield real_pdfplumber
    finally:
        hp.pdfplumber = sys.modules["pdfplumber"]


@pytest.fixture(scope="module")
def order(real):
    return hp.parse_hpsj(str(FIXTURE))


def test_header(order):
    assert order.client == "Health Plan of San Joaquin"
    assert order.title == "DSNP"
    assert order.contact == "Jeremy Hixon"
    assert order.market_code == "CVC"
    assert order.proposal_date == date(2026, 9, 22)
    assert order.flight_start == "10/05/2026" and order.flight_end == "12/07/2026"
    assert order.rates_are_net is False


def test_grid_shape(order):
    assert [ln.insertion for ln in order.lines] == [
        "Vietnamese News/Talk",
        "Vietnamese Drama",
        "FILIPINO",
        "SOUTH ASIAN",
        "Vietnamese",
        "FILIPINO",
        "SOUTH ASIAN",
    ]
    # the week of 10/26 is a hiatus: eight columns, not nine
    assert order.week_dates == [
        date(2026, 10, 5),
        date(2026, 10, 12),
        date(2026, 10, 19),
        date(2026, 11, 2),
        date(2026, 11, 9),
        date(2026, 11, 16),
        date(2026, 11, 23),
        date(2026, 11, 30),
    ]
    news = order.lines[0]
    assert news.daypart == "M-SUN 11A-12P" and news.length_sec == 30
    assert news.value == 50.0 and news.cost == 35.0 and news.rate == 35.0
    assert news.week_spots == [10, 10, 10, 9, 10, 10, 9, 9] and news.units == 77
    assert order.lines[1].daypart == "M-SUN 10A-11A & 12P-1P"  # wrapped above the row
    assert order.lines[2].daypart == "M-F 4p-7pm/ Sat- Sun 4p-6p"
    # the SOUTH ASIAN bonus row: extract_tables merges five of its cells; words do not
    assert order.lines[6].week_spots == [2] * 8 and order.lines[6].units == 16


def test_bonus_rows_are_ros_with_zero_rate(order):
    for ln in order.bonus_lines:
        assert ln.is_bonus and ln.rate == 0.0 and ln.cost == 0.0 and ln.daypart == "ROS"
        assert ln.value == 40.0
    assert [ln.base_language for ln in order.bonus_lines] == [
        "Vietnamese",
        "Filipino",
        "South Asian",
    ]
    assert order.lines[3].base_language == "South Asian"


def test_totals_reconcile(order):
    assert order.paid_units_stated == 204 and order.bonus_units_stated == 86
    assert order.paid_units_by_week == [27, 27, 27, 26, 25, 25, 24, 23]
    assert order.grand_value_stated == 14240.0 and order.grand_cost_stated == 7500.0
    assert (
        order.paid_total == 7140.0
        and order.production_total == 360.0
        and order.total_cost == 7500.0
    )


def test_translation_is_a_charge_not_a_line(order):
    assert len(order.charges) == 1
    ch = order.charges[0]
    assert (
        ch.description.startswith("Translation Cost") and ch.amount == 360.0 and ch.value == 600.0
    )
    assert all("translation" not in ln.insertion.lower() for ln in order.lines)


def test_detection_is_client_keyed():
    assert hp.is_hpsj_text("Advertiser Health Plan of San Joaquin\nMarket: Central Valley")
    assert not hp.is_hpsj_text("Advertiser San Joaquin County Registrar of Voters")
    assert not hp.is_hpsj_text("Crossings TV Media Proposal")


def test_bridge_aliases(order):
    assert order.advertiser == order.client and order.market == "CVC"
    ln = order.lines[2]
    assert ln.weekly_spots == ln.week_spots and ln.duration == "30" and ln.length == 30
    assert ln.language == "Filipino"


# ── Tampering through the word stream ────────────────────────────────────────


class _Shim:
    """Wrap the real pdfplumber so extract_words() output can be mutated."""

    def __init__(self, real, mutate):
        self._real, self._mutate = real, mutate

    def open(self, path):
        real_pdf = self._real.open(path)
        mutate = self._mutate

        class _Page:
            def __init__(self, pg):
                self._pg = pg

            def extract_text(self, *a, **k):
                return self._pg.extract_text(*a, **k)

            def extract_words(self, *a, **k):
                return mutate(list(self._pg.extract_words(*a, **k)))

        class _Pdf:
            pages = [_Page(p) for p in real_pdf.pages]

            def __enter__(self):
                return self

            def __exit__(self, *a):
                real_pdf.close()

        return _Pdf()


def _parse_with(monkeypatch, real, mutate):
    monkeypatch.setattr(hp, "pdfplumber", _Shim(real, mutate))
    return hp.parse_hpsj(str(FIXTURE))


def _find(words, text, nth=0):
    hits = [w for w in words if w["text"] == text]
    return hits[nth]


def test_noop_mutation_still_parses(monkeypatch, real, order):
    again = _parse_with(monkeypatch, real, lambda ws: ws)
    assert [ln.week_spots for ln in again.lines] == [ln.week_spots for ln in order.lines]


def test_dropped_week_cell_refuses(monkeypatch, real):
    def mutate(ws):
        w = _find(ws, "77")  # Total Units of the first row; drop one of its week cells
        row = [x for x in ws if abs(x["top"] - w["top"]) < 2 and x["text"] == "10"]
        ws.remove(row[0])
        return ws

    with pytest.raises(ValueError, match="week cells sum"):
        _parse_with(monkeypatch, real, mutate)


def test_slid_week_cell_refuses(monkeypatch, real):
    def mutate(ws):
        w = _find(ws, "77")
        cells = sorted(
            [x for x in ws if abs(x["top"] - w["top"]) < 2 and x["text"] in ("10", "9")],
            key=lambda x: x["x0"],
        )
        cells[0]["x0"] += 31  # onto the next week column → two cells in one column
        cells[0]["x1"] += 31
        return ws

    with pytest.raises(ValueError, match="two cells landed|week cells sum|Paid Units"):
        _parse_with(monkeypatch, real, mutate)


def test_blank_rate_refuses(monkeypatch, real):
    def mutate(ws):
        w = _find(ws, "77")
        for x in ws:
            if abs(x["top"] - w["top"]) < 2 and x["text"] == "35.00":
                x["text"] = ""
        return ws

    with pytest.raises(ValueError, match="Promo Unit Cost|Total Cost"):
        _parse_with(monkeypatch, real, mutate)


def test_wrong_line_total_refuses(monkeypatch, real):
    def mutate(ws):
        _find(ws, "3,850.00")["text"] = "3,800.00"
        return ws

    with pytest.raises(ValueError, match="Total Value says"):
        _parse_with(monkeypatch, real, mutate)


def test_wrong_footer_refuses(monkeypatch, real):
    def mutate(ws):
        _find(ws, "204")["text"] = "203"
        return ws

    with pytest.raises(ValueError, match="Paid Units row"):
        _parse_with(monkeypatch, real, mutate)


def test_renamed_column_refuses(monkeypatch, real):
    def mutate(ws):
        for x in ws:
            if x["text"] == "Creative":
                x["text"] = "Length"
        return ws

    with pytest.raises(ValueError, match="header columns not found"):
        _parse_with(monkeypatch, real, mutate)


def test_bonus_row_with_a_cost_refuses(monkeypatch, real):
    def mutate(ws):
        w = _find(ws, "54")  # Vietnamese bonus Total Units
        for x in ws:
            if abs(x["top"] - w["top"]) < 3 and x["text"] == "-" and x["x0"] < 300:
                x["text"] = "35.00"
        return ws

    with pytest.raises(ValueError, match="BONUS row .* carries a cost|Total Cost says"):
        _parse_with(monkeypatch, real, mutate)


def test_blanked_translation_refuses(monkeypatch, real):
    def mutate(ws):
        _find(ws, "360.00")["text"] = ""
        return ws

    with pytest.raises(ValueError, match="translation row has no Total Cost|sheet total cost"):
        _parse_with(monkeypatch, real, mutate)


def test_week_header_not_a_monday_refuses(monkeypatch, real):
    def mutate(ws):
        _find(ws, "5-Oct")["text"] = "6-Oct"
        return ws

    with pytest.raises(ValueError, match="not a Monday"):
        _parse_with(monkeypatch, real, mutate)
