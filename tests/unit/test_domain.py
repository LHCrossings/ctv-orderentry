"""
Unit tests for domain layer - pure business logic with no I/O.

These tests verify that our domain entities and value objects
work correctly in isolation.
"""

import pytest
from datetime import date, time
from decimal import Decimal
from pathlib import Path

# Import domain objects
import sys
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from domain.enums import OrderType, OrderStatus, Market, Language
from domain.value_objects import TimeRange, DayPattern, ScheduleLine, OrderInput
from domain.entities import Order, Contract, ProcessingResult


class TestOrderType:
    """Test OrderType enum behavior."""
    
    def test_worldlink_requires_block_refresh(self):
        """WorldLink orders should require block refresh."""
        assert OrderType.WORLDLINK.requires_block_refresh() is True
    
    def test_tcaa_does_not_require_block_refresh(self):
        """TCAA orders should not require block refresh."""
        assert OrderType.TCAA.requires_block_refresh() is False
    
    def test_worldlink_supports_multiple_markets(self):
        """WorldLink should support multiple markets."""
        assert OrderType.WORLDLINK.supports_multiple_markets() is True
    
    def test_tcaa_does_not_support_multiple_markets(self):
        """TCAA should not support multiple markets."""
        assert OrderType.TCAA.supports_multiple_markets() is False


class TestLanguage:
    """Test Language enum methods."""
    
    def test_chinese_ros_schedule(self):
        """Chinese should have M-Su 6a-11:59p ROS schedule."""
        days, time_range = Language.MANDARIN.get_ros_schedule()
        assert days == "M-Su"
        assert time_range == "6a-11:59p"
    
    def test_filipino_ros_schedule(self):
        """Filipino should have M-Su 4p-7p ROS schedule."""
        days, time_range = Language.FILIPINO.get_ros_schedule()
        assert days == "M-Su"
        assert time_range == "4p-7p"
    
    def test_chinese_block_abbreviation(self):
        """Chinese should use C/M block code."""
        assert Language.MANDARIN.get_block_abbreviation() == "C/M"
    
    def test_filipino_block_abbreviation(self):
        """Filipino should use T block code."""
        assert Language.FILIPINO.get_block_abbreviation() == "T"


class TestTimeRange:
    """Test TimeRange value object."""
    
    def test_parse_simple_time_range(self):
        """Should parse '5p-7p' correctly."""
        tr = TimeRange.from_string("5p-7p")
        assert tr.start_time == time(17, 0)
        assert tr.end_time == time(19, 0)
    
    def test_parse_time_with_minutes(self):
        """Should parse '6:00a-11:59p' correctly."""
        tr = TimeRange.from_string("6:00a-11:59p")
        assert tr.start_time == time(6, 0)
        assert tr.end_time == time(23, 59)
    
    def test_midnight_converts_to_2359(self):
        """Midnight (12:00a) should convert to 23:59 for Etere."""
        tr = TimeRange.from_string("11:00p-12:00a")
        assert tr.end_time == time(23, 59)
    
    def test_to_etere_format(self):
        """Should format times for Etere as HH:MM."""
        tr = TimeRange(time(17, 0), time(19, 30))
        start, end = tr.to_etere_format()
        assert start == "17:00"
        assert end == "19:30"
    
    def test_validates_start_before_end(self):
        """Should raise error if start time is after end time."""
        with pytest.raises(ValueError):
            TimeRange(time(19, 0), time(17, 0))


class TestDayPattern:
    """Test DayPattern value object."""
    
    def test_weekday_pattern_to_list(self):
        """M-F should expand to all weekdays."""
        pattern = DayPattern("M-F")
        assert pattern.to_day_list() == ["M", "Tu", "W", "Th", "F"]
    
    def test_weekend_pattern_to_list(self):
        """Sa-Su should expand to weekend days."""
        pattern = DayPattern("Sa-Su")
        assert pattern.to_day_list() == ["Sa", "Su"]
    
    def test_full_week_pattern(self):
        """M-Su should expand to all seven days."""
        pattern = DayPattern("M-Su")
        assert len(pattern.to_day_list()) == 7
    
    def test_includes_sunday(self):
        """M-Su should include Sunday."""
        assert DayPattern("M-Su").includes_sunday() is True
        assert DayPattern("M-F").includes_sunday() is False
    
    def test_remove_sunday_from_full_week(self):
        """Removing Sunday from M-Su should give M-Sa."""
        pattern = DayPattern("M-Su")
        no_sunday = pattern.remove_sunday()
        assert no_sunday is not None
        assert no_sunday.pattern == "M-Sa"
    
    def test_remove_sunday_from_weekend(self):
        """Removing Sunday from Sa-Su should give just Sa."""
        pattern = DayPattern("Sa-Su")
        no_sunday = pattern.remove_sunday()
        assert no_sunday is not None
        assert no_sunday.pattern == "Sa"


class TestOrder:
    """Test Order entity."""
    
    def test_order_is_processable_when_pending(self):
        """Order with PENDING status and known type should be processable."""
        order = Order(
            pdf_path=Path("test.pdf"),
            order_type=OrderType.WORLDLINK,
            customer_name="Test Customer",
            status=OrderStatus.PENDING
        )
        assert order.is_processable() is True
    
    def test_order_not_processable_when_unknown_type(self):
        """Order with UNKNOWN type should not be processable."""
        order = Order(
            pdf_path=Path("test.pdf"),
            order_type=OrderType.UNKNOWN,
            customer_name="Test Customer",
            status=OrderStatus.PENDING
        )
        assert order.is_processable() is False
    
    def test_order_not_processable_when_completed(self):
        """Order with COMPLETED status should not be processable."""
        order = Order(
            pdf_path=Path("test.pdf"),
            order_type=OrderType.WORLDLINK,
            customer_name="Test Customer",
            status=OrderStatus.COMPLETED
        )
        assert order.is_processable() is False
    
    def test_daviselen_requires_upfront_input(self):
        """Daviselen orders should require upfront input."""
        order = Order(
            pdf_path=Path("test.pdf"),
            order_type=OrderType.DAVISELEN,
            customer_name="Test Customer"
        )
        assert order.requires_upfront_input() is True
    
    def test_worldlink_does_not_require_upfront_input(self):
        """WorldLink orders should not require upfront input."""
        order = Order(
            pdf_path=Path("test.pdf"),
            order_type=OrderType.WORLDLINK,
            customer_name="Test Customer"
        )
        assert order.requires_upfront_input() is False
    
    def test_with_status_creates_new_order(self):
        """with_status should return new Order with updated status."""
        order = Order(
            pdf_path=Path("test.pdf"),
            order_type=OrderType.WORLDLINK,
            customer_name="Test Customer",
            status=OrderStatus.PENDING
        )
        updated = order.with_status(OrderStatus.PROCESSING)
        
        # Original unchanged
        assert order.status == OrderStatus.PENDING
        # New order has updated status
        assert updated.status == OrderStatus.PROCESSING
        # Other fields unchanged
        assert updated.pdf_path == order.pdf_path
        assert updated.order_type == order.order_type


class TestContract:
    """Test Contract entity."""
    
    def test_worldlink_contract_requires_refresh(self):
        """WorldLink contracts should require block refresh."""
        contract = Contract(
            contract_number="12345",
            order_type=OrderType.WORLDLINK
        )
        assert contract.requires_block_refresh() is True
    
    def test_tcaa_contract_does_not_require_refresh(self):
        """TCAA contracts should not require block refresh."""
        contract = Contract(
            contract_number="12345",
            order_type=OrderType.TCAA
        )
        assert contract.requires_block_refresh() is False
    
    def test_contract_with_highest_line_has_partial_lines(self):
        """Contract with highest_line should have partial lines."""
        contract = Contract(
            contract_number="12345",
            order_type=OrderType.WORLDLINK,
            highest_line=10
        )
        assert contract.has_partial_lines() is True
    
    def test_contract_without_highest_line_has_no_partial_lines(self):
        """Contract without highest_line should not have partial lines."""
        contract = Contract(
            contract_number="12345",
            order_type=OrderType.WORLDLINK
        )
        assert contract.has_partial_lines() is False
    
    def test_get_refresh_range_for_partial(self):
        """Should return correct range for partial refresh."""
        contract = Contract(
            contract_number="12345",
            order_type=OrderType.WORLDLINK,
            highest_line=10
        )
        start, end = contract.get_refresh_range()
        assert start == 10
        assert end is None


class TestScheduleLine:
    """Test ScheduleLine value object."""
    
    def test_duration_weeks_calculation(self):
        """Should calculate weeks correctly."""
        line = ScheduleLine(
            start_date=date(2025, 1, 1),
            end_date=date(2025, 1, 14),  # 14 days = 2 weeks
            time_range=TimeRange.from_string("5p-7p"),
            day_pattern=DayPattern("M-F"),
            weekly_spots=10,
            rate=Decimal("100.00"),
            market=Market.NYC
        )
        assert line.duration_weeks() == 2
    
    def test_total_spots_calculation(self):
        """Should calculate total spots correctly."""
        line = ScheduleLine(
            start_date=date(2025, 1, 1),
            end_date=date(2025, 1, 14),
            time_range=TimeRange.from_string("5p-7p"),
            day_pattern=DayPattern("M-F"),
            weekly_spots=10,
            rate=Decimal("100.00"),
            market=Market.NYC
        )
        assert line.total_spots() == 20  # 10 spots/week * 2 weeks
    
    def test_total_cost_calculation(self):
        """Should calculate total cost correctly."""
        line = ScheduleLine(
            start_date=date(2025, 1, 1),
            end_date=date(2025, 1, 14),
            time_range=TimeRange.from_string("5p-7p"),
            day_pattern=DayPattern("M-F"),
            weekly_spots=10,
            rate=Decimal("50.00"),
            market=Market.NYC
        )
        assert line.total_cost() == Decimal("1000.00")  # 20 spots * $50
    
    def test_needs_splitting_when_spots_differ(self):
        """Lines with different spot counts need splitting."""
        line1 = ScheduleLine(
            start_date=date(2025, 1, 1),
            end_date=date(2025, 1, 7),
            time_range=TimeRange.from_string("5p-7p"),
            day_pattern=DayPattern("M-F"),
            weekly_spots=10,
            rate=Decimal("100.00"),
            market=Market.NYC
        )
        line2 = ScheduleLine(
            start_date=date(2025, 1, 8),
            end_date=date(2025, 1, 14),
            time_range=TimeRange.from_string("5p-7p"),
            day_pattern=DayPattern("M-F"),
            weekly_spots=15,  # Different!
            rate=Decimal("100.00"),
            market=Market.NYC
        )
        assert line1.needs_splitting(line2) is True
    
    def test_no_splitting_when_spots_same(self):
        """Lines with same spot counts don't need splitting."""
        line1 = ScheduleLine(
            start_date=date(2025, 1, 1),
            end_date=date(2025, 1, 7),
            time_range=TimeRange.from_string("5p-7p"),
            day_pattern=DayPattern("M-F"),
            weekly_spots=10,
            rate=Decimal("100.00"),
            market=Market.NYC
        )
        line2 = ScheduleLine(
            start_date=date(2025, 1, 8),
            end_date=date(2025, 1, 14),
            time_range=TimeRange.from_string("5p-7p"),
            day_pattern=DayPattern("M-F"),
            weekly_spots=10,  # Same!
            rate=Decimal("100.00"),
            market=Market.NYC
        )
        assert line1.needs_splitting(line2) is False


if __name__ == "__main__":
    # Run tests with pytest
    pytest.main([__file__, "-v"])
