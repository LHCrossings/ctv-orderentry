"""RPM daypart normalisation: wrapped end times on any daypart code, dialect kept."""

import sys
from pathlib import Path

import pytest

_root = Path(__file__).parent.parent.parent
for _p in (_root, _root / "src", _root / "browser_automation"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from browser_automation.parsers.rpm_parser import (  # noqa: E402
    _SPLIT_TIME_RE,
    _normalize_daypart_name,
)


@pytest.mark.parametrize(
    "line, start, code",
    [
        ("MTuWThF 11:30P- LF $57.00 C 30 2 3 2", "11:30P", "LF"),
        ("MTuWThFSaSu 6:00A- AV $0.00 C 30 4 4 4", "6:00A", "AV"),
        ("MTuWThF 11:00A- RT $46.00 C 30 3 3 3", "11:00A", "RT"),
    ],
)
def test_split_time_any_daypart_code(line, start, code):
    m = _SPLIT_TIME_RE.search(line)
    assert m and m.group(1) == start and m.group(2) == code


def test_split_time_not_on_complete_range():
    assert _SPLIT_TIME_RE.search("MTuWThF 6:00A-7:00A RT $37.00 C 30 3") is None


@pytest.mark.parametrize(
    "program, daypart, code",
    [
        ("MTuWThF 11:30p-12:00a CANTONESE NEWS", "M-F 11:30p-12m Cantonese", "C"),
        ("MTuWThF 8:00p-9:00p MANDARIN NEWS", "M-F 8p-9p Mandarin", "M"),
        ("MTuWThF 6:00a-7:00a CHINESE NEWS", "M-F 6a-7a Chinese", "M/C"),
        ("MTuWThFSaSu 6:00a-12:00a ASIAN ROTATION", "M-Su 6a-12m Bonus MCV ROS", "L"),
        ("MTuWThF 11:00a-12:00p VIETNAMESE NEWS", "M-F 11a-12n Vietnamese", "V"),
    ],
)
def test_dialect_preserved(program, daypart, code):
    assert _normalize_daypart_name(program) == (daypart, code)


def test_mcv_ros_guesses_L():
    from browser_automation.line_language import guess_language

    assert guess_language("(Line 6) M-Su Bonus MCV ROS") == "L"
    assert guess_language("M-F 6a-7a Chinese") == "M/C"
