"""
iGraphix Order Parser
Parses iGraphix agency insertion order PDFs
Handles Pechanga Resort Casino and Sky River Casino orders
Format: Single-page order with ad codes and spot allocations
"""

import pdfplumber
import re
from datetime import datetime, timedelta
from dataclasses import dataclass
from typing import List, Dict, Optional, Tuple
import math


@dataclass
class IGraphixAdCode:
    """Represents a single ad code line from iGraphix order."""
    ad_code: str  # e.g., "FS0308", "JP0126" (without # prefix)
    description: str  # e.g., "Father & Son 2026 Tour"
    spots: int  # Number of spots for this ad code
    start_date: str  # Format: MM/DD/YY
    end_date: str  # Format: MM/DD/YY
    is_bonus: bool = False  # Set during allocation


@dataclass
class IGraphixOrder:
    """Represents an iGraphix order."""
    purchase_number: str  # e.g., "30141"
    client: str  # e.g., "Pechanga Resort Casino", "Sky River Casino"
    customer_id: int  # Etere customer ID
    market: str  # e.g., "LAX", "SFO", "CVC"
    language: str  # e.g., "Filipino", "Vietnamese", "Hmong"
    language_abbrev: str  # e.g., "T", "V", "Hm" (for Sky River codes)
    market_abbrev: str  # e.g., "SF", "CV" (for Sky River codes)
    net_total: float  # Net amount from PDF
    gross_total: float  # Calculated: net_total / 0.85
    paid_spots: int  # Number of paid spots
    bonus_spots: int  # Number of bonus spots
    spot_duration: int  # Duration in seconds (15, 30, 60)
    rate_per_spot: float  # Calculated: gross_total / paid_spots (rounded to 2 decimals)
    paid_time_range: str  # e.g., "M-Su: 4pm-6pm" or "M-F: 11am-12pm/12pm-1pm"
    paid_days: str  # Parsed days: "M-Su", "M-F", "Sa-Su"
    paid_time: str  # Parsed time: "4-6p", "11a-1p", "6-8p"
    bonus_time_range: Optional[str]  # e.g., "ROS: 30 sec" or None if not separate
    ad_codes: List[IGraphixAdCode]  # List of ad code lines
    flight_start: str  # Earliest start date from ad codes
    flight_end: str  # Latest end date from ad codes
    channel_description: str  # Full channel description from PDF
    
    def get_contract_code(self) -> str:
        """Generate contract code based on client."""
        if "Pechanga" in self.client:
            return f"IG Pechanga {self.purchase_number}"
        elif "Sky River" in self.client:
            return f"IG SRC {self.purchase_number} {self.language_abbrev} {self.market_abbrev}"
        else:
            return f"IG {self.purchase_number}"
    
    def get_contract_description(self) -> str:
        """Generate contract description based on client."""
        if "Pechanga" in self.client:
            return f"Pechanga Resort Casino {self.purchase_number}"
        elif "Sky River" in self.client:
            return f"Sky River Casino Est {self.purchase_number} {self.language_abbrev} {self.market_abbrev}"
        else:
            return f"{self.client} {self.purchase_number}"
    
    def allocate_paid_bonus(self):
        """
        Allocate paid/bonus status to ad codes using top-to-bottom allocation.
        
        This modifies the ad_codes list in-place, setting is_bonus flag.
        Algorithm: Fill paid spots first (top to bottom), then mark rest as bonus.
        
        If an ad code straddles the paid/bonus boundary, it will be split into two entries:
        - One entry with paid spots (is_bonus=False)
        - One entry with bonus spots (is_bonus=True)
        """
        spots_allocated = 0
        new_ad_codes = []
        
        for ad_code in self.ad_codes:
            if spots_allocated + ad_code.spots <= self.paid_spots:
                # This entire ad code is paid
                ad_code.is_bonus = False
                new_ad_codes.append(ad_code)
                spots_allocated += ad_code.spots
            elif spots_allocated < self.paid_spots:
                # This ad code needs to be split
                paid_remaining = self.paid_spots - spots_allocated
                bonus_portion = ad_code.spots - paid_remaining
                
                # Create paid entry
                paid_entry = IGraphixAdCode(
                    ad_code=ad_code.ad_code,
                    description=ad_code.description,
                    spots=paid_remaining,
                    start_date=ad_code.start_date,
                    end_date=ad_code.end_date,
                    is_bonus=False
                )
                new_ad_codes.append(paid_entry)
                
                # Create bonus entry
                bonus_entry = IGraphixAdCode(
                    ad_code=ad_code.ad_code,
                    description=ad_code.description,
                    spots=bonus_portion,
                    start_date=ad_code.start_date,
                    end_date=ad_code.end_date,
                    is_bonus=True
                )
                new_ad_codes.append(bonus_entry)
                
                spots_allocated += ad_code.spots
            else:
                # All remaining ad codes are bonus
                ad_code.is_bonus = True
                new_ad_codes.append(ad_code)
        
        # Replace ad_codes list with the split version
        self.ad_codes = new_ad_codes


def _verify_spot_duration(text: str, parsed_duration: int, paid_spots: int, bonus_spots: int) -> int:
    """
    Verify spot duration and prompt user if there's a mismatch.
    
    Checks if the "Total" line mentions a different duration than what was parsed
    from the line items. Common issue: line says "15 sec" but total says "30 sec".
    
    Uses a cache to avoid asking the same question twice for the same PDF.
    
    Args:
        text: Full PDF text
        parsed_duration: Duration parsed from line items (e.g., 15 or 30)
        paid_spots: Number of paid spots
        bonus_spots: Number of bonus spots
        
    Returns:
        Confirmed spot duration (user's choice if mismatch detected)
    """
    import re
    import hashlib
    
    # Create a cache key from the text content
    cache_key = hashlib.md5(text.encode()).hexdigest()
    
    # Check if we've already asked about this PDF
    if not hasattr(_verify_spot_duration, '_cache'):
        _verify_spot_duration._cache = {}
    
    if cache_key in _verify_spot_duration._cache:
        cached_duration = _verify_spot_duration._cache[cache_key]
        print(f"[CACHE] Using cached duration: {cached_duration} seconds")
        return cached_duration
    
    # Look for "Total: (XX) spots of YY sec" pattern
    total_match = re.search(r'Total[:\s]+\(?\d+\)?\s+spots?\s+of\s+(\d+)\s+sec', text, re.IGNORECASE)
    
    result_duration = parsed_duration  # Default
    
    if total_match:
        total_duration = int(total_match.group(1))
        
        # Check for mismatch
        if total_duration != parsed_duration:
            print("\n" + "="*70)
            print("⚠ DURATION MISMATCH DETECTED - POSSIBLE TYPO IN IO")
            print("="*70)
            print(f"Line items show: {parsed_duration} sec spots")
            print(f"Total line shows: {total_duration} sec spots")
            print(f"Paid spots: {paid_spots}, Bonus spots: {bonus_spots}")
            print()
            print("Which duration is correct?")
            print(f"  1. {parsed_duration} seconds (from line items)")
            print(f"  2. {total_duration} seconds (from total)")
            print()
            
            while True:
                choice = input("Enter 1 or 2: ").strip()
                if choice == '1':
                    print(f"✓ Using {parsed_duration} seconds")
                    result_duration = parsed_duration
                    break
                elif choice == '2':
                    print(f"✓ Using {total_duration} seconds")
                    result_duration = total_duration
                    break
                else:
                    print("Invalid choice. Please enter 1 or 2.")
    
    # Cache the result
    _verify_spot_duration._cache[cache_key] = result_duration
    
    return result_duration


def parse_igraphix_pdf(pdf_path: str) -> IGraphixOrder:
    """
    Parse iGraphix PDF and extract order data.
    
    Format: Single-page order with:
    - Header: Purchase #, Advertiser
    - Channel description with language/market
    - Paid/bonus spot counts
    - Ad code lines with dates and spot counts
    
    Args:
        pdf_path: Path to the iGraphix PDF file
        
    Returns:
        IGraphixOrder object with all order details
    """
    with pdfplumber.open(pdf_path) as pdf:
        # Extract all text from first page
        page_text = pdf.pages[0].extract_text()
        
        # Parse header fields
        purchase_number = _extract_purchase_number(page_text)
        client = _extract_client(page_text)
        net_total = _extract_net_total(page_text)
        
        # Parse channel description to get market and language
        channel_desc = _extract_channel_description(page_text)
        market, market_abbrev = _parse_market_from_channel(channel_desc, client)
        language, language_abbrev = _parse_language_from_channel(channel_desc)
        
        # Determine customer ID
        customer_id = _get_customer_id(client)
        
        # Parse spot counts and time ranges
        paid_spots, bonus_spots, spot_duration = _parse_spot_counts(page_text)
        
        # Check for duration mismatch and prompt user if found
        spot_duration = _verify_spot_duration(page_text, spot_duration, paid_spots, bonus_spots)
        
        paid_time_range, bonus_time_range = _parse_time_ranges(page_text)
        paid_days, paid_time = _parse_days_and_time(paid_time_range)
        
        # Calculate billing
        gross_total = net_total / 0.85
        rate_per_spot = round(gross_total / paid_spots, 2) if paid_spots > 0 else 0.0
        
        # Parse ad code lines
        ad_codes = _extract_ad_codes(page_text)
        
        # Get flight dates from ad codes
        if ad_codes:
            flight_start = min(ad.start_date for ad in ad_codes)
            flight_end = max(ad.end_date for ad in ad_codes)
        else:
            flight_start = "01/01/26"
            flight_end = "01/31/26"
        
        order = IGraphixOrder(
            purchase_number=purchase_number,
            client=client,
            customer_id=customer_id,
            market=market,
            language=language,
            language_abbrev=language_abbrev,
            market_abbrev=market_abbrev,
            net_total=net_total,
            gross_total=gross_total,
            paid_spots=paid_spots,
            bonus_spots=bonus_spots,
            spot_duration=spot_duration,
            rate_per_spot=rate_per_spot,
            paid_time_range=paid_time_range,
            paid_days=paid_days,
            paid_time=paid_time,
            bonus_time_range=bonus_time_range,
            ad_codes=ad_codes,
            flight_start=flight_start,
            flight_end=flight_end,
            channel_description=channel_desc
        )
        
        # Allocate paid/bonus to ad codes
        order.allocate_paid_bonus()
        
        return order


def _extract_purchase_number(text: str) -> str:
    """Extract purchase number from PDF text."""
    # Pattern: "Purchase #: 00030141"
    match = re.search(r'Purchase\s*#:\s*(\d+)', text, re.IGNORECASE)
    if match:
        # Remove leading zeros but keep at least one digit
        num = match.group(1).lstrip('0')
        return num if num else '0'
    return "Unknown"


def _extract_client(text: str) -> str:
    """Extract client/advertiser name from PDF text."""
    # Pattern: "Advertiser:\n IGraphix\n c/o\n Pechanga Resort Casino"
    # We want ONLY "Pechanga Resort Casino", not the rest of the document
    import re
    
    # Look for the pattern and capture only up to the next section break
    match = re.search(r'Advertiser:.*?c/o\s+([^\n]+)', text, re.DOTALL | re.IGNORECASE)
    if match:
        client = match.group(1).strip()
        return client
    
    return "Unknown"


def _extract_net_total(text: str) -> float:
    """Extract net total amount from PDF text."""
    # Pattern: "Net Total: $2,295.00"
    match = re.search(r'Net\s+Total:\s*\$?([\d,]+\.?\d*)', text, re.IGNORECASE)
    if match:
        amount_str = match.group(1).replace(',', '')
        return float(amount_str)
    return 0.0


def _extract_channel_description(text: str) -> str:
    """
    Extract full channel description line including package info.
    
    May span multiple lines for Sky River orders that have format:
    "Crossing TV Comcast Ch. 398 Central Valley
    - Vietnamese package: $525/ month"
    """
    # Patterns:
    # "Crossing TV Spectrum channel 1519"
    # "Crossing TV Comcast Ch. 398 Central Valley"
    # "Crossing TV - XfinityTV CH. 3131 SF Vietnamese"
    # "Crossing TV - XfinityTV Ch. 3131 SF Filipino"
    
    patterns = [
        r'(Crossing\s+TV.*?(?:channel|Ch\.)\s+\d+.*?)(?:\n|$)',
        r'(Crossing\s+TV.*?(?:Spectrum|Comcast|XfinityTV).*?)(?:\n|$)',
    ]
    
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            desc = match.group(1).strip()
            
            # Check if the next line has package info (e.g., "- Vietnamese package:")
            # This helps with language detection for multi-line descriptions
            package_pattern = r'-\s*(\w+)\s+package'
            remaining_text = text[match.end():match.end()+100]  # Look ahead 100 chars
            package_match = re.search(package_pattern, remaining_text, re.IGNORECASE)
            if package_match:
                # Append the package line to the description
                desc += " " + package_match.group(0)
            
            # Clean up extra whitespace
            desc = re.sub(r'\s+', ' ', desc)
            return desc
    
    return "Unknown Channel"


def _parse_market_from_channel(channel_desc: str, client: str) -> Tuple[str, str]:
    """
    Parse market code and abbreviation from channel description.
    
    Returns:
        Tuple of (market_code, market_abbrev)
        e.g., ("LAX", "LAX") or ("SFO", "SF") or ("CVC", "CV")
    """
    channel_upper = channel_desc.upper()
    
    # Pechanga always LAX
    if "Pechanga" in client:
        return ("LAX", "LAX")
    
    # Sky River - parse from channel description
    if "SF" in channel_upper or "SAN FRANCISCO" in channel_upper:
        return ("SFO", "SF")
    elif "CENTRAL VALLEY" in channel_upper or " CV" in channel_upper:
        return ("CVC", "CV")
    elif "LA" in channel_upper or "LOS ANGELES" in channel_upper:
        return ("LAX", "LA")
    elif "SEATTLE" in channel_upper:
        return ("SEA", "SEA")
    elif "SPECTRUM CHANNEL 1519" in channel_upper:
        return ("LAX", "LAX")
    
    # Default
    return ("LAX", "LAX")


def _parse_language_from_channel(channel_desc: str) -> Tuple[str, str]:
    """
    Parse language and abbreviation from channel description.
    
    Returns:
        Tuple of (language_name, language_abbrev)
        e.g., ("Filipino", "T") or ("Vietnamese", "V")
    """
    channel_upper = channel_desc.upper()
    
    # Language mapping
    if "FILIPINO" in channel_upper or "TAGALOG" in channel_upper:
        return ("Filipino", "T")
    elif "VIETNAMESE" in channel_upper:
        return ("Vietnamese", "V")
    elif "HMONG" in channel_upper:
        return ("Hmong", "Hm")
    elif "KOREAN" in channel_upper:
        return ("Korean", "K")
    elif "MANDARIN" in channel_upper or "CHINESE" in channel_upper:
        return ("Chinese", "M")
    elif "CANTONESE" in channel_upper:
        return ("Chinese", "C")
    elif "SOUTH ASIAN" in channel_upper or "PUNJABI" in channel_upper:
        return ("South Asian", "SA")
    elif "JAPANESE" in channel_upper:
        return ("Japanese", "J")
    
    # Check package description line
    # Pattern: "- Filipino package: $525/ month"
    package_match = re.search(r'-\s*(\w+)\s+package', channel_desc, re.IGNORECASE)
    if package_match:
        lang = package_match.group(1).capitalize()
        # Map to abbreviation
        lang_map = {
            "Filipino": "T",
            "Vietnamese": "V",
            "Hmong": "Hm",
            "Korean": "K",
            "Chinese": "M",
            "Mandarin": "M",
            "Cantonese": "C",
            "South": "SA",
            "Punjabi": "SA",
            "Japanese": "J"
        }
        abbrev = lang_map.get(lang, "T")
        return (lang, abbrev)
    
    # Default to Filipino
    return ("Filipino", "T")


def _get_customer_id(client: str) -> int:
    """Get Etere customer ID based on client name."""
    client_upper = client.upper()
    
    if "PECHANGA" in client_upper:
        return 26
    elif "SKY RIVER" in client_upper:
        return 191
    
    return 0  # Unknown


def _parse_spot_counts(text: str) -> Tuple[int, int, int]:
    """
    Parse paid spots, bonus spots, and duration from PDF text.
    
    Returns:
        Tuple of (paid_spots, bonus_spots, duration_seconds)
    """
    paid_spots = 0
    bonus_spots = 0
    duration = 30  # Default
    
    # Pattern 1: "1) M-Su: 4pm-6pm x 30 spots" (paid spots line)
    # Look for numbered line with time and spots (NOT containing "Bonus")
    paid_match = re.search(r'1\)[^:]+:[^x]+x\s+(\d+)\s+spots?', text, re.IGNORECASE)
    if paid_match:
        paid_spots = int(paid_match.group(1))
    
    # Pattern 2: "2) Bonus- ROS: 30 sec x 18 spots" or "2) Bonus: ROS (30 sec) x 7 spots" or "2) Bonus x 9 spots"
    bonus_patterns = [
        r'2\)\s*Bonus[^x]+x\s+(\d+)\s+spots?',  # Handles all bonus formats
        r'Bonus\s+x\s+(\d+)\s+spots?',  # Fallback for "Bonus x 9 spots"
    ]
    for pattern in bonus_patterns:
        bonus_match = re.search(pattern, text, re.IGNORECASE)
        if bonus_match:
            bonus_spots = int(bonus_match.group(1))
            break
    
    # Parse duration: "30 sec" or "30 Sec" - look for it in the entire text
    duration_match = re.search(r'(\d+)\s+sec', text, re.IGNORECASE)
    if duration_match:
        duration = int(duration_match.group(1))
    
    return paid_spots, bonus_spots, duration


def _parse_time_ranges(text: str) -> Tuple[str, Optional[str]]:
    """
    Parse paid and bonus time ranges from PDF text.
    
    Returns:
        Tuple of (paid_time_range, bonus_time_range)
        e.g., ("M-Su: 4pm-6pm", "Bonus- ROS")
    """
    paid_time_range = ""
    bonus_time_range = None
    
    # Pattern for paid time: "1) M-Su: 4pm-6pm x 30 spots"
    # Match everything between "1)" and " x " to capture the full time range
    paid_match = re.search(r'1\)\s*([^x]+?)\s+x\s+\d+', text, re.IGNORECASE)
    if paid_match:
        paid_time_range = paid_match.group(1).strip()
    
    # Pattern for bonus time: "2) Bonus- ROS: 30 sec x 18 spots"
    bonus_match = re.search(r'2\)\s*(Bonus[-:\s]+.*?)(?=\n|thru|$)', text, re.IGNORECASE)
    if bonus_match:
        bonus_time_range = bonus_match.group(1).strip()
    
    return paid_time_range, bonus_time_range


def _parse_days_and_time(time_range: str) -> Tuple[str, str]:
    """
    Parse days and time from time range string.
    
    Args:
        time_range: e.g., "M-Su: 4pm-6pm" or "M-F: 11am-12pm/12pm-1pm" or "Sat-Sun: 6pm - 8pm"
        
    Returns:
        Tuple of (days, time)
        e.g., ("M-Su", "4-6p") or ("M-F", "11a-1p") or ("Sa-Su", "6-8p")
    """
    # Normalize Sat-Sun to Sa-Su
    time_range = time_range.replace("Sat-Sun", "Sa-Su")
    time_range = time_range.replace("Sat", "Sa")
    time_range = time_range.replace("Sun", "Su")
    
    # Split on colon
    if ':' in time_range:
        parts = time_range.split(':', 1)
        days = parts[0].strip()
        time_str = parts[1].strip()
    else:
        days = "M-Su"
        time_str = time_range
    
    # Parse time: "4pm-6pm" → "4-6p"
    # or: "11am-12pm/12pm-1pm" → "11a-1p"
    # or: "6pm - 8pm" → "6-8p"
    time_str = time_str.replace(' ', '')  # Remove spaces
    
    # Handle slash notation (11am-12pm/12pm-1pm → 11am-1pm)
    if '/' in time_str:
        # Get start from first range, end from last range
        ranges = time_str.split('/')
        start_match = re.match(r'(\d+:?\d*[ap]m?)', ranges[0])
        end_match = re.search(r'-(\d+:?\d*[ap]m?)$', ranges[-1])
        if start_match and end_match:
            time_str = f"{start_match.group(1)}-{end_match.group(1)}"
    
    # Convert to simplified format: "4pm-6pm" → "4-6p"
    # Pattern: "4pm-6pm" or "11am-1pm" or "6:00pm-8:00pm"
    time_match = re.match(r'(\d+):?\d*([ap])m?-(\d+):?\d*([ap])m?', time_str, re.IGNORECASE)
    if time_match:
        start_hour = time_match.group(1)
        start_period = time_match.group(2).lower()
        end_hour = time_match.group(3)
        end_period = time_match.group(4).lower()
        
        time = f"{start_hour}{start_period}-{end_hour}{end_period}"
    else:
        time = time_str
    
    return days, time


def _extract_ad_codes(text: str) -> List[IGraphixAdCode]:
    """
    Extract ad code lines from PDF text.
    
    Pattern:
    "02/1/26 thru 2/28/26: Father & Son 2026 Tour ad/#FS0308 (15 spots)"
    "01/1/26 thru 1/31/26: January Promotion ad/#JP0126 (19 spots)"
    "02/10/26 thru 2/28/26: LNY Greeting -Saddle ad/#LP0228-G (13 spots)"
    """
    ad_codes = []
    
    # Pattern: "MM/D/YY thru M/DD/YY: Description ad/#CODE (XX spots)"
    # Updated to handle hyphens and other characters in description
    pattern = r'(\d{1,2}/\d{1,2}/\d{2})\s+thru\s+(\d{1,2}/\d{1,2}/\d{2}):\s*(.+?)\s+ad/#([\w-]+)\s*\((\d+)\s+spots?\)'
    
    matches = re.finditer(pattern, text, re.IGNORECASE)
    
    for match in matches:
        start_date_raw = match.group(1)
        end_date_raw = match.group(2)
        description = match.group(3).strip()
        ad_code = match.group(4).strip()  # Already without #
        spots = int(match.group(5))
        
        # Normalize dates to MM/DD/YY format
        start_date = _normalize_date(start_date_raw)
        end_date = _normalize_date(end_date_raw)
        
        ad_codes.append(IGraphixAdCode(
            ad_code=ad_code,
            description=description,
            spots=spots,
            start_date=start_date,
            end_date=end_date,
            is_bonus=False  # Will be set during allocation
        ))
    
    return ad_codes


def _normalize_date(date_str: str) -> str:
    """
    Normalize date string to MM/DD/YY format.
    
    Args:
        date_str: e.g., "2/1/26" or "02/01/26" or "2/28/26"
        
    Returns:
        Normalized date: "02/01/26"
    """
    parts = date_str.split('/')
    if len(parts) == 3:
        month = parts[0].zfill(2)
        day = parts[1].zfill(2)
        year = parts[2]
        return f"{month}/{day}/{year}"
    return date_str


def calculate_max_daily_spots(total_spots: int, days_pattern: str, start_date: str, end_date: str) -> int:
    """
    Calculate max daily spots based on total spots and available days.
    
    Formula: ceil(total_spots / available_days)
    
    Args:
        total_spots: Total spots for this line
        days_pattern: Day pattern (e.g., "M-Su", "M-F", "Sa-Su")
        start_date: Start date in MM/DD/YY format
        end_date: End date in MM/DD/YY format
        
    Returns:
        Max daily spots (always >= 1)
    """
    # Calculate total available days
    start_dt = datetime.strptime(start_date, '%m/%d/%y')
    end_dt = datetime.strptime(end_date, '%m/%d/%y')
    total_days = (end_dt - start_dt).days + 1
    
    # Adjust for day pattern
    if days_pattern == "M-F":
        # Approximately 5/7 of days
        available_days = int(total_days * 5 / 7)
    elif days_pattern == "Sa-Su":
        # Approximately 2/7 of days
        available_days = int(total_days * 2 / 7)
    elif days_pattern == "M-Sa":
        # Approximately 6/7 of days
        available_days = int(total_days * 6 / 7)
    else:
        # M-Su or other - use all days
        available_days = total_days
    
    # Ensure at least 1 day
    available_days = max(1, available_days)
    
    # Calculate max daily spots
    max_daily = math.ceil(total_spots / available_days)
    
    return max(1, max_daily)


def get_language_block_prefix(language: str) -> List[str]:
    """
    Get block prefix(es) for a given language.
    
    Args:
        language: Language name (e.g., "Filipino", "Vietnamese")
        
    Returns:
        List of block prefixes to filter by
    """
    mapping = {
        'Filipino': ['T'],
        'Vietnamese': ['V'],
        'Hmong': ['Hm'],
        'Korean': ['K'],
        'Chinese': ['M', 'C'],
        'Mandarin': ['M'],
        'Cantonese': ['C'],
        'South Asian': ['SA', 'P'],
        'Punjabi': ['P'],
        'Japanese': ['J']
    }
    
    return mapping.get(language, [])


def get_igraphix_billing_defaults() -> Dict[str, any]:
    """
    Get iGraphix-specific billing defaults.
    
    Returns:
        Dictionary with billing settings
    """
    return {
        'billing_type': 'N',  # Net
        'commission_type': '2',  # Agency Commission 15%
        'agency_commission': '15.00',
        'cash_discount': '2.00'
    }


def get_default_separation_intervals(language: str, is_bonus: bool) -> Dict[str, int]:
    """
    Get default separation intervals for iGraphix orders.
    
    iGraphix uses language-specific customer intervals:
    - Filipino Paid: 30, Filipino Bonus: 30
    - Vietnamese Paid: 30, Vietnamese Bonus: 20
    - Hmong Paid: 20, Hmong Bonus: 15
    
    Order and event intervals are always 0.
    
    Args:
        language: Language name (e.g., "Filipino", "Vietnamese", "Hmong")
        is_bonus: True if this is a bonus line, False if paid
        
    Returns:
        Dictionary with separation intervals in seconds (converted from minutes)
    """
    # Define customer intervals by language and paid/bonus
    customer_intervals = {
        'Filipino': {'paid': 30, 'bonus': 30},
        'Vietnamese': {'paid': 30, 'bonus': 20},
        'Hmong': {'paid': 20, 'bonus': 15},
        # Defaults for other languages (use Filipino as baseline)
        'Chinese': {'paid': 30, 'bonus': 30},
        'Mandarin': {'paid': 30, 'bonus': 30},
        'Cantonese': {'paid': 30, 'bonus': 30},
        'Korean': {'paid': 30, 'bonus': 30},
        'South Asian': {'paid': 30, 'bonus': 30},
        'Punjabi': {'paid': 30, 'bonus': 30},
        'Japanese': {'paid': 30, 'bonus': 30}
    }
    
    # Get the customer interval for this language and type
    lang_intervals = customer_intervals.get(language, {'paid': 30, 'bonus': 30})
    customer_interval_minutes = lang_intervals['bonus'] if is_bonus else lang_intervals['paid']
    
    # Convert minutes to seconds and return
    return {
        'min_sep': customer_interval_minutes,  # Customer interval (minutes)
        'max_sep': 0,  # Order interval (always 0)
        'interval': 0  # Event interval (always 0)
    }
