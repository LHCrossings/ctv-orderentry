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
from datetime import datetime, date, timedelta
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
    week_dates: tuple = ()  # Actual calendar start dates for each week column


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


def _ocr_extract_text(pdf_path: str, dpi: int = 300) -> str:
    """
    Extract text from an image-based PDF using tesseract OCR.

    Renders the first page at the given DPI (300 is a good balance of
    speed vs. quality for these single-column schedule tables) and passes
    the greyscale image through pytesseract.
    """
    try:
        import fitz          # PyMuPDF
        import pytesseract
        from PIL import Image
    except ImportError as e:
        print(f"[OCR] ⚠ Dependencies not available ({e}) — install pymupdf and pytesseract")
        return ""
    try:
        doc = fitz.open(pdf_path)
        page = doc[0]
        mat = fitz.Matrix(dpi / 72, dpi / 72)
        pix = page.get_pixmap(matrix=mat, colorspace=fitz.csGRAY)
        img = Image.frombytes("L", [pix.width, pix.height], pix.samples)
        text = pytesseract.image_to_string(img, config="--psm 6")
        doc.close()
        print(f"[OCR] ✓ Extracted {len(text)} chars at {dpi} DPI")
        return text
    except Exception as e:
        print(f"[OCR] ⚠ Failed: {e}")
        return ""


def _try_parse_date_token(
    token: str,
    flight_year: int,
    flight_start: Optional[date],
    flight_end: Optional[date],
    prev_month: int,
) -> Optional[date]:
    """
    Parse one whitespace-delimited token as an M/D date, tolerating OCR
    artifacts common in RPM PDFs rendered as vector outlines:

        "3/16"   → clean
        "+=3/16" → non-digit prefix stripped → "3/16"
        "119"    → slash deleted → month=1 day=19 → Jan 19
        "1119"   → slash OCR'd as "1" → month=1 day=19 → Jan 19
        "19"     → bare day, uses prev_month (e.g. still January) → Jan 19

    Returns None if the token cannot be interpreted as a plausible date.
    """
    # Strip leading garbage (e.g. "+=", "—")
    clean = re.sub(r'^[^\d]+', '', token)
    if not clean:
        return None

    def try_date(mo: int, day: int) -> Optional[date]:
        if 1 <= mo <= 12 and 1 <= day <= 31:
            try:
                return date(flight_year, mo, day)
            except ValueError:
                pass
        return None

    def in_flight(d: date) -> bool:
        if flight_start and flight_end:
            buf = timedelta(days=21)
            return (flight_start - buf) <= d <= (flight_end + buf)
        return True

    # ── Clean M/D ──────────────────────────────────────────────────────
    m = re.fullmatch(r'(\d{1,2})/(\d{1,2})', clean)
    if m:
        d = try_date(int(m.group(1)), int(m.group(2)))
        if d:
            return d

    # ── 3-digit: slash removed ("119" → 1/19) ──────────────────────────
    if re.fullmatch(r'\d{3}', clean):
        d = try_date(int(clean[0]), int(clean[1:]))
        if d:
            return d

    # ── 4-digit: slash OCR'd as extra digit ("1119" → 1/19) ────────────
    if re.fullmatch(r'\d{4}', clean):
        # Primary: single-digit month, ignore [1] (was "/"), 2-digit day
        d1 = try_date(int(clean[0]), int(clean[2:4]))
        # Secondary: two-digit month / two-digit day
        d2 = try_date(int(clean[:2]), int(clean[2:]))
        for candidate in (d1, d2):
            if candidate and in_flight(candidate):
                return candidate
        if d1:
            return d1

    # ── Bare 1-2 digit day number: use prev_month ───────────────────────
    if re.fullmatch(r'\d{1,2}', clean):
        day = int(clean)
        if 2 <= day <= 31:   # skip 0 and 1 — too noisy
            d = try_date(prev_month, day)
            if d and in_flight(d):
                return d

    return None


def _parse_week_header_dates(
    text_lines: list[str],
    flight_start: Optional[date],
    flight_end: Optional[date],
) -> tuple:
    """
    Extract actual week start dates from the table header row.

    RPM orders frequently skip weeks (e.g., Jan + March, skipping February),
    so these dates CANNOT be derived from flight_start + week_idx.

    Works with both clean pdfplumber text and OCR output:
      pdfplumber: "... Dur Wks 1/5 1/12 1/19 3/9 3/16 3/23 Total ..."
      OCR:        "Dur 1119 1/26 2/2 2/9 +=3/16 3/23 Rtg"

    Returns a tuple of date objects (one per week column), or () if not found.
    """
    flight_year = flight_start.year if flight_start else datetime.now().year
    flight_month = flight_start.month if flight_start else 1

    def parse_dates_from_tokens(tokens: list[str]) -> list[date]:
        result = []
        prev_month = flight_month
        for tok in tokens:
            d = _try_parse_date_token(tok, flight_year, flight_start, flight_end, prev_month)
            if d is not None:
                result.append(d)
                prev_month = d.month
        return result

    # Strategy 1: dates on the same line as "Wks" (clean pdfplumber text)
    for line in text_lines:
        if 'Wks' not in line:
            continue
        after = line[line.index('Wks') + 3:]
        dates = parse_dates_from_tokens(after.split())
        if len(dates) >= 2:
            return tuple(dates)

    # Strategy 2: "Dur" line with dates (OCR — dates are on the Dur row,
    # not the Wks label row)
    for line in text_lines:
        s = line.strip()
        if not re.match(r'^Dur\b', s, re.IGNORECASE):
            continue
        if s.lower().startswith('duration'):
            continue
        tokens = s.split()[1:]   # drop the "Dur" keyword itself
        dates = parse_dates_from_tokens(tokens)
        if len(dates) >= 2:
            return tuple(dates)

    return ()


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

    Tries pdfplumber first; if the PDF is image-based (vector outlines,
    scanned), falls back to tesseract OCR via PyMuPDF.

    Returns:
        Tuple of (RPMOrder, list[RPMLine])
        Returns (None, []) if parsing fails
    """
    # ── Step 1: extract text ──────────────────────────────────────────
    text = ""
    try:
        with pdfplumber.open(pdf_path) as pdf:
            text = pdf.pages[0].extract_text() or ""
    except Exception as e:
        error_str = str(e).lower()
        if "no /root object" in error_str or "not a pdf" in error_str or "corrupt" in error_str:
            print(f"[RPM PARSER] ✗ Corrupted PDF: {e}")
            return None, []
        print(f"[RPM PARSER] ✗ pdfplumber failed: {e}")
        return None, []

    if len(text.strip()) < 50:
        print("[RPM PARSER] Insufficient text from pdfplumber — trying OCR...")
        text = _ocr_extract_text(pdf_path, dpi=300)
        if len(text.strip()) < 50:
            print("[RPM PARSER] ✗ OCR also returned insufficient text")
            return None, []

    # ── Step 2: parse the extracted text ─────────────────────────────
    try:
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

        # Parse header fields
        for line in text.split('\n'):
            # Client and Estimate on same line
            if "Client:" in line and "Estimate:" in line:
                client_match = re.search(r'Client:\s*([^E]+?)Estimate:', line)
                if client_match:
                    client = client_match.group(1).strip()
                estimate_match = re.search(r'Estimate:\s*(\d+)', line)
                if estimate_match:
                    estimate = estimate_match.group(1)

            if "Description:" in line:
                desc_match = re.search(r'Description:\s*(.+)', line)
                if desc_match:
                    description = desc_match.group(1).strip()

            if "Market:" in line:
                market_match = re.search(r'Market:\s*([^F]+?)(?:Flight|$)', line)
                if market_match:
                    market_text = market_match.group(1).strip()

            if "Flight Start Date:" in line:
                date_match = re.search(r'(\d{1,2}/\d{1,2}/\d{4})', line)
                if date_match:
                    flight_start = datetime.strptime(date_match.group(1), "%m/%d/%Y").date()

            if "Flight End Date:" in line:
                date_match = re.search(r'(\d{1,2}/\d{1,2}/\d{4})', line)
                if date_match:
                    flight_end = datetime.strptime(date_match.group(1), "%m/%d/%Y").date()

            if "Product:" in line:
                product_match = re.search(r'Product:\s*([^F]+?)(?:Flight|$)', line)
                if product_match:
                    product = product_match.group(1).strip()

            if "Primary Demo:" in line:
                demo_match = re.search(r'Primary Demo:\s*(.+)', line)
                if demo_match:
                    demo = demo_match.group(1).strip()

            if "Separation between spots:" in line:
                sep_match = re.search(r'Separation between spots:\s*(\d+)', line)
                if sep_match:
                    separation = int(sep_match.group(1))

            if "Buyer:" in line:
                buyer_match = re.search(r'Buyer:\s*(.+)', line)
                if buyer_match:
                    buyer = buyer_match.group(1).strip()

        # Convert market name to code
        market_code = _extract_market_code(market_text)

        # Parse line items from text
        lines = []
        text_lines = text.split('\n')

        # Preprocess: fix spaces in times (e.g., "11 :00a" → "11:00a")
        text_lines = [re.sub(r'(\d+)\s*:\s*(\d+)([ap])', r'\1:\2\3', ln) for ln in text_lines]

        i = 0
        while i < len(text_lines):
            line_text = text_lines[i].strip()

            # Look for lines that start with daypart patterns (case-insensitive for OCR)
            if re.match(r'^(MTuWThF|SaSu|MTuWThFSaSu)', line_text, re.IGNORECASE):
                try:
                    # Handle split time: "MTuWThFSaSu 6:00a- RT $0.00..."
                    split_match = re.search(r'(\d+:\d+[ap])-\s+(RT|DT)', line_text, re.IGNORECASE)
                    if split_match:
                        i += 1
                        if i < len(text_lines):
                            time_end = text_lines[i].strip()
                            start_time = split_match.group(1)
                            day_code = split_match.group(2)
                            line_text = line_text.replace(
                                f'{start_time}- {day_code}',
                                f'{start_time}-{time_end} {day_code}'
                            )

                    parts = line_text.split()

                    if len(parts) < 10:
                        i += 1
                        continue

                    daypart_time = parts[1]
                    daypart_code = parts[2]   # RT or DT
                    rate_str = parts[3]       # $36.00
                    duration_val = parts[4]   # 30

                    # Weekly spots (up to 6 values)
                    weekly_spots = []
                    for j in range(5, min(11, len(parts))):
                        try:
                            weekly_spots.append(int(parts[j]))
                        except ValueError:
                            break

                    # Total spots
                    if len(parts) > 11:
                        try:
                            total_spots = int(parts[11])
                        except ValueError:
                            total_spots = sum(weekly_spots)
                    else:
                        total_spots = sum(weekly_spots)

                    # Language from next line
                    language_name = ""
                    if i + 1 < len(text_lines):
                        next_line = text_lines[i + 1].strip()
                        if next_line and not re.match(
                            r'^(MTuWThF|SaSu|Total)', next_line, re.IGNORECASE
                        ):
                            language_name = next_line
                            i += 1

                    # Parse rate
                    try:
                        rate = Decimal(rate_str.replace('$', '').replace(',', ''))
                    except Exception:
                        i += 1
                        continue

                    is_bonus = (rate == Decimal('0.00'))
                    program_name = f"{parts[0]} {daypart_time} {language_name}"
                    daypart, language = _normalize_daypart_name(program_name)

                    try:
                        dur_num = int(duration_val)
                        duration = f"00:00:{dur_num:02d}:00"
                    except Exception:
                        duration = "00:00:30:00"

                    lines.append(RPMLine(
                        daypart=daypart,
                        daypart_code=daypart_code,
                        rate=rate,
                        duration=duration,
                        language=language,
                        weekly_spots=weekly_spots,
                        total_spots=total_spots,
                        is_bonus=is_bonus,
                    ))

                except Exception as e:
                    print(f"[RPM PARSER] Warning: Failed to parse line: {line_text[:50]}... ({e})")

            i += 1

        # Totals
        total_spots = sum(ln.total_spots for ln in lines)
        total_cost = sum(ln.rate * ln.total_spots for ln in lines)

        # Extract actual week start dates — RPM orders skip weeks, so these
        # CANNOT be derived from flight_start + week_idx.
        week_dates = _parse_week_header_dates(text_lines, flight_start, flight_end)
        if week_dates:
            print(f"  Week dates: {[str(d) for d in week_dates]}")
        else:
            print("  Week dates: not found in header (will fall back to flight_start + week_idx)")

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
            total_cost=total_cost,
            week_dates=week_dates,
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
