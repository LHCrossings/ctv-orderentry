"""
Wallrich Order Parser
Parses Wallrich agency insertion order PDFs using Strata IO system.
Format: Client/Estimate/Description/Market header + "# of SPOTS PER WEEK" table.
Station is KBTV (Crossings TV Sacramento). One estimate per PDF.
"""

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import List, Optional

import pdfplumber

_DATE_RE = re.compile(r'\b(\d{1,2}/\d{1,2})\b')
_DAY_RE  = re.compile(
    r'^(M-Su|M-Sa|M-F|Sa-Su|MTuWThFSaSu|MTuWThF|MTuWTh|'
    r'TuWThF|WThF|MTu|TuW|ThF|MWF|MF|M|Tu|W|Th|F|Sa|Su)$',
    re.IGNORECASE,
)
_TIME_FULL_RE  = re.compile(r'^(\d+:\d+[ap])-(\d+:\d+[ap])(.*)$', re.IGNORECASE)
_TIME_START_RE = re.compile(r'^(\d+:\d+[ap])-?$',                  re.IGNORECASE)
_TIME_END_RE   = re.compile(r'^(\d+:\d+[ap])(.*)?$',               re.IGNORECASE)


@dataclass
class WallrichLine:
    days: str
    time: str          # "7:00p-8:00p"
    program: str       # "Cantonese", "Mandarin", etc.
    duration: int      # seconds (30 = :30)
    weekly_spots: List[int]
    total_spots: int
    rate: float

    @property
    def is_bonus(self) -> bool:
        return self.rate == 0.0


@dataclass
class WallrichEstimate:
    estimate_number: str
    description: str
    client: str
    market: str        # raw from PDF, e.g. "Sacramento"
    flight_start: str  # MM/DD/YYYY
    flight_end: str    # MM/DD/YYYY
    buyer: str
    separation: int    # minutes from "Separation between spots:" on page 2
    week_starts: List[str]   # ["5/4", "5/11", ...] — week Monday dates from header
    lines: List[WallrichLine]
    pdf_path: str = ""
    rates_are_net: bool = False


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def parse_wallrich_pdf(pdf_path: str) -> List[WallrichEstimate]:
    """
    Parse a Wallrich PDF and return a list with one WallrichEstimate.
    Returns an empty list if parsing fails.
    """
    all_text_pages = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            all_text_pages.append(page.extract_text() or "")

    if not all_text_pages:
        return []

    page1 = all_text_pages[0]
    all_text = "\n".join(all_text_pages)

    header = _parse_header(page1, all_text)
    if not header:
        return []

    week_starts = _extract_week_starts(page1)
    if not week_starts:
        print("[WALLRICH] ✗ Could not find week start dates")
        return []

    lines = _parse_data_lines(page1, len(week_starts))
    if not lines:
        print("[WALLRICH] ✗ No data lines found")
        return []

    estimate = WallrichEstimate(
        estimate_number=header["estimate"],
        description=header["description"],
        client=header["client"],
        market=header["market"],
        flight_start=header["flight_start"],
        flight_end=header["flight_end"],
        buyer=header["buyer"],
        separation=header["separation"],
        week_starts=week_starts,
        lines=lines,
        pdf_path=pdf_path,
    )
    return [estimate]


# ---------------------------------------------------------------------------
# Header parsing
# ---------------------------------------------------------------------------

def _parse_header(page1: str, all_text: str) -> Optional[dict]:
    """Extract estimate header fields from page 1 (+ separation from page 2)."""
    h = {}

    m = re.search(r'Estimate:\s*(\d+)', page1)
    if not m:
        return None
    h["estimate"] = m.group(1)

    m = re.search(r'Client:\s*(\S+)', page1)
    h["client"] = m.group(1).strip() if m else "Unknown"

    m = re.search(r'Description:\s*(.+?)(?:\n|Product:|Market:)', page1)
    h["description"] = m.group(1).strip() if m else ""

    m = re.search(r'Market:\s*([^\n]+?)(?:\s+Buyer:|\n)', page1)
    h["market"] = m.group(1).strip() if m else "Unknown"

    m = re.search(r'Buyer:\s*([^\n]+)', page1)
    h["buyer"] = m.group(1).strip() if m else "Unknown"

    # Flight Date: 4/20/2026-8/16/2026
    m = re.search(r'Flight Date:\s*(\d{1,2}/\d{1,2}/\d{4})-(\d{1,2}/\d{1,2}/\d{4})', page1)
    if m:
        h["flight_start"] = m.group(1)
        h["flight_end"]   = m.group(2)
    else:
        # Try Flight Start/End Date separately (page 2 style)
        ms = re.search(r'Flight Start Date:\s*(\d{1,2}/\d{1,2}/\d{4})', all_text)
        me = re.search(r'Flight End Date:\s*(\d{1,2}/\d{1,2}/\d{4})',   all_text)
        h["flight_start"] = ms.group(1) if ms else "Unknown"
        h["flight_end"]   = me.group(1) if me else "Unknown"

    # Separation between spots: 30  (may be on page 2)
    m = re.search(r'Separation between spots:\s*(\d+)', all_text)
    h["separation"] = int(m.group(1)) if m else 15

    return h


# ---------------------------------------------------------------------------
# Week date extraction
# ---------------------------------------------------------------------------

def _extract_week_starts(page1: str) -> List[str]:
    """
    Find the line immediately after '# of SPOTS PER WEEK' and extract
    all M/D date tokens from it (those are the week Monday start dates).
    """
    lines = page1.split("\n")
    for i, line in enumerate(lines):
        if "# of SPOTS PER WEEK" in line:
            for j in range(i + 1, min(i + 4, len(lines))):
                candidate = lines[j].strip()
                dates = _DATE_RE.findall(candidate)
                if len(dates) >= 2:
                    # Drop last token if it looks like a day (e.g. Total column)
                    # Keep only M/D patterns (1-2 digit month, 1-2 digit day)
                    return dates
    return []


# ---------------------------------------------------------------------------
# Data line parsing
# ---------------------------------------------------------------------------

_SKIP_PREFIXES = re.compile(
    r'^\(?CROSSINGS\)?-?TV|^Station Total:|^Spots Per Week|'
    r'^Cost Per Week|^SCHEDULE TOTALS|^TOTAL|^Agreed|^Page:',
    re.IGNORECASE,
)


def _parse_data_lines(page1: str, n_weeks: int) -> List[WallrichLine]:
    """Parse all data lines from page 1."""
    lines_text = page1.split("\n")
    results: List[WallrichLine] = []

    in_table = False
    for raw in lines_text:
        stripped = raw.strip()

        if "# of SPOTS PER WEEK" in stripped:
            in_table = True
            continue

        if not in_table:
            continue

        # Skip column-header rows and totals/footer rows
        if _SKIP_PREFIXES.search(stripped):
            continue
        if stripped.startswith("Station Day") or stripped.startswith("5/") or stripped.startswith("11/"):
            continue

        parsed = _parse_data_line(stripped, n_weeks)
        if parsed:
            results.append(parsed)

    return results


def _parse_data_line(line: str, n_weeks: int) -> Optional[WallrichLine]:
    """
    Parse one data line into a WallrichLine.

    Examples:
      "KBTV M-F 7:00p- 8:00p Cantonese 30 3 3 3 0 0 3 3 3 3 3 3 27 $50.00"
      "M-F 8:00p-11:00p Mandarin 30 7 7 7 0 0 7 7 7 7 7 7 63 $50.00"
      "M-F 11:00a-12:30pVietnamese 30 4 4 4 0 0 4 4 4 4 4 4 36 $50.00"
      "Sa-Su 6:00p- 8:00p Hmong 30 4 4 4 0 0 4 4 4 4 4 4 36 $50.00"
    """
    if not line:
        return None

    parts = line.split()
    if len(parts) < 5:
        return None

    idx = 0

    # Skip optional station prefix (e.g. "KBTV")
    if not _DAY_RE.match(parts[idx]):
        idx += 1
        if idx >= len(parts) or not _DAY_RE.match(parts[idx]):
            return None

    days = parts[idx]
    idx += 1

    if idx >= len(parts):
        return None

    # --- Time extraction ---
    time_start: Optional[str] = None
    time_end: Optional[str] = None
    program_fragment: Optional[str] = None   # when program is merged onto time_end

    t1 = parts[idx]

    # Case 1: "11:00a-12:30pVietnamese" or "8:00p-11:00p"
    m_full = _TIME_FULL_RE.match(t1)
    if m_full:
        time_start = m_full.group(1)
        time_end   = m_full.group(2)
        leftover   = m_full.group(3).strip()
        if leftover:
            program_fragment = leftover
        idx += 1
    else:
        # Case 2: "7:00p-" followed by "8:00p"
        m_start = _TIME_START_RE.match(t1)
        if not m_start:
            return None
        time_start = m_start.group(1)
        idx += 1
        if idx >= len(parts):
            return None
        t2 = parts[idx]
        m_end = _TIME_END_RE.match(t2)
        if not m_end:
            return None
        time_end = m_end.group(1)
        leftover = (m_end.group(2) or "").strip()
        if leftover:
            program_fragment = leftover
        idx += 1

    # --- Program name ---
    if program_fragment:
        program = program_fragment
    else:
        if idx >= len(parts):
            return None
        program = parts[idx]
        idx += 1

    # --- Duration ---
    if idx >= len(parts):
        return None
    try:
        duration = int(parts[idx])
        idx += 1
    except ValueError:
        return None

    # --- Numbers: weekly_spots... total_spots $rate ---
    remaining = parts[idx:]
    if not remaining:
        return None

    # Last token is rate
    if not remaining[-1].startswith('$'):
        return None
    rate = float(remaining[-1][1:].replace(',', ''))
    remaining = remaining[:-1]

    # Second-to-last is total_spots
    if not remaining:
        return None
    try:
        total_spots = int(remaining[-1])
        weekly_spots = [int(x) for x in remaining[:-1]]
    except ValueError:
        return None

    # Sanity: weekly count should match n_weeks (±1 for OCR noise)
    if abs(len(weekly_spots) - n_weeks) > 2:
        return None

    return WallrichLine(
        days=days,
        time=f"{time_start}-{time_end}",
        program=program,
        duration=duration,
        weekly_spots=weekly_spots,
        total_spots=total_spots,
        rate=rate,
    )


# ---------------------------------------------------------------------------
# Week consolidation (used by automation)
# ---------------------------------------------------------------------------

def consolidate_wallrich_weeks(
    weekly_spots: List[int],
    week_starts: List[str],
    flight_end: str,
    flight_year: int,
) -> List[dict]:
    """
    Consolidate weekly spot counts into contiguous date ranges for Etere.

    Splits on:
    - 0-spot weeks (gaps)
    - Different spot count (e.g., [3,3,5,5] → two ranges)

    Args:
        weekly_spots:   Per-week spot counts from the PDF
        week_starts:    Week Monday dates as "M/D" strings from the PDF header
        flight_end:     Overall flight end "MM/DD/YYYY" (used to cap last range)
        flight_year:    Calendar year (to expand "M/D" → "M/D/YYYY")

    Returns:
        List of dicts: {start_date, end_date, spots_per_week, num_weeks, total_spots}
    """
    flight_end_dt = datetime.strptime(flight_end, "%m/%d/%Y")

    def to_dt(md: str) -> datetime:
        return datetime.strptime(f"{md}/{flight_year}", "%m/%d/%Y")

    ranges: List[dict] = []
    cur_start: Optional[datetime] = None
    cur_spots: Optional[int] = None
    cur_weeks: int = 0

    for i, (spots, ws) in enumerate(zip(weekly_spots, week_starts)):
        week_dt = to_dt(ws)

        if spots > 0:
            if cur_start is None:
                cur_start = week_dt
                cur_spots = spots
                cur_weeks = 1
            elif spots == cur_spots:
                cur_weeks += 1
            else:
                # Spot count changed — close and start new range
                next_week = to_dt(week_starts[i])   # = week_dt (current)
                end_dt = min(next_week - timedelta(days=1), flight_end_dt)
                ranges.append({
                    "start_date":    cur_start.strftime("%m/%d/%Y"),
                    "end_date":      end_dt.strftime("%m/%d/%Y"),
                    "spots_per_week": cur_spots,
                    "num_weeks":     cur_weeks,
                    "total_spots":   cur_spots * cur_weeks,
                })
                cur_start = week_dt
                cur_spots = spots
                cur_weeks = 1
        else:
            if cur_start is not None:
                # Gap — close current range; end = day before this zero-week's Monday
                end_dt = min(week_dt - timedelta(days=1), flight_end_dt)
                ranges.append({
                    "start_date":    cur_start.strftime("%m/%d/%Y"),
                    "end_date":      end_dt.strftime("%m/%d/%Y"),
                    "spots_per_week": cur_spots,
                    "num_weeks":     cur_weeks,
                    "total_spots":   cur_spots * cur_weeks,
                })
                cur_start = None
                cur_spots = None
                cur_weeks = 0

    # Close last open range
    if cur_start is not None:
        ranges.append({
            "start_date":    cur_start.strftime("%m/%d/%Y"),
            "end_date":      flight_end,
            "spots_per_week": cur_spots,
            "num_weeks":     cur_weeks,
            "total_spots":   cur_spots * cur_weeks,
        })

    return ranges


# ---------------------------------------------------------------------------
# CLI test harness
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    pdf = sys.argv[1] if len(sys.argv) > 1 else None
    if not pdf:
        print("Usage: python wallrich_parser.py <path-to-pdf>")
        sys.exit(1)

    estimates = parse_wallrich_pdf(pdf)
    if not estimates:
        print("No estimates parsed.")
        sys.exit(1)

    est = estimates[0]
    print(f"\nEstimate:   {est.estimate_number}")
    print(f"Client:     {est.client}")
    print(f"Market:     {est.market}")
    print(f"Flight:     {est.flight_start} – {est.flight_end}")
    print(f"Buyer:      {est.buyer}")
    print(f"Separation: {est.separation} min")
    print(f"Weeks:      {est.week_starts}")
    print(f"Lines ({len(est.lines)}):")
    for ln in est.lines:
        bonus = " [BONUS]" if ln.is_bonus else ""
        print(f"  {ln.days} {ln.time} {ln.program} :{ln.duration}  "
              f"{ln.weekly_spots} → {ln.total_spots} spots  ${ln.rate:.2f}{bonus}")
