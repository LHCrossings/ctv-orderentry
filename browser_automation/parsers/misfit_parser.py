"""
Misfit Order Parser
Parses Misfit agency insertion order PDFs

Format: Crossings TV Media Proposal with:
- Header table with agency info, markets, budget
- Separate market tables (Los Angeles, San Francisco, Central Valley)
- Each table has: Language Block, Day Part/Program, Unit Value, Weekly spots
- Summary table at bottom
"""

import pdfplumber
import re
from datetime import datetime, timedelta
from dataclasses import dataclass
from typing import List, Dict, Optional


@dataclass
class MisfitLine:
    """Represents a single line item from Misfit order."""
    language: str  # "Cantonese News", "Mandarin News", "Chinese", "Filipino", etc.
    program: str  # "M-F 7p-8p", "M-Sun 8p-9p", "ROS"
    days: str  # "M-F", "M-Sun", "Sat-Sun", "M-Su"
    time: str  # "7p-8p", "8p-9p", "ROS"
    rate: float  # Unit rate (0 for bonus)
    weekly_spots: List[int]  # Spots per week [3, 3, 3, ...]
    total_spots: int
    gross: float
    net: float
    market: str  # "LAX", "SFO", "CVC"
    is_bonus: bool  # True if rate = 0
    
    def get_duration(self) -> int:
        """Get spot duration in seconds. Default to 30s for Misfit orders."""
        return 30
    
    def get_description(self) -> str:
        """
        Get line description for Etere.
        
        Format:
        - Paid lines: "{days} {time} {language}"
          Example: "M-F 7p-8p Cantonese News"
        
        - Bonus/ROS lines: "{days} BNS {language} ROS"
          Example: "M-Sun BNS Chinese ROS"
        """
        if self.is_bonus:
            # Bonus ROS lines: "{days} BNS {language} ROS"
            return f"{self.days} BNS {self.language} ROS"
        else:
            # Paid lines: "{days} {time} {language}"
            time_fmt = format_time_for_description(self.time)
            return f"{self.days} {time_fmt} {self.language}"
    
    def get_ros_schedule(self) -> tuple[str, str]:
        """
        Get actual days and time for ROS (Run of Schedule) lines.
        
        ROS mappings:
        - Chinese ROS: M-Su 0600-2359 (6a-11:59p)
        - Filipino ROS: M-Su 1600-1900 (4p-7p)
        - Korean ROS: M-Su 0800-1000 (8a-10a)
        - Vietnamese ROS: M-Su 1100-1300 (11a-1p)
        - Hmong ROS: Sa-Su 1800-2000 (6p-8p)
        - South Asian ROS: M-Su 1300-1600 (1p-4p)
        - Japanese ROS: M-F 1000-1100 (10a-11a)
        
        Returns:
            (days, time) tuple for Etere blocks tab
        """
        if self.time != "ROS":
            # Not a ROS line - return original days and time
            return (self.days, self.time)
        
        language_upper = self.language.upper()
        
        if 'CHINESE' in language_upper or 'MANDARIN' in language_upper or 'CANTONESE' in language_upper:
            return ("M-Su", "6:00a-11:59p")
        elif 'FILIPINO' in language_upper:
            return ("M-Su", "4:00p-7:00p")
        elif 'KOREAN' in language_upper:
            return ("M-Su", "8:00a-10:00a")
        elif 'VIETNAMESE' in language_upper:
            return ("M-Su", "11:00a-1:00p")
        elif 'HMONG' in language_upper:
            return ("Sa-Su", "6:00p-8:00p")
        elif 'SOUTH ASIAN' in language_upper:
            return ("M-Su", "1:00p-4:00p")
        elif 'JAPANESE' in language_upper:
            return ("M-F", "10:00a-11:00a")
        else:
            # Fallback to original
            return (self.days, self.time)


@dataclass
class MisfitOrder:
    """Represents a Misfit order."""
    agency: str  # "Misfit"
    contact: str
    email: str
    phone: str
    markets: List[str]  # ["LA", "SF", "CVC"]
    budget_gross: float
    budget_net: float
    date: str  # "1/7/2026"
    commission: str  # "7.50%"
    week_start_dates: List[str]  # ["26-Jan", "2-Feb", ...]
    lines: List[MisfitLine]  # All lines from all markets
    
    def get_lines_by_market(self, market: str) -> List[MisfitLine]:
        """Get all lines for a specific market."""
        return [line for line in self.lines if line.market == market]
    
    def get_flight_dates(self) -> tuple[str, str]:
        """
        Get flight start and end dates from week dates.
        Returns: (start_date, end_date) in MM/DD/YYYY format
        """
        if not self.week_start_dates:
            return ("Unknown", "Unknown")
        
        # Parse first week date
        first_week = self.week_start_dates[0]
        start_date = _parse_week_date(first_week, self.date)
        
        # Parse last week date and add 6 days
        last_week = self.week_start_dates[-1]
        end_dt = _parse_week_date(last_week, self.date)
        end_date_dt = datetime.strptime(end_dt, '%m/%d/%Y') + timedelta(days=6)
        end_date = end_date_dt.strftime('%m/%d/%Y')
        
        return (start_date, end_date)


def parse_misfit_pdf(pdf_path: str) -> MisfitOrder:
    """
    Parse Misfit PDF and extract order data.
    
    Args:
        pdf_path: Path to the Misfit PDF file
        
    Returns:
        MisfitOrder object with all order details
    """
    with pdfplumber.open(pdf_path) as pdf:
        page = pdf.pages[0]
        tables = page.extract_tables()
        
        if len(tables) < 2:
            raise ValueError("PDF does not have expected table structure")
        
        # Table 1: Header info
        header_table = tables[0]
        header = _parse_header_table(header_table)
        
        # Tables 2-4: Market schedules (LA, SF, CVC)
        lines = []
        week_dates = []
        
        for table_idx in range(1, len(tables)):
            table = tables[table_idx]
            
            # Check if this is a market table (not summary)
            # Market tables have "Language Block" in first few rows
            is_market_table = False
            for row in table[:3]:
                if 'Language Block' in str(row):
                    is_market_table = True
                    break
            
            if is_market_table:
                market_name, market_lines, weeks = _parse_market_table(table)
                lines.extend(market_lines)
                
                # Get week dates from first market table
                if not week_dates:
                    week_dates = weeks
        
        # Derive markets from parsed lines — more reliable than the header field,
        # which can be None/empty in supplemental budget PDFs.
        derived_markets = list(dict.fromkeys(line.market for line in lines))
        markets = derived_markets if derived_markets else header['markets']

        return MisfitOrder(
            agency=header['agency'],
            contact=header['contact'],
            email=header['email'],
            phone=header['phone'],
            markets=markets,
            budget_gross=header['budget_gross'],
            budget_net=header['budget_net'],
            date=header['date'],
            commission=header['commission'],
            week_start_dates=week_dates,
            lines=lines
        )


def _normalize_date(date_str: str) -> str:
    """
    Normalize various date formats to MM/DD/YYYY.
    Handles: MM/DD/YYYY (passthrough), DD-Mon (e.g. '16-Mar'), M/D/YYYY, etc.
    """
    if not date_str:
        return date_str
    # Already in expected format
    if re.match(r'\d{1,2}/\d{1,2}/\d{4}', date_str):
        return date_str
    # DD-Mon or D-Mon (e.g. '16-Mar', '3-Jan')
    m = re.match(r'^(\d{1,2})-([A-Za-z]{3})$', date_str.strip())
    if m:
        day = int(m.group(1))
        month_str = m.group(2).upper()
        months = {
            'JAN': 1, 'FEB': 2, 'MAR': 3, 'APR': 4,
            'MAY': 5, 'JUN': 6, 'JUL': 7, 'AUG': 8,
            'SEP': 9, 'OCT': 10, 'NOV': 11, 'DEC': 12
        }
        month = months.get(month_str)
        if month:
            year = datetime.now().year
            return f"{month:02d}/{day:02d}/{year}"
    return date_str


def _parse_header_table(table: List[List[str]]) -> Dict:
    """Parse header table (Table 1) for order info."""
    header = {
        'agency': '',
        'contact': '',
        'email': '',
        'phone': '',
        'markets': [],
        'budget_gross': 0.0,
        'budget_net': 0.0,
        'date': '',
        'commission': '7.50%'
    }
    
    for row in table:
        if not row or len(row) < 2:
            continue
        
        label = str(row[0]).strip().lower()
        value = str(row[1]).strip() if len(row) > 1 else ''
        
        if 'agency' in label:
            header['agency'] = value
        elif 'contact' in label:
            header['contact'] = value
        elif 'email' in label:
            header['email'] = value
        elif 'phone' in label:
            header['phone'] = value
        elif 'market' in label:
            # Parse "LA, SF, CVC" into list
            markets_str = value.replace(' ', '')
            header['markets'] = markets_str.split(',')
        elif 'gross' in label:
            # Parse "$ 79,000.36"
            header['budget_gross'] = _parse_currency(value)
        elif 'net' in label:
            header['budget_net'] = _parse_currency(value)
        elif 'date' in label:
            header['date'] = _normalize_date(value)
        elif 'commission' in label:
            header['commission'] = value
    
    return header


def _parse_market_table(table: List[List[str]]) -> tuple[str, List[MisfitLine], List[str]]:
    """
    Parse a market table (Tables 2-4) for schedule lines.
    
    Returns:
        (market_name, lines, week_dates)
    """
    # Find market name and header row
    market_name = None
    header_row_idx = None
    
    # Check rows 0-2 for market name and header
    for idx in range(min(3, len(table))):
        row = table[idx]
        row_text = ' '.join([str(cell) for cell in row if cell])
        
        # Check if this row has the market name
        if not market_name and ('California-Los Angeles' in row_text or
           'California-San Francisco' in row_text or
           'California-Central Valley' in row_text):
            market_name = _extract_market_from_header(row)
            continue
        
        # Check if this row is the header (has "Language Block")
        if 'Language Block' in row_text:
            header_row_idx = idx
            break
    
    if not market_name:
        market_name = 'Unknown'
    
    # Extract week dates from header row
    week_dates = []
    if header_row_idx is not None and header_row_idx < len(table):
        week_dates = _extract_week_dates_from_header(table[header_row_idx])
    
    # Parse schedule lines (start after header row)
    lines = []
    start_row = (header_row_idx + 1) if header_row_idx is not None else 3
    
    for row_idx in range(start_row, len(table)):
        row = table[row_idx]
        
        # Skip empty rows (need at least: Language Block, Day Part, Rate, 1 week col, Total)
        if not row or len(row) < 5:
            continue
        
        # Skip rows with "Paid"/"Bonus" in any of first few columns
        first_cols = ' '.join([str(row[i]) for i in range(min(3, len(row))) if row[i]])
        if any(keyword in first_cols for keyword in ['Paid', 'Bonus', 'Language Block']):
            continue
        
        line = _parse_schedule_line(row, market_name, week_dates)
        if line:
            lines.append(line)
    
    return (market_name, lines, week_dates)


def _extract_market_from_header(header_row: List[str]) -> str:
    """
    Extract market code from market header.
    
    Examples:
    - "California-Los Angeles Spectrum 1519" → "LAX"
    - "California-San Francisco Xfinity TV 3131" → "SFO"
    - "California-Central Valley Xfinity TV 398" → "CVC"
    """
    header_text = ' '.join([str(cell) for cell in header_row if cell])
    header_upper = header_text.upper()
    
    if 'LOS ANGELES' in header_upper:
        return 'LAX'
    elif 'SAN FRANCISCO' in header_upper:
        return 'SFO'
    elif 'CENTRAL VALLEY' in header_upper:
        return 'CVC'
    else:
        return 'Unknown'


def _extract_week_dates_from_header(header_row: List[str]) -> List[str]:
    """
    Extract week start dates from header row.
    
    Example: ['26-Jan', '2-Feb', '9-Feb', ...]
    """
    week_dates = []
    
    for cell in header_row:
        if not cell:
            continue
        
        # Look for pattern like "26-Jan", "2-Feb"
        if re.match(r'\d+-[A-Za-z]{3}', str(cell)):
            week_dates.append(str(cell))
    
    return week_dates


def _parse_schedule_line(row: List[str], market: str, week_dates: List[str]) -> Optional[MisfitLine]:
    """
    Parse a single schedule line from table row.
    
    Row format can have offset - first column might be None
    [None, Language Block, Day Part/Program, Unit Value, week1, week2, ...]
    OR
    [Language Block, Day Part/Program, Unit Value, week1, week2, ...]
    """
    try:
        # Determine column offset (0 or 1 based on whether first column is None)
        offset = 1 if (not row[0] and row[1]) else 0
        
        # Column 0+offset: Language Block (e.g., "Cantonese News", "Chinese" for bonus)
        language_block = str(row[0 + offset]).strip()
        
        # Column 1+offset: Day Part/Program (e.g., "M-F 7p-8p", "ROS")
        program = str(row[1 + offset]).strip()
        
        # Column 2+offset: Unit Value/Rate (e.g., "$ 117.65", "$ -")
        rate_str = str(row[2 + offset]).strip()
        rate = _parse_currency(rate_str)
        
        # Check if bonus (rate = 0)
        is_bonus = (rate == 0.0)
        
        # Extract weekly spots (columns 3+offset to 3+offset+num_weeks)
        num_weeks = len(week_dates)
        weekly_spots = []
        for i in range(3 + offset, 3 + offset + num_weeks):
            if i < len(row):
                spot_str = str(row[i]).strip()
                try:
                    spots = int(spot_str) if spot_str and spot_str.isdigit() else 0
                    weekly_spots.append(spots)
                except ValueError:
                    weekly_spots.append(0)
        
        # Total spots column (after weekly spots)
        total_col = 3 + offset + num_weeks
        total_spots = 0
        if total_col < len(row):
            total_str = str(row[total_col]).strip()
            try:
                total_spots = int(total_str) if total_str and total_str.isdigit() else 0
            except ValueError:
                total_spots = sum(weekly_spots)
        
        # Gross column
        gross_col = total_col + 1
        gross = 0.0
        if gross_col < len(row):
            gross = _parse_currency(str(row[gross_col]))
        
        # NET column
        net_col = gross_col + 1
        net = 0.0
        if net_col < len(row):
            net = _parse_currency(str(row[net_col]))
        
        # Parse days and time from program field
        days, time = _parse_program_field(program)
        
        return MisfitLine(
            language=language_block,
            program=program,
            days=days,
            time=time,
            rate=rate,
            weekly_spots=weekly_spots,
            total_spots=total_spots,
            gross=gross,
            net=net,
            market=market,
            is_bonus=is_bonus
        )
        
    except Exception as e:
        # Skip lines that fail to parse
        return None


def _parse_program_field(program: str) -> tuple[str, str]:
    """
    Parse program field into days and time.
    
    Examples:
    - "M-F 7p-8p" → ("M-F", "7p-8p")
    - "M-Sun 8p-9p" → ("M-Sun", "8p-9p")
    - "Sat-Sun 6p-8p" → ("Sat-Sun", "6p-8p")
    - "M-F 4p-5p; 6p-7p" → ("M-F", "4p-5p; 6p-7p")  # Semicolon ranges
    - "ROS" → ("M-Su", "ROS")
    """
    program = program.strip()
    
    # Check for ROS (Run of Schedule)
    if program == "ROS":
        return ("M-Su", "ROS")
    
    # Try to split on space
    parts = program.split()
    if len(parts) >= 2:
        days = parts[0]
        # Join all remaining parts (handles "4p-5p; 6p-7p")
        time = ' '.join(parts[1:])
        return (days, time)
    
    # Default
    return ("M-Su", program)


def _parse_currency(value: str) -> float:
    """
    Parse currency string to float.
    
    Examples:
    - "$ 117.65" → 117.65
    - "$ 6 ,000.15" → 6000.15 (note the space before comma)
    - "$ -" → 0.0
    """
    if not value:
        return 0.0
    
    # Remove $, spaces, and commas
    cleaned = value.replace('$', '').replace(' ', '').replace(',', '').strip()
    
    # Handle dash as zero
    if cleaned == '-' or cleaned == '':
        return 0.0
    
    try:
        return float(cleaned)
    except ValueError:
        return 0.0


def _parse_week_date(week_str: str, order_date: str) -> str:
    """
    Parse week date string to MM/DD/YYYY format.
    
    Args:
        week_str: Week date like "26-Jan" or "2-Feb"
        order_date: Order date like "1/7/2026" to get year
        
    Returns:
        Date in MM/DD/YYYY format
    """
    try:
        # Extract year from order date (fall back to current year if format differs)
        try:
            order_dt = datetime.strptime(order_date, '%m/%d/%Y')
            year = order_dt.year
        except (ValueError, TypeError):
            year = datetime.now().year
        
        # Parse week string: "26-Jan" or "2-Feb"
        parts = week_str.split('-')
        if len(parts) == 2:
            day = int(parts[0])
            month_str = parts[1].upper()
            
            # Month mapping
            months = {
                'JAN': 1, 'FEB': 2, 'MAR': 3, 'APR': 4,
                'MAY': 5, 'JUN': 6, 'JUL': 7, 'AUG': 8,
                'SEP': 9, 'OCT': 10, 'NOV': 11, 'DEC': 12
            }
            
            month = months.get(month_str, 1)
            
            # Create date
            dt = datetime(year, month, day)
            return dt.strftime('%m/%d/%Y')
    
    except Exception:
        pass
    
    return week_str


def analyze_weekly_distribution(weekly_spots: List[int], week_dates: List[str],
                                contract_end_date: Optional[str] = None) -> List[Dict]:
    """
    Analyze weekly spot distribution and split into continuous ranges.
    
    CRITICAL RULES:
    1. Split whenever weekly spot count changes
    2. Skip weeks with zero spots (create gaps)
    3. Split when there are gaps in week dates (non-consecutive weeks)
    4. Cap last week end date at contract end date
    
    Args:
        weekly_spots: List of spot counts per week [3, 3, 0, 3, 3]
        week_dates: List of week start dates in MM/DD/YYYY format
        contract_end_date: Contract end date to cap the last week
        
    Returns:
        List of dictionaries with start_date, end_date, spots_per_week, weeks
    """
    ranges = []
    current_range = None
    
    for i, spots in enumerate(weekly_spots):
        if spots == 0:
            # Zero spots - end current range if exists
            if current_range:
                ranges.append(current_range)
                current_range = None
            continue
        
        week_start = datetime.strptime(week_dates[i], '%m/%d/%Y')
        week_end = week_start + timedelta(days=6)
        
        # Check if this is consecutive with previous week
        is_consecutive = True
        if current_range:
            prev_end = datetime.strptime(current_range['end_date'], '%m/%d/%Y')
            # Allow for weekend gap (7 days between week starts)
            days_since_prev = (week_start - prev_end).days
            if days_since_prev > 7:  # More than 1 day gap after previous week end
                is_consecutive = False
        
        if current_range is None or not is_consecutive:
            # Start new range (either first range or after gap)
            if current_range:
                ranges.append(current_range)
            
            current_range = {
                'start_date': week_dates[i],
                'end_date': week_end.strftime('%m/%d/%Y'),
                'spots_per_week': spots,
                'weeks': 1
            }
        elif current_range['spots_per_week'] == spots:
            # Same spot count and consecutive - extend range
            current_range['end_date'] = week_end.strftime('%m/%d/%Y')
            current_range['weeks'] += 1
        else:
            # Different spot count - save current and start new
            ranges.append(current_range)
            current_range = {
                'start_date': week_dates[i],
                'end_date': week_end.strftime('%m/%d/%Y'),
                'spots_per_week': spots,
                'weeks': 1
            }
    
    # Add final range
    if current_range:
        # Cap end date at contract end if provided
        if contract_end_date:
            contract_end_dt = datetime.strptime(contract_end_date, '%m/%d/%Y')
            current_end_dt = datetime.strptime(current_range['end_date'], '%m/%d/%Y')
            if current_end_dt > contract_end_dt:
                current_range['end_date'] = contract_end_date
        
        ranges.append(current_range)
    
    return ranges


def format_time_for_description(time_str: str) -> str:
    """
    Format time range for description field.
    
    Input: "7p-8p", "8p-9p", "ROS"
    Output: "7-8p", "8-9p", "ROS"
    """
    if time_str == "ROS":
        return "ROS"
    
    # Already formatted
    return time_str


def get_language_block_prefix(language: str) -> List[str]:
    """
    Get block prefixes for language-based filtering.
    
    Language abbreviations in programming blocks:
    - Chinese (Cantonese/Mandarin): C or M
    - Filipino: T
    - Korean: K
    - Vietnamese: V
    - Hmong: Hm
    - South Asian: SA or P
    - Japanese: J
    
    Args:
        language: Language from schedule (e.g., "Cantonese News", "Mandarin News")
        
    Returns:
        List of block prefixes to filter on
    """
    language_upper = language.upper()
    
    if 'CANTONESE' in language_upper:
        return ['C']
    elif 'MANDARIN' in language_upper or 'CHINESE' in language_upper:
        return ['M']
    elif 'FILIPINO' in language_upper:
        return ['T']
    elif 'KOREAN' in language_upper:
        return ['K']
    elif 'VIETNAMESE' in language_upper or 'VIET' in language_upper:
        return ['V']
    elif 'HMONG' in language_upper:
        return ['Hm']
    elif 'SOUTH ASIAN' in language_upper:
        return ['SA', 'P']
    elif 'JAPANESE' in language_upper:
        return ['J']
    else:
        return []


def get_default_order_code(order: MisfitOrder) -> str:
    """Generate default order code: \"Misfit CACC 2602\"."""
    return "Misfit CACC 2602"


def get_default_order_description(order: MisfitOrder) -> str:
    """Generate default order description: \"CA Community Colleges 2602-2606\"."""
    return "CA Community Colleges 2602-2606"


def get_default_customer_order_ref(order: MisfitOrder) -> str:
    """Generate default Customer Order Ref."""
    return f"Misfit {order.date}"


def get_default_notes(order: MisfitOrder) -> str:
    """
    Generate default Notes field.
    
    Format:
    Agency: Misfit
    Contact: {contact}
    Markets: {markets}
    """
    markets_str = ', '.join(order.markets)
    return f"Agency: {order.agency}\nContact: {order.contact}\nMarkets: {markets_str}"


def prompt_for_spot_duration() -> int:
    """
    Prompt user for spot duration.
    
    Returns:
        Duration in seconds (15, 30, 45, or 60)
    """
    print("\n[SPOT DURATION]")
    print("What is the spot length for this order?")
    print("  1. :15 (15 seconds)")
    print("  2. :30 (30 seconds)")
    print("  3. :45 (45 seconds)")
    print("  4. :60 (60 seconds)")
    
    while True:
        choice = input("Enter choice (1-4) or duration (:15, :30, :45, :60): ").strip()
        
        # Handle direct duration input
        if choice in [':15', '15', '1']:
            return 15
        elif choice in [':30', '30', '2']:
            return 30
        elif choice in [':45', '45', '3']:
            return 45
        elif choice in [':60', '60', '4']:
            return 60
        else:
            print("Invalid choice. Please enter 1-4 or :15, :30, :45, :60")


def get_misfit_billing_defaults() -> Dict[str, str]:
    """Get default billing settings for Misfit orders."""
    return {
        'charge_to': 'Agency with Credit Note',
        'invoice_header': 'Customer'
    }


def get_default_separation_intervals(order: MisfitOrder) -> Dict[str, int]:
    """Get default separation intervals for Misfit orders."""
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
        pdf_path = '/mnt/user-data/uploads/Crossings_TV_Misfit_ICanGoToCollege_2026__1_.pdf'
    
    print(f"Parsing Misfit PDF: {pdf_path}\n")
    
    try:
        order = parse_misfit_pdf(pdf_path)
        
        print(f"Agency: {order.agency}")
        print(f"Contact: {order.contact}")
        print(f"Email: {order.email}")
        print(f"Phone: {order.phone}")
        print(f"Markets: {', '.join(order.markets)}")
        print(f"Budget Gross: ${order.budget_gross:,.2f}")
        print(f"Budget Net: ${order.budget_net:,.2f}")
        print(f"Date: {order.date}")
        flight_start, flight_end = order.get_flight_dates()
        print(f"Flight: {flight_start} - {flight_end}")
        print(f"Weeks: {len(order.week_start_dates)} ({', '.join(order.week_start_dates[:5])}...)")
        print(f"Total Lines: {len(order.lines)}\n")
        
        # Group by market
        for market in order.markets:
            # Map from header codes to line codes
            market_code = {'LA': 'LAX', 'SF': 'SFO', 'CVC': 'CVC'}.get(market, market)
            market_lines = order.get_lines_by_market(market_code)
            
            print(f"\n{'='*70}")
            print(f"MARKET: {market} ({market_code}) - {len(market_lines)} lines")
            print(f"{'='*70}\n")
            
            for i, line in enumerate(market_lines, 1):
                bonus = " [BONUS]" if line.is_bonus else ""
                lang_prefix = get_language_block_prefix(line.language)
                lang_info = f" ({lang_prefix[0]})" if lang_prefix else ""
                
                # Show Etere description
                description = line.get_description()
                
                print(f"{i}. {description}{lang_info}{bonus}")
                
                # For ROS lines, show the actual schedule
                if line.time == "ROS":
                    ros_days, ros_time = line.get_ros_schedule()
                    print(f"   ROS Schedule: {ros_days} {ros_time}")
                
                print(f"   Rate: ${line.rate:,.2f} | Total: {line.total_spots} spots")
                print(f"   Weekly: {line.weekly_spots[:5]}...")
                
                # Show if line needs splitting
                flight_start, flight_end = order.get_flight_dates()
                ranges = analyze_weekly_distribution(line.weekly_spots, 
                                                    [_parse_week_date(w, order.date) for w in order.week_start_dates],
                                                    flight_end)
                if len(ranges) > 1:
                    print(f"   ⚠ Will split into {len(ranges)} Etere lines")
                
                print()
    
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
