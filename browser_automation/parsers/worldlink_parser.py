# parsers/worldlink_parser.py
"""
WorldLink PDF Parser v12
Extracts order data from WorldLink/Tatari insertion order PDFs
Detects new contracts vs revisions based on ACTION and LINE NO columns

FIXED: Now handles days column with '0' and 'X' characters (not just 'X' and spaces)
"""

import pdfplumber
import re
from datetime import datetime
from pathlib import Path

def parse_worldlink_pdf(pdf_path):
    """
    Parse a WorldLink PDF and extract order data
    
    Args:
        pdf_path: Path to the WorldLink PDF file
        
    Returns:
        Dictionary with order data including order_type detection
        None if parsing fails
    """
    try:
        with pdfplumber.open(pdf_path) as pdf:
            # WorldLink orders are typically single page
            first_page = pdf.pages[0]
            text = first_page.extract_text()
            
            # Parse header information
            order_data = _parse_header(text)
            
            # Parse line items from text
            order_data['lines'] = _parse_line_items_from_text(text)
            
            # Determine network from Station/Region (needed for description prefix)
            order_data['network'] = _extract_network(text)
            
            # Build description after lines are parsed: "WL {Agency First} {Advertiser First} {Spot Length} {Tracking}"
            # For Asian Channel, add "TAC - " prefix
            agency_first = order_data.get('agency', 'Unknown').split()[0] if order_data.get('agency') else "Unknown"
            advertiser_first = order_data.get('advertiser', 'Unknown').split()[0] if order_data.get('advertiser') else "Unknown"
            tracking = order_data.get('tracking_number', 'Unknown')
            
            # Get spot length from first line
            spot_length = ''
            if order_data.get('lines'):
                first_line = order_data['lines'][0]
                duration = first_line.get('duration', '')
                if duration:
                    spot_length = duration
            
            # Get network
            network = order_data.get('network', 'CROSSINGS')
            
            # Build description
            base_description = ""
            if spot_length:
                base_description = f"WL {agency_first} {advertiser_first} {spot_length} {tracking}"
            else:
                base_description = f"WL {agency_first} {advertiser_first} {tracking}"
            
            # Add TAC prefix for Asian Channel orders
            if network == "ASIAN":
                order_data['description'] = f"TAC - {base_description}"
            else:
                order_data['description'] = base_description
            
            # Determine order type based on first line's ACTION and LINE NO
            order_data['order_type'] = _determine_order_type(order_data['lines'])
            
            return order_data
            
    except Exception as e:
        print(f"[PARSER] Error parsing WorldLink PDF: {e}")
        import traceback
        traceback.print_exc()
        return None

def _extract_network(text):
    """Determine if this is Crossings TV or Asian Channel"""
    if "ASIAN" in text.upper():
        return "ASIAN"
    else:
        return "CROSSINGS"

def _determine_order_type(lines):
    """
    Determine order type based on ACTION and LINE NO columns
    
    Rules:
    - If Action = "ADD" and Line No = 1 → "new"
    - If Action = "ADD" and Line No > 1 → "revision_add"
    - If Action = "CHANGE" → "revision_change"
    """
    if not lines:
        return "new"
    
    first_line = lines[0]
    action = first_line.get('action', 'ADD')
    line_no = first_line.get('line_number', 1)
    
    if action == "CHANGE":
        return "revision_change"
    
    if action == "ADD":
        if line_no == 1:
            return "new"
        else:
            return "revision_add"
    
    # Default to new if uncertain
    return "new"

def _parse_header(text):
    """Extract header information from PDF text"""
    order_data = {}
    
    # Extract tracking number - handle both "WL Tracking No." and "Unwired Tracking No."
    tracking_match = re.search(r'(?:WL|Unwired)\s+Tracking\s+No\.\s*(\d+)', text, re.IGNORECASE)
    tracking_no = tracking_match.group(1) if tracking_match else "Unknown"
    order_data['tracking_number'] = tracking_no
    
    # Extract Agency
    agency_match = re.search(r'Agency:\s*(.+?)(?:\s+Station)', text)
    if agency_match:
        agency_name = agency_match.group(1).strip()
        order_data['agency'] = agency_name
        # Build order code using first word of agency name only
        agency_first_word = agency_name.split()[0] if agency_name else "Unknown"
        if tracking_no != "Unknown":
            order_data['order_code'] = f"WL {agency_first_word} {tracking_no}"
        else:
            order_data['order_code'] = "Unknown"
    else:
        order_data['agency'] = "Unknown"
        order_data['order_code'] = f"WL {tracking_no}" if tracking_no != "Unknown" else "Unknown"
    
    # Extract Advertiser
    advertiser_match = re.search(r'Advertiser:\s*(.+?)(?:\s+Product Desc)', text)
    if advertiser_match:
        advertiser_name = advertiser_match.group(1).strip()
        order_data['advertiser'] = advertiser_name
    else:
        advertiser_name = "Unknown"
        order_data['advertiser'] = "Unknown"
    
    # Note: Description will be built AFTER we parse lines (need spot length)
    # Format: "WL {Agency First} {Advertiser First} {Spot Length} {Tracking}"
    # This is set later in parse_worldlink_pdf() after lines are parsed
    
    # Extract Product
    product_match = re.search(r'Product:\s*(.+?)(?:\s+Buyer Phone|$)', text, re.MULTILINE)
    if product_match:
        order_data['product'] = product_match.group(1).strip()
    
    # Extract Order Comment (keep separate, don't use for description)
    comment_match = re.search(r'Order Comment:\s*(.+?)(?:\s+Client Approval|$)', text, re.DOTALL)
    if comment_match:
        comment = comment_match.group(1).strip()
        # Clean up the comment
        comment = re.sub(r'\s+', ' ', comment)  # Normalize whitespace
        order_data['order_comment'] = comment
    
    return order_data

def _parse_line_items_from_text(text):
    """Parse line items from PDF text using regex patterns"""
    lines = []
    
    # Split text into lines
    text_lines = text.split('\n')
    
    for text_line in text_lines:
        # Look for lines that start with a number followed by ADD or CHANGE
        match = re.match(r'^(\d+)\s+(ADD|CHANGE)', text_line.strip())
        if not match:
            continue
        
        try:
            line_item = _parse_single_line(text_line.strip())
            if line_item:
                lines.append(line_item)
        except Exception as e:
            print(f"[PARSER] Error parsing line: {e}")
            print(f"[PARSER] Line text: {text_line}")
            continue
    
    return lines

def _parse_single_line(line_text):
    """Parse a single line item from text"""
    line_item = {}
    
    # Pattern breakdown:
    # (\d+) - Line number
    # (ADD|CHANGE) - Action
    # (\d{1,2}/\d{1,2}/\d{4}) - Create date
    # (ROS|[A-Z\s]+) - Program name
    # ([X0\s]+) - Days (X marks which days, 0 for unmarked days) ← FIXED HERE
    # (\d{1,2}/\d{1,2}/\d{4}\s*-\s*\d{1,2}/\d{1,2}/\d{4}) - Date range
    # (\d{1,2}:\d{2}\s*[AP]M\s*-\s*\d{1,2}:\d{2}\s*[AP]M) - Time range
    # (\d+) - Length/Duration
    # (\d+) - Number of weeks
    # (\d+) - Spots per week
    # (\$[\d,]+\.\d{2}) - Rate
    # (\d+) - Total spots
    # (\$[\d,]+\.\d{2}) - Total amount
    
    pattern = r'^(\d+)\s+(ADD|CHANGE)\s+(\d{1,2}/\d{1,2}/\d{4})\s+(\S+)\s+([X0\s]+?)\s+(\d{1,2}/\d{1,2}/\d{4}\s*-\s*\d{1,2}/\d{1,2}/\d{4})\s+(\d{1,2}:\d{2}\s*[AP]M\s*-\s*\d{1,2}:\d{2}\s*[AP]M)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\$[\d,]+\.\d{2})\s+(\d+)\s+(\$[\d,]+\.\d{2})'
    
    match = re.search(pattern, line_text)
    
    if not match:
        return None
    
    # Extract matched groups
    line_no, action, create_date, program, days_str, date_range, time_range, length, weeks, spots_per_week, rate, total_spots, total_amount = match.groups()
    
    # Line number
    line_item['line_number'] = int(line_no)
    
    # Action (ADD or CHANGE)
    line_item['action'] = action
    
    # Date range (e.g., "12/22/2025 - 12/28/2025")
    date_parts = date_range.split('-')
    if len(date_parts) == 2:
        line_item['start_date'] = date_parts[0].strip()
        line_item['end_date'] = date_parts[1].strip()
    
    # Time range (e.g., "6:00 AM - 9:00 AM")
    line_item['time_range'] = time_range.strip()
    
    # Convert to 24-hour format
    time_parts = time_range.split('-')
    if len(time_parts) == 2:
        line_item['from_time'] = _convert_to_24hr(time_parts[0].strip())
        line_item['to_time'] = _convert_to_24hr(time_parts[1].strip())
    
    # Duration - store as-is, will be formatted when entering into Etere
    line_item['duration'] = length
    line_item['duration_formatted'] = _format_duration_for_etere(length)
    
    # Spots per week
    line_item['spots'] = int(spots_per_week)
    
    # Total spots (No. Units column)
    line_item['total_spots'] = int(total_spots)
    
    # Rate (remove $ and commas)
    line_item['rate'] = rate.replace('$', '').replace(',', '')
    
    # Days of week - parse exact days from X pattern
    line_item['days_of_week'] = _parse_days_pattern(days_str)
    
    return line_item

def _parse_days_pattern(days_str):
    """
    Parse days pattern from X/0 string to day abbreviations
    
    Format in PDF: M Tu W Th F Sa Su
    Position:      0  1 2  3 4  5  6
    
    Examples:
        "X X X X X X X" → "M-Su" (all week)
        "X X X X X 0 0" → "M-F" (weekdays)
        "0 0 0 0 0 X X" → "Sa-Su" (weekends)
        "X X 0 X X X X" → "M-Tu,Th-Su" (no Wednesday)
    
    Args:
        days_str: String containing X and 0 characters
        
    Returns:
        Formatted day string (e.g., "M-F", "M-Tu,Th-Su")
    """
    # Day abbreviations in order
    day_abbrev = ['M', 'Tu', 'W', 'Th', 'F', 'Sa', 'Su']
    
    # Extract just the X and 0 characters (remove spaces)
    pattern = days_str.replace(' ', '')
    
    # Build list of active days
    active_days = []
    for i, char in enumerate(pattern):
        if i < len(day_abbrev) and char == 'X':
            active_days.append(i)
    
    # If no days or all days, return simple format
    if not active_days:
        return 'M-Su'  # Default
    
    if len(active_days) == 7:
        return 'M-Su'
    
    # Check for common patterns
    if active_days == [0, 1, 2, 3, 4]:
        return 'M-F'
    
    if active_days == [5, 6]:
        return 'Sa-Su'
    
    # Build custom pattern with ranges
    return _format_day_ranges(active_days, day_abbrev)

def _format_day_ranges(active_days, day_abbrev):
    """
    Format list of day indices into readable range format
    
    Examples:
        [0,1,2] → "M-W"
        [0,1,3,4] → "M-Tu,Th-F"
        [0,2,4,6] → "M,W,F,Su"
    """
    if not active_days:
        return 'M-Su'
    
    ranges = []
    start = active_days[0]
    end = active_days[0]
    
    for i in range(1, len(active_days)):
        if active_days[i] == end + 1:
            # Consecutive day, extend range
            end = active_days[i]
        else:
            # Gap found, save current range and start new one
            ranges.append((start, end))
            start = active_days[i]
            end = active_days[i]
    
    # Add final range
    ranges.append((start, end))
    
    # Format ranges
    formatted = []
    for start, end in ranges:
        if start == end:
            # Single day
            formatted.append(day_abbrev[start])
        else:
            # Range
            formatted.append(f"{day_abbrev[start]}-{day_abbrev[end]}")
    
    return ','.join(formatted)

def _format_duration_for_etere(duration_seconds):
    """
    Format duration for Etere entry - use 8-digit frame-based format
    
    Etere expects duration as HHMMSSFF (no punctuation):
    - HH = hours (00)
    - MM = minutes (00-59)
    - SS = seconds (00-59)
    - FF = frames (00)
    
    Examples:
    - 15 seconds → "00001500" (00 hours, 00 minutes, 15 seconds, 00 frames)
    - 30 seconds → "00003000" (00 hours, 00 minutes, 30 seconds, 00 frames)
    - 60 seconds → "00010000" (00 hours, 01 minute, 00 seconds, 00 frames)
    - 120 seconds → "00020000" (00 hours, 02 minutes, 00 seconds, 00 frames)
    
    Args:
        duration_seconds: Duration in seconds (as string or int)
        
    Returns:
        8-digit string in HHMMSSFF format
    """
    try:
        seconds = int(duration_seconds)
        
        # Calculate hours, minutes, seconds
        hours = seconds // 3600
        remaining = seconds % 3600
        minutes = remaining // 60
        secs = remaining % 60
        frames = 0  # Always 00 for frames
        
        # Format as 8-digit string: HHMMSSFF
        return f"{hours:02d}{minutes:02d}{secs:02d}{frames:02d}"
            
    except (ValueError, TypeError):
        # If we can't parse it, return 30 seconds as default
        return "00003000"

def _convert_to_24hr(time_str):
    """Convert 12-hour time to 24-hour format (HH:MM)"""
    try:
        # Parse time like "9:00 AM" or "5:00 PM"
        time_str = time_str.strip()
        
        # Handle special cases
        if '12:00 AM' in time_str.upper():
            return '00:00'
        if '2:00 AM' in time_str.upper() or '2AM' in time_str.upper():
            return '02:00'
        
        # Try standard format
        dt = datetime.strptime(time_str, '%I:%M %p')
        return dt.strftime('%H:%M')
    except:
        # Try without space: "9:00AM"
        try:
            dt = datetime.strptime(time_str.replace(' ', ''), '%I:%M%p')
            return dt.strftime('%H:%M')
        except:
            print(f"[PARSER] Could not parse time: {time_str}")
            return '00:00'

def validate_parsed_data(order_data):
    """Validate that parsed data has required fields"""
    required_fields = ['order_code', 'lines', 'order_type']
    
    for field in required_fields:
        if field not in order_data:
            print(f"[PARSER] Missing required field: {field}")
            return False
    
    if not order_data['lines']:
        print(f"[PARSER] No line items found")
        return False
    
    # Validate each line has minimum required fields
    for line in order_data['lines']:
        required_line_fields = ['line_number', 'action', 'start_date', 'end_date', 'spots', 'rate']
        for field in required_line_fields:
            if field not in line:
                print(f"[PARSER] Line {line.get('line_number', '?')} missing field: {field}")
                return False
    
    return True

# Test function
if __name__ == "__main__":
    import sys
    
    if len(sys.argv) < 2:
        print("Usage: python worldlink_parser.py <pdf_file>")
        sys.exit(1)
    
    pdf_path = sys.argv[1]
    print(f"Parsing: {pdf_path}")
    
    data = parse_worldlink_pdf(pdf_path)
    
    if data:
        print("\n✓ Successfully parsed!")
        print(f"Order Type: {data.get('order_type').upper()}")
        print(f"Network: {data.get('network')}")
        print(f"Order Code: {data.get('order_code')}")
        print(f"Tracking Number: {data.get('tracking_number')}")
        print(f"Advertiser: {data.get('advertiser')}")
        print(f"Description: {data.get('description')}")
        print(f"Lines found: {len(data.get('lines', []))}")
        
        if validate_parsed_data(data):
            print("\n✓ Data validation passed")
            
            # Print line details
            for line in data['lines']:
                print(f"\nLine {line['line_number']} [{line['action']}]:")
                print(f"  Dates: {line.get('start_date')} to {line.get('end_date')}")
                print(f"  Time: {line.get('time_range')} ({line.get('from_time')} - {line.get('to_time')})")
                print(f"  Duration: {line.get('duration')} seconds")
                print(f"  Spots/Week: {line.get('spots')}")
                print(f"  Total Units: {line.get('total_spots')}")
                print(f"  Rate: ${line.get('rate')}")
                print(f"  Days: {line.get('days_of_week')}")
        else:
            print("\n✗ Data validation failed")
    else:
        print("\n✗ Parsing failed")