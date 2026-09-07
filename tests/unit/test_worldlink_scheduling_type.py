"""Every WorldLink line enters as Rotation (PRENOTAZIONE=1) — paid and bonus, CTV and DAL.

Lee, 2026-09-07: Priority first-fit stacked WL spots into the first eligible show and
blocked later orders. This pins the three add_contract_line call sites to Rotation.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock

_root = Path(__file__).parent.parent.parent
for _p in (_root, _root / "src", _root / "browser_automation"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import browser_automation.worldlink_automation as wl  # noqa: E402


def _line(rate, number=1):
    return {
        "line_number": number,
        "days_of_week": "M-Su",
        "from_time": "6:00a",
        "to_time": "11:59p",
        "time_range": "6:00a-11:59p",
        "duration": "30",
        "spots": 10,
        "total_spots": 40,
        "start_date": "09/07/2026",
        "end_date": "10/04/2026",
        "rate": rate,
    }


def _calls(fn, lines):
    client = MagicMock()
    client.add_contract_line.return_value = 1
    fn(client, lines, separation=(15, 0, 0))
    return client.add_contract_line.call_args_list


def test_constant_is_rotation():
    assert wl.WL_SCHEDULING_TYPE == 1


def test_crossings_lines_paid_and_bonus_are_rotation():
    calls = _calls(wl._add_crossings_lines_direct, [_line(25.0, 1), _line(0.0, 2)])
    # 2 lines x (NYC + 8 zero-rate markets)
    assert len(calls) == 2 * (1 + len(wl.CROSSINGS_ZERO_MARKETS))
    assert {c.kwargs["scheduling_type"] for c in calls} == {1}
    assert {c.kwargs["booking_code"] for c in calls} == {2, 10}


def test_asian_lines_paid_and_bonus_are_rotation():
    calls = _calls(wl._add_asian_lines_direct, [_line(25.0, 1), _line(0.0, 2)])
    assert len(calls) == 2
    assert {c.kwargs["scheduling_type"] for c in calls} == {1}


def test_no_priority_call_site_remains():
    import inspect

    src = inspect.getsource(wl)
    assert "scheduling_type=0" not in src
