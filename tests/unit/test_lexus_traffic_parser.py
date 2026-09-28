"""IW Group (Lexus) Television Traffic Sheet parser.

Lee 9/28: the three CY26 September sheets parsed to ZERO rows because IW Group
started printing four-digit years ('09/11/2026 - 09/30/2026') and the date
pattern only knew 'M/D/YY' — so the Lexus card assigned nothing and every
September-tail spot on the 2610 contracts was assigned by hand. Two real sheets
are fixtures; the two-digit form is pinned from text so the old layout cannot
regress unnoticed.
"""

import sys
from pathlib import Path

import pytest

_root = Path(__file__).resolve().parents[2]
for p in [str(_root), str(_root / "src")]:
    if p not in sys.path:
        sys.path.insert(0, p)

from browser_automation.parsers import lexus_traffic_parser as ltp  # noqa: E402

FIX = _root / "tests" / "fixtures" / "lexus_traffic"


@pytest.fixture(scope="module")
def sheets(real_pdfplumber):
    ltp.pdfplumber = real_pdfplumber  # the module bound conftest's mock at import
    try:
        yield {
            "nyc": ltp.parse_lexus_traffic_pdf((FIX / "cy26-sept-nyc-hinglish.pdf").read_bytes()),
            "sfo": ltp.parse_lexus_traffic_pdf((FIX / "cy26-sept-sfo-vietnamese.pdf").read_bytes()),
        }
    finally:
        ltp.pdfplumber = sys.modules["pdfplumber"]


def _periods(instr):
    return [
        (
            p.duration_sec,
            p.date_from_sql,
            p.date_to_sql,
            [(s.isci, s.rotation_pct) for s in p.spots],
        )
        for p in instr.periods
    ]


def test_nyc_hinglish_four_digit_years(sheets):
    nyc = sheets["nyc"]
    assert (nyc.advertiser, nyc.coverage_area, nyc.market_code) == ("Lexus", "New York", "NYC")
    assert nyc.search_suggestion == "Lexus NYC"
    # HD section only — the SD repeat of every row must not double the spots
    assert _periods(nyc) == [
        (30, "2026-09-11", "2026-09-30", [("TYIW26TL681H", 50.0), ("TYIW26TL723H", 50.0)]),
        (15, "2026-09-11", "2026-09-30", [("TYIW26TL682H", 50.0), ("TYIW26TL724H", 50.0)]),
    ]
    assert nyc.periods[0].date_label == "09/11/2026–09/30/2026"
    assert nyc.periods[0].spots[0].title == "MY26-TX_TV_Here_ENG_CY26-Sept_23_07_LEA-4"


def test_sfo_vietnamese_sheet(sheets):
    sfo = sheets["sfo"]
    assert sfo.market_code == "SFO" and sfo.campaign == "September LDA (TX, NX, RX)"
    assert _periods(sfo) == [
        (30, "2026-09-11", "2026-09-30", [("TYIW26TL687H", 50.0), ("TYIW26TL729H", 50.0)]),
        (15, "2026-09-11", "2026-09-30", [("TYIW26TL688H", 50.0), ("TYIW26TL730H", 50.0)]),
    ]


_TWO_DIGIT = """IW Group Television Traffic Sheet
Agency Contact: Adam Vuong Advertiser: Lexus
Campaign: August LDA
Coverage Area: Seattle
Time Period: 8/5/26 - 8/30/26
TV Spot
HD
:30
Hinglish TX :30 MY26-TX_TV_Here_ENG TYIW26TL431H 50% 8/5/26 - 8/30/26
Hinglish RX :30 MY26-RX_TV_Across TYIW26TL432H 50% 8/5/26 - 8/30/26 late start
:15
Hinglish TX :15 MY26-TX_TV_Here_ENG TYIW26TL479H 100% 8/5/26 - 8/17/26
Hinglish TX :15 MY26-TX_TV_Here_ENG TYIW26TL480H 100% 8/18/26 - 8/30/26
SD
:30
Hinglish TX :30 MY26-TX_TV_Here_ENG TYIW26TL431H 50% 8/5/26 - 8/30/26
"""


def test_two_digit_years_still_parse_and_split_periods():
    instr = ltp.parse_lexus_traffic_text(_TWO_DIGIT)
    assert instr.market_code == "SEA"
    assert _periods(instr) == [
        (30, "2026-08-05", "2026-08-30", [("TYIW26TL431H", 50.0), ("TYIW26TL432H", 50.0)]),
        (15, "2026-08-05", "2026-08-17", [("TYIW26TL479H", 100.0)]),
        (15, "2026-08-18", "2026-08-30", [("TYIW26TL480H", 100.0)]),
    ]
    assert instr.periods[0].spots[1].notes == "late start"


def test_row_without_a_date_range_is_dropped_not_guessed():
    text = _TWO_DIGIT.replace("TYIW26TL432H 50% 8/5/26 - 8/30/26 late start", "TYIW26TL432H 50%")
    instr = ltp.parse_lexus_traffic_text(text)
    assert [s.isci for p in instr.periods for s in p.spots] == [
        "TYIW26TL431H",
        "TYIW26TL479H",
        "TYIW26TL480H",
    ]
