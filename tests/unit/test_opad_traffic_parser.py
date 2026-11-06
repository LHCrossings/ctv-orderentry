"""opAD Media traffic memo: parse oracle, refusals, and estimate → contract resolution.

The fixture is the real NYSOH Open Enrollment sheet (estimate 3180, Mandarin,
11/9–11/29/2026, one :30 and one :15 ISCI) Lee assigned by hand on 2026-11-05.
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
    pick_contract,
)
from browser_automation.parsers.opad_traffic_parser import (  # noqa: E402
    OPAD_AGENCY_ID,
    is_opad_traffic_text,
    parse_opad_traffic_text,
)

FIXTURE = _root / "tests" / "fixtures" / "opad_traffic" / "nysoh_est3180_mnd_nov26.pdf"

SHEET = """\
1450 Broadway, Suite 902
New York, NY 10018
Phone: 212 490-1300
TRAFFIC INSTRUCTIONS
To: Operations Desk / Traffic Departments
From: Laurel Light llight@opadmedia.com
RE: NYSDOH | NYSOH | Open Enrollment
EST# 3180
***MANDARIN***
Date: November 5, 2026
_______________________________________________________________________
FROM: MONDAY, NOVEMBER 9, 2026
TO: SUNDAY, NOVEMBER 29, 2026
:30s
ISCI Code Length Rotation
NYS36ENRGQMH :30 100%
:15s
ISCI Code Length Rotation
NYS16ENRGQMH :15 100%
Please include ISCI codes on all invoices.
"""


@pytest.fixture(scope="module")
def fixture_text(real_pdfplumber):
    import io

    with real_pdfplumber.open(io.BytesIO(FIXTURE.read_bytes())) as pdf:
        return "\n".join(p.extract_text() or "" for p in pdf.pages)


def test_fixture_text_is_the_pinned_sheet(fixture_text):
    # The hand-typed SHEET below is what the tamper tests mutate; keep it honest.
    assert fixture_text.strip() == SHEET.strip()


def test_fixture_parses_estimate_language_flight_and_both_lengths(real_pdfplumber):
    import sys

    from browser_automation.parsers import opad_traffic_parser as mod

    mod.pdfplumber = real_pdfplumber  # the module bound conftest's mock at import
    try:
        instr = mod.parse_opad_traffic_pdf(FIXTURE.read_bytes())
    finally:
        mod.pdfplumber = sys.modules["pdfplumber"]
    assert instr.estimate == "3180"
    assert instr.advertiser == "NYSDOH | NYSOH | Open Enrollment"
    assert instr.language == "MANDARIN"
    assert instr.language_code == "M"
    assert (instr.start_date, instr.end_date) == ("11/9/26", "11/29/26")
    assert (instr.date_from_sql, instr.date_to_sql) == ("2026-11-09", "2026-11-29")
    assert instr.durations == [30, 15]
    assert [(s.isci, s.duration_sec, s.rotation_pct) for s in instr.spots] == [
        ("NYS36ENRGQMH", 30, 100.0),
        ("NYS16ENRGQMH", 15, 100.0),
    ]
    assert [s.isci for s in instr.spots_for(15)] == ["NYS16ENRGQMH"]


def test_detector_keys_on_opad_and_the_est_line():
    assert is_opad_traffic_text(SHEET)
    # Davis Elen / Tatari / a bare memo without EST# are not opAD.
    assert not is_opad_traffic_text(SHEET.replace("llight@opadmedia.com", "someone@agency.com"))
    assert not is_opad_traffic_text(SHEET.replace("EST# 3180", "Estimate 3180"))
    assert not is_opad_traffic_text(
        "SPOT COMMERCIAL INSTRUCTIONS\nDavis Elen Advertising\nEstimate 1500"
    )


def test_memo_sender_line_is_not_the_flight_start():
    # "From: Laurel Light" sits above "FROM: MONDAY, NOVEMBER 9, 2026" — the flight
    # is the FROM whose value is a date.
    instr = parse_opad_traffic_text(SHEET)
    assert instr.date_from_sql == "2026-11-09"


def test_weighted_rotation_within_one_length():
    text = SHEET.replace("NYS36ENRGQMH :30 100%", "NYS36ENRGQMH :30 60%\nNYS37ENRGQMH :30 40%")
    instr = parse_opad_traffic_text(text)
    assert [(s.isci, s.rotation_pct) for s in instr.spots_for(30)] == [
        ("NYS36ENRGQMH", 60.0),
        ("NYS37ENRGQMH", 40.0),
    ]


@pytest.mark.parametrize(
    "old, new, reason",
    [
        ("EST# 3180", "Estimate 3180", "no EST#"),
        ("FROM: MONDAY, NOVEMBER 9, 2026\n", "", "no FROM"),
        ("TO: SUNDAY, NOVEMBER 29, 2026", "TO: SUNDAY, NOVEMBER 2, 2026", "TO before FROM"),
        ("NYS36ENRGQMH :30 100%\n", "", "a :30 table with no rows is fine only if no table"),
        ("NYS36ENRGQMH :30 100%", "NYS36ENRGQMH :30 60%", "rotations add to 60"),
        ("NYS16ENRGQMH :15 100%", "NYS16ENRGQMH :30 100%", "a :30 row under the :15s table"),
        ("NYS36ENRGQMH :30 100%", "NYS36ENRGQMH :30 50%\nNYS36ENRGQMH :30 50%", "duplicate ISCI"),
    ],
)
def test_tampered_sheet_refuses(old, new, reason):
    text = SHEET.replace(old, new)
    assert text != SHEET, reason
    if reason.startswith("a :30 table with no rows"):
        # Dropping one length's row leaves the other table intact: still a sheet.
        instr = parse_opad_traffic_text(text)
        assert instr.durations == [15]
        return
    with pytest.raises(ValueError):
        parse_opad_traffic_text(text)


def test_sheet_with_no_isci_rows_refuses():
    text = SHEET.replace("NYS36ENRGQMH :30 100%\n", "").replace("NYS16ENRGQMH :15 100%\n", "")
    with pytest.raises(ValueError, match="no ISCI rows"):
        parse_opad_traffic_text(text)


def _c(cid, code, desc, ref, agency):
    return {"id": cid, "code": code, "description": desc, "customer_ref": ref, "agency_id": agency}


def test_pick_contract_finds_the_opad_contract_by_ref_and_code():
    cands = [
        _c(3178, "opAD NYDOH 3180", "NYS Dept of Health OE 4Q26 Est 3180", "3180", OPAD_AGENCY_ID),
        _c(3133, "IG Pechanga 31800", "Pechanga Resort Casino 31800", "", 125),
    ]
    chosen, matches = pick_contract(cands, "3180", agency_id=OPAD_AGENCY_ID)
    assert chosen["id"] == 3178
    assert [m["id"] for m in matches] == [3178]


def test_pick_contract_matches_customer_ref_alone():
    cands = [_c(3178, "opAD NYDOH Q4", "NYS Dept of Health OE 4Q26", "3180", OPAD_AGENCY_ID)]
    chosen, _ = pick_contract(cands, "3180", agency_id=OPAD_AGENCY_ID)
    assert chosen["id"] == 3178


def test_pick_contract_prefers_the_named_agency_among_two_token_matches():
    cands = [
        _c(1, "Other 3180", "Other agency 3180", "", 999),
        _c(2, "opAD NYDOH 3180", "NYS Dept of Health Est 3180", "3180", OPAD_AGENCY_ID),
    ]
    chosen, matches = pick_contract(cands, "3180", agency_id=OPAD_AGENCY_ID)
    assert chosen["id"] == 2 and len(matches) == 2
    # The Davis Elen default is unchanged: neither row is agency 121 → operator picks.
    assert pick_contract(cands, "3180")[0] is None
    assert DAVISELEN_AGENCY_ID == 121


def test_pick_contract_refuses_two_opad_revisions():
    cands = [
        _c(1, "opAD NYDOH 3180", "Est 3180", "3180", OPAD_AGENCY_ID),
        _c(2, "opAD NYDOH 3180 R1", "Est 3180 revised", "3180", OPAD_AGENCY_ID),
    ]
    chosen, matches = pick_contract(cands, "3180", agency_id=OPAD_AGENCY_ID)
    assert chosen is None and len(matches) == 2
