"""
Time Advertising Broadcast Order Parser

Parses "BROADCAST ORDER" PDF format from Time Advertising, Inc.
Orders are addressed to Ms. Charmaine Lane at Crossings TV and
submitted by Bonnie Ho on behalf of the advertiser.

═══════════════════════════════════════════════════════════════════════════════
FORMAT DESCRIPTION
═══════════════════════════════════════════════════════════════════════════════

HEADER SECTION:
    BROADCAST ORDER
    TO: Ms. Charmaine Lane    DATE: M/D/YYYY
    Crossing TV - {Market} ({Xfinity ID}/{Channel})
    (Tel: ...)
    FROM: {name}
    ADVERTISER: {advertiser}
    AD TITLE: {title}
              - {title2}
    LENGTH: : {N} secs

SCHEDULE TABLE (space-aligned plain text, NOT a grid table):
    DAY-PART SCHEDULE                    Gross Rate  Total  Total
    PROGRAM / TIME   FLIGHT DATES  M T W T F S S  Per Spot  Spots  Cost

    {Section Header e.g. "Prime Time Dream Drive 8 Car Giveaway"}
    M-F: Cant. News/Talk 7pm-8pm    wk of 3/2/26   1 1  $180.00  2  $ 3 60.00
                                     wk of 3/9/26   1 1  $180.00  2  $ 3 60.00
    M-F: Mand. News/Drama 8pm-10pm  wk of 3/2/26   1 1  $180.00  2  $ 3 60.00
                                     wk of 3/9/26   1 1  $180.00  2  $ 3 60.00

    Thematic {Title} (Existing)
    M-Sun: ROS Free spots            wk of 3/2/26   1 1       2
                                     wk of 3/9/26     1       1

    FLIGHT TOTAL : N
    GROSS TOTAL: $N
    BILL TO: Time Advertising, Inc.

KEY CHARACTERISTICS:
    - One PDF per market (SF = SFO, Sacramento = CVC)
    - Paid dayparts: Cantonese + Mandarin blocks with rates
    - Thematic spots: bonus/free (no rate, no cost)
    - Day columns: individual "1" under M T W T F S S (visual columns, not parsed)
    - Dollar amounts may have PDF rendering spaces: "$ 3 60.00" = "$360.00"
    - Week dates: "wk of 3/2/26" → "03/02/2026"
    - Sacramento variant: program line may appear without week on same line

═══════════════════════════════════════════════════════════════════════════════
"""

import re
import pdfplumber
from dataclasses import dataclass, field
from typing import Optional


# ═══════════════════════════════════════════════════════════════════════════════
# DATA STRUCTURES
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class TimeAdvertisingWeek:
    """A single week's spot data for one program line."""
    week_start: str   # "03/02/2026" (MM/DD/YYYY)
    spots: int        # total spots this week
    rate: float       # per-spot gross rate (0.0 for thematic/free)


@dataclass(frozen=True)
class TimeAdvertisingLine:
    """A program/daypart line with its weekly spot schedule."""
    program: str                      # "M-F: Cant. News/Talk 7pm-8pm"
    section: str                      # "Prime Time Dream Drive..." or "Thematic..."
    is_thematic: bool                 # True = free/bonus spots, False = paid
    weeks: tuple                      # tuple[TimeAdvertisingWeek, ...]

    @property
    def total_spots(self) -> int:
        return sum(w.spots for w in self.weeks)

    @property
    def weekly_spots(self) -> list:
        return [w.spots for w in self.weeks]

    @property
    def week_start_dates(self) -> list:
        return [w.week_start for w in self.weeks]

    @property
    def rate(self) -> float:
        """Per-spot rate (from first non-zero week)."""
        for w in self.weeks:
            if w.rate > 0:
                return w.rate
        return 0.0


@dataclass
class TimeAdvertisingOrder:
    """Complete parsed Time Advertising broadcast order."""
    advertiser: str              # "Graton Casino"
    station: str                 # "Crossing TV - SF (Xfinity3131/KQTA)"
    market: str                  # "SFO" or "CVC"
    ad_titles: list              # ["Graton Casino - Dream Drive 8 Car Giveaway", ...]
    duration_seconds: int        # 30
    from_name: str               # "Bonnie Ho"
    order_date: str              # "3/2/2026"
    agency: str                  # "Time Advertising, Inc."
    lines: list = field(default_factory=list)
    pdf_path: str = ""

    @property
    def paid_lines(self) -> list:
        return [l for l in self.lines if not l.is_thematic]

    @property
    def thematic_lines(self) -> list:
        return [l for l in self.lines if l.is_thematic]

    @property
    def flight_start(self) -> str:
        """First week start across all paid lines."""
        dates = [w.week_start for ln in self.paid_lines for w in ln.weeks]
        return min(dates) if dates else ""

    @property
    def flight_end(self) -> str:
        """Last week start across all paid lines (approximate)."""
        dates = [w.week_start for ln in self.paid_lines for w in ln.weeks]
        return max(dates) if dates else ""


# ═══════════════════════════════════════════════════════════════════════════════
# MARKET DETECTION
# ═══════════════════════════════════════════════════════════════════════════════

_MARKET_KEYWORDS: dict = {
    "CVC": ["sacramento", "kbtv"],
    "SFO": ["- sf", "kqta", "san francisco"],
    "SEA": ["seattle"],
    "LAX": ["los angeles"],
    "NYC": ["new york"],
    "HOU": ["houston"],
}


def _detect_market(station_text: str) -> str:
    lower = station_text.lower()
    for code, keywords in _MARKET_KEYWORDS.items():
        if any(kw in lower for kw in keywords):
            return code
    return "UNKNOWN"


# ═══════════════════════════════════════════════════════════════════════════════
# DATE PARSING
# ═══════════════════════════════════════════════════════════════════════════════

def _parse_week_date(date_str: str) -> str:
    """
    Parse "M/D/YY" from "wk of M/D/YY" into "MM/DD/YYYY".

    Examples:
        "3/2/26"  → "03/02/2026"
        "3/30/26" → "03/30/2026"
    """
    parts = date_str.strip().split('/')
    if len(parts) != 3:
        return date_str
    month, day = int(parts[0]), int(parts[1])
    year = 2000 + int(parts[2])
    return f"{month:02d}/{day:02d}/{year}"


# ═══════════════════════════════════════════════════════════════════════════════
# WEEK LINE PARSING
# ═══════════════════════════════════════════════════════════════════════════════

def _parse_week_data(text_after_wk_date: str) -> tuple:
    """
    Parse total spots and rate from the text following "wk of M/D/YY".

    Paid format:  "   1 1  $180.00  2  $ 3 60.00"
      → rate=180.0, spots=2  (first $ = rate, next integer = total)

    Thematic:     "   1 1       2"
      → rate=0.0, spots=2  (no $, last integer = total)

    Returns:
        (total_spots, rate_per_spot)
    """
    if '$' in text_after_wk_date:
        # Paid line: first $ = rate, first integer after rate = total spots
        rate_match = re.search(r'\$\s*([\d,]+\.?\d*)', text_after_wk_date)
        if rate_match:
            rate = float(rate_match.group(1).replace(',', ''))
            after_rate = text_after_wk_date[rate_match.end():]
            total_match = re.search(r'(\d+)', after_rate)
            total_spots = int(total_match.group(1)) if total_match else 0
            return total_spots, rate

    # Thematic / free: last standalone integer = total spots
    total_match = re.search(r'(\d+)\s*$', text_after_wk_date.strip())
    return (int(total_match.group(1)), 0.0) if total_match else (0, 0.0)


# ═══════════════════════════════════════════════════════════════════════════════
# HEADER PARSING
# ═══════════════════════════════════════════════════════════════════════════════

_COLUMN_HEADER_KEYWORDS = [
    "PROGRAM / TIME", "M T W T F S S", "Per Spot", "Gross Rate", "FLIGHT DATES",
]


def _parse_header(raw_lines: list) -> tuple:
    """
    Parse the header block (above DAY-PART SCHEDULE).

    Returns:
        (advertiser, station, from_name, order_date, ad_titles, duration_seconds, schedule_start_idx)
    """
    advertiser = ""
    station = ""
    station_captured = False
    from_name = ""
    order_date = ""
    ad_titles: list = []
    duration_seconds = 30
    in_ad_title = False
    schedule_start_idx = 0

    for i, line in enumerate(raw_lines):
        stripped = line.strip()

        if "BROADCAST ORDER" in stripped:
            in_ad_title = False
            continue

        if stripped.startswith("TO:"):
            date_match = re.search(r'DATE:\s*(\d{1,2}/\d{1,2}/\d{4})', stripped)
            if date_match:
                order_date = date_match.group(1)
            in_ad_title = False
            continue

        if not station_captured and (
            "Crossing TV" in stripped or "KQTA" in stripped or "KBTV" in stripped
        ):
            station = stripped
            station_captured = True
            in_ad_title = False
            continue

        if stripped.startswith("FROM:"):
            from_name = re.sub(r'^FROM:\s*', '', stripped).strip()
            in_ad_title = False
            continue

        if stripped.startswith("ADVERTISER:"):
            advertiser = re.sub(r'^ADVERTISER:\s*', '', stripped).strip()
            in_ad_title = False
            continue

        if stripped.startswith("AD TITLE:"):
            title = re.sub(r'^AD TITLE:\s*', '', stripped).strip()
            if title:
                ad_titles.append(title)
            in_ad_title = True
            continue

        if in_ad_title and stripped.startswith('-'):
            ad_titles.append(stripped.lstrip('- ').strip())
            continue

        if stripped.startswith("LENGTH:"):
            dur_match = re.search(r'(\d+)\s*sec', stripped, re.IGNORECASE)
            if dur_match:
                duration_seconds = int(dur_match.group(1))
            in_ad_title = False
            continue

        if "DAY-PART SCHEDULE" in stripped:
            schedule_start_idx = i + 1
            break

        in_ad_title = False

    return advertiser, station, from_name, order_date, ad_titles, duration_seconds, schedule_start_idx


# ═══════════════════════════════════════════════════════════════════════════════
# SCHEDULE PARSING
# ═══════════════════════════════════════════════════════════════════════════════

def _parse_schedule(schedule_lines: list) -> list:
    """
    Parse the schedule section into TimeAdvertisingLine objects.

    State machine:
    - Section header → set current_section, in_thematic flag
    - M-[Day]: program line → flush previous, start new program
    - wk of line → append week to current program
    - FLIGHT TOTAL / GROSS TOTAL → stop

    Args:
        schedule_lines: Lines starting just after "DAY-PART SCHEDULE"

    Returns:
        List of TimeAdvertisingLine objects
    """
    parsed_lines: list = []

    current_section = ""
    in_thematic = False
    current_program: Optional[str] = None
    current_weeks: list = []

    def _flush():
        if current_program and current_weeks:
            parsed_lines.append(TimeAdvertisingLine(
                program=current_program,
                section=current_section,
                is_thematic=in_thematic,
                weeks=tuple(current_weeks),
            ))

    for line in schedule_lines:
        stripped = line.strip()

        # End of schedule
        if "FLIGHT TOTAL" in stripped or "GROSS TOTAL" in stripped:
            break

        # Skip blank lines and column headers
        if not stripped:
            continue
        if any(kw in stripped for kw in _COLUMN_HEADER_KEYWORDS):
            continue

        # ── Week line (may also start with program name) ──────────────────
        wk_match = re.search(r'wk of (\d{1,2}/\d{1,2}/\d{2})', stripped)
        if wk_match:
            week_date_str = wk_match.group(1)
            week_start = _parse_week_date(week_date_str)
            after_wk = stripped[wk_match.end():]
            spots, rate = _parse_week_data(after_wk)

            # Check if this line also carries the program name
            before_wk = stripped[:wk_match.start()].strip()
            if before_wk and re.match(r'^M-[A-Za-z]', before_wk):
                _flush()
                current_program = before_wk
                current_weeks = []

            current_weeks.append(TimeAdvertisingWeek(
                week_start=week_start,
                spots=spots,
                rate=rate,
            ))
            continue

        # ── Program line: "M-F: ...", "M-Sun: ...", etc. ─────────────────
        if re.match(r'^M-[A-Za-z]+:', stripped):
            _flush()
            current_program = stripped
            current_weeks = []
            continue

        # ── Section header ────────────────────────────────────────────────
        # Anything else that isn't blank, a column header, or a digit-leading line
        if stripped and not re.match(r'^\d', stripped):
            _flush()
            current_section = stripped
            in_thematic = "thematic" in stripped.lower() or "free" in stripped.lower()
            current_program = None
            current_weeks = []

    # Flush final program
    _flush()
    return parsed_lines


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN PARSER
# ═══════════════════════════════════════════════════════════════════════════════

def parse_timeadvertising_pdf(pdf_path: str) -> Optional[TimeAdvertisingOrder]:
    """
    Parse a Time Advertising broadcast order PDF.

    Args:
        pdf_path: Path to the PDF file

    Returns:
        TimeAdvertisingOrder, or None if the PDF cannot be parsed
    """
    try:
        with pdfplumber.open(pdf_path) as pdf:
            text = pdf.pages[0].extract_text() or ""
    except Exception as e:
        print(f"[TIMEADVERTISING] Error opening PDF: {e}")
        return None

    if not text.strip():
        return None

    return _parse_text(text, pdf_path)


def _parse_text(text: str, pdf_path: str = "") -> Optional[TimeAdvertisingOrder]:
    """Parse raw extracted text into a TimeAdvertisingOrder."""
    raw_lines = text.split('\n')

    # ── Agency from BILL TO (scan full text) ──────────────────────────────
    agency = "Time Advertising, Inc."
    bill_match = re.search(r'BILL TO:\s*(.+)', text)
    if bill_match:
        agency = bill_match.group(1).strip()

    # ── Parse header ──────────────────────────────────────────────────────
    (
        advertiser, station, from_name, order_date,
        ad_titles, duration_seconds, schedule_start_idx
    ) = _parse_header(raw_lines)

    market = _detect_market(station)

    # ── Parse schedule ────────────────────────────────────────────────────
    schedule_lines = raw_lines[schedule_start_idx:]
    lines = _parse_schedule(schedule_lines)

    return TimeAdvertisingOrder(
        advertiser=advertiser,
        station=station,
        market=market,
        ad_titles=ad_titles,
        duration_seconds=duration_seconds,
        from_name=from_name,
        order_date=order_date,
        agency=agency,
        lines=lines,
        pdf_path=pdf_path,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# TESTING / STANDALONE
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python timeadvertising_parser.py <pdf_path>")
        sys.exit(1)

    pdf_path = sys.argv[1]
    order = parse_timeadvertising_pdf(pdf_path)

    if not order:
        print("Failed to parse order")
        sys.exit(1)

    print(f"\n{'='*70}")
    print("TIME ADVERTISING ORDER")
    print(f"{'='*70}")
    print(f"  Advertiser:  {order.advertiser}")
    print(f"  Station:     {order.station}")
    print(f"  Market:      {order.market}")
    print(f"  Duration:    :{order.duration_seconds}s")
    print(f"  Date:        {order.order_date}")
    print(f"  From:        {order.from_name}")
    print(f"  Agency:      {order.agency}")
    print(f"  Flight:      {order.flight_start} → {order.flight_end}")
    print(f"  Ad Titles:")
    for t in order.ad_titles:
        print(f"    • {t}")
    print(f"  Lines:       {len(order.lines)} "
          f"({len(order.paid_lines)} paid, {len(order.thematic_lines)} thematic)")

    for j, ln in enumerate(order.lines):
        spot_type = "THEMATIC" if ln.is_thematic else "PAID"
        print(f"\n  [{j+1}] {spot_type}  {ln.program}")
        print(f"        Section: {ln.section}")
        print(f"        Rate: ${ln.rate:.2f}/spot")
        for w in ln.weeks:
            print(f"        {w.week_start}: {w.spots} spots")
        print(f"        Total: {ln.total_spots} spots")
