"""Hiatus: the daily cap a line needs once days are excluded (contract 3089, Lee 11/20)."""
import datetime as dt
import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "hiatus_exclude", Path(__file__).parents[2] / "scripts/hiatus_exclude.py"
)
hx = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(hx)

START, END = dt.date(2026, 11, 30), dt.date(2026, 12, 27)
HIATUS = {dt.date(2026, 12, 24), dt.date(2026, 12, 25)}
MF = (1, 1, 1, 1, 1, 0, 0)
MSU = (1,) * 7


def test_m_f_line_needs_four_a_day_in_the_three_days_left():
    assert hx.needed_daily_cap(START, END, MF, HIATUS, 40, 10) == (4, [])


def test_m_su_line_fits_at_two_a_day():
    assert hx.needed_daily_cap(START, END, MSU, HIATUS, 40, 10) == (2, [])


def test_weekend_line_is_not_touched():
    assert hx.needed_daily_cap(START, END, (0, 0, 0, 0, 0, 1, 1), HIATUS, 24, 6) == (0, [])


def test_no_weekly_cap_spreads_the_total_over_the_weeks():
    assert hx.needed_daily_cap(START, END, MF, HIATUS, 40, 0) == (4, [])


def test_week_with_no_day_left_is_reported():
    thu_fri = (0, 0, 0, 1, 1, 0, 0)
    assert hx.needed_daily_cap(START, END, thu_fri, HIATUS, 8, 2) == (0, [dt.date(2026, 12, 21)])
