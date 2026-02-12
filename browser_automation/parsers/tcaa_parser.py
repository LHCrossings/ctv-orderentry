"""
TCAA Order Parser
Parses TCAA (The Asian Channel) annual buy PDFs for Seattle market.
Each estimate number represents a separate contract.
"""

import pdfplumber
import re
from datetime import datetime
from dataclasses import dataclass
from typing import List, Dict, Optional, Tuple


@dataclass
class TCAALine:
    """Represents a single line item from TCAA order."""
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
class TCAAEstimate:
    """Represents a single estimate (contract) from TCAA order."""
    estimate_number: str
    description: str  # e.g., "JAN26 Asian Cable"
    flight_start: str
    flight_end: str
    client: str
    buyer: str
    market: str
    lines: List[TCAALine]


def parse_tcaa_pdf(pdf_path: str) -> List[TCAAEstimate]:
    """
    Parse TCAA PDF and extract all estimates.
    
    Args:
        pdf_path: Path to the TCAA PDF file
        
    Returns:
        List of TCAAEstimate objects, one per estimate number
    """
    estimates = []
    
    with pdfplumber.open(pdf_path) as pdf:
        current_estimate = None
        
        for page_num, page in enumerate(pdf.pages):
            text = page.extract_text()
            
            # Check if this is a new estimate page (not a summary page)
            if "Estimate:" in text and "# of SPOTS PER WEEK" in text:
                # Extract estimate header information
                estimate_data = _extract_estimate_header(text)
                
                if estimate_data:
                    # If we were building an estimate, save it
                    if current_estimate:
                        estimates.append(current_estimate)
                    
                    # Start new estimate
                    current_estimate = TCAAEstimate(
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
                    current_estimate.lines.extend(lines)
        
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
    desc_match = re.search(r'Description:\s*(.+?)(?:\s+Product:|$)', text)
    if desc_match:
        header['description'] = desc_match.group(1).strip()
    
    # Extract flight dates
    flight_match = re.search(r'Flight Date:\s*(\d{1,2}/\d{1,2}/\d{4})-(\d{1,2}/\d{1,2}/\d{4})', text)
    if flight_match:
        header['flight_start'] = flight_match.group(1)
        header['flight_end'] = flight_match.group(2)
    
    # Extract client
    client_match = re.search(r'Client:\s*([^\n]+)', text)
    if client_match:
        header['client'] = client_match.group(1).strip()
    
    # Extract buyer
    buyer_match = re.search(r'Buyer:\s*([^\n]+)', text)
    if buyer_match:
        header['buyer'] = buyer_match.group(1).strip()
    
    # Extract market
    market_match = re.search(r'Market:\s*([^\n]+)', text)
    if market_match:
        header['market'] = market_match.group(1).strip()
    
    return header


def _extract_lines_from_page(text: str) -> List[TCAALine]:
    """Extract all line items from a page."""
    lines = []
    
    # Split text into lines
    text_lines = text.split('\n')
    
    # Find the start of the table and extract week column headers
    table_start = None
    week_columns = []
    
    for i, line in enumerate(text_lines):
        if 'Station Day DP Time Program' in line:
            table_start = i + 1
            # Extract week column headers from this line
            # Format: "Station Day DP Time Program RTG Dur 1/4 1/11 1/18 1/25 Spots Cost CPP"
            parts = line.split()
            # Find date patterns (M/D format)
            for part in parts:
                if '/' in part and len(part) <= 5:
                    week_columns.append(part)
            break
    
    if table_start is None:
        return lines
    
    # Parse each line entry
    i = table_start
    while i < len(text_lines):
        line = text_lines[i]
        
        # Stop at summary lines
        if 'Station Total:' in line or 'SCHEDULE TOTALS' in line:
            break
        
        # Check if this is a station line (starts with CRTV-Cable)
        if line.startswith('CRTV-Cable'):
            line_obj, next_idx = _parse_line_entry(text_lines, i, len(week_columns))
            if line_obj:
                lines.append(line_obj)
                i = next_idx
            else:
                i += 1
        else:
            i += 1
    
    return lines


def _parse_line_entry(text_lines: List[str], start_index: int, num_weeks: int) -> Tuple[Optional[TCAALine], int]:
    """
    Parse a single line entry from the text.
    Program names span multiple lines after the main data line.
    
    Args:
        text_lines: All text lines from the page
        start_index: Index of the line starting with CRTV-Cable
        num_weeks: Number of week columns in the table
        
    Returns:
        Tuple of (TCAALine object or None, next index to continue parsing)
    """
    line = text_lines[start_index]
    
    # Parse: CRTV-Cable M-Su RT 6:00a- 7:00a CRTV-TV 0.0 30 14 0 14 14 42 $25.00 $0.00
    parts = line.split()
    
    if len(parts) < 8:
        return None, start_index + 1
    
    try:
        station = parts[0]  # CRTV-Cable
        days = parts[1]  # M-Su
        daypart_code = parts[2]  # RT or VE
        
        # Extract time: 6:00a- 7:00a or 8:00a-10:00a
        time_pattern = r'(\d+:\d+[ap])-?\s*(\d+:\d+[ap])'
        time_match = re.search(time_pattern, line)
        if time_match:
            time = time_match.group(0).replace(' ', '')
        else:
            time = "Unknown"
        
        # Extract all numbers from the line, but skip the time portion first
        line_without_time = re.sub(time_pattern, 'TIME', line)
        numbers = re.findall(r'[\d,]+\.?\d*', line_without_time)
        clean_numbers = [float(n.replace(',', '')) for n in numbers]
        
        # Structure: RTG (0.0), Dur (30), [weekly spots x num_weeks], total_spots, rate, CPP
        # Example: 0.0 30 14 0 14 14 42 25.00 0.00
        
        if len(clean_numbers) < 2:
            return None, start_index + 1
        
        rating = clean_numbers[0]  # 0.0
        duration = int(clean_numbers[1])  # 30 or 60
        
        # Extract weekly spots (next num_weeks values)
        weekly_start = 2
        weekly_end = weekly_start + num_weeks
        weekly_spots = [int(n) for n in clean_numbers[weekly_start:weekly_end]]
        
        # After weekly spots: total_spots, rate, CPP
        if len(clean_numbers) >= weekly_end + 2:
            total_spots = int(clean_numbers[weekly_end])
            rate = clean_numbers[weekly_end + 1]
            # CPP is the last one, but we don't need it
        else:
            total_spots = sum(weekly_spots)
            rate = 0.0
        
        # Calculate total cost
        total_cost = rate * total_spots
        
        # Extract program name from following lines
        program_lines = []
        current_index = start_index + 1
        
        while current_index < len(text_lines):
            next_line = text_lines[current_index].strip()
            
            # Stop if we hit another station line, summary, or empty
            if (next_line.startswith('CRTV-Cable') or 
                next_line.startswith('Station Total') or
                not next_line):
                break
            
            # Check for 'AV' line (part of VE AV daypart)
            if next_line == 'AV' and daypart_code == 'VE':
                daypart_code = 'VE AV'
                current_index += 1
                break
            
            # Add to program name if it doesn't look like data
            if not any(c in next_line for c in ['$', 'Total', 'Cost', 'Spots Per Week']):
                program_lines.append(next_line)
                current_index += 1
            else:
                break
        
        program = ' '.join(program_lines).strip()
        
        line_obj = TCAALine(
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
        
        return line_obj, current_index
        
    except (IndexError, ValueError) as e:
        print(f"Error parsing line at index {start_index}: {e}")
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


def extract_language_from_program(program: str) -> str:
    """
    Extract language from program description.
    
    Args:
        program: Program text (e.g., "mandarin news", "Korean News", "South Asian")
        
    Returns:
        Language name with proper capitalization
    """
    program_lower = program.lower().strip()
    
    # Check for specific languages
    if 'mandarin' in program_lower:
        return 'Mandarin'
    elif 'korean' in program_lower:
        return 'Korean'
    elif 'japanese' in program_lower:
        return 'Japanese'
    elif 'vietnamese' in program_lower:
        return 'Vietnamese'
    elif 'south asian' in program_lower:
        return 'South Asian'
    elif 'filipino' in program_lower:
        return 'Filipino'
    elif 'cantonese' in program_lower:
        return 'Cantonese'
    elif 'chinese' in program_lower:
        return 'Chinese'
    elif 'punjabi' in program_lower:
        return 'Punjabi'
    elif 'hindi' in program_lower:
        return 'Hindi'
    elif 'hmong' in program_lower:
        return 'Hmong'
    else:
        # Return first word capitalized as fallback
        return program.split()[0].capitalize() if program else 'Unknown'


def get_language_block_prefix(language: str) -> List[str]:
    """
    Get block prefix(es) for a given language.
    
    Args:
        language: Language name (e.g., "Mandarin", "Korean", "Chinese")
        
    Returns:
        List of block prefixes to filter by
    """
    mapping = {
        'Mandarin': ['M'],
        'Korean': ['K'],
        'South Asian': ['SA'],  # Note: May need user clarification for Hindi vs Punjabi
        'Hindi': ['SA'],
        'Punjabi': ['P'],
        'Filipino': ['T'],
        'Vietnamese': ['V'],
        'Cantonese': ['C'],
        'Chinese': ['M', 'C'],  # Both Mandarin and Cantonese
        'Japanese': ['J'],
        'Hmong': ['Hm']
    }
    
    return mapping.get(language, [])


def analyze_weekly_distribution(weekly_spots: List[int], flight_start: str, flight_end: str) -> List[Dict]:
    """
    Analyze weekly distribution and determine if lines need splitting.
    
    UNIVERSAL RULES:
    - Split on gaps (weeks with 0 spots)
    - Split when spot counts change (e.g., [5,5,7] becomes two ranges: [5,5] and [7])
    - Only combine consecutive weeks with IDENTICAL spot counts
    - Always cap calculated end dates at contract/flight end date
    
    Args:
        weekly_spots: List of spots per week (e.g., [14, 0, 14, 14])
        flight_start: Flight start date (MM/DD/YYYY)
        flight_end: Flight end date (MM/DD/YYYY)
        
    Returns:
        List of dicts with {start_date, end_date, spots, spots_per_week, num_weeks}
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
        pdf_path = '/mnt/user-data/uploads/2026_Annual_CRTV-TV.pdf'
    
    print(f"Parsing TCAA PDF: {pdf_path}\n")
    
    try:
        estimates = parse_tcaa_pdf(pdf_path)
        
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
