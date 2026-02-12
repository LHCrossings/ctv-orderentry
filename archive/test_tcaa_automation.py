"""
Test Suite for TCAA Browser Automation Module

Tests business logic functions (pure functions) without requiring browser automation.
Integration tests for browser automation would require a live Etere instance.
"""

import pytest
from tcaa_automation import (
    parse_start_time,
    parse_end_time,
    count_active_days,
    apply_sunday_6_7a_rule,
    convert_days_to_etere_checkboxes,
    get_language_block_prefixes,
    BonusLineInput,
)


# ============================================================================
# TIME PARSING TESTS
# ============================================================================

class TestTimeParser:
    """Test time parsing functions."""
    
    def test_parse_start_time_morning(self):
        """Test parsing morning start times."""
        assert parse_start_time("6:00a-7:00a") == "06:00"
        assert parse_start_time("8:00a-10:00a") == "08:00"
        assert parse_start_time("11:00a-1:00p") == "11:00"
    
    def test_parse_start_time_afternoon(self):
        """Test parsing afternoon/evening start times."""
        assert parse_start_time("1:00p-3:00p") == "13:00"
        assert parse_start_time("7:00p-11:00p") == "19:00"
        assert parse_start_time("11:00p-12:00a") == "23:00"
    
    def test_parse_start_time_midnight(self):
        """Test parsing midnight start time."""
        assert parse_start_time("12:00a-1:00a") == "00:00"
    
    def test_parse_start_time_noon(self):
        """Test parsing noon start time."""
        assert parse_start_time("12:00p-1:00p") == "12:00"
    
    def test_parse_end_time_morning(self):
        """Test parsing morning end times."""
        assert parse_end_time("6:00a-7:00a") == "07:00"
        assert parse_end_time("8:00a-10:00a") == "10:00"
    
    def test_parse_end_time_afternoon(self):
        """Test parsing afternoon/evening end times."""
        assert parse_end_time("1:00p-3:00p") == "15:00"
        assert parse_end_time("7:00p-11:00p") == "23:00"
    
    def test_parse_end_time_midnight_caps_at_2359(self):
        """Test that midnight end time caps at 23:59."""
        assert parse_end_time("7:00p-12:00a") == "23:59"
        assert parse_end_time("11:00p-12:00a") == "23:59"
    
    def test_parse_end_time_noon(self):
        """Test parsing noon end time."""
        assert parse_end_time("11:00a-12:00p") == "12:00"


# ============================================================================
# DAY PATTERN TESTS
# ============================================================================

class TestDayPatterns:
    """Test day pattern handling functions."""
    
    def test_count_active_days_standard_patterns(self):
        """Test counting days in standard patterns."""
        assert count_active_days("M-Su") == 7
        assert count_active_days("M-F") == 5
        assert count_active_days("M-Sa") == 6
        assert count_active_days("Sa-Su") == 2
    
    def test_count_active_days_unknown_defaults_to_7(self):
        """Test that unknown patterns default to 7 days."""
        assert count_active_days("Tu-Th") == 7
    
    def test_sunday_6_7a_rule_removes_sunday_from_m_su(self):
        """Test Sunday removal from M-Su pattern for 6-7a time slot."""
        adjusted_days, adjusted_count = apply_sunday_6_7a_rule("M-Su", "6:00a-7:00a")
        assert adjusted_days == "M-Sa"
        assert adjusted_count == 6
    
    def test_sunday_6_7a_rule_converts_sa_su_to_sa(self):
        """Test Sunday removal from Sa-Su pattern for 6-7a time slot."""
        adjusted_days, adjusted_count = apply_sunday_6_7a_rule("Sa-Su", "6:00a-7:00a")
        assert adjusted_days == "Sa"
        assert adjusted_count == 1
    
    def test_sunday_6_7a_rule_no_change_for_other_times(self):
        """Test no adjustment for times other than 6-7a."""
        adjusted_days, adjusted_count = apply_sunday_6_7a_rule("M-Su", "7:00a-8:00a")
        assert adjusted_days == "M-Su"
        assert adjusted_count == 7
    
    def test_sunday_6_7a_rule_no_change_when_no_sunday(self):
        """Test no adjustment when Sunday not in pattern."""
        adjusted_days, adjusted_count = apply_sunday_6_7a_rule("M-F", "6:00a-7:00a")
        assert adjusted_days == "M-F"
        assert adjusted_count == 5
    
    def test_convert_days_to_etere_checkboxes_all_days(self):
        """Test M-Su converts to all checkboxes True."""
        checkboxes = convert_days_to_etere_checkboxes("M-Su")
        assert all(checkboxes.values())
    
    def test_convert_days_to_etere_checkboxes_weekdays(self):
        """Test M-F converts to weekday checkboxes only."""
        checkboxes = convert_days_to_etere_checkboxes("M-F")
        assert checkboxes["Mon"] is True
        assert checkboxes["Tue"] is True
        assert checkboxes["Wed"] is True
        assert checkboxes["Thu"] is True
        assert checkboxes["Fri"] is True
        assert checkboxes["Sat"] is False
        assert checkboxes["Sun"] is False
    
    def test_convert_days_to_etere_checkboxes_weekend(self):
        """Test Sa-Su converts to weekend checkboxes only."""
        checkboxes = convert_days_to_etere_checkboxes("Sa-Su")
        assert checkboxes["Mon"] is False
        assert checkboxes["Tue"] is False
        assert checkboxes["Wed"] is False
        assert checkboxes["Thu"] is False
        assert checkboxes["Fri"] is False
        assert checkboxes["Sat"] is True
        assert checkboxes["Sun"] is True
    
    def test_convert_days_to_etere_checkboxes_mon_through_sat(self):
        """Test M-Sa converts correctly."""
        checkboxes = convert_days_to_etere_checkboxes("M-Sa")
        assert checkboxes["Mon"] is True
        assert checkboxes["Tue"] is True
        assert checkboxes["Wed"] is True
        assert checkboxes["Thu"] is True
        assert checkboxes["Fri"] is True
        assert checkboxes["Sat"] is True
        assert checkboxes["Sun"] is False


# ============================================================================
# LANGUAGE BLOCK PREFIX TESTS
# ============================================================================

class TestLanguageBlockPrefixes:
    """Test language block prefix mapping."""
    
    def test_get_prefixes_mandarin(self):
        """Test Mandarin returns M prefix."""
        assert get_language_block_prefixes("Mandarin") == ["M"]
        assert get_language_block_prefixes("mandarin") == ["M"]
    
    def test_get_prefixes_korean(self):
        """Test Korean returns K prefix."""
        assert get_language_block_prefixes("Korean") == ["K"]
    
    def test_get_prefixes_filipino(self):
        """Test Filipino returns T prefix."""
        assert get_language_block_prefixes("Filipino") == ["T"]
    
    def test_get_prefixes_vietnamese(self):
        """Test Vietnamese returns V prefix."""
        assert get_language_block_prefixes("Vietnamese") == ["V"]
    
    def test_get_prefixes_cantonese(self):
        """Test Cantonese returns C prefix."""
        assert get_language_block_prefixes("Cantonese") == ["C"]
    
    def test_get_prefixes_chinese_returns_both_m_and_c(self):
        """Test Chinese returns both M and C prefixes."""
        prefixes = get_language_block_prefixes("Chinese")
        assert set(prefixes) == {"M", "C"}
    
    def test_get_prefixes_japanese(self):
        """Test Japanese returns J prefix."""
        assert get_language_block_prefixes("Japanese") == ["J"]
    
    def test_get_prefixes_hmong(self):
        """Test Hmong returns Hm prefix."""
        assert get_language_block_prefixes("Hmong") == ["Hm"]
    
    def test_get_prefixes_south_asian_hindi(self):
        """Test South Asian with Hindi returns SA prefix."""
        prefixes = get_language_block_prefixes("South Asian", "Hindi")
        assert prefixes == ["SA"]
    
    def test_get_prefixes_south_asian_punjabi(self):
        """Test South Asian with Punjabi returns P prefix."""
        prefixes = get_language_block_prefixes("South Asian", "Punjabi")
        assert prefixes == ["P"]
    
    def test_get_prefixes_south_asian_both(self):
        """Test South Asian with Both returns SA and P prefixes."""
        prefixes = get_language_block_prefixes("South Asian", "Both")
        assert set(prefixes) == {"SA", "P"}
    
    def test_get_prefixes_south_asian_defaults_to_both(self):
        """Test South Asian without specification defaults to both."""
        prefixes = get_language_block_prefixes("South Asian")
        assert set(prefixes) == {"SA", "P"}
    
    def test_get_prefixes_unknown_returns_empty(self):
        """Test unknown language returns empty list."""
        assert get_language_block_prefixes("Klingon") == []


# ============================================================================
# BONUS LINE INPUT TESTS
# ============================================================================

class TestBonusLineInput:
    """Test BonusLineInput value object."""
    
    def test_bonus_line_input_immutable(self):
        """Test that BonusLineInput is immutable."""
        bonus = BonusLineInput(
            days="M-F",
            time="6a-7a",
            language="Mandarin"
        )
        
        # Should not be able to modify
        with pytest.raises(AttributeError):
            bonus.days = "M-Su"  # type: ignore
    
    def test_bonus_line_input_with_south_asian(self):
        """Test BonusLineInput with South Asian disambiguation."""
        bonus = BonusLineInput(
            days="M-Su",
            time="8a-10a",
            language="South Asian",
            hindi_punjabi_both="Hindi"
        )
        
        assert bonus.language == "South Asian"
        assert bonus.hindi_punjabi_both == "Hindi"
    
    def test_bonus_line_input_without_south_asian(self):
        """Test BonusLineInput without South Asian disambiguation."""
        bonus = BonusLineInput(
            days="Sa-Su",
            time="4p-6p",
            language="Hmong"
        )
        
        assert bonus.language == "Hmong"
        assert bonus.hindi_punjabi_both is None


# ============================================================================
# INTEGRATION SCENARIO TESTS
# ============================================================================

class TestIntegrationScenarios:
    """Test realistic integration scenarios."""
    
    def test_paid_mandarin_line_full_workflow(self):
        """Test complete workflow for a paid Mandarin line."""
        # Input data
        days = "M-Su"
        time = "6:00a-7:00a"
        language = "Mandarin"
        
        # Apply Sunday rule
        adjusted_days, adjusted_count = apply_sunday_6_7a_rule(days, time)
        
        # Should remove Sunday due to paid programming
        assert adjusted_days == "M-Sa"
        assert adjusted_count == 6
        
        # Parse times
        start_time = parse_start_time(time)
        end_time = parse_end_time(time)
        
        assert start_time == "06:00"
        assert end_time == "07:00"
        
        # Get block prefixes
        block_prefixes = get_language_block_prefixes(language)
        assert block_prefixes == ["M"]
        
        # Convert to checkboxes
        checkboxes = convert_days_to_etere_checkboxes(adjusted_days)
        assert checkboxes["Sun"] is False
        assert checkboxes["Mon"] is True
        assert checkboxes["Sat"] is True
    
    def test_bonus_korean_line_full_workflow(self):
        """Test complete workflow for a bonus Korean line."""
        # Input data (from user prompt)
        bonus = BonusLineInput(
            days="M-F",
            time="8a-10a",
            language="Korean"
        )
        
        # Apply Sunday rule (not applicable for M-F)
        adjusted_days, adjusted_count = apply_sunday_6_7a_rule(
            bonus.days,
            bonus.time
        )
        
        assert adjusted_days == "M-F"
        assert adjusted_count == 5
        
        # Parse times
        start_time = parse_start_time(bonus.time)
        end_time = parse_end_time(bonus.time)
        
        assert start_time == "08:00"
        assert end_time == "10:00"
        
        # Get block prefixes
        block_prefixes = get_language_block_prefixes(bonus.language)
        assert block_prefixes == ["K"]
        
        # Convert to checkboxes
        checkboxes = convert_days_to_etere_checkboxes(adjusted_days)
        assert checkboxes["Mon"] is True
        assert checkboxes["Fri"] is True
        assert checkboxes["Sat"] is False
        assert checkboxes["Sun"] is False
    
    def test_south_asian_line_with_both_hindi_punjabi(self):
        """Test South Asian line requesting both Hindi and Punjabi blocks."""
        # Input data
        bonus = BonusLineInput(
            days="Sa-Su",
            time="1p-4p",
            language="South Asian",
            hindi_punjabi_both="Both"
        )
        
        # Get block prefixes
        block_prefixes = get_language_block_prefixes(
            bonus.language,
            bonus.hindi_punjabi_both
        )
        
        assert set(block_prefixes) == {"SA", "P"}
        
        # Parse times
        start_time = parse_start_time(bonus.time)
        end_time = parse_end_time(bonus.time)
        
        assert start_time == "13:00"
        assert end_time == "16:00"
        
        # Convert to checkboxes
        checkboxes = convert_days_to_etere_checkboxes(bonus.days)
        assert checkboxes["Sat"] is True
        assert checkboxes["Sun"] is True
        assert checkboxes["Mon"] is False
    
    def test_midnight_endtime_caps_correctly(self):
        """Test that late-night spots ending at midnight cap at 23:59."""
        # Late night programming ending at midnight
        time = "7:00p-12:00a"
        
        start_time = parse_start_time(time)
        end_time = parse_end_time(time)
        
        assert start_time == "19:00"
        assert end_time == "23:59"  # Capped, not 00:00


# ============================================================================
# EDGE CASE TESTS
# ============================================================================

class TestEdgeCases:
    """Test edge cases and boundary conditions."""
    
    def test_sunday_rule_with_exact_match_6a_7a(self):
        """Test Sunday rule with exact match variations."""
        # Test various formatting
        assert apply_sunday_6_7a_rule("M-Su", "6:00a-7:00a")[0] == "M-Sa"
        assert apply_sunday_6_7a_rule("M-Su", "6a-7a")[0] == "M-Sa"
    
    def test_sunday_rule_does_not_trigger_for_similar_times(self):
        """Test Sunday rule doesn't trigger for similar but different times."""
        # 5-6a should not trigger
        assert apply_sunday_6_7a_rule("M-Su", "5:00a-6:00a")[0] == "M-Su"
        
        # 7-8a should not trigger
        assert apply_sunday_6_7a_rule("M-Su", "7:00a-8:00a")[0] == "M-Su"
    
    def test_case_insensitive_language_matching(self):
        """Test language matching is case-insensitive."""
        assert get_language_block_prefixes("mandarin") == ["M"]
        assert get_language_block_prefixes("MANDARIN") == ["M"]
        assert get_language_block_prefixes("Mandarin") == ["M"]
    
    def test_empty_day_pattern_defaults_to_7(self):
        """Test that unusual patterns default to 7 days."""
        assert count_active_days("") == 7
        assert count_active_days("XYZ") == 7


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
