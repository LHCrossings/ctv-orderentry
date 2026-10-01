"""Gauger + Associates Broadcast Order parser — real IO fixture + tampering.

The fixture is the actual Shea Homes IO 90658 (Oct 2026); it carries its own totals
(line Totals, "50 paid spots", TOTAL SPOTS, NET), so every guard is exercised by
mutating the word stream pdfplumber hands the parser.
"""

import sys
from datetime import date
from pathlib import Path

import pytest

_root = Path(__file__).resolve().parents[2]
for _p in [str(_root), str(_root / "src")]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

from browser_automation.parsers import gauger_parser as gp  # noqa: E402

FIXTURE = _root / "tests" / "fixtures" / "gauger" / "gauger_shea_io90658.pdf"


@pytest.fixture(scope="module")
def real(real_pdfplumber):
    gp.pdfplumber = real_pdfplumber  # the module bound conftest's mock at import
    try:
        yield real_pdfplumber
    finally:
        gp.pdfplumber = sys.modules["pdfplumber"]


@pytest.fixture(scope="module")
def order(real):
    return gp.parse_gauger(str(FIXTURE))


def test_header(order):
    assert order.order_number == "90658"
    assert order.customer_ref == "Order 90658"
    assert order.order_date == date(2026, 9, 30)
    assert order.client_code == "SHA"
    assert order.job == "SHA 997-26"
    assert order.market_name == "San Francisco" and order.market_code == "SFO"
    assert order.ad_name == "Opal-Emerald"
    assert order.broadcast_month == "October 2026"
    assert order.length_sec == 30
    assert order.revision == "Original"
    assert order.contact == "Carol Polombo"
    assert order.email == "cpolombo@gauger-associates.com"
    assert order.rates_are_net is True
    assert order.flight_start == date(2026, 10, 8) and order.flight_end == date(2026, 10, 29)


def test_lines_and_names_completed_from_the_desc_block(order):
    rows = [
        (ln.description, ln.spots, ln.net_rate, ln.net_total, ln.is_bonus) for ln in order.lines
    ]
    assert rows == [
        ("M-F 1p-2p Hindi News and Variety", 21, 102.0, 2142.0, False),
        ("M-F 2p-4p Punjabi News", 18, 102.0, 1836.0, False),
        ("Sa-Su 1p-4p Hindi Variety", 11, 85.0, 935.0, False),
        ("BNS M-Su 1p-4p Hindi/Punjabi", 24, 0.0, 0.0, True),
    ]
    # the table itself prints a truncated and a misspelt name — kept for the record
    assert order.lines[0].program_cell == "Hindi News and Vari..."
    assert order.lines[1].program_cell == "Pujabi News"
    assert all(
        ln.date_from == date(2026, 10, 8) and ln.date_to == date(2026, 10, 29) for ln in order.lines
    )
    assert all(ln.length_sec == 30 for ln in order.lines)


def test_totals(order):
    assert order.paid_spots_stated == 50 and order.total_spots_stated == 74
    assert order.net_total_stated == 4913.0 and order.net_total == 4913.0
    assert order.total_spots == 74


def test_detection_text(order):
    assert gp.is_gauger_text("Broadcast Order\nGauger + Associates\n576 Sacramento Street")
    assert not gp.is_gauger_text("Broadcast Order\nSome Other Agency")
    assert not gp.is_gauger_text("")


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("M-F", "M-F"),
        ("Mon - Sun", "M-Su"),
        ("Sat-Sun", "Sa-Su"),
        ("Sat", "Sa"),
        ("Monday-Friday", "M-F"),
    ],
)
def test_normalize_days(raw, expected):
    assert gp.normalize_days(raw) == expected


def test_unknown_day_pattern_raises():
    with pytest.raises(gp.GaugerParseError):
        gp.normalize_days("Tue/Thu")


# ─── tampering: the word stream the parser reads ─────────────────────────────


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
    monkeypatch.setattr(gp, "pdfplumber", _Shim(real, mutate))
    return gp.parse_gauger(str(FIXTURE))


def _find(words, text, nth=0):
    return [w for w in words if w["text"] == text][nth]


def test_noop_mutation_still_parses(monkeypatch, real, order):
    again = _parse_with(monkeypatch, real, lambda ws: ws)
    assert [ln.description for ln in again.lines] == [ln.description for ln in order.lines]
    assert again.net_total == order.net_total


def test_dropped_line_total_refuses(monkeypatch, real):
    def drop(ws):
        ws.remove(_find(ws, "$935.00"))
        return ws

    with pytest.raises(gp.GaugerParseError, match="no Total"):
        _parse_with(monkeypatch, real, drop)


def test_changed_spot_count_refuses_on_the_paid_summary(monkeypatch, real):
    def change(ws):
        _find(ws, "21", nth=1)["text"] = (
            "20"  # the Spots cell of line 1 (nth=0 is the Desc block's "21")
        )
        return ws

    with pytest.raises(gp.GaugerParseError, match="paid spots|not a cent rate"):
        _parse_with(monkeypatch, real, change)


def test_renamed_header_column_refuses(monkeypatch, real):
    def rename(ws):
        _find(ws, "Spots")["text"] = "Units"
        return ws

    with pytest.raises(gp.GaugerParseError, match="missing column"):
        _parse_with(monkeypatch, real, rename)


def test_money_on_a_bonus_row_refuses(monkeypatch, real):
    def pay(ws):
        w = dict(_find(ws, "$935.00"))
        bonus_spots = _find(ws, "24", nth=1)  # the table row, not the Desc block's "24 spots"
        w["top"], w["bottom"] = bonus_spots["top"], bonus_spots["bottom"]
        w["text"] = "$1.00"
        ws.append(w)
        return ws

    with pytest.raises(gp.GaugerParseError, match="bonus row carries money"):
        _parse_with(monkeypatch, real, pay)


# ─── entry planner + bridge ──────────────────────────────────────────────────


def test_line_plan_grosses_up_and_caps_per_day(order):
    from browser_automation.gauger_automation import gross_rate, line_plan

    assert (
        gross_rate(102.0, 15.0) == 120.0
        and gross_rate(85.0, 15.0) == 100.0
        and gross_rate(0, 15) == 0.0
    )
    plan = line_plan(order, date(2026, 10, 8), 15.0)
    assert [
        (p["description"], p["rate"], p["total_spots"], p["max_daily"], p["time_range"])
        for p in plan
    ] == [
        ("M-F 1p-2p Hindi News and Variety", 120.0, 21, 2, "13:00-14:00"),
        ("M-F 2p-4p Punjabi News", 120.0, 18, 2, "14:00-16:00"),
        ("Sa-Su 1p-4p Hindi Variety", 100.0, 11, 2, "13:00-16:00"),
        ("BNS M-Su 1p-4p Hindi/Punjabi", 0.0, 24, 2, "13:00-16:00"),
    ]
    assert round(sum(p["rate"] * p["total_spots"] for p in plan), 2) == 5780.0  # 4913 / 0.85
    # a later start clips the window and raises the cap
    late = line_plan(order, date(2026, 10, 19), 15.0)
    assert (
        late[0]["date_from"] == date(2026, 10, 19) and late[0]["max_daily"] == 3
    )  # 21 over 9 weekdays


def test_bridge_normalizer_feeds_net_rates(order):
    from web.parser_bridge import _normalize_gauger

    n = _normalize_gauger(order)
    assert n["rates_are_net"] is True
    assert n["estimate_number"] == "90658" and n["markets"] == ["SFO"]
    assert n["total_spots"] == 74 and n["total_cost"] == 4913.0
    assert n["lines"][0]["rate"] == 102.0 and n["lines"][3]["is_bonus"]
    assert n["flight_start"] == "2026-10-08" and n["flight_end"] == "2026-10-29"
