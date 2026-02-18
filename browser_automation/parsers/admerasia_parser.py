"""
Admerasia Order Parser
Parses Admerasia PDF insertion orders with specific business rules

BUSINESS RULES:
1. All rates are NET - must gross up by dividing by 0.85
2. Customer intervals: Always 3, 5, 0 (customer, order, event)
3. Day representation: M, T, W, R, F, S, U (single letters)
4. Program names are disregarded - only use days and times in descriptions
5. One order = one language = one market
6. Header info goes into contract notes verbatim
7. Order number goes into customer order ref
8. Estimate number format: [prefix][market code] [suffix]
   - Example: 11-MD10-2602CT → 11SE 2602 (SE = Seattle)
9. Market codes: SE=Seattle, SF=San Francisco, LA=Los Angeles, NY=NYC, 
   HO=Houston, DC=Washington DC, XX=Multimarket
10. Spot length is grouped on left (e.g., :15 applies to all lines below it)
11. Time format: "7-730p" = 7:00pm-7:30pm (suffix applies to both if only at end)
"""

import pdfplumber
import re
from dataclasses import dataclass, field
from datetime import datetime, date, timedelta
from typing import List, Dict, Optional, Tuple
from decimal import Decimal, ROUND_HALF_UP


# Market code mapping for estimate numbers
MARKET_CODES = {
    "SEATTLE": "SE",
    "SAN FRANCISCO": "SF",
    "LOS ANGELES": "LA",
    "NEW YORK": "NY",
    "HOUSTON": "HO",
    "WASHINGTON DC": "DC",
    "SACRAMENTO": "CV",  # Central Valley
    "DALLAS": "DL"
}

# DMA to Etere market code
DMA_TO_ETERE = {
    "SEATTLE": "SEA",
    "SAN FRANCISCO": "SFO",
    "LOS ANGELES": "LAX",
    "NEW YORK": "NYC",
    "HOUSTON": "HOU",
    "WASHINGTON DC": "WDC",
    "SACRAMENTO": "CVC",
    "DALLAS": "DAL"
}


@dataclass
class AdmerasiaLine:
    """Represents a single line item from an Admerasia order."""
    days: str  # e.g., "M-F", "M,W", "S-U"
    time: str  # e.g., "6a-7a", "7-730p"
    net_rate: Decimal
    weekly_spots: List[int]  # Spot counts per week (deprecated - use daily_spots)
    spot_length: int  # 15, 30, 45, or 60 seconds
    
    # Additional fields for pattern analysis
    _daily_spots: List[int] = None  # Daily spot counts from calendar grid
    _calendar_days: List[int] = None  # Calendar day numbers
    
    def get_gross_rate(self) -> Decimal:
        """Calculate gross rate from net rate (net / 0.85)."""
        gross = self.net_rate / Decimal('0.85')
        # Round to 2 decimal places
        return gross.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    
    def get_total_spots(self) -> int:
        """Calculate total spots from daily counts."""
        if self._daily_spots:
            return sum(self._daily_spots)
        return sum(self.weekly_spots)
    
    def get_description(self) -> str:
        """Generate line description - just days and time."""
        return f"{self.days} {self.time}"


@dataclass
class AdmerasiaOrder:
    """Represents a complete Admerasia order."""
    order_number: str
    order_date: date
    header_text: str  # Full header for notes field
    markets: List[str]  # List of market names from DMA field
    language: str  # "Chinese" or "Vietnamese" based on ISCI section
    lines: List[AdmerasiaLine] = field(default_factory=list)
    week_start_dates: List[date] = field(default_factory=list)
    
    def get_estimate_number(self) -> str:
        """
        Generate estimate number from order number and market.
        Format: [prefix][market code] [YYMM]
        Example: 11-MD10-2602CT with start date 2/2/2026 → 11SE 2602
        
        YYMM is derived from the first spot air date (campaign start date)
        """
        # Extract prefix (everything before first hyphen) and strip leading zeros
        prefix_match = re.match(r'^([^-]+)', self.order_number)
        prefix = prefix_match.group(1) if prefix_match else ""
        prefix = prefix.lstrip('0')  # Remove leading zeros (04 → 4)
        
        # Determine market code
        if len(self.markets) > 1:
            market_code = "XX"  # Multimarket
        elif len(self.markets) == 1:
            market_upper = self.markets[0].upper()
            market_code = MARKET_CODES.get(market_upper, "XX")
        else:
            market_code = "XX"
        
        # Get YYMM from first week start date (first spot air date)
        if self.week_start_dates:
            first_date = self.week_start_dates[0]
            yymm = f"{first_date.year % 100:02d}{first_date.month:02d}"
        else:
            # Fallback: try to extract from order number (last 4 digits)
            suffix_match = re.search(r'(\d{4})[A-Z]*$', self.order_number)
            yymm = suffix_match.group(1) if suffix_match else "0000"
        
        return f"{prefix}{market_code} {yymm}"
    
    def get_market_code(self) -> str:
        """Get Etere market code for the primary market."""
        if not self.markets:
            return "Unknown"
        
        market_upper = self.markets[0].upper()
        return DMA_TO_ETERE.get(market_upper, "Unknown")
    
    def is_multimarket(self) -> bool:
        """Check if this is a multimarket order."""
        return len(self.markets) > 1
    
    def get_flight_dates(self) -> Tuple[date, date]:
        """Get flight start and end dates from week starts."""
        if not self.week_start_dates:
            return date.today(), date.today() + timedelta(days=30)
        
        start_date = self.week_start_dates[0]
        # End date is start of last week + 6 days
        end_date = self.week_start_dates[-1] + timedelta(days=6)
        
        return start_date, end_date
    
    def get_etere_lines(self) -> List[Dict]:
        """
        Convert parsed order lines into Etere line specifications.
        
        Returns list of dicts with:
        - start_date, end_date
        - days (e.g., "M-F", "M,W")
        - time
        - rate (net)
        - total_spots
        - per_day_max
        - per_week_max
        - spot_length
        """
        campaign_start, campaign_end = self.get_flight_dates()
        
        all_etere_lines = []
        
        for line in self.lines:
            if not line._daily_spots or not line._calendar_days:
                print(f"[WARNING] Line missing daily spot data, skipping: {line.time}")
                continue
            
            etere_specs = analyze_daily_patterns_to_etere_lines(
                daily_spots=line._daily_spots,
                calendar_days=line._calendar_days,
                campaign_start=campaign_start,
                campaign_end=campaign_end,
                time_str=line.time,
                net_rate=line.net_rate,
                spot_length=line.spot_length
            )
            
            all_etere_lines.extend(etere_specs)
        
        return all_etere_lines


# ============================================================================
# HELPER FUNCTIONS FOR FUNCTIONS FILE
# ============================================================================

def analyze_weekly_distribution(lines: List[AdmerasiaLine]) -> str:
    """
    Analyze weekly distribution pattern for description.
    Not needed for Admerasia since we use simple descriptions.
    """
    return "Weekly"


def format_time_for_description(time_str: str) -> str:
    """
    Format time string for contract description.
    
    Example: "11:00a-11:30a" -> "11-1130a"
    """
    if not time_str or time_str == "TBD":
        return "TBD"
    
    # Split on hyphen
    parts = time_str.split('-')
    if len(parts) != 2:
        return time_str
    
    start, end = parts
    
    # Remove colons and simplify
    start_simple = start.replace(':', '')
    end_simple = end.replace(':', '')
    
    # Format: "11-1130a"
    return f"{start_simple}-{end_simple}"


def get_default_customer_order_ref(order: AdmerasiaOrder) -> str:
    """
    Get default customer/order reference.
    
    Returns: Just the order number (e.g., "05-MD10-2602FT")
    """
    return order.order_number


def get_default_notes(order: AdmerasiaOrder) -> str:
    """
    Get default notes field content.
    
    Includes header text from PDF (campaign info, DMA, restrictions).
    """
    return order.header_text if order.header_text else f"McDonald's Order {order.order_number}"


def get_language_block_prefix(language: str) -> str:
    """
    Get language block prefix for Admerasia orders.
    
    Args:
        language: "Chinese" or "Vietnamese"
    
    Returns:
        Block prefix: "C" for Chinese, "V" for Vietnamese
    """
    if language == "Vietnamese":
        return "V"
    else:  # Chinese (default)
        return "C"


def get_default_order_code(order: AdmerasiaOrder) -> str:
    """
    Get default contract code.
    
    Format: "Admerasia McD [Estimate Number]"
    Example: "Admerasia McD 11SE 2602"
    """
    return f"Admerasia McD {order.get_estimate_number()}"


def get_default_order_description(order: AdmerasiaOrder) -> str:
    """
    Get default contract description.
    
    Format: "McDonald's Est [Order Prefix] [Market Code] [YYMM]"
    Example: "McDonald's Est 11 SEA 2602"
    """
    estimate_num = order.get_estimate_number()  # e.g., "11SE 2602"
    order_prefix = order.order_number.split('-')[0].lstrip('0')  # e.g., "11" (strip leading zeros)
    market_code = order.get_market_code()  # e.g., "SEA"
    yymm = estimate_num.split()[1]  # e.g., "2602"
    
    return f"McDonald's Est {order_prefix} {market_code} {yymm}"


# ============================================================================
# MAIN PARSING FUNCTION
# ============================================================================

def parse_admerasia_pdf(pdf_path: str, time_overrides: dict = None) -> AdmerasiaOrder:
    """
    Parse an Admerasia PDF insertion order.
    
    Args:
        pdf_path: Path to the Admerasia PDF file
        time_overrides: Optional dict of {row_idx: time_str} for garbled times.
                       If provided, skips prompting user.
        
    Returns:
        AdmerasiaOrder object containing all parsed data
        
    Raises:
        ValueError: If PDF format is invalid or cannot be parsed
    """
    with pdfplumber.open(pdf_path) as pdf:
        # Extract text from first page
        first_page = pdf.pages[0]
        text = first_page.extract_text()
        
        if not text:
            raise ValueError("PDF appears to be empty or image-based")
        
        # Parse header information (for notes field)
        header_text = _extract_header_text(text)
        
        # Detect language from ISCI section
        language = _detect_language(text)
        
        # Parse basic order info
        order_number = _extract_order_number(text)
        order_date = _extract_order_date(text)
        markets = _extract_markets(text)
        
        # Parse campaign period to get week start dates
        campaign_period = _extract_campaign_period(text)
        week_start_dates = _calculate_week_starts(campaign_period)
        
        # First pass: check for any ambiguous times that need user input
        ambiguous_times = _check_for_ambiguous_times(pdf)
        
        # Prompt user for any ambiguous times BEFORE processing
        # (unless time_overrides already provided from a previous scan)
        if time_overrides is None:
            time_overrides = {}
            
        if ambiguous_times and not time_overrides:
            print(f"\n{'='*70}")
            print("TIME CLARIFICATION NEEDED")
            print('='*70)
            print(f"\n{len(ambiguous_times)} line(s) have garbled/ambiguous times in the PDF.")
            print("Please enter the correct time for each line.\n")
            print("Format: HH:MMa-HH:MMp (example: 11:30a-12:00p)\n")
            
            for row_idx, context in ambiguous_times.items():
                print(f"Line {row_idx}:")
                print(f"  {context}")
                
                while True:
                    user_input = input(f"  Enter time (or press Enter for 11:30a-12:00p): ").strip()
                    
                    # Allow default
                    if not user_input:
                        user_input = "11:30a-12:00p"
                        print(f"  Using default: {user_input}")
                    
                    # Validate format
                    if re.match(r'\d{1,2}:\d{2}[ap]?-\d{1,2}:\d{2}[ap]', user_input, re.IGNORECASE):
                        time_overrides[row_idx] = user_input
                        break
                    else:
                        print(f"  ✗ Invalid format. Use: 11:30a-12:00p")
                
                print()  # Blank line between entries
            
            print(f"{'='*70}\n")
        
        # Parse line items from table (with time overrides)
        lines = _parse_line_items(pdf, week_start_dates, time_overrides)
        
        order = AdmerasiaOrder(
            order_number=order_number,
            order_date=order_date,
            header_text=header_text,
            markets=markets,
            language=language,
            lines=lines,
            week_start_dates=week_start_dates
        )
        
        # Store time_overrides as hidden attribute for reuse
        order._time_overrides = time_overrides
        
        return order


def _find_broadcast_table(tables: list) -> tuple:
    """
    Find the broadcast order table and its starting row.
    
    Returns: (table, start_row_offset)
        - table: The table containing "Broadcast Order"
        - start_row_offset: Row index where headers begin (where "Length", "Ad Title" appear)
    """
    for table_idx, table in enumerate(tables):
        for row_idx, row in enumerate(table):
            # Look for "Broadcast Order" indicator
            row_text = ' '.join([str(cell) for cell in row if cell])
            if "Broadcast Order" in row_text:
                # Found it! Headers are usually 2 rows after "Broadcast Order"
                # Return the table and the row offset to the header row
                return (table, row_idx + 2)
    
    # Fallback to table 1, row 0 (old behavior)
    return (tables[1] if len(tables) > 1 else tables[0], 0)


def _check_for_ambiguous_times(pdf: pdfplumber.PDF) -> Dict[int, Optional[str]]:
    """
    First pass to check for any lines with ambiguous/garbled times.
    
    Returns dict of {row_idx: context_info} for rows that need user input.
    Context info includes program snippet and rate to help user identify the line.
    """
    ambiguous = {}
    
    page = pdf.pages[0]
    tables = page.extract_tables()
    
    if not tables:
        return ambiguous
    
    broadcast_table, row_offset = _find_broadcast_table(tables)
    
    # Adjust starting row based on offset (headers are at row_offset, day numbers at row_offset+1, data starts at row_offset+2)
    start_row = row_offset + 2
    
    for row_idx in range(start_row, len(broadcast_table)):
        row = broadcast_table[row_idx]
        
        if len(row) < 5:
            continue
        
        program = row[3] if len(row) > 3 and row[3] else ""
        time_str = row[4] if len(row) > 4 and row[4] else ""
        rate_str = row[5] if len(row) > 5 and row[5] else ""
        
        if not program or not time_str:
            continue
        
        # Check if this is a valid line (has day pattern)
        if not re.search(r'\(([MTWRFSU-]+)\)', program):
            continue
        
        # Check if time is garbled
        if not re.search(r'\d+:?\d*[ap]?-\d+:?\d*[ap]', time_str):
            # Time is garbled - save context to help user
            context = f"Program: {program[:60]}..." if len(program) > 60 else program
            context += f" | Rate: {rate_str}"
            ambiguous[row_idx] = context
    
    return ambiguous


def _extract_header_text(text: str) -> str:
    """
    Extract header text for contract notes.
    Format:
    Ref: McDonald's
    Campaign: Hot Honey - 2026
    Campaign Period: 2/2/2026 - 3/1/2026
    DMA: Seattle
    No religious shows
    """
    lines = text.split('\n')
    header_lines = []
    
    for line in lines:
        line = line.strip()
        if line.startswith('Ref:'):
            header_lines.append(line)
        elif line.startswith('Campaign:') and 'Campaign Period:' not in line:
            header_lines.append(line)
        elif line.startswith('Campaign Period:'):
            header_lines.append(line)
        elif line.startswith('DMA:'):
            header_lines.append(line)
        elif 'religious' in line.lower():
            header_lines.append(line)
    
    return '\n'.join(header_lines)


def _detect_language(text: str) -> str:
    """
    Detect language from ISCI section.
    
    Looks for language indicators in the ISCI/version section:
    - "Vietnamese" → Vietnamese
    - "Mandarin" or "Cantonese" → Chinese
    - "Taglish" or "Filipino" → Filipino
    
    Returns: "Chinese", "Vietnamese", or "Filipino"
    """
    # Check for Vietnamese
    if "Vietnamese" in text:
        return "Vietnamese"
    
    # Check for Filipino (Taglish is a mix of Tagalog and English)
    if "Taglish" in text or "Filipino" in text or "Tagalog" in text:
        return "Filipino"
    
    # Check for Chinese (Mandarin or Cantonese)
    if "Mandarin" in text or "Cantonese" in text:
        return "Chinese"
    
    # Default to Chinese if unclear
    return "Chinese"


def _extract_order_number(text: str) -> str:
    """Extract order number from text."""
    match = re.search(r'Order Number:\s*(\S+)', text, re.IGNORECASE)
    if match:
        return match.group(1).strip()
    return "Unknown"


def _extract_order_date(text: str) -> date:
    """Extract order date from text."""
    match = re.search(r'Order Date:\s*(\d+/\d+/\d+)', text, re.IGNORECASE)
    if match:
        date_str = match.group(1)
        return datetime.strptime(date_str, '%m/%d/%Y').date()
    return date.today()


def _extract_markets(text: str) -> List[str]:
    """Extract market(s) from DMA field."""
    match = re.search(r'DMA:\s*(.+)', text, re.IGNORECASE)
    if match:
        dma_text = match.group(1).strip()
        # Remove any trailing content after the market name
        dma_text = re.split(r'\n|No religious', dma_text)[0].strip()
        
        # Check for multiple markets (comma or slash separated)
        if ',' in dma_text or '/' in dma_text:
            markets = re.split(r'[,/]', dma_text)
            return [m.strip() for m in markets if m.strip()]
        else:
            return [dma_text]
    
    return []


def _extract_campaign_period(text: str) -> str:
    """Extract campaign period from text."""
    match = re.search(r'Campaign Period:\s*([0-9/\-\s]+)', text, re.IGNORECASE)
    if match:
        return match.group(1).strip()
    return ""


def _calculate_week_starts(campaign_period: str) -> List[date]:
    """Calculate week start dates from campaign period."""
    # Parse "2/2/2026 - 3/1/2026"
    match = re.search(r'(\d+/\d+/\d+)\s*-\s*(\d+/\d+/\d+)', campaign_period)
    if not match:
        # Fallback: generate 5 weeks starting today
        start_date = date.today()
        return [start_date + timedelta(weeks=i) for i in range(5)]
    
    start_str = match.group(1)
    end_str = match.group(2)
    
    start_date = datetime.strptime(start_str, '%m/%d/%Y').date()
    end_date = datetime.strptime(end_str, '%m/%d/%Y').date()
    
    # Generate week starts
    week_starts = []
    current = start_date
    while current <= end_date:
        week_starts.append(current)
        current += timedelta(weeks=1)
    
    return week_starts


def _parse_line_items_from_text_fallback(pdf: pdfplumber.PDF, calendar_days: list, spot_length: int = 15) -> List[AdmerasiaLine]:
    """
    Fallback parser using raw text extraction when table extraction fails.
    
    This handles PDFs where pdfplumber's table extraction produces corrupted results.
    Parses lines like: "Frontline Sa Umaga ET 4:00-4:30p $ 55.00 1 1 1 1 1 1 6 $ 3 30.00"
    
    Args:
        pdf: The PDF object
        calendar_days: List of day numbers in the calendar
        spot_length: Spot length (15 or 30)
    
    Returns:
        List of AdmerasiaLine objects with _daily_spots attribute
    """
    lines = []
    
    page = pdf.pages[0]
    text = page.extract_text()
    text_lines = text.split('\n')
    
    print("[TEXT FALLBACK] Using text-based parsing due to table extraction issues")
    
    # Find the broadcast order section
    in_broadcast_section = False
    
    for line_idx, line in enumerate(text_lines):
        # Start parsing after "Broadcast Order"
        if "Broadcast Order" in line:
            in_broadcast_section = True
            continue
        
        if not in_broadcast_section:
            continue
        
        # Stop at footer
        if "Order Total" in line or "*If the program" in line or "Note:" in line:
            break
        
        # Look for lines with program name, time, rate, and spot counts
        # Must have a time pattern and a rate
        if not re.search(r'\d+:\d+[ap]?-\d+:\d+[ap]?', line):
            continue
        if '$ ' not in line:
            continue
        
        # Parse time (remove timezone prefix)
        time_match = re.search(r'(PST|CT|ET|CST|EST|MST|MT|PT|EDT|CDT|MDT|PDT)?\s*(\d+:\d+[ap]?-\d+:\d+[ap]?)', line)
        if not time_match:
            continue
        
        time_str = time_match.group(2)
        
        # Extract rate (first $ followed by number)
        rate_match = re.search(r'\$\s*([\d.]+)', line)
        if not rate_match:
            continue
        
        net_rate = Decimal(rate_match.group(1))
        
        # Extract program name (everything before the time, skip any ":15s ACM..." prefix)
        program_start_idx = 0
        if ":15s" in line or ":30s" in line:
            prefix_end = line.find("s ")
            if prefix_end > 0:
                program_start_idx = prefix_end + 2
        
        time_start_idx = time_match.start()
        program_name = line[program_start_idx:time_start_idx].strip()
        
        # Remove "ACM This This or This Version X" if stuck to program name
        program_name = re.sub(r'ACM\s+This\s+This\s+or\s+This\s+Version\s+[A-Z]', '', program_name).strip()
        
        if not program_name or len(program_name) < 3:
            continue
        
        # Extract daily spot counts
        # Find all single digits between rate and final cost
        after_rate = line[rate_match.end():]
        
        # Remove everything after second "$" (the cost)
        second_dollar_idx = after_rate.find('$')
        if second_dollar_idx > 0:
            relevant_part = after_rate[:second_dollar_idx].strip()
        else:
            relevant_part = after_rate.strip()
        
        # The format is: "1 1 1 1 1 1 6" or "1 1 1 1 1 1 1 1 1 1 1 1 1 1 14"
        # Daily spots are single digits, total can be 1-2 digits
        # Strategy: Find all numbers, last one (which could be 2 digits) is total
        all_numbers = re.findall(r'\b\d+\b', relevant_part)
        
        if len(all_numbers) < 2:
            print(f"[TEXT FALLBACK] Skipping line (insufficient numbers): {program_name[:30]}")
            continue
        
        # Last number is total (could be 14, 15, etc)
        expected_total = int(all_numbers[-1])
        
        # All previous numbers are daily spots (should be single digits)
        daily_spots = [int(x) for x in all_numbers[:-1]]
        
        calculated_total = sum(daily_spots)
        
        # Verify spot count
        if calculated_total != expected_total:
            print(f"[TEXT FALLBACK WARNING] {program_name[:30]}: calculated {calculated_total} != expected {expected_total}")
        
        print(f"[TEXT FALLBACK] Parsed: {program_name[:30]} | {time_str} | ${net_rate} | {len(daily_spots)} spots")
        
        # Create line
        line_obj = AdmerasiaLine(
            days="VARIES",  # Will be determined from daily_spots
            time=time_str,
            net_rate=net_rate,
            weekly_spots=[],
            spot_length=spot_length
        )
        
        # Store daily spots and calendar days
        line_obj._daily_spots = daily_spots
        line_obj._calendar_days = calendar_days.copy()
        
        lines.append(line_obj)
    
    return lines


def _parse_line_items(pdf: pdfplumber.PDF, week_start_dates: List[date], 
                     time_overrides: Dict[int, str] = None) -> List[AdmerasiaLine]:
    """
    Parse line items from PDF using table extraction for accurate calendar grid.
    
    Falls back to coordinate-based extraction if table extraction fails.
    
    Args:
        pdf: The PDF object
        week_start_dates: List of week start dates
        time_overrides: Optional dict of {row_idx: corrected_time_str}
    
    Returns:
        List of AdmerasiaLine objects
    """
    try:
        return _parse_line_items_table_based(pdf, week_start_dates, time_overrides)
    except ValueError as e:
        # If table parsing fails, try coordinate-based extraction
        if "Spot count verification failed" in str(e) or "Calendar days not consecutive" in str(e):
            print(f"\n[PARSE] Table extraction failed, trying coordinate-based extraction...")
            
            try:
                return _parse_line_items_coordinate_based(pdf, week_start_dates)
            except Exception as coord_error:
                # Coordinate-based also failed - show original error message
                print(f"\n[PARSE] Coordinate-based extraction also failed: {coord_error}")
                print(f"\n{'='*70}")
                print("ERROR: PDF CANNOT BE PARSED")
                print('='*70)
                print("\nNeither table extraction nor coordinate-based extraction worked.")
                print("This PDF has a format that cannot be automatically processed.")
                print("\nOptions:")
                print("  1. Ask the agency for a different PDF format")
                print("  2. Manually enter this order into Etere")
                print("="*70)
                
                raise ValueError(
                    "PDF cannot be parsed with any available method. Manual entry required."
                )
        else:
            raise


def _parse_line_items_coordinate_based(pdf: pdfplumber.PDF, week_start_dates: List[date]) -> List[AdmerasiaLine]:
    """
    Parse line items using coordinate-based extraction when table extraction fails.
    
    This method:
    1. Finds day numbers in header row by coordinates
    2. Finds data rows by text content (program names, times, rates)
    3. Maps "1" positions to calendar days by X coordinate matching
    
    Args:
        pdf: The PDF object
        week_start_dates: List of week start dates (for context)
    
    Returns:
        List of AdmerasiaLine objects with accurate day-by-day data
    """
    print("[COORDINATE] Using coordinate-based extraction")
    
    page = pdf.pages[0]
    words = page.extract_words()
    text = page.extract_text()
    
    # Step 1: Find calendar day numbers in header row
    target_days = list(range(17, 29)) + list(range(1, 9))
    day_positions = []
    
    for word in words:
        if word['text'].isdigit() and int(word['text']) in target_days:
            day_positions.append({
                'day': int(word['text']),
                'x': (word['x0'] + word['x1']) / 2,
                'y': word['top']
            })
    
    # Group by Y to find header row (should have most days)
    from collections import defaultdict
    by_y = defaultdict(list)
    for d in day_positions:
        y_rounded = round(d['y'])
        by_y[y_rounded].append(d)
    
    # Header row is the one with most days
    header_y, header_days = max(by_y.items(), key=lambda x: len(x[1]))
    header_days.sort(key=lambda d: d['x'])
    calendar_days = [d['day'] for d in header_days]
    
    print(f"[COORDINATE] Found {len(calendar_days)} calendar days: {calendar_days}")
    
    # Step 2: Parse data rows from text
    text_lines = text.split('\n')
    lines = []
    spot_length = 15  # Default
    
    for line_text in text_lines:
        # Look for lines with time and rate
        if not re.search(r'\d+:\d+[ap]?-\d+:\d+[ap]?', line_text):
            continue
        if '$ ' not in line_text:
            continue
        
        # Extract spot length
        if ":15s" in line_text or ":30s" in line_text:
            match = re.search(r':(\d+)s', line_text)
            if match:
                spot_length = int(match.group(1))
        
        # Extract time
        time_match = re.search(r'(PST|CT|ET|CST|EST|MST|MT|PT|EDT|CDT|MDT|PDT)?\s*(\d+:\d+[ap]?-\d+:\d+[ap]?)', line_text)
        if not time_match:
            continue
        time_str = time_match.group(2)
        
        # Extract rate (may have spaces like "$ 5 9.50")
        # The rate ends when we hit the spot counts (isolated "1" with spaces)
        # Pattern: Find $ followed by anything until we hit " 1 " or end of price pattern
        rate_match = re.search(r'\$\s*([\d\s.]+?)(?=\s+1\s|\s+$)', line_text)
        if not rate_match:
            # Fallback: simpler pattern for rates without spaces
            rate_match = re.search(r'\$\s*([\d.]+)', line_text)
        
        if not rate_match:
            continue
        
        # Remove spaces from the rate string  
        rate_str = rate_match.group(1).replace(' ', '').strip()
        try:
            net_rate = Decimal(rate_str)
        except:
            print(f"[COORDINATE WARNING] Could not parse rate from: '{rate_match.group(1)}'")
            continue
        
        # Extract program name (for logging/debugging only - not used in Etere)
        program_start = 0
        if ":15s" in line_text or ":30s" in line_text:
            prefix_end = line_text.find("s ")
            if prefix_end > 0:
                program_start = prefix_end + 2
        
        program_name = line_text[program_start:time_match.start()].strip()
        program_name = re.sub(r'ACM\s+This\s+This\s+or\s+This\s+Version\s+[A-Z]', '', program_name).strip()
        
        # Program name validation (optional - just for logging)
        if not program_name or len(program_name) < 3:
            program_name = f"Line at {time_str}"  # Fallback name for logging
        
        # Step 3: Find this row's Y position by searching for the exact time string
        # This is reliable since each time should be unique per order
        row_y = None
        
        for word in words:
            # Must match the full time string exactly
            if word['text'] == time_str:
                row_y = word['top']
                break
        
        if not row_y:
            print(f"[COORDINATE WARNING] Could not find Y position for time: {time_str}")
            continue
        
        # Step 4: Find all "1" characters at this Y position
        ones_x = []
        for word in words:
            if word['text'] == '1' and abs(word['top'] - row_y) < 3:
                ones_x.append((word['x0'] + word['x1']) / 2)
        
        # Step 5: Map each "1" to closest calendar day
        daily_spots = [0] * len(calendar_days)
        for x in ones_x:
            # Find closest header day
            closest_idx = min(range(len(header_days)), key=lambda i: abs(header_days[i]['x'] - x))
            if abs(header_days[closest_idx]['x'] - x) < 15:  # Within 15 pixels
                daily_spots[closest_idx] = 1
        
        total_spots = sum(daily_spots)
        print(f"[COORDINATE] {time_str} @ ${net_rate} | {total_spots} spots on {sum(1 for s in daily_spots if s)} days")
        
        # Create line
        line = AdmerasiaLine(
            days="VARIES",
            time=time_str,
            net_rate=net_rate,
            weekly_spots=[],
            spot_length=spot_length
        )
        line._daily_spots = daily_spots
        line._calendar_days = calendar_days.copy()
        
        lines.append(line)
    
    print(f"[COORDINATE] Successfully extracted {len(lines)} lines")
    return lines


def _parse_line_items_table_based(pdf: pdfplumber.PDF, week_start_dates: List[date], 
                     time_overrides: Dict[int, str] = None) -> List[AdmerasiaLine]:
    """
    Parse line items from PDF using table extraction for accurate calendar grid.
    
    CRITICAL: This parser extracts EXACT daily spot counts from the calendar grid
    and verifies totals against the PDF's "Total Spots" column to ensure accuracy.
    
    The broadcast order is in a table with:
    - Row 3: Headers (Length, Ad Title, Program Name, Day Part, Unit Cost, then M T W R F S U repeating)
    - Row 4: Day numbers (2, 3, 4, 5... 28, 1)
    - Row 5+: Data rows with spot counts in calendar columns
    - Last columns: Total Spots and Total Cost
    """
    lines = []
    
    # Initialize time_overrides if not provided
    if time_overrides is None:
        time_overrides = {}
    
    # Extract tables from PDF
    page = pdf.pages[0]
    tables = page.extract_tables()
    
    if not tables:
        raise ValueError("Could not find any tables in PDF")
    
    # Find the broadcast order table and its row offset
    broadcast_table, row_offset = _find_broadcast_table(tables)
    
    if len(broadcast_table) < row_offset + 3:
        raise ValueError("Broadcast order table has insufficient rows")
    
    # DETECT COLUMN LAYOUT FIRST
    # Vietnamese orders: [empty, empty, Program, DayPart, Rate, calendar...]
    # Chinese orders: [Length, AdTitle, empty, Program, DayPart, Rate, calendar...]
    # Check first data row to determine layout
    # Note: Column 0 might be None even for Chinese format, so check column 1 for Length value
    first_data_row = broadcast_table[row_offset + 2]
    col_offset = 0
    
    # Check if column 1 has a spot length (like ":15s" or ":30s") - indicates Chinese format
    if first_data_row[1] and re.search(r':\d+s?', str(first_data_row[1])):
        # Chinese format - has Length column
        col_offset = 0
        print("[PARSE] Detected Chinese format (with Length/AdTitle columns)")
    else:
        # Vietnamese format - no Length/AdTitle columns
        col_offset = -2
        print("[PARSE] Detected Vietnamese format (no Length/AdTitle columns)")
    
    # Adjust column indices based on format
    # Vietnamese: Program=2, Time=3, Rate=4, Calendar=5
    # Chinese: Program=3, Time=4, Rate=5, Calendar=6
    program_col = 2 if col_offset == -2 else 3
    time_col = 3 if col_offset == -2 else 4
    rate_col = 4 if col_offset == -2 else 5
    calendar_start_col = 5 if col_offset == -2 else 6
    
    # Get calendar day numbers from day numbers row (row_offset + 1)
    day_numbers_row = broadcast_table[row_offset + 1]
    calendar_days = []
    
    for i in range(calendar_start_col, len(day_numbers_row)):
        if day_numbers_row[i] and day_numbers_row[i].strip():
            try:
                day_num = int(day_numbers_row[i].strip())
                if 1 <= day_num <= 31:
                    calendar_days.append(day_num)
            except ValueError:
                break  # Stop when we hit non-numeric columns (Total Spots, etc.)
    
    print(f"[PARSE] Found {len(calendar_days)} calendar days: {calendar_days}")
    
    # Validate calendar days are consecutive
    if calendar_days and len(calendar_days) > 1:
        for i in range(len(calendar_days) - 1):
            if calendar_days[i + 1] != calendar_days[i] + 1:
                # Check for month rollover (e.g., 28 → 1)
                if not (calendar_days[i] >= 28 and calendar_days[i + 1] == 1):
                    raise ValueError(
                        f"Calendar days not consecutive: {calendar_days[i]} → {calendar_days[i + 1]}. "
                        f"This may indicate an error in the order. Full sequence: {calendar_days}"
                    )
    
    # Get spot length from first data row
    spot_length = 15  # Default
    first_data_row = broadcast_table[row_offset + 2]
    if first_data_row[0] and str(first_data_row[0]) != 'None':
        match = re.search(r':(\d+)', str(first_data_row[0]))
        if match:
            spot_length = int(match.group(1))
    
    print(f"[PARSE] Spot length: :{spot_length}s")
    
    # Parse data rows (starting from first data row)
    for row_idx in range(row_offset + 2, len(broadcast_table)):
        row = broadcast_table[row_idx]
        
        # Stop at footer/totals row
        if len(row) < 6:
            continue
        
        program = row[program_col] if len(row) > program_col and row[program_col] else ""
        time_str = row[time_col] if len(row) > time_col and row[time_col] else ""
        rate_str = row[rate_col] if len(row) > rate_col and row[rate_col] else ""
        
        # Skip if no program or time
        if not program or not time_str:
            continue
        
        # Stop if we hit totals/footer rows
        if 'Grand Total' in str(program) or '*' in str(program):
            break
        
        # Extract day pattern from program name (for reference only)
        # Filipino orders may not have day patterns - we'll determine days from calendar grid
        day_matches = re.findall(r'\(([MTWRFSU-]+)\)', program)
        if day_matches:
            days_reference = day_matches[0]
        else:
            # No day pattern in program name - we'll derive it from the calendar grid
            days_reference = "VARIES"  # Placeholder
        
        # Clean up time string - remove time zone prefixes
        # First check if user provided an override for this row
        if time_overrides and row_idx in time_overrides:
            time_str = time_overrides[row_idx]
            print(f"[INFO] Using user-provided time for row {row_idx}: {time_str}")
        elif time_str and not re.search(r'\d+:?\d*[ap]?-\d+:?\d*[ap]', time_str):
            # Time column is garbled and no override - skip this line
            print(f"[ERROR] Row {row_idx} has garbled time but no user override provided")
            continue
        else:
            # Strip common time zone abbreviations (PST, CT, ET, CST, EST, MST, MT, PT)
            time_str = re.sub(r'^(PST|CT|ET|CST|EST|MST|MT|PT|EST|EDT|CDT|MDT|PDT)\s*', '', time_str)
            time_str = time_str.replace('  ', ' ').strip()
        
        # Parse rate (handle space-separated format like "$ 2 9.75")
        if not rate_str or '$' not in rate_str:
            continue
        
        rate_parts = rate_str.replace('$', '').strip().split()
        rate_str_clean = ''.join(rate_parts)
        
        try:
            net_rate = Decimal(rate_str_clean)
        except:
            print(f"[WARNING] Could not parse rate: {rate_str}")
            continue
        
        # Extract daily spot counts from calendar columns (starting at column 6)
        daily_spots = []
        for col_idx in range(calendar_start_col, calendar_start_col + len(calendar_days)):
            if col_idx < len(row):
                cell_value = row[col_idx]
                if cell_value and cell_value.strip() and cell_value.strip().isdigit():
                    try:
                        count = int(cell_value.strip())
                        daily_spots.append(count)
                    except ValueError:
                        daily_spots.append(0)
                else:
                    daily_spots.append(0)
            else:
                daily_spots.append(0)
        
        # CRITICAL VERIFICATION: Extract expected total from "Total Spots" column
        # This is typically in the column after the calendar days
        expected_total = None
        total_col_idx = calendar_start_col + len(calendar_days)
        
        if total_col_idx < len(row):
            total_cell = row[total_col_idx]
            if total_cell and total_cell.strip():
                try:
                    expected_total = int(total_cell.strip())
                except:
                    # Try next column
                    if total_col_idx + 1 < len(row):
                        total_cell = row[total_col_idx + 1]
                        if total_cell and total_cell.strip():
                            try:
                                expected_total = int(total_cell.strip())
                            except:
                                pass
        
        # Calculate actual total from daily spots
        calculated_total = sum(daily_spots)
        
        # VERIFICATION: Compare calculated vs expected
        if expected_total is not None:
            if calculated_total != expected_total:
                print(f"\n{'='*70}")
                print(f"[ERROR] SPOT COUNT MISMATCH - Row {row_idx}")
                print(f"{'='*70}")
                print(f"Program: {program}")
                print(f"Time: {time_str}")
                print(f"Day pattern reference: ({days_reference})")
                print(f"Daily spots read: {daily_spots}")
                print(f"Calculated total: {calculated_total}")
                print(f"Expected total: {expected_total}")
                print(f"Difference: {calculated_total - expected_total}")
                print(f"{'='*70}")
                raise ValueError(
                    f"Spot count verification failed for line '{program[:50]}'. "
                    f"Calculated {calculated_total} spots but PDF shows {expected_total}. "
                    f"The calendar grid may have been read incorrectly."
                )
            else:
                print(f"[VERIFY] ✓ Row {row_idx}: {calculated_total} spots (matches PDF)")
        else:
            print(f"[WARNING] Could not find expected total for row {row_idx}, calculated: {calculated_total}")
        
        # Store the parsed line with daily spot counts
        # We'll analyze patterns and create proper Etere lines later
        lines.append(AdmerasiaLine(
            days=days_reference,  # Reference only - actual days determined from grid
            time=time_str,
            net_rate=net_rate,
            weekly_spots=[],  # Will be replaced with daily_spots
            spot_length=spot_length
        ))
        
        # Store daily spots as a special attribute for pattern analysis
        lines[-1]._daily_spots = daily_spots
        lines[-1]._calendar_days = calendar_days.copy()
    
    return lines



def _normalize_time_to_colon_format(time_str: str) -> str:
    """
    Normalize time to colon format for Etere.
    
    Examples:
        "7-730p" → "7:00p-7:30p"
        "7:00-7:30p" → "7:00p-7:30p"  (has colon but shared am/pm)
        "11:30-12:00p" → "11:30a-12:00p"  (noon-crossing: start must be AM)
        "11-100p" → "11:00a-1:00p"  (noon-crossing: start must be AM)
        "1030p-12a" → "10:30p-12:00a"
        "6a-7a" → "6:00a-7:00a"  
        "7:00p-7:30p" → "7:00p-7:30p" (already normalized)
    
    NOON-CROSSING RULE: When a shared period is 'p' but start_hour > end_hour
    (e.g., 11 > 1 in "11:30-1:00p"), the start time must be AM because going
    from 11pm to 1pm would be backward. This handles the common case where
    Admerasia PDFs write "11:30-12:00p" meaning 11:30am-12:00pm.
    """
    import re
    
    # Pattern 1: Already fully normalized (both times have am/pm)? Return as-is
    if re.match(r'^\d+:\d+[ap]-\d+:\d+[ap]$', time_str):
        return time_str
    
    # Pattern 2: Has colons but shared am/pm at end: "7:00-7:30p"
    match = re.match(r'^(\d+):(\d+)-(\d+):(\d+)([ap])$', time_str)
    if match:
        start_hour = match.group(1)
        start_min = match.group(2)
        end_hour = match.group(3)
        end_min = match.group(4)
        period = match.group(5)
        
        # Detect noon-crossing: "11:30-12:00p" means 11:30a-12:00p
        # Rule: if shared period is 'p', start is AM when:
        #   - end is 12 and start is not 12 (e.g., 11:30-12:00p)
        #   - start > end and start is not 12 (e.g., 11:30-1:00p)
        # But NOT when start is 12 (e.g., 12:00-1:00p stays PM)
        start_period = period
        start_h = int(start_hour)
        end_h = int(end_hour)
        if period == 'p' and start_h != 12:
            if end_h == 12 or start_h > end_h:
                start_period = 'a'
        
        return f"{start_hour}:{start_min}{start_period}-{end_hour}:{end_min}{period}"
    
    # Pattern 3: "7-730p" or "6-7a" (shared am/pm at end, no colons)
    match = re.match(r'^(\d+)-(\d{3,4})([ap])$', time_str)
    if match:
        start_hour = match.group(1)
        end_time_digits = match.group(2)
        period = match.group(3)
        
        # Split end time: if 3 digits like "730", it's "7:30"
        # if 4 digits like "1030", it's "10:30"
        if len(end_time_digits) == 3:
            end_hour = end_time_digits[0]
            end_min = end_time_digits[1:3]
        elif len(end_time_digits) == 4:
            end_hour = end_time_digits[0:2]
            end_min = end_time_digits[2:4]
        else:
            return time_str  # Can't parse
        
        # Detect noon-crossing: "11-1200p" means 11:00a-12:00p
        # Same rule as Pattern 2
        start_period = period
        start_h = int(start_hour)
        end_h = int(end_hour)
        if period == 'p' and start_h != 12:
            if end_h == 12 or start_h > end_h:
                start_period = 'a'
        
        return f"{start_hour}:00{start_period}-{end_hour}:{end_min}{period}"
    
    # Pattern 4: "1030p-12a" (each has own am/pm, no colons in first time)
    match = re.match(r'^(\d{3,4})([ap])-(\d+)([ap])$', time_str)
    if match:
        start_digits = match.group(1)
        start_period = match.group(2)
        end_hour = match.group(3)
        end_period = match.group(4)
        
        if len(start_digits) == 3:
            start_hour = start_digits[0]
            start_min = start_digits[1:3]
        elif len(start_digits) == 4:
            start_hour = start_digits[0:2]
            start_min = start_digits[2:4]
        else:
            return time_str
        
        return f"{start_hour}:{start_min}{start_period}-{end_hour}:00{end_period}"
    
    # Pattern 5: "6a-7a" or "7p-8p" (simple hour-to-hour, each with am/pm)
    match = re.match(r'^(\d+)([ap])-(\d+)([ap])$', time_str)
    if match:
        start_hour = match.group(1)
        start_period = match.group(2)
        end_hour = match.group(3)
        end_period = match.group(4)
        
        return f"{start_hour}:00{start_period}-{end_hour}:00{end_period}"
    
    # Return as-is if we can't parse it
    return time_str


def analyze_daily_patterns_to_etere_lines(daily_spots: List[int], calendar_days: List[int], 
                                         campaign_start: date, campaign_end: date,
                                         time_str: str, net_rate: Decimal, 
                                         spot_length: int) -> List[Dict]:
    """
    Analyze daily spot patterns and convert to Etere line specifications.
    
    SIMPLIFIED STRATEGY: Analyze each week independently.
    - Within each week, group DOWs by per-day-max
    - Create separate Etere lines for each week's patterns
    - No attempt to merge across weeks
    
    This is simpler, more reliable, and still achieves the goal of correct spot placement.
    """
    if not daily_spots or not calendar_days:
        return []
    
    # Map calendar days to dates and day-of-week
    day_mapping = []  # [(date, day_of_week, spot_count)]
    day_names = ['M', 'T', 'W', 'R', 'F', 'S', 'U']
    
    for i, (day_num, spot_count) in enumerate(zip(calendar_days, daily_spots)):
        actual_date = campaign_start + timedelta(days=i)
        dow_idx = actual_date.weekday()  # Monday=0, Sunday=6
        dow = day_names[dow_idx]
        day_mapping.append((actual_date, dow, spot_count))
    
    # Group by week
    weeks = {}  # {week_start_date: [(date, dow, spot_count)]}
    
    for actual_date, dow, spot_count in day_mapping:
        # Find week start (Monday of the week containing this date)
        week_start = actual_date - timedelta(days=actual_date.weekday())
        
        if week_start not in weeks:
            weeks[week_start] = []
        
        weeks[week_start].append((actual_date, dow, spot_count))
    
    # Process each week independently
    etere_lines = []
    
    for week_start in sorted(weeks.keys()):
        week_data = weeks[week_start]
        
        # Group this week's data by per-day-max
        # Format: {per_day_max: [(date, dow, spot_count)]}
        week_groups = {}
        
        for actual_date, dow, spot_count in week_data:
            if spot_count == 0:
                continue  # Skip days with no spots
            
            if spot_count not in week_groups:
                week_groups[spot_count] = []
            
            week_groups[spot_count].append((actual_date, dow, spot_count))
        
        # For each per-day-max group in this week, create an Etere line
        for per_day_max, group_data in week_groups.items():
            # Extract DOWs and dates
            dows = [dow for _, dow, _ in group_data]
            dates = [actual_date for actual_date, _, _ in group_data]
            total_spots = sum(spot_count for _, _, spot_count in group_data)
            
            # Sort DOWs in standard order
            day_order = ['M', 'T', 'W', 'R', 'F', 'S', 'U']
            sorted_dows = [d for d in day_order if d in dows]
            
            if not sorted_dows:
                continue
            
            # Determine day string (M-F, M,W, etc.)
            if len(sorted_dows) > 1 and _are_consecutive_days(sorted_dows, day_order):
                days_str = f"{sorted_dows[0]}-{sorted_dows[-1]}"
            elif len(sorted_dows) > 1:
                days_str = ",".join(sorted_dows)
            else:
                days_str = sorted_dows[0]
            
            start_date = min(dates)
            end_date = min(max(dates), campaign_end)
            
            # Calculate per-week-max
            # Since we're within a single week, always 0
            per_week_max = 0
            
            # Calculate gross rate from net rate (net / 0.85)
            gross_rate = net_rate / Decimal('0.85')
            gross_rate = gross_rate.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
            
            # Normalize time format for Etere (7-730p → 7:00p-7:30p)
            normalized_time = _normalize_time_to_colon_format(time_str)
            
            etere_lines.append({
                'start_date': start_date,
                'end_date': end_date,
                'days': days_str,
                'time': normalized_time,
                'rate': gross_rate,
                'total_spots': total_spots,
                'per_day_max': per_day_max,
                'per_week_max': per_week_max,
                'spot_length': spot_length
            })
    
    # Sort by start date, then by first DOW
    def sort_key(line):
        first_dow = line['days'].split('-')[0].split(',')[0]
        dow_index = day_order.index(first_dow) if first_dow in day_order else 99
        return (line['start_date'], dow_index)
    
    etere_lines.sort(key=sort_key)
    
    # MERGE STEP: Combine consecutive weeks with identical patterns
    merged_lines = []
    i = 0
    
    while i < len(etere_lines):
        current_line = etere_lines[i].copy()
        
        # Look ahead for consecutive weeks with same pattern
        j = i + 1
        while j < len(etere_lines):
            next_line = etere_lines[j]
            
            # Check if patterns match (same days, time, rate, per_day_max)
            if (next_line['days'] == current_line['days'] and
                next_line['time'] == current_line['time'] and
                next_line['rate'] == current_line['rate'] and
                next_line['per_day_max'] == current_line['per_day_max'] and
                next_line['per_week_max'] == 0 and  # Only merge single-week lines
                current_line['per_week_max'] == 0):
                
                # Check if they're part of a recurring weekly pattern
                days_gap = (next_line['start_date'] - current_line['end_date']).days
                
                # Allow gaps for:
                # 1. Within same week or adjacent week (gap <= 5, e.g., M-F ending Fri, next M-W starting Mon)
                # 2. Weekly recurring single-day patterns (gap 6-8 days, e.g., every Saturday)
                if days_gap <= 5 or (6 <= days_gap <= 8):
                    # Merge: extend end date and add spots
                    current_line['end_date'] = next_line['end_date']
                    current_line['total_spots'] += next_line['total_spots']
                    j += 1
                else:
                    break  # Not consecutive, stop merging
            else:
                break  # Different pattern, stop merging
        
        # Add the merged (or single) line
        merged_lines.append(current_line)
        i = j if j > i + 1 else i + 1
    
    return merged_lines


def _are_consecutive_days(dows: List[str], day_order: List[str]) -> bool:
    """Check if a list of day-of-week codes are consecutive."""
    if len(dows) <= 1:
        return True
    
    indices = [day_order.index(d) for d in dows]
    indices.sort()
    
    for i in range(len(indices) - 1):
        if indices[i + 1] != indices[i] + 1:
            return False
    
    return True


# ============================================================================
# HELPER FUNCTIONS FOR AUTOMATION
# ============================================================================

# NOTE: get_default_order_code, get_default_order_description,
# get_default_customer_order_ref, and get_default_notes are defined
# earlier in this file (near line 225-280). Do NOT duplicate them here.


def get_admerasia_billing_defaults() -> Dict[str, str]:
    """Get Admerasia billing defaults."""
    return {
        'accounting_agency': '',  # Leave blank
        'accounting_advertiser': '',  # Leave blank
        'adv_disc': '0',
        'ag_comm': '0'
    }


def get_default_separation_intervals() -> Tuple[int, int, int]:
    """
    Get default separation intervals for Admerasia.
    Format: (customer, order, event) in minutes
    
    ALWAYS: 3, 5, 0
    """
    return (3, 5, 0)


def get_customer_id_from_client(header_text: str) -> Optional[int]:
    """
    Map Admerasia client to Etere customer ID.
    
    Known mappings:
    - McDonald's: Customer ID 42
    """
    if "MCDONALD" in header_text.upper():
        return 42
    
    return None  # Will trigger customer search if not McDonald's


def get_language_from_order(order: AdmerasiaOrder) -> str:
    """
    Determine language from order.
    Since Admerasia orders are single-language, we can infer from order number or ask user.
    """
    # This will need to be determined per order - may need user input
    return "Unknown"


def analyze_weekly_distribution(weekly_spots: List[int], 
                                week_start_dates: List[date],
                                contract_end_date: date) -> List[Tuple[date, date, int]]:
    """
    Analyze weekly spot distribution and group consecutive weeks with same count.
    
    Returns list of (start_date, end_date, spots_per_week) tuples.
    Each tuple represents a period where spot count is consistent.
    """
    if not weekly_spots or not week_start_dates:
        return []
    
    ranges = []
    current_start = week_start_dates[0]
    current_count = weekly_spots[0]
    current_weeks = 1
    
    for i in range(1, len(weekly_spots)):
        if weekly_spots[i] == current_count:
            # Same count - extend current range
            current_weeks += 1
        else:
            # Different count - save current range and start new one
            if current_count > 0:  # Only save ranges with spots
                # Calculate end date
                calculated_end = current_start + timedelta(days=7 * current_weeks - 1)
                # Cap at contract end date
                end_date = min(calculated_end, contract_end_date)
                ranges.append((current_start, end_date, current_count))
            
            # Start new range
            current_start = week_start_dates[i]
            current_count = weekly_spots[i]
            current_weeks = 1
    
    # Add final range
    if current_count > 0:
        calculated_end = current_start + timedelta(days=7 * current_weeks - 1)
        end_date = min(calculated_end, contract_end_date)
        ranges.append((current_start, end_date, current_count))
    
    return ranges


def prompt_for_spot_duration(order: AdmerasiaOrder) -> int:
    """
    Prompt user for spot duration if needed.
    Admerasia orders typically show spot length in the PDF (:15, :30, etc.)
    """
    if order.lines and order.lines[0].spot_length:
        # Use spot length from first line (all lines should be same length)
        return order.lines[0].spot_length
    
    # Fallback: ask user
    print("\n[SPOT DURATION] Could not determine spot length from PDF")
    while True:
        duration_input = input("Enter spot duration in seconds (15, 30, 45, or 60): ").strip()
        try:
            duration = int(duration_input)
            if duration in [15, 30, 45, 60]:
                return duration
            print("Invalid duration. Must be 15, 30, 45, or 60.")
        except ValueError:
            print("Invalid input. Please enter a number.")
