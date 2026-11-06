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
            **{
                f"nov_{k}": ltp.parse_lexus_traffic_pdf((FIX / f"cy26-nov-{k}.pdf").read_bytes())
                for k in (
                    "nyc-hinglish",
                    "sea-hinglish",
                    "sea-chinese",
                    "sea-vietnamese",
                    "sfo-hinglish",
                    "sfo-vietnamese",
                )
            },
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
    assert nyc.search_suggestion == "Lexus NYC Hinglish"  # names the contract's description
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


# ---------------------------------------------------------------------------
# November 2026 (Lee 11/6): six sheets, six live contracts. New in this layout:
# the flight uses an en dash ('11/04/26 – 11/30/26') with two-digit years again,
# an 'HD' marker sits after the file name and/or after the ISCI, and most ISCIs
# print as 13-character '…HH' — the delivered .mov files are '…H'.
# ---------------------------------------------------------------------------


def test_november_sea_vietnamese_en_dash_hd_marker_and_hh_isci(sheets):
    sea = sheets["nov_sea-vietnamese"]
    assert (sea.market_code, sea.search_suggestion) == ("SEA", "Lexus SEA Viet")
    assert _periods(sea) == [
        (30, "2026-11-04", "2026-11-30", [("TYIW26TL935H", 50.0), ("TYIW26TL891H", 50.0)]),
        (30, "2026-12-01", "2026-12-31", [("TYIW27TL031H", 50.0), ("TYIW26TL987H", 50.0)]),
        (15, "2026-11-04", "2026-11-30", [("TYIW26TL936H", 50.0), ("TYIW26TL892H", 50.0)]),
        (15, "2026-12-01", "2026-12-31", [("TYIW27TL032H", 50.0), ("TYIW26TL988H", 50.0)]),
    ]
    first = sea.periods[0].spots[0]
    assert first.isci_printed == "TYIW26TL935HH"
    assert first.title == "MY26-RX_TV_OVER-THE-YEARS_VT_CY26-NOV_23-07_LWA-7"  # no stray HD
    assert (first.language, first.model) == ("Vietnamese", "RX")
    assert first.notes == "Asset Swap at 12:01 AM 12/1"
    assert all(p.rotation_total == 100.0 for p in sea.periods)


def test_november_nyc_keeps_the_sheets_dates_and_exposes_the_rotation_total(sheets):
    nyc = sheets["nov_nyc-hinglish"]
    assert nyc.search_suggestion == "Lexus NYC Hinglish"
    by = {(p.duration_sec, p.date_from_sql): p for p in nyc.periods}
    # ES rows are dated 12/1–12/31 on the sheet although delivered with November;
    # the parser does not move them — the total makes the defect visible on the card.
    assert [s.isci for s in by[(30, "2026-11-04")].spots] == ["TYIW26TL929H", "TYIW26TL885H"]
    assert by[(30, "2026-11-04")].rotation_total == 66.0
    assert [s.isci for s in by[(30, "2026-12-01")].spots] == [
        "TYIW26TL877H",
        "TYIW27TL025H",
        "TYIW26TL981H",
        "TYIW26TL973H",
    ]
    assert by[(30, "2026-12-01")].rotation_total == 132.0
    # an ISCI printed with ' HD' after it is read whole, with nothing to normalise
    es = by[(30, "2026-12-01")].spots[0]
    assert es.isci_printed == "" and es.model == "ES"


def test_november_sea_and_sfo_sheets_share_the_western_creatives(sheets):
    def iscis(k):
        return [s.isci for p in sheets[k].periods for s in p.spots]

    assert iscis("nov_sea-hinglish") == iscis("nov_sfo-hinglish")
    assert iscis("nov_sea-vietnamese") == iscis("nov_sfo-vietnamese")
    assert sheets["nov_sea-chinese"].search_suggestion == "Lexus SEA Chinese"
    assert sheets["nov_sfo-hinglish"].search_suggestion == "Lexus SFO Hinglish"
    assert {s.language for p in sheets["nov_sea-chinese"].periods for s in p.spots} == {"Mandarin"}


def test_normalize_isci_only_strips_the_fused_hd_marker():
    assert ltp.normalize_isci("TYIW26TL929HH") == "TYIW26TL929H"
    assert ltp.normalize_isci("TYIW26TL929H") == "TYIW26TL929H"
    assert ltp.normalize_isci("TYIW26TL929") == "TYIW26TL929"
    assert ltp.normalize_isci("MCIV012326VH") == "MCIV012326VH"


def test_hd_marker_in_text_does_not_touch_file_names_or_the_section_switch():
    text = _TWO_DIGIT.replace(
        "MY26-TX_TV_Here_ENG TYIW26TL431H 50% 8/5/26 - 8/30/26",
        "MY26-TX_TV_Here_HD_ENG HD TYIW26TL431HH HD 50% 8/5/26 – 8/30/26",
    )
    instr = ltp.parse_lexus_traffic_text(text)
    s = instr.periods[0].spots[0]
    assert (s.isci, s.isci_printed, s.title) == (
        "TYIW26TL431H",
        "TYIW26TL431HH",
        "MY26-TX_TV_Here_HD_ENG",
    )
    assert instr.periods[0].date_to_sql == "2026-08-30"
    # the SD section is still ignored: the row repeats there and must not double
    assert [sp.isci for p in instr.periods for sp in p.spots].count("TYIW26TL431H") == 1
