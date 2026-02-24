"""
Impact Marketing Order Parser
Parses Impact Marketing agency insertion order PDFs

Format: Crossings TV Proposal with quarterly breakouts (Q1-Q4)
- Each page represents one quarter (one Etere contract)
- Header with client info, market, flight schedule
- Single table per quarter with columns: Programming, Gross per:30, Discounted, weekly spots
- Languages: Filipino, Hindi, Punjabi, South Asian ROS, Chinese, Korean, Hmong

CRITICAL BUSINESS RULES:
- Each page = separate quarter = separate Etere contract
- Client: Big Valley Ford (Customer ID: 252) - ONLY client for Impact Marketing
- Market: Always CVC (Central Valley), Master Market: NYC (Crossings TV)
- Use "Discounted" rate column (not "Gross per:30")
- Week dates are column headers - ignore month labels (broadcast calendar vs regular calendar)

SPECIAL LINE FORMATTING:
1. "Filipino News M-F 4p-5p,6:30p-7p and Talk 6p-6:30p"
   → Book as: M-F 4p-7p
   → Description: "M-F 4-5p, 6-7p Filipino News & Talk"
   
2. "Hindi News & VarietyM-F 1p-2p / Hindi Variety Sat -Sun 1p-4p"
   → Book as: M-Su 1p-4p
   → Description: "M-Su 1p-4p Hindi shows"
   
3. "Chinese News ( M-Sat 6a-7a,M-Sun 7p-9p)"
   → Book as: M-Su 6a-9p
   → Description: "M-Su Chinese News Programs"

DEFAULT VALUES:
- Order Code: "Impact BVFL 26Q{quarter}" (e.g., "Impact BVFL 26Q1")
- Description: "Big Valley Ford {start_month}-{end_month}" (e.g., "Big Valley Ford 2601-2603")
- Customer ID: 252 (Big Valley Ford)
- Separation: 15/0/0 (customer/event/order)
- Billing: "Customer share indicating agency %" / "Agency"
"""

import pdfplumber
import re
from datetime import datetime, timedelta
from dataclasses import dataclass
from typing import List, Dict, Optional, Tuple


@dataclass
class ImpactLine:
    """Represents a single line item from Impact order."""
    language: str  # "Filipino", "Hindi", "Punjabi", "Chinese", "Korean", "Hmong", "South Asian"
    program: str  # Original program description
    days: str  # "M-F", "M-Su", "Sa-Su"
    time: str  # "4p-7p", "1p-4p", "6a-9p", "8a-9a", "2p-4p", "6p-8p", "ROS"
    rate: float  # Discounted rate (0 for bonus/ROS)
    weekly_spots: List[int]  # Spots per week
    total_spots: int
    gross: float
    is_bonus: bool  # True if rate = 0 (ROS lines)
    quarter: str  # "Q1", "Q2", "Q3", "Q4"
    
    def get_duration(self) -> int:
        """Get spot duration in seconds. Will be prompted from user."""
        return 30  # Default, but user will be prompted
    
    def get_description(self) -> str:
        """
        Get line description for Etere based on special formatting rules.
        
        Rules:
        1. Filipino News M-F 4p-5p,6:30p-7p and Talk → "M-F 4-5p, 6-7p Filipino News & Talk"
        2. Hindi News & Variety M-F 1p-2p / Hindi Variety Sat-Sun 1p-4p → "M-Su 1p-4p Hindi shows"
        3. Chinese News ( M-Sat 6a-7a,M-Sun 7p-9p) → "M-Su Chinese News Programs"
        4. Punjabi News M-F 2p-4p → "M-F 2-4p Punjabi News"
        5. Korean News M-F 8a-9a → "M-F 8-9a Korean News"
        6. Hmong News and Entertainment Sat-Sun 6p-8p → "Sa-Su 6-8p Hmong News"
        7. ROS lines → "{days} BNS {language} ROS"
        """
        if self.is_bonus:
            # Bonus/ROS lines
            return f"{self.days} BNS {self.language} ROS"
        
        # Paid lines - apply special formatting
        if "Filipino" in self.language:
            return "M-F 4-5p, 6-7p Filipino News & Talk"
        elif "Hindi" in self.language:
            return "M-Su 1p-4p Hindi shows"
        elif "Chinese" in self.language:
            return "M-Su Chinese News Programs"
        elif "Punjabi" in self.language:
            time_fmt = format_time_for_description(self.time)
            return f"{self.days} {time_fmt} Punjabi News"
        elif "Korean" in self.language:
            time_fmt = format_time_for_description(self.time)
            return f"{self.days} {time_fmt} Korean News"
        elif "Hmong" in self.language:
            time_fmt = format_time_for_description(self.time)
            return f"{self.days} {time_fmt} Hmong News"
        else:
            # Fallback
            time_fmt = format_time_for_description(self.time)
            return f"{self.days} {time_fmt} {self.language}"
    
    def get_ros_schedule(self) -> tuple[str, str]:
        """
        Get actual days and time for ROS (Run of Schedule) lines.
        
        ROS mappings (from user memory):
        - Filipino ROS: M-Su 4p-7p
        - South Asian ROS: M-Su 1p-4p
        - Chinese ROS: M-Su 6a-11:59p
        - Korean ROS: M-Su 8a-10a
        - Hmong ROS: Sa-Su 6p-8p
        
        Returns:
            (days, time) tuple for Etere blocks tab
        """
        if self.time != "ROS":
            # Not a ROS line - return original days and time
            return (self.days, self.time)
        
        language_upper = self.language.upper()
        
        if 'FILIPINO' in language_upper:
            return ("M-Su", "4:00p-7:00p")
        elif 'SOUTH ASIAN' in language_upper or 'PUNJABI' in language_upper or 'HINDI' in language_upper:
            return ("M-Su", "1:00p-4:00p")
        elif 'CHINESE' in language_upper:
            return ("M-Su", "6:00a-11:59p")
        elif 'KOREAN' in language_upper:
            return ("M-Su", "8:00a-10:00a")
        elif 'HMONG' in language_upper:
            return ("Sa-Su", "6:00p-8:00p")
        else:
            # Fallback
            return (self.days, self.time)


@dataclass
class ImpactQuarterOrder:
    """Represents a single quarter's order from Impact Marketing."""
    agency: str  # "Impact Marketing"
    client: str  # "Big Valley Ford"
    contact: str  # "Phillip Guuthier"
    email: str  # "phillip@impactcalifornia.com"
    market: str  # "Central Valley" (will map to CVC)
    quarter: str  # "Q1-2026", "Q2-2026", etc.
    quarter_num: int  # 1, 2, 3, 4
    year: int  # 2026
    week_start_dates: List[str]  # ["12-Jan", "19-Jan", ...]
    lines: List[ImpactLine]  # All lines for this quarter
    
    def get_lines_by_type(self, is_bonus: bool) -> List[ImpactLine]:
        """Get all lines of a specific type (paid or bonus)."""
        return [line for line in self.lines if line.is_bonus == is_bonus]
    
    def get_flight_dates(self) -> tuple[str, str]:
        """
        Get flight start and end dates from week dates.
        Returns: (start_date, end_date) in MM/DD/YYYY format
        """
        if not self.week_start_dates:
            return ("Unknown", "Unknown")
        
        # Parse first week date
        first_week = self.week_start_dates[0]
        start_date = _parse_week_date(first_week, self.year)
        
        # Parse last week date and add 6 days
        last_week = self.week_start_dates[-1]
        end_dt_str = _parse_week_date(last_week, self.year)
        end_dt = datetime.strptime(end_dt_str, '%m/%d/%Y') + timedelta(days=6)
        end_date = end_dt.strftime('%m/%d/%Y')
        
        return (start_date, end_date)
    
    def get_default_order_code(self) -> str:
        """Generate default order code: 'Impact BVFL 26Q{quarter}'."""
        return f"Impact BVFL 26Q{self.quarter_num}"
    
    def get_default_description(self) -> str:
        """
        Generate default description based on quarter.
        Q1: "Big Valley Ford 2601-2603" (Jan-Mar)
        Q2: "Big Valley Ford 2604-2606" (Apr-Jun)
        Q3: "Big Valley Ford 2607-2609" (Jul-Sep)
        Q4: "Big Valley Ford 2610-2612" (Oct-Dec)
        """
        if self.quarter_num == 1:
            return "Big Valley Ford 2601-2603"
        elif self.quarter_num == 2:
            return "Big Valley Ford 2604-2606"
        elif self.quarter_num == 3:
            return "Big Valley Ford 2607-2609"
        elif self.quarter_num == 4:
            return "Big Valley Ford 2610-2612"
        else:
            return f"Big Valley Ford 26Q{self.quarter_num}"


def parse_impact_pdf(pdf_path: str) -> List[ImpactQuarterOrder]:
    """
    Parse Impact Marketing PDF and extract all quarterly orders.
    
    Each page represents one quarter = one separate Etere contract.
    
    Args:
        pdf_path: Path to the Impact Marketing PDF file
        
    Returns:
        List of ImpactQuarterOrder objects (one per quarter/page)
    """
    orders = []
    
    with pdfplumber.open(pdf_path) as pdf:
        for page_num, page in enumerate(pdf.pages, 1):
            print(f"\n[PARSE] Processing page {page_num}...")
            
            text = page.extract_text()
            tables = page.extract_tables()
            
            if not tables:
                print(f"[PARSE] No tables found on page {page_num}, skipping")
                continue
            
            # Parse header info (same across all pages)
            header = _parse_header_from_text(text)
            
            # Determine which quarter this page represents
            quarter_match = re.search(r'Q(\d)\s*-?\s*(\d{4})', text)
            if quarter_match:
                quarter_num = int(quarter_match.group(1))
                year = int(quarter_match.group(2))
                quarter = f"Q{quarter_num}-{year}"
            else:
                print(f"[PARSE] Warning: Could not determine quarter from page {page_num}")
                quarter_num = page_num
                year = datetime.now().year
                quarter = f"Q{quarter_num}-{year}"
            
            print(f"[PARSE] Quarter: {quarter}")
            
            # Find the main data table (largest table with weekly spots)
            main_table = _find_main_data_table(tables)
            
            if not main_table:
                print(f"[PARSE] Warning: Could not find main data table on page {page_num}")
                continue
            
            # Parse week dates and lines from table
            week_dates, lines = _parse_quarter_table(main_table, quarter, quarter_num, year)
            
            # Create quarter order object
            order = ImpactQuarterOrder(
                agency=header['agency'],
                client=header['client'],
                contact=header['contact'],
                email=header['email'],
                market=header['market'],
                quarter=quarter,
                quarter_num=quarter_num,
                year=year,
                week_start_dates=week_dates,
                lines=lines
            )
            
            orders.append(order)
            print(f"[PARSE] ✓ Found {len(lines)} lines for {quarter}")
    
    return orders


def _parse_header_from_text(text: str) -> Dict:
    """Parse header information from page text."""
    header = {
        'agency': 'Impact Marketing',
        'client': 'Big Valley Ford',
        'contact': '',
        'email': '',
        'market': 'Central Valley'
    }
    
    # Extract contact
    contact_match = re.search(r'Contact:\s*([^\n]+)', text)
    if contact_match:
        header['contact'] = contact_match.group(1).strip()
    
    # Extract email
    email_match = re.search(r'Email:\s*([^\s]+@[^\s]+)', text)
    if email_match:
        header['email'] = email_match.group(1).strip()
    
    # Extract market
    market_match = re.search(r'Market\s+([^\n]+)', text)
    if market_match:
        header['market'] = market_match.group(1).strip()
    
    return header


def _find_main_data_table(tables: List[List[List]]) -> Optional[List[List]]:
    """Find the main data table with weekly spot columns."""
    for table in tables:
        if not table or len(table) < 3:
            continue
        
        # Look for table with "Programming" in any of the first 2 rows
        for row_idx in range(min(2, len(table))):
            row = table[row_idx] if table[row_idx] else []
            if any('Programming' in str(cell) for cell in row if cell):
                return table
    
    return None


def _parse_quarter_table(table: List[List], quarter: str, quarter_num: int, year: int) -> Tuple[List[str], List[ImpactLine]]:
    """
    Parse the main quarterly table to extract week dates and lines.
    
    Table structure:
    Row 0: Month labels (optional) - January | February | March
    Row 1: Headers - Programming | Gross per:30 | Discounted | Week dates... | Units | Gross
    Row 2+: Data rows with program name, rates, weekly spots
    
    Returns:
        (week_dates, lines) tuple
    """
    if not table or len(table) < 2:
        return ([], [])
    
    # Find the header row (should have "Programming")
    header_row_idx = None
    for i in range(min(3, len(table))):
        row = table[i]
        if any('Programming' in str(cell) for cell in row if cell):
            header_row_idx = i
            break
    
    if header_row_idx is None:
        print("[PARSE] Warning: Could not find header row with 'Programming'")
        return ([], [])
    
    header_row = table[header_row_idx]
    data_start_row = header_row_idx + 1
    
    # Find column indices
    programming_col = None
    gross_col = None
    discounted_col = None
    week_start_col = None
    week_end_col = None
    
    for i, cell in enumerate(header_row):
        cell_str = str(cell).strip() if cell else ''
        
        if 'Programming' in cell_str:
            programming_col = i
        elif 'Gross' in cell_str and 'per' in cell_str:
            gross_col = i
        elif 'Discounted' in cell_str:
            discounted_col = i
            # Week columns start after Discounted
            week_start_col = i + 1
        elif 'Units' in cell_str and week_end_col is None:
            # Week columns end before Units
            week_end_col = i
    
    if programming_col is None or discounted_col is None:
        print("[PARSE] Warning: Could not identify required columns")
        return ([], [])
    
    if week_start_col is None or week_end_col is None:
        # Try to infer week columns
        week_start_col = discounted_col + 1
        week_end_col = len(header_row) - 2  # Before Units and Gross columns
    
    # Extract week dates from header
    week_dates = []
    for i in range(week_start_col, week_end_col):
        if i < len(header_row) and header_row[i]:
            week_date = str(header_row[i]).strip()
            if week_date and week_date not in ['', 'Units', 'Gross'] and not week_date.startswith('$'):
                week_dates.append(week_date)
    
    print(f"[PARSE] Found {len(week_dates)} week columns")
    
    # Parse data rows
    lines = []
    for row_idx in range(data_start_row, len(table)):
        row = table[row_idx]
        
        if not row or len(row) <= discounted_col:
            continue
        
        # Get program name
        program_name = str(row[programming_col]).strip() if row[programming_col] else ''
        
        # Skip empty rows or summary rows
        if not program_name or 'Total' in program_name or 'Monthly' in program_name or 'Bonus' in program_name:
            continue
        
        # Get rates - handle spaces in numbers like "$ 3 0.00"
        try:
            discounted_str = str(row[discounted_col]).replace('$', '').replace(',', '').replace(' ', '').strip() if row[discounted_col] else '0'
            discounted_rate = float(discounted_str) if discounted_str and discounted_str != '-' else 0.0
        except (ValueError, AttributeError):
            discounted_rate = 0.0
        
        # Determine if this is a bonus line (rate = 0 or "ROS" in name)
        is_bonus = (discounted_rate == 0.0) or ('ROS' in program_name.upper())
        
        # Extract weekly spots
        weekly_spots = []
        for i in range(week_start_col, week_start_col + len(week_dates)):
            if i < len(row) and row[i]:
                try:
                    spots = int(str(row[i]).strip())
                    weekly_spots.append(spots)
                except (ValueError, AttributeError):
                    weekly_spots.append(0)
            else:
                weekly_spots.append(0)
        
        # Skip if no spots
        if sum(weekly_spots) == 0:
            continue
        
        # Parse program to extract language, days, time
        language, days, time = _parse_program_name(program_name)
        
        # Create line
        line = ImpactLine(
            language=language,
            program=program_name,
            days=days,
            time=time,
            rate=discounted_rate,
            weekly_spots=weekly_spots,
            total_spots=sum(weekly_spots),
            gross=0.0,  # Not needed for automation
            is_bonus=is_bonus,
            quarter=quarter
        )
        
        lines.append(line)
        print(f"[PARSE]   - {line.get_description()} | Rate: ${discounted_rate:.2f} | Spots: {sum(weekly_spots)}")
    
    return (week_dates, lines)


def _parse_program_name(program_name: str) -> Tuple[str, str, str]:
    """
    Parse program name to extract language, days, and time.
    
    Handles special cases:
    1. "Filipino News M-F 4p-5p,6:30p-7p and Talk 6p-6:30p" → Filipino, M-F, 4p-7p
    2. "Hindi News & VarietyM-F 1p-2p / Hindi Variety Sat -Sun 1p-4p" → Hindi, M-Su, 1p-4p
    3. "Chinese News ( M-Sat 6a-7a,M-Sun 7p-9p)" → Chinese, M-Su, 6a-9p
    4. "Punjabi News M-F 2p-4p" → Punjabi, M-F, 2p-4p
    5. "Korean News M-F 8a-9a" → Korean, M-F, 8a-9a
    6. "Hmong News and Entertainment Sat-Sun 6p-8p" → Hmong, Sa-Su, 6p-8p
    7. "Filipino ROS" → Filipino, M-Su, ROS
    8. "South Asian ROS" → South Asian, M-Su, ROS
    """
    program_upper = program_name.upper()
    
    # Determine language
    if 'FILIPINO' in program_upper or 'TAGALOG' in program_upper:
        language = 'Filipino'
    elif 'HINDI' in program_upper:
        language = 'Hindi'
    elif 'PUNJABI' in program_upper:
        language = 'Punjabi'
    elif 'SOUTH ASIAN' in program_upper:
        language = 'South Asian'
    elif 'CHINESE' in program_upper:
        language = 'Chinese'
    elif 'KOREAN' in program_upper:
        language = 'Korean'
    elif 'HMONG' in program_upper:
        language = 'Hmong'
    else:
        language = 'Unknown'
    
    # Handle Hmong specifically FIRST - ALWAYS Sa-Su 6p-8p or Sa-Su ROS
    if language == 'Hmong':
        if 'ROS' in program_upper:
            return ('Hmong', 'Sa-Su', 'ROS')
        else:
            return ('Hmong', 'Sa-Su', '6p-8p')
    
    # Check for ROS (all other languages default to M-Su)
    if 'ROS' in program_upper:
        return (language, 'M-Su', 'ROS')
    
    # Special case handling based on language
    if language == 'Filipino' and ('4P-5P' in program_upper or '6:30P-7P' in program_upper or '6P-6:30P' in program_upper):
        # Filipino News M-F 4p-5p,6:30p-7p and Talk 6p-6:30p
        return ('Filipino', 'M-F', '4p-7p')
    
    if language == 'Hindi' and ('1P-2P' in program_upper or 'SAT' in program_upper or 'SUN' in program_upper):
        # Hindi News & Variety M-F 1p-2p / Hindi Variety Sat-Sun 1p-4p
        return ('Hindi', 'M-Su', '1p-4p')
    
    if language == 'Chinese' and ('6A-7A' in program_upper or '7P-9P' in program_upper or '7P9P' in program_upper):
        # Chinese News ( M-Sat 6a-7a,M-Sun 7p-9p)
        return ('Chinese', 'M-Su', '6a-9p')
    
    # Extract days pattern - handle line breaks by removing them first
    program_clean = program_name.replace('\n', '').replace('\r', '')
    days_match = re.search(r'(M-F|M-Su|M-Sun|Sa-Su|Sat-Sun|SatSun|M-Sa)', program_clean, re.IGNORECASE)
    if days_match:
        days = days_match.group(1)
        # Normalize
        if 'Sun' in days:
            days = days.replace('Sun', 'Su')
        if 'Sat' in days:
            days = days.replace('Sat', 'Sa')
        # Handle "SatSun" format (no hyphen)
        if days.upper() == 'SATSUN':
            days = 'Sa-Su'
    else:
        # Don't default - this is an error condition
        print(f"[PARSE] WARNING: Could not extract day pattern from: {program_name[:50]}")
        days = 'M-F'  # Safe default for most paid lines
    
    # Extract time pattern
    time_match = re.search(r'(\d+[ap]-\d+[ap])', program_clean, re.IGNORECASE)
    # Extract time pattern
    time_match = re.search(r'(\d+[ap]-\d+[ap])', program_clean, re.IGNORECASE)
    if time_match:
        time = time_match.group(1).lower()
    else:
        # Try finding time with colons
        time_match = re.search(r'(\d+:\d+[ap]-\d+:\d+[ap])', program_clean, re.IGNORECASE)
        if time_match:
            time = time_match.group(1).lower()
        else:
            time = 'ROS'
    
    return (language, days, time)


def _parse_week_date(week_str: str, year: int) -> str:
    """
    Parse week date string to MM/DD/YYYY format.
    
    Input formats:
    - "12-Jan" → "01/12/2026"
    - "19-Jan" → "01/19/2026"
    - "2-Feb" → "02/02/2026"
    
    Args:
        week_str: Week date string (e.g., "12-Jan")
        year: Year (e.g., 2026)
        
    Returns:
        Date in MM/DD/YYYY format
    """
    try:
        # Parse "12-Jan" format
        parts = week_str.split('-')
        if len(parts) == 2:
            day = int(parts[0])
            month_str = parts[1]
            
            # Convert month abbreviation to number
            month_map = {
                'Jan': 1, 'Feb': 2, 'Mar': 3, 'Apr': 4, 'May': 5, 'Jun': 6,
                'Jul': 7, 'Aug': 8, 'Sep': 9, 'Oct': 10, 'Nov': 11, 'Dec': 12
            }
            
            month = month_map.get(month_str, 1)
            
            return f"{month:02d}/{day:02d}/{year}"
    except Exception as e:
        print(f"[PARSE] Warning: Could not parse week date '{week_str}': {e}")
    
    return f"01/01/{datetime.now().year}"  # Fallback


def analyze_weekly_distribution(weekly_spots: List[int], week_dates: List[str],
                                contract_end_date: Optional[str] = None, year: Optional[int] = None) -> List[Dict]:
    """
    Analyze weekly spot distribution to determine how to split into Etere lines.
    
    CRITICAL RULE: Lines must split when weekly spot counts differ between consecutive weeks.
    
    Args:
        weekly_spots: List of spots per week [5, 5, 4, 4, ...]
        week_dates: List of week start dates ["12-Jan", "19-Jan", ...]
        contract_end_date: Optional contract end date to cap ranges (MM/DD/YYYY)
        year: Year for date parsing
        
    Returns:
        List of dicts with keys: start_date, end_date, spots_per_week, weeks
    """
    if not weekly_spots or not week_dates:
        return []

    if year is None:
        year = datetime.now().year

    ranges = []
    current_range = None
    
    for i, spots in enumerate(weekly_spots):
        if i >= len(week_dates):
            break
        
        week_start = _parse_week_date(week_dates[i], year)
        week_start_dt = datetime.strptime(week_start, '%m/%d/%Y')
        week_end = (week_start_dt + timedelta(days=6)).strftime('%m/%d/%Y')
        
        if current_range is None:
            # Start first range
            current_range = {
                'start_date': week_start,
                'end_date': week_end,
                'spots_per_week': spots,
                'weeks': 1
            }
        elif current_range['spots_per_week'] == spots:
            # Same spot count - extend range
            current_range['end_date'] = week_end
            current_range['weeks'] += 1
        else:
            # Different spot count - save current and start new
            ranges.append(current_range)
            current_range = {
                'start_date': week_start,
                'end_date': week_end,
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
    
    Input: "4p-7p", "1p-4p", "6a-9p", "8a-9a"
    Output: "4-7p", "1-4p", "6a-9p", "8-9a"
    """
    if time_str == "ROS":
        return "ROS"
    
    # Handle formats like "4p-7p" → "4-7p"
    match = re.match(r'(\d+)([ap])-(\d+)([ap])', time_str)
    if match:
        start_num = match.group(1)
        start_period = match.group(2)
        end_num = match.group(3)
        end_period = match.group(4)
        
        # If same period, only show at end
        if start_period == end_period:
            return f"{start_num}-{end_num}{end_period}"
        else:
            return f"{start_num}{start_period}-{end_num}{end_period}"
    
    return time_str


def get_language_block_prefix(language: str) -> List[str]:
    """
    Get block prefixes for language-based filtering.
    
    Language abbreviations in programming blocks:
    - Filipino: T
    - Hindi/Punjabi/South Asian: SA or P
    - Chinese: C or M
    - Korean: K
    - Hmong: Hm
    
    Args:
        language: Language from schedule (e.g., "Filipino", "Hindi")
        
    Returns:
        List of block prefixes to filter on
    """
    language_upper = language.upper()
    
    if 'FILIPINO' in language_upper or 'TAGALOG' in language_upper:
        return ['T']
    elif 'HINDI' in language_upper or 'PUNJABI' in language_upper or 'SOUTH ASIAN' in language_upper:
        return ['SA', 'P']
    elif 'CHINESE' in language_upper:
        return ['M', 'C']
    elif 'KOREAN' in language_upper:
        return ['K']
    elif 'HMONG' in language_upper:
        return ['Hm']
    else:
        return []


def prompt_for_spot_duration() -> tuple[int, bool]:
    """
    Prompt user for spot duration (same as Misfit, but with Bookends option).
    
    Returns:
        Tuple of (duration_seconds, is_bookend)
        - duration_seconds: 15, 30, 45, or 60
        - is_bookend: True if :15 Bookends selected
    """
    print("\n[SPOT DURATION]")
    print("What is the spot length for this order?")
    print("  1. :15 (15 seconds)")
    print("  2. :30 (30 seconds)")
    print("  3. :45 (45 seconds)")
    print("  4. :60 (60 seconds)")
    print("  5. :15 Bookends (first/last spot in break)")
    
    while True:
        choice = input("Enter choice (1-5) or duration (:15, :30, :45, :60, bookends): ").strip().lower()
        
        # Handle direct duration input
        if choice in [':15', '15', '1']:
            return (15, False)
        elif choice in [':30', '30', '2']:
            return (30, False)
        elif choice in [':45', '45', '3']:
            return (45, False)
        elif choice in [':60', '60', '4']:
            return (60, False)
        elif choice in ['5', 'bookends', 'bookend', ':15 bookends']:
            return (15, True)
        else:
            print("Invalid choice. Please enter 1-5 or :15, :30, :45, :60, bookends")


def get_impact_billing_defaults() -> Dict[str, str]:
    """Get default billing settings for Impact Marketing orders."""
    return {
        'charge_to': 'Customer share indicating agency %',
        'invoice_header': 'Agency'
    }


def get_default_separation_intervals() -> Dict[str, int]:
    """Get default separation intervals for Impact Marketing orders."""
    return {
        'customer_interval': 15,
        'event_interval': 0,
        'order_interval': 0
    }


def get_default_customer_id() -> str:
    """Get default customer ID for Impact Marketing (Big Valley Ford)."""
    return "252"


if __name__ == '__main__':
    # Test the parser
    import sys
    
    if len(sys.argv) > 1:
        pdf_path = sys.argv[1]
    else:
        pdf_path = '/mnt/user-data/uploads/Crossings_TV_Proposal_Big_Valley_Ford_Jan-December26_encrypted_.pdf'
    
    print(f"Parsing Impact Marketing PDF: {pdf_path}\n")
    
    try:
        orders = parse_impact_pdf(pdf_path)
        
        print(f"\n{'='*70}")
        print(f"FOUND {len(orders)} QUARTERLY ORDERS")
        print(f"{'='*70}\n")
        
        for order in orders:
            print(f"\n{'='*70}")
            print(f"ORDER: {order.quarter}")
            print(f"{'='*70}")
            print(f"Client: {order.client} (Customer ID: {get_default_customer_id()})")
            print(f"Agency: {order.agency}")
            print(f"Contact: {order.contact} ({order.email})")
            print(f"Market: {order.market} (CVC)")
            flight_start, flight_end = order.get_flight_dates()
            print(f"Flight: {flight_start} - {flight_end}")
            print(f"Weeks: {len(order.week_start_dates)}")
            print(f"Lines: {len(order.lines)} ({len(order.get_lines_by_type(False))} paid + {len(order.get_lines_by_type(True))} bonus)")
            print(f"\nDefault Code: {order.get_default_order_code()}")
            print(f"Default Description: {order.get_default_description()}")
            
            print(f"\n{'Lines:'}")
            for i, line in enumerate(order.lines, 1):
                bonus = " [BONUS]" if line.is_bonus else ""
                lang_prefix = get_language_block_prefix(line.language)
                lang_info = f" ({lang_prefix[0]})" if lang_prefix else ""
                
                description = line.get_description()
                print(f"{i}. {description}{lang_info}{bonus}")
                print(f"   Rate: ${line.rate:.2f} | Total: {line.total_spots} spots")
                
                # Show if line needs splitting
                ranges = analyze_weekly_distribution(line.weekly_spots, order.week_start_dates, 
                                                    flight_end, order.year)
                if len(ranges) > 1:
                    print(f"   ⚠ Will split into {len(ranges)} Etere lines")
                    for r_idx, r in enumerate(ranges, 1):
                        print(f"      Range {r_idx}: {r['spots_per_week']} spots/week × {r['weeks']} weeks")
                
                print()
    
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
