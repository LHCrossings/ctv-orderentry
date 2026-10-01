"""Illinois Lottery (Flowers) house-proposal parser — real PDF fixture + tampering.

The fixture is the FY'27 MCM Media Plan; it carries its own totals (Units, Value,
Proposed, header Gross/Net), so every guard is exercised by mutating the word stream
pdfplumber hands the parser.
"""

import sys
from datetime import date
from pathlib import Path

import pytest

_root = Path(__file__).resolve().parents[2]
for _p in [str(_root), str(_root / "src")]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

from browser_automation.parsers import illottery_parser as ip  # noqa: E402

FIXTURE = _root / "tests" / "fixtures" / "illottery" / "illottery_fy27_mcm_media_plan.pdf"


@pytest.fixture(scope="module")
def real(real_pdfplumber):
    ip.pdfplumber = real_pdfplumber
    try:
        yield real_pdfplumber
    finally:
        ip.pdfplumber = sys.modules["pdfplumber"]


@pytest.fixture(scope="module")
def order(real):
    return ip.parse_illottery(str(FIXTURE))


def test_header(order):
    assert order.advertiser == "Illinois Lottery"
    assert order.agency == "Flowers Communications Group"
    assert order.market_name.startswith("Chicago-Twin Cities") and order.market_code == "CMP"
    assert order.contact == "Jeff Robey" and order.email == "robey@explorefcg.com"
    assert order.gross_stated == 17644.0 and order.net_stated == 14997.40
    assert order.implied_commission == 15.0
    assert order.proposal_date == date(2026, 7, 27)
    assert order.holiday == "Lunar New Year 2/6"
    assert order.length_sec == 15
    assert order.rates_are_net is False


def test_weeks_cross_new_year(order):
    assert len(order.week_dates) == 20
    assert order.week_dates[0] == date(2026, 10, 12)
    assert order.week_dates[11] == date(2026, 12, 28)
    assert order.week_dates[12] == date(2027, 1, 4)
    assert order.week_dates[-1] == date(2027, 2, 22)
    assert all(d.weekday() == 0 for d in order.week_dates)
    assert order.flight_start == date(2026, 10, 12) and order.flight_end == date(2027, 2, 28)


def test_rows(order):
    rows = [
        (ln.block, ln.daypart, ln.rate, ln.units, ln.proposed, ln.is_bonus) for ln in order.lines
    ]
    assert rows == [
        ("Cantonese & Mandarin News & Drama", "M-F 7p-9p", 33.0, 144, 4752.0, False),
        ("Mandarin News & Entertainment", "Sat-Sun 8p-12a", 20.0, 76, 1520.0, False),
        ("Filipino News /Talk", "M-F 4p-5p, 6p-7p", 27.0, 134, 3618.0, False),
        ("Korean News &Entertainment", "M-F 8a-10a", 24.0, 134, 3216.0, False),
        ("South Asian News", "M-F 1p-4p", 27.0, 134, 3618.0, False),
        ("South Asian Entertainment", "Sat-Sun1p-4p", 20.0, 46, 920.0, False),
        ("Chinese ROS", "", 0.0, 172, 0.0, True),
        ("Filipino ROS", "", 0.0, 172, 0.0, True),
        ("Korean News &Entertainment ROS", "", 0.0, 172, 0.0, True),
        ("South Asian ROS", "", 0.0, 172, 0.0, True),
    ]
    # the wrapped title row ("Cantonese & Mandarin News &") was claimed by the row below it
    assert order.lines[0].week_spots == [
        5,
        5,
        5,
        5,
        5,
        5,
        6,
        6,
        6,
        10,
        10,
        10,
        6,
        6,
        6,
        12,
        12,
        12,
        6,
        6,
    ]
    assert order.lines[0].rate_per30 == 70.0 and order.lines[0].rate_per15 == 42.0
    # wrapped impressions cells attach to the row above them
    assert order.lines[3].impressions == 176534 and order.lines[4].impressions == 265044
    assert order.total_spots == 1356 and order.total_units_stated == 1356
    assert order.gross_total == 17644.0 and order.total_value_stated == 39888.0


def test_detection_text():
    assert ip.is_illottery_text("Client Flowers Communications Group\nAdvertiser Illinois Lottery")
    assert not ip.is_illottery_text("Advertiser California State Lottery")


def test_week_years_walk_backwards_from_the_printed_year():
    assert ip._week_years("October, November, December, January, February 2027") == {
        10: 2026,
        11: 2026,
        12: 2026,
        1: 2027,
        2: 2027,
    }
    assert ip._week_years("March, April 2027") == {3: 2027, 4: 2027}
    with pytest.raises(ip.ILLotteryParseError):
        ip._week_years("October, November")


def test_split_block_daypart():
    assert ip.split_block_daypart("Cantonese & Mandarin News & Drama M-F 7p-9p") == (
        "Cantonese & Mandarin News & Drama",
        "M-F 7p-9p",
    )
    assert ip.split_block_daypart("Filipino News /Talk M-F 4p-5p, 6p-7p") == (
        "Filipino News /Talk",
        "M-F 4p-5p, 6p-7p",
    )
    assert ip.split_block_daypart("South Asian Entertainment Sat-Sun1p-4p") == (
        "South Asian Entertainment",
        "Sat-Sun1p-4p",
    )
    assert ip.split_block_daypart("Bonus Chinese ROS") == ("Bonus Chinese ROS", "")


# ─── tampering ───────────────────────────────────────────────────────────────


class _Shim:
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
    monkeypatch.setattr(ip, "pdfplumber", _Shim(real, mutate))
    return ip.parse_illottery(str(FIXTURE))


def _find(words, text, nth=0):
    return [w for w in words if w["text"] == text][nth]


def test_noop_mutation_still_parses(monkeypatch, real, order):
    again = _parse_with(monkeypatch, real, lambda ws: ws)
    assert [ln.units for ln in again.lines] == [ln.units for ln in order.lines]


def test_dropped_week_cell_refuses(monkeypatch, real):
    def drop(ws):
        ws.remove(_find(ws, "144"))  # Units of row 1 stays; a week cell goes
        return ws

    with pytest.raises(ip.ILLotteryParseError, match="Units"):
        _parse_with(monkeypatch, real, drop)


def test_changed_week_cell_refuses(monkeypatch, real):
    def change(ws):
        w = _find(ws, "12")  # the first "12" week cell (row 1, 25-Jan)
        w["text"] = "13"
        return ws

    with pytest.raises(ip.ILLotteryParseError, match="week cells sum"):
        _parse_with(monkeypatch, real, change)


def test_renamed_header_column_refuses(monkeypatch, real):
    def rename(ws):
        _find(ws, "Units")["text"] = "Spots"
        return ws

    with pytest.raises(ip.ILLotteryParseError, match="missing column"):
        _parse_with(monkeypatch, real, rename)


def test_changed_rate_refuses(monkeypatch, real):
    def change(ws):
        _find(ws, "33.00")["text"] = "34.00"
        return ws

    with pytest.raises(ip.ILLotteryParseError, match="Proposed"):
        _parse_with(monkeypatch, real, change)


def test_dangling_wrapped_title_refuses(monkeypatch, real):
    def strand(ws):
        # delete the data row the wrapped title belongs to: every word on the 'Drama' row
        top = _find(ws, "Drama")["top"]
        return [w for w in ws if abs(w["top"] - top) > 2.0]

    with pytest.raises(ip.ILLotteryParseError):
        _parse_with(monkeypatch, real, strand)


# ─── planner + bridge ────────────────────────────────────────────────────────


def test_line_descriptions_use_house_short_forms(order):
    from browser_automation.illottery_automation import line_description

    assert [line_description(ln) for ln in order.lines] == [
        "M-F 7p-9p Chinese News & Drama",
        "Sa-Su 8p-12a Mandarin News & Entertainment",
        "M-F 4p-7p Filipino News/Talk",
        "M-F 8a-10a Korean News & Entertainment",
        "M-F 1p-4p South Asian News",
        "Sa-Su 1p-4p South Asian Entertainment",
        "BNS Chinese ROS",
        "BNS Filipino ROS",
        "BNS Korean ROS",
        "BNS South Asian ROS",
    ]


def test_line_plan_consolidates_weeks_and_uses_ros_windows(order):
    from browser_automation.illottery_automation import line_plan

    plan = line_plan(order, date(2026, 10, 12))
    assert sum(p["total_spots"] for p in plan) == 1356
    assert round(sum(p["rate"] * p["total_spots"] for p in plan), 2) == 17644.0
    first = [p for p in plan if p["description"] == "M-F 7p-9p Chinese News & Drama"]
    assert [(p["date_from"], p["date_to"], p["spots_per_week"], p["weeks"]) for p in first] == [
        (date(2026, 10, 12), date(2026, 11, 22), 5, 6),
        (date(2026, 11, 23), date(2026, 12, 13), 6, 3),
        (date(2026, 12, 14), date(2027, 1, 3), 10, 3),
        (date(2027, 1, 4), date(2027, 1, 24), 6, 3),
        (date(2027, 1, 25), date(2027, 2, 14), 12, 3),
        (date(2027, 2, 15), date(2027, 2, 28), 6, 2),
    ]
    assert all(p["time_range"] == "19:00-21:00" and p["days"] == "M-F" for p in first)
    filipino = next(p for p in plan if p["description"].startswith("M-F 4p-7p Filipino"))
    assert filipino["time_range"] == "16:00-19:00"
    chinese_ros = next(p for p in plan if p["description"] == "BNS Chinese ROS")
    assert (
        chinese_ros["days"] == "M-Su"
        and chinese_ros["time_range"] == "06:00-23:59"
        and chinese_ros["rate"] == 0.0
    )
    korean_ros = next(p for p in plan if p["description"] == "BNS Korean ROS")
    assert korean_ros["time_range"] == "08:00-10:00"
    # a late start inside the first week keeps the week's spots at a higher cap
    late = line_plan(order, date(2026, 10, 14))
    assert late[0]["date_from"] == date(2026, 10, 14) and late[0]["tag"]


def test_bridge_normalizer_is_gross(order):
    from web.parser_bridge import _normalize_illottery

    n = _normalize_illottery(order)
    assert n["rates_are_net"] is False
    assert n["markets"] == ["CMP"] and n["total_spots"] == 1356 and n["total_cost"] == 17644.0
    assert n["flight_start"] == "2026-10-12" and n["flight_end"] == "2027-02-28"
    assert n["lines"][0]["weekly_spots"][:3] == [5, 5, 5] and n["lines"][0]["rate"] == 33.0
    assert n["lines"][6]["is_bonus"] and n["lines"][6]["description"] == "BNS Chinese ROS"
