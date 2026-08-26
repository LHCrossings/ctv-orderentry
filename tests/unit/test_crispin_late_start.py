"""Crispin late-start replanning — flight dates AND max-per-day follow the answer.

The BAAQMD IO arrived on 8/10/26 for a flight starting 8/10/26, so entry has to
ask what date the order should actually start. A later start does NOT reduce the
week's spot count — it compresses those spots into fewer days, so the truncated
first week needs a HIGHER max-per-day than the full weeks behind it.

`add_contract_line`'s auto-calculation cannot see that: it divides by the day
PATTERN's width (M-F → 5) with no idea the line opens on a Thursday. So
`_plan_ranges` computes the cap per range and, when the short week and the full
weeks disagree, splits one IO line into two Etere lines.
"""

import sys
from datetime import date
from pathlib import Path

import pytest

_root = Path(__file__).parent.parent.parent
for _p in (_root, _root / "browser_automation"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from browser_automation.crispin_automation import _active_days, _plan_ranges  # noqa: E402

# One consolidated range as EtereClient.consolidate_weeks emits it: 4 spots a week
# for 5 weeks from Monday 8/10, ending Sunday 9/13.
FIVE_WEEKS = [
    {"start_date": "08/10/2026", "end_date": "09/13/2026", "spots_per_week": 4, "weeks": 5}
]
END = date(2026, 9, 13)


def _plan(days, start, ranges=None, end=END):
    return _plan_ranges(ranges or FIVE_WEEKS, days, start, end)


# ── On time: unchanged behaviour ─────────────────────────────────────────────


@pytest.mark.parametrize("days,expected_cap", [("M-F", 1), ("M-Su", 1)])
def test_on_time_start_is_one_range_at_the_pattern_cap(days, expected_cap):
    got, notes = _plan(days, date(2026, 8, 10))
    assert notes == []
    assert len(got) == 1
    assert (got[0]["date_from"], got[0]["date_to"]) == (date(2026, 8, 10), END)
    assert (got[0]["spots_per_week"], got[0]["weeks"]) == (4, 5)
    assert got[0]["max_daily"] == expected_cap


# ── A truncated first week that needs a higher cap → its own line ────────────


def test_thursday_start_splits_an_m_f_line_and_raises_its_cap():
    """Thu 8/13: only Thu+Fri remain of that M-F week, so 4 spots need 2/day —
    while the full weeks behind it still only need 1/day."""
    got, notes = _plan("M-F", date(2026, 8, 13))
    assert notes == []
    assert len(got) == 2

    short, rest = got
    assert (short["date_from"], short["date_to"]) == (date(2026, 8, 13), date(2026, 8, 16))
    assert (short["spots_per_week"], short["weeks"]) == (4, 1)
    assert short["max_daily"] == 2
    assert "short week" in short["tag"]

    assert (rest["date_from"], rest["date_to"]) == (date(2026, 8, 17), END)
    assert (rest["spots_per_week"], rest["weeks"]) == (4, 4)
    assert rest["max_daily"] == 1

    assert _spots(got) == 20  # nothing lost


def test_thursday_start_does_not_split_an_m_su_line():
    """Thu–Sun is still 4 days for 4 spots, so the cap is unchanged at 1/day and
    splitting the line would be pointless churn."""
    got, notes = _plan("M-Su", date(2026, 8, 13))
    assert notes == []
    assert len(got) == 1
    assert got[0]["date_from"] == date(2026, 8, 13)
    assert got[0]["max_daily"] == 1
    assert _spots(got) == 20


def test_a_single_week_range_keeps_its_short_week_cap_without_splitting():
    one_week = [
        {"start_date": "08/10/2026", "end_date": "08/16/2026", "spots_per_week": 4, "weeks": 1}
    ]
    got, notes = _plan("M-F", date(2026, 8, 13), ranges=one_week, end=date(2026, 8, 16))
    assert notes == []
    assert len(got) == 1
    assert got[0]["max_daily"] == 2
    assert _spots(got) == 4


def test_a_sunday_start_on_a_seven_day_line_stacks_the_week_on_one_day():
    got, _ = _plan("M-Su", date(2026, 8, 16))
    assert got[0]["date_from"] == got[0]["date_to"] == date(2026, 8, 16)
    assert got[0]["max_daily"] == 4


# ── Spots the later start makes undeliverable ───────────────────────────────


def test_a_saturday_start_drops_an_m_f_week_and_says_so():
    """No M-F day remains in the week of 8/10 on or after Sat 8/15, so that
    week's 4 spots cannot air. Drop them loudly rather than silently rescheduling."""
    got, notes = _plan("M-F", date(2026, 8, 15))
    assert len(notes) == 1
    assert "4 spot(s) dropped" in notes[0]
    assert "no M-F day left" in notes[0]
    assert len(got) == 1
    assert (got[0]["date_from"], got[0]["weeks"]) == (date(2026, 8, 17), 4)
    assert _spots(got) == 16


def test_whole_weeks_before_the_new_start_are_dropped_with_a_count():
    got, notes = _plan("M-Su", date(2026, 8, 24))
    assert len(notes) == 1
    assert "8 spot(s) dropped" in notes[0]  # the 8/10 and 8/17 weeks
    assert "2 whole week(s)" in notes[0]
    assert got[0]["date_from"] == date(2026, 8, 24)
    assert _spots(got) == 12


def test_a_range_entirely_before_the_new_start_is_dropped():
    got, notes = _plan("M-Su", date(2026, 9, 20))
    assert got == []
    assert len(notes) == 1
    assert "20 spot(s) dropped" in notes[0]


# ── Invariants ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize("days", ["M-F", "M-Su", "M-Sa", "Sa-Su"])
@pytest.mark.parametrize("offset", range(14))
def test_cap_is_always_at_least_one_and_dates_stay_inside_the_flight(days, offset):
    from datetime import timedelta

    start = date(2026, 8, 10) + timedelta(days=offset)
    got, _ = _plan(days, start)
    for r in got:
        assert r["max_daily"] >= 1
        assert start <= r["date_from"] <= r["date_to"] <= END


@pytest.mark.parametrize("days", ["M-F", "M-Su"])
@pytest.mark.parametrize("offset", range(5))  # Mon–Fri starts lose nothing
def test_a_start_inside_the_working_week_never_loses_spots(days, offset):
    from datetime import timedelta

    got, notes = _plan(days, date(2026, 8, 10) + timedelta(days=offset))
    assert notes == []
    assert _spots(got) == 20


def test_every_range_can_physically_hold_its_spots():
    """cap × available days must cover the week's spots — otherwise Etere is
    handed a line it can never fill."""
    from datetime import timedelta

    from browser_automation.etere_direct_client import parse_day_bits

    for days in ("M-F", "M-Su", "M-Sa"):
        bits = parse_day_bits(days)
        for offset in range(14):
            start = date(2026, 8, 10) + timedelta(days=offset)
            for r in _plan(days, start)[0]:
                first_week_end = min(r["date_from"] + timedelta(days=6), r["date_to"])
                avail = _active_days(r["date_from"], first_week_end, bits)
                assert r["max_daily"] * avail >= r["spots_per_week"], (
                    f"{days} from {start}: {r['spots_per_week']}/wk needs more than "
                    f"{r['max_daily']}/day over {avail} day(s)"
                )


# ── _active_days ─────────────────────────────────────────────────────────────


def test_active_days_counts_only_the_lines_own_days():
    from browser_automation.etere_direct_client import parse_day_bits

    mf = parse_day_bits("M-F")
    assert _active_days(date(2026, 8, 10), date(2026, 8, 16), mf) == 5  # full week
    assert _active_days(date(2026, 8, 13), date(2026, 8, 16), mf) == 2  # Thu+Fri
    assert _active_days(date(2026, 8, 15), date(2026, 8, 16), mf) == 0  # Sat+Sun
    weekend = parse_day_bits("Sa-Su")
    assert _active_days(date(2026, 8, 10), date(2026, 8, 16), weekend) == 2


def _spots(ranges):
    return sum(r["spots_per_week"] * r["weeks"] for r in ranges)


# ── The real order, on the real start date Lee chose ─────────────────────────

FIXTURE = str(_root / "tests" / "fixtures" / "crispin" / "crispin_io_212735.pdf")


def test_the_real_io_replanned_for_the_wednesday_8_12_start(real_pdfplumber):
    """Lee's actual call for order 212735: start Wed 8/12 instead of Mon 8/10.

    The three M-F lines each gain a short-week line at 2/day (Wed–Fri is 3 days
    for 4 spots); the M-Su lines don't split because Wed–Sun still holds 4 spots
    at 1/day. 16 Etere lines become 19 and **no spot is lost**.
    """
    from browser_automation.crispin_automation import _line_plan
    from browser_automation.parsers.crispin_parser import parse_crispin_pdf

    order = parse_crispin_pdf(FIXTURE)
    plan = _line_plan(order, date(2026, 8, 12), order.flight_end)

    lines = [(desc, r) for _ln, _d, _t, desc, ranges, _n in plan for r in ranges]
    assert len(lines) == 19
    assert all(not notes for *_x, notes in [(p[3], p[5]) for p in plan]), (
        "no spots should be undeliverable"
    )
    assert sum(r["spots_per_week"] * r["weeks"] for _d, r in lines) == 323

    # Every line now opens on 8/12, never the IO's 8/10.
    assert min(r["date_from"] for _d, r in lines) == date(2026, 8, 12)

    short = [(d, r) for d, r in lines if r["tag"]]
    assert len(short) == 3
    for desc, r in short:
        assert "M-F" in desc or "Cantonese ROS" in desc  # the M-F dayparts
        assert (r["date_from"], r["date_to"]) == (date(2026, 8, 12), date(2026, 8, 16))
        assert r["max_daily"] == 2 and r["weeks"] == 1
        assert "3 of 5 day(s)" in r["tag"]

    # The M-Su lines keep one range for the 4/wk block, still capped at 1/day.
    m_su = [(d, r) for d, r in lines if r["date_from"] == date(2026, 8, 12) and not r["tag"]]
    assert len(m_su) == 5
    assert {r["max_daily"] for _d, r in m_su} == {1}
