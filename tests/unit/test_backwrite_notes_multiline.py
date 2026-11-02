"""Etere's contract Notes keep their line breaks all the way to the workbook.

Admerasia 3053 (Lee 2026-11-02): the note is five lines in Etere (Ref /
Campaign / Campaign Period / DMA / No religious shows) and the Sales
Confirmation showed them joined with "; " on one line, because the placement
CSV builder flattened the field and parse_csv read the header from one
physical line. csv.writer already quotes embedded newlines; the readers now
parse CSV records.
"""

import csv
import inspect
import io
import sys
from pathlib import Path

import openpyxl

_root = Path(__file__).resolve().parents[2]
for _p in (_root, _root / "src"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from backwrite import eterebridge_runner as runner  # noqa: E402
from backwrite.transformer import _replace_placeholder, parse_csv  # noqa: E402

NOTES = (
    "Ref: McDonald's\nCampaign: Spicy McNuggets 2026\n"
    "Campaign Period: 9/7/2026 - 10/4/2026\nDMA: San Francisco\nNo religious shows"
)


def _csv_bytes(notes: str) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow([""] * 10)
    w.writerow(
        [
            "Admerasia",
            "Admerasia McD 15SF 2609",
            "",
            "McDonald's Est 15 SFO 2609",
            "",
            "McDonald's",
            "",
            "Lee Hudson",
            notes,
            "",
        ]
    )
    w.writerow([""] * 10)
    w.writerow(
        [
            "id_contrattirighe",
            "Textbox14",
            "duration3",
            "IMPORTO2",
            "nome2",
            "dateschedule",
            "airtimep",
        ]
    )
    return buf.getvalue().encode("utf-8")


def test_parse_csv_keeps_the_notes_line_breaks_and_the_other_header_columns():
    header, spots = parse_csv(_csv_bytes(NOTES))
    assert header.notes == NOTES
    assert header.notes.count("\n") == 4
    assert (header.agency, header.contract_code, header.client, header.ae) == (
        "Admerasia",
        "Admerasia McD 15SF 2609",
        "McDonald's",
        "Lee Hudson",
    )
    assert spots == []


def test_extract_header_values_reads_a_record_that_spans_lines(tmp_path):
    f = tmp_path / "placement.csv"
    f.write_bytes(_csv_bytes(NOTES))
    assert runner._extract_header_values(str(f)) == ("Admerasia", "McDonald's")


def test_the_csv_builder_no_longer_flattens_notes():
    src = inspect.getsource(runner.build_placement_csv_from_db)
    assert '"; "' not in src and "'; '" not in src
    assert 'replace("\\r\\n", "\\n")' in src


def test_whole_cell_placeholder_writes_the_multiline_value_verbatim():
    ws = openpyxl.Workbook().active
    ws["B22"] = "<notes>"
    assert _replace_placeholder(ws["B22"], {"notes": NOTES})
    assert ws["B22"].value == NOTES
