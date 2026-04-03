"""
Hyphen Buy Detail Report Parser

Parses "Buy Detail Report" PDFs from Hyphen (formerly JP Marketing) agency.

PDF Layout (text-based — pdfplumber extracts clean text):
  Header block (repeated on each page):
    Buy Detail Report
    ...
    Client: <Client>  Estimate: <N>  Send Billing To:HYPHEN ...
    Description: <Desc>   Flight Start Date: M/D/YYYY ...
    Market: <City>        Flight End Date:   M/D/YYYY ...
    Separation between spots: <N>  Buyer: <Name>

  Column header area (within each page):
    "...Wks [Spots]"                    ← "Spots" present on pages with total col
    "Dur <M/D> <M/D> ..."               ← date columns for this page

  Data rows (one per program line):
    "<n> <days> <start>- [<end>] <code> $<gross> $<net> <dur> <spot> ..."
    [<end_time>]     ← optional wrapped end time on next line (e.g. "12:00a")
    <Program Name>   ← program name on line after
    [BONUS]          ← optional duplicate label line (ignored)

  Multi-page: same line numbers appear on each page with different week columns.
  Lines are merged by line_number — weekly_spots lists are concatenated.

Key Business Rules:
  - is_bonus: gross_rate == 0.00 and total_spots > 0
  - Time wrapping: "6:00a-" end time may appear on next line as "12:00a"
  - Market: PDF says city name (e.g. "Fresno") — normalised via normalize_market_code()
  - Week dates: M/D format in PDF → "Mon DD" strings for consolidate_weeks()
  - Rate: gross_rate is the advertiser-facing rate; automation uses gross
"""

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from typing import List, Optional, Dict, Any
from pathlib import Path
import re
import sys

_project_root = Path(__file__).parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

import pdfplumber

from browser_automation.parsers.sagent_parser import normalize_market_code


# ─────────────────────────────────────────────────────────────────────────────
# CONSTANTS
# ─────────────────────────────────────────────────────────────────────────────

_MONTH_ABBR = {
    1: 'Jan', 2: 'Feb', 3: 'Mar', 4: 'Apr', 5: 'May', 6: 'Jun',
    7: 'Jul', 8: 'Aug', 9: 'Sep', 10: 'Oct', 11: 'Nov', 12: 'Dec',
}

# Lines in the text that should never be treated as a program name
_SKIP_LINE_RE = re.compile(
    r'^(BONUS|Total\s+Spots|Total\s+Cost|Station\s+Monthly|Page:|'
    r'Signature:|Disclaimer|Terms\s*&|Crossings\s+TV|Buy\s+Detail)',
    re.IGNORECASE,
)

# Regex matching a standalone wrapped end-time line (e.g. "12:00a" or "8:00p")
_TIME_ONLY_RE = re.compile(r'^\d+:\d{2}[ap]$', re.IGNORECASE)

# Main data line regex.
# Groups: (line_no)(days)(time_start_with_dash)(time_end?)(code)(gross)(net)(dur)(spots_str)
_DATA_LINE_RE = re.compile(
    r'^(\d+)\s+'            # 1: line number
    r'([A-Za-z]+)\s+'       # 2: day pattern (e.g. MTuWThF, SaSu, MTuWThFSaSu)
    r'(\d+:\d{2}[ap]-)'     # 3: time start with trailing dash (e.g. "6:00p-")
    r'\s*(\d+:\d{2}[ap])?'  # 4: optional time end (e.g. "8:00p")
    r'\s+([A-Z]{2,3})\s+'   # 5: daypart code (e.g. EN, RT, EF, PA)
    r'\$([\d.]+)\s+'        # 6: gross rate
    r'\$([\d.]+)\s+'        # 7: net rate
    r'(\d+)'                # 8: duration (seconds)
    r'((?:\s+\d+)+)?'       # 9: weekly spot counts (optional, space-separated)
    r'\s*$',
    re.IGNORECASE,
)


# ─────────────────────────────────────────────────────────────────────────────
# DATACLASSES
# ─────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class HyphenLine:
    """
    A single program line item from a Hyphen Buy Detail Report.

    Attributes:
        line_number:      Line number from PDF (1-based)
        days:             Day pattern as printed (e.g. "MTuWThF", "SaSu")
        time_str:         Normalized time range (e.g. "6:00p-8:00p", "6:00a-12:00a")
        daypart_code:     Daypart code from PDF (e.g. "EN", "RT", "EF", "PA")
        gross_rate:       Gross rate per spot (advertiser-facing)
        net_rate:         Net rate per spot (station-facing, ~85% of gross)
        duration:         Spot duration in seconds (e.g. 30)
        program:          Program name (e.g. "Hmong News & Entertainment")
        weekly_spots:     Spots per week, merged across all PDF pages
        total_spots:      Sum of weekly_spots
        week_start_dates: Week start dates as "Mon DD" strings (e.g. ["Apr 6", "Apr 13"])
        is_bonus:         True when gross_rate == 0 and total_spots > 0
        is_billboard:     True when :05/:10 shares time period with a :30 (unused currently)
    """
    line_number: int
    days: str
    time_str: str
    daypart_code: str
    gross_rate: Decimal
    net_rate: Decimal
    duration: int
    program: str
    weekly_spots: List[int]
    total_spots: int
    week_start_dates: List[str]
    is_bonus: bool
    is_billboard: bool

    def get_etere_days(self) -> str:
        from browser_automation.day_utils import to_etere
        return to_etere(self.days)

    def get_etere_time(self) -> str:
        """Return time_str ready for EtereClient.parse_time_range()."""
        return self.time_str

    def get_duration_seconds(self) -> int:
        return self.duration

    def get_description(self, etere_days: str, etere_time: str) -> str:
        """
        Build Etere line description.
          Paid:  "Sa-Su 6-8p Hmong"
          Bonus: "M-Su 6a-12a BNS Hmong"
        Time is compressed (drop :00, share period suffix when same).
        Program is reduced to its first word (the language name).
        """
        short_time = _shorten_time(self.time_str)
        language = self.program.split()[0].title()
        prefix = f"(Line {self.line_number}) "
        if self.is_bonus:
            return f"{prefix}{etere_days} {short_time} BNS {language}"
        return f"{prefix}{etere_days} {short_time} {language}"

    def get_block_prefixes(self) -> List[str]:
        """Infer language block prefixes from program name."""
        p = self.program.lower()
        if "hmong" in p:
            return ["Hm"]
        if "filipino" in p or "tagalog" in p:
            return ["T"]
        if "mandarin" in p:
            return ["M"]
        if "cantonese" in p:
            return ["C"]
        if "vietnamese" in p:
            return ["V"]
        if "korean" in p:
            return ["K"]
        if "japanese" in p:
            return ["J"]
        if "punjabi" in p or "south asian" in p:
            return ["SA"]
        return []


@dataclass(frozen=True)
class HyphenEstimate:
    """
    Complete parsed Hyphen Buy Detail Report order.

    Attributes:
        client:       Advertiser name (e.g. "Department of Pesticide Regulation")
        estimate:     Estimate number string (e.g. "1613")
        description:  Campaign description (e.g. "DPR 2026 Crossings TV")
        product:      Product name (e.g. "Spray Days")
        market:       Normalised market code (e.g. "CVC", "LAX")
        flight_start: Flight start as M/D/YYYY (e.g. "3/30/2026")
        flight_end:   Flight end as M/D/YYYY (e.g. "8/30/2026")
        separation:   Spot separation in minutes from PDF (e.g. 30)
        buyer:        Buyer name (e.g. "Andrea Salvio")
        lines:        Parsed line items, sorted by line_number
    """
    client: str
    estimate: str
    description: str
    product: str
    market: str
    flight_start: str
    flight_end: str
    separation: int
    buyer: str
    lines: List[HyphenLine]

    def get_default_contract_code(self, code_name: str, include_market: bool = False) -> str:
        """Build contract code from customer DB code_name + optional market suffix."""
        if not include_market:
            return code_name
        short = {
            "CVC": "CV", "SFO": "SF", "SEA": "SEA", "LAX": "LA",
            "HOU": "HOU", "CMP": "CMP", "WDC": "WDC", "NYC": "NYC",
        }
        return f"{code_name}{short.get(self.market, self.market)}"

    def get_default_description(self, description_name: str) -> str:
        """Build description from customer DB description_name + estimate number."""
        return f"{description_name} {self.estimate}"


# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _shorten_time(time_str: str) -> str:
    """
    Compress a time range for use in Etere line descriptions.

    "6:00p-8:00p"  → "6-8p"    (same period, drop :00 and leading period)
    "6:00a-12:00a" → "6a-12a"  (same period, drop :00)
    "4:00p-5:00p"  → "4-5p"
    "11:30a-1:00p" → "11:30a-1p"  (different period, keep both; drop :00 only)
    """
    m = re.match(
        r'^(\d+)(?::(\d+))?([ap])-(\d+)(?::(\d+))?([ap])$',
        time_str.strip(),
        re.IGNORECASE,
    )
    if not m:
        return time_str  # unrecognized format, pass through unchanged

    sh, sm, sa, eh, em, ea = m.groups()
    sa, ea = sa.lower(), ea.lower()

    def _fmt(h: str, m_str: Optional[str]) -> str:
        """Format hour[:min], dropping :00."""
        if m_str and m_str != "00":
            return f"{h}:{m_str}"
        return h

    start = _fmt(sh, sm)
    end   = _fmt(eh, em)

    if sa == ea:
        # Same period — prefix on end only: "6-8p"
        return f"{start}-{end}{ea}"
    else:
        # Different period — keep both: "11:30a-1p"
        return f"{start}{sa}-{end}{ea}"


def _extract_field(text: str, pattern: str, default: str = "") -> str:
    m = re.search(pattern, text, re.IGNORECASE | re.MULTILINE)
    return m.group(1).strip() if m else default


def _parse_date_field(raw: str) -> str:
    """
    Extract M/D/YYYY from a field that may include a time component.
    e.g. "3/30/2026 03:00 AM" → "3/30/2026"
    """
    m = re.search(r'(\d{1,2}/\d{1,2}/\d{4})', raw)
    return m.group(1) if m else raw.strip()


def _md_to_mon_dd(date_str: str) -> str:
    """Convert "4/6" → "Apr 6"."""
    parts = date_str.strip().split('/')
    if len(parts) == 2:
        month = int(parts[0])
        day = int(parts[1])
        return f"{_MONTH_ABBR[month]} {day}"
    return date_str


def _parse_header(text: str) -> Dict[str, Any]:
    """Extract header fields from the first page text."""
    client = _extract_field(text, r'Client:\s*(.+?)\s+Estimate:')
    estimate = _extract_field(text, r'Estimate:\s*(\d+)')
    # Description is on the "Media: TV Description: ..." line, followed by the agency
    # street address (e.g. "677 W Palmdon Dr"). Stop before the street number: a 3+ digit
    # number followed by a single uppercase letter (street direction) + space.
    description = _extract_field(text, r'Description:\s*(.*?)\s+\d{3,}\s+[A-Z]\s')
    # Product is on the same line as "Flight Start Date:"
    product = _extract_field(text, r'Product:\s*(.*?)\s+Flight\s+Start\s+Date:')
    market_raw = _extract_field(text, r'Market:\s*(.+?)\s+(?:Flight|Survey)')
    market = normalize_market_code(market_raw)

    flight_start_raw = _extract_field(text, r'Flight\s+Start\s+Date:\s*(.+?)(?:\s+Suite|\s+Fresno|\s+Phone|\n)')
    flight_end_raw = _extract_field(text, r'Flight\s+End\s+Date:\s*(.+?)(?:\s+Suite|\s+Fresno|\s+Phone|\n)')
    flight_start = _parse_date_field(flight_start_raw)
    flight_end = _parse_date_field(flight_end_raw)

    sep_str = _extract_field(text, r'Separation\s+between\s+spots:\s*(\d+)')
    separation = int(sep_str) if sep_str.isdigit() else 15

    buyer = _extract_field(text, r'Buyer:\s*(.+?)(?:\s+Fax:|\s*$)', "")

    return {
        'client': client,
        'estimate': estimate,
        'description': description,
        'product': product,
        'market': market,
        'flight_start': flight_start,
        'flight_end': flight_end,
        'separation': separation,
        'buyer': buyer,
    }


def _parse_page_week_header(text_lines: List[str]):
    """
    Find the "Dur <date> <date>..." line and detect whether a "Total Spots" column
    is present (last column).

    Returns:
        (week_dates: List[str], has_total_col: bool)
        week_dates are "Mon DD" strings, e.g. ["Apr 6", "Apr 13"].
        Returns ([], False) if no Dur line found on this page.
    """
    dur_idx = -1
    for i, line in enumerate(text_lines):
        if re.match(r'^\s*Dur\s+\d', line):
            dur_idx = i
            break

    if dur_idx < 0:
        return [], False

    dur_line = text_lines[dur_idx]
    raw_dates = re.findall(r'\b(\d{1,2}/\d{1,2})\b', dur_line)
    week_dates = [_md_to_mon_dd(d) for d in raw_dates]

    # Check for "Spots" in the column header line(s) above the Dur line
    has_total_col = False
    for look in range(max(0, dur_idx - 3), dur_idx):
        if re.search(r'\bSpots\b', text_lines[look], re.IGNORECASE):
            has_total_col = True
            break

    return week_dates, has_total_col


def _parse_page_lines(text_lines: List[str], n_weeks: int, has_total_col: bool) -> List[Dict]:
    """
    Parse data lines from one page's text.

    Returns a list of dicts with keys:
        line_number, days, time_str, daypart_code, gross_rate, net_rate,
        duration, program, weekly_spots
    """
    results = []
    i = 0

    while i < len(text_lines):
        line = text_lines[i].strip()
        m = _DATA_LINE_RE.match(line)
        if not m:
            i += 1
            continue

        line_number  = int(m.group(1))
        days         = m.group(2)
        time_start   = m.group(3)           # e.g. "6:00p-"
        time_end     = m.group(4)           # e.g. "8:00p" or None
        daypart_code = m.group(5)
        gross_rate   = Decimal(m.group(6)).quantize(Decimal("0.01"), ROUND_HALF_UP)
        net_rate     = Decimal(m.group(7)).quantize(Decimal("0.01"), ROUND_HALF_UP)
        duration     = int(m.group(8))
        spots_raw    = (m.group(9) or "").strip()
        spot_nums    = [int(x) for x in spots_raw.split() if x.strip().isdigit()]

        # Advance past the matched line to look ahead
        look = i + 1

        # Wrapped end time: if missing, next non-empty line may be "12:00a"
        if time_end is None and look < len(text_lines):
            candidate = text_lines[look].strip()
            if _TIME_ONLY_RE.match(candidate):
                time_end = candidate
                look += 1

        # Build normalized time string
        base_start = time_start.rstrip('-')  # "6:00p"
        time_str = f"{base_start}-{time_end}" if time_end else f"{base_start}-23:59"

        # Program name: first useful line after the data (and any time continuation)
        program = ""
        for j in range(look, min(look + 4, len(text_lines))):
            candidate = text_lines[j].strip()
            if not candidate:
                continue
            if _SKIP_LINE_RE.match(candidate):
                break
            if _TIME_ONLY_RE.match(candidate):
                continue
            if _DATA_LINE_RE.match(candidate):
                break  # Next data line already
            program = candidate
            break

        # Extract weekly spots, stripping total column if present
        if has_total_col and len(spot_nums) == n_weeks + 1:
            weekly_spots = spot_nums[:n_weeks]
        elif has_total_col and len(spot_nums) > n_weeks:
            weekly_spots = spot_nums[:n_weeks]
        else:
            weekly_spots = spot_nums

        results.append({
            'line_number':  line_number,
            'days':         days,
            'time_str':     time_str,
            'daypart_code': daypart_code,
            'gross_rate':   gross_rate,
            'net_rate':     net_rate,
            'duration':     duration,
            'program':      program,
            'weekly_spots': weekly_spots,
        })

        i += 1

    return results


# ─────────────────────────────────────────────────────────────────────────────
# MAIN PARSER
# ─────────────────────────────────────────────────────────────────────────────

def parse_hyphen_pdf(pdf_path: str) -> HyphenEstimate:
    """
    Parse a Hyphen Buy Detail Report PDF.

    Handles multi-page PDFs where each page shows the same lines for a
    different set of week columns.  Lines are merged by line_number.

    Args:
        pdf_path: Path to the PDF file.

    Returns:
        HyphenEstimate with all lines populated.

    Raises:
        ValueError: If critical fields cannot be parsed.
    """
    print(f"\n[HYPHEN PARSER] Reading: {pdf_path}")

    header: Dict[str, Any] = {}
    # line_number → accumulated dict (weekly_spots and week_start_dates grow per page)
    merged: Dict[int, Dict] = {}

    with pdfplumber.open(pdf_path) as pdf:
        for page_idx, page in enumerate(pdf.pages):
            text = page.extract_text() or ""
            text_lines = text.split('\n')

            # Parse header fields from first page only
            if page_idx == 0:
                header = _parse_header(text)
                print(f"[HYPHEN PARSER] Client:      {header['client']}")
                print(f"[HYPHEN PARSER] Estimate:    {header['estimate']}")
                print(f"[HYPHEN PARSER] Description: {header['description']}")
                print(f"[HYPHEN PARSER] Market:      {header['market']}")
                print(f"[HYPHEN PARSER] Flight:      {header['flight_start']} – {header['flight_end']}")

            # Extract week date columns and total-col flag for this page
            week_dates, has_total_col = _parse_page_week_header(text_lines)
            if not week_dates:
                print(f"[HYPHEN PARSER] Page {page_idx + 1}: no data columns, skipping")
                continue

            n_weeks = len(week_dates)
            print(f"[HYPHEN PARSER] Page {page_idx + 1}: {n_weeks} weeks {week_dates} "
                  f"({'total col' if has_total_col else 'no total col'})")

            # Parse data lines for this page
            page_lines = _parse_page_lines(text_lines, n_weeks, has_total_col)

            for pline in page_lines:
                ln = pline['line_number']
                if ln not in merged:
                    merged[ln] = {**pline, 'week_start_dates': list(week_dates)}
                else:
                    # Extend with this page's spots and dates
                    merged[ln]['weekly_spots'].extend(pline['weekly_spots'])
                    merged[ln]['week_start_dates'].extend(week_dates)
                    # Update program if we didn't get it on the first page
                    if not merged[ln]['program'] and pline['program']:
                        merged[ln]['program'] = pline['program']

    if not merged:
        raise ValueError(f"No lines parsed from Hyphen PDF: {pdf_path}")

    # Build HyphenLine objects
    lines: List[HyphenLine] = []
    for ln in sorted(merged.keys()):
        d = merged[ln]
        total = sum(d['weekly_spots'])
        is_bonus = d['gross_rate'] == Decimal("0.00") and total > 0

        hl = HyphenLine(
            line_number=ln,
            days=d['days'],
            time_str=d['time_str'],
            daypart_code=d['daypart_code'],
            gross_rate=d['gross_rate'],
            net_rate=d['net_rate'],
            duration=d['duration'],
            program=d['program'],
            weekly_spots=d['weekly_spots'],
            total_spots=total,
            week_start_dates=d['week_start_dates'],
            is_bonus=is_bonus,
            is_billboard=False,
        )
        lines.append(hl)
        rate_str = 'BONUS' if is_bonus else f"${d['gross_rate']}"
        print(f"[HYPHEN PARSER] Line {ln}: {d['program']!r} "
              f"{rate_str} days={d['days']} spots={d['weekly_spots']} total={total}")

    if not header:
        raise ValueError("Header fields could not be parsed")

    return HyphenEstimate(
        client=header.get('client', ''),
        estimate=header.get('estimate', ''),
        description=header.get('description', ''),
        product=header.get('product', ''),
        market=header.get('market', ''),
        flight_start=header.get('flight_start', ''),
        flight_end=header.get('flight_end', ''),
        separation=header.get('separation', 15),
        buyer=header.get('buyer', ''),
        lines=lines,
    )


# ─────────────────────────────────────────────────────────────────────────────
# STANDALONE TEST
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys as _sys

    if len(_sys.argv) < 2:
        print("Usage: python hyphen_parser.py <pdf_path>")
        _sys.exit(1)

    try:
        order = parse_hyphen_pdf(_sys.argv[1])

        print("\n" + "=" * 70)
        print("HYPHEN ESTIMATE SUMMARY")
        print("=" * 70)
        print(f"Client:      {order.client}")
        print(f"Estimate:    {order.estimate}")
        print(f"Description: {order.description}")
        print(f"Product:     {order.product}")
        print(f"Market:      {order.market}")
        print(f"Flight:      {order.flight_start} – {order.flight_end}")
        print(f"Separation:  {order.separation} min")
        print(f"Buyer:       {order.buyer}")
        print(f"Total Lines: {len(order.lines)}")
        print(f"Total Spots: {sum(l.total_spots for l in order.lines)}")

        print("\n" + "=" * 70)
        print("LINES")
        print("=" * 70)
        for line in order.lines:
            d = line.get_etere_days()
            t = line.get_etere_time()
            print(f"\nLine {line.line_number}: {line.program}")
            print(f"  Days:    {line.days!r}  →  {d}")
            print(f"  Time:    {line.time_str}  →  {t}")
            print(f"  Code:    {line.daypart_code}  |  Dur: {line.duration}s")
            print(f"  Gross:   ${line.gross_rate}  |  Net: ${line.net_rate}"
                  f"  {'[BONUS]' if line.is_bonus else ''}")
            print(f"  Weeks ({len(line.week_start_dates)}): {line.week_start_dates}")
            print(f"  Spots:   {line.weekly_spots}  total={line.total_spots}")
            print(f"  Blocks:  {line.get_block_prefixes()}")
            print(f"  Desc:    {line.get_description(d, t)}")

    except Exception as exc:
        print(f"\n✗ Error: {exc}")
        import traceback
        traceback.print_exc()
        _sys.exit(1)
