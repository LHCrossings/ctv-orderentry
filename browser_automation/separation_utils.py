"""
Separation Interval Detection and Confirmation

Utility for detecting separation requirements from order PDFs
and confirming with user before applying to contracts.

UNIVERSAL RULE: Default separation is 15,0,0 (Customer=15, Event=0, Order=0)
Override only when PDF explicitly specifies different separation.
"""

import re
from typing import Optional, Tuple


def detect_separation_from_text(text: str) -> Optional[int]:
    """
    Detect separation interval specification from PDF text.
    
    Looks for patterns like:
    - "Separation between spots: 10"
    - "Separation: 15 minutes"
    - "Spot separation: 10"
    
    Args:
        text: Full text content from PDF
        
    Returns:
        Separation value in minutes if found, None otherwise
    """
    # Common patterns for separation specification
    patterns = [
        r'separation\s+between\s+spots[:\s]+(\d+)',
        r'spot\s+separation[:\s]+(\d+)',
        r'separation[:\s]+(\d+)\s*(?:minutes|mins?)?',
    ]
    
    text_lower = text.lower()
    
    for pattern in patterns:
        match = re.search(pattern, text_lower)
        if match:
            separation = int(match.group(1))
            return separation
    
    return None


def confirm_separation_intervals(
    detected_separation: Optional[int],
    order_type: str,
    estimate_number: Optional[str] = None
) -> Tuple[int, int, int]:
    """
    Confirm separation intervals with user.
    
    Shows detected separation (if any) and allows user to confirm or adjust.
    Returns the final separation intervals tuple to use.
    
    Args:
        detected_separation: Detected separation value from PDF (or None)
        order_type: Order type (e.g., "TCAA", "WorldLink", "Daviselen")
        estimate_number: Optional estimate number for context
        
    Returns:
        Tuple of (customer, event, order) intervals in minutes
    """
    print("\n" + "=" * 70)
    print("SEPARATION INTERVAL CONFIRMATION")
    print("=" * 70)
    
    if estimate_number:
        print(f"Order: {order_type} - Estimate {estimate_number}")
    else:
        print(f"Order: {order_type}")
    
    print()
    
    # Default is always 15,0,0 (industry standard)
    default_customer = 15
    default_event = 0
    default_order = 0
    
    if detected_separation is not None:
        print(f"✓ PDF specifies separation: {detected_separation} minutes")
        print(f"  This will be applied as CUSTOMER separation")
        print(f"  Recommended: Customer={detected_separation}, Event=0, Order=0")
        print()
        suggested_customer = detected_separation
    else:
        print(f"No separation specified in PDF")
        print(f"Default: Customer={default_customer}, Event=0, Order=0 (industry standard)")
        print()
        suggested_customer = default_customer
    
    # Ask user to confirm or adjust
    print("Options:")
    print(f"  1. Use recommended: Customer={suggested_customer}, Event=0, Order=0")
    print(f"  2. Use default: Customer={default_customer}, Event=0, Order=0")
    print(f"  3. Custom (enter your own values)")
    print()
    
    while True:
        choice = input("Select option (1-3) [default: 1]: ").strip()
        
        if choice == "" or choice == "1":
            # Use recommended
            return (suggested_customer, default_event, default_order)
        
        elif choice == "2":
            # Use default 15,0,0
            return (default_customer, default_event, default_order)
        
        elif choice == "3":
            # Custom entry
            print()
            print("Enter custom separation intervals (in minutes):")
            
            while True:
                try:
                    customer = int(input("  Customer separation: ").strip())
                    event = int(input("  Event separation: ").strip())
                    order = int(input("  Order separation: ").strip())
                    
                    print(f"\n  Confirmed: Customer={customer}, Event={event}, Order={order}")
                    confirm = input("  Is this correct? (y/n): ").strip().lower()
                    
                    if confirm == "y":
                        return (customer, event, order)
                    else:
                        print("\n  Let's try again...")
                        
                except ValueError:
                    print("  Invalid input. Please enter numbers only.")
        
        else:
            print("Invalid choice. Please select 1, 2, or 3.")


def format_separation_intervals(intervals: Tuple[int, int, int]) -> str:
    """
    Format separation intervals for display.
    
    Args:
        intervals: Tuple of (customer, event, order) in minutes
        
    Returns:
        Formatted string like "Customer=15, Event=0, Order=0"
    """
    customer, event, order = intervals
    return f"Customer={customer}, Event={event}, Order={order}"


def validate_spot_capacity(
    time_from: str,
    time_to: str,
    spots_per_day: int,
    separation_minutes: int,
    days_pattern: str
) -> Tuple[bool, str]:
    """
    Validate if the requested spots can physically fit in the time window.
    
    Critical for lines like "11p-2a" which actually become "23:00-23:59" (59 minutes).
    With 4 spots/day and 15-minute separation, you need 60 minutes minimum.
    
    Args:
        time_from: Start time in "HH:MM" format (e.g., "23:00")
        time_to: End time in "HH:MM" format (e.g., "23:59")
        spots_per_day: Number of spots per day
        separation_minutes: Customer separation interval in minutes
        days_pattern: Day pattern like "M-Su" (for context in warnings)
    
    Returns:
        Tuple of (can_fit: bool, message: str)
    """
    # Convert times to minutes since midnight
    from_parts = time_from.split(':')
    to_parts = time_to.split(':')
    
    from_minutes = int(from_parts[0]) * 60 + int(from_parts[1])
    to_minutes = int(to_parts[0]) * 60 + int(to_parts[1])
    
    # Available time window
    available_minutes = to_minutes - from_minutes
    
    if available_minutes <= 0:
        # This shouldn't happen, but handle it
        return (False, f"Invalid time range: {time_from}-{time_to}")
    
    # Required time: spots_per_day × separation_minutes
    # Note: The LAST spot doesn't need separation after it
    required_minutes = (spots_per_day - 1) * separation_minutes if spots_per_day > 1 else 0
    
    # Can we fit?
    can_fit = available_minutes >= required_minutes
    
    if can_fit:
        buffer = available_minutes - required_minutes
        message = f"✓ Capacity OK: {available_minutes} min available, {required_minutes} min required ({buffer} min buffer)"
    else:
        shortage = required_minutes - available_minutes
        # Show the ACTUAL calculation: (spots - 1) separations
        num_separations = spots_per_day - 1 if spots_per_day > 1 else 0
        message = (
            f"⚠ CAPACITY WARNING: {spots_per_day} spots/day needs {num_separations} separations × {separation_minutes} min = {required_minutes} min required\n"
            f"  Available time: {time_from}-{time_to} = {available_minutes} minutes\n"
            f"  SHORT BY: {shortage} minutes\n"
            f"  Etere may reject this line or spots may not air properly!"
        )
    
    return (can_fit, message)


def confirm_time_and_capacity(
    original_time: str,
    parsed_from: str,
    parsed_to: str,
    spots_per_week: int,
    days_pattern: str,
    separation_minutes: int
) -> Tuple[str, str, int, bool]:
    """
    Confirm time parsing and validate spot capacity.
    
    Shows user what the parsed times will be, especially important when:
    - Start time was adjusted to floor (06:00)
    - End time crossed midnight and was capped (23:59)
    - Spots won't fit with current separation (offers to adjust THIS LINE ONLY)
    
    Args:
        original_time: Original time string from PDF (e.g., "11p-2a")
        parsed_from: Parsed start time (e.g., "23:00")
        parsed_to: Parsed end time (e.g., "23:59")
        spots_per_week: Total spots per week
        days_pattern: Day pattern (e.g., "M-Su")
        separation_minutes: Customer separation in minutes
    
    Returns:
        Tuple of (confirmed_from, confirmed_to, adjusted_separation, user_approved)
        Note: adjusted_separation may differ from input if user chose option 2
    """
    # Calculate spots per day
    day_count = _count_days(days_pattern)
    spots_per_day = spots_per_week // day_count if day_count > 0 else spots_per_week
    
    # Check if times were adjusted
    time_adjusted = False
    adjustment_notes = []
    
    # Detect midnight crossing
    if '12a' in original_time.lower() or '1a' in original_time.lower() or '2a' in original_time.lower():
        time_adjusted = True
        adjustment_notes.append(f"End time capped at 23:59 (midnight crossing detected)")
    
    # Detect early start
    if parsed_from == "06:00" and not original_time.lower().startswith('6'):
        time_adjusted = True
        adjustment_notes.append(f"Start time adjusted to 06:00 (minimum allowed)")
    
    # If no adjustments and capacity is fine, skip confirmation
    can_fit, capacity_msg = validate_spot_capacity(
        parsed_from, parsed_to, spots_per_day, separation_minutes, days_pattern
    )
    
    if not time_adjusted and can_fit:
        # Everything is fine, no confirmation needed
        return (parsed_from, parsed_to, separation_minutes, True)
    
    # Show confirmation prompt
    print("\n" + "=" * 70)
    print("TIME & CAPACITY CONFIRMATION")
    print("=" * 70)
    print(f"Original time: {original_time}")
    print(f"Parsed time:   {parsed_from} - {parsed_to}")
    
    if adjustment_notes:
        print("\nAdjustments made:")
        for note in adjustment_notes:
            print(f"  • {note}")
    
    print(f"\nSpot distribution: {spots_per_week} spots/week ÷ {day_count} days = {spots_per_day} spots/day")
    print(f"Separation: {separation_minutes} minutes")
    print()
    print(capacity_msg)
    print()
    
    if not can_fit:
        print("⚠ WARNING: This line may fail in Etere or not air all spots!")
        print("\nOptions:")
        print("  1. Continue anyway (Etere may reject or under-deliver)")
        print("  2. Adjust separation for THIS LINE ONLY (recommended)")
        print("  3. Reduce spots per week (requires source data change)")
        print("  4. Adjust time range manually")
        
        choice = input("\nSelect option (1-4) [default: 2]: ").strip() or "2"
        
        if choice == "2":
            # Calculate what separation would work
            # Required: (spots_per_day - 1) × separation ≤ available_minutes
            # So: separation ≤ available_minutes / (spots_per_day - 1)
            max_separation = available_minutes // (spots_per_day - 1) if spots_per_day > 1 else available_minutes
            
            print(f"\nCurrent separation: {separation_minutes} minutes (doesn't fit)")
            print(f"Maximum separation that fits: {max_separation} minutes")
            print(f"Recommended: 10 minutes (common fallback for tight windows)")
            print()
            
            while True:
                new_sep = input(f"Enter new separation for THIS LINE ONLY (1-{max_separation}) [default: 10]: ").strip()
                if not new_sep:
                    new_sep = 10
                    break
                try:
                    new_sep = int(new_sep)
                    if 0 <= new_sep <= max_separation:
                        break
                    else:
                        print(f"Please enter a value between 0 and {max_separation}")
                except ValueError:
                    print("Please enter a valid number")
            
            # Update separation for this line
            separation_minutes = new_sep
            
            # Re-validate with new separation
            can_fit_now, new_msg = validate_spot_capacity(
                parsed_from, parsed_to, spots_per_day, separation_minutes, days_pattern
            )
            print(f"\n{new_msg}")
            
            if can_fit_now:
                print(f"\n✓ Line will use separation: ({separation_minutes}, 0, 0) for this line only")
                print("  Other lines will use the default/order separation")
            
        elif choice == "3":
            print("\nNote: You'll need to adjust this in the PDF or source data.")
            print("Continuing with current values for now...")
        elif choice == "4":
            print("\nManual time entry:")
            parsed_from = input(f"  Start time (HH:MM) [current: {parsed_from}]: ").strip() or parsed_from
            parsed_to = input(f"  End time (HH:MM) [current: {parsed_to}]: ").strip() or parsed_to
    
    confirm = input(f"\nProceed with times {parsed_from}-{parsed_to}? (Y/n): ").strip().lower()
    approved = confirm in ['', 'y', 'yes']
    
    # Return the (possibly adjusted) separation as well
    return (parsed_from, parsed_to, separation_minutes, approved)


def _count_days(days_pattern: str) -> int:
    """Count active days in a pattern like 'M-Su', 'M-F', etc."""
    if days_pattern == "M-Su":
        return 7
    elif days_pattern == "M-F":
        return 5
    elif days_pattern == "M-Sa":
        return 6
    elif days_pattern == "Sa-Su":
        return 2
    elif days_pattern in ["Sa", "SAT"]:
        return 1
    elif days_pattern in ["Su", "SU", "SUN"]:
        return 1
    else:
        # Default/unknown - assume 7
        return 7


# Example usage in agency automation:
"""
from separation_utils import confirm_time_and_capacity

# After parsing time
time_from, time_to = EtereClient.parse_time_range("11p-2a")

# Before creating line, validate and confirm
time_from, time_to, line_separation, approved = confirm_time_and_capacity(
    original_time="11p-2a",
    parsed_from=time_from,
    parsed_to=time_to,
    spots_per_week=28,
    days_pattern="M-Su",
    separation_minutes=15  # Default/order separation
)

if not approved:
    print("User rejected time configuration, skipping line...")
    continue

# Use line_separation (may be different from order default if adjusted)
etere.add_contract_line(
    ...
    separation_intervals=(line_separation, 0, 0)  # Use adjusted value for THIS LINE
)
"""
