"""The "Traffic Instructions" agency sheet (Mynt Agency → WorldLink client Pacagen,
2026-09-18): Maija dropped it on the assign-assets page and it came back 'unknown'.

The format is the LAYOUT (``Code/Name With 800#`` / ``% to Run`` columns), the agency
is whatever the From: line says, and every row carries its own flight — so the sheet
is exposed as periods, like Direct Donor, and reconciles against its own percentages."""

import sys
from pathlib import Path

import pytest

_root = Path(__file__).parent.parent.parent
for _p in (_root, _root / "browser_automation"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

FIXTURE = _root / "tests" / "fixtures" / "trafinst" / "pacagen_mynt_q326.pdf"


@pytest.fixture(scope="module", autouse=True)
def _use_real_pdfplumber(real_pdfplumber):
    return real_pdfplumber


@pytest.fixture(scope="module")
def text():
    import io

    import pdfplumber

    with pdfplumber.open(io.BytesIO(FIXTURE.read_bytes())) as pdf:
        return "\n".join(p.extract_text() or "" for p in pdf.pages)


@pytest.fixture(scope="module")
def instr():
    from browser_automation.parsers.trafinst_traffic_parser import parse_trafinst_traffic_pdf

    return parse_trafinst_traffic_pdf(FIXTURE.read_bytes())


def test_detected_by_layout_not_by_agency(text):
    from browser_automation.parsers.trafinst_traffic_parser import is_trafinst_text

    assert is_trafinst_text(text)
    assert is_trafinst_text(text.replace("Mynt Agency", "Some Other Shop"))
    assert not is_trafinst_text("TRAFFIC INSTRUCTIONS tatari ISCI Duration Split")
    assert not is_trafinst_text("Client PRNT - 4imprint Traffic Instructions % Run")


def test_header_fields_stop_at_the_neighbouring_column(instr):
    assert instr.agency == "Mynt Agency"
    assert instr.advertiser == "Pacagen"  # not "Pacagen Time: 14:18:25"
    assert instr.client_code == "PCGN"
    assert instr.product == "Pacagen"
    assert instr.estimate == "Q326 July-Sept 2026"
    assert instr.market.startswith("National Cable")
    assert instr.search_suggestion == "Pacagen"


def test_six_thirty_second_rows_with_titles_and_own_dates(instr):
    rows = {s.isci: s for s in instr.spots}
    assert list(rows) == [
        "INVISIBLE26H",
        "SCIENCTS26H",
        "PACTOXIC30H",
        "PACFSCSP30H",
        "PACRHCSUP30H",
        "HAZMAT2H",
    ]
    assert {s.duration_sec for s in instr.spots} == {30}
    assert [s.rotation_pct for s in instr.spots] == [25.0, 15.0, 15.0, 20.0, 10.0, 15.0]
    assert rows["PACFSCSP30H"].title == "FOUNDER STORY CAT SPRAY"
    assert rows["HAZMAT2H"].title == "Pacagen Hazmat Guest No Offer"
    assert all(
        s.date_from_sql == "2026-09-07" and s.date_to_sql == "2026-09-27" for s in instr.spots
    )
    assert (instr.date_from_sql, instr.date_to_sql) == ("2026-09-07", "2026-09-27")
    assert (instr.date_from_display, instr.date_to_display) == ("9/7", "9/27")


def test_one_period_per_flight_window(instr):
    assert len(instr.periods) == 1
    assert [s.isci for s in instr.periods[0].spots] == [s.isci for s in instr.spots]


def test_a_second_flight_becomes_its_own_period(text):
    from browser_automation.parsers.trafinst_traffic_parser import parse_trafinst_traffic_text

    # A real sheet lists a creative once per flight (the Direct Donor lesson): add a
    # second flight in which two of the cuts run 50/50.
    t = text + (
        "\nISCI: INVISIBLE26H (778) 000-0000 030 50.0 09/28/26 10/04/26 / /"
        "\nPacagen Invisible 2026"
        "\nISCI: HAZMAT2H (778) 000-0000 030 50.0 09/28/26 10/04/26 / /"
        "\nPacagen Hazmat Guest No Offer\n"
    )
    r = parse_trafinst_traffic_text(t)
    assert len(r.spots) == 8 and len(r.periods) == 2
    first, second = r.periods
    assert (first.date_from_sql, first.date_to_sql) == ("2026-09-07", "2026-09-27")
    assert len(first.spots) == 6
    assert (second.date_from_sql, second.date_to_sql) == ("2026-09-28", "2026-10-04")
    assert [(s.isci, s.rotation_pct) for s in second.spots] == [
        ("INVISIBLE26H", 50.0),
        ("HAZMAT2H", 50.0),
    ]
    assert (r.date_from_sql, r.date_to_sql) == ("2026-09-07", "2026-10-04")


def test_percentages_that_do_not_foot_refuse(text):
    from browser_automation.parsers.trafinst_traffic_parser import (
        TrafInstParseError,
        parse_trafinst_traffic_text,
    )

    with pytest.raises(TrafInstParseError, match="sums to 95"):
        parse_trafinst_traffic_text(text.replace("030 10.0 09/07/26", "030 5.0 09/07/26"))


def test_duplicate_row_refuses(text):
    from browser_automation.parsers.trafinst_traffic_parser import (
        TrafInstParseError,
        parse_trafinst_traffic_text,
    )

    dup = text.replace("ISCI: HAZMAT2H", "ISCI: INVISIBLE26H")
    with pytest.raises(TrafInstParseError, match="listed twice"):
        parse_trafinst_traffic_text(dup)


def test_wrong_layout_refuses():
    from browser_automation.parsers.trafinst_traffic_parser import (
        TrafInstParseError,
        parse_trafinst_traffic_text,
    )

    with pytest.raises(TrafInstParseError):
        parse_trafinst_traffic_text("Client PRNT - 4imprint Traffic Instructions")


def test_no_op_mutation_still_parses(text):
    """The negative tests above pass for the right reason only if an untouched text parses."""
    from browser_automation.parsers.trafinst_traffic_parser import parse_trafinst_traffic_text

    assert len(parse_trafinst_traffic_text(text + "\n").spots) == 6
