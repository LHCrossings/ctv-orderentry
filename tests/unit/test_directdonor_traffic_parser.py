"""Direct Donor TV (WorldLink/AATV) traffic instruction parser.

The fixture is the real Shriners September 3Q26 file: two flight windows,
the same ISCI in both (100% in week 1, 33% in weeks 2-4). The original
parser took one flight from the first row and deduped on ISCI alone, so
only the first line was ever assigned (Lee, 2026-09-07).
"""

import sys
from pathlib import Path

import pytest

_root = Path(__file__).resolve().parents[2]
for _p in (_root, _root / "browser_automation"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from browser_automation.parsers.directdonor_traffic_parser import (  # noqa: E402
    parse_directdonor_traffic_ods,
)

FIXTURE = _root / "tests" / "fixtures" / "directdonor" / "TRAFFIC-AATV-SHC-SEPT_3Q26.xls"


@pytest.fixture(scope="module")
def instr():
    pytest.importorskip("xlrd")
    return parse_directdonor_traffic_ods(FIXTURE.read_bytes(), FIXTURE.name)


def test_header_fields(instr):
    assert instr.advertiser == "Shriners Hospital for Children"
    assert instr.search_suggestion == "Shriners"
    # instruction-level window = union of every row's flight (contract search only)
    assert (instr.date_from_sql, instr.date_to_sql) == ("2026-08-31", "2026-09-27")
    assert (instr.date_from_display, instr.date_to_display) == ("8/31", "9/27")


def test_every_row_is_kept_with_its_own_flight(instr):
    rows = [(s.isci, s.rotation_pct, s.date_from_sql, s.date_to_sql) for s in instr.spots]
    assert rows == [
        ("SHCMS2CCFH", 100.0, "2026-08-31", "2026-09-06"),
        ("SHCMS2CCFH", 33.0, "2026-09-07", "2026-09-27"),
        ("SHCJM2CCFH", 33.0, "2026-09-07", "2026-09-27"),
        ("SHCPG2CCFH", 34.0, "2026-09-07", "2026-09-27"),
    ]
    assert {s.duration_sec for s in instr.spots} == {120}


def test_periods_group_by_flight_window_in_file_order(instr):
    assert [(p.date_from_display, p.date_to_display) for p in instr.periods] == [
        ("8/31", "9/6"),
        ("9/7", "9/27"),
    ]
    week1, rest = instr.periods
    assert [(s.isci, s.rotation_pct) for s in week1.spots] == [("SHCMS2CCFH", 100.0)]
    assert [(s.isci, s.rotation_pct) for s in rest.spots] == [
        ("SHCMS2CCFH", 33.0),
        ("SHCJM2CCFH", 33.0),
        ("SHCPG2CCFH", 34.0),
    ]
    assert sum(s.rotation_pct for s in rest.spots) == 100.0


def test_titles_and_notes_rows_are_not_spots(instr):
    titles = {s.isci: s.title for s in instr.spots}
    assert titles["SHCJM2CCFH"] == "James Story (JM) :120"
    # the "Please provide 30 minute seperation" / spotbox rows carry no ISCI
    assert len(instr.spots) == 4
