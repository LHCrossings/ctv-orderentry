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
    - Day columns: individual "1" under M T W T F S S — parsed using
      x-coordinates so exact days are captured (like Admerasia)
    - Day codes: M=Mon, T=Tue, W=Wed, R=Thu, F=Fri, S=Sat, U=Sun
    - Dollar amounts may have PDF rendering spaces: "$ 3 60.00" = "$360.00"
    - Week dates: "wk of 3/2/26" → "03/02/2026"
    - Sacramento variant: program line may appear without week on same line
    - Spots must air on the exact days marked — no weekly cap (like Admerasia)

═══════════════════════════════════════════════════════════════════════════════
"""

import re
import pdfplumber
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional


# Day code order for display and sorting (matches Admerasia convention)
# M=Mon, T=Tue, W=Wed, R=Thu, F=Fri, S=Sat, U=Sun
DAY_ORDER = "MTWRFSU"

# Maps position index in "M T W T F S S" header → day code
# The two T's are disambiguated by x-position (first=Tue, second=Thu)
_COL_INDEX_TO_CODE = ["M", "T", "W", "R", "F", "S", "U"]


# ═══════════════════════════════════════════════════════════════════════════════
# DATA STRUCTURES
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class TimeAdvertisingWeek:
    """
    A single week's spot data for one program line, with day-level resolution.

    day_spots maps day code → spot count on that day.
    Example: {"R": 1, "F": 1} = 1 spot on Thursday + 1 spot on Friday.
    """
    week_start: str        # "03/02/2026" (MM/DD/YYYY, always Monday)
    day_spots: dict        # {"R": 1, "F": 1} etc.
    rate: float            # per-spot gross rate (0.0 for thematic/free)

    @property
    def total_spots(self) -> int:
        return sum(self.day_spots.values())

    @property
    def days_str(self) -> str:
        """Sorted day string for pattern comparison, e.g. 'RF' or 'WR'."""
        return ''.join(d for d in DAY_ORDER if d in self.day_spots)


@dataclass(frozen=True)
class TimeAdvertisingLine:
    """A program/daypart line with its weekly spot schedule."""
    program: str                # "M-F: Cant. News/Talk 7pm-8pm"
    section: str                # "Prime Time Dream Drive..." or "Thematic..."
    is_thematic: bool           # True = free/bonus spots, False = paid
    weeks: tuple                # tuple[TimeAdvertisingWeek, ...]

    @property
    def total_spots(self) -> int:
        return sum(w.total_spots for w in self.weeks)

    @property
    def weekly_spots(self) -> list:
        """Total spots per week (for compatibility with consolidate_weeks)."""
        return [w.total_spots for w in self.weeks]

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

    def get_etere_lines(self) -> list:
        """
        Convert weekly day-spot data to Etere line specifications.

        Groups consecutive weeks with identical day patterns into single blocks.
        Weeks with different day patterns become separate Etere lines.

        Etere entry style (like Admerasia):
          - spots_per_week = 0   (no weekly cap — exact scheduling)
          - max_daily_run  = 1   (1 spot per day per line)
          - total_spots    = total across block date range
          - days           = day code string e.g. "RF", "T", "WR"

        Returns:
            List of dicts: start_date, end_date, days, total_spots,
                           per_day_max, spots_per_week, rate
        """
        etere_lines = []
        n = len(self.weeks)
        i = 0

        while i < n:
            wk = self.weeks[i]
            if wk.total_spots == 0:
                i += 1
                continue

            pattern = wk.days_str
            block_total = wk.total_spots
            block_week_start = datetime.strptime(wk.week_start, '%m/%d/%Y')
            last_week_start = block_week_start

            # Extend block while consecutive weeks have the same day pattern
            j = i + 1
            while j < n:
                next_wk = self.weeks[j]
                next_start = datetime.strptime(next_wk.week_start, '%m/%d/%Y')
                gap = (next_start - datetime.strptime(
                    self.weeks[j - 1].week_start, '%m/%d/%Y'
                )).days

                if next_wk.days_str != pattern or gap != 7:
                    break

                block_total += next_wk.total_spots
                last_week_start = next_start
                j += 1

            # Block end = Saturday of last week in block
            block_end = last_week_start + timedelta(days=6)

            etere_lines.append({
                'days': pattern,
                'start_date': block_week_start.strftime('%m/%d/%Y'),
                'end_date': block_end.strftime('%m/%d/%Y'),
                'total_spots': block_total,
                'per_day_max': 1,
                'spots_per_week': 0,
                'rate': self.rate,
            })

            i = j

        return etere_lines


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
        return [ln for ln in self.lines if not ln.is_thematic]

    @property
    def thematic_lines(self) -> list:
        return [ln for ln in self.lines if ln.is_thematic]

    @property
    def flight_start(self) -> str:
        dates = [w.week_start for ln in self.paid_lines for w in ln.weeks]
        return min(dates) if dates else ""

    @property
    def flight_end(self) -> str:
        """Saturday of the last paid week."""
        dates = [w.week_start for ln in self.paid_lines for w in ln.weeks]
        if not dates:
            return ""
        last_wk = datetime.strptime(max(dates), '%m/%d/%Y')
        return (last_wk + timedelta(days=6)).strftime('%m/%d/%Y')


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
    """Parse "M/D/YY" to "MM/DD/YYYY"."""
    parts = date_str.strip().split('/')
    if len(parts) != 3:
        return date_str
    month, day = int(parts[0]), int(parts[1])
    year = 2000 + int(parts[2])
    return f"{month:02d}/{day:02d}/{year}"


# ═══════════════════════════════════════════════════════════════════════════════
# COORDINATE-BASED DAY COLUMN DETECTION
# ═══════════════════════════════════════════════════════════════════════════════

def _group_words_by_row(words: list, y_tolerance: float = 4.0) -> dict:
    """
    Group pdfplumber words by visual row using y-coordinate proximity.

    Returns:
        Dict of representative_y → [words in that row], sorted by y.
    """
    rows: dict = {}
    for word in words:
        y = word['top']
        matched_y = None
        for ey in rows:
            if abs(ey - y) <= y_tolerance:
                matched_y = ey
                break
        if matched_y is None:
            matched_y = y
        rows.setdefault(matched_y, []).append(word)
    return rows


def _find_day_columns(words: list) -> dict:
    """
    Find x-positions of the M T W T F S S day columns from the header row.

    Looks for a row containing at least 5 single-letter words from {M,T,W,F,S}
    clustered within ~80px (the day column grid). Maps column positions to
    day codes M,T,W,R,F,S,U (R=Thursday to avoid T ambiguity).

    Returns:
        Dict day_code → x_position, or {} if not found.
    """
    rows = _group_words_by_row(words)

    for _y, row_words in sorted(rows.items()):
        candidates = [
            w for w in row_words
            if len(w['text']) == 1 and w['text'] in 'MTWFS'
        ]
        if len(candidates) < 5:
            continue

        candidates_sorted = sorted(candidates, key=lambda w: w['x0'])
        xs = [w['x0'] for w in candidates_sorted]

        # Must span <80px (day column grid width) and have M and F in there
        texts = [w['text'] for w in candidates_sorted]
        if 'M' not in texts or 'F' not in texts:
            continue
        if xs[-1] - xs[0] > 100:
            continue

        # Map positions → day codes
        result = {}
        for i, w in enumerate(candidates_sorted[:7]):
            if i < len(_COL_INDEX_TO_CODE):
                result[_COL_INDEX_TO_CODE[i]] = w['x0']
        return result

    return {}


def _get_day_spots_from_row(row_words: list, day_col_x: dict, x_tol: float = 8.0) -> dict:
    """
    Extract day-spot markers from a week row using x-coordinate alignment.

    Counts digits ("1", "2", etc.) whose x-position aligns with a day column.
    Digits outside the day column x-range (total spots, cost) are ignored.

    Args:
        row_words: Words in the week row (from extract_words)
        day_col_x: Dict day_code → x_position (from _find_day_columns)
        x_tol: Tolerance in points for matching a word to a column

    Returns:
        Dict day_code → spot_count (e.g. {"R": 1, "F": 1})
    """
    if not day_col_x:
        return {}

    min_x = min(day_col_x.values()) - x_tol
    max_x = max(day_col_x.values()) + x_tol

    day_spots: dict = {}
    for word in row_words:
        if not re.match(r'^\d+$', word['text']):
            continue
        wx = word['x0']
        if not (min_x <= wx <= max_x):
            continue
        closest = min(day_col_x, key=lambda d: abs(day_col_x[d] - wx))
        day_spots[closest] = day_spots.get(closest, 0) + int(word['text'])

    return day_spots


# ═══════════════════════════════════════════════════════════════════════════════
# HEADER PARSING (text-based)
# ═══════════════════════════════════════════════════════════════════════════════

_COLUMN_HEADER_KEYWORDS = [
    "PROGRAM / TIME", "M T W T F S S", "Per Spot", "Gross Rate", "FLIGHT DATES",
]


def _parse_header(raw_lines: list) -> tuple:
    """
    Parse the header block (above DAY-PART SCHEDULE).

    Returns:
        (advertiser, station, from_name, order_date, ad_titles,
         duration_seconds, schedule_start_idx)
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
            m = re.search(r'DATE:\s*(\d{1,2}/\d{1,2}/\d{4})', stripped)
            if m:
                order_date = m.group(1)
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
            m = re.search(r'(\d+)\s*sec', stripped, re.IGNORECASE)
            if m:
                duration_seconds = int(m.group(1))
            in_ad_title = False
            continue

        if "DAY-PART SCHEDULE" in stripped:
            schedule_start_idx = i + 1
            break

        in_ad_title = False

    return (
        advertiser, station, from_name, order_date,
        ad_titles, duration_seconds, schedule_start_idx,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# SCHEDULE PARSING (coordinate-aware)
# ═══════════════════════════════════════════════════════════════════════════════

def _parse_schedule(schedule_lines: list, word_rows: dict, day_col_x: dict) -> list:
    """
    Parse the schedule section into TimeAdvertisingLine objects.

    Uses text content for structure detection (section headers, program lines,
    week lines) and x-coordinates for day-level spot parsing.

    Args:
        schedule_lines: Text lines starting just after "DAY-PART SCHEDULE"
        word_rows: Dict of y → [words] covering the full page
        day_col_x: Dict day_code → x_position (from _find_day_columns)

    Returns:
        List of TimeAdvertisingLine objects
    """
    parsed_lines: list = []
    current_section = ""
    in_thematic = False
    current_program: Optional[str] = None
    current_weeks: list = []

    # Build a lookup: week date string → [day_spots dict per occurrence]
    # Key is the y-coordinate of the row so duplicate dates (same wk in different
    # dayparts) are distinguished.
    y_to_day_spots: dict = {}
    for y, rw in word_rows.items():
        texts = [w['text'] for w in rw]
        if 'wk' not in texts:
            continue
        y_to_day_spots[y] = _get_day_spots_from_row(rw, day_col_x)

    # We also need to link text lines → y coordinates for week rows.
    # Strategy: for each text line containing "wk of M/D/YY", find the
    # matching y row by looking for a word_row that contains that date string.
    # We consume y-keys in order so duplicate dates map correctly.
    wk_date_y_queue: dict = {}   # date_str → [sorted y values]
    for y, rw in sorted(word_rows.items()):
        texts = [w['text'] for w in rw]
        if 'wk' not in texts:
            continue
        row_text = ' '.join(w['text'] for w in sorted(rw, key=lambda w: w['x0']))
        m = re.search(r'wk of (\d{1,2}/\d{1,2}/\d{2})', row_text)
        if m:
            ds = m.group(1)
            wk_date_y_queue.setdefault(ds, []).append(y)

    # Usage counters for consuming the queue
    wk_date_used: dict = {}

    def _pop_day_spots(date_str: str) -> dict:
        """Get the next unused y-row's day_spots for this week date string."""
        ys = wk_date_y_queue.get(date_str, [])
        idx = wk_date_used.get(date_str, 0)
        if idx < len(ys):
            wk_date_used[date_str] = idx + 1
            return y_to_day_spots.get(ys[idx], {})
        return {}

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

        if "FLIGHT TOTAL" in stripped or "GROSS TOTAL" in stripped:
            break

        if not stripped:
            continue
        if any(kw in stripped for kw in _COLUMN_HEADER_KEYWORDS):
            continue

        # ── Week line ─────────────────────────────────────────────────────
        wk_match = re.search(r'wk of (\d{1,2}/\d{1,2}/\d{2})', stripped)
        if wk_match:
            date_str = wk_match.group(1)
            week_start = _parse_week_date(date_str)

            # Rate from first $ in line
            rate = 0.0
            rate_m = re.search(r'\$\s*([\d,]+\.?\d*)', stripped)
            if rate_m:
                rate = float(rate_m.group(1).replace(',', ''))

            # Day spots from coordinates
            day_spots = _pop_day_spots(date_str)

            # Fallback if no coordinate data: infer from total spots count
            if not day_spots:
                # Try text-based total as a last resort
                after_wk = stripped[wk_match.end():]
                if '$' in after_wk:
                    after_rate = after_wk[after_wk.index('$') + 1:]
                    after_rate = re.sub(r'[\d,]+\.?\d*', '', after_rate, count=1)
                    tm = re.search(r'(\d+)', after_rate)
                    total = int(tm.group(1)) if tm else 0
                else:
                    tm = re.search(r'(\d+)\s*$', after_wk.strip())
                    total = int(tm.group(1)) if tm else 0
                if total:
                    day_spots = {'?': total}

            # Check for program name before "wk" on same line
            before_wk = stripped[:wk_match.start()].strip()
            if before_wk and re.match(r'^M-[A-Za-z]', before_wk):
                _flush()
                current_program = before_wk
                current_weeks = []

            current_weeks.append(TimeAdvertisingWeek(
                week_start=week_start,
                day_spots=day_spots,
                rate=rate,
            ))
            continue

        # ── Program line ─────────────────────────────────────────────────
        if re.match(r'^M-[A-Za-z]+:', stripped):
            _flush()
            current_program = stripped
            current_weeks = []
            continue

        # ── Section header ───────────────────────────────────────────────
        if stripped and not re.match(r'^\d', stripped):
            _flush()
            current_section = stripped
            in_thematic = "thematic" in stripped.lower() or "free" in stripped.lower()
            current_program = None
            current_weeks = []

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
        TimeAdvertisingOrder, or None if parsing fails
    """
    try:
        with pdfplumber.open(pdf_path) as pdf:
            page = pdf.pages[0]
            text = page.extract_text() or ""
            words = page.extract_words(x_tolerance=3, y_tolerance=3)
    except Exception as e:
        print(f"[TIMEADVERTISING] Error opening PDF: {e}")
        return None

    if not text.strip():
        return None

    raw_lines = text.split('\n')

    # Agency from BILL TO (scan full text)
    agency = "Time Advertising, Inc."
    bill_m = re.search(r'BILL TO:\s*(.+)', text)
    if bill_m:
        agency = bill_m.group(1).strip()

    # Parse header (text-based)
    (
        advertiser, station, from_name, order_date,
        ad_titles, duration_seconds, schedule_start_idx,
    ) = _parse_header(raw_lines)

    market = _detect_market(station)

    # Find day column x-positions
    day_col_x = _find_day_columns(words)
    if not day_col_x:
        print("[TIMEADVERTISING] ⚠ Could not find day column headers — day-level data may be missing")

    # Group words by y-row for coordinate-based schedule parsing
    word_rows = _group_words_by_row(words)

    # Parse schedule
    schedule_lines = raw_lines[schedule_start_idx:]
    lines = _parse_schedule(schedule_lines, word_rows, day_col_x)

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
    print(f"  Lines:  {len(order.lines)} ({len(order.paid_lines)} paid, {len(order.thematic_lines)} thematic)")

    for j, ln in enumerate(order.lines):
        kind = "THEMATIC" if ln.is_thematic else "PAID"
        print(f"\n  [{j+1}] {kind}  {ln.program}")
        print(f"        Section: {ln.section}")
        print(f"        Rate: ${ln.rate:.2f}/spot")
        for w in ln.weeks:
            print(f"        {w.week_start}: {w.days_str:6s}  {w.total_spots} spot(s)  {w.day_spots}")
        print(f"        Total: {ln.total_spots} spots")

    print(f"\n{'='*70}")
    print("ETERE LINES (as would be entered)")
    print(f"{'='*70}")
    for j, ln in enumerate(order.lines):
        kind = "THEMATIC" if ln.is_thematic else "PAID"
        etere = ln.get_etere_lines()
        print(f"\n  [{j+1}] {kind}  {ln.program}")
        for k, spec in enumerate(etere, 1):
            rate_str = f"${spec['rate']:.2f}" if spec['rate'] > 0 else "$0.00 (free)"
            print(f"    Line {k}: days={spec['days']:6s}  "
                  f"{spec['start_date']} – {spec['end_date']}  "
                  f"{spec['total_spots']} spots  "
                  f"{rate_str}")
