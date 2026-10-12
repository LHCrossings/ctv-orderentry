"""Bookend pair assignment scopes breaks by the language airing at the break.

Lee (2026-10-12): "Imprenta PGE TS 26Q4 is a bookend order with both Mandarin
and Cantonese spots … I need to assign a bookend pair to the Cantonese spots and
a bookend pair to the Mandarin spots, and I have no way to do that in our
bookends utility." The utility assigned contract-wide. `_bookend_breaks` now
stamps every break with the language that airs at its (weekday, ORA) via the
day-aware window table — the Chinese ROS bonus line's breaks land in whichever
window they were placed — and the assign route keeps only the chosen language.
"""

import sys
from datetime import datetime
from pathlib import Path

_root = Path(__file__).parent.parent.parent
for _p in (_root, _root / "src"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from src.web.routes.orders import (  # noqa: E402
    _BOOKEND_UNCLASSIFIED,
    _bookend_breaks,
    _bookend_language_summary,
)

_FPS = 29.97
_CVC = 7


def _frames(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return round((int(h) * 3600 + int(m) * 60) * _FPS)


def _pair(day: str, hhmm: str, line_id: int, desc: str, tp_base: int) -> list:
    """A top/bottom bookend pair 90 s apart on `day` at `hhmm` (CVC)."""
    data = datetime.strptime(day, "%Y-%m-%d")
    return [
        {
            "line_id": line_id,
            "line_desc": desc,
            "market": _CVC,
            "tp_id": tp_base,
            "data": data,
            "ora": _frames(hhmm),
            "booking_code": 2,
        },
        {
            "line_id": line_id,
            "line_desc": desc,
            "market": _CVC,
            "tp_id": tp_base + 1,
            "data": data,
            "ora": _frames(hhmm) + round(90 * _FPS),
            "booking_code": 2,
        },
    ]


def _contract_rows() -> list:
    rows = []
    rows += _pair("2026-10-19", "19:30", 85706, "BOOKEND Cantonese News/Talk", 100)  # Mon
    rows += _pair("2026-10-19", "20:30", 85707, "BOOKEND Mandarin (News)", 200)  # Mon
    rows += _pair("2026-10-24", "22:30", 85709, "BNS Chinese ROS Bonus", 300)  # Sat → Mandarin
    rows += _pair("2026-10-20", "19:45", 85706, "BOOKEND Cantonese News/Talk", 400)  # Tue
    rows.sort(key=lambda r: (r["data"], r["ora"]))
    return rows


def test_breaks_are_stamped_with_the_language_airing_at_their_time():
    breaks, bad = _bookend_breaks(_contract_rows())
    assert bad == []
    assert [b["language"] for b in breaks] == ["Cantonese", "Mandarin", "Cantonese", "Mandarin"]
    assert all(len(b["rows"]) == 2 for b in breaks)
    # top first, bottom second — the pair's A/B order is the ORA order
    assert all(b["rows"][0]["ora"] < b["rows"][1]["ora"] for b in breaks)


def test_language_filter_keeps_only_that_language_including_the_ros_bonus_line():
    mandarin, bad = _bookend_breaks(_contract_rows(), "Mandarin")
    assert bad == []
    assert {b["rows"][0]["line_id"] for b in mandarin} == {85707, 85709}
    cantonese, _ = _bookend_breaks(_contract_rows(), "Cantonese")
    assert {b["rows"][0]["line_id"] for b in cantonese} == {85706}
    assert _bookend_breaks(_contract_rows(), "Vietnamese") == ([], [])


def test_a_bad_break_in_the_other_language_does_not_block_this_one():
    rows = _contract_rows()
    # a stray third spot in the Monday Cantonese break
    rows.append(
        {
            "line_id": 85706,
            "line_desc": "BOOKEND Cantonese News/Talk",
            "market": _CVC,
            "tp_id": 999,
            "data": datetime(2026, 10, 19),
            "ora": _frames("19:30") + 5000,
            "booking_code": 2,
        }
    )
    rows.sort(key=lambda r: (r["data"], r["ora"]))
    _, bad_all = _bookend_breaks(rows)
    assert [(n, c) for n, _, c in bad_all] == [(1, 3)]
    _, bad_c = _bookend_breaks(rows, "Cantonese")
    assert [(n, c) for n, _, c in bad_c] == [(1, 3)]
    _, bad_m = _bookend_breaks(rows, "Mandarin")
    assert bad_m == []


def test_unclassified_when_no_window_or_overlap():
    rows = _pair("2026-10-19", "03:00", 1, "ROS", 1)  # 03:00 raw frames: no CTV window
    breaks, _ = _bookend_breaks(rows)
    assert breaks[0]["language"] == _BOOKEND_UNCLASSIFIED


def test_summary_counts_breaks_and_lines_per_language():
    breaks, _ = _bookend_breaks(_contract_rows())
    summary = _bookend_language_summary(breaks)
    assert [s["language"] for s in summary] == ["Cantonese", "Mandarin"]
    cant = summary[0]
    assert (cant["break_count"], cant["spot_count"]) == (2, 4)
    assert cant["lines"] == [
        {"line_id": 85706, "description": "BOOKEND Cantonese News/Talk", "spot_count": 4}
    ]
    mand = summary[1]
    assert (mand["break_count"], mand["spot_count"]) == (2, 4)
    assert [ln["line_id"] for ln in mand["lines"]] == [85707, 85709]


def test_empty_rows():
    assert _bookend_breaks([]) == ([], [])
    assert _bookend_language_summary([]) == []
