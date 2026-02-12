"""
Daviselen Order Parser
Parses Daviselen agency insertion order PDFs (Los Angeles market)
Handles weekly spot distribution, language-based programming, and bonus lines
Format: Brand Time Schedule with weekly columns
"""

import pdfplumber
import re
from datetime import datetime, timedelta
from dataclasses import dataclass
from typing import List, Dict, Optional


@dataclass
class DaviselenLine:
    """Represents a single line item from Daviselen order."""
    line_number: str
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
class DaviselenOrder:
    """Represents a Daviselen order."""
    order_number: str
    client: str
    estimate_number: str
    description: str
    market: str
    product: str
    flight_start: str
    flight_end: str
    week_start_dates: List[str]  # Start date of each week
    lines: List[DaviselenLine]
    # CPE fields (Client/Product/Estimate short codes)
    client_code: Optional[str] = None
    product_code: Optional[str] = None
    estimate_detail: Optional[str] = None  # Full estimate detail line (e.g., "26_02_02_T_NCA_MTV_AM_M")
    
    def get_cpe(self) -> str:
        """
        Get CPE (Client/Product/Estimate) string.
        
        CPE is a broadcast industry term for the short codes that identify
        the client, product, and estimate number.
        
        Example: "DSCT/NCA/1295"
        """
        if self.client_code and self.product_code and self.estimate_number:
            return f"{self.client_code}/{self.product_code}/{self.estimate_number}"
        return f"Unknown/{self.estimate_number}"


def parse_daviselen_pdf(pdf_path: str) -> DaviselenOrder:
    """
    Parse Daviselen PDF and extract order data.
    
    Format: Page 1 has order header, Page 2+ has Brand Time Schedule
    
    Args:
        pdf_path: Path to the Daviselen PDF file
        
    Returns:
        DaviselenOrder object with all order details
    """
    with pdfplumber.open(pdf_path) as pdf:
        # Page 1: Extract order header
        page1_text = pdf.pages[0].extract_text()
        header_page1 = _extract_page1_header(page1_text)
        
        # Page 2+: Extract schedule details
        schedule_text = ""
        for page in pdf.pages[1:]:
            schedule_text += page.extract_text() + "\n"
        
        header_page2 = _extract_schedule_header(schedule_text)
        week_dates = _extract_week_dates(schedule_text)
        
        # Use positional extraction to handle zeros correctly
        lines = _extract_lines_with_positions(pdf_path, len(week_dates))
        
        # Use page 1 client if page 2 appears truncated (ends with "ASS" or other incomplete words)
        client_page2 = header_page2.get('client', '')
        client_page1 = header_page1.get('client', 'Unknown')
        
        # If page 2 client looks incomplete, use page 1
        if client_page2 and (client_page2.endswith(' ASS') or len(client_page2) < len(client_page1)):
            client_name = client_page1
        else:
            client_name = client_page2 if client_page2 else client_page1
        
        return DaviselenOrder(
            order_number=header_page1.get('order_number', 'Unknown'),
            client=client_name,
            estimate_number=header_page2.get('estimate', header_page1.get('estimate', 'Unknown')),
            description=header_page1.get('description', ''),
            market=header_page2.get('market', 'Unknown'),
            product=header_page2.get('product', header_page1.get('product', '')),
            flight_start=header_page2['flight_start'],
            flight_end=header_page2['flight_end'],
            week_start_dates=week_dates,
            lines=lines,
            client_code=header_page2.get('client_code'),
            product_code=header_page2.get('product_code'),
            estimate_detail=header_page2.get('estimate_detail')
        )


def _extract_page1_header(text: str) -> Dict[str, str]:
    """Extract header information from page 1 (order details page)."""
    header = {}
    
    # Order number: "Order# 00023475" (remove leading zeros)
    order_match = re.search(r'Order#?\s*(\d+)', text, re.IGNORECASE)
    if order_match:
        order_num = order_match.group(1).lstrip('0')  # Remove leading zeros
        header['order_number'] = order_num if order_num else '0'  # Handle case of all zeros
    
    # Client: "Client SO. CAL. TDA"
    client_match = re.search(r'Client\s+(.+?)(?:\n|Product)', text)
    if client_match:
        header['client'] = client_match.group(1).strip()
    
    # Product: "Product NEW CAR"
    product_match = re.search(r'Product\s+(.+?)(?:\n|Estimate)', text)
    if product_match:
        header['product'] = product_match.group(1).strip()
    
    # Estimate: "Estimate 1295"
    estimate_match = re.search(r'Estimate\s+(\d+)', text)
    if estimate_match:
        header['estimate'] = estimate_match.group(1)
    
    # Description from order comments (optional)
    desc_match = re.search(r'Order Comments\s*:\s*(.+?)(?=Terms & Conditions:|$)', text, re.DOTALL)
    if desc_match:
        desc_text = desc_match.group(1).strip()
        # Clean up the description (remove extra whitespace)
        desc_text = ' '.join(desc_text.split())
        header['description'] = desc_text[:200]  # Limit length
    else:
        header['description'] = ''
    
    return header


def _extract_schedule_header(text: str) -> Dict[str, str]:
    """Extract header information from schedule page (page 2+)."""
    header = {}
    
    # Period: "PERIOD FROM JAN26/26 TO FEB22/26"
    period_match = re.search(r'PERIOD FROM\s+([A-Z]+\d+/\d+)\s+TO\s+([A-Z]+\d+/\d+)', text)
    if period_match:
        start_str = period_match.group(1)  # e.g., "JAN26/26"
        end_str = period_match.group(2)    # e.g., "FEB22/26"
        
        # Parse dates
        header['flight_start'] = _parse_daviselen_date(start_str)
        header['flight_end'] = _parse_daviselen_date(end_str)
    else:
        header['flight_start'] = 'Unknown'
        header['flight_end'] = 'Unknown'
    
    # Client: "CLIENT DSCT SO. CAL. TDA" or "CLIENT DMWW WESTERN WASHINGTON OP. ASSMarket"
    # Extract both the full name and the short code (handle missing space before Market)
    # First try with proper space
    client_match = re.search(r'CLIENT\s+([A-Z]+)\s+(.+?)\s+Market', text)
    if not client_match:
        # Try without space before Market (e.g., "ASSMarket")
        client_match = re.search(r'CLIENT\s+([A-Z]+)\s+(.+?)Market', text)
    
    if client_match:
        header['client_code'] = client_match.group(1)  # Short code (e.g., "DSCT", "DMWW")
        header['client'] = client_match.group(2).strip()  # Full name
    else:
        # Final fallback to just getting full client name
        client_fallback = re.search(r'CLIENT\s+(.+?)(?:Market|$)', text)
        if client_fallback:
            header['client'] = client_fallback.group(1).strip()
    
    # Market: "Market SEA WA SEATTLE-TACOMA" - extract market code (SEA, LAX, etc.)
    market_match = re.search(r'Market\s+([A-Z]{2,4})\s+([A-Z]{2})\s+(.+?)(?:\s+RTG|$)', text)
    if market_match:
        market_code = market_match.group(1)  # SEA, LAX, NYC, etc.
        header['market'] = market_code
    else:
        # Fallback - default to Los Angeles if not found
        header['market'] = 'Los Angeles'
    
    # Product: "PRODUCT NCA NEW CAR"
    # Extract both the full name and the short code
    product_match = re.search(r'PRODUCT\s+([A-Z]+)\s+(.+?)(?:\n|ESTIMATE)', text)
    if product_match:
        header['product_code'] = product_match.group(1)  # Short code (e.g., "NCA")
        header['product'] = product_match.group(2).strip()  # Full name
    else:
        # Fallback
        product_fallback = re.search(r'PRODUCT\s+(.+?)(?:\n|ESTIMATE)', text)
        if product_fallback:
            header['product'] = product_fallback.group(1).strip()
    
    # Estimate: "ESTIMATE 1295 26_02_02_T_NCA_MTV_AM_M" or "ESTIMATE 0040 ..."
    # Extract both the number and the detail line (remove leading zeros from number)
    estimate_match = re.search(r'ESTIMATE\s+(\d+)\s+(.+?)(?:\n|REVISION)', text)
    if estimate_match:
        estimate_num = estimate_match.group(1).lstrip('0')  # Remove leading zeros
        header['estimate'] = estimate_num if estimate_num else '0'  # Handle case of all zeros
        header['estimate_detail'] = estimate_match.group(2).strip()
    else:
        # Fallback to just number
        estimate_fallback = re.search(r'ESTIMATE\s+(\d+)', text)
        if estimate_fallback:
            estimate_num = estimate_fallback.group(1).lstrip('0')  # Remove leading zeros
            header['estimate'] = estimate_num if estimate_num else '0'  # Handle case of all zeros
    
    return header


def _parse_daviselen_date(date_str: str) -> str:
    """
    Parse Daviselen date format to MM/DD/YYYY.
    Input: "JAN26/26" or "FEB22/26"
    Output: "01/26/2026" or "02/22/2026"
    """
    # Extract month name and day/year
    match = re.match(r'([A-Z]+)(\d+)/(\d+)', date_str)
    if not match:
        return 'Unknown'
    
    month_name = match.group(1)
    day = match.group(2)
    year_short = match.group(3)
    
    # Convert month name to number
    month_map = {
        'JAN': '01', 'FEB': '02', 'MAR': '03', 'APR': '04',
        'MAY': '05', 'JUN': '06', 'JUL': '07', 'AUG': '08',
        'SEP': '09', 'OCT': '10', 'NOV': '11', 'DEC': '12'
    }
    
    month_num = month_map.get(month_name, '01')
    
    # Assume 20xx for year
    year_full = f"20{year_short}"
    
    return f"{month_num}/{day}/{year_full}"


def _extract_week_dates(text: str) -> List[str]:
    """
    Extract week start dates from the header row.
    
    Example header: "AD JAN FEB FEB FEB"
                    "LINE# DAY(S) TIME PROGRAM SIZE DP 26 02 09 16 TOT COST/TAX"
    
    Returns: ['01/26/2026', '02/02/2026', '02/09/2026', '02/16/2026']
    """
    week_dates = []
    
    # Find the month header line and the day numbers line
    # Pattern: "AD JAN FEB FEB FEB" followed by line with day numbers
    month_line_match = re.search(r'AD\s+((?:[A-Z]{3}\s*)+)', text)
    day_line_match = re.search(r'LINE#\s+DAY\(S\)\s+TIME\s+PROGRAM\s+SIZE\s+DP\s+([\d\s]+)\s+TOT', text)
    
    if not month_line_match or not day_line_match:
        return week_dates
    
    # Extract months
    months_str = month_line_match.group(1).strip()
    months = months_str.split()
    
    # Extract day numbers
    days_str = day_line_match.group(1).strip()
    days = days_str.split()
    
    # Determine the year from the period line
    period_match = re.search(r'PERIOD FROM\s+[A-Z]+\d+/(\d+)', text)
    year = f"20{period_match.group(1)}" if period_match else "2026"
    
    # Combine months and days
    for month_name, day in zip(months, days):
        # Convert month name to number
        month_map = {
            'JAN': '01', 'FEB': '02', 'MAR': '03', 'APR': '04',
            'MAY': '05', 'JUN': '06', 'JUL': '07', 'AUG': '08',
            'SEP': '09', 'OCT': '10', 'NOV': '11', 'DEC': '12'
        }
        
        month_num = month_map.get(month_name, '01')
        
        # Create full date
        full_date = f"{month_num}/{day.zfill(2)}/{year}"
        
        # Validate and format
        try:
            dt = datetime.strptime(full_date, '%m/%d/%Y')
            week_dates.append(dt.strftime('%m/%d/%Y'))
        except ValueError:
            continue
    
    return week_dates


def _extract_lines_from_schedule(text: str, num_weeks: int) -> List[DaviselenLine]:
    """
    Extract line items from schedule text using layout-based parsing.
    
    Since zeros are not shown in text extraction, we need to use
    positional information to determine which weeks have spots.
    """
    # We'll need to re-open the PDF to get positional data
    # For now, use the text-based approach but be aware of limitations
    lines = []
    
    text_lines = text.split('\n')
    
    for i, line in enumerate(text_lines):
        line = line.strip()
        
        # Skip empty lines and headers
        if not line or 'LINE#' in line or 'MEDIA OUTLET' in line or 'SLS REP' in line:
            continue
        
        # Stop at totals line
        if 'TOT' in line and 'COST' not in line and (line.endswith(('0.00', '.00')) or ' 1400.00' in line):
            break
        
        # Check if line starts with line number (3 digits)
        if re.match(r'^\d{3}\s+', line):
            line_obj = _parse_schedule_line(line, num_weeks)
            if line_obj:
                lines.append(line_obj)
    
    return lines


def _extract_lines_with_positions(pdf_path: str, num_weeks: int) -> List[DaviselenLine]:
    """
    Extract lines using positional information from PDF.
    This handles cases where zeros are not shown in text.
    """
    import pdfplumber
    
    lines = []
    
    with pdfplumber.open(pdf_path) as pdf:
        # Assume schedule is on page 2 (index 1)
        if len(pdf.pages) < 2:
            return lines
        
        page = pdf.pages[1]
        words = page.extract_words()
        
        # Find column header positions (week numbers - any 2-digit numbers in header area)
        week_cols = []
        seen_x_positions = set()
        for word in words:
            # Look for 2-digit numbers in the header area
            if (word['text'].isdigit() and 
                len(word['text']) == 2 and 
                word['top'] < 200 and
                word['x0'] not in seen_x_positions):  # Avoid duplicates
                week_cols.append({
                    'text': word['text'],
                    'x0': word['x0'],
                    'x1': word['x1'],
                    'top': word['top']
                })
                seen_x_positions.add(word['x0'])
        
        # Sort by x position
        week_cols.sort(key=lambda w: w['x0'])
        
        if not week_cols:
            return lines
        
        # Determine x-ranges for each week column
        col_ranges = []
        for i, col in enumerate(week_cols):
            col_center = (col['x0'] + col['x1']) / 2
            col_ranges.append({
                'week_idx': i,
                'x_min': col_center - 10,
                'x_max': col_center + 10,
                'x_center': col_center
            })
        
        # Find line numbers (001, 002, etc.)
        line_data = {}
        for word in words:
            if re.match(r'^\d{3}$', word['text']) and word['top'] > 200:
                line_num = word['text']
                line_data[line_num] = {
                    'line_number': line_num,
                    'top': word['top'],
                    'weekly_spots': [0] * num_weeks,
                    'words': []
                }
        
        # Collect all words for each line (same vertical position)
        for word in words:
            if word['top'] > 200:  # Skip headers
                # Find which line this word belongs to
                for line_num, data in line_data.items():
                    if abs(word['top'] - data['top']) < 5:  # Same line (within 5pt)
                        data['words'].append(word)
                        break
        
        # For each line, determine weekly spots by position
        for line_num, data in line_data.items():
            # Find spots in weekly columns
            for word in data['words']:
                if word['text'].isdigit() and len(word['text']) <= 2:
                    # Check which column this belongs to
                    word_x = (word['x0'] + word['x1']) / 2
                    
                    for col in col_ranges:
                        if col['x_min'] <= word_x <= col['x_max']:
                            # This is a weekly spot value
                            data['weekly_spots'][col['week_idx']] = int(word['text'])
                            break
            
            # Now parse the full line text to get other details
            line_words = sorted(data['words'], key=lambda w: w['x0'])
            line_text = ' '.join(w['text'] for w in line_words)
            
            line_obj = _parse_schedule_line_with_spots(line_text, data['weekly_spots'])
            if line_obj:
                lines.append(line_obj)
    
    return lines


def _parse_schedule_line_with_spots(line: str, weekly_spots: List[int]) -> Optional[DaviselenLine]:
    """
    Parse a schedule line with pre-determined weekly spots (from positional parsing).
    
    Format: "001 M-F 8-9P Mandarin News :30 RO 1 1 1 3 180.00"
    """
    try:
        parts = line.split()
        
        if len(parts) < 6:
            return None
        
        # LINE NUMBER (remove leading zeros)
        line_number = parts[0].lstrip('0') or '0'
        
        # DAYS
        days = parts[1]
        
        # TIME (e.g., "8-9P")
        time = parts[2]
        
        # Find where PROGRAM ends (before :15, :30, or :60)
        program_parts = []
        idx = 3
        while idx < len(parts) and not re.match(r'^:\d+$', parts[idx]):
            program_parts.append(parts[idx])
            idx += 1
        
        program = ' '.join(program_parts)
        
        # DURATION (e.g., ":30")
        if idx < len(parts):
            duration_str = parts[idx]
            duration = int(duration_str.replace(':', ''))
            idx += 1
        else:
            return None
        
        # Find TOT and COST at the end
        # They're the last two numbers in the line
        numbers_at_end = []
        for i in range(len(parts) - 1, -1, -1):
            part = parts[i].replace(',', '')
            if part.replace('.', '').isdigit():
                numbers_at_end.insert(0, part)
                if len(numbers_at_end) == 2:
                    break
        
        if len(numbers_at_end) >= 2:
            total_spots = int(numbers_at_end[0])
            rate = float(numbers_at_end[1])  # This is the RATE per spot, not total cost
        else:
            total_spots = sum(weekly_spots)
            rate = 0.0
        
        # Calculate total cost
        total_cost = rate * total_spots
        
        # Detect language from program name
        language = _detect_language(program)
        
        # Convert time format: "8-9P" -> "8:00p-9:00p"
        time_formatted = _format_time_from_daviselen(time)
        
        # Convert days format if needed
        days_formatted = _format_days(days)
        
        # Station
        station = "CROSSINGS TV"
        
        return DaviselenLine(
            line_number=line_number,
            station=station,
            days=days_formatted,
            time=time_formatted,
            program=program,
            duration=duration,
            weekly_spots=weekly_spots,
            total_spots=total_spots,
            rate=rate,
            total_cost=total_cost,
            language=language
        )
        
    except (IndexError, ValueError) as e:
        print(f"Error parsing line: {line}")
        print(f"Error: {e}")
        return None


def _parse_schedule_line(line: str, num_weeks: int) -> Optional[DaviselenLine]:
    """
    Parse a single schedule line.
    
    Format: "001 M-F 8-9P Mandarin News :30 RO 1 1 1 3 180.00"
    Parts: LINE# DAY(S) TIME PROGRAM SIZE DP [weekly spots...] TOT COST
    
    Note: Weekly spots may have fewer values than num_weeks if some weeks are 0.
          The last two numbers are always TOT and COST.
    """
    try:
        parts = line.split()
        
        if len(parts) < 6:
            return None
        
        # LINE NUMBER (remove leading zeros)
        line_number = parts[0].lstrip('0') or '0'
        
        # DAYS
        days = parts[1]
        
        # TIME (e.g., "8-9P")
        time = parts[2]
        
        # Find where PROGRAM ends (before :15, :30, or :60)
        program_parts = []
        idx = 3
        while idx < len(parts) and not re.match(r'^:\d+$', parts[idx]):
            program_parts.append(parts[idx])
            idx += 1
        
        program = ' '.join(program_parts)
        
        # DURATION (e.g., ":30")
        if idx < len(parts):
            duration_str = parts[idx]
            duration = int(duration_str.replace(':', ''))
            idx += 1
        else:
            return None
        
        # Skip DP type (e.g., "RO")
        if idx < len(parts):
            idx += 1
        
        # Remaining numbers: [weekly spots...] TOT COST
        # The last two numbers are ALWAYS total and cost
        # Everything before that is weekly spots
        remaining_parts = parts[idx:]
        
        if len(remaining_parts) < 2:
            return None
        
        # Extract total and cost (last two values)
        cost_str = remaining_parts[-1].replace(',', '')
        rate = float(cost_str)  # This is the RATE per spot, not total cost
        
        total_spots = int(remaining_parts[-2])
        
        # Weekly spots are everything before total and cost
        weekly_spot_parts = remaining_parts[:-2]
        weekly_spots_raw = [int(x) for x in weekly_spot_parts if x.isdigit()]
        
        # Now we need to figure out which weeks these spots belong to
        # If we have fewer spots than weeks, assume leading weeks are 0
        if len(weekly_spots_raw) < num_weeks:
            # Pad with zeros at the beginning
            zeros_needed = num_weeks - len(weekly_spots_raw)
            weekly_spots = [0] * zeros_needed + weekly_spots_raw
        else:
            weekly_spots = weekly_spots_raw[:num_weeks]
        
        # Calculate total cost
        total_cost = rate * total_spots
        
        # Detect language from program name
        language = _detect_language(program)
        
        # Convert time format: "8-9P" -> "8:00p-9:00p"
        time_formatted = _format_time_from_daviselen(time)
        
        # Convert days format if needed: "SA-SU" stays as is
        days_formatted = _format_days(days)
        
        # Detect station from context (default to CROSSINGS TV for CLAN)
        station = "CROSSINGS TV"
        
        return DaviselenLine(
            line_number=line_number,
            station=station,
            days=days_formatted,
            time=time_formatted,
            program=program,
            duration=duration,
            weekly_spots=weekly_spots,
            total_spots=total_spots,
            rate=rate,
            total_cost=total_cost,
            language=language
        )
        
    except (IndexError, ValueError) as e:
        print(f"Error parsing line: {line}")
        print(f"Error: {e}")
        return None


def _detect_language(program: str) -> Optional[str]:
    """Detect language from program name."""
    program_upper = program.upper()
    
    if 'MANDARIN' in program_upper:
        return 'Mandarin'
    elif 'CANTONESE' in program_upper:
        return 'Cantonese'
    elif 'KOREAN' in program_upper:
        return 'Korean'
    elif 'VIETNAMESE' in program_upper:
        return 'Vietnamese'
    elif 'FILIPINO' in program_upper or 'TAGALOG' in program_upper:
        return 'Filipino'
    elif 'PUNJABI' in program_upper:
        return 'Punjabi'
    elif 'HINDI' in program_upper or 'SOUTH ASIAN' in program_upper:
        return 'South Asian'
    elif 'HMONG' in program_upper:
        return 'Hmong'
    elif 'JAPANESE' in program_upper:
        return 'Japanese'
    
    return None


def _format_time_from_daviselen(time: str) -> str:
    """
    Convert Daviselen time format to standard format.
    Input: "8-9P", "6-8A", "730-8P", "7-730P", "1130P-12A"
    Output: "8:00p-9:00p", "6:00a-8:00a", "7:30p-8:00p", "7:00p-7:30p", "11:30p-12:00a"
    """
    # Match pattern with optional minutes and periods
    # Handles: 8-9P, 730-8P, 7-730P, 1130P-12A, 6A-12A
    match = re.match(r'(\d+)([AP]?)-(\d+)([AP])', time, re.IGNORECASE)
    if match:
        start_time = match.group(1)
        start_period = match.group(2) or match.group(4)  # Use end period if start doesn't have one
        end_time = match.group(3)
        end_period = match.group(4)
        
        # Parse start time - handle both hour-only and hour+minutes
        if len(start_time) <= 2:
            # Just hour: "8" or "12"
            start_formatted = f"{start_time}:00"
        elif len(start_time) == 3:
            # Hour + minutes without colon: "730" = 7:30
            start_formatted = f"{start_time[0]}:{start_time[1:3]}"
        elif len(start_time) == 4:
            # Hour + minutes without colon: "1130" = 11:30
            start_formatted = f"{start_time[0:2]}:{start_time[2:4]}"
        else:
            start_formatted = f"{start_time}:00"
        
        # Parse end time - same logic
        if len(end_time) <= 2:
            # Just hour: "8" or "12"
            end_formatted = f"{end_time}:00"
        elif len(end_time) == 3:
            # Hour + minutes without colon: "730" = 7:30
            end_formatted = f"{end_time[0]}:{end_time[1:3]}"
        elif len(end_time) == 4:
            # Hour + minutes without colon: "1130" = 11:30
            end_formatted = f"{end_time[0:2]}:{end_time[2:4]}"
        else:
            end_formatted = f"{end_time}:00"
        
        return f"{start_formatted}{start_period.lower()}-{end_formatted}{end_period.lower()}"
    
    return time


def _format_days(days: str) -> str:
    """
    Ensure days format is consistent.
    Input: "M-F", "SA-SU", "M-SU"
    Output: Same but normalized
    """
    # Standardize case
    days = days.upper()
    
    # Convert common variations
    if days == "M-SU":
        return "M-Su"
    elif days == "SA-SU":
        return "Sa-Su"
    elif days == "M-SA":
        return "M-Sa"
    
    return days


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


def get_default_order_code(order: DaviselenOrder) -> str:
    """
    Generate default order code for Daviselen orders.
    
    Rules:
    - So Cal Toyota: "Daviselen Toyota <estimate>"
    - All McDonald's: "Daviselen McD <estimate>"
    
    Args:
        order: DaviselenOrder object
        
    Returns:
        Default order code string
        
    Example:
        >>> get_default_order_code(order)
        "Daviselen Toyota 1295"
    """
    estimate = order.estimate_number
    client_upper = order.client.upper()
    
    # Check for McDonald's FIRST (before Toyota, since SoCal McD contains "SO. CAL")
    if ("MCDONALD" in client_upper or 
        "MCD" in client_upper or 
        "WESTERN WASHINGTON" in client_upper or 
        "CAPITAL BUSINESS UNIT" in client_upper):
        return f"Daviselen McD {estimate}"
    # Then check for Toyota
    elif "TOYOTA" in client_upper or "SCTDA" in client_upper:
        return f"Daviselen Toyota {estimate}"
    else:
        # Generic fallback
        return f"Daviselen {estimate}"


def get_default_order_description(order: DaviselenOrder) -> str:
    """
    Generate default order description for Daviselen orders.
    
    Rules:
    - So Cal Toyota: "So Cal Toyota Est <estimate>"
    - Seattle McD: "McDonald's WA Est <estimate>"
    - SoCal McD: "McDonald's LAX Est <estimate>"
    - WDC McD: "McDonald's DC Est <estimate>"
    
    Args:
        order: DaviselenOrder object
        
    Returns:
        Default description string
        
    Example:
        >>> get_default_order_description(order)
        "So Cal Toyota Est 1295"
    """
    estimate = order.estimate_number
    client_upper = order.client.upper()
    
    # Check for McDonald's FIRST (before Toyota, since SoCal McD contains "SO. CAL")
    if ("MCDONALD" in client_upper or 
        "MCD" in client_upper or 
        "WESTERN WASHINGTON" in client_upper or 
        "CAPITAL BUSINESS UNIT" in client_upper):
        # McDonald's - use market-specific label
        market = order.market.upper()
        if market in ["SEA", "SEATTLE"]:
            market_label = "WA"
        elif market in ["LAX", "LA", "LOS ANGELES"]:
            market_label = "LAX"
        elif market in ["WDC", "WAS", "WASHINGTON"]:
            market_label = "DC"
        else:
            market_label = market  # Use as-is if unknown
        
        return f"McDonald's {market_label} Est {estimate}"
    # Then check for Toyota
    elif "TOYOTA" in client_upper or "SCTDA" in client_upper:
        return f"So Cal Toyota Est {estimate}"
    else:
        # Generic fallback
        return f"{order.client} Est {estimate}"


def get_default_customer_order_ref(order: DaviselenOrder) -> str:
    """
    Generate default Customer Order Ref for ALL Daviselen orders.
    
    Format: "Order <order_number>, Est <estimate_number>"
    
    Args:
        order: DaviselenOrder object
        
    Returns:
        Customer Order Ref string
        
    Example:
        >>> get_default_customer_order_ref(order)
        "Order 23475, Est 1295"
    """
    return f"Order {order.order_number}, Est {order.estimate_number}"


def get_default_notes(order: DaviselenOrder) -> str:
    """
    Generate default Notes for ALL Daviselen orders.
    
    Format:
    CLIENT <client_code> <client_name>
    PRODUCT <product_code> <product_name>
    ESTIMATE <estimate_number> <estimate_detail>
    
    Args:
        order: DaviselenOrder object
        
    Returns:
        Notes string with line breaks
        
    Example:
        >>> get_default_notes(order)
        "CLIENT DSCT SO. CAL. TDA
        PRODUCT NCA NEW CAR
        ESTIMATE 1295 26_02_02_T_NCA_MTV_AM_M"
    """
    # Build client line
    client_line = "CLIENT "
    if order.client_code:
        client_line += f"{order.client_code} "
    client_line += order.client
    
    # Build product line
    product_line = "PRODUCT "
    if order.product_code:
        product_line += f"{order.product_code} "
    product_line += order.product
    
    # Build estimate line
    estimate_line = f"ESTIMATE {order.estimate_number}"
    if order.estimate_detail:
        estimate_line += f" {order.estimate_detail}"
    
    return f"{client_line}\n{product_line}\n{estimate_line}"


def get_daviselen_billing_defaults() -> Dict[str, str]:
    """
    Get default billing settings for ALL Daviselen orders.
    
    Returns:
        Dictionary with billing defaults:
        - charge_to: "Agency with Credit Note"
        - invoice_header: "Customer"
    """
    return {
        'charge_to': 'Agency with Credit Note',
        'invoice_header': 'Customer'
    }


def get_default_separation_intervals(order: DaviselenOrder) -> Dict[str, int]:
    """
    Get default separation intervals for Daviselen orders.
    
    Rules:
    - So Cal Toyota: Customer=25, Event=0, Order=0
    - Seattle McD: Customer=15, Event=0, Order=0
    - WDC McD: Customer=15, Event=0, Order=0 (same as Seattle McD)
    
    Args:
        order: DaviselenOrder object
        
    Returns:
        Dictionary with separation intervals:
        - customer_interval: Minutes between same customer spots
        - event_interval: Minutes between same event spots
        - order_interval: Minutes between same order spots
        
    Example:
        >>> get_default_separation_intervals(order)
        {'customer_interval': 25, 'event_interval': 0, 'order_interval': 0}
    """
    client_upper = order.client.upper()
    
    # Detect which customer
    if "SO. CAL" in client_upper or "SCTDA" in client_upper:
        # So Cal Toyota
        return {
            'customer_interval': 25,
            'event_interval': 0,
            'order_interval': 0
        }
    elif ("WESTERN WASHINGTON" in client_upper or 
          "CAPITAL BUSINESS UNIT" in client_upper or 
          "MCDONALD" in client_upper or 
          "MCD" in client_upper):
        # Seattle McD or WDC McD (both use same intervals)
        return {
            'customer_interval': 15,
            'event_interval': 0,
            'order_interval': 0
        }
    else:
        # Generic fallback (conservative)
        return {
            'customer_interval': 15,
            'event_interval': 0,
            'order_interval': 0
        }


if __name__ == '__main__':
    # Test the parser
    import sys
    
    if len(sys.argv) > 1:
        pdf_path = sys.argv[1]
    else:
        pdf_path = '/mnt/user-data/uploads/NYSDOH-NYSOH-2824_CROSSINGS.pdf'
    
    print(f"Parsing Daviselen PDF: {pdf_path}\n")
    
    try:
        order = parse_daviselen_pdf(pdf_path)
        
        print(f"Order #: {order.order_number}")
        print(f"Client: {order.client}")
        print(f"Estimate: {order.estimate_number}")
        print(f"Description: {order.description}")
        print(f"Market: {order.market}")
        print(f"Product: {order.product}")
        print(f"CPE: {order.get_cpe()}")
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
