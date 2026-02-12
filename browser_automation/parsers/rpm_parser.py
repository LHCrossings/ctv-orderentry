"""
RPM Agency Order Parser
Extracts order data from RPM insertion order PDFs

Key Features:
- Single market per order (no duplication like WorldLink)
- Markets: CVC (Sacramento), SFO (San Francisco), SEA (Seattle)
- Language-specific blocks (Chinese→M/C, Vietnamese→V, Asian Rotation→ROS)
- Weekly distribution pattern
- Bonus lines with $0.00 rates
"""

from dataclasses import dataclass
from datetime import datetime, date
from decimal import Decimal
from typing import Optional
import pdfplumber
import re


@dataclass(frozen=True)
class RPMOrder:
    """Immutable RPM order data."""
    client: str
    estimate_number: str
    description: str
    market: str  # CVC, SFO, or SEA
    flight_start: date
    flight_end: date
    product: str
    primary_demo: str
    separation_minutes: int
    buyer: str
    total_spots: int
    total_cost: Decimal


@dataclass(frozen=True)
class RPMLine:
    """Immutable RPM line item data."""
    daypart: str  # e.g., "M-F 6a-8p Chinese"
    daypart_code: str  # RT or DT
    rate: Decimal
    duration: str  # Always "00:00:30:00" or "00:00:15:00"
    language: str  # M/C, V, ROS
    weekly_spots: list[int]  # Distribution across weeks
    total_spots: int
    is_bonus: bool


def _extract_market_code(market_text: str) -> str:
    """
    Convert market name to market code.
    
    Args:
        market_text: Market name from PDF (e.g., "Seattle-Tacoma", "Sacramento-Stockton")
        
    Returns:
        Market code: SEA, SFO, or CVC
    """
    market_lower = market_text.lower()
    
    if "seattle" in market_lower or "tacoma" in market_lower:
        return "SEA"
    elif "san francisco" in market_lower or "oakland" in market_lower:
        return "SFO"
    elif "sacramento" in market_lower or "stockton" in market_lower:
        return "CVC"
    else:
        # Default fallback - shouldn't happen
        print(f"[WARN] Unknown market '{market_text}' - defaulting to SEA")
        return "SEA"


def _normalize_daypart_name(program_name: str) -> tuple[str, str]:
    """
    Convert RPM program name to standardized format with language.
    
    Args:
        program_name: Raw program name from PDF
        
    Returns:
        Tuple of (normalized_daypart, language_code)
        
    Examples:
        "MTuWThF 6:00a-8:00p CHINESE" → ("M-F 6a-8p Chinese", "M/C")
        "SaSu 11:00a-1:00p VIETNAMESE" → ("Sa-Su 11a-1p Vietnamese", "V")
        "MTuWThFSaSu 6:00a-12:00a Asian Rotation" → ("M-Su 6a-12m ROS", "ROS")
    """
    # Extract day pattern
    day_pattern = ""
    if "MTuWThF" in program_name and "SaSu" in program_name:
        day_pattern = "M-Su"
    elif "MTuWThF" in program_name:
        day_pattern = "M-F"
    elif "SaSu" in program_name:
        day_pattern = "Sa-Su"
    else:
        # Shouldn't happen, but handle edge cases
        day_pattern = "M-Su"
    
    # Extract time range and normalize
    time_match = re.search(r'(\d{1,2}):(\d{2})([ap])-(\d{1,2}):(\d{2})([ap])', program_name)
    if time_match:
        start_hr, start_min, start_ap, end_hr, end_min, end_ap = time_match.groups()
        
        # Format start time
        start_display = f"{int(start_hr)}{start_ap}"
        
        # Format end time - special handling for midnight/noon
        end_hour = int(end_hr)
        if end_ap == 'p' and end_hour == 12 and end_min == '00':
            end_display = "12n"  # Noon
        elif end_ap == 'a' and end_hour == 12 and end_min == '00':
            end_display = "12m"  # Midnight
        else:
            end_display = f"{end_hour}{end_ap}"
        
        time_range = f"{start_display}-{end_display}"
    else:
        time_range = "???"
    
    # Determine language
    language_code = "ROS"  # Default
    language_display = "ROS"
    
    if "CHINESE" in program_name.upper():
        language_code = "M/C"
        language_display = "Chinese"
    elif "VIETNAMESE" in program_name.upper():
        language_code = "V"
        language_display = "Vietnamese"
    elif "ASIAN ROTATION" in program_name.upper():
        language_code = "ROS"
        language_display = "ROS"
    
    # Build final daypart string
    daypart = f"{day_pattern} {time_range} {language_display}"
    
    return daypart, language_code


def _parse_weekly_distribution(row_data: list[str], start_index: int, num_weeks: int) -> list[int]:
    """
    Extract weekly spot distribution from table row.
    
    Args:
        row_data: Row data from PDF table
        start_index: Index where weekly data starts (after "Dur" column)
        num_weeks: Number of weeks in flight
        
    Returns:
        List of spot counts per week
    """
    weekly_spots = []
    
    for i in range(num_weeks):
        try:
            spot_value = row_data[start_index + i].strip()
            weekly_spots.append(int(spot_value) if spot_value else 0)
        except (IndexError, ValueError):
            weekly_spots.append(0)
    
    return weekly_spots


def parse_rpm_pdf(pdf_path: str) -> tuple[Optional[RPMOrder], list[RPMLine]]:
    """
    Parse RPM insertion order PDF.
    
    Args:
        pdf_path: Path to RPM PDF file
        
    Returns:
        Tuple of (RPMOrder, list[RPMLine])
        Returns (None, []) if parsing fails
    """
    try:
        with pdfplumber.open(pdf_path) as pdf:
            first_page = pdf.pages[0]
            text = first_page.extract_text()
            
            # Check if PDF is image-based (no extractable text)
            if not text or len(text.strip()) < 50:
                print("\n" + "="*70)
                print("❌ IMAGE-BASED PDF DETECTED")
                print("="*70)
                print("\n⚠️  This PDF cannot be processed because it's an image-based PDF")
                print("    (scanned or OCR'd) without proper text structure.\n")
                print("📋 TO FIX THIS ISSUE:")
                print("    1. Open this PDF file in Google Chrome")
                print("    2. Press Ctrl+P (or Cmd+P on Mac) to print")
                print("    3. Select 'Save as PDF' as the printer")
                print("    4. Save with a new filename (e.g., add '_fixed' to the name)")
                print("    5. Run this script again with the new PDF file\n")
                print(f"📁 Current file: {pdf_path}\n")
                print("="*70)
                return None, []
            
    except Exception as e:
        # Check for specific PDF corruption errors
        error_str = str(e).lower()
        if "no /root object" in error_str or "not a pdf" in error_str or "corrupt" in error_str:
            print("\n" + "="*70)
            print("❌ CORRUPTED PDF STRUCTURE DETECTED")
            print("="*70)
            print("\n⚠️  This PDF has structural corruption and cannot be read.\n")
            print("📋 TO FIX THIS ISSUE:")
            print("    1. Open this PDF file in Google Chrome")
            print("    2. Press Ctrl+P (or Cmd+P on Mac) to print")
            print("    3. Select 'Save as PDF' as the printer")
            print("    4. Save with a new filename (e.g., add '_fixed' to the name)")
            print("    5. Run this script again with the new PDF file\n")
            print(f"📁 Current file: {pdf_path}")
            print(f"🔧 Error details: {e}\n")
            print("="*70)
            return None, []
        else:
            # Other unexpected error
            print(f"[RPM PARSER] Error parsing PDF: {e}")
            import traceback
            traceback.print_exc()
            return None, []
    
    # Continue with normal parsing if we get here
    try:
        with pdfplumber.open(pdf_path) as pdf:
            first_page = pdf.pages[0]
            text = first_page.extract_text()
            
            # Extract header information
            client = ""
            estimate = ""
            description = ""
            market_text = ""
            flight_start = None
            flight_end = None
            product = ""
            demo = ""
            separation = 30  # Default
            buyer = ""
            
            # Parse header fields - handle Chrome-saved format where fields are on same line
            for line in text.split('\n'):
                # Client and Estimate on same line
                if "Client:" in line and "Estimate:" in line:
                    client_match = re.search(r'Client:\s*([^E]+?)Estimate:', line)
                    if client_match:
                        client = client_match.group(1).strip()
                    estimate_match = re.search(r'Estimate:\s*(\d+)', line)
                    if estimate_match:
                        estimate = estimate_match.group(1)
                
                # Description on same line as Media
                if "Description:" in line:
                    desc_match = re.search(r'Description:\s*(.+)', line)
                    if desc_match:
                        description = desc_match.group(1).strip()
                
                # Market and Flight Start on same line
                if "Market:" in line:
                    market_match = re.search(r'Market:\s*([^F]+?)(?:Flight|$)', line)
                    if market_match:
                        market_text = market_match.group(1).strip()
                
                # Flight dates
                if "Flight Start Date:" in line:
                    date_match = re.search(r'(\d{1,2}/\d{1,2}/\d{4})', line)
                    if date_match:
                        flight_start = datetime.strptime(date_match.group(1), "%m/%d/%Y").date()
                
                if "Flight End Date:" in line:
                    date_match = re.search(r'(\d{1,2}/\d{1,2}/\d{4})', line)
                    if date_match:
                        flight_end = datetime.strptime(date_match.group(1), "%m/%d/%Y").date()
                
                # Product
                if "Product:" in line:
                    product_match = re.search(r'Product:\s*([^F]+?)(?:Flight|$)', line)
                    if product_match:
                        product = product_match.group(1).strip()
                
                # Primary Demo
                if "Primary Demo:" in line:
                    demo_match = re.search(r'Primary Demo:\s*(.+)', line)
                    if demo_match:
                        demo = demo_match.group(1).strip()
                
                # Separation
                if "Separation between spots:" in line:
                    sep_match = re.search(r'Separation between spots:\s*(\d+)', line)
                    if sep_match:
                        separation = int(sep_match.group(1))
                
                # Buyer
                if "Buyer:" in line:
                    buyer_match = re.search(r'Buyer:\s*(.+)', line)
                    if buyer_match:
                        buyer = buyer_match.group(1).strip()
            
            # Convert market name to code
            market_code = _extract_market_code(market_text)
            
            # Parse lines from text (Chrome format has no tables)
            lines = []
            text_lines = text.split('\n')
            
            # Preprocess: fix spaces in times (e.g., "11 :00a" → "11:00a")
            text_lines = [re.sub(r'(\d+)\s*:\s*(\d+)([ap])', r'\1:\2\3', line) for line in text_lines]
            
            i = 0
            while i < len(text_lines):
                line_text = text_lines[i].strip()
                
                # Look for lines that start with daypart patterns
                if re.match(r'^(MTuWThF|SaSu|MTuWThFSaSu)', line_text):
                    try:
                        # Check if this line has a time that's split (time ends with "- " before RT/DT)
                        # Example: "MTuWThFSaSu 6:00a- RT $0.00..."
                        split_match = re.search(r'(\d+:\d+[ap])-\s+(RT|DT)', line_text)
                        if split_match:
                            # Time is split - next line has the end time
                            i += 1
                            if i < len(text_lines):
                                time_end = text_lines[i].strip()
                                start_time = split_match.group(1)  # e.g., "6:00a"
                                day_code = split_match.group(2)     # RT or DT
                                # Reconstruct: replace "6:00a- RT" with "6:00a-12:00a RT"
                                line_text = line_text.replace(f'{start_time}- {day_code}', f'{start_time}-{time_end} {day_code}')
                        
                        # Parse the daypart line
                        parts = line_text.split()
                        
                        if len(parts) < 10:
                            i += 1
                            continue
                        
                        # Get daypart time (e.g., "6:00a-8:00p")
                        daypart_time = parts[1]
                        daypart_code = parts[2]  # RT or DT
                        rate_str = parts[3]      # $36.00
                        duration_val = parts[4]  # 30
                        
                        # Weekly spots (next 6 values)
                        weekly_spots = []
                        for j in range(5, min(11, len(parts))):
                            try:
                                weekly_spots.append(int(parts[j]))
                            except ValueError:
                                break
                        
                        # Get total spots
                        if len(parts) > 11:
                            try:
                                total_spots = int(parts[11])
                            except ValueError:
                                total_spots = sum(weekly_spots)
                        else:
                            total_spots = sum(weekly_spots)
                        
                        # Get language from next line
                        language_name = ""
                        if i + 1 < len(text_lines):
                            next_line = text_lines[i + 1].strip()
                            if next_line and not re.match(r'^(MTuWThF|SaSu|Total)', next_line):
                                language_name = next_line
                                i += 1  # Skip the language line
                        
                        # Parse rate
                        try:
                            rate = Decimal(rate_str.replace('$', '').replace(',', ''))
                        except:
                            i += 1
                            continue
                        
                        # Determine if bonus
                        is_bonus = (rate == Decimal('0.00'))
                        
                        # Build full program name for normalization
                        program_name = f"{parts[0]} {daypart_time} {language_name}"
                        
                        # Normalize daypart and extract language code
                        daypart, language = _normalize_daypart_name(program_name)
                        
                        # Format duration
                        try:
                            dur_num = int(duration_val)
                            duration = f"00:00:{dur_num:02d}:00"
                        except:
                            duration = "00:00:30:00"
                        
                        # Create line object
                        line = RPMLine(
                            daypart=daypart,
                            daypart_code=daypart_code,
                            rate=rate,
                            duration=duration,
                            language=language,
                            weekly_spots=weekly_spots,
                            total_spots=total_spots,
                            is_bonus=is_bonus
                        )
                        lines.append(line)
                        
                    except Exception as e:
                        print(f"[RPM PARSER] Warning: Failed to parse line: {line_text[:50]}... ({e})")
                
                i += 1
            
            # Calculate totals
            total_spots = sum(line.total_spots for line in lines)
            total_cost = sum(line.rate * line.total_spots for line in lines)
            
            # Create order object
            order = RPMOrder(
                client=client,
                estimate_number=estimate,
                description=description,
                market=market_code,
                flight_start=flight_start,
                flight_end=flight_end,
                product=product,
                primary_demo=demo,
                separation_minutes=separation,
                buyer=buyer,
                total_spots=total_spots,
                total_cost=total_cost
            )
            
            print(f"\n[RPM PARSER] ✓ Parsed order successfully")
            print(f"  Client: {order.client}")
            print(f"  Estimate: {order.estimate_number}")
            print(f"  Market: {order.market}")
            print(f"  Flight: {order.flight_start} to {order.flight_end}")
            print(f"  Lines: {len(lines)} ({total_spots} total spots)")
            
            return order, lines
            
    except Exception as e:
        print(f"[RPM PARSER] Error parsing PDF: {e}")
        import traceback
        traceback.print_exc()
        return None, []


# Test function
if __name__ == "__main__":
    import sys
    
    if len(sys.argv) < 2:
        print("Usage: python rpm_parser.py <path_to_rpm_pdf>")
        sys.exit(1)
    
    pdf_path = sys.argv[1]
    order, lines = parse_rpm_pdf(pdf_path)
    
    if order:
        print(f"\n{'='*70}")
        print("ORDER SUMMARY")
        print('='*70)
        print(f"Client: {order.client}")
        print(f"Estimate: {order.estimate_number}")
        print(f"Market: {order.market}")
        print(f"Description: {order.description}")
        print(f"Flight: {order.flight_start} to {order.flight_end}")
        print(f"Total: {order.total_spots} spots @ ${order.total_cost:,.2f}")
        print(f"\n{'='*70}")
        print("LINES")
        print('='*70)
        
        for idx, line in enumerate(lines, 1):
            bonus_tag = " [BONUS]" if line.is_bonus else ""
            print(f"\n{idx}. {line.daypart}{bonus_tag}")
            print(f"   Code: {line.daypart_code} | Language: {line.language}")
            print(f"   Rate: ${line.rate:,.2f} | Duration: {line.duration}")
            print(f"   Weekly: {line.weekly_spots}")
            print(f"   Total: {line.total_spots} spots")
