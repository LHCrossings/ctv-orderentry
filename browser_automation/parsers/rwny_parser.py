"""
Resorts World New York (RWNY) Parser

Parses the Crossings TV Media Proposal for Resorts World New York.
Supports both PDF (.pdf) and Excel (.xlsx) input formats.

═══════════════════════════════════════════════════════════════════════════════
FORMAT DESCRIPTION
═══════════════════════════════════════════════════════════════════════════════

Header section (key-value table):
  Client, Contact, Email, Billing Cycle, Market, Channel,
  Estimate Flight Date, Date

Flight Schedule table:
  Language Block | Day Part/Program | Standard Rate per :30s |
  Rate for Resorts World | Month1 | Month2 | ... | Total Units | Value | Budget

Paid rows:   Language block name | actual daypart | std_rate | rwny_rate | spots...
Bonus rows:  [Excel: BONUS | language | ROS | ...] [PDF: language | ROS | ... | $0]

KEY DIFFERENCES FROM CHARMAINE:
  - Month columns (May, June) instead of weekly breakdown
  - Two rate columns — use "Rate for Resorts World" only
  - Market always NYC
  - Duration always :30s
  - Billing cycle: Calendar

ETERE ENTRY:
  One Etere line per language/daypart per calendar month.
  First month may be a partial month (e.g., May 15–31).

DETECTION:
  "Resorts World New York" in PDF text
  Filename contains "Resorts World"

═══════════════════════════════════════════════════════════════════════════════
"""

import re
from calendar import monthrange
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

# ─────────────────────────────────────────────────────────────────────────────
# DATA CLASSES
# ─────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class RWNYMonthColumn:
    """A calendar month column with Etere-ready date range."""
    label: str       # "May", "June"
    start_date: str  # "05/15/2026" (partial for first month; 01 otherwise)
    end_date: str    # "05/31/2026"


@dataclass(frozen=True)
class RWNYLine:
    """One language block row from the RWNY Flight Schedule table."""
    language: str        # "Filipino", "Vietnamese"
    block_name: str      # "FILIPINO NEWS/TALK" (raw label from document)
    daypart_raw: str     # "M-F 4p-5p, 6p-7p" (raw, for reference)
    days: str            # "M-F", "Sa-Su" (normalized)
    time_str: str        # "4p-5p; 6p-7p" (semicolons; ready for EtereClient)
    is_bonus: bool
    rate: float          # Rate for Resorts World (0.0 for bonus)
    monthly_spots: list  # [12, 12] — spots per month column
    total_spots: int


@dataclass
class RWNYOrder:
    """Complete parsed RWNY order."""
    client: str              # "Resorts World New York"
    contact: str             # "Joe Wong"
    email: str               # "joe.wong@rwnewyork.com"
    market: str              # Always "NYC"
    duration_seconds: int    # 30
    flight_start: str        # "05/15/2026" (MM/DD/YYYY)
    flight_end: str          # "06/30/2026" (MM/DD/YYYY)
    month_columns: list      # list[RWNYMonthColumn]
    lines: list = field(default_factory=list)  # list[RWNYLine]
    pdf_path: str = ""
    rates_are_net: bool = False


# ─────────────────────────────────────────────────────────────────────────────
# CONSTANTS
# ─────────────────────────────────────────────────────────────────────────────

_MONTH_NUMS = {
    'jan': 1, 'feb': 2, 'mar': 3, 'apr': 4, 'may': 5, 'jun': 6,
    'jul': 7, 'aug': 8, 'sep': 9, 'oct': 10, 'nov': 11, 'dec': 12,
}

# ROS schedule per language (days, time_str) — mirrors Language.get_ros_schedule()
_ROS_BY_LANGUAGE: dict[str, tuple[str, str]] = {
    'filipino':    ('M-Su', '4p-7p'),
    'vietnamese':  ('M-Su', '10a-1p'),
    'chinese':     ('M-Su', '6a-11:59p'),
    'korean':      ('M-Su', '8a-10a'),
    'hmong':       ('Sa-Su', '6p-8p'),
    'south asian': ('M-Su', '1p-4p'),
    'japanese':    ('M-F',  '10a-11a'),
}

# Row labels that indicate summary/footer rows (skip these)
_SKIP_FIRST_WORDS = {
    'paid', 'bonuses', 'total', 'translation', 'discount',
    'contract', 'production', 'thank', 'discounted', 'voiceover',
    'filipino and', 'none',
}


# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _month_num(label: str) -> int:
    """Return 1–12 for a month label, 0 if unrecognised."""
    return _MONTH_NUMS.get(str(label).strip().lower()[:3], 0)


def _parse_flight_range(date_range: str, year: int) -> tuple[str, str]:
    """Parse 'May 15  through  June 30' → ('05/15/2026', '06/30/2026').

    Also accepts the month-only variant 'July through September' seen on the
    Q3 2026 proposal → first day of the start month, last day of the end month.
    """
    s = str(date_range).strip()
    m = re.search(r'([A-Za-z]+)\s+(\d{1,2})\s+through\s+([A-Za-z]+)\s+(\d{1,2})', s)
    if m:
        sm = _MONTH_NUMS.get(m.group(1).lower()[:3], 0)
        em = _MONTH_NUMS.get(m.group(3).lower()[:3], 0)
        if sm and em:
            return (f'{sm:02d}/{int(m.group(2)):02d}/{year}',
                    f'{em:02d}/{int(m.group(4)):02d}/{year}')
    m = re.search(r'([A-Za-z]+)\s+through\s+([A-Za-z]+)', s)
    if m:
        sm = _MONTH_NUMS.get(m.group(1).lower()[:3], 0)
        em = _MONTH_NUMS.get(m.group(2).lower()[:3], 0)
        if sm and em:
            return (f'{sm:02d}/01/{year}',
                    f'{em:02d}/{monthrange(year, em)[1]:02d}/{year}')
    return '', ''


def _build_month_columns(
    labels: list[str],
    flight_start: str,
    year: int,
) -> list[RWNYMonthColumn]:
    """
    Build month column date ranges.

    First month uses the actual flight start (may be mid-month).
    Subsequent months start on the 1st.
    """
    cols = []
    fs_dt = None
    if flight_start:
        try:
            fs_dt = datetime.strptime(flight_start, '%m/%d/%Y')
        except ValueError:
            pass

    for i, label in enumerate(labels):
        mn = _month_num(label)
        if not mn:
            continue
        last_day = monthrange(year, mn)[1]
        end_date = f'{mn:02d}/{last_day:02d}/{year}'

        if i == 0 and fs_dt and fs_dt.month == mn:
            start_date = flight_start   # partial first month
        else:
            start_date = f'{mn:02d}/01/{year}'

        cols.append(RWNYMonthColumn(label=label.strip(), start_date=start_date, end_date=end_date))
    return cols


def _detect_language(block_name: str) -> str:
    """Extract base language name from a block label."""
    bn = block_name.upper()
    if 'FILIPINO' in bn or 'TAGALOG' in bn:
        return 'Filipino'
    if 'VIETNAMESE' in bn:
        return 'Vietnamese'
    if 'CHINESE' in bn or 'MANDARIN' in bn or 'CANTONESE' in bn:
        return 'Chinese'
    if 'KOREAN' in bn:
        return 'Korean'
    if 'HMONG' in bn:
        return 'Hmong'
    if 'SOUTH ASIAN' in bn or 'HINDI' in bn or 'PUNJABI' in bn:
        return 'South Asian'
    if 'JAPANESE' in bn:
        return 'Japanese'
    return block_name.split()[0].title() if block_name.split() else ''


def _get_ros_schedule(language: str) -> tuple[str, str]:
    """Return (days, time_str) for the standard ROS schedule of a language."""
    return _ROS_BY_LANGUAGE.get(language.lower().strip(), ('M-Su', '6a-11:59p'))


def _normalize_day_pattern(s: str) -> str:
    """Normalize day abbreviations: Sat→Sa, Sun→Su, remove spaces around hyphen."""
    s = re.sub(r'\bSat\b', 'Sa', s, flags=re.IGNORECASE)
    s = re.sub(r'\bSun\b', 'Su', s, flags=re.IGNORECASE)
    s = re.sub(r'\s*[-–]\s*', '-', s)
    return s.strip()


def _extract_days_time(daypart: str) -> tuple[str, str]:
    """
    Split a daypart string into (days, time_str).

    "M-F 4p-5p, 6p-7p"  → ("M-F", "4p-5p; 6p-7p")
    "Sat- Sun 4p-6p"     → ("Sa-Su", "4p-6p")
    "Sat- Sun 10A-1P"    → ("Sa-Su", "10A-1P")
    "ROS"                → ("M-Su", "6a-11:59p")

    Commas in time ranges are converted to semicolons for EtereClient.parse_time_range.
    """
    daypart = str(daypart).strip()
    if daypart.upper() == 'ROS' or not daypart:
        return 'M-Su', '6a-11:59p'

    # Match day pattern at start: M-F, Sa-Su, Sat-Sun, M-Su, etc.
    day_match = re.match(
        r'^((?:M|Tu|W|Th|F|Sa|Su|Sat|Sun|Mon)(?:\s*[-–]\s*(?:M|Tu|W|Th|F|Sa|Su|Sat|Sun|Mon))?)\s+',
        daypart, re.IGNORECASE,
    )
    if day_match:
        days = _normalize_day_pattern(day_match.group(1))
        time_part = daypart[day_match.end():].strip()
        time_part = time_part.replace(',', ';')
        return days, time_part

    return 'M-Su', daypart


def _parse_rate(s) -> float:
    """Parse a rate value like '$ 100.00', '$0', '100', '0' → float."""
    if s is None:
        return 0.0
    cleaned = re.sub(r'[,$\s]', '', str(s))
    try:
        return float(cleaned)
    except ValueError:
        return 0.0


def _parse_spots(s) -> int:
    """Parse a spot count value → int."""
    if s is None:
        return 0
    try:
        return int(str(s).strip().split('.')[0])
    except (ValueError, AttributeError):
        return 0


def _detect_year(text: str) -> int:
    """Find a 4-digit year in a string, defaulting to current year."""
    m = re.search(r'\b(20\d{2})\b', str(text))
    return int(m.group(1)) if m else datetime.now().year


# ─────────────────────────────────────────────────────────────────────────────
# PDF PARSER (pdfplumber)
# ─────────────────────────────────────────────────────────────────────────────

def _parse_rwny_pdf_file(pdf_path: str) -> Optional[RWNYOrder]:
    import pdfplumber

    with pdfplumber.open(pdf_path) as pdf:
        if not pdf.pages:
            return None
        page = pdf.pages[0]
        tables = page.extract_tables()

    if len(tables) < 2:
        return None

    # ── Table 1: header key-value pairs ──────────────────────────────────────
    header_kv: dict[str, str] = {}
    for row in tables[0]:
        if row and len(row) >= 2 and row[0] and row[1]:
            key = str(row[0]).strip().rstrip(':').strip()
            val = str(row[1]).strip()
            header_kv[key] = val

    client          = header_kv.get('Client', 'Resorts World New York')
    contact         = header_kv.get('Contact', '').rstrip(':').strip()
    email           = header_kv.get('Email', '')
    flight_range    = header_kv.get('Estimate Flight Date', '')
    date_str        = header_kv.get('Date', '')

    year = _detect_year(date_str) or _detect_year(flight_range) or datetime.now().year
    flight_start, flight_end = _parse_flight_range(flight_range, year)

    # ── Table 2: Flight Schedule ──────────────────────────────────────────────
    sched = tables[1]
    if not sched or len(sched) < 2:
        return None

    # Find the header row (contains "Language Block")
    hdr_idx = next(
        (i for i, row in enumerate(sched)
         if row and any(c and 'Language Block' in str(c) for c in row)),
        -1,
    )
    if hdr_idx < 0:
        return None

    hdr = [str(c or '').replace('\n', ' ').strip() for c in sched[hdr_idx]]

    # Locate key columns
    lang_col     = 0
    daypart_col  = 1
    rwny_rate_col = 3  # "Rate for Resorts World"

    # Month columns: after col 3, before "Total Units" / "Value" / "Budget"
    month_col_idxs: list[int] = []
    month_labels: list[str] = []
    for j, h in enumerate(hdr):
        if j <= rwny_rate_col:
            continue
        if any(kw in h.lower() for kw in ('total', 'value', 'budget')):
            break
        if _month_num(h):
            month_col_idxs.append(j)
            month_labels.append(h)

    if not month_col_idxs:
        return None

    month_columns = _build_month_columns(month_labels, flight_start, year)
    # Fallback: header flight range unparseable but month columns exist —
    # the flight is simply first month start → last month end.
    if (not flight_start or not flight_end) and month_columns:
        flight_start = flight_start or month_columns[0].start_date
        flight_end   = flight_end   or month_columns[-1].end_date

    # ── Parse data rows ───────────────────────────────────────────────────────
    lines: list[RWNYLine] = []

    for row in sched[hdr_idx + 1:]:
        if not row:
            continue

        col0 = str(row[lang_col] or '').replace('\n', ' ').strip() if row[lang_col] else ''
        col1 = str(row[daypart_col] or '').strip() if len(row) > daypart_col and row[daypart_col] else ''

        if not col0:
            continue

        # Skip summary/footer rows
        first_word = col0.lower().split()[0] if col0.split() else ''
        if first_word in _SKIP_FIRST_WORDS or col0.lower().startswith('translation'):
            continue

        # Bonus detection: daypart column contains "ROS"
        is_bonus = 'ros' in col1.lower()

        # RWNY rate
        rate_raw = str(row[rwny_rate_col] or '').strip() if len(row) > rwny_rate_col else '0'
        rate = _parse_rate(rate_raw)

        # Monthly spots
        monthly_spots = [
            _parse_spots(row[j] if len(row) > j else 0)
            for j in month_col_idxs
        ]
        total_spots = sum(monthly_spots)
        if total_spots == 0:
            continue

        if is_bonus:
            language = col0.strip()      # PDF bonus rows: col0 = language name
            block_name = col0
            days, time_str = _get_ros_schedule(language)
        else:
            language = _detect_language(col0)
            block_name = col0
            days, time_str = _extract_days_time(col1)

        lines.append(RWNYLine(
            language=language,
            block_name=block_name,
            daypart_raw=col1,
            days=days,
            time_str=time_str,
            is_bonus=is_bonus,
            rate=rate,
            monthly_spots=monthly_spots,
            total_spots=total_spots,
        ))

    return RWNYOrder(
        client=client,
        contact=contact,
        email=email,
        market='NYC',
        duration_seconds=30,
        flight_start=flight_start,
        flight_end=flight_end,
        month_columns=month_columns,
        lines=lines,
        pdf_path=pdf_path,
    )


# ─────────────────────────────────────────────────────────────────────────────
# EXCEL PARSER (openpyxl)
# ─────────────────────────────────────────────────────────────────────────────

def _parse_rwny_xlsx(path: str) -> Optional[RWNYOrder]:
    import openpyxl

    wb = openpyxl.load_workbook(path, data_only=True)
    ws = wb.active

    # Read all rows preserving column positions (None for empty cells)
    all_rows = [[c.value for c in row] for row in ws.iter_rows()]

    header_kv: dict[str, str] = {}
    hdr_row_idx = -1
    hdr_row: list = []

    for ri, row in enumerate(all_rows):
        # Detect the Flight Schedule header row
        if any(v is not None and 'Language Block' in str(v) for v in row):
            hdr_row_idx = ri
            hdr_row = row
            break

        # Parse key-value header pairs (key in first non-None cell, value in next)
        non_none = [(j, v) for j, v in enumerate(row) if v is not None]
        if len(non_none) >= 2:
            k = str(non_none[0][1]).strip().rstrip(':').strip()
            v = str(non_none[1][1]).strip()
            if k in ('Client', 'Contact', 'Email', 'Billing Cycle', 'Market',
                     'Channel', 'Estimate Flight Date', 'Date'):
                header_kv[k] = v

    if hdr_row_idx < 0:
        return None

    # Map column positions from the header row
    lang_col = next(
        (j for j, v in enumerate(hdr_row) if v is not None and 'Language Block' in str(v)), None)
    daypart_col = next(
        (j for j, v in enumerate(hdr_row) if v is not None and ('Day Part' in str(v) or 'Program' in str(v))), None)
    rwny_rate_col = next(
        (j for j, v in enumerate(hdr_row) if v is not None and 'Resorts World' in str(v)), None)
    total_col = next(
        (j for j, v in enumerate(hdr_row)
         if v is not None and 'Total' in str(v) and j > (rwny_rate_col or 0)), None)

    if lang_col is None or daypart_col is None or rwny_rate_col is None:
        return None

    # Month columns: between rwny_rate_col and total_col
    month_col_idxs: list[int] = []
    month_labels: list[str] = []
    for j, v in enumerate(hdr_row):
        if j <= rwny_rate_col:
            continue
        if total_col is not None and j >= total_col:
            break
        if v is not None and _month_num(str(v)):
            month_col_idxs.append(j)
            month_labels.append(str(v).strip())

    if not month_col_idxs:
        return None

    # Flight date info
    client       = header_kv.get('Client', 'Resorts World New York')
    contact      = header_kv.get('Contact', '').rstrip(':').strip()
    email        = header_kv.get('Email', '')
    flight_range = header_kv.get('Estimate Flight Date', '')
    date_str     = str(header_kv.get('Date', ''))

    year = _detect_year(date_str) or _detect_year(flight_range) or datetime.now().year
    flight_start, flight_end = _parse_flight_range(flight_range, year)
    month_columns = _build_month_columns(month_labels, flight_start, year)
    # Fallback: header flight range unparseable but month columns exist —
    # the flight is simply first month start → last month end.
    if (not flight_start or not flight_end) and month_columns:
        flight_start = flight_start or month_columns[0].start_date
        flight_end   = flight_end   or month_columns[-1].end_date

    # ── Parse data rows ───────────────────────────────────────────────────────
    lines: list[RWNYLine] = []

    for row in all_rows[hdr_row_idx + 1:]:
        if not any(v is not None for v in row):
            continue

        col0_val = row[lang_col] if lang_col < len(row) else None
        if col0_val is None:
            continue
        col0 = str(col0_val).strip()
        if not col0:
            continue

        # Bonus detection approach A: 'BONUS' appears at lang_col → data shifts right by 1
        bonus_at_lang_col = col0.upper() == 'BONUS'

        # Bonus detection approach B: 'BONUS' appears one column before lang_col
        # (language name ends up at lang_col, all other data stays at normal positions)
        dp_raw = str(row[daypart_col]).strip() if daypart_col < len(row) and row[daypart_col] else ''
        bonus_before_lang_col = (not bonus_at_lang_col) and dp_raw.upper() == 'ROS'

        is_bonus = bonus_at_lang_col or bonus_before_lang_col

        if is_bonus:
            if bonus_at_lang_col:
                # BONUS at lang_col — language name is at lang_col+1, data shifts +1
                lang_val  = row[lang_col + 1] if lang_col + 1 < len(row) else ''
                language  = str(lang_val).strip() if lang_val else ''
                rate_raw  = row[rwny_rate_col + 1] if rwny_rate_col + 1 < len(row) else 0
                monthly_spots = [
                    _parse_spots(row[j + 1] if j + 1 < len(row) else 0)
                    for j in month_col_idxs
                ]
            else:
                # BONUS at col before lang_col — language is col0, data at normal positions
                language  = col0
                rate_raw  = row[rwny_rate_col] if rwny_rate_col < len(row) else 0
                monthly_spots = [
                    _parse_spots(row[j] if j < len(row) else 0)
                    for j in month_col_idxs
                ]
            block_name  = language
            daypart_raw = 'ROS'
        else:
            # Skip summary/footer rows
            first_word = col0.lower().split()[0] if col0.split() else ''
            if first_word in _SKIP_FIRST_WORDS or col0.lower().startswith('translation'):
                continue

            language   = _detect_language(col0)
            block_name = col0
            daypart_raw = str(row[daypart_col]).strip() if daypart_col < len(row) and row[daypart_col] else ''
            rate_raw   = row[rwny_rate_col] if rwny_rate_col < len(row) else 0
            monthly_spots = [
                _parse_spots(row[j] if j < len(row) else 0)
                for j in month_col_idxs
            ]

        rate = _parse_rate(rate_raw)
        total_spots = sum(monthly_spots)
        if total_spots == 0:
            continue

        if is_bonus:
            days, time_str = _get_ros_schedule(language)
        else:
            days, time_str = _extract_days_time(daypart_raw)

        lines.append(RWNYLine(
            language=language,
            block_name=block_name,
            daypart_raw=daypart_raw,
            days=days,
            time_str=time_str,
            is_bonus=is_bonus,
            rate=rate,
            monthly_spots=monthly_spots,
            total_spots=total_spots,
        ))

    return RWNYOrder(
        client=client,
        contact=contact,
        email=email,
        market='NYC',
        duration_seconds=30,
        flight_start=flight_start,
        flight_end=flight_end,
        month_columns=month_columns,
        lines=lines,
        pdf_path=path,
    )


# ─────────────────────────────────────────────────────────────────────────────
# PUBLIC API
# ─────────────────────────────────────────────────────────────────────────────

def parse_rwny_pdf(path: str) -> list[RWNYOrder]:
    """
    Parse an RWNY Crossings TV Media Proposal (PDF or XLSX).

    Args:
        path: Path to the PDF or XLSX file

    Returns:
        List containing one RWNYOrder, or empty list on failure.
    """
    ext = Path(path).suffix.lower()
    if ext in ('.xlsx', '.xls'):
        order = _parse_rwny_xlsx(path)
    else:
        order = _parse_rwny_pdf_file(path)

    return [order] if order else []


# ─────────────────────────────────────────────────────────────────────────────
# STANDALONE ENTRY POINT
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    import sys

    if len(sys.argv) < 2:
        print('Usage: python rwny_parser.py <pdf_or_xlsx_path>')
        sys.exit(1)

    orders = parse_rwny_pdf(sys.argv[1])
    if not orders:
        print('✗ No order parsed')
        sys.exit(1)

    order = orders[0]
    print(f'\n{"=" * 70}')
    print('RWNY ORDER')
    print(f'{"=" * 70}')
    print(f'  Client:   {order.client}')
    print(f'  Contact:  {order.contact}  {order.email}')
    print(f'  Market:   {order.market}')
    print(f'  Duration: :{order.duration_seconds}s')
    print(f'  Flight:   {order.flight_start} – {order.flight_end}')
    print(f'  Months:   {[c.label for c in order.month_columns]}')
    print(f'  Lines:    {len(order.lines)}')

    for mc in order.month_columns:
        print(f'\n  [{mc.label}]  {mc.start_date} – {mc.end_date}')

    total_spots = 0
    total_cost = 0.0
    for i, line in enumerate(order.lines):
        spot_type = 'BNS' if line.is_bonus else 'PAID'
        print(f'\n  [{i+1}] {spot_type}  {line.language}  —  {line.block_name}')
        print(f'       Daypart: {line.days} {line.time_str}')
        print(f'       Rate:    ${line.rate:.2f}')
        print(f'       Monthly: {line.monthly_spots}  (total {line.total_spots})')
        total_spots += line.total_spots
        total_cost += line.total_spots * line.rate

    print(f'\n  TOTALS: {total_spots} spots, ${total_cost:,.2f}')
