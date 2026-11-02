"""Tatari (WorldLink) traffic instruction parser.

The fixture is the real Lectric eBikes "week of Mon 11/02/26" PDF: two pages,
each its own flight with its own ISCI weights (page 1 = 11/04-11/08 Wed-Sun,
page 2 = 11/02-11/03 Mon-Tue), one ISCI on both pages at 10%. The original
parser read ``pdf.pages[0]`` only, so the Mon-Tue rotation was never applied
(Lee, 2026-11-02: "not all pages are being applied").
"""

import sys
from pathlib import Path

import pytest

_root = Path(__file__).resolve().parents[2]
for _p in (_root, _root / "browser_automation"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from browser_automation.parsers import tatari_traffic_parser as ttp  # noqa: E402

FIXTURE = _root / "tests" / "fixtures" / "tatari" / "lectric_ebikes_week_of_11-02-26.pdf"

PAGE1 = [
    ("Fold It Ride It Love It XP4 07.2026-30s", "MBFIRIV130H", 10.0),
    ("Lectric eBikes - 2026 Brand Spot - Black Friday - 30s", "MBLBBSV130H", 45.0),
    ("Lectric eBikes XP4 Spot Black Friday 30s", "MBLBSBV130H", 45.0),
]
PAGE2 = [
    ("Lectric eBikes XP4 TV Spot V2", "MBLBSVV130H", 45.0),
    ("Summer Brand Spot 07.2026-30s", "MBSBTTV130H", 45.0),
    ("Fold It Ride It Love It XP4 07.2026-30s", "MBFIRIV130H", 10.0),
]


@pytest.fixture(scope="module")
def instr(real_pdfplumber):
    ttp.pdfplumber = real_pdfplumber  # the module bound conftest's mock at import
    try:
        yield ttp.parse_tatari_traffic_pdf(FIXTURE.read_bytes(), year=2026)
    finally:
        ttp.pdfplumber = sys.modules["pdfplumber"]


def test_header_fields(instr):
    assert instr.advertiser == "Lectric eBikes"
    assert instr.search_suggestion == "Lectric"
    # instruction-level window = union of every page's flight (contract search only)
    assert (instr.date_from_sql, instr.date_to_sql) == ("2026-11-02", "2026-11-08")
    assert (instr.date_from_display, instr.date_to_display) == ("11/02", "11/08")


def test_every_page_is_read_and_each_spot_carries_its_page_flight(instr):
    rows = [(s.title, s.isci, s.rotation_pct, s.date_from_sql, s.date_to_sql) for s in instr.spots]
    assert rows == [(t, i, p, "2026-11-04", "2026-11-08") for t, i, p in PAGE1] + [
        (t, i, p, "2026-11-02", "2026-11-03") for t, i, p in PAGE2
    ]
    assert {s.duration_sec for s in instr.spots} == {30}


def test_periods_group_by_flight_window_in_page_order(instr):
    assert [(p.date_from_display, p.date_to_display) for p in instr.periods] == [
        ("11/04", "11/08"),
        ("11/02", "11/03"),
    ]
    wed_sun, mon_tue = instr.periods
    assert [(s.isci, s.rotation_pct) for s in wed_sun.spots] == [(i, p) for _, i, p in PAGE1]
    assert [(s.isci, s.rotation_pct) for s in mon_tue.spots] == [(i, p) for _, i, p in PAGE2]
    for per in instr.periods:
        assert sum(s.rotation_pct for s in per.spots) == 100.0


def test_the_shared_isci_is_kept_once_per_flight(instr):
    shared = [s for s in instr.spots if s.isci == "MBFIRIV130H"]
    assert [(s.date_from_sql, s.date_to_sql) for s in shared] == [
        ("2026-11-04", "2026-11-08"),
        ("2026-11-02", "2026-11-03"),
    ]


# ── build_instruction on hand-written pages (no PDF needed) ───────────────────

ISCI_HDR = ["Ad Name", "ISCI", "Duration", "Split"]
FLIGHT_HDR = ["Description", "Buy Type", "Flight", "Days", "Start Time", "End Time"]
TEXT = "Station: Crossings TV Advertiser: Woof Pet\n"


def _isci_table(*rows):
    return [ISCI_HDR] + [list(r) for r in rows]


def _flight_table(*flights):
    return [FLIGHT_HDR] + [["N", "Everyday", f, "Mon - Sun", "9:00 AM", "5:00 PM"] for f in flights]


def test_single_page_file_is_one_period_with_the_old_instruction_fields():
    pages = [
        (
            TEXT,
            [
                _isci_table(["Ad A", "WOOFA30H", "30s", "60%"], ["Ad B", "WOOFB30H", "30s", "40%"]),
                _flight_table("5/18 - 5/24"),
            ],
        )
    ]
    instr = ttp.build_instruction(pages, 2026)
    assert instr.search_suggestion == "Woof"
    assert (instr.date_from_sql, instr.date_to_sql) == ("2026-05-18", "2026-05-24")
    assert (instr.date_from_display, instr.date_to_display) == ("5/18", "5/24")
    assert len(instr.periods) == 1
    assert [(s.isci, s.rotation_pct) for s in instr.periods[0].spots] == [
        ("WOOFA30H", 60.0),
        ("WOOFB30H", 40.0),
    ]


def test_two_pages_with_the_same_flight_merge_into_one_period():
    pages = [
        (TEXT, [_isci_table(["Ad A", "WOOFA30H", "30s", "50%"]), _flight_table("5/18 - 5/24")]),
        ("", [_isci_table(["Ad B", "WOOFB30H", "30s", "50%"]), _flight_table("5/18 - 5/24")]),
    ]
    instr = ttp.build_instruction(pages, 2026)
    assert len(instr.periods) == 1
    assert [s.isci for s in instr.periods[0].spots] == ["WOOFA30H", "WOOFB30H"]


def test_several_flight_rows_on_one_page_span_the_page_window():
    pages = [
        (
            TEXT,
            [
                _isci_table(["Ad A", "WOOFA30H", "30s", "100%"]),
                _flight_table("5/20 - 5/24", "5/18 - 5/19"),
            ],
        ),
    ]
    instr = ttp.build_instruction(pages, 2026)
    assert [(p.date_from_sql, p.date_to_sql) for p in instr.periods] == [
        ("2026-05-18", "2026-05-24")
    ]


def test_a_week_across_new_year_rolls_the_end_date_forward():
    pages = [
        (TEXT, [_isci_table(["Ad A", "WOOFA30H", "30s", "100%"]), _flight_table("12/29 - 1/04")])
    ]
    instr = ttp.build_instruction(pages, 2026)
    assert (instr.date_from_sql, instr.date_to_sql) == ("2026-12-29", "2027-01-04")


def test_isci_rows_without_flight_dates_refuse_by_page():
    pages = [
        (TEXT, [_isci_table(["Ad A", "WOOFA30H", "30s", "100%"]), _flight_table("5/18 - 5/24")]),
        ("", [_isci_table(["Ad B", "WOOFB30H", "30s", "100%"])]),
    ]
    with pytest.raises(ValueError, match="page 2 .* no Flight dates"):
        ttp.build_instruction(pages, 2026)


def test_flight_dates_without_isci_rows_refuse_by_page():
    pages = [("", [_flight_table("5/18 - 5/24")])]
    with pytest.raises(ValueError, match="page 1 .* no ISCI rows"):
        ttp.build_instruction(pages, 2026)


def test_a_blank_page_is_skipped():
    pages = [
        (TEXT, [_isci_table(["Ad A", "WOOFA30H", "30s", "100%"]), _flight_table("5/18 - 5/24")]),
        ("Page 2 of 2\nNotes", []),
    ]
    assert len(ttp.build_instruction(pages, 2026).periods) == 1
