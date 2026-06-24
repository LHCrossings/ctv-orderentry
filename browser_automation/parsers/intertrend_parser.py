"""
Intertrend Communications Order Parser
Parses Intertrend agency insertion order PDFs (Brand Time Schedule format)
Similar to Daviselen — weekly spot distribution, Chinese programming, bonus lines
"""

import re
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional

import pdfplumber

_MONTH_MAP = {
    'JAN': ('Jan', 1), 'FEB': ('Feb', 2), 'MAR': ('Mar', 3),
    'APR': ('Apr', 4), 'MAY': ('May', 5), 'JUN': ('Jun', 6),
    'JUL': ('Jul', 7), 'AUG': ('Aug', 8), 'SEP': ('Sep', 9),
    'OCT': ('Oct', 10), 'NOV': ('Nov', 11), 'DEC': ('Dec', 12),
}

_MARKET_MAP = {
    'SF':  'SFO',
    'SFO': 'SFO',
    'LA':  'LAX',
    'LAX': 'LAX',
    'SEA': 'SEA',
    'SAC': 'CVC',
    'CVC': 'CVC',
    'HOU': 'HOU',
    'CHI': 'CMP',
    'CMP': 'CMP',
    'DC':  'WDC',
    'WDC': 'WDC',
    'NYC': 'NYC',
}


@dataclass
class IntertrendLine:
    """One schedule line from an Intertrend Brand Time Schedule."""
    line_number: str
    days: str           # e.g. "W-F" or "M-F"
    time: str           # e.g. "7P-12A"
    program: str        # e.g. "Drama,Ent,Lifestyle" or "B2G1"
    duration: int       # 15 or 30
    dp_code: str        # "RS" = paid, "AV" = bonus
    weekly_spots: List[int]
    total_spots: int
    net_rate: float

    @property
    def is_bonus(self) -> bool:
        return self.dp_code == 'AV' or self.net_rate == 0.0


@dataclass
class IntertrendOrder:
    """Parsed Intertrend insertion order."""
    order_number: str
    client: str
    client_code: str
    product: str
    product_code: str
    estimate: str
    market: str
    flight_start: str       # YYYY-MM-DD from cover page
    flight_end: str         # YYYY-MM-DD
    week_start_dates: List[str]     # ["May 11", "May 18", ...]
    lines: List[IntertrendLine]
    rates_are_net: bool = True      # Intertrend rates are always net


def parse_intertrend_pdf(pdf_path: str) -> IntertrendOrder:
    with pdfplumber.open(pdf_path) as pdf:
        page1_text = pdf.pages[0].extract_text() or ''

        # Schedule page: last page containing "Brand Time Schedule" + "INTERTREND"
        schedule_page_idx = None
        for i in range(len(pdf.pages) - 1, -1, -1):
            text = pdf.pages[i].extract_text() or ''
            if 'Brand Time Schedule' in text and 'INTERTREND' in text.upper():
                schedule_page_idx = i
                break

        if schedule_page_idx is None:
            raise ValueError("Could not find Brand Time Schedule page in PDF")

        schedule_text = pdf.pages[schedule_page_idx].extract_text() or ''

    header = _extract_page1_header(page1_text)
    sched = _extract_schedule_header(schedule_text)
    week_start_dates = _extract_week_dates(schedule_text)

    lines = _extract_lines_with_positions(pdf_path, schedule_page_idx, len(week_start_dates))

    market = _MARKET_MAP.get((sched.get('market_code') or '').upper(), 'SFO')

    flight_start = header.get('flight_start') or sched.get('period_start', '')
    flight_end = header.get('flight_end') or sched.get('period_end', '')

    return IntertrendOrder(
        order_number=header.get('order_number', ''),
        client=header.get('client') or sched.get('client', ''),
        client_code=sched.get('client_code', ''),
        product=header.get('product') or sched.get('product', ''),
        product_code=sched.get('product_code', ''),
        estimate=header.get('estimate') or sched.get('estimate', ''),
        market=market,
        flight_start=flight_start,
        flight_end=flight_end,
        week_start_dates=week_start_dates,
        lines=lines,
        rates_are_net=True,
    )


def _extract_page1_header(text: str) -> dict:
    header: dict = {}

    m = re.search(r'Order#\s*(\d+)', text)
    if m:
        header['order_number'] = m.group(1).lstrip('0') or '0'

    m = re.search(r'Client\s+(.+?)(?:\n|Product)', text)
    if m:
        header['client'] = m.group(1).strip()

    m = re.search(r'Product\s+(.+?)(?:\n|Estimate)', text)
    if m:
        header['product'] = m.group(1).strip()

    m = re.search(r'Estimate\s+(\w+)', text)
    if m:
        header['estimate'] = m.group(1).strip()

    # Flight dates: "5/13/26" or "6/28/26" (MM/DD/YY)
    dates = re.findall(r'(\d{1,2})/(\d{1,2})/(\d{2})', text)
    parsed = []
    for mon, day, yr in dates:
        try:
            parsed.append(datetime(int('20' + yr), int(mon), int(day)))
        except ValueError:
            pass
    if len(parsed) >= 2:
        parsed.sort()
        header['flight_start'] = parsed[0].strftime('%Y-%m-%d')
        header['flight_end'] = parsed[-1].strftime('%Y-%m-%d')

    return header


def _extract_schedule_header(text: str) -> dict:
    header: dict = {}

    # "CLIENT CSL California State Lottery Market SF CA ..."
    m = re.search(r'CLIENT\s+(\w+)\s+(.+?)\s+Market\s+(\w+)', text)
    if m:
        header['client_code'] = m.group(1)
        header['client'] = m.group(2).strip()
        header['market_code'] = m.group(3)

    # "PRODUCT LSSC Late Spring Scratchers"
    m = re.search(r'PRODUCT\s+(\w+)\s+(.+?)(?:\n|ESTIMATE)', text)
    if m:
        header['product_code'] = m.group(1)
        header['product'] = m.group(2).strip()

    # "ESTIMATE 0028"
    m = re.search(r'ESTIMATE\s+(\w+)', text)
    if m:
        header['estimate'] = m.group(1)

    # "PERIOD FROM MAY11/26 TO JUN28/26"
    m = re.search(r'PERIOD FROM ([A-Z]+?)(\d+)/(\d+) TO ([A-Z]+?)(\d+)/(\d+)', text)
    if m:
        for prefix, mon_str, day_str, yr_str, key in [
            ('', m.group(1), m.group(2), m.group(3), 'period_start'),
            ('', m.group(4), m.group(5), m.group(6), 'period_end'),
        ]:
            entry = _MONTH_MAP.get(mon_str.upper()[:3])
            if entry:
                _, mon_num = entry
                try:
                    dt = datetime(int('20' + yr_str), mon_num, int(day_str))
                    header[key] = dt.strftime('%Y-%m-%d')
                except ValueError:
                    pass

    return header


def _extract_week_dates(text: str) -> List[str]:
    """
    Parse week start dates from the Brand Time Schedule header.

    Header spans two lines:
      MAY MAY MAY JUN JUN JUN JUN --GROSS-- ---NET---
      LINE# DAY(S) TIME PROGRAM LEN DP  11  18  25  01  08  15  22  TOT ...
    """
    lines = text.split('\n')

    # Find the line that has repeated month names
    month_names = list(_MONTH_MAP.keys())
    month_line_idx = None
    months_in_order: List[str] = []

    for i, line in enumerate(lines):
        upper = line.upper()
        found = re.findall(r'(?:' + '|'.join(month_names) + r')', upper)
        # Require at least one month name repeated (distinguishes column header from
        # "PERIOD FROM MAY11/26 TO JUN28/26" which only has one of each)
        from collections import Counter
        if len(found) >= 2 and max(Counter(found).values()) >= 2:
            months_in_order = found
            month_line_idx = i
            break

    if month_line_idx is None:
        return []

    # Find the next line that has 1-2 digit numbers (the day numbers)
    day_numbers: List[str] = []
    for line in lines[month_line_idx + 1: month_line_idx + 4]:
        nums = re.findall(r'\b(\d{1,2})\b', line)
        if len(nums) >= len(months_in_order):
            day_numbers = nums[:len(months_in_order)]
            break

    if not day_numbers:
        return []

    week_dates = []
    for month_str, day_str in zip(months_in_order, day_numbers):
        entry = _MONTH_MAP.get(month_str.upper()[:3])
        if entry:
            month_title, _ = entry
            day = day_str.lstrip('0') or '0'
            week_dates.append(f"{month_title} {day}")

    return week_dates


def _extract_lines_with_positions(pdf_path: str, page_idx: int, num_weeks: int) -> List[IntertrendLine]:
    """
    Use x-coordinate positioning to assign spot counts to the correct week columns.

    The text extraction collapses spaces so zeros (empty cells) are invisible —
    positional parsing is the only reliable way to reconstruct the weekly distribution.
    """
    lines: List[IntertrendLine] = []

    with pdfplumber.open(pdf_path) as pdf:
        if page_idx >= len(pdf.pages):
            return lines

        page = pdf.pages[page_idx]
        words = page.extract_words()

        # Locate "TOT" column x-center to bound the week-column search
        tot_x: Optional[float] = None
        for word in words:
            if word['text'] == 'TOT' and word['top'] < 350:
                tot_x = (word['x0'] + word['x1']) / 2
                break

        # Collect 1-2 digit numbers from the header band that fall left of TOT
        week_cols = []
        seen_x: set = set()
        for word in words:
            if (word['text'].isdigit() and
                    len(word['text']) <= 2 and
                    50 < word['top'] < 200 and
                    (tot_x is None or (word['x0'] + word['x1']) / 2 < tot_x - 5) and
                    round(word['x0']) not in seen_x):
                week_cols.append({
                    'text': word['text'],
                    'x0': word['x0'],
                    'x1': word['x1'],
                    'top': word['top'],
                })
                seen_x.add(round(word['x0']))

        week_cols.sort(key=lambda w: w['x0'])

        # Keep only the rightmost num_weeks entries (the actual week day columns)
        if len(week_cols) > num_weeks:
            week_cols = week_cols[-num_weeks:]

        if not week_cols:
            return lines

        # Build narrow x-ranges for each week column
        col_ranges = []
        for i, col in enumerate(week_cols):
            center = (col['x0'] + col['x1']) / 2
            col_ranges.append({
                'week_idx': i,
                'x_min': center - 10,
                'x_max': center + 10,
            })

        # Find data rows: 3-digit line numbers below the header band
        line_data: dict = {}
        for word in words:
            if re.match(r'^\d{3}$', word['text']) and word['top'] > 200:
                lnum = word['text']
                if lnum not in line_data:
                    line_data[lnum] = {
                        'line_number': lnum,
                        'top': word['top'],
                        'weekly_spots': [0] * num_weeks,
                        'words': [],
                    }

        # Group words into their data row (within 5pt vertical tolerance)
        for word in words:
            if word['top'] > 200:
                for data in line_data.values():
                    if abs(word['top'] - data['top']) < 5:
                        data['words'].append(word)
                        break

        # Assign 1-2 digit integers to week columns by x-position
        for data in line_data.values():
            for word in data['words']:
                if word['text'].isdigit() and len(word['text']) <= 2:
                    wx = (word['x0'] + word['x1']) / 2
                    for col in col_ranges:
                        if col['x_min'] <= wx <= col['x_max']:
                            data['weekly_spots'][col['week_idx']] = int(word['text'])
                            break

        # Parse each row's text to extract remaining fields
        for lnum in sorted(line_data.keys()):
            data = line_data[lnum]
            row_words = sorted(data['words'], key=lambda w: w['x0'])
            row_text = ' '.join(w['text'] for w in row_words)

            obj = _parse_line_text(row_text, data['weekly_spots'])
            if obj:
                lines.append(obj)

    return lines


def _parse_line_text(line_text: str, weekly_spots: List[int]) -> Optional[IntertrendLine]:
    """
    Extract fields from a single schedule row.
    Format: "001 W-F 7P-12A Drama,Ent,Lifestyle :30 RS 10 10 N162.00 162.00"
    """
    try:
        parts = line_text.split()
        if len(parts) < 6 or not re.match(r'^\d{3}$', parts[0]):
            return None

        line_number = parts[0].lstrip('0') or '0'
        days = parts[1]
        time_str = parts[2]

        # Scan forward for duration (:15, :30, :60)
        program_parts = []
        duration = None
        dp_code = None
        idx = 3

        while idx < len(parts):
            part = parts[idx]
            if re.match(r'^:\d+$', part):
                duration = int(part[1:])
                idx += 1
                if idx < len(parts) and re.match(r'^[A-Z]{2}$', parts[idx]):
                    dp_code = parts[idx]
                    idx += 1
                break
            program_parts.append(part)
            idx += 1

        if duration is None or dp_code is None:
            return None

        program = ' '.join(program_parts)

        # Net rate = last value with a decimal point in the remaining tokens
        net_rate = 0.0
        for part in reversed(parts[idx:]):
            clean = part.lstrip('N').replace(',', '')
            if '.' in clean:
                try:
                    net_rate = float(clean)
                    break
                except ValueError:
                    pass

        return IntertrendLine(
            line_number=line_number,
            days=days,
            time=time_str,
            program=program,
            duration=duration,
            dp_code=dp_code,
            weekly_spots=weekly_spots,
            total_spots=sum(weekly_spots),
            net_rate=net_rate,
        )
    except Exception:
        return None


def format_time_for_description(time_str: str) -> str:
    """Convert "7P-12A" to "7p-12a" for Etere line descriptions."""
    return time_str.lower()


if __name__ == '__main__':
    import sys

    if len(sys.argv) < 2:
        print("Usage: python intertrend_parser.py <pdf_path>")
        sys.exit(1)

    pdf_path = sys.argv[1]
    print(f"Parsing Intertrend PDF: {pdf_path}\n")

    try:
        order = parse_intertrend_pdf(pdf_path)

        print(f"Order #:  {order.order_number}")
        print(f"Client:   {order.client} ({order.client_code})")
        print(f"Product:  {order.product} ({order.product_code})")
        print(f"Estimate: {order.estimate}")
        print(f"Market:   {order.market}")
        print(f"Flight:   {order.flight_start} – {order.flight_end}")
        print(f"Weeks:    {', '.join(order.week_start_dates)}")
        print(f"Lines:    {len(order.lines)}")
        print()

        for line in order.lines:
            tag = ' [BNS]' if line.is_bonus else ''
            print(f"  {line.line_number}. {line.days} {line.time} :{line.duration} {line.dp_code}{tag}")
            print(f"     Program:  {line.program}")
            print(f"     Net rate: ${line.net_rate:.2f}  |  Total: {line.total_spots} spots")
            print(f"     Weekly:   {line.weekly_spots}")
            print()

    except Exception as e:
        import traceback
        print(f"Error: {e}")
        traceback.print_exc()
