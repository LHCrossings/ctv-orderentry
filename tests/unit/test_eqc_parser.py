"""EQC (TH Media) proposal reader. Lee 2026-09-29: the 2026-2027 proposal's January-March
week cells carried 2026 (a Sunday, months before the December column), so the gather
offered a 26Q1 contract dated 01/11/2026. Week columns run forward in time and every
week-start is a Monday; a date that steps backwards is rolled to the next year, and
anything that still is not a Monday, or falls outside the title's season, is refused."""

import datetime as dt
import io
import sys
from pathlib import Path

import openpyxl
import pytest

_root = Path(__file__).resolve().parents[2]
for p in [str(_root), str(_root / "browser_automation")]:
    if p not in sys.path:
        sys.path.insert(0, p)

from browser_automation.eqc_automation import _quarter_groups, build_quarter_lines  # noqa: E402
from browser_automation.parsers.eqc_parser import parse_eqc_xlsx, repair_week_years  # noqa: E402

FIXTURE = _root / "tests/fixtures/eqc/TH Media_ EQC_2026-2027 Media Proposal.xlsx"


def test_both_tables_are_read_and_new_year_columns_roll_forward():
    order = parse_eqc_xlsx(str(FIXTURE))
    assert order.tables == 2, "Oct-Mar table, then Apr-Sep below the first footer"
    years = [d.year for d in order.week_dates]
    assert years == [2026] * 6 + [2027] * 18
    assert all(d.weekday() == 0 for d in order.week_dates), "every week-start is a Monday"
    assert order.week_dates[6] == dt.date(2027, 1, 11) and order.week_dates[12] == dt.date(
        2027, 4, 5
    )
    assert len(order.repairs) == 18 and "01/11/2026 (Sunday)" in order.repairs[0]
    assert "read as 01/11/2027" in order.repairs[0]
    quarters = _quarter_groups(order.week_dates)
    assert [q["desc"].split()[-1] for q in quarters] == ["26Q4", "27Q1", "27Q2", "27Q3"]
    assert [q["code"] for q in quarters] == [
        "TH EQC 2610",
        "TH EQC 2701",
        "TH EQC 2704",
        "TH EQC 2707",
    ]
    assert quarters[3]["start"] == dt.date(2027, 7, 5) and quarters[3]["end"] == dt.date(
        2027, 9, 26
    )
    assert order.flight_end == "09/26/2027"
    assert len(order.lines) == 9, "the same nine programs in both tables merge into one line each"
    assert all(len(ln.week_spots) == 24 for ln in order.lines)
    assert sum(ln.total_spots for ln in order.paid_lines) == 14 * 24
    assert sum(ln.total_spots for ln in order.bonus_lines) == 14 * 24
    for q in quarters:
        specs = build_quarter_lines(order, {"cols": q["cols"]})
        assert len(specs) == 54 and sum(sp["total_spots"] for sp in specs) == 168
        assert sum(sp["rate"] * sp["total_spots"] for sp in specs) == 3900, (
            "matches the Gross Amount row"
        )


def _mutated(tmp_path, mutate) -> str:
    """Re-save the fixture with a change. The footer rows are formulas whose cached values
    openpyxl drops on save, so they are frozen to their computed numbers first (the sheet
    keeps footing unless the mutation itself breaks it)."""
    values = openpyxl.load_workbook(FIXTURE, data_only=True).active
    wb = openpyxl.load_workbook(FIXTURE)
    ws = wb.active
    for r in range(1, ws.max_row + 1):
        for c in range(1, ws.max_column + 1):
            v = ws.cell(r, c).value
            if isinstance(v, str) and v.startswith("="):
                ws.cell(r, c).value = values.cell(r, c).value
    mutate(ws)
    out = io.BytesIO()
    wb.save(out)
    path = tmp_path / "mutated.xlsx"
    path.write_bytes(out.getvalue())
    return str(path)


def test_a_sheet_with_correct_years_needs_no_repair_and_parses_the_same(tmp_path):
    def fix(ws):
        for c in range(13, 19):  # M..R = the January-March columns of table 1
            ws.cell(8, c).value = ws.cell(8, c).value.replace(year=2027)
        for c in range(6, 18):  # F..Q = every column of table 2
            ws.cell(27, c).value = ws.cell(27, c).value.replace(year=2027)

    good = parse_eqc_xlsx(_mutated(tmp_path, fix))
    bad = parse_eqc_xlsx(str(FIXTURE))
    assert good.repairs == []
    assert good.week_dates == bad.week_dates
    assert [ln.week_spots for ln in good.lines] == [ln.week_spots for ln in bad.lines]


def test_a_table_that_does_not_foot_is_refused(tmp_path):
    def drop_a_spot(ws):
        ws.cell(28, 6).value = 2  # Shanghai Primetime News, first April week: 3 -> 2

    with pytest.raises(ValueError, match="table 2: paid units row .* does not match"):
        parse_eqc_xlsx(_mutated(tmp_path, drop_a_spot))

    def wrong_gross(ws):
        ws.cell(21, 6).value = 1200

    with pytest.raises(ValueError, match="table 1: Gross Amount row totals"):
        parse_eqc_xlsx(_mutated(tmp_path, wrong_gross))

    def bonus_row_off(ws):
        ws.cell(38, 8).value = 13

    with pytest.raises(ValueError, match="table 2: bonus units row"):
        parse_eqc_xlsx(_mutated(tmp_path, bonus_row_off))


def test_a_second_table_that_is_deleted_leaves_one_table(tmp_path):
    def erase_table_two(ws):
        ws.delete_rows(26, 16)

    order = parse_eqc_xlsx(_mutated(tmp_path, erase_table_two))
    assert order.tables == 1 and len(order.week_dates) == 12 and len(order.repairs) == 6


def test_a_week_column_that_is_not_a_monday_is_refused(tmp_path):
    def tuesday(ws):
        ws.cell(8, 6).value = ws.cell(8, 6).value.replace(day=6)  # 10/06/2026

    with pytest.raises(ValueError, match="Tuesday, not a Monday"):
        parse_eqc_xlsx(_mutated(tmp_path, tuesday))


def test_a_repaired_date_outside_the_title_season_is_refused(tmp_path):
    def wrong_title(ws):
        ws.cell(1, 4).value = "2026-2026   Flight schedule"

    with pytest.raises(ValueError, match="outside the sheet's season 2026-2026"):
        parse_eqc_xlsx(_mutated(tmp_path, wrong_title))


def test_repair_rolls_forward_as_many_years_as_it_takes():
    dates = [dt.date(2026, 12, 21), dt.date(2024, 1, 4)]  # two years stale; 01/04/2027 is a Monday
    fixed, notes = repair_week_years(dates)
    assert fixed[0] == dt.date(2026, 12, 21)
    assert fixed[1] > fixed[0] and fixed[1].weekday() == 0 and fixed[1] == dt.date(2027, 1, 4)
    assert len(notes) == 1
    assert repair_week_years([]) == ([], [])
