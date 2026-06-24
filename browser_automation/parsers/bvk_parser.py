"""
BVK Order PDF Parser

Parses broadcast orders from BVK agency in their standard "Revision" format.
BVK is a Milwaukee-based agency. Orders typically cover Crossings TV markets
(Sacramento CVC) for multi-language ethnic programming.

PDF Structure:
- Header: Client, Market, Flight Dates, CPE (estimate), Description, Separation
- Table (multi-page): Line#, Daypart (days+time+program), Daypart Code,
  Gross Rate, C/T, Duration, weekly spot counts per date column
- Detection: "Billing To: BVK" + "CPE:" in header

Business Rules:
- Rates are GROSS from PDF -- no gross-up needed
- Separation: PDF says 30 min -> caller enters as (25, 0, 0) per lessons rule
- Bonus lines: Gross = $0.00
- Day patterns: MTuWThF -> M-F, SaSu -> Sa-Su, MTuWThFSaSu -> M-Su
- Time ranges may be missing the hyphen (e.g. "11:30P12:00A" -> "11:30P-12:00A")
- "Revision" header / Version field are BVK's internal versioning -- always new order

Implementation note:
  BVK's PDF alternates shaded/unshaded rows.  pdfplumber's extract_table() only
  detects cell borders for even-numbered rows (the shaded ones), so it silently
  skips odd-numbered lines.  We use extract_table() only to identify the week-date
  columns, then use extract_text() for all line-item parsing.
"""

import re
from dataclasses import dataclass
from typing import List

import pdfplumber

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_DAY_MAP = {
    'MTuWThFSaSu': 'M-Su',
    'MTuWThFSa':   'M-Sa',
    'MTuWThF':     'M-F',
    'SaSu':        'Sa-Su',
    'Sa':          'Sa',
    'Su':          'Su',
}

_MONTH_ABBR = {
    1: 'Jan', 2: 'Feb', 3: 'Mar', 4: 'Apr',
    5: 'May', 6: 'Jun', 7: 'Jul', 8: 'Aug',
    9: 'Sep', 10: 'Oct', 11: 'Nov', 12: 'Dec',
}

# Combined Hindi/Punjabi must be checked before the individual patterns.
_LANG_PATTERNS = [
    (re.compile(r'hindi.{0,5}punjabi|punjabi.{0,5}hindi', re.I), 'SA'),
    (re.compile(r'cantonese',                                re.I), 'C'),
    (re.compile(r'mandarin|mandrain',                        re.I), 'M'),
    (re.compile(r'vietnamese',                               re.I), 'V'),
    (re.compile(r'korean',                                   re.I), 'K'),
    (re.compile(r'hindi|hinid',                              re.I), 'SA'),
    (re.compile(r'punjabi',                                  re.I), 'P'),
    (re.compile(r'hmong',                                    re.I), 'Hm'),
    (re.compile(r'filipino',                                 re.I), 'T'),
    (re.compile(r'japanese|jananese',                        re.I), 'J'),
]

_MARKET_MAP = {
    'SACRAMENTO':     'CVC',
    'CENTRAL VALLEY': 'CVC',
    'SAN FRANCISCO':  'SFO',
    'SEATTLE':        'SEA',
    'LOS ANGELES':    'LAX',
    'LA':             'LAX',
    'HOUSTON':        'HOU',
    'CHICAGO':        'CMP',
    'MINNEAPOLIS':    'CMP',
    'WASHINGTON':     'WDC',
    'NEW YORK':       'NYC',
}

# Regex anchored to start of line: line_no  day_pattern  time_start
_START_RE = re.compile(r'^(\d+)\s+([A-Za-z]+)\s+(\d+:\d+)')

# Daypart data line: "PA $25.00 C 15 3 4 3 3 3 3 0 ..."
_DATA_RE = re.compile(
    r'([A-Z]{2})\s+'           # 2-letter daypart code
    r'\$(\d+\.\d+)\s+'         # gross rate
    r'([CT])\s+'               # C or T
    r'(\d+)\s+'                # duration (seconds)
    r'([\d ]+)'                # weekly spot counts
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _normalize_days(raw: str) -> str:
    return _DAY_MAP.get(raw.strip(), raw.strip())


def _fix_time_range(s: str) -> str:
    """Insert missing hyphen: '11:30P12:00A' -> '11:30P-12:00A'"""
    return re.sub(r'(\d+:\d+[APap])(\d)', r'\1-\2', s)


def _detect_language(program: str, fallback: str = '') -> str:
    for pattern, code in _LANG_PATTERNS:
        if pattern.search(program):
            return code
    return fallback


def _to_month_day(mmdd: str) -> str:
    """'5/18' -> 'May 18'  (format expected by EtereClient.consolidate_weeks)"""
    try:
        m, d = mmdd.strip().split('/')
        return f"{_MONTH_ABBR[int(m)]} {int(d)}"
    except (ValueError, KeyError):
        return mmdd


def _expand_year(date_str: str) -> str:
    """'5/18/26' -> '5/18/2026'  (consolidate_weeks requires 4-digit year)"""
    parts = date_str.strip().split('/')
    if len(parts) == 3 and len(parts[2]) == 2:
        parts[2] = '20' + parts[2]
    return '/'.join(parts)


def _normalize_market(raw: str) -> str:
    return _MARKET_MAP.get(raw.strip().upper(), raw.strip().upper())


def _extract_field(text: str, pattern: str) -> str:
    m = re.search(pattern, text, re.IGNORECASE | re.DOTALL)
    return m.group(1).strip() if m else ''


def _parse_text_records(all_text: str, num_week_cols: int) -> List[dict]:
    """
    Parse all BVK line items from raw text (extract_text output).

    BVK text structure (confirmed from raw dump):
      - The daypart code, rate, C/T, duration, and spot counts are ALWAYS on the
        same line as the line number -- never on a subsequent line.
      - The program name is on the NEXT line(s) in parentheses.
      - If the time range wraps (first line ends with bare '-'), the time-end
        appears at the start of the line that also has the opening '('.

    Examples:
      Complete time (most lines):
        "1 MTuWThF 7:00P-8:00P PA $25.00 C 15 3 4 3 3 3 3 0 0 0 0 0 0 0 19"
        "(Cantonese News/Talk)"

      Wrapped time:
        "2 MTuWThF 11:30P- LN $25.00 C 15 2 2 2 2 2 2 0 0 0 0 0 0 0 12"
        "12:00A (Cantonese"
        "News/Talk)"

    Returns list of dicts with keys:
      line_no, days_raw, time_raw, program, daypart_code, rate, duration, weekly_spots
    """
    records = []
    lines = [ln.strip() for ln in all_text.split('\n')]
    n = len(lines)

    i = 0
    while i < n:
        sm = _START_RE.match(lines[i])
        if not sm:
            i += 1
            continue

        line_no  = int(sm.group(1))
        days_raw = sm.group(2)

        # Everything after the day-pattern token on the first line
        rest = lines[i][sm.end(2):].strip()

        # Data (daypart code, rate, duration, spots) is always on this same line
        dm = _DATA_RE.search(rest)
        if not dm:
            i += 1
            continue

        # Time prefix = text before the data match
        time_prefix = rest[:dm.start()].strip()

        daypart_code = dm.group(1)
        rate         = float(dm.group(2))
        duration     = int(dm.group(4))
        spots        = [int(x) for x in dm.group(5).split() if x.strip().isdigit()]
        # Drop trailing total (one extra number beyond num_week_cols)
        spots = spots[:num_week_cols]
        while len(spots) < num_week_cols:
            spots.append(0)

        # Collect program name from subsequent lines.
        # If time_prefix ends with '-', the time-end appears at the start of the
        # line that contains the opening '('.
        time_end      = ''
        program_parts = []
        j = i + 1

        while j < n:
            lj = lines[j]

            # Next record start — stop
            if _START_RE.match(lj):
                break

            # If time wrapped, look for time-end at the beginning of this line
            if not time_end and time_prefix.endswith('-'):
                te_m = re.match(r'(\d+:\d+[APap])', lj)
                if te_m:
                    time_end = te_m.group(1)
                    lj = lj[te_m.end():].strip()

            program_parts.append(lj)
            j += 1

            # Stop once we have a complete parenthesised program name
            combined = ' '.join(program_parts)
            if '(' in combined and ')' in combined:
                break

        combined = ' '.join(program_parts)
        prog_m   = re.search(r'\(([^)]+)\)', combined)
        program  = re.sub(r'\s+', ' ', prog_m.group(1)).strip() if prog_m else ''

        if time_prefix.endswith('-') and time_end:
            time_raw = time_prefix + time_end
        else:
            time_raw = time_prefix.rstrip('-').strip()

        records.append({
            'line_no':      line_no,
            'days_raw':     days_raw,
            'time_raw':     time_raw,
            'program':      program,
            'daypart_code': daypart_code,
            'rate':         rate,
            'duration':     duration,
            'weekly_spots': spots,
        })

        i = j

    return records


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class BVKLine:
    line_no:      int
    days:         str        # "M-F", "Sa-Su", "M-Su"
    time_str:     str        # "7:00P-8:00P" (hyphen guaranteed)
    program:      str        # "Cantonese News/Talk"
    daypart_code: str        # "PA", "AV", "RT", etc.
    gross_rate:   float
    duration:     int        # seconds
    weekly_spots: List[int]
    is_bonus:     bool
    language:     str        # "C", "M", "V", "K", "SA", "P", "Hm", "T", "J"

    @property
    def total_spots(self) -> int:
        return sum(self.weekly_spots)

    def get_description(self) -> str:
        if self.is_bonus:
            return f"(Line {self.line_no}) BNS {self.language}"
        # Drop :00 minutes for readability: "7:00P-8:00P" -> "7P-8P"
        short_time = re.sub(r':00([APap])', r'\1', self.time_str).upper()
        return f"(Line {self.line_no}) {self.days} {short_time} {self.language}"


@dataclass
class BVKOrder:
    client:         str
    product:        str
    description:    str         # IO Description field ("UCDH BIB Crossing TV")
    market:         str         # "CVC"
    flight_start:   str         # "5/18/26"
    flight_end:     str         # "8/16/26"
    estimate:       str         # CPE field ("ucd/ucd/4807")
    separation_min: int         # from PDF (typically 30)
    week_dates:     List[str]   # ["May 18", "May 25", ...] for consolidate_weeks
    lines:          List[BVKLine]
    rates_are_net:  bool = False

    @property
    def estimate_number(self) -> str:
        return self.estimate.split('/')[-1]

    def get_default_contract_code(
        self,
        code_name: str = '',
        include_market: bool = False,
    ) -> str:
        cpe_tail = self.estimate.split('/')[-1]
        if code_name:
            parts = [code_name]
            if include_market:
                short = {'CVC': 'CV', 'SFO': 'SF', 'SEA': 'SEA'}.get(self.market, self.market)
                parts.append(short)
            parts.append(cpe_tail)
            return ' '.join(parts)
        return f"BVK {self.client[:8].strip()} {cpe_tail}"

    def get_default_description(self, desc_name: str = '') -> str:
        cpe_tail = self.estimate.split('/')[-1]
        prefix = desc_name if desc_name else self.client
        return f"{prefix} - Est {cpe_tail} {self.description}"


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

def parse_bvk_pdf(pdf_path: str) -> BVKOrder:
    """
    Parse a BVK broadcast order PDF.

    Args:
        pdf_path: Path to PDF file

    Returns:
        BVKOrder with all parsed data

    Raises:
        ValueError: If PDF cannot be parsed or contains no line data
    """
    print(f"\n[BVK PARSER] Reading: {pdf_path}")

    with pdfplumber.open(pdf_path) as pdf:
        page0_text = pdf.pages[0].extract_text() or ''

        # Header fields
        client       = _extract_field(page0_text, r'Client:\s*(.+?)(?:\s{2,}|\n|Demo:)')
        product      = _extract_field(page0_text, r'Product:\s*(.+?)(?:\s{2,}|\n|Separation:)')
        description  = _extract_field(page0_text, r'Description:\s*(.+?)(?:\s{2,}|\n|Flight|Version)')
        market_raw   = _extract_field(page0_text, r'Market:\s*(.+?)(?:\s{2,}|\n|Vendor:)')
        flight_start = _expand_year(_extract_field(page0_text, r'Flight Start:\s*(\S+)'))
        flight_end   = _expand_year(_extract_field(page0_text, r'Flight End:\s*(\S+)'))
        estimate     = _extract_field(page0_text, r'CPE:\s*(\S+)')
        sep_str      = _extract_field(page0_text, r'Separation:\s*(\d+)')

        market         = _normalize_market(market_raw)
        separation_min = int(sep_str) if sep_str.isdigit() else 15

        print(f"[BVK PARSER] Client:  {client}")
        print(f"[BVK PARSER] Market:  {market}")
        print(f"[BVK PARSER] Flight:  {flight_start} - {flight_end}")
        print(f"[BVK PARSER] CPE:     {estimate}")
        print(f"[BVK PARSER] Sep:     {separation_min} min")

        # Week dates: extract_table() reliably finds the header row
        week_dates    = []
        num_week_cols = 0

        for page in pdf.pages:
            table = page.extract_table()
            if not table:
                continue
            for row in table:
                if not row:
                    continue
                date_cols = [
                    str(c).strip()
                    for c in row
                    if c and re.match(r'^\d{1,2}/\d{1,2}$', str(c).strip())
                ]
                if len(date_cols) >= 3:
                    week_dates    = [_to_month_day(d) for d in date_cols]
                    num_week_cols = len(week_dates)
                    break
            if week_dates:
                break

        if not week_dates:
            raise ValueError("Could not identify week date columns in BVK PDF")

        print(f"[BVK PARSER] Week dates ({num_week_cols}): {week_dates}")

        # Line items: use extract_text() which sees ALL rows (unlike extract_table)
        all_text = '\n'.join((page.extract_text() or '') for page in pdf.pages)
        raw_records = _parse_text_records(all_text, num_week_cols)

        print(f"[BVK PARSER] Records found: {len(raw_records)}")

        # Build BVKLine objects
        lines:         List[BVKLine] = []
        last_language: str           = ''

        for rec in raw_records:
            line_no      = rec['line_no']
            days         = _normalize_days(rec['days_raw'])
            time_str     = _fix_time_range(rec['time_raw'])
            program      = rec['program']
            daypart_code = rec['daypart_code']
            gross_rate   = rec['rate']
            duration     = rec['duration']
            weekly_spots = rec['weekly_spots']
            is_bonus     = gross_rate == 0.0

            language = _detect_language(program, fallback=last_language)
            if language:
                last_language = language

            lines.append(BVKLine(
                line_no=line_no,
                days=days,
                time_str=time_str,
                program=program,
                daypart_code=daypart_code,
                gross_rate=gross_rate,
                duration=duration,
                weekly_spots=weekly_spots,
                is_bonus=is_bonus,
                language=language,
            ))

            print(
                f"[BVK PARSER] Line {line_no:2d}: {days:6s} {time_str:22s} "
                f"{program[:28]:28s} "
                f"{'BONUS' if is_bonus else f'${gross_rate:.2f}':8s} "
                f"lang={language:3s}  spots={sum(weekly_spots)}"
            )

    print(f"[BVK PARSER] Done: {len(lines)} lines parsed")
    return BVKOrder(
        client=client,
        product=product,
        description=description,
        market=market,
        flight_start=flight_start,
        flight_end=flight_end,
        estimate=estimate,
        separation_min=separation_min,
        week_dates=week_dates,
        lines=lines,
        rates_are_net=False,
    )


# ---------------------------------------------------------------------------
# Standalone test
# ---------------------------------------------------------------------------

if __name__ == '__main__':
    import sys

    if len(sys.argv) < 2:
        print("Usage: python bvk_parser.py <pdf_path>")
        sys.exit(1)

    order = parse_bvk_pdf(sys.argv[1])

    print(f"\n{'='*70}")
    print("BVK ORDER SUMMARY")
    print(f"{'='*70}")
    print(f"Client:      {order.client}")
    print(f"Market:      {order.market}")
    print(f"Flight:      {order.flight_start} - {order.flight_end}")
    print(f"Estimate:    {order.estimate}")
    print(f"Description: {order.description}")
    print(f"Sep (PDF):   {order.separation_min} min")
    print(f"Week dates:  {order.week_dates}")
    print(f"Lines total: {len(order.lines)}")
    print(f"Total spots: {sum(l.total_spots for l in order.lines)}")

    print(f"\n{'='*70}")
    print("LINES")
    print(f"{'='*70}")
    for line in order.lines:
        flag = ' [BONUS]' if line.is_bonus else ''
        print(
            f"  Line {line.line_no:2d} [{line.language:3s}] "
            f"{line.days:6s} {line.time_str:22s} "
            f"${line.gross_rate:.2f}{flag}  "
            f"spots={line.weekly_spots}={line.total_spots}  "
            f"desc={line.get_description()}"
        )
