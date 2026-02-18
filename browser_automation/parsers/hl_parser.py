"""
H&L Partners Order Parser
Parses H&L Partners agency insertion order PDFs using Strata IO system.
Each estimate number represents a separate contract.
"""

import pdfplumber
import re
from datetime import datetime
from dataclasses import dataclass
from typing import List, Dict, Optional, Tuple


@dataclass
class HLLine:
    """Represents a single line item from H&L Partners order."""
    station: str
    days: str
    daypart: str  # RT or VE AV
    time: str
    program: str
    duration: int
    weekly_spots: List[int]  # Spots per week across the flight
    rate: float
    total_spots: int
    total_cost: float
    
    def is_bonus(self) -> bool:
        """Check if this is a bonus/value-added line."""
        return self.rate == 0.0


@dataclass
class HLEstimate:
    """Represents a single estimate (contract) from H&L Partners order."""
    estimate_number: str
    description: str  # e.g., "JAN26 Asian Cable"
    flight_start: str
    flight_end: str
    client: str
    buyer: str
    market: str
    lines: List[HLLine]


def parse_hl_pdf(pdf_path: str) -> List[HLEstimate]:
    """
    Parse H&L Partners PDF and extract all estimates.
    
    Args:
        pdf_path: Path to the H&L Partners PDF file
        
    Returns:
        List of HLEstimate objects, one per estimate number
    """
    estimates = []
    
    with pdfplumber.open(pdf_path) as pdf:
        current_estimate = None
        
        for page_num, page in enumerate(pdf.pages):
            text = page.extract_text()
            
            # Check if this is a new estimate page (has estimate number and table headers)
            # H&L uses "Estimate:" and "Line No" or "Daypart Program" headers
            if "Estimate:" in text and ("Daypart" in text and "Program" in text):
                # Skip summary pages (they have "Summary by Week" or "Summary by Market")
                if "Summary by" in text:
                    continue
                    
                # Extract estimate header information
                estimate_data = _extract_estimate_header(text)
                
                if estimate_data:
                    # If we were building an estimate, save it
                    if current_estimate:
                        estimates.append(current_estimate)
                    
                    # Start new estimate
                    current_estimate = HLEstimate(
                        estimate_number=estimate_data['estimate'],
                        description=estimate_data['description'],
                        flight_start=estimate_data['flight_start'],
                        flight_end=estimate_data['flight_end'],
                        client=estimate_data['client'],
                        buyer=estimate_data['buyer'],
                        market=estimate_data['market'],
                        lines=[]
                    )
                    
                    # Extract lines from this page
                    lines = _extract_lines_from_page(text)
                    
                    # Only add lines if we actually found some
                    if lines:
                        current_estimate.lines.extend(lines)
                    elif current_estimate and not current_estimate.lines:
                        # First page with no lines - might be duplicate, skip it
                        current_estimate = None
        
        # Don't forget the last estimate
        if current_estimate:
            estimates.append(current_estimate)
    
    return estimates


def _extract_estimate_header(text: str) -> Optional[Dict[str, str]]:
    """Extract header information from an estimate page."""
    header = {}
    
    # Extract estimate number
    estimate_match = re.search(r'Estimate:\s*(\d+)', text)
    if estimate_match:
        header['estimate'] = estimate_match.group(1)
    else:
        return None
    
    # Extract description
    desc_match = re.search(r'Description:\s*([^\n]+?)(?:\s+100\s+Webster|\s+Flight Start Date:|\n)', text)
    if desc_match:
        header['description'] = desc_match.group(1).strip()
    else:
        header['description'] = ''
    
    # Extract flight dates
    # H&L format: "Flight Start Date: 1/5/2026 03:00 AM"
    flight_start_match = re.search(r'Flight Start Date:\s*(\d{1,2}/\d{1,2}/\d{4})', text)
    flight_end_match = re.search(r'Flight End Date:\s*(\d{1,2}/\d{1,2}/\d{4})', text)
    
    if flight_start_match and flight_end_match:
        header['flight_start'] = flight_start_match.group(1)
        header['flight_end'] = flight_end_match.group(1)
    else:
        header['flight_start'] = 'Unknown'
        header['flight_end'] = 'Unknown'
    
    # Extract client
    client_match = re.search(r'Client:\s*([^\n]+?)(?:\s+Estimate:|\s+Vendor:)', text)
    if client_match:
        header['client'] = client_match.group(1).strip()
    else:
        header['client'] = 'Unknown'
    
    # Extract buyer
    buyer_match = re.search(r'Buyer:\s*([^\n]+?)(?:\s+Fax:|\n)', text)
    if buyer_match:
        header['buyer'] = buyer_match.group(1).strip()
    else:
        header['buyer'] = 'Unknown'
    
    # Extract market
    market_match = re.search(r'Market:\s*([^\n]+?)(?:\s+Flight End Date:|\n)', text)
    if market_match:
        header['market'] = market_match.group(1).strip()
    else:
        header['market'] = 'Unknown'
    
    return header


def _extract_lines_from_page(text: str) -> List[HLLine]:
    """Extract all line items from a page."""
    lines = []
    
    # Split text into lines
    text_lines = text.split('\n')
    
    # Find the start of the table - look for "CRTV-TV" as station marker
    table_start = None
    
    for i, line in enumerate(text_lines):
        # Look for standalone "CRTV-TV" line which marks table start
        if line.strip() == "CRTV-TV":
            table_start = i + 1  # Data starts on next line
            break
    
    if table_start is None:
        return lines
    
    # Parse each line entry
    i = table_start
    while i < len(text_lines):
        line = text_lines[i]
        
        # Stop at summary lines
        if 'Total Spots:' in line or 'Total GRP' in line or 'Disclaimer:' in line or 'Signature:' in line:
            break
        
        # Check if this is a data line (starts with line number followed by day pattern)
        if re.match(r'^\d+\s+[A-Z]', line):
            line_obj, next_idx = _parse_line_entry(text_lines, i)
            if line_obj:
                lines.append(line_obj)
                i = next_idx
            else:
                i += 1
        else:
            i += 1
    
    return lines


def _parse_line_entry(text_lines: List[str], start_index: int) -> Tuple[Optional[HLLine], int]:
    """
    Parse a single line entry from the text.
    H&L format: Line# Days Time Daypart $Rate Dur Week1 Week2... Total Rating
    Program name on next line.
    
    Example:
    "1 MTuWThF 1:00p- 2:00p EF $50.00 30 0 0 3 3 6 0.0"
    "HINDI NEWS/TALK $0.00"
    
    Args:
        text_lines: All text lines from the page
        start_index: Index of the line with data
        
    Returns:
        Tuple of (HLLine object or None, next index to continue parsing)
    """
    line = text_lines[start_index]
    
    try:
        parts = line.split()
        
        if len(parts) < 5:
            return None, start_index + 1
        
        # First part is line number
        line_number = parts[0]
        
        # Days: MTuWThF, SaSu, MTuWThFSaSu, etc.
        days = parts[1]
        
        # Time can be in parts[2] alone or split across parts[2] and parts[3]
        # Examples: "1:00p-" "2:00p" or "7:00p-" or "7:00p-8:00p"
        time_parts = []
        idx = 2
        
        # Collect time parts until we hit the daypart code (2 letters like EF, EN, PA, PT)
        while idx < len(parts) and not (len(parts[idx]) == 2 and parts[idx].isupper() and parts[idx].isalpha()):
            time_parts.append(parts[idx])
            idx += 1
        
        # Build time string from collected parts
        # The parts might be: ['7:00p-', 'Asian', '8:00p'] where we want '7:00p-8:00p'
        # Strategy: Find all time patterns and connect them
        time_patterns = []
        for part in time_parts:
            if re.match(r'\d+:\d+[ap]', part):
                time_patterns.append(part)
            elif part.endswith('-') and re.match(r'\d+:\d+[ap]-$', part):
                # Part is like "7:00p-"
                time_patterns.append(part)
        
        # Join time patterns
        if len(time_patterns) >= 2:
            # We have start and end
            start = time_patterns[0].rstrip('-')
            end = time_patterns[1]
            time_str = f"{start}-{end}"
        elif len(time_patterns) == 1:
            time_str = time_patterns[0]
        else:
            time_str = ''.join(time_parts)
        
        # Handle case where time wraps to next text line entirely
        if time_str.endswith('-'):
            # Check if the NEXT TEXT LINE starts with a time pattern
            next_line_idx = start_index + 1
            if next_line_idx < len(text_lines):
                next_line = text_lines[next_line_idx].strip()
                # Check if next line starts with a time (e.g., "9:00p EN $65.00..." or just "9:00p")
                time_continuation = re.match(r'^(\d+:\d+[ap])', next_line)
                if time_continuation:
                    # Complete the time
                    time_str += time_continuation.group(1)
                    print(f"        [DEBUG] Time wrapped to next line, completed: {time_str}")
        
        # Normalize time (ensure it has a dash)
        time_match = re.match(r'(\d+:\d+[ap])[-\s]*(\d+:\d+[ap])', time_str)
        if time_match:
            time = f"{time_match.group(1)}-{time_match.group(2)}"
        else:
            time = time_str
        
        # Daypart code (idx should now point to it)
        if idx >= len(parts):
            return None, start_index + 1
        daypart_code = parts[idx]
        idx += 1
        
        # Rate: $50.00
        if idx >= len(parts):
            return None, start_index + 1
        rate_str = parts[idx].replace('$', '').replace(',', '')
        rate = float(rate_str)
        idx += 1
        
        # Duration: 30
        if idx >= len(parts):
            return None, start_index + 1
        duration = int(parts[idx])
        idx += 1
        
        # Now comes weekly spots followed by total spots and rating
        # Weekly spots are integers, then total spots (integer), then rating (float with decimal)
        # Example: 0 0 3 3 6 0.0
        # The last value with decimal is the rating, second to last is total spots
        
        remaining_numbers = []
        while idx < len(parts):
            try:
                # Try to parse as number
                num_str = parts[idx].replace(',', '')
                num = float(num_str)
                remaining_numbers.append(num)
                idx += 1
            except ValueError:
                break
        
        if len(remaining_numbers) < 2:
            return None, start_index + 1
        
        # Last number is rating (has decimal), second to last is total spots
        rating = remaining_numbers[-1]
        total_spots = int(remaining_numbers[-2])
        
        # Everything else is weekly spots
        weekly_spots = [int(n) for n in remaining_numbers[:-2]]
        
        # Program name is on the NEXT line (or line after that if time wrapped)
        next_idx = start_index + 1
        program = "Unknown Program"
        
        if next_idx < len(text_lines):
            next_line = text_lines[next_idx].strip()
            
            # Check if this line is just a time fragment (e.g., "9:00p")
            # If so, skip it and get the program from the line after
            if re.match(r'^\d+:\d+[ap]$', next_line):
                # This is a time fragment, skip it
                next_idx += 1
                if next_idx < len(text_lines):
                    next_line = text_lines[next_idx].strip()
            
            # Remove trailing cost info (like "$0.00")
            clean_line = re.sub(r'\$[\d,]+\.?\d*$', '', next_line).strip()
            if clean_line and not re.match(r'^\d+\s+[A-Z]', clean_line):
                program = clean_line
                next_idx += 1
        
        # Calculate total cost
        total_cost = rate * total_spots
        
        # Station is always CRTV-TV for H&L
        station = "CRTV-TV"
        
        line_obj = HLLine(
            station=station,
            days=days,
            daypart=daypart_code,
            time=time,
            program=program,
            duration=duration,
            weekly_spots=weekly_spots,
            rate=rate,
            total_spots=total_spots,
            total_cost=total_cost
        )
        
        return line_obj, next_idx
        
    except (IndexError, ValueError) as e:
        print(f"Error parsing line at index {start_index}: {e}")
        print(f"Line content: {line}")
        return None, start_index + 1


def format_time_for_description(time: str) -> str:
    """
    Format time for line description.
    Input: "6:00a- 7:00a" or "8:00a-10:00a"
    Output: "6-7a" or "8-10a"
    """
    # Remove spaces
    time = time.replace(' ', '')
    
    # Extract start and end
    match = re.match(r'(\d+):00([ap])-(\d+):00([ap])', time)
    if match:
        start_hour = match.group(1)
        start_period = match.group(2)
        end_hour = match.group(3)
        end_period = match.group(4)
        
        # Format: 6-7a or 7p-12a
        if start_period == end_period:
            return f"{start_hour}-{end_hour}{start_period}"
        else:
            return f"{start_hour}{start_period}-{end_hour}{end_period}"
    
    return time


def convert_hl_days_to_etere(hl_days: str) -> str:
    """
    Convert H&L day format to Etere format.
    
    H&L uses: MTuWThF, SaSu, MTuWThFSaSu
    Etere uses: M-F, Sa-Su, M-Su
    
    Args:
        hl_days: Day string from H&L (e.g., "MTuWThF")
        
    Returns:
        Etere format day string (e.g., "M-F")
    """
    # Map H&L patterns to Etere patterns
    mapping = {
        'MTuWThF': 'M-F',
        'MTuWThFSa': 'M-Sa',
        'MTuWThFSaSu': 'M-Su',
        'SaSu': 'Sa-Su',
        'Su': 'Su',
        'Sa': 'Sa',
        'M': 'M',
        'Tu': 'Tu',
        'W': 'W',
        'Th': 'Th',
        'F': 'F',
        'WThF': 'WThF',
        'WThFSaSu': 'WThFSaSu',
        'WThFSa': 'WThFSa',
        'ThF': 'ThF',
        'ThFSaSu': 'ThFSaSu',
    }
    
    return mapping.get(hl_days, hl_days)


# ═══════════════════════════════════════════════════════════════════════════════
# LANGUAGE UTILITIES - imported from universal language_utils
# ═══════════════════════════════════════════════════════════════════════════════
#
# extract_language_from_program() → use language_utils.extract_language_from_program()
# get_language_block_prefix()     → use language_utils.get_language_block_prefixes()
#
# These were removed from this file to avoid duplication.
# Import from browser_automation.language_utils instead.
# ═══════════════════════════════════════════════════════════════════════════════

from browser_automation.language_utils import (
    extract_language_from_program,
    get_language_block_prefixes as get_language_block_prefix,
)


def analyze_weekly_distribution(weekly_spots: List[int], flight_start: str, flight_end: str) -> List[Dict]:
    """
    Analyze weekly distribution and determine if lines need splitting.
    
    UNIVERSAL RULES:
    - **SKIP weeks with 0 spots entirely** - do not create lines for them
    - Split on gaps (weeks with 0 spots)
    - Split when spot counts change (e.g., [5,5,7] becomes two ranges: [5,5] and [7])
    - Only combine consecutive weeks with IDENTICAL spot counts
    - Always cap calculated end dates at contract/flight end date
    
    Args:
        weekly_spots: List of spots per week (e.g., [0, 0, 3, 3])
        flight_start: Flight start date (MM/DD/YYYY)
        flight_end: Flight end date (MM/DD/YYYY)
        
    Returns:
        List of dicts with {start_date, end_date, spots, spots_per_week, num_weeks}
        Empty list if all weeks have 0 spots
        
    Example:
        weekly_spots = [0, 0, 3, 3]
        flight_start = "1/5/2026"
        flight_end = "2/1/2026"
        
        Returns: [{'start_date': '1/19/2026', 'end_date': '2/1/2026', 
                   'spots': 6, 'spots_per_week': 3, 'num_weeks': 2}]
        
        Result: Only ONE line created in Etere (weeks 1/5 and 1/12 skipped)
    """
    from datetime import datetime, timedelta
    
    # Parse dates
    start_date = datetime.strptime(flight_start, "%m/%d/%Y")
    flight_end_dt = datetime.strptime(flight_end, "%m/%d/%Y")
    
    # Find consecutive week ranges with IDENTICAL spot counts
    ranges = []
    current_range_start = None
    current_range_spots_per_week = None
    current_range_week_count = 0
    current_week_date = start_date
    
    for week_idx, spots in enumerate(weekly_spots):
        if spots > 0:
            if current_range_start is None:
                # Start new range
                current_range_start = current_week_date
                current_range_spots_per_week = spots
                current_range_week_count = 1
            elif spots == current_range_spots_per_week:
                # Same spot count - continue range
                current_range_week_count += 1
            else:
                # Different spot count - end current range and start new one
                range_end = current_week_date - timedelta(days=1)
                
                # Cap at flight end date
                if range_end > flight_end_dt:
                    range_end = flight_end_dt
                
                ranges.append({
                    'start_date': current_range_start.strftime("%m/%d/%Y"),
                    'end_date': range_end.strftime("%m/%d/%Y"),
                    'spots': current_range_spots_per_week * current_range_week_count,
                    'spots_per_week': current_range_spots_per_week,
                    'num_weeks': current_range_week_count
                })
                
                # Start new range with different spot count
                current_range_start = current_week_date
                current_range_spots_per_week = spots
                current_range_week_count = 1
        else:
            # Gap (0 spots)
            if current_range_start is not None:
                # End current range
                range_end = current_week_date - timedelta(days=1)
                
                # Cap at flight end date
                if range_end > flight_end_dt:
                    range_end = flight_end_dt
                
                ranges.append({
                    'start_date': current_range_start.strftime("%m/%d/%Y"),
                    'end_date': range_end.strftime("%m/%d/%Y"),
                    'spots': current_range_spots_per_week * current_range_week_count,
                    'spots_per_week': current_range_spots_per_week,
                    'num_weeks': current_range_week_count
                })
                
                current_range_start = None
                current_range_spots_per_week = None
                current_range_week_count = 0
        
        # Move to next week
        current_week_date += timedelta(days=7)
    
    # Don't forget the last range
    if current_range_start is not None:
        # Use flight end date (already capped)
        ranges.append({
            'start_date': current_range_start.strftime("%m/%d/%Y"),
            'end_date': flight_end,
            'spots': current_range_spots_per_week * current_range_week_count,
            'spots_per_week': current_range_spots_per_week,
            'num_weeks': current_range_week_count
        })
    
    return ranges


if __name__ == '__main__':
    # Test the parser
    import sys
    
    if len(sys.argv) > 1:
        pdf_path = sys.argv[1]
    else:
        pdf_path = '/mnt/user-data/uploads/hl_sample.pdf'
    
    print(f"Parsing H&L Partners PDF: {pdf_path}\n")
    
    try:
        estimates = parse_hl_pdf(pdf_path)
        
        print(f"Found {len(estimates)} estimates:\n")
        
        for est in estimates:
            print(f"Estimate {est.estimate_number}: {est.description}")
            print(f"  Flight: {est.flight_start} - {est.flight_end}")
            print(f"  Market: {est.market}")
            print(f"  Client: {est.client}")
            print(f"  Buyer: {est.buyer}")
            print(f"  Lines: {len(est.lines)}")
            
            for i, line in enumerate(est.lines, 1):
                bonus = " [BONUS]" if line.is_bonus() else ""
                lang = extract_language_from_program(line.program)
                time_fmt = format_time_for_description(line.time)
                print(f"    {i}. {line.days} {time_fmt} {lang} - {line.program}{bonus}")
                print(f"       Rate: ${line.rate}, Total: {line.total_spots} spots")
                print(f"       Weekly: {line.weekly_spots}")
            
            print()
    
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
