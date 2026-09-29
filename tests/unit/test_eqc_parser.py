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

from browser_automation.eqc_automation import _quarter_groups  # noqa: E402
from browser_automation.parsers.eqc_parser import parse_eqc_xlsx, repair_week_years  # noqa: E402

FIXTURE = _root / "tests/fixtures/eqc/TH Media_ EQC_2026-2027 Media Proposal.xlsx"


def test_season_crossing_new_year_rolls_the_january_columns_forward():
    order = parse_eqc_xlsx(str(FIXTURE))
    years = [d.year for d in order.week_dates]
    assert years == [2026] * 6 + [2027] * 6
    assert all(d.weekday() == 0 for d in order.week_dates), "every week-start is a Monday"
    assert order.week_dates[6] == dt.date(2027, 1, 11)
    assert len(order.repairs) == 6 and "01/11/2026 (Sunday)" in order.repairs[0]
    assert "read as 01/11/2027" in order.repairs[0]
    quarters = _quarter_groups(order.week_dates)
    assert [q["desc"].split()[-1] for q in quarters] == ["26Q4", "27Q1"]
    assert [q["code"] for q in quarters] == ["TH EQC 2610", "TH EQC 2701"]
    assert quarters[1]["start"] == dt.date(2027, 1, 11) and quarters[1]["end"] == dt.date(
        2027, 3, 28
    )
    assert order.flight_end == "03/28/2027"
    assert len(order.lines) == 9 and sum(ln.total_spots for ln in order.paid_lines) == 168


def _mutated(tmp_path, mutate) -> str:
    wb = openpyxl.load_workbook(FIXTURE)
    mutate(wb.active)
    out = io.BytesIO()
    wb.save(out)
    path = tmp_path / "mutated.xlsx"
    path.write_bytes(out.getvalue())
    return str(path)


def test_a_sheet_with_correct_years_needs_no_repair_and_parses_the_same(tmp_path):
    def fix(ws):
        for c in range(13, 19):  # M..R = the January-March columns
            v = ws.cell(8, c).value
            ws.cell(8, c).value = v.replace(year=2027)

    good = parse_eqc_xlsx(_mutated(tmp_path, fix))
    bad = parse_eqc_xlsx(str(FIXTURE))
    assert good.repairs == []
    assert good.week_dates == bad.week_dates
    assert [ln.week_spots for ln in good.lines] == [ln.week_spots for ln in bad.lines]


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
