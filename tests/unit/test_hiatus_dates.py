"""Hiatus date entry: what an operator types and what an order prints (Lee 11/20)."""

import datetime as dt
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from browser_automation.hiatus import (  # noqa: E402
    hiatus_mentions,
    mentioned_dates,
    parse_hiatus_dates,
)

S, E = dt.date(2026, 11, 30), dt.date(2026, 12, 27)
D24, D25 = dt.date(2026, 12, 24), dt.date(2026, 12, 25)
TODAY = dt.date(2026, 11, 20)


@pytest.mark.parametrize(
    "text",
    ["12/24-12/25", "12/24 and 12/25", "12/24, 12/25", "12/24/26 - 12/25/2026", "2026-12-24 2026-12-25",
     "12/24 thru 12/25", "12.24/25", "12/24-25", "Hiatus 12/24 and 12/25"],
)
def test_two_day_hiatus_spellings(text):
    assert parse_hiatus_dates(text, S, E, TODAY) == [D24, D25]


def test_blank_and_none_mean_no_hiatus():
    assert parse_hiatus_dates("", S, E, TODAY) == []
    assert parse_hiatus_dates("none", S, E, TODAY) == []


def test_text_without_a_date_is_refused():
    with pytest.raises(ValueError):
        parse_hiatus_dates("christmas", S, E, TODAY)


def test_range_is_expanded_and_backwards_range_refused():
    assert len(parse_hiatus_dates("12/22-12/26", S, E, TODAY)) == 5
    with pytest.raises(ValueError):
        parse_hiatus_dates("12/25-12/24", S, E, TODAY)


def test_year_comes_from_the_flight_when_it_crosses_new_year():
    got = parse_hiatus_dates("1/1", dt.date(2026, 12, 14), dt.date(2027, 1, 10), TODAY)
    assert got == [dt.date(2027, 1, 1)]


def test_no_flight_means_the_next_occurrence():
    assert parse_hiatus_dates("12/24", today=TODAY) == [D24]
    assert parse_hiatus_dates("1/5", today=TODAY) == [dt.date(2027, 1, 5)]


def test_order_wording_is_found_and_only_dates_after_it_count():
    text = "Flight 11/30-12/27\nDEC. PLEASE DO NOT AIR ON 12.24/25\nTotal 100"
    m = hiatus_mentions(text)
    assert m == ["DEC. PLEASE DO NOT AIR ON 12.24/25"]
    assert mentioned_dates(m, S, E, TODAY) == [D24, D25]
    # a flight range printed before the wording on the same line is not a hiatus
    assert mentioned_dates(["11/30-12/27 Hiatus 12/24-12/25"], S, E, TODAY) == [D24, D25]
    assert mentioned_dates(["hiatus tbd"], S, E, TODAY) == []
