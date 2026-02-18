"""
Charmaine Client Order Parser
Parses Charmaine's Excel-based insertion order PDFs (printed to PDF).

═══════════════════════════════════════════════════════════════════════════════
FORMAT DESCRIPTION
═══════════════════════════════════════════════════════════════════════════════

Charmaine's template is an Excel-based IO printed to PDF. Visual format:

HEADER SECTION:
    - Title line: "Crossings TV: {Advertiser} {Campaign}"
    - Advertiser, Contact, Email, Station, Languages fields
    - "AIRTIME - Schedule -:{duration} seconds   Week of {start} through {end}"
    - Market line: "{Market description}"

TABLE SECTION (pdfplumber extracts cleanly):
    Row 0: Market header row (e.g., "Central Valley of California...")
    Row 1: Column headers: Language | Daypart | Unit Value | Week1 | Week2 | ... | Total Spots | Total Amount
    Row 2+: Data rows:
        - Paid lines: Language name | time range | $rate | spots... | total | $amount
        - Bonus lines: Language name | "{Language} ROS Bonus" | $- | spots... | total | $-
    Summary rows: "Total Paid", "Total Bonus", "Total Units"

KEY CHARACTERISTICS:
    - Bonus rows identified by "ROS Bonus" in Daypart column
    - BONUS rows in text extraction start with "BONUS" prefix
    - Rate column: "$ 30.00" for paid, "$ -" for bonus
    - Week columns contain spot counts per week
    - Single market per page (market detected from header row)
    - No agency name present = likely CLIENT order (not agency)
    - Duration specified in header: ":15 seconds" or ":30 seconds"

═══════════════════════════════════════════════════════════════════════════════
"""

import pdfplumber
import re
import os
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from typing import Optional


@dataclass(frozen=True)
class CharmaineWeekColumn:
    """Represents a week column header with its date."""
    label: str          # e.g., "27-Apr", "4-May"
    start_date: str     # e.g., "04/27/2026" (MM/DD/YYYY for Etere)


@dataclass(frozen=True)
class CharmaineLine:
    """Represents a single line item from Charmaine order."""
    language: str           # "Chinese", "Filipino", "Hmong", etc.
    daypart: str            # "M-F 7p-11p; Sat-Sun 7p-12a" or "Chinese ROS Bonus"
    is_bonus: bool          # True if this is a bonus/ROS line
    rate: float             # Per-spot rate (0.0 for bonus)
    weekly_spots: list[int] # Spots per week [10, 6] etc.
    total_spots: int        # Total spots across all weeks
    total_amount: float     # Total dollar amount


@dataclass
class CharmaineOrder:
    """Complete parsed Charmaine order."""
    advertiser: str                     # "Sacramento Region Community Foundation"
    contact: str                        # "Vasey Coman"
    email: str                          # "Vasey@sacregcf.org"
    campaign: str                       # "Big Day Of Giving" (from title)
    station: str                        # "Crossings TV"
    languages: str                      # "Mandarin, Tagalog, Vietnamese, Hindi, Hmong, Korean"
    market: str                         # "CVC" (detected from market description)
    duration_seconds: int               # 15 or 30
    flight_start: str                   # "04/27/2026" (MM/DD/YYYY)
    flight_end: str                     # "05/07/2026" (MM/DD/YYYY)
    week_columns: list[CharmaineWeekColumn]  # Week date columns
    lines: list[CharmaineLine] = field(default_factory=list)
    pdf_path: str = ""
    year: int = 0                       # Detected or inferred year


# ═══════════════════════════════════════════════════════════════════════════════
# MARKET DETECTION
# ═══════════════════════════════════════════════════════════════════════════════

MARKET_KEYWORDS: dict[str, list[str]] = {
    "CVC": ["central valley", "sacramento", "kbtv"],
    "SFO": ["san francisco", "ktsf"],
    "LAX": ["los angeles", "la market"],
    "SEA": ["seattle"],
    "HOU": ["houston"],
    "CMP": ["chicago", "minneapolis"],
    "WDC": ["washington", "d.c."],
    "NYC": ["new york", "new jersey"],
    "MMT": ["multimarket", "national"],
    "DAL": ["dallas"],
}


def detect_market_from_text(text: str) -> str:
    """
    Detect market code from market description text.
    
    Args:
        text: Market description line from PDF
        
    Returns:
        Market code (e.g., "CVC") or "UNKNOWN"
    """
    text_lower = text.lower()
    for market_code, keywords in MARKET_KEYWORDS.items():
        if any(kw in text_lower for kw in keywords):
            return market_code
    return "UNKNOWN"


# ═══════════════════════════════════════════════════════════════════════════════
# DATE PARSING
# ═══════════════════════════════════════════════════════════════════════════════

def parse_flight_dates(header_text: str, year: int) -> tuple[str, str]:
    """
    Parse flight start and end dates from header text.
    
    Handles formats like:
        "Week of 4/27 through May 7"
        "Week of 1/6 through 1/31"
        "3/23/2026 -5/24/2026"
        "3/23/2026-5/24/2026"
        "Flight schedule 3/23/2026 -5/24/2026"
    
    Args:
        header_text: The header text containing dates
        year: Year to use for dates without year
        
    Returns:
        Tuple of (start_date, end_date) in MM/DD/YYYY format
    """
    # Try direct date range: "M/D/YYYY -M/D/YYYY" or "M/D/YYYY-M/D/YYYY"
    direct_match = re.search(
        r'(\d{1,2}/\d{1,2}/\d{4})\s*-\s*(\d{1,2}/\d{1,2}/\d{4})',
        header_text
    )
    if direct_match:
        start_raw = direct_match.group(1)
        end_raw = direct_match.group(2)
        # Normalize to MM/DD/YYYY
        start_date = _normalize_date_mmddyyyy(start_raw)
        end_date = _normalize_date_mmddyyyy(end_raw)
        return start_date, end_date
    
    # Try "Week of {start} through {end}"
    match = re.search(
        r'[Ww]eek\s+of\s+(.+?)\s+through\s+(.+?)(?:\s*$)',
        header_text
    )
    if not match:
        return ("", "")
    
    start_raw = match.group(1).strip()
    end_raw = match.group(2).strip()
    
    start_date = _parse_flexible_date(start_raw, year)
    end_date = _parse_flexible_date(end_raw, year)
    
    return start_date, end_date


def _normalize_date_mmddyyyy(date_str: str) -> str:
    """Normalize M/D/YYYY to MM/DD/YYYY."""
    parts = date_str.split('/')
    if len(parts) == 3:
        return f"{int(parts[0]):02d}/{int(parts[1]):02d}/{parts[2]}"
    return date_str


def _parse_flexible_date(date_str: str, year: int) -> str:
    """
    Parse a flexible date string into MM/DD/YYYY format.
    
    Handles: "4/27", "May 7", "October 31", "1/6", "Jan 15"
    
    Args:
        date_str: Flexible date string
        year: Year to use
        
    Returns:
        Date in MM/DD/YYYY format
    """
    month_names = {
        'jan': 1, 'january': 1, 'feb': 2, 'february': 2,
        'mar': 3, 'march': 3, 'apr': 4, 'april': 4,
        'may': 5, 'jun': 6, 'june': 6,
        'jul': 7, 'july': 7, 'aug': 8, 'august': 8,
        'sep': 9, 'september': 9, 'oct': 10, 'october': 10,
        'nov': 11, 'november': 11, 'dec': 12, 'december': 12,
    }
    
    # Try numeric: "4/27"
    numeric_match = re.match(r'(\d{1,2})/(\d{1,2})', date_str)
    if numeric_match:
        month = int(numeric_match.group(1))
        day = int(numeric_match.group(2))
        return f"{month:02d}/{day:02d}/{year}"
    
    # Try named: "May 7", "October 31"
    named_match = re.match(r'([A-Za-z]+)\s+(\d{1,2})', date_str)
    if named_match:
        month_str = named_match.group(1).lower()
        day = int(named_match.group(2))
        month = month_names.get(month_str, 0)
        if month:
            return f"{month:02d}/{day:02d}/{year}"
    
    return ""


def parse_week_column_dates(
    column_labels: list[str],
    year: int
) -> list[CharmaineWeekColumn]:
    """
    Parse week column headers into structured date objects.
    
    Column headers like "27-Apr", "4-May" → CharmaineWeekColumn with start dates.
    
    Args:
        column_labels: List of column header strings
        year: Year to use for dates
        
    Returns:
        List of CharmaineWeekColumn objects
    """
    month_abbrev = {
        'jan': 1, 'feb': 2, 'mar': 3, 'apr': 4,
        'may': 5, 'jun': 6, 'jul': 7, 'aug': 8,
        'sep': 9, 'oct': 10, 'nov': 11, 'dec': 12,
    }
    
    columns = []
    for label in column_labels:
        # Try "27-Apr" format
        match = re.match(r'(\d{1,2})-([A-Za-z]+)', label)
        if match:
            day = int(match.group(1))
            month_str = match.group(2).lower()[:3]
            month = month_abbrev.get(month_str, 0)
            if month:
                date_str = f"{month:02d}/{day:02d}/{year}"
                columns.append(CharmaineWeekColumn(label=label, start_date=date_str))
                continue
        
        # Try "Apr-27" format (reversed)
        match = re.match(r'([A-Za-z]+)-(\d{1,2})', label)
        if match:
            month_str = match.group(1).lower()[:3]
            day = int(match.group(2))
            month = month_abbrev.get(month_str, 0)
            if month:
                date_str = f"{month:02d}/{day:02d}/{year}"
                columns.append(CharmaineWeekColumn(label=label, start_date=date_str))
                continue
        
        # Fallback: keep label but empty date
        columns.append(CharmaineWeekColumn(label=label, start_date=""))
    
    return columns


# ═══════════════════════════════════════════════════════════════════════════════
# RATE PARSING
# ═══════════════════════════════════════════════════════════════════════════════

def parse_rate(rate_str: str) -> float:
    """
    Parse rate from table cell.
    
    Handles: "$ 30.00", "$30", "$ -", "$-", "-", ""
    
    Args:
        rate_str: Rate string from table cell
        
    Returns:
        Float rate value (0.0 for bonus/missing)
    """
    if not rate_str:
        return 0.0
    
    cleaned = rate_str.replace('$', '').replace(',', '').strip()
    
    if cleaned in ['-', '', '–', '—']:
        return 0.0
    
    try:
        return float(cleaned)
    except ValueError:
        return 0.0


def parse_amount(amount_str: str) -> float:
    """
    Parse total amount from table cell.
    
    Handles: "$ 480.00", "$ 2,500.00", "$ -"
    """
    return parse_rate(amount_str)  # Same logic


def parse_spots(spots_str: str) -> int:
    """
    Parse spot count from table cell.
    
    Handles: "10", "0", "", None
    """
    if not spots_str:
        return 0
    try:
        return int(spots_str.strip())
    except ValueError:
        return 0


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN PARSER
# ═══════════════════════════════════════════════════════════════════════════════

def parse_charmaine_pdf(pdf_path: str) -> list[CharmaineOrder]:
    """
    Parse a Charmaine-style insertion order PDF.
    
    Each page may represent a separate order (if multi-page with different
    markets or campaigns). Currently handles single-page orders.
    
    Args:
        pdf_path: Path to the PDF file
        
    Returns:
        List of CharmaineOrder objects (one per page/order)
    """
    orders: list[CharmaineOrder] = []
    
    with pdfplumber.open(pdf_path) as pdf:
        for page_num, page in enumerate(pdf.pages):
            text = page.extract_text() or ""
            tables = page.extract_tables()
            
            # Skip pages with no meaningful content (e.g., signature audit pages)
            if not tables or len(tables) < 2:
                continue
            
            # Check if this page has actual order data
            has_order_data = any(
                row for table in tables for row in table
                if row and any(cell and ('$' in str(cell) or 'ROS' in str(cell))
                              for cell in row if cell)
            )
            if not has_order_data:
                continue
            
            order = _parse_page(text, tables, pdf_path, page_num)
            if order and order.lines:
                orders.append(order)
    
    return orders


def _parse_page(
    text: str,
    tables: list,
    pdf_path: str,
    page_num: int
) -> Optional[CharmaineOrder]:
    """
    Parse a single page into a CharmaineOrder.
    
    Args:
        text: Full text of the page
        tables: All tables extracted from the page
        pdf_path: Source PDF path
        page_num: Page number (0-indexed)
        
    Returns:
        CharmaineOrder or None if page doesn't contain valid order data
    """
    lines_text = text.split('\n')
    
    # ═══════════════════════════════════════════════════════════════
    # PARSE HEADER FIELDS FROM TEXT AND TABLES
    # ═══════════════════════════════════════════════════════════════
    
    advertiser = ""
    contact = ""
    email = ""
    station = ""
    languages = ""
    campaign = ""
    duration_seconds = 30  # Default
    year = datetime.now().year
    
    # ── Strategy 1: Parse from free text lines (BDOG-style) ──
    
    for line in lines_text:
        line_stripped = line.strip()
        
        # Title line: "Crossings TV: {Advertiser} "{Campaign}""
        if line_stripped.startswith("Crossings TV:"):
            # Extract campaign from quotes
            quote_match = re.search(r'"(.+?)"', line_stripped)
            if quote_match:
                campaign = quote_match.group(1)
        
        # Explicit fields (BDOG format: "Advertiser Name Here")
        if line_stripped.startswith("Advertiser ") and not advertiser:
            advertiser = line_stripped.replace("Advertiser ", "", 1).strip()
        elif line_stripped.startswith("Contact ") and not contact:
            contact = line_stripped.replace("Contact ", "", 1).strip()
        elif line_stripped.startswith("Email ") and not email:
            email = line_stripped.replace("Email ", "", 1).strip()
        elif line_stripped.startswith("Station ") and not station:
            station = line_stripped.replace("Station ", "", 1).strip()
        elif line_stripped.startswith("Languages ") and not languages:
            languages = line_stripped.replace("Languages ", "", 1).strip()
        
        # AIRTIME line: duration + flight dates (BDOG format)
        if "AIRTIME" in line_stripped and "Schedule" in line_stripped:
            dur_match = re.search(r':(\d+)\s*seconds?', line_stripped)
            if dur_match:
                duration_seconds = int(dur_match.group(1))
            
            year_match = re.search(r'20\d{2}', line_stripped)
            if year_match:
                year = int(year_match.group())
        
        # Flight schedule line (Ntooitive format: "Flight schedule 3/23/2026 -5/24/2026")
        if "flight schedule" in line_stripped.lower() and not year:
            year_match = re.search(r'20\d{2}', line_stripped)
            if year_match:
                year = int(year_match.group())
    
    # ── Strategy 2: Parse from header tables (Ntooitive-style) ──
    # These PDFs have structured key-value tables instead of free text
    
    for table in tables:
        if not table or len(table) < 2:
            continue
        
        # Check if this looks like a header table (small, key-value pairs)
        if len(table) > 12:
            continue  # Too many rows — likely a data table
        
        for row in table:
            if not row or len(row) < 2:
                continue
            
            key = str(row[0]).strip().rstrip(':') if row[0] else ""
            val = str(row[1]).strip() if row[1] else ""
            key_lower = key.lower()
            
            if not key or not val:
                continue
            
            if key_lower == "advertiser" and not advertiser:
                # Clean up: "L.A. Care Health (LA Covered) campaign" → stop at "campaign"
                # Some PDFs merge adjacent cells
                advertiser = val
                # Remove trailing noise like "campaign", "Billing", "Broadcast"
                for noise in [" campaign", " Billing", " Broadcast"]:
                    if noise in advertiser:
                        advertiser = advertiser[:advertiser.index(noise)].strip()
            
            elif key_lower == "contact" and not contact:
                contact = val
            
            elif key_lower == "email" and not email:
                email = val
            
            elif key_lower == "media buying agency" and not campaign:
                # Agency name is in this field — but we track it for billing detection
                pass
            
            elif key_lower == "flight schedule":
                # "3/23/2026 -5/24/2026"
                year_match = re.search(r'20\d{2}', val)
                if year_match:
                    year = int(year_match.group())
            
            elif key_lower == "market" and not station:
                station = val
    
    # ── Extract campaign from title or advertiser line ──
    
    if not campaign:
        # Check the data table header row for campaign hints
        # e.g., "California-Los Angeles Spectrum 1519 3/23/2026-5/24/2026"
        # Or check the text for "Crossings TV Media Proposal" (no campaign in title)
        
        # Look in text for explicit campaign label
        for line in lines_text:
            ls = line.strip()
            # "Advertiser: L.A. Care Health (LA Covered) campaign Broadcast"
            # → The word(s) between advertiser and "campaign" keyword are the client
            # → The word after "campaign" is actually the billing cycle, not campaign name
            pass
        
        # If no campaign found, try to infer from context
        # Use "Medi-Cal" from filename or "Broadcast" as generic
        if not campaign:
            # Check filename for campaign hints
            filename = os.path.basename(pdf_path) if pdf_path else ""
            # Look for meaningful keywords between underscores/spaces
            for part in re.split(r'[_\s]+', filename):
                # Remove file extension and parenthetical suffixes
                part_clean = re.sub(r'\.\w+$', '', part).strip()
                part_clean = re.sub(r'\(\d+\)$', '', part_clean).strip()
                if part_clean and part_clean.lower() not in [
                    'crossings', 'tv', 'proposal', 'contract', 'pdf', '003',
                    '(003)', str(year),
                ]:
                    # Skip agency name and advertiser name fragments
                    if advertiser and part_clean.lower() in advertiser.lower():
                        continue
                    # Skip known agency names  
                    _skip_names = [
                        "worldlink", "tatari", "tcaa", "daviselen", "misfit",
                        "igraphix", "admerasia", "opad", "rpm", "sagent",
                        "galeforce", "ntooitive",
                    ]
                    if part_clean.lower() in _skip_names:
                        continue
                    # This might be a campaign hint
                    if len(part_clean) > 2:
                        # Strip trailing year (e.g., "Medi-Cal2026" → "Medi-Cal")
                        campaign = re.sub(r'20\d{2}$', '', part_clean).strip()
                        if campaign:
                            break
        
        if not campaign:
            campaign = "Broadcast"
    
    # If no explicit year found, check the PDF filename
    if year == datetime.now().year:
        year_in_path = re.search(r'20\d{2}', pdf_path)
        if year_in_path:
            year = int(year_in_path.group())
    
    # ═══════════════════════════════════════════════════════════════
    # PARSE MAIN DATA TABLE
    # ═══════════════════════════════════════════════════════════════
    
    # Find the main data table
    # The main table has: language rows, week date columns, and $ amounts
    # Key differentiator: week date headers like "23-Mar", "6-Apr", etc.
    main_table = None
    for table in tables:
        if not table or len(table) < 3:
            continue
        
        # Check if any row has week date patterns (e.g., "23-Mar", "6-Apr", "11-May")
        has_week_dates = False
        has_dollars = False
        for row in table:
            if not row:
                continue
            for cell in row:
                cell_str = str(cell) if cell else ""
                # Week date pattern: number + dash + month abbreviation
                if re.search(r'\d{1,2}-(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)', cell_str):
                    has_week_dates = True
                if '$' in cell_str:
                    has_dollars = True
        
        if has_week_dates and has_dollars:
            if main_table is None or len(table) > len(main_table):
                main_table = table
    
    if not main_table:
        return None
    
    # ── Detect column layout dynamically ──
    # Some PDFs have an extra leading column (e.g., "BONUS" label in col 0)
    # Find the offset by locating "Language" in the header row
    
    header_row = main_table[1] if len(main_table) > 1 else main_table[0] if main_table else []
    
    # Detect column offset: find which column has "Language" header
    col_offset = 0
    for i, cell in enumerate(header_row):
        if cell and "language" in str(cell).lower():
            col_offset = i
            break
    
    # Detect if there are Spot Type / Length columns between Daypart and week data
    # BDOG format:  Language | Daypart | Rate | Week1 | Week2 | ... | Total | Amount
    # Ntooitive:    [extra] | Language | Daypart | SpotType | Length | Week1 | ... | Total | Rate | LineTotal | NetTotal
    has_spot_type_col = False
    spot_type_offset = 0
    for i, cell in enumerate(header_row):
        if cell and "spot type" in str(cell).lower():
            has_spot_type_col = True
            spot_type_offset = 2  # Spot Type + Length = 2 extra columns
            break
    
    # Row 0: Market header
    market_text = ""
    # Search across all columns in row 0 for market info
    if main_table[0]:
        for cell in main_table[0]:
            if cell and len(str(cell)) > 10:
                market_text = str(cell)
                break
    market = detect_market_from_text(market_text)
    
    # Find week column labels from header row
    # Weeks start after: Language, Daypart, [SpotType, Length], then week dates
    week_start_col = col_offset + 2 + spot_type_offset  # After language + daypart + optional cols
    
    # Find where weeks end: look for "Total" column
    week_end_col = len(header_row)
    for i in range(week_start_col, len(header_row)):
        cell = str(header_row[i]).strip().lower() if header_row[i] else ""
        if "total" in cell:
            week_end_col = i
            break
    
    week_labels: list[str] = []
    for i in range(week_start_col, week_end_col):
        cell = header_row[i]
        if cell and str(cell).strip():
            week_labels.append(str(cell).strip())
    
    week_columns = parse_week_column_dates(week_labels, year)
    
    # Parse flight dates from text or header table
    airtime_line = ""
    for line in lines_text:
        if "AIRTIME" in line:
            airtime_line = line.strip()
            break
    
    # Also check table rows for AIRTIME info
    if tables:
        for tbl in tables:
            if tbl:
                for row in tbl:
                    for cell in row:
                        if cell and "AIRTIME" in str(cell):
                            airtime_line = str(cell).strip()
                            break
    
    # Also check for "Flight schedule" format
    if not airtime_line:
        for line in lines_text:
            if "flight schedule" in line.lower():
                airtime_line = line.strip()
                break
    
    # Try to get flight dates from header table too
    for tbl in tables:
        if tbl and len(tbl) <= 12:
            for row in tbl:
                if row and row[0] and "flight" in str(row[0]).lower():
                    airtime_line = f"{row[0]} {row[1] if len(row) > 1 and row[1] else ''}"
                    break
    
    flight_start, flight_end = parse_flight_dates(airtime_line, year)
    
    # ── Detect duration from table data if not found in header ──
    # Look at "Length" column in data rows (e.g., ":30", ":15")
    if duration_seconds == 30:  # Still default, try to confirm from table
        for row_idx in range(2, min(5, len(main_table))):
            row = main_table[row_idx]
            if not row:
                continue
            # Length column is at col_offset + 2 + 1 (after SpotType) if has_spot_type_col
            length_col = col_offset + 2 + 1 if has_spot_type_col else -1
            if has_spot_type_col and length_col < len(row) and row[length_col]:
                dur_match = re.search(r':(\d+)', str(row[length_col]))
                if dur_match:
                    duration_seconds = int(dur_match.group(1))
                    break
    
    # ═══════════════════════════════════════════════════════════════
    # PARSE DATA ROWS
    # ═══════════════════════════════════════════════════════════════
    
    parsed_lines: list[CharmaineLine] = []
    
    for row_idx in range(2, len(main_table)):
        row = main_table[row_idx]
        
        if not row:
            continue
        
        # Get the language cell (at col_offset)
        lang_cell = str(row[col_offset]).strip() if col_offset < len(row) and row[col_offset] else ""
        
        # Also check column before offset for "BONUS" marker
        bonus_marker = ""
        if col_offset > 0 and row[0]:
            bonus_marker = str(row[0]).strip().upper()
        
        # Skip summary/total rows
        lang_lower = lang_cell.lower()
        if lang_lower in ['total paid', 'total bonus', 'total units', 'total bonuses',
                          'production', 'production ( talent hosting)']:
            continue
        if not lang_cell and not bonus_marker:
            continue
        
        # Detect bonus from multiple signals
        daypart_col = col_offset + 1
        daypart = str(row[daypart_col]).strip() if daypart_col < len(row) and row[daypart_col] else ""
        
        is_bonus = (
            "ros bonus" in daypart.lower()
            or "bonus" in daypart.lower()
            or bonus_marker == "BONUS"
            or (has_spot_type_col and (col_offset + 2) < len(row) 
                and row[col_offset + 2] and "bonus" in str(row[col_offset + 2]).lower())
        )
        
        # Language: combine bonus_marker cell with language cell if needed
        # e.g., bonus_marker="BONUS", lang_cell="CHINESE" → language="Chinese"
        language = lang_cell
        if bonus_marker == "BONUS" and not lang_cell:
            continue  # Skip if no language info at all
        
        # Rate: find it in the right column
        # For Ntooitive format with Spot Type + Length, rate is in the "Promo Unit Cost" 
        # column which is after the week columns
        # For BDOG format, rate is at col_offset + 2
        rate = 0.0
        if has_spot_type_col:
            # Rate is in the column after Total Unit # (which is at week_end_col)
            rate_col = week_end_col + 1
            if rate_col < len(row) and row[rate_col]:
                rate = parse_rate(str(row[rate_col]))
        else:
            rate_col = col_offset + 2
            if rate_col < len(row) and row[rate_col]:
                rate = parse_rate(str(row[rate_col]))
        
        # Weekly spots
        weekly_spots: list[int] = []
        num_week_cols = len(week_columns) if week_columns else 0
        for i in range(week_start_col, week_start_col + num_week_cols):
            if i < len(row):
                weekly_spots.append(parse_spots(str(row[i]) if row[i] else "0"))
            else:
                weekly_spots.append(0)
        
        # Total spots (at week_end_col)
        total_spots = 0
        if week_end_col < len(row) and row[week_end_col]:
            total_spots = parse_spots(str(row[week_end_col]))
        
        # Total amount (after rate column)
        total_amount = 0.0
        amount_col = (week_end_col + 2) if has_spot_type_col else (week_end_col + 1)
        if amount_col < len(row) and row[amount_col]:
            total_amount = parse_amount(str(row[amount_col]))
        
        # Skip rows with no weekly spots (summary rows that slipped through)
        if all(s == 0 for s in weekly_spots) and total_spots == 0:
            continue
        
        parsed_lines.append(CharmaineLine(
            language=language,
            daypart=daypart,
            is_bonus=is_bonus,
            rate=rate,
            weekly_spots=weekly_spots,
            total_spots=total_spots,
            total_amount=total_amount,
        ))
    
    if not parsed_lines:
        return None
    
    return CharmaineOrder(
        advertiser=advertiser,
        contact=contact,
        email=email,
        campaign=campaign,
        station=station,
        languages=languages,
        market=market,
        duration_seconds=duration_seconds,
        flight_start=flight_start,
        flight_end=flight_end,
        week_columns=week_columns,
        lines=parsed_lines,
        pdf_path=pdf_path,
        year=year,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# TESTING / STANDALONE
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import sys
    
    if len(sys.argv) < 2:
        print("Usage: python charmaine_parser.py <pdf_path>")
        sys.exit(1)
    
    pdf_path = sys.argv[1]
    orders = parse_charmaine_pdf(pdf_path)
    
    for i, order in enumerate(orders):
        print(f"\n{'='*70}")
        print(f"ORDER {i+1}")
        print(f"{'='*70}")
        print(f"  Advertiser: {order.advertiser}")
        print(f"  Campaign:   {order.campaign}")
        print(f"  Contact:    {order.contact}")
        print(f"  Market:     {order.market}")
        print(f"  Duration:   :{order.duration_seconds}s")
        print(f"  Flight:     {order.flight_start} - {order.flight_end}")
        print(f"  Weeks:      {[wc.label for wc in order.week_columns]}")
        print(f"  Lines:      {len(order.lines)}")
        
        for j, line in enumerate(order.lines):
            spot_type = "BNS" if line.is_bonus else "PAID"
            print(f"\n  [{j+1}] {spot_type} {line.language}")
            print(f"      Daypart: {line.daypart}")
            print(f"      Rate: ${line.rate:.2f}")
            print(f"      Weekly: {line.weekly_spots}")
            print(f"      Total: {line.total_spots} spots, ${line.total_amount:.2f}")
