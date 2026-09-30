"""Davis Elen traffic instructions: parse oracle + estimate → contract resolution.

The fixture is the real Toyota estimate 1500 sheet (Mandarin, Oct 2026) whose
substring lookup picked 'IG Pechanga 31500' on 2026-09-29.
"""

import sys
from pathlib import Path

import pytest

_root = Path(__file__).resolve().parents[2]
for _p in [str(_root), str(_root / "src")]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

from browser_automation.parsers.daviselen_traffic_parser import (  # noqa: E402
    DAVISELEN_AGENCY_ID,
    estimate_matches,
    pick_contract,
)

FIXTURE = _root / "tests" / "fixtures" / "daviselen_traffic" / "toyota_est1500_oct26.pdf"


@pytest.fixture(scope="module")
def instr(real_pdfplumber):
    import sys

    from browser_automation.parsers import daviselen_traffic_parser as mod

    mod.pdfplumber = real_pdfplumber  # the module bound conftest's mock at import
    try:
        yield mod.parse_daviselen_traffic_pdf(FIXTURE.read_bytes())
    finally:
        mod.pdfplumber = sys.modules["pdfplumber"]


def test_fixture_parses_estimate_and_creative(instr):
    assert instr.estimate == "1500"
    assert instr.duration_sec == 30
    assert instr.date_from_sql == "2026-10-01"
    assert instr.date_to_sql == "2026-10-25"
    assert [s.isci for s in instr.spots] == ["TYRL6705TH"]
    assert instr.spots[0].rotation_pct == 100.0


@pytest.mark.parametrize(
    "estimate, text, expected",
    [
        ("1500", "Daviselen Toyota 1500", True),
        ("1500", "So Cal Toyota Est 1500", True),
        ("1500", "IG Pechanga 31500", False),
        ("1500", "Pechanga Resort Casino 31500", False),
        ("1500", "Toyota 15000", False),
        ("1500", "Toyota 1500A", True),  # a letter is not a digit
        ("1500", None, False),
        ("", "Daviselen Toyota 1500", False),
        ("15x0", "Daviselen Toyota 15x0", False),
    ],
)
def test_estimate_matches_is_whole_number(estimate, text, expected):
    assert estimate_matches(estimate, text) is expected


def _c(cid, code, desc, agency=DAVISELEN_AGENCY_ID):
    return {"id": cid, "code": code, "description": desc, "agency_id": agency}


def test_pick_contract_drops_substring_neighbour():
    # Exactly the 2026-09-29 rows, in the order the route saw them (newest first).
    cands = [
        _c(3133, "IG Pechanga 31500", "Pechanga Resort Casino 31500", agency=125),
        _c(3077, "Daviselen Toyota 1500", "So Cal Toyota Est 1500"),
    ]
    chosen, matches = pick_contract(cands, "1500")
    assert chosen["id"] == 3077
    assert [m["id"] for m in matches] == [3077]


def test_pick_contract_prefers_daviselen_when_two_tokens_match():
    cands = [
        _c(1, "Other Agency 1500", "Some est 1500", agency=99),
        _c(2, "Daviselen Toyota 1500", "So Cal Toyota Est 1500"),
    ]
    chosen, matches = pick_contract(cands, "1500")
    assert chosen["id"] == 2
    assert len(matches) == 2


def test_pick_contract_refuses_ambiguous():
    cands = [
        _c(1, "Daviselen Toyota 1500", "So Cal Toyota Est 1500"),
        _c(2, "Daviselen Toyota 1500*", "So Cal Toyota Est 1500 rev"),
    ]
    chosen, matches = pick_contract(cands, "1500")
    assert chosen is None
    assert len(matches) == 2


def test_pick_contract_nothing_matches():
    chosen, matches = pick_contract([_c(3133, "IG Pechanga 31500", "x", agency=125)], "1500")
    assert chosen is None and matches == []
