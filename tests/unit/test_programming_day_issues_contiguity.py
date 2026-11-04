"""The Weekly Schedules copy tool refuses a day whose blocks are not contiguous.

Two real shapes, both of which keep the day at exactly 24:00:00 by span AND by sum of
durations (so a span/sum guard cannot see them):

* MMT 2026-11-04: one block slid +1438 frames (one pixel of the Weekly Schedule
  timeline) -> a 47.98 s gap before it and a 47.98 s overlap after it.
* DAL Saturdays from 2027-03-20: a one-hour block moved two hours earlier -> a one-hour
  overlap at 24:00 and a one-hour hole at 26:00.

`_day_issues` is what `_copy_station` runs on every source day before writing and on every
written day inside the transaction, so a bad source day is never carried forward.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from web.routes.programming import DAY_START, FRAMES_PER_HOUR, _day_issues  # noqa: E402

HALF = FRAMES_PER_HOUR // 2
PIXEL = 1438


def _lay(names_durs):
    """Contiguous day from (name, duration) pairs, starting at 06:00."""
    out, off = [], DAY_START
    for i, (name, dur) in enumerate(names_durs):
        out.append({"id": 1000 + i, "name": name, "offset": off, "duration": dur})
        off += dur
    return out


def _full_day():
    # 24 one-hour blocks: 06:00 .. 30:00
    return _lay([(f"B{i}", FRAMES_PER_HOUR) for i in range(24)])


def test_clean_day_has_no_issues():
    assert _day_issues(_full_day()) == []


def test_one_pixel_slide_is_refused_even_though_span_and_sum_stay_24h():
    day = _full_day()
    day[6]["offset"] += PIXEL  # the 12:00 block slides 47.98 s late
    assert day[-1]["offset"] + day[-1]["duration"] - day[0]["offset"] == 24 * FRAMES_PER_HOUR
    assert sum(b["duration"] for b in day) == 24 * FRAMES_PER_HOUR
    issues = _day_issues(day)
    assert any("gap" in m and "1438 frames" in m for m in issues), issues
    assert any("overlap of 1438 frames" in m for m in issues), issues


def test_dal_saturday_whole_hour_move_is_refused():
    # ... 22:00 Korean Drama (2h), 24:00 four Cantonese half hours, 26:00 Primetime News
    # (1h), 27:00 IYATO (1.5h), 28:30 IYATO FanFav (1.5h) -> 30:00
    day = _lay(
        [(f"B{i}", FRAMES_PER_HOUR) for i in range(16)]  # 06:00 .. 22:00
        + [("K- Korean Drama Saturday", 2 * FRAMES_PER_HOUR)]
        + [("C - News Talk AM", HALF), ("C - The Great Era AM", HALF)]
        + [("C - News Decoder REV", HALF), ("C - My Hong Kong Perspectives", HALF)]
        + [("M - Primetime News REV", FRAMES_PER_HOUR)]
        + [("M - If You Are The One REV", 3 * HALF), ("M - If You Are the One FanFavCol", 3 * HALF)]
    )
    assert _day_issues(day) == []
    news = next(b for b in day if b["name"] == "M - Primetime News REV")
    news["offset"] -= 2 * FRAMES_PER_HOUR  # moved from 26:00 to 24:00
    day.sort(key=lambda b: b["offset"])
    issues = _day_issues(day)
    assert any("overlap" in m for m in issues), issues
    assert any("gap of 1h00" in m for m in issues), issues


def test_universal_two_frame_weekend_overlap_is_tolerated():
    day = _full_day()
    day[6]["duration"] += 2  # the weekend 11:45 block is 2 frames long on every market
    assert _day_issues(day) == []
