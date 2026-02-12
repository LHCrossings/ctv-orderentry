"""
Value Objects - Immutable domain data structures.

Value objects represent domain concepts that are identified by their
values rather than a unique identity. They are immutable and comparable.
"""

from dataclasses import dataclass
from datetime import date, time
from decimal import Decimal
from typing import Any

from .enums import Language, Market


@dataclass(frozen=True)
class TimeRange:
    """
    Represents a time range for ad scheduling (e.g., "5p-7p").
    
    Immutable value object with validation and conversion methods.
    """
    start_time: time
    end_time: time
    
    def __post_init__(self) -> None:
        """Validate that start time is before end time."""
        if self.start_time >= self.end_time:
            raise ValueError(f"Start time {self.start_time} must be before end time {self.end_time}")
    
    def to_etere_format(self) -> tuple[str, str]:
        """
        Convert to Etere system format (HH:MM strings).
        
        Returns:
            Tuple of (start_time_str, end_time_str) in HH:MM format
        """
        return (
            self.start_time.strftime("%H:%M"),
            self.end_time.strftime("%H:%M")
        )
    
    @classmethod
    def from_string(cls, time_range: str) -> "TimeRange":
        """
        Parse time range from string like "5p-7p" or "6a-11:59p".
        
        Args:
            time_range: String in format like "5p-7p" or "6:00a-11:59p"
            
        Returns:
            TimeRange object
            
        Examples:
            >>> TimeRange.from_string("5p-7p")
            TimeRange(start_time=time(17, 0), end_time=time(19, 0))
        """
        start_str, end_str = time_range.split("-")
        return cls(
            start_time=cls._parse_time(start_str.strip()),
            end_time=cls._parse_time(end_str.strip())
        )
    
    @staticmethod
    def _parse_time(time_str: str) -> time:
        """Parse time string like '5p' or '11:59p' to time object."""
        time_str = time_str.strip().lower()
        is_pm = time_str.endswith("p")
        is_am = time_str.endswith("a")
        
        # Remove am/pm marker
        time_str = time_str[:-1]
        
        # Handle special case: midnight
        if time_str == "12:00" and is_am:
            return time(23, 59)  # Etere uses 23:59 for midnight
        
        # Split hours and minutes
        if ":" in time_str:
            hour_str, min_str = time_str.split(":")
            hour = int(hour_str)
            minute = int(min_str)
        else:
            hour = int(time_str)
            minute = 0
        
        # Convert to 24-hour format
        if is_pm and hour != 12:
            hour += 12
        elif is_am and hour == 12:
            hour = 0
        
        return time(hour, minute)


@dataclass(frozen=True)
class DayPattern:
    """
    Represents a day-of-week pattern (e.g., "M-F", "Sa-Su", "M-Su").
    
    Used for scheduling ads across specific days of the week.
    """
    pattern: str
    
    def __post_init__(self) -> None:
        """Validate day pattern format."""
        valid_patterns = {
            "M-F", "Sa-Su", "M-Su", "M", "Tu", "W", "Th", "F", "Sa", "Su",
            "M-Th", "F-Su", "M-Sa"
        }
        if self.pattern not in valid_patterns:
            raise ValueError(f"Invalid day pattern: {self.pattern}")
    
    def to_day_list(self) -> list[str]:
        """
        Convert pattern to list of individual days.
        
        Returns:
            List of day abbreviations
            
        Examples:
            >>> DayPattern("M-F").to_day_list()
            ["M", "Tu", "W", "Th", "F"]
        """
        day_mapping = {
            "M": ["M"],
            "Tu": ["Tu"],
            "W": ["W"],
            "Th": ["Th"],
            "F": ["F"],
            "Sa": ["Sa"],
            "Su": ["Su"],
            "M-F": ["M", "Tu", "W", "Th", "F"],
            "Sa-Su": ["Sa", "Su"],
            "M-Su": ["M", "Tu", "W", "Th", "F", "Sa", "Su"],
            "M-Th": ["M", "Tu", "W", "Th"],
            "F-Su": ["F", "Sa", "Su"],
            "M-Sa": ["M", "Tu", "W", "Th", "F", "Sa"],
        }
        return day_mapping.get(self.pattern, [self.pattern])
    
    def includes_sunday(self) -> bool:
        """Check if pattern includes Sunday (important for paid programming restrictions)."""
        return "Su" in self.to_day_list()
    
    def remove_sunday(self) -> "DayPattern | None":
        """
        Remove Sunday from pattern (for paid programming restrictions).
        
        Returns:
            New DayPattern without Sunday, or None if pattern becomes empty
        """
        days = [d for d in self.to_day_list() if d != "Su"]
        if not days:
            return None
        
        # Try to create simplified pattern
        if days == ["M", "Tu", "W", "Th", "F"]:
            return DayPattern("M-F")
        elif days == ["Sa"]:
            return DayPattern("Sa")
        elif days == ["M", "Tu", "W", "Th", "F", "Sa"]:
            return DayPattern("M-Sa")
        else:
            # Return first day only (simplification)
            return DayPattern(days[0])


@dataclass(frozen=True)
class ScheduleLine:
    """
    Represents a single line item in an advertising schedule.
    
    Combines date range, time range, day pattern, and spot counts.
    """
    start_date: date
    end_date: date
    time_range: TimeRange
    day_pattern: DayPattern
    weekly_spots: int
    rate: Decimal
    market: Market
    language: Language | None = None
    
    def duration_weeks(self) -> int:
        """Calculate the number of weeks in this schedule line."""
        days = (self.end_date - self.start_date).days + 1
        return (days + 6) // 7  # Round up to nearest week
    
    def total_spots(self) -> int:
        """Calculate total number of spots across all weeks."""
        return self.weekly_spots * self.duration_weeks()
    
    def total_cost(self) -> Decimal:
        """Calculate total cost for all spots."""
        return self.rate * Decimal(self.total_spots())
    
    def needs_splitting(self, next_line: "ScheduleLine | None") -> bool:
        """
        Determine if this line needs to be split from the next line.
        
        Lines must be split when weekly spot counts differ (critical business rule).
        """
        if next_line is None:
            return False
        return self.weekly_spots != next_line.weekly_spots


@dataclass(frozen=True)
class OrderInput:
    """
    User-provided inputs collected upfront for unattended processing.
    
    Stores all decisions needed before automation begins.
    """
    order_code: str
    description: str
    customer_id: str | None = None
    time_overrides: dict[str, Any] | None = None
    spot_duration: int | None = None
    
    def has_customer_override(self) -> bool:
        """Check if user provided explicit customer ID."""
        return self.customer_id is not None
    
    def has_time_overrides(self) -> bool:
        """Check if user provided time overrides (Admerasia orders)."""
        return self.time_overrides is not None and len(self.time_overrides) > 0
