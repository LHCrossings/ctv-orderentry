"""
opAD Order Parser
Parses opAD agency insertion order PDFs for NYC market (Crossings TV Network)
Handles weekly spot distribution, language-based programming, and bonus lines
"""

import pdfplumber
import re
from datetime import datetime, timedelta
from dataclasses import dataclass
from typing import List, Dict, Optional


@dataclass
class OpADLine:
    """Represents a single line item from opAD order."""
    station: str
    days: str
    time: str
    program: str
    duration: int
    weekly_spots: List[int]  # Spots per week across the flight
    total_spots: int
    rate: float
    total_cost: float
    language: Optional[str] = None  # Mandarin, Cantonese, etc.
    
    def is_bonus(self) -> bool:
        """Check if this is a bonus/value-added line."""
        return self.rate == 0.0


@dataclass
class OpADOrder:
    """Represents an opAD order."""
    client: str
    estimate_number: str
    description: str
    market: str
    product: str
    flight_start: str
    flight_end: str
    week_start_dates: List[str]  # Start date of each week
    lines: List[OpADLine]


def parse_opad_pdf(pdf_path: str) -> OpADOrder:
    """
    Parse opAD PDF and extract order data.
    
    Args:
        pdf_path: Path to the opAD PDF file
        
    Returns:
        OpADOrder object with all order details
    """
    with pdfplumber.open(pdf_path) as pdf:
        # Extract header from first page
        first_page = pdf.pages[0]
        text = first_page.extract_text()
        
        header_data = _extract_header(text)
        
        # Extract week start dates from header row
        week_dates = _extract_week_dates(text)
        
        # Extract all lines from all pages
        all_lines = []
        for page in pdf.pages:
            page_text = page.extract_text()
            lines = _extract_lines_from_page(page_text, len(week_dates))
            all_lines.extend(lines)
        
        return OpADOrder(
            client=header_data['client'],
            estimate_number=header_data['estimate'],
            description=header_data['description'],
            market=header_data['market'],
            product=header_data['product'],
            flight_start=header_data['flight_start'],
            flight_end=header_data['flight_end'],
            week_start_dates=week_dates,
            lines=all_lines
        )


def _extract_header(text: str) -> Dict[str, str]:
    """Extract header information from first page."""
    header = {}
    
    # Extract client
    client_match = re.search(r'Client:\s*(.+?)(?:\n|Media:)', text)
    if client_match:
        header['client'] = client_match.group(1).strip()
    else:
        header['client'] = 'Unknown'
    
    # Extract estimate number
    estimate_match = re.search(r'Estimate:\s*(\d+)', text)
    if estimate_match:
        header['estimate'] = estimate_match.group(1)
    else:
        header['estimate'] = 'Unknown'
    
    # Extract description
    desc_match = re.search(r'Description:\s*(.+?)(?:\n|Market:)', text)
    if desc_match:
        header['description'] = desc_match.group(1).strip()
    else:
        header['description'] = ''
    
    # Extract market
    market_match = re.search(r'Market:\s*(.+?)(?:\n|Estimate:)', text)
    if market_match:
        header['market'] = market_match.group(1).strip()
    else:
        header['market'] = 'New York'
    
    # Extract product
    product_match = re.search(r'Product:\s*(.+?)(?:\n|#)', text)
    if product_match:
        header['product'] = product_match.group(1).strip()
    else:
        header['product'] = ''
    
    # Extract flight dates
    # Format can be: "Flight Date: 12/19/2025\n1/5/2026-1/31/2026"
    flight_match = re.search(r'Flight Date:\s*(?:\d{1,2}/\d{1,2}/\d{4}\s*)?\n?\s*(\d{1,2}/\d{1,2}/\d{4})\s*-\s*(\d{1,2}/\d{1,2}/\d{4})', text, re.MULTILINE)
    if flight_match:
        start_date = flight_match.group(1)
        end_date = flight_match.group(2)
        
        # Parse and reformat to ensure MM/DD/YYYY
        start_dt = datetime.strptime(start_date, '%m/%d/%Y')
        end_dt = datetime.strptime(end_date, '%m/%d/%Y')
        
        header['flight_start'] = start_dt.strftime('%m/%d/%Y')
        header['flight_end'] = end_dt.strftime('%m/%d/%Y')
    else:
        header['flight_start'] = 'Unknown'
        header['flight_end'] = 'Unknown'
    
    return header


def _extract_week_dates(text: str) -> List[str]:
    """
    Extract week start dates from the header row.
    
    Example header: "1/5  1/12  1/19  1/26"
    Returns: ['1/5/2026', '1/12/2026', '1/19/2026', '1/26/2026']
    """
    week_dates = []
    
    # Look for the week header pattern - dates without year
    # They appear in the header row like: "1/5 1/12 1/19 1/26"
    
    # First, extract the flight start to get the year
    flight_match = re.search(r'Flight Date:\s*\d{1,2}/\d{1,2}/(\d{4})', text)
    year = flight_match.group(1) if flight_match else '2026'
    
    # Find the line with week dates (before "Total STN Gross")
    week_line_match = re.search(r'# of SPOTS PER WEEK\s*([\d/\s]+)\s*Total STN', text)
    if week_line_match:
        dates_str = week_line_match.group(1).strip()
        # Extract all M/D patterns
        date_patterns = re.findall(r'(\d{1,2}/\d{1,2})', dates_str)
        
        for date_pattern in date_patterns:
            # Add year to create full date
            full_date = f"{date_pattern}/{year}"
            # Validate and format
            try:
                dt = datetime.strptime(full_date, '%m/%d/%Y')
                week_dates.append(dt.strftime('%m/%d/%Y'))
            except ValueError:
                continue
    
    return week_dates


def _extract_lines_from_page(text: str, num_weeks: int) -> List[OpADLine]:
    """Extract line items from a page."""
    lines = []
    
    text_lines = text.split('\n')
    
    # Find where the table starts
    table_start = None
    for i, line in enumerate(text_lines):
        if 'Station Day Time Program Dur' in line:
            table_start = i + 1
            break
    
    if table_start is None:
        return lines
    
    # Parse each line
    i = table_start
    current_language = None
    
    while i < len(text_lines):
        line = text_lines[i]
        
        # Stop at summary lines
        if 'Station Total:' in line or 'SCHEDULE TOTALS' in line or 'Page:' in line:
            break
        
        # Check for language marker (standalone line with just language name)
        if line.strip() in ['MANDARIN', 'CANTONESE', 'KOREAN', 'VIETNAMESE', 'FILIPINO', 'SOUTH ASIAN', 'PUNJABI', 'HMONG']:
            current_language = line.strip().title()
            i += 1
            continue
        
        # Check if this line starts with a station or day pattern
        if re.match(r'^(CROSSINGS TV|M-Su|M-F|M-Sa|Sa-Su|M-Th|Tu-F|M|Tu|W|Th|F|Sa|Su)\b', line.strip()):
            line_obj, next_idx = _parse_line_entry(text_lines, i, num_weeks, current_language)
            if line_obj:
                lines.append(line_obj)
                i = next_idx
            else:
                i += 1
        else:
            i += 1
    
    return lines


def _parse_line_entry(text_lines: List[str], start_index: int, num_weeks: int, current_language: Optional[str]) -> tuple:
    """
    Parse a single line entry, handling multi-line program names.
    Language marker comes AFTER the line data (or program name wrap).
    
    Args:
        text_lines: All text lines from page
        start_index: Starting line index
        num_weeks: Number of weeks in schedule
        current_language: Current language context from previous lines (may be overridden)
        
    Returns:
        Tuple of (OpADLine or None, next_index)
    """
    line = text_lines[start_index]
    
    # Pattern: CROSSINGS TVTVM-Su 7:00p-11:00p PRIME 30 5 0 0 0 5 $0.00
    # Or: M-Su 7:00p-11:00p PRIME 30 5 0 0 0 5 $0.00
    
    try:
        # Remove "CROSSINGS TVTV" if present (seems to be artifact)
        line = line.replace('CROSSINGS TVTV', '').strip()
        
        # Extract components using regex
        # Station is either explicit or implied (CROSSINGS TV for all)
        station = "CROSSINGS TV"
        
        # Days pattern: M-Su, M-F, M-Sa, Sa-Su, or single days (M, Tu, W, Th, F, Sa, Su)
        days_match = re.search(r'(M-Su|M-F|M-Sa|Sa-Su|M-Th|Tu-F|Tu|Th|Su|Sa|M|W|F)\b', line)
        if not days_match:
            return None, start_index + 1
        
        days = days_match.group(1)
        
        # Time pattern: 7:00p-11:00p or 6:00a- 7:00a
        time_match = re.search(r'(\d{1,2}:\d{2}[ap])-?\s*(\d{1,2}:\d{2}[ap])', line)
        if not time_match:
            return None, start_index + 1
        
        time = f"{time_match.group(1)}-{time_match.group(2)}"
        
        # Extract program name and numbers - handle multi-line program names
        time_end_pos = time_match.end()
        remaining = line[time_end_pos:]
        
        # Duration is either 15 or 30
        dur_match = re.search(r'\s+(15|30)\s+', remaining)
        
        next_idx = start_index + 1
        
        if not dur_match:
            # Duration might be on next line (program name wrapped)
            if next_idx < len(text_lines):
                next_line = text_lines[next_idx].strip()
                
                # Check if next line is a program continuation (not a new line or language)
                is_new_line = re.match(r'^(CROSSINGS TV|M-Su|M-F|M-Sa|Sa-Su|M-Th|Tu-F|M|Tu|W|Th|F|Sa|Su)\b', next_line)
                is_language = next_line in ['MANDARIN', 'CANTONESE', 'KOREAN', 'VIETNAMESE', 'FILIPINO', 'SOUTH ASIAN', 'PUNJABI', 'HMONG']
                
                if not is_new_line and not is_language:
                    # This is a program name continuation
                    combined = remaining + ' ' + next_line
                    dur_match = re.search(r'\s+(15|30)\s+', combined)
                    
                    if dur_match:
                        duration = int(dur_match.group(1))
                        program = combined[:dur_match.start()].strip()
                        numbers_part = combined[dur_match.end():].strip()
                        next_idx = start_index + 2  # Skip the wrapped line
                    else:
                        return None, start_index + 1
                else:
                    return None, start_index + 1
            else:
                return None, start_index + 1
        else:
            # Duration found on same line
            duration = int(dur_match.group(1))
            program = remaining[:dur_match.start()].strip()
            numbers_part = remaining[dur_match.end():].strip()
        
        # Extract weekly spots and totals
        numbers = re.findall(r'[\d,]+\.?\d*', numbers_part)
        clean_numbers = [float(n.replace(',', '')) for n in numbers]
        
        # Structure: [week1, week2, ..., weekN, total_spots, rate]
        if len(clean_numbers) < num_weeks + 2:
            return None, start_index + 1
        
        weekly_spots = [int(n) for n in clean_numbers[:num_weeks]]
        total_spots = int(clean_numbers[num_weeks])
        rate = clean_numbers[num_weeks + 1]
        
        # Check for language marker AFTER the line data
        # Language comes on the line after the data (or after program wrap)
        detected_language = None
        if next_idx < len(text_lines):
            check_line = text_lines[next_idx].strip()
            if check_line in ['MANDARIN', 'CANTONESE', 'KOREAN', 'VIETNAMESE', 'FILIPINO', 'SOUTH ASIAN', 'PUNJABI', 'HMONG']:
                detected_language = check_line.title()
                next_idx += 1  # Skip the language line
        
        # Use detected language if found, otherwise use context
        line_language = detected_language if detected_language else current_language
        
        # Calculate total cost
        total_cost = rate * total_spots
        
        line_obj = OpADLine(
            station=station,
            days=days,
            time=time,
            program=program,
            duration=duration,
            weekly_spots=weekly_spots,
            total_spots=total_spots,
            rate=rate,
            total_cost=total_cost,
            language=line_language
        )
        
        return line_obj, next_idx
        
    except (IndexError, ValueError) as e:
        print(f"Error parsing line at index {start_index}: {e}")
        return None, start_index + 1


def format_time_for_description(time: str) -> str:
    """
    Format time for line description.
    Input: "6:00a-7:00a" or "7:00p-11:00p"
    Output: "6-7a" or "7-11p"
    """
    time = time.replace(' ', '')
    
    match = re.match(r'(\d+):00([ap])-(\d+):00([ap])', time)
    if match:
        start_hour = match.group(1)
        start_period = match.group(2)
        end_hour = match.group(3)
        end_period = match.group(4)
        
        if start_period == end_period:
            return f"{start_hour}-{end_hour}{start_period}"
        else:
            return f"{start_hour}{start_period}-{end_hour}{end_period}"
    
    return time


def analyze_weekly_distribution(weekly_spots: List[int], week_start_dates: List[str], 
                               contract_end_date: Optional[str] = None) -> List[Dict]:
    """
    Analyze weekly distribution to determine if lines need splitting.
    
    RULES:
    - Split on gaps (weeks with 0 spots)
    - Split when spot counts change (e.g., [5,5,7] becomes two ranges: [5,5] and [7])
    - Only combine consecutive weeks with IDENTICAL spot counts
    
    Args:
        weekly_spots: List of spots per week (e.g., [5, 5, 0, 7, 7])
        week_start_dates: List of week start dates matching weekly_spots
        contract_end_date: Optional contract end date to cap last week (e.g., "01/31/2026")
        
    Returns:
        List of dicts with {start_date, end_date, spots, spots_per_week, num_weeks}
    """
    from datetime import datetime, timedelta
    
    ranges = []
    current_range_start = None
    current_range_spots_per_week = None
    current_range_week_count = 0
    current_range_first_week_idx = None
    
    for week_idx, spots in enumerate(weekly_spots):
        if spots > 0:
            if current_range_start is None:
                # Start new range
                current_range_start = week_start_dates[week_idx]
                current_range_spots_per_week = spots
                current_range_week_count = 1
                current_range_first_week_idx = week_idx
            elif spots == current_range_spots_per_week:
                # Same spot count - continue range
                current_range_week_count += 1
            else:
                # Different spot count - end current range and start new one
                # Calculate end date for current range
                last_week_start = week_start_dates[week_idx - 1]
                end_dt = datetime.strptime(last_week_start, '%m/%d/%Y') + timedelta(days=6)
                
                # Cap at contract end date if provided
                if contract_end_date:
                    contract_end_dt = datetime.strptime(contract_end_date, '%m/%d/%Y')
                    if end_dt > contract_end_dt:
                        end_dt = contract_end_dt
                
                ranges.append({
                    'start_date': current_range_start,
                    'end_date': end_dt.strftime('%m/%d/%Y'),
                    'spots': current_range_spots_per_week * current_range_week_count,
                    'spots_per_week': current_range_spots_per_week,
                    'num_weeks': current_range_week_count
                })
                
                # Start new range with different spot count
                current_range_start = week_start_dates[week_idx]
                current_range_spots_per_week = spots
                current_range_week_count = 1
                current_range_first_week_idx = week_idx
        else:
            # Gap (0 spots)
            if current_range_start is not None:
                # End current range
                last_week_start = week_start_dates[week_idx - 1]
                end_dt = datetime.strptime(last_week_start, '%m/%d/%Y') + timedelta(days=6)
                
                # Cap at contract end date if provided
                if contract_end_date:
                    contract_end_dt = datetime.strptime(contract_end_date, '%m/%d/%Y')
                    if end_dt > contract_end_dt:
                        end_dt = contract_end_dt
                
                ranges.append({
                    'start_date': current_range_start,
                    'end_date': end_dt.strftime('%m/%d/%Y'),
                    'spots': current_range_spots_per_week * current_range_week_count,
                    'spots_per_week': current_range_spots_per_week,
                    'num_weeks': current_range_week_count
                })
                
                current_range_start = None
                current_range_spots_per_week = None
                current_range_week_count = 0
    
    # Don't forget the last range
    if current_range_start is not None:
        # End date is 6 days after the last week's start
        last_week_start = week_start_dates[len(weekly_spots) - 1]
        end_dt = datetime.strptime(last_week_start, '%m/%d/%Y') + timedelta(days=6)
        
        # Cap at contract end date if provided
        if contract_end_date:
            contract_end_dt = datetime.strptime(contract_end_date, '%m/%d/%Y')
            if end_dt > contract_end_dt:
                end_dt = contract_end_dt
        
        ranges.append({
            'start_date': current_range_start,
            'end_date': end_dt.strftime('%m/%d/%Y'),
            'spots': current_range_spots_per_week * current_range_week_count,
            'spots_per_week': current_range_spots_per_week,
            'num_weeks': current_range_week_count
        })
    
    return ranges


def get_language_block_prefix(language: Optional[str]) -> List[str]:
    """
    Get block prefix(es) for a given language.
    
    Args:
        language: Language name (e.g., "Mandarin", "Cantonese")
        
    Returns:
        List of block prefixes to filter by
    """
    if not language:
        return []
    
    mapping = {
        'Mandarin': ['M'],
        'Cantonese': ['C'],
        'Korean': ['K'],
        'Vietnamese': ['V'],
        'Filipino': ['T'],
        'South Asian': ['SA'],
        'Punjabi': ['P'],
        'Hindi': ['SA'],
        'Hmong': ['Hm'],
        'Japanese': ['J']
    }
    
    return mapping.get(language, [])


if __name__ == '__main__':
    # Test the parser
    import sys
    
    if len(sys.argv) > 1:
        pdf_path = sys.argv[1]
    else:
        pdf_path = '/mnt/user-data/uploads/NYSDOH-NYSOH-2824_CROSSINGS.pdf'
    
    print(f"Parsing opAD PDF: {pdf_path}\n")
    
    try:
        order = parse_opad_pdf(pdf_path)
        
        print(f"Client: {order.client}")
        print(f"Estimate: {order.estimate_number}")
        print(f"Description: {order.description}")
        print(f"Market: {order.market}")
        print(f"Product: {order.product}")
        print(f"Flight: {order.flight_start} - {order.flight_end}")
        print(f"Weeks: {', '.join(order.week_start_dates)}")
        print(f"Lines: {len(order.lines)}\n")
        
        for i, line in enumerate(order.lines, 1):
            bonus = " [BONUS]" if line.is_bonus() else ""
            lang = f" ({line.language})" if line.language else ""
            time_fmt = format_time_for_description(line.time)
            
            print(f"{i}. {line.days} {time_fmt} {line.program}{lang}{bonus}")
            print(f"   Duration: {line.duration}s | Rate: ${line.rate} | Total: {line.total_spots} spots")
            print(f"   Weekly: {line.weekly_spots}")
            
            # Show if line needs splitting
            ranges = analyze_weekly_distribution(line.weekly_spots, order.week_start_dates)
            if len(ranges) > 1:
                print(f"   ⚠ Will split into {len(ranges)} Etere lines due to gaps")
            
            print()
    
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
