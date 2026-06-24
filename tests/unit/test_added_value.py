"""
Tests for the Hoffman Lewis Added Value helper (browser_automation/added_value.py).
"""

import sys
from datetime import date
from pathlib import Path

import pytest

# Add browser_automation + repo root to path
_root = Path(__file__).parent.parent.parent
for _p in (_root, _root / "browser_automation"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from browser_automation.added_value import (
    SPOT_CODE_AV,
    add_av_line,
    av_total_spots,
    format_languages,
    widest_window,
)


class TestAvTotalSpots:
    @pytest.mark.parametrize("start,end,expected", [
        (date(2026, 7, 7), date(2026, 8, 2), 27),   # 7/7-8/2
        (date(2026, 7, 7), date(2026, 7, 10), 4),    # 7/7-7/10
        (date(2026, 7, 7), date(2026, 7, 27), 21),   # 7/7-7/27
        (date(2026, 9, 1), date(2026, 9, 30), 30),   # full Sep
        (date(2026, 7, 7), date(2026, 7, 7), 1),     # single day
    ])
    def test_one_spot_per_calendar_day_inclusive(self, start, end, expected):
        assert av_total_spots(start, end) == expected


class TestWidestWindow:
    def test_spans_earliest_start_to_latest_end(self):
        assert widest_window(["4:00p-7:00p", "4:00p-6:00p"]) == "16:00-19:00"

    def test_unparseable_falls_back_to_full_day(self):
        assert widest_window(["garbage"]) == "06:00-23:59"


class TestFormatLanguages:
    def test_single_language_full_name(self):
        assert format_languages(["FILIPINO", "FILIPINO"]) == "Filipino"

    def test_multiple_languages_comma_abbreviations(self):
        assert format_languages(["MANDARIN", "CANTONESE", "VIETNAMESE"]) == "M,C,V"

    def test_order_preserved_and_deduped(self):
        assert format_languages(["CANTONESE", "MANDARIN", "CANTONESE"]) == "C,M"

    def test_unrecognized_tokens_filtered_out(self):
        # "VARIOUS"/"NEWS" are not languages → only FILIPINO survives
        assert format_languages(["FILIPINO", "VARIOUS", "NEWS"]) == "Filipino"

    def test_no_recognized_languages_returns_empty(self):
        assert format_languages(["VARIOUS", "NEWS"]) == ""


class _FakeClient:
    def __init__(self):
        self.calls = []

    def add_contract_line(self, **kwargs):
        self.calls.append(kwargs)
        return 999


class TestAddAvLine:
    def _call(self, languages, fallback="16:00-19:00"):
        client = _FakeClient()
        line_id = add_av_line(
            client,
            contract_id=1,
            market="SFO",
            date_from=date(2026, 7, 7),
            date_to=date(2026, 8, 2),
            duration="00:00:30:00",
            separation=(25, 0, 0),
            languages=languages,
            fallback_time=fallback,
        )
        return line_id, client.calls[0]

    def test_av_line_uses_correct_spot_type_and_scheduling(self):
        _, kw = self._call(["FILIPINO"])
        assert kw["booking_code"] == SPOT_CODE_AV == 1
        assert kw["is_added_value"] is True
        assert kw["days"] == "M-Su"
        assert kw["max_daily_run"] == 1
        assert kw["total_spots"] == 27
        assert kw["rate"] == 0.0

    def test_description_lists_single_language(self):
        _, kw = self._call(["FILIPINO"])
        assert kw["description"] == "M-Su Filipino AV ROS"

    def test_description_lists_multiple_language_abbreviations(self):
        _, kw = self._call(["MANDARIN", "CANTONESE"])
        assert kw["description"] == "M-Su M,C AV ROS"

    def test_description_falls_back_to_time_when_no_language(self):
        _, kw = self._call(["VARIOUS"], fallback="16:00-19:00")
        assert kw["description"] == "M-Su 16:00-19:00 AV ROS"
