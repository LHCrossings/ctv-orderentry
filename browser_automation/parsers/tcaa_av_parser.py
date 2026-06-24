"""
Toyota AAPI Added Value order parser.
Parses flight schedule PDFs like "Toyota AAPI Month Flight Schedule_2026.pdf".

Format:
  Header lines with title, flight dates, week column headers.
  15 data rows: {description} {days} {time} {w1} {w2} {w3} {w4} {w5} {total}
  Last 5 rows are ROS — days/time embedded in parentheses: "Language (days time)"
"""
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Tuple

import pdfplumber

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class ToyotaAVLine:
    description: str        # program description used as Etere line description
    language: str           # e.g. "Chinese", "South Asian"
    days: str               # normalized day pattern, e.g. "M-Su", "Sa-Su", "M-F"
    time: str               # time range string, e.g. "7p-9p", "4p-5p; 6p-7p"
    weekly_spots: List[int] # per-week spot counts (5 values)
    total_spots: int
    is_bonus: bool = False


@dataclass
class ToyotaAVOrder:
    title: str              # e.g. "May AAPI Heritage Month Sponsorship"
    flight_start: str       # MM/DD/YYYY
    flight_end: str         # MM/DD/YYYY
    week_dates: List[str]   # ["Apr 27", "May 4", "May 11", "May 18", "May 25"]
    lines: List[ToyotaAVLine] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

NUM_WEEK_COLS = 5

# x-coordinate boundary: left of this = description text, right = data numbers
DESC_X_THRESHOLD = 340.0

MONTH_ABBR = {
    'Jan': 1, 'Feb': 2, 'Mar': 3, 'Apr': 4,  'May': 5,  'Jun': 6,
    'Jul': 7, 'Aug': 8, 'Sep': 9, 'Oct': 10, 'Nov': 11, 'Dec': 12,
}

# Checked longest-first so "South Asian" matches before "South"
LANGUAGES = ["South Asian", "Chinese", "Mandarin", "Filipino", "Vietnamese", "Korean"]

DAY_NORMALIZATIONS = [
    (re.compile(r'^M-?Sun$', re.I),      'M-Su'),
    (re.compile(r'^M-?Su$',  re.I),      'M-Su'),
    (re.compile(r'^Sat\s*-\s*Sun$', re.I), 'Sa-Su'),
    (re.compile(r'^Sa-?Su$', re.I),      'Sa-Su'),
    (re.compile(r'^M-?F$',   re.I),      'M-F'),
    (re.compile(r'^M-?Sa$',  re.I),      'M-Sa'),
]

# Single time token: 7p, 12a, 11a, 1p, etc.
_T = r'\d+[ap](?:\d+)?'
# A single time range: 7p-9p
_TR = rf'{_T}-{_T}'
# Multiple ranges separated by comma or semicolon
_MULTI_TR = rf'{_TR}(?:\s*[,;]\s*{_TR})*'
TIME_TAIL_RE = re.compile(rf'\s+({_MULTI_TR},?)\s*$', re.IGNORECASE)

# Day patterns to detect at the end of a string (checked longest first)
_DAY_PATTERNS = [
    r'Sat\s*-\s*Sun', r'Sa-Su', r'M-Sun', r'M-Su', r'M-Sa', r'M-F',
    r'Sat', r'Sun', r'Sa', r'Su',
]
DAY_TAIL_RE = re.compile(
    r'\s+(' + '|'.join(_DAY_PATTERNS) + r')\s*$',
    re.IGNORECASE,
)

# ROS line: "Language (days time)" — parens may be missing closing one
ROS_RE = re.compile(r'^(.+?)\s*\((.+?)\)?\s*$')
ROS_INNER_RE = re.compile(
    r'^(' + '|'.join(_DAY_PATTERNS) + r')\s+(.+)$',
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _normalize_days(s: str) -> str:
    s = s.strip()
    for pattern, replacement in DAY_NORMALIZATIONS:
        if pattern.match(s):
            return replacement
    return s


def _normalize_time(s: str) -> str:
    t = s.strip().rstrip(',').strip()
    t = re.sub(r'\s*,\s*', '; ', t)
    return t


def _extract_language(text: str) -> str:
    for lang in LANGUAGES:
        if text.strip().startswith(lang):
            return lang
    return text.strip().split()[0] if text.strip() else "Unknown"


def _parse_week_header(line: str) -> Optional[List[str]]:
    """Parse '27-Apr 4-May 11-May 18-May 25-May Total' → ['Apr 27', 'May 4', ...]."""
    dates = []
    for tok in line.split():
        m = re.match(r'^(\d+)-([A-Za-z]{3})$', tok)
        if m:
            day, mon = m.group(1), m.group(2).capitalize()
            if mon in MONTH_ABBR:
                dates.append(f"{mon} {day}")
    return dates if len(dates) == NUM_WEEK_COLS else None


def _parse_flight_dates(line: str, year: int) -> Optional[Tuple[str, str]]:
    """Parse 'May 1st through May 31' → ('05/01/2026', '05/31/2026')."""
    m = re.search(
        r'(\w+)\s+(\d+)(?:st|nd|rd|th)?\s+through\s+(\w+)\s+(\d+)',
        line, re.IGNORECASE,
    )
    if not m:
        return None
    sm = MONTH_ABBR.get(m.group(1).capitalize())
    em = MONTH_ABBR.get(m.group(3).capitalize())
    if not sm or not em:
        return None
    return (
        f"{sm:02d}/{int(m.group(2)):02d}/{year}",
        f"{em:02d}/{int(m.group(4)):02d}/{year}",
    )


def _parse_regular_desc(text: str):
    """
    Parse 'Filipino News M-F 4p-5p , 6p-7p' → (language, description, days, time).
    """
    s = text.strip()

    time_m = TIME_TAIL_RE.search(s)
    if time_m:
        time_raw = time_m.group(1)
        s = s[:time_m.start()].strip()
    else:
        time_raw = "06:00-23:59"

    day_m = DAY_TAIL_RE.search(s)
    if day_m:
        days_raw = day_m.group(1)
        s = s[:day_m.start()].strip()
    else:
        days_raw = "M-Su"

    language = _extract_language(s)
    description = s
    days = _normalize_days(days_raw)
    time = _normalize_time(time_raw)
    return language, description, days, time


def _parse_ros_desc(text: str):
    """
    Parse 'Chinese (M-Sun 7p-12a' or 'South Asian (M-Sun 1p-4p)' → (language, days, time).
    """
    m = ROS_RE.match(text.strip())
    if not m:
        language = _extract_language(text)
        return language, "M-Su", "06:00-23:59"

    lang_part = m.group(1).strip()
    inner = m.group(2).strip()
    language = _extract_language(lang_part)

    inner_m = ROS_INNER_RE.match(inner)
    if inner_m:
        days = _normalize_days(inner_m.group(1))
        time = _normalize_time(inner_m.group(2))
    else:
        days = "M-Su"
        time = _normalize_time(inner)

    return language, days, time


# ---------------------------------------------------------------------------
# Main parse function
# ---------------------------------------------------------------------------

def parse_toyota_av_pdf(pdf_path: str) -> ToyotaAVOrder:
    """Parse a Toyota AAPI Added Value flight schedule PDF."""
    path = Path(pdf_path)

    # Infer year from filename (e.g. "_2026.pdf")
    year_m = re.search(r'_(\d{4})', path.name)
    year = int(year_m.group(1)) if year_m else 2026

    with pdfplumber.open(path) as pdf:
        page = pdf.pages[0]
        words = page.extract_words()
        text_lines = page.extract_text().splitlines()

    # --- Step 1: Parse header metadata ---
    title = None
    flight_start = flight_end = None
    week_dates = None

    for raw in text_lines:
        line = raw.strip()
        if not line:
            continue
        if title is None and ("AAPI" in line or "Heritage" in line):
            title = line
            continue
        if flight_start is None and "through" in line.lower():
            result = _parse_flight_dates(line, year)
            if result:
                flight_start, flight_end = result
            continue
        if week_dates is None:
            result = _parse_week_header(line)
            if result:
                week_dates = result

    if not title:
        title = "Toyota AAPI Month Sponsorship"
    if not week_dates:
        raise ValueError("Could not find week header in PDF")
    if not flight_start or not flight_end:
        raise ValueError("Could not find flight dates in PDF")

    # --- Step 2: Group words by row (bucket top by 2pt) ---
    rows: dict = {}
    for w in words:
        key = round(w['top'] / 2) * 2
        rows.setdefault(key, []).append(w)

    # --- Step 3: Collect data rows ---
    data_rows = []
    for key in sorted(rows):
        row_words = sorted(rows[key], key=lambda w: w['x0'])
        desc_words = [w for w in row_words if w['x0'] < DESC_X_THRESHOLD]
        num_words  = [w for w in row_words if w['x0'] >= DESC_X_THRESHOLD]

        if not desc_words or not num_words:
            continue
        try:
            nums = [int(w['text']) for w in num_words]
        except ValueError:
            continue
        if len(nums) != NUM_WEEK_COLS + 1:
            continue

        desc_text = ' '.join(w['text'] for w in desc_words)
        data_rows.append((desc_text, nums[:NUM_WEEK_COLS], nums[NUM_WEEK_COLS]))

    if len(data_rows) != 15:
        raise ValueError(
            f"Expected 15 data rows, found {len(data_rows)}. "
            "Check PDF layout or DESC_X_THRESHOLD."
        )

    # --- Step 4: Parse each row ---
    lines = []
    for i, (desc_text, weekly_spots, total_spots) in enumerate(data_rows):
        is_bonus = i >= 10  # last 5 rows are ROS

        if is_bonus:
            language, days, time = _parse_ros_desc(desc_text)
            description = f"BNS {language} ROS"
        else:
            language, prog_name, days, time = _parse_regular_desc(desc_text)
            description = prog_name

        lines.append(ToyotaAVLine(
            description=description,
            language=language,
            days=days,
            time=time,
            weekly_spots=weekly_spots,
            total_spots=total_spots,
            is_bonus=is_bonus,
        ))

    return ToyotaAVOrder(
        title=title,
        flight_start=flight_start,
        flight_end=flight_end,
        week_dates=week_dates,
        lines=lines,
    )
