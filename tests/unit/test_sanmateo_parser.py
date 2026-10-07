"""San Mateo County Voters house-proposal parser — real PDF fixture + tampering.

The fixture is the actual General Election 2026 proposal (10/5 through 11/2); it
carries its own totals, so every guard is exercised by mutating the word stream
pdfplumber hands the parser. The line plan is pinned for the real late-entry case
(a Thursday 10/8 start) and for the flight's one-Monday last week.
"""

import sys
from datetime import date
from pathlib import Path

import pytest

_root = Path(__file__).resolve().parents[2]
for _p in [str(_root), str(_root / "src")]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

from browser_automation.parsers import sanmateo_parser as sm  # noqa: E402

FIXTURE = _root / "tests" / "fixtures" / "sanmateo" / "sanmateo_general_2026.pdf"


@pytest.fixture(scope="module")
def real(real_pdfplumber):
    sm.pdfplumber = real_pdfplumber  # the module bound conftest's mock at import
    try:
        yield real_pdfplumber
    finally:
        sm.pdfplumber = sys.modules["pdfplumber"]


@pytest.fixture(scope="module")
def order(real):
    return sm.parse_sanmateo(str(FIXTURE))


def test_header(order):
    assert order.client == "San Mateo County Voters"
    assert order.campaign == "San Mateo County Voters Media Campaign"
    assert order.title == "General Election"
    assert order.email == "Outreach@smcacre.gov"
    assert order.length_sec == 15
    assert order.market_code == "SFO"
    # no year on the sheet: it comes from the file name ("… 2026.pdf")
    assert order.flight_start == "10/05/2026" and order.flight_end == "11/02/2026"
    assert order.rates_are_net is False


def test_grid_shape(order):
    assert [ln.insertion for ln in order.lines] == [
        "Chinese (Cantonese) News",
        "Chinese (Mandarin)",
        "Filipino News/Drama/Variety",
        "South Asian News/Variety (Hindi)",
        "Korean News/Drama",
        "Chinese",
        "Filipino",
        "South Asian",
        "Korean",
    ]
    assert [ln.time_raw for ln in order.paid_lines] == [
        "M-F 7p-8p",
        "M-Sat 8p-9p",
        "M-F 6p-7p",
        "M-F 1p-2p & Sat-Sun 1p-4p",  # wrapped over two rows
        "M-F 8a-10a",
    ]
    assert order.week_dates == [
        date(2026, 10, 5),
        date(2026, 10, 12),
        date(2026, 10, 19),
        date(2026, 10, 26),
        date(2026, 11, 2),
    ]
    assert [ln.week_spots for ln in order.paid_lines] == [
        [4, 3, 3, 3, 2],
        [4, 4, 4, 4, 2],
        [4, 4, 4, 3, 2],
        [4, 3, 3, 3, 2],
        [4, 3, 3, 3, 2],
    ]
    assert [ln.rate for ln in order.paid_lines] == [45.0, 45.0, 35.0, 35.0, 35.0]
    assert all(ln.length_sec == 15 for ln in order.lines)


def test_bonus_rows_are_ros_with_zero_rate(order):
    bonus = order.bonus_lines
    assert [ln.base_language for ln in bonus] == ["Chinese", "Filipino", "South Asian", "Korean"]
    assert all(ln.is_bonus and ln.rate == 0 and ln.cost == 0 for ln in bonus)
    assert all(ln.week_spots == [2, 2, 2, 2, 2] for ln in bonus)
    assert all(ln.days == "M-Su" and ln.time == "ROS" for ln in bonus)


def test_totals_reconcile(order):
    assert order.paid_units_by_week == [20, 17, 17, 16, 10] and order.paid_units_stated == 80
    assert order.bonus_units_by_week == [8, 8, 8, 8, 8] and order.bonus_units_stated == 40
    assert order.paid_total == 3130.0
    assert order.airtime_cost_stated == 3130.0
    assert order.grand_total_stated == 3505.0
    assert order.total_cost == 3505.0
    assert order.total_spots == 120


def test_editing_is_a_charge_not_a_line(order):
    assert [(c.description, c.amount) for c in order.charges] == [("Editing", 375.0)]
    assert order.production_total == 375.0
    assert len(order.lines) == 9  # the charge never became a grid row


def test_dual_daypart_enters_as_one_line_on_the_union(order):
    sa = order.paid_lines[3]
    assert sa.day_time() == ("M-Su", "1p-2p; 1p-4p")
    assert sa.days == "M-Su" and sa.time == "1p-2p; 1p-4p"
    assert order.paid_lines[1].day_time() == ("M-Sa", "8p-9p")


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Chinese ( Mandarin)", "Chinese (Mandarin)"),
        ("Chinese(Cantonese) News", "Chinese (Cantonese) News"),
        ("Filipino News/ Drama/Variety", "Filipino News/Drama/Variety"),
        ("  Korean   News/Drama ", "Korean News/Drama"),
    ],
)
def test_tidy_label(raw, expected):
    assert sm.tidy_label(raw) == expected


def test_detection_is_client_keyed():
    assert sm.is_sanmateo_text("Client: San Mateo County Voters\nPhone: (650)")
    assert sm.is_sanmateo_text("Client:  San  Mateo County\nVoters")
    assert not sm.is_sanmateo_text("Client: San Mateo County Environmental Health Program")
    assert not sm.is_sanmateo_text("San Mateo County Voters".replace("Voters", "Health"))


def test_bridge_aliases(order):
    assert order.advertiser == order.client and order.market == "SFO"
    assert order.description == "General Election"
    ln = order.lines[2]
    assert ln.weekly_spots == ln.week_spots and ln.duration == "15" and ln.length == 15
    assert ln.language == "Filipino" and ln.daypart == "M-F 6p-7p"
    assert ln.description == "Filipino News/Drama/Variety M-F 6p-7p"


# ── Line plan: late entry and the one-Monday last week ───────────────────────


def _plan(order, start):
    from browser_automation.sanmateo_automation import _line_plan

    return _line_plan(order, start, order.flight_end)


def test_line_plan_on_time_gives_the_last_monday_its_own_cap(order):
    plan = _plan(order, date(2026, 10, 5))
    descs = [desc for _ln, _d, _t, desc, _r, _n in plan]
    assert descs == [
        "M-F 7p-8p Chinese (Cantonese) News",
        "M-Sa 8p-9p Chinese (Mandarin)",
        "M-F 6p-7p Filipino News/Drama/Variety",
        "M-Su 1p-4p South Asian News/Variety (Hindi)",
        "M-F 8a-10a Korean News/Drama",
        "BNS Chinese ROS",
        "BNS Filipino ROS",
        "BNS South Asian ROS",
        "BNS Korean ROS",
    ]
    assert all(notes == [] for *_, notes in plan)
    cant = plan[0][4]
    assert [
        (r["date_from"], r["date_to"], r["spots_per_week"], r["weeks"], r["max_daily"])
        for r in cant
    ] == [
        (date(2026, 10, 5), date(2026, 10, 11), 4, 1, 1),
        (date(2026, 10, 12), date(2026, 11, 1), 3, 3, 1),
        (date(2026, 11, 2), date(2026, 11, 2), 2, 1, 2),  # 2 spots on one Monday → 2/day
    ]
    assert cant[-1]["tag"].startswith("short last week")
    # every range can actually hold its spots
    total = sum(r["spots_per_week"] * r["weeks"] for *_, ranges, _n in plan for r in ranges)
    assert total == order.total_spots == 120


def test_line_plan_thursday_start_splits_the_short_first_week(order):
    plan = _plan(order, date(2026, 10, 8))
    by_desc = {desc: ranges for _ln, _d, _t, desc, ranges, _n in plan}
    cant = by_desc["M-F 7p-8p Chinese (Cantonese) News"]
    assert (cant[0]["date_from"], cant[0]["date_to"], cant[0]["max_daily"]) == (
        date(2026, 10, 8),
        date(2026, 10, 11),
        2,  # Thu + Fri hold 4 spots
    )
    assert (cant[1]["date_from"], cant[1]["max_daily"]) == (date(2026, 10, 12), 1)
    mand = by_desc["M-Sa 8p-9p Chinese (Mandarin)"]
    assert (mand[0]["date_to"], mand[0]["max_daily"]) == (date(2026, 10, 11), 2)  # Thu-Sat
    sa = by_desc["M-Su 1p-4p South Asian News/Variety (Hindi)"]
    assert (sa[0]["date_to"], sa[0]["max_daily"]) == (date(2026, 10, 11), 1)  # Thu-Sun: 4 days
    assert all(notes == [] for *_, notes in plan)
    total = sum(r["spots_per_week"] * r["weeks"] for *_, ranges, _n in plan for r in ranges)
    assert total == 120


def test_line_plan_saturday_start_drops_m_f_spots_and_says_so(order):
    plan = _plan(order, date(2026, 10, 10))
    by_desc = {desc: (ranges, notes) for _ln, _d, _t, desc, ranges, notes in plan}
    ranges, notes = by_desc["M-F 8a-10a Korean News/Drama"]
    assert ranges[0]["date_from"] == date(2026, 10, 12)
    assert notes and "4 spot(s) dropped" in notes[0]


# ── plan_ranges: the truncated-last-week rule in isolation ───────────────────


def test_plan_ranges_short_last_week():
    from browser_automation.line_planner import plan_ranges

    cons = [{"start_date": "10/05/2026", "end_date": "11/02/2026", "spots_per_week": 2, "weeks": 5}]
    got, notes = plan_ranges(cons, "M-F", date(2026, 10, 5), date(2026, 11, 2))
    assert notes == []
    assert [(r["date_from"], r["date_to"], r["weeks"], r["max_daily"]) for r in got] == [
        (date(2026, 10, 5), date(2026, 11, 1), 4, 1),
        (date(2026, 11, 2), date(2026, 11, 2), 1, 2),
    ]
    # a one-week range just takes the higher cap
    got, _ = plan_ranges(
        [{"start_date": "11/02/2026", "end_date": "11/02/2026", "spots_per_week": 3, "weeks": 1}],
        "M-F",
        date(2026, 11, 2),
        date(2026, 11, 2),
    )
    assert len(got) == 1 and got[0]["max_daily"] == 3
    # a last week with none of the line's days left is dropped, with a note
    got, notes = plan_ranges(cons, "Sa-Su", date(2026, 10, 5), date(2026, 11, 2))
    assert [(r["date_from"], r["date_to"], r["weeks"]) for r in got] == [
        (date(2026, 10, 5), date(2026, 11, 1), 4)
    ]
    assert notes == ["2 spot(s) dropped: no Sa-Su day left in the week of 11/2 on or before 11/2"]
    # a flight that ends on its Sunday is untouched
    got, notes = plan_ranges(
        [{"start_date": "10/05/2026", "end_date": "11/01/2026", "spots_per_week": 2, "weeks": 4}],
        "M-F",
        date(2026, 10, 5),
        date(2026, 11, 1),
    )
    assert notes == [] and len(got) == 1 and got[0]["max_daily"] == 1


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
    monkeypatch.setattr(sm, "pdfplumber", _Shim(real, mutate))
    return sm.parse_sanmateo(str(FIXTURE))


def _find(words, text, nth=0):
    hits = [w for w in words if w["text"] == text]
    return hits[nth]


def _row_of(words, anchor, text):
    return [x for x in words if abs(x["top"] - anchor["top"]) < 2 and x["text"] == text]


def test_noop_mutation_still_parses(monkeypatch, real, order):
    again = _parse_with(monkeypatch, real, lambda ws: ws)
    assert [ln.week_spots for ln in again.lines] == [ln.week_spots for ln in order.lines]


def test_dropped_week_cell_refuses(monkeypatch, real):
    def mutate(ws):
        w = _find(ws, "18")  # Mandarin Units; drop one of its "4" week cells
        ws.remove(_row_of(ws, w, "4")[0])
        return ws

    with pytest.raises(ValueError, match="week cells sum"):
        _parse_with(monkeypatch, real, mutate)


def test_slid_week_cell_refuses(monkeypatch, real):
    def mutate(ws):
        w = _find(ws, "18")
        cells = sorted(_row_of(ws, w, "4"), key=lambda x: x["x0"])
        cells[0]["x0"] += 45  # onto the next week column → two cells in one column
        cells[0]["x1"] += 45
        return ws

    with pytest.raises(ValueError, match="two cells landed|week cells sum|Paid row"):
        _parse_with(monkeypatch, real, mutate)


def test_blank_rate_refuses(monkeypatch, real):
    def mutate(ws):
        w = _find(ws, "18")
        for x in _row_of(ws, w, "45.00"):
            x["text"] = ""
        return ws

    with pytest.raises(ValueError, match="unplaced cell|Cost|Airtime total"):
        _parse_with(monkeypatch, real, mutate)


def test_wrong_line_total_refuses(monkeypatch, real):
    def mutate(ws):
        _find(ws, "10.00")["text"] = "20.00"  # "$ 8 10.00" → $820.00 on the Mandarin row
        return ws

    with pytest.raises(ValueError, match="Airtime total says"):
        _parse_with(monkeypatch, real, mutate)


def test_wrong_footer_refuses(monkeypatch, real):
    def mutate(ws):
        _find(ws, "80")["text"] = "79"
        return ws

    with pytest.raises(ValueError, match="Paid row"):
        _parse_with(monkeypatch, real, mutate)


def test_renamed_column_refuses(monkeypatch, real):
    def mutate(ws):
        for x in ws:
            if x["text"] == "Units":
                x["text"] = "Spots"
        return ws

    with pytest.raises(ValueError, match="header columns not found"):
        _parse_with(monkeypatch, real, mutate)


def test_bonus_row_with_a_cost_refuses(monkeypatch, real):
    def mutate(ws):
        w = _find(ws, "Bonus")  # first "ROS Bonus" row (Chinese)
        for x in ws:
            if abs(x["top"] - w["top"]) < 3 and x["text"] == "-" and x["x0"] < 400:
                x["text"] = "45.00"
        return ws

    with pytest.raises(ValueError, match="bonus row .* carries a cost|Airtime total says"):
        _parse_with(monkeypatch, real, mutate)


def test_blanked_editing_refuses(monkeypatch, real):
    def mutate(ws):
        _find(ws, "375.00")["text"] = ""
        return ws

    with pytest.raises(ValueError, match="unreadable charge row|Total Amount"):
        _parse_with(monkeypatch, real, mutate)


def test_removed_editing_row_refuses(monkeypatch, real):
    def mutate(ws):
        w = _find(ws, "Editing")
        return [x for x in ws if abs(x["top"] - w["top"]) >= 2]

    with pytest.raises(ValueError, match="Total Amount"):
        _parse_with(monkeypatch, real, mutate)


def test_week_header_not_a_monday_refuses(monkeypatch, real):
    def mutate(ws):
        _find(ws, "12-Oct")["text"] = "13-Oct"
        return ws

    with pytest.raises(ValueError, match="not a Monday"):
        _parse_with(monkeypatch, real, mutate)


def test_year_comes_from_the_monday_rule_without_a_file_name_year(real, tmp_path):
    import shutil

    copy = tmp_path / "San Mateo County Voter General.pdf"
    shutil.copy(FIXTURE, copy)
    o = sm.parse_sanmateo(str(copy))
    assert o.week_dates[0] == date(2026, 10, 5)  # 10/5 is a Monday only in 2026 near today


def test_file_name_year_that_contradicts_the_sheet_refuses(real, tmp_path):
    import shutil

    copy = tmp_path / "San Mateo County Voter General 2025.pdf"
    shutil.copy(FIXTURE, copy)
    with pytest.raises(ValueError, match="not a Monday"):
        sm.parse_sanmateo(str(copy))
