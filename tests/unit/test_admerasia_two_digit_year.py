"""Admerasia IO dates: the 11/5/2026 McDonald's APA Next IOs print ``Order Date: 11/5/26``
(two-digit year) while the campaign period keeps four digits; the gather crashed on all
three files with "time data '11/5/26' does not match format '%m/%d/%Y'". Every date
site reads both shapes through one helper.
"""

import sys
from datetime import date
from pathlib import Path

import pytest

_root = Path(__file__).resolve().parents[2]
for _p in (_root, _root / "browser_automation"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from browser_automation.parsers.admerasia_parser import (  # noqa: E402
    _calculate_week_starts,
    _extract_order_date,
    _parse_mdy,
)


@pytest.mark.parametrize(
    "text, expected",
    [
        ("11/5/26", date(2026, 11, 5)),
        ("11/5/2026", date(2026, 11, 5)),
        ("11/05/2026", date(2026, 11, 5)),
        (" 1/2/27 ", date(2027, 1, 2)),
    ],
)
def test_parse_mdy_reads_two_and_four_digit_years(text, expected):
    assert _parse_mdy(text) == expected


def test_parse_mdy_refuses_anything_else():
    with pytest.raises(ValueError, match="cannot read date"):
        _parse_mdy("2026-11-05")
    with pytest.raises(ValueError):
        _parse_mdy("13/5/26")


def test_order_date_line_with_two_digit_year():
    header = "Order Number: 34-MD10-2611CT ISCI ISCI\nOrder Date: 11/5/26\nVersion: Original"
    assert _extract_order_date(header) == date(2026, 11, 5)
    assert _extract_order_date("Order Date: 11/5/2026") == date(2026, 11, 5)


def test_week_starts_accept_either_year_shape():
    four = _calculate_week_starts("11/15/2026 - 12/27/2026")
    two = _calculate_week_starts("11/15/26 - 12/27/26")
    assert four == two
    assert four[0] == date(2026, 11, 15) and four[-1] == date(2026, 12, 27)
    assert len(four) == 7
