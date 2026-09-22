"""RPM: the CPE fallback yields the bare estimate number (Muckleshoot 11062, 2026-09-22)."""

import sys
from pathlib import Path

import pytest

_root = Path(__file__).parent.parent.parent
for _p in (_root, _root / "src", _root / "browser_automation"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from browser_automation.parsers.rpm_parser import _estimate_from_cpe  # noqa: E402


@pytest.mark.parametrize(
    "token, expected",
    [
        ("/MC/11062", "11062"),
        ("wwtdaa/asian/9711", "9711"),
        ("10985", "10985"),
        ("MUCK/TV/10904/", "10904"),
        ("LNY", "LNY"),
    ],
)
def test_last_numeric_segment(token, expected):
    assert _estimate_from_cpe(token) == expected
