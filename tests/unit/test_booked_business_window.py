"""Booked Business date window — month mode vs air-date range mode.

Aki (via Lee, 2026-09-09) asked for a calendar-date filter on the Booked Business
report: "what aired Aug 12–20", next to the existing month report that applies
broadcast bounds to Broadcast contracts and calendar bounds to Calendar ones.

`_bb_report_window` is the one place that decides the window both SQL queries
(airtime + production charges) use. Month mode must stay exactly what the page
has always shown; range mode collapses both bound pairs to the same dates so the
CASE on CENTROMEDIA in the query becomes a no-op.
"""

import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.web.routes.orders import _bb_report_window  # noqa: E402


def test_month_mode_broadcast_and_calendar_bounds():
    # Aug 1 2026 is a Saturday → broadcast August starts Mon Jul 27;
    # Sep 1 2026 is a Tuesday → broadcast September starts Mon Aug 31.
    w = _bb_report_window(2026, 8)
    assert w["mode"] == "month"
    assert (w["bcast_start"], w["bcast_end"]) == (date(2026, 7, 27), date(2026, 8, 30))
    assert (w["cal_start"], w["cal_end"]) == (date(2026, 8, 1), date(2026, 8, 31))
    assert w["month_label"] == "August 2026"
    assert w["bcast_bounds"] == "Jul 27 – Aug 30, 2026"
    assert w["cal_bounds"] == "Aug 1 – Aug 31, 2026"
    assert w["range_label"] is None


def test_month_mode_december_rolls_the_year():
    w = _bb_report_window(2026, 12)
    # Jan 1 2027 is a Friday → broadcast January starts Mon Dec 28 2026.
    assert w["bcast_end"] == date(2026, 12, 27)
    assert w["cal_end"] == date(2026, 12, 31)


def test_range_mode_applies_one_window_to_every_contract():
    w = _bb_report_window(2026, 8, date(2026, 8, 12), date(2026, 8, 20))
    assert w["mode"] == "range"
    # Broadcast and Calendar bounds are identical, so the query's CASE on
    # CENTROMEDIA cannot change which spots are counted.
    assert w["bcast_start"] == w["cal_start"] == date(2026, 8, 12)
    assert w["bcast_end"] == w["cal_end"] == date(2026, 8, 20)
    assert w["month_label"] == w["range_label"] == "Aug 12 – Aug 20, 2026"


def test_range_mode_may_cross_months_and_ignores_year_month_args():
    w = _bb_report_window(2020, 1, date(2026, 8, 25), date(2026, 9, 5))
    assert w["bcast_start"] == date(2026, 8, 25)
    assert w["cal_end"] == date(2026, 9, 5)
    assert w["range_label"] == "Aug 25 – Sep 5, 2026"


@pytest.mark.parametrize("df,dt", [(date(2026, 8, 12), None), (None, date(2026, 8, 20))])
def test_range_mode_needs_both_dates(df, dt):
    with pytest.raises(ValueError):
        _bb_report_window(2026, 8, df, dt)


def test_range_mode_rejects_reversed_dates():
    with pytest.raises(ValueError):
        _bb_report_window(2026, 8, date(2026, 8, 20), date(2026, 8, 12))
