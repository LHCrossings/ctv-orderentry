"""Marketing Architects (WorldLink/MA) traffic instruction parser.

No MA traffic PDF is on hand (the seven "MA" files in incoming/Used are WorldLink IOs
for MA clients), so the shape is pinned on the row text the regex was written for.
Until 2026-11-02 the parser read page 1 only and kept the ONE date segment with the
most ISCIs; every other segment was dropped (the Tatari pages[0] lesson, same day).
"""

import sys
from pathlib import Path

_root = Path(__file__).resolve().parents[2]
for _p in (_root, _root / "browser_automation"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from browser_automation.parsers import ma_traffic_parser as mtp  # noqa: E402

HEADER = (
    "Client PRNT - 4imprint Traffic Instructions\n"
    "Product PRTV - 4imprint TV\n"
    "Length :30 - (030)\n"
    "ISCI Title Phone Length % Run Start End\n"
)
ROW = "{isci} {title} 800-555-1234 :30 {pct} {start} {end}\n"


def _row(isci, title, pct, start, end):
    return ROW.format(isci=isci, title=title, pct=pct, start=start, end=end)


def test_single_segment_is_one_period_with_the_old_instruction_fields():
    text = (
        HEADER
        + _row("PRNT-1234XX30A", "Promo A", 60, "5/26/2025", "6/29/2025")
        + _row("PRNT-1235XX30B", "Promo B", 40, "5/26/2025", "6/29/2025")
    )
    instr = mtp.build_instruction(text)
    assert (instr.advertiser, instr.client_code, instr.product_code) == ("4imprint", "PRNT", "PRTV")
    assert instr.search_suggestion == "4imprint"
    assert (instr.date_from_sql, instr.date_to_sql) == ("2025-05-26", "2025-06-29")
    assert (instr.date_from_display, instr.date_to_display) == ("5/26", "6/29")
    assert len(instr.periods) == 1
    assert [(s.isci, s.rotation_pct, s.duration_sec) for s in instr.periods[0].spots] == [
        ("PRNT-1234XX30A", 60.0, 30),
        ("PRNT-1235XX30B", 40.0, 30),
    ]


def test_every_segment_is_kept_as_its_own_period_in_file_order():
    # week 1 = one creative at 100%; the rest of the month = three at 33/33/34 — the
    # old "largest segment wins" rule dropped week 1 entirely.
    text = (
        HEADER
        + _row("PRNT-1234XX30A", "Promo A", 100, "5/26/2025", "6/1/2025")
        + _row("PRNT-1234XX30A", "Promo A", 33, "6/2/2025", "6/29/2025")
        + _row("PRNT-1235XX30B", "Promo B", 33, "6/2/2025", "6/29/2025")
        + _row("PRNT-1236XX30C", "Promo C", 34, "6/2/2025", "6/29/2025")
    )
    instr = mtp.build_instruction(text)
    assert [(p.date_from_display, p.date_to_display) for p in instr.periods] == [
        ("5/26", "6/1"),
        ("6/2", "6/29"),
    ]
    week1, rest = instr.periods
    assert [(s.isci, s.rotation_pct) for s in week1.spots] == [("PRNT-1234XX30A", 100.0)]
    assert [(s.isci, s.rotation_pct) for s in rest.spots] == [
        ("PRNT-1234XX30A", 33.0),
        ("PRNT-1235XX30B", 33.0),
        ("PRNT-1236XX30C", 34.0),
    ]
    # every spot carries ITS row's flight; the instruction window is the union
    assert [(s.isci, s.date_from_sql, s.date_to_sql) for s in instr.spots] == [
        ("PRNT-1234XX30A", "2025-05-26", "2025-06-01"),
        ("PRNT-1234XX30A", "2025-06-02", "2025-06-29"),
        ("PRNT-1235XX30B", "2025-06-02", "2025-06-29"),
        ("PRNT-1236XX30C", "2025-06-02", "2025-06-29"),
    ]
    assert (instr.date_from_sql, instr.date_to_sql) == ("2025-05-26", "2025-06-29")


def test_a_double_rendered_row_counts_once_within_its_segment():
    text = HEADER + _row("PRNT-1234XX30A", "Promo A", 100, "5/26/2025", "6/1/2025") * 2
    instr = mtp.build_instruction(text)
    assert len(instr.periods) == 1 and len(instr.periods[0].spots) == 1


def test_rows_on_a_second_page_are_read():
    page1 = HEADER + _row("PRNT-1234XX30A", "Promo A", 100, "5/26/2025", "6/1/2025")
    page2 = "Page 2 of 2\n" + _row("PRNT-1235XX30B", "Promo B", 100, "6/2/2025", "6/29/2025")
    instr = mtp.build_instruction(page1 + "\n" + page2)
    assert [p.date_from_display for p in instr.periods] == ["5/26", "6/2"]


def test_the_split_year_artifact_still_parses():
    text = HEADER + _row("PRNT-1234XX30A", "Promo A", 100, "5/26/2025", "6/29/202 5")
    instr = mtp.build_instruction(text)
    assert instr.periods[0].date_to_sql == "2025-06-29"


def test_no_rows_means_no_periods():
    instr = mtp.build_instruction(HEADER)
    assert instr.periods == [] and instr.date_from_sql is None
