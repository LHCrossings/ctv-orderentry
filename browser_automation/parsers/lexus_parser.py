"""
Lexus / IW Group Order Parser

Parses Lexus insertion orders from:
  - JPG screenshots (new orders from Melissa)
  - XLSX spreadsheets (revisions from Melissa)

Business Rules:
  - Rates in document are NET; gross = net / 0.85
  - BNS bonus spots: rate = $0, spot_code = 10
  - Broadcast calendar uses week-start day numbers within a broadcast month
  - One file = one market + one language
  - Melissa Check: validate that day patterns have valid placement dates each week
"""

import calendar
import math
import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Optional


# ───────────────────────────────────────────────────────────────────────────
# DATA CLASSES
# ───────────────────────────────────────────────────────────────────────────

@dataclass
class LexusLine:
    program: str
    duration: int                           # seconds: 15 or 30
    time: str                               # e.g. "6A-10A"
    days: str                               # e.g. "M-F", "M,W,R,F"
    rate_net: float
    rate_gross: float                       # rate_net / 0.85
    spots_by_week: list[int]
    week_date_ranges: list[tuple[date, date]]
    market: str
    language: str
    estimate: str
    is_bonus: bool = False


@dataclass
class LexusParseResult:
    lines: list[LexusLine]
    broadcast_month: str                    # e.g. "Jan-26"
    week_headers: list[str]                 # raw day-number tokens from document
    language: Optional[str]
    estimate: str
    market: str
    order_type: str                         # "new" or "revision"


# ───────────────────────────────────────────────────────────────────────────
# FILENAME PARSING
# ───────────────────────────────────────────────────────────────────────────

_MARKET_TOKENS: dict[str, str] = {
    "NYC": "NYC", "NY": "NYC",
    "SF": "SFO", "SFO": "SFO", "SAN FRANCISCO": "SFO",
    "SEA": "SEA", "SEATTLE": "SEA",
    "LA": "LAX", "LAX": "LAX", "LOS ANGELES": "LAX",
    "SAC": "CVC", "SACRAMENTO": "CVC", "CVC": "CVC",
    "HOU": "HOU", "HOUSTON": "HOU",
    "WDC": "WDC", "DC": "WDC", "WASHINGTON": "WDC",
}

_LANGUAGE_TOKENS: dict[str, str] = {
    "ASIAN INDIAN": "Hinglish",
    "ASIAN-INDIAN": "Hinglish",
    "AI": "Hinglish",
    "HINGLISH": "Hinglish",
    "CHINESE": "Chinese",
    "CH": "Chinese",
    "VT": "Viet",
    "VIET": "Viet",
    "VIETNAMESE": "Viet",
}


def parse_lexus_filename(filename: str) -> dict:
    """
    Extract metadata from a Lexus order filename.

    Returns dict with keys:
        order_type: "new" or "revision"
        estimate:   string estimate number, e.g. "202"
        market:     Etere market code, e.g. "NYC"; "" if not found
        language:   Language string, e.g. "Hinglish"; None if not found
    """
    stem = Path(filename).stem
    upper = stem.upper()

    # Order type
    if upper.startswith("NEW ORDER"):
        order_type = "new"
    elif upper.startswith("REVISED") or upper.startswith("REVISION"):
        order_type = "revision"
    else:
        order_type = "new"

    # Estimate number
    est_match = re.search(r'EST[\s_-]*(\d+)', upper)
    estimate = est_match.group(1) if est_match else ""

    # Market: scan tokens
    market = ""
    # Try multi-word markets first
    for token, code in sorted(_MARKET_TOKENS.items(), key=lambda x: -len(x[0])):
        if token in upper:
            market = code
            break

    # Language: scan tokens (longer tokens first to catch "ASIAN INDIAN" before "AI")
    language = None
    for token, lang in sorted(_LANGUAGE_TOKENS.items(), key=lambda x: -len(x[0])):
        if token in upper:
            language = lang
            break

    return {
        "order_type": order_type,
        "estimate": estimate,
        "market": market,
        "language": language,
    }


# ───────────────────────────────────────────────────────────────────────────
# BROADCAST CALENDAR RESOLUTION
# ───────────────────────────────────────────────────────────────────────────

_MONTH_ABBR = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
    "january": 1, "february": 2, "march": 3, "april": 4, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10,
    "november": 11, "december": 12,
}


def _parse_broadcast_month(bm: str) -> tuple[int, int]:
    """
    Parse broadcast month string to (month_int, year_int).

    Accepts: "Jan-26", "January 2026", "Jan 26", "1/2026"
    """
    bm = bm.strip()

    # "Jan-26" or "Jan-2026" or "Jan 26"
    m = re.match(r'([A-Za-z]+)[\s\-]+(\d{2,4})$', bm)
    if m:
        mon_str = m.group(1).lower()
        yr_str = m.group(2)
        month = _MONTH_ABBR.get(mon_str)
        if not month:
            raise ValueError(f"Unknown month: {m.group(1)}")
        year = int(yr_str)
        if year < 100:
            year += 2000
        return month, year

    # "January 2026"
    m = re.match(r'([A-Za-z]+)\s+(\d{4})$', bm)
    if m:
        mon_str = m.group(1).lower()
        month = _MONTH_ABBR.get(mon_str)
        if not month:
            raise ValueError(f"Unknown month: {m.group(1)}")
        return month, int(m.group(2))

    raise ValueError(f"Cannot parse broadcast month: {bm!r}")


def resolve_week_dates(
    broadcast_month: str,
    week_headers: list[str],
) -> list[tuple[date, date]]:
    """
    Convert broadcast-week header tokens into (start, end) date pairs.

    Each header token is either:
      "13"    → start = date(year, month, 13), end = min(start+6, month_end)
      "26-31" → start = date(year, month, 26), end = date(year, month, 31)
      "1/13"  → start = date(year, 1, 13) (month included)

    The end date is capped at month_end (last day of the broadcast month).
    """
    month, year = _parse_broadcast_month(broadcast_month)
    month_end = date(year, month, calendar.monthrange(year, month)[1])

    result: list[tuple[date, date]] = []
    for header in week_headers:
        header = header.strip()

        # "26-31" or "26-Feb3" style
        range_m = re.match(r'^(\d{1,2})-(\d{1,2})$', header)
        if range_m:
            start_day = int(range_m.group(1))
            end_day = int(range_m.group(2))
            start = date(year, month, start_day)
            end = date(year, month, min(end_day, calendar.monthrange(year, month)[1]))
            result.append((start, end))
            continue

        # "1/13" style
        slash_m = re.match(r'^(\d{1,2})/(\d{1,2})$', header)
        if slash_m:
            hdr_month = int(slash_m.group(1))
            hdr_day = int(slash_m.group(2))
            try:
                start = date(year, hdr_month, hdr_day)
            except ValueError:
                start = date(year, month, hdr_day)
            end = min(start + timedelta(days=6), month_end)
            result.append((start, end))
            continue

        # Plain day number "13"
        day_m = re.match(r'^(\d{1,2})$', header)
        if day_m:
            start_day = int(day_m.group(1))
            try:
                start = date(year, month, start_day)
            except ValueError:
                result.append((month_end, month_end))
                continue
            end = min(start + timedelta(days=6), month_end)
            result.append((start, end))
            continue

        # Unrecognised — skip with a warning placeholder
        print(f"[LEXUS PARSER] ⚠ Cannot parse week header: {header!r}")
        result.append((month_end, month_end))

    return result


# ───────────────────────────────────────────────────────────────────────────
# MELISSA CHECK
# ───────────────────────────────────────────────────────────────────────────

# EtereClient day_ids index → Python weekday() mapping
# EtereClient: 0=Sunday, 1=Monday, 2=Tue, 3=Wed, 4=Thu, 5=Fri, 6=Sat
# Python weekday(): 0=Monday, 1=Tue, 2=Wed, 3=Thu, 4=Fri, 5=Sat, 6=Sunday
_ETERE_IDX_TO_PYTHON_WD = {0: 6, 1: 0, 2: 1, 3: 2, 4: 3, 5: 4, 6: 5}

# Replicate EtereClient._parse_day_codes logic here to avoid circular imports
_CODE_TO_IDX = {'M': 1, 'T': 2, 'W': 3, 'R': 4, 'F': 5, 'S': 6, 'U': 0}
_ALIASES = {'Sa': 'S', 'SAT': 'S', 'Su': 'U', 'SU': 'U', 'Sun': 'U', 'SUN': 'U'}
_WEEK_SEQ = ['M', 'T', 'W', 'R', 'F', 'S', 'U']


def _parse_day_codes(days: str) -> list[int]:
    """Return sorted list of EtereClient day_ids indices from a day pattern string."""
    days = days.strip()
    indices: set[int] = set()

    def _resolve(code: str) -> int:
        code = _ALIASES.get(code, code)
        return _CODE_TO_IDX[code]

    m = re.match(r'^([A-Za-z]+)-([A-Za-z]+)$', days)
    if m:
        start = _ALIASES.get(m.group(1), m.group(1))
        end = _ALIASES.get(m.group(2), m.group(2))
        if start in _WEEK_SEQ and end in _WEEK_SEQ:
            si, ei = _WEEK_SEQ.index(start), _WEEK_SEQ.index(end)
            for code in _WEEK_SEQ[si:ei + 1]:
                indices.add(_CODE_TO_IDX[code])
        else:
            return list(range(7))
    else:
        for part in days.split(','):
            part = part.strip()
            try:
                indices.add(_resolve(part))
            except KeyError:
                pass

    return sorted(indices) if indices else list(range(7))


def melissa_check(lines: list[LexusLine]) -> list[str]:
    """
    Validate that each week's day pattern has at least one valid placement date.

    Returns list of warning strings (empty = no problems).
    """
    warnings: list[str] = []

    for line in lines:
        if line.is_bonus:
            continue  # BNS spots are flexible; skip check

        etere_indices = _parse_day_codes(line.days)
        python_wds = {_ETERE_IDX_TO_PYTHON_WD[i] for i in etere_indices}

        for week_idx, (spots, (wk_start, wk_end)) in enumerate(
            zip(line.spots_by_week, line.week_date_ranges)
        ):
            if spots <= 0:
                continue

            # Enumerate valid dates in this week's range
            valid_dates = []
            d = wk_start
            while d <= wk_end:
                if d.weekday() in python_wds:
                    valid_dates.append(d)
                d += timedelta(days=1)

            prefix = (
                f"[MELISSA] EST {line.estimate} | {line.program} | "
                f"{line.days} {line.time} | Week {week_idx + 1} "
                f"({wk_start} - {wk_end})"
            )

            if not valid_dates:
                warnings.append(
                    f"{prefix}: {spots} spots but NO valid placement days for '{line.days}'"
                )
            elif spots > len(valid_dates):
                warnings.append(
                    f"{prefix}: {spots} spots > {len(valid_dates)} available days"
                )

    return warnings


# ───────────────────────────────────────────────────────────────────────────
# JPG PARSING (OCR)
# ───────────────────────────────────────────────────────────────────────────

def _cluster_by_y(words: list[dict], tolerance: int = 8) -> list[list[dict]]:
    """Group OCR word dicts into rows based on top-y proximity."""
    if not words:
        return []
    sorted_words = sorted(words, key=lambda w: w['top'])
    rows: list[list[dict]] = []
    current_row: list[dict] = [sorted_words[0]]
    current_y = sorted_words[0]['top']

    for word in sorted_words[1:]:
        if abs(word['top'] - current_y) <= tolerance:
            current_row.append(word)
        else:
            rows.append(sorted(current_row, key=lambda w: w['left']))
            current_row = [word]
            current_y = word['top']

    if current_row:
        rows.append(sorted(current_row, key=lambda w: w['left']))

    return rows


def _row_text(row: list[dict]) -> str:
    """Join words in a row into a single string."""
    return " ".join(w['text'] for w in row if w['text'].strip())


def _find_broadcast_month_in_rows(rows: list[list[dict]]) -> Optional[str]:
    """Scan rows for a cell containing a month name + year."""
    month_pattern = re.compile(
        r'\b(january|february|march|april|may|june|july|august|september|'
        r'october|november|december)\b[\s\-]*(\d{2,4})',
        re.IGNORECASE
    )
    for row in rows:
        text = _row_text(row)
        m = month_pattern.search(text)
        if m:
            # Normalise to "Mon-YY" format
            mon_str = m.group(1)[:3].capitalize()
            yr = m.group(2)
            if len(yr) == 4:
                yr = yr[2:]
            return f"{mon_str}-{yr}"
    return None


def _extract_week_headers(row: list[dict]) -> list[str]:
    """
    From a header row, extract the week-start day-number tokens.

    Looks for tokens that are:
      - Pure integers (1-31)
      - "dd-dd" ranges
      - "m/dd" month/day patterns
    """
    headers = []
    for word in row:
        t = word['text'].strip()
        if re.match(r'^\d{1,2}$', t) and 1 <= int(t) <= 31:
            headers.append(t)
        elif re.match(r'^\d{1,2}-\d{1,2}$', t):
            headers.append(t)
        elif re.match(r'^\d{1,2}/\d{1,2}$', t):
            headers.append(t)
    return headers


def _parse_rate(text: str) -> Optional[float]:
    """Extract a dollar amount from a text string."""
    m = re.search(r'\$?\s*(\d[\d,]*\.?\d*)', text.replace(',', ''))
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            pass
    return None


def _parse_duration(text: str) -> Optional[int]:
    """Parse spot duration from ':15', ':30', '15', '30' → seconds int."""
    m = re.search(r':?(\d+)\s*(?:sec|s)?', text)
    if m:
        val = int(m.group(1))
        if val in (15, 30, 60):
            return val
    return None


def _looks_like_time(text: str) -> bool:
    """Check if text looks like a time range, e.g. '6A-10A', '2-3p', '330-4p'."""
    # Both ends have suffix: "6A-10A", "11:30A-12P"
    if re.match(r'\d+[AaPp]M?[-–]\d+[AaPp]M?', text):
        return True
    # Suffix only at end (IW Group format): "2-3p", "330-4p", "1-130p"
    if re.match(r'^\d{1,4}(?::\d{2})?[-–]\d{1,4}(?::\d{2})?[AaPp][Mm]?$', text):
        return True
    return False


def _looks_like_days(text: str) -> bool:
    """Check if text looks like a day pattern, e.g. 'M-F', 'M,W,R'."""
    return bool(re.match(r'^[MTWRFSU][MTWRFSU\-,]*$', text, re.IGNORECASE))


def _parse_data_row_ocr(
    row: list[dict],
    week_col_lefts: list[int],
    col_tolerance: int = 20,
) -> Optional[dict]:
    """
    Parse a data row from OCR output.

    Returns dict with: program, duration, days, time, rate_net, spots_by_week
    or None if the row doesn't look like a data row.
    """
    text = _row_text(row)
    if not text.strip():
        return None

    # Skip summary / total rows early
    text_upper = text.upper()
    if any(kw in text_upper for kw in ("TOTAL PAID", "TOTAL BONUS", "GRAND TOTAL",
                                        "WEEKLY PAID", "WEEKLY SPEND", "PROGRAM NAME")):
        return None

    # Detect bonus row
    is_bonus = "BNS" in text_upper or "BONUS" in text_upper

    # Find duration token (:15 or :30) and its position in the word list
    duration = None
    duration_word_idx = None
    for word_idx, word in enumerate(row):
        d = _parse_duration(word['text'])
        if d:
            duration = d
            duration_word_idx = word_idx
            break
    if duration is None:
        duration = 30  # default

    # Collect program name: all words BEFORE the duration token.
    # IW Group programs embed days+time in the name ("M-F 2-3p Punjabi News"),
    # so don't stop at day/time tokens — let _extract_days/time_from_program handle it.
    program_words = []
    for word_idx, word in enumerate(row):
        if duration_word_idx is not None and word_idx >= duration_word_idx:
            break
        t = word['text'].strip()
        if not t:
            continue
        if '$' in t or re.match(r'^\d+\.\d{2}$', t):
            break
        # Stop at what looks like an isolated small integer (spot count column)
        if re.match(r'^\d{1,2}$', t) and 1 <= int(t) <= 31:
            break
        program_words.append(t)
    program = " ".join(program_words)

    # Extract days and time from assembled program name (same logic as XLSX parser)
    days_str = _extract_days_from_program(program) if program else "M-F"
    time_str = _extract_time_from_program(program) if program else ""

    # Fallback: scan row tokens individually for time if extraction failed
    if not time_str:
        for word in row:
            if _looks_like_time(word['text']):
                time_str = word['text'].upper()
                break

    # Try to find rate
    rate_net = 0.0
    if not is_bonus:
        for word in row:
            t = word['text']
            if '$' in t or re.match(r'^\d+\.\d{2}$', t):
                r = _parse_rate(t)
                if r and r > 5:  # rates are typically > $5
                    rate_net = r
                    break

    # Extract spots per week: find word closest to each week column
    spots_by_week = []
    for col_left in week_col_lefts:
        best_word = None
        best_dist = 9999
        for word in row:
            dist = abs(word['left'] - col_left)
            if dist < best_dist and dist < col_tolerance * 3:
                best_dist = dist
                best_word = word
        if best_word:
            t = best_word['text'].strip()
            try:
                spots_by_week.append(int(t))
            except ValueError:
                spots_by_week.append(0)
        else:
            spots_by_week.append(0)

    # Only accept row if it has at least one non-zero spot count
    if not time_str and not any(s > 0 for s in spots_by_week):
        return None

    return {
        "program": program,
        "duration": duration,
        "days": days_str,
        "time": time_str,
        "rate_net": rate_net,
        "spots_by_week": spots_by_week,
        "is_bonus": is_bonus,
    }


def _find_date_range_row_ocr(rows: list[list[dict]]) -> Optional[list[dict]]:
    """
    Find the OCR row containing multiple 'M/D-M/D' date-range tokens.

    Used in multi-month orders (e.g. 1/20-1/30, 2/10-2/27, 3/4-3/30).
    Returns the row word list, or None if not found.
    """
    dr_re = re.compile(r'^\d{1,2}/\d{1,2}-\d{1,2}/\d{1,2}$')
    best_row: Optional[list[dict]] = None
    best_count = 0
    for row in rows:
        count = sum(1 for w in row if dr_re.match(w['text'].strip()))
        if count > best_count:
            best_count = count
            best_row = row
    return best_row if best_count >= 2 else None


def _build_week_dates_from_date_range_row(
    date_range_row: list[dict],
    week_col_lefts: list[int],
    week_headers: list[str],
    year: int,
) -> list[tuple[date, date]]:
    """
    Build week date ranges for a multi-month order by mapping each week column's
    x-position to the nearest 'M/D-M/D' date-range cell to its left.

    The date-range row has cells like '1/20-1/30', '2/10-2/27', '3/4-3/30',
    each spanning multiple week columns.  For each week column we find the
    rightmost date-range cell whose left edge is ≤ column left, giving us
    the month.  The day-number comes from the week header token.
    """
    dr_re = re.compile(r'^(\d{1,2})/(\d{1,2})-(\d{1,2})/(\d{1,2})$')
    dr_words = sorted(
        [w for w in date_range_row if dr_re.match(w['text'].strip())],
        key=lambda w: w['left'],
    )

    result: list[tuple[date, date]] = []

    for col_left, wh in zip(week_col_lefts, week_headers):
        # Rightmost date-range word whose left edge is at or before this column
        owning_dr = dr_words[0] if dr_words else None
        for dr_word in dr_words:
            if dr_word['left'] <= col_left + 30:  # 30px tolerance for centering
                owning_dr = dr_word

        if owning_dr is None:
            result.append((date.today(), date.today()))
            continue

        m = dr_re.match(owning_dr['text'].strip())
        if not m:
            result.append((date.today(), date.today()))
            continue

        start_month = int(m.group(1))

        range_m = re.match(r'^(\d{1,2})-(\d{1,2})$', wh)
        plain_m = re.match(r'^(\d{1,2})$', wh)

        try:
            if range_m:
                start_day = int(range_m.group(1))
                end_day = int(range_m.group(2))
                wk_start = date(year, start_month, start_day)
                max_day = calendar.monthrange(year, start_month)[1]
                wk_end = date(year, start_month, min(end_day, max_day))
            elif plain_m:
                start_day = int(plain_m.group(1))
                wk_start = date(year, start_month, start_day)
                max_day = calendar.monthrange(year, start_month)[1]
                wk_end = min(wk_start + timedelta(days=6),
                             date(year, start_month, max_day))
            else:
                wk_start = wk_end = date(year, start_month, 1)
        except ValueError:
            wk_start = wk_end = date(year, start_month, 1)

        result.append((wk_start, wk_end))

    return result


def parse_lexus_jpg(path: str | Path, filename_meta: Optional[dict] = None) -> LexusParseResult:
    """
    Parse a Lexus insertion order from a JPG/PNG screenshot via OCR.

    Args:
        path: Path to the image file
        filename_meta: Optional dict from parse_lexus_filename (skips re-parsing)

    Returns:
        LexusParseResult
    """
    try:
        import pytesseract
        from PIL import Image
        from pytesseract import Output
    except ImportError as e:
        raise ImportError(f"pytesseract and Pillow are required for JPG parsing: {e}")

    path = Path(path)
    meta = filename_meta or parse_lexus_filename(path.name)

    img = Image.open(path)

    # OCR with position data
    data = pytesseract.image_to_data(img, output_type=Output.DICT, config='--psm 6')

    # Build word dicts (filter out low-confidence and empty words)
    words = []
    for i, text in enumerate(data['text']):
        if not text.strip():
            continue
        if int(data['conf'][i]) < 20:
            continue
        words.append({
            'text': text,
            'left': data['left'][i],
            'top': data['top'][i],
            'width': data['width'][i],
            'height': data['height'][i],
        })

    rows = _cluster_by_y(words)

    # Find broadcast month
    broadcast_month = _find_broadcast_month_in_rows(rows)
    if not broadcast_month:
        # Try full image text as fallback
        full_text = pytesseract.image_to_string(img)
        m = re.search(
            r'(january|february|march|april|may|june|july|august|september|'
            r'october|november|december)[\s\-]*(\d{2,4})',
            full_text, re.IGNORECASE
        )
        if m:
            broadcast_month = f"{m.group(1)[:3].capitalize()}-{m.group(2)[-2:]}"
        else:
            broadcast_month = "Unknown"

    # Try to find language in OCR text if not in filename
    language = meta.get("language")
    if not language:
        full_text_lower = pytesseract.image_to_string(img).lower()
        if "hinglish" in full_text_lower or "asian indian" in full_text_lower:
            language = "Hinglish"
        elif "chinese" in full_text_lower or "mandarin" in full_text_lower or "cantonese" in full_text_lower:
            language = "Chinese"
        elif "vietnamese" in full_text_lower or "viet" in full_text_lower:
            language = "Viet"

    # Find header row with week numbers — pick the row with the MOST integer tokens.
    # Requiring ≥4 avoids firing on the UNIT column (:15/:30 → OCR reads as "15"/"30").
    week_headers: list[str] = []
    header_row_idx = -1
    best_wh_count = 0
    for idx, row in enumerate(rows):
        wh = _extract_week_headers(row)
        if len(wh) > best_wh_count:
            best_wh_count = len(wh)
            week_headers = wh
            header_row_idx = idx
    if best_wh_count < 4:
        print(f"[LEXUS PARSER] ⚠ Week header row found only {best_wh_count} tokens — may be wrong")

    # Get x-positions of week columns for data extraction
    week_col_lefts: list[int] = []
    if header_row_idx >= 0:
        header_row = rows[header_row_idx]
        for word in header_row:
            t = word['text'].strip()
            if (re.match(r'^\d{1,2}$', t) and 1 <= int(t) <= 31) or \
               re.match(r'^\d{1,2}-\d{1,2}$', t) or \
               re.match(r'^\d{1,2}/\d{1,2}$', t):
                week_col_lefts.append(word['left'])

    # Resolve week date ranges.
    # Multi-month orders (e.g. Jan–Apr) have a 'M/D-M/D' date-range row instead of
    # a single broadcast month — detect and use it first.
    year = _infer_year_from_filename(path, default=date.today().year)
    week_date_ranges: list[tuple[date, date]] = []

    date_range_row = _find_date_range_row_ocr(rows)
    if date_range_row and week_col_lefts:
        week_date_ranges = _build_week_dates_from_date_range_row(
            date_range_row, week_col_lefts, week_headers, year
        )
        if week_date_ranges:
            first_start = week_date_ranges[0][0]
            broadcast_month = first_start.strftime("%b-%y")
            print(f"[LEXUS PARSER] ✓ Multi-month date range row found; "
                  f"first week → {broadcast_month}")
    elif broadcast_month != "Unknown" and week_headers:
        try:
            week_date_ranges = resolve_week_dates(broadcast_month, week_headers)
        except Exception as e:
            print(f"[LEXUS PARSER] ⚠ Could not resolve week dates: {e}")

    # Pad to match week count
    n_weeks = len(week_headers)
    while len(week_date_ranges) < n_weeks:
        week_date_ranges.append((date.today(), date.today()))

    # Parse data rows (rows after header)
    lines: list[LexusLine] = []
    start_row = header_row_idx + 1 if header_row_idx >= 0 else 0
    for row in rows[start_row:]:
        row_data = _parse_data_row_ocr(row, week_col_lefts)
        if row_data is None:
            continue

        # Pad spots_by_week to n_weeks
        spots = row_data["spots_by_week"]
        while len(spots) < n_weeks:
            spots.append(0)
        spots = spots[:n_weeks]

        rate_net = row_data["rate_net"]
        rate_gross = round(rate_net / 0.85, 2)

        line = LexusLine(
            program=row_data["program"],
            duration=row_data["duration"],
            time=row_data["time"],
            days=row_data["days"],
            rate_net=rate_net,
            rate_gross=rate_gross,
            spots_by_week=spots,
            week_date_ranges=week_date_ranges,
            market=meta.get("market", ""),
            language=language or "",
            estimate=meta.get("estimate", ""),
            is_bonus=row_data["is_bonus"],
        )
        lines.append(line)

    return LexusParseResult(
        lines=lines,
        broadcast_month=broadcast_month,
        week_headers=week_headers,
        language=language,
        estimate=meta.get("estimate", ""),
        market=meta.get("market", ""),
        order_type=meta.get("order_type", "new"),
    )


# ───────────────────────────────────────────────────────────────────────────
# XLSX PARSING
# ───────────────────────────────────────────────────────────────────────────

def _cell_str(cell) -> str:
    """Get cell value as stripped string, handling None."""
    if cell is None or cell.value is None:
        return ""
    return str(cell.value).strip()


def _row_cells_str(row) -> list[str]:
    """Convert an openpyxl row to a list of string cell values."""
    return [_cell_str(c) for c in row]


def _extract_days_from_program(program: str) -> str:
    """
    Extract the day pattern from an IW Group program name.

    Examples:
        "M-Sun 8p-9P Shanghai TV..." → "M-Su"
        "Mon-Friday 10p-11:30p..."   → "M-F"
        "Sat 9-1030p..."             → "Sa"
        "Sat-Sun 1030p-12m..."       → "Sa-Su"
        "M-SAT 6A-7A NEWS"           → "M-Sa"
        "M-SU 8P-12M PRIMEBREAK"     → "M-Su"
    """
    prog_upper = program.strip().upper().lstrip()

    # Multi-word day patterns (must check before single-word)
    day_map = [
        (r'^MON[\-\s]+FRI(DAY)?', "M-F"),
        (r'^M[\-\s]+SUN', "M-Su"),
        (r'^M[\-\s]+SU\b', "M-Su"),
        (r'^M[\-\s]+SAT', "M-Sa"),
        (r'^M[\-\s]+F\b', "M-F"),
        (r'^M[\-\s]+TH\b', "M-R"),
        (r'^SAT[\-\s]+SUN', "Sa-Su"),
        (r'^SAT[\-\s]+SU\b', "Sa-Su"),
        (r'^TU[\-\s]+SU\b', "T-Su"),
        (r'^SAT\b', "Sa"),
        (r'^SUN\b', "Su"),
        (r'^M\b', "M-F"),         # bare "M" at start — assume M-F
    ]
    for pattern, result in day_map:
        if re.match(pattern, prog_upper):
            return result
    return "M-F"   # default


def _extract_time_from_program(program: str) -> str:
    """
    Extract a time range from an IW Group program name.

    Examples:
        "M-Sun 8p-9P Shanghai TV"  → "8P-9P"
        "M-SAT 6A-7A NEWS"         → "6A-7A"
        "Mon-Friday 10p-11:30p..." → "10P-11:30P"
        "M-F 1130P-12M"            → "11:30P-12M"
        "Sat 9-1030p..."           → "9P-10:30P"
        "M-F 7-8P NEWS/TALK"       → "7P-8P"
        "M-F HINDI NEWS 130-2P"    → "1:30P-2P"
        "M-Sun 8P-12M PRIMEBREAK"  → "8P-12M"
    """
    prog = program.strip()

    def _normalise_suffix(s: str) -> str:
        """Convert 'a','p','am','pm','m' to uppercase 2-char AM/PM."""
        s = s.upper()
        if s in ('A', 'AM'):
            return 'AM'
        if s in ('P', 'PM'):
            return 'PM'
        if s in ('M', 'MN', 'MIDNIGHT'):
            return 'AM'   # midnight = 12 AM next day
        return s

    def _fmt_h(h_str: str) -> str:
        """Expand compact hour '1130' → '11:30', '130' → '1:30'."""
        h_str = h_str.strip()
        if ':' in h_str:
            return h_str
        if len(h_str) == 4:   # "1130" → "11:30"
            return h_str[:2] + ':' + h_str[2:]
        if len(h_str) == 3:   # "130" → "1:30"
            return h_str[0] + ':' + h_str[1:]
        return h_str

    # Single comprehensive pattern:
    # Captures: start_h (1-4 digits, optional :mm), optional start_sfx ([AaPpMm]+),
    #           separator, end_h (1-4 digits, optional :mm), required end_sfx
    m = re.search(
        r'(\d{1,4}(?::\d{2})?)\s*([AaPpMm]+)?\s*[-–]\s*(\d{1,4}(?::\d{2})?)\s*([AaPpMm]+)',
        prog
    )
    if m:
        start_h = _fmt_h(m.group(1))
        start_sfx_raw = m.group(2) or ""
        end_h = _fmt_h(m.group(3))
        end_sfx = _normalise_suffix(m.group(4))

        # Inherit end suffix if start has none
        start_sfx = _normalise_suffix(start_sfx_raw) if start_sfx_raw else end_sfx

        return f"{start_h}{start_sfx}-{end_h}{end_sfx}"

    return ""   # couldn't parse — caller should prompt


def _infer_year_from_filename(path: Path, default: int = 2025) -> int:
    """Extract year from 'CY25' or 'CY2025' token in filename stem."""
    stem = path.stem.upper()
    m = re.search(r'CY(\d{2,4})', stem)
    if m:
        yr = int(m.group(1))
        return yr + 2000 if yr < 100 else yr
    return default


def _build_week_date_ranges_from_headers(
    week_col_indices: list[int],
    row13_cells: list[str],   # 0-indexed, from ws row 13
    row16_cells: list[str],   # 0-indexed, from ws row 16 (day numbers)
    year: int,
) -> list[tuple[date, date]]:
    """
    Map each week column → actual (start_date, end_date) using:
      - row 13: date-range strings like "1/20-1/30" → gives month context
      - row 16: day numbers like "20", "27-30" → gives day within month

    Strategy: for each week column, find the nearest row-13 date-range entry
    whose column index is ≤ the week column. Extract month from that entry.
    Combine with day number from row 16.
    """
    # Build mapping: col → month from row 13 date-range cells
    col_to_month: dict[int, int] = {}
    date_range_re = re.compile(r'^(\d{1,2})/(\d{1,2})-(\d{1,2})/(\d{1,2})$')

    for col_idx, val in enumerate(row13_cells):
        val = val.strip()
        m = date_range_re.match(val)
        if m:
            col_to_month[col_idx] = int(m.group(1))   # start month of range

    result: list[tuple[date, date]] = []

    for week_col in week_col_indices:
        # Find month: nearest col_to_month entry with col ≤ week_col
        month = None
        for c in sorted(col_to_month.keys(), reverse=True):
            if c <= week_col:
                month = col_to_month[c]
                break
        if month is None:
            # Fallback: first month in col_to_month
            month = min(col_to_month.values()) if col_to_month else 1

        # Actual year: if month < 3 and the date range is from a CY25 file,
        # check if the week is in year+1 (e.g. Dec 2025 → Dec 25, Jan 2026 → Jan 26)
        # Simple rule: month 1-12 stays in 'year'; we'll adjust during cutoff filtering
        day_val = row16_cells[week_col] if week_col < len(row16_cells) else "1"

        # Parse day spec ("20", "27-30")
        range_m = re.match(r'^(\d{1,2})-(\d{1,2})$', str(day_val).strip())
        plain_m = re.match(r'^(\d{1,2})$', str(day_val).strip())

        try:
            if range_m:
                start_day = int(range_m.group(1))
                end_day = int(range_m.group(2))
                start = date(year, month, start_day)
                max_day = calendar.monthrange(year, month)[1]
                end = date(year, month, min(end_day, max_day))
            elif plain_m:
                start_day = int(plain_m.group(1))
                start = date(year, month, start_day)
                max_day = calendar.monthrange(year, month)[1]
                end = min(start + timedelta(days=6), date(year, month, max_day))
            else:
                # Not parseable — use a placeholder
                start = end = date(year, month, 1)
        except ValueError:
            # Invalid date (e.g. day 31 in a month with 30 days)
            start = end = date(year, month, 1)

        result.append((start, end))

    return result


def parse_lexus_xlsx(path: str | Path, filename_meta: Optional[dict] = None) -> LexusParseResult:
    """
    Parse a Lexus insertion order from an IW Group XLSX spreadsheet.

    Handles the multi-month "CY##" campaign format used by Melissa at IW Group:
      Row 12: Month names (JANUARY, FEBRUARY, ...) in merged cells
      Row 13: Campaign date ranges ("1/20-1/30", "2/10-2/27", ...)
      Row 16: Week start day numbers (20, 27-30, 10, 17, ...) + header labels
      Rows 17–36: Paid spot data (col B = program, col C = unit, rate in last columns)
      Rows 39–58: Bonus spot data (same structure, rate = $0)

    Args:
        path: Path to the XLSX file
        filename_meta: Optional dict from parse_lexus_filename

    Returns:
        LexusParseResult
    """
    try:
        import openpyxl
        import warnings as _warnings
        _warnings.filterwarnings("ignore", category=UserWarning, module="openpyxl")
    except ImportError:
        raise ImportError("openpyxl is required for XLSX parsing. Run: uv add openpyxl")

    path = Path(path)
    meta = filename_meta or parse_lexus_filename(path.name)

    wb = openpyxl.load_workbook(path, data_only=True)
    ws = wb.active

    # Determine year (CY25 → 2025, CY26 → 2026, else current year)
    import datetime as _dt
    year = _infer_year_from_filename(path, default=_dt.date.today().year)

    # ── Read all rows into value lists ────────────────────────────────────
    all_row_values: list[list] = []
    for row in ws.iter_rows():
        all_row_values.append([c.value for c in row])

    def _sv(row_vals: list, col: int) -> str:
        """Safe string value of a cell."""
        if col < len(row_vals) and row_vals[col] is not None:
            return str(row_vals[col]).strip()
        return ""

    # ── Extract metadata from header rows ────────────────────────────────
    # Try to read estimate (row 7, col 1), market (row 9, col 1),
    # language/segment (row 10, col 1)
    estimate = meta.get("estimate", "")
    market = meta.get("market", "")
    language = meta.get("language")

    for row_idx, row_vals in enumerate(all_row_values[:15]):
        row0 = _sv(row_vals, 0).upper()
        row1 = _sv(row_vals, 1)
        if "ESTIMATE" in row0 and row1 and not estimate:
            estimate = row1.strip().split()[0]  # first token
        elif "DMA" in row0 and row1 and not market:
            # Normalise DMA to market code
            for tok, code in sorted(_MARKET_TOKENS.items(), key=lambda x: -len(x[0])):
                if tok in row1.upper():
                    market = code
                    break
            if not market:
                market = row1.strip()
        elif "SEGMENT" in row0 and row1 and not language:
            for tok, lang in sorted(_LANGUAGE_TOKENS.items(), key=lambda x: -len(x[0])):
                if tok in row1.upper():
                    language = lang
                    break
            if not language:
                language = row1.strip()

    # ── Locate key structural rows ────────────────────────────────────────
    # Row 13 (0-indexed=12): date range row — "1/20-1/30", "2/10-2/27" ...
    # Row 16 (0-indexed=15): header row — "PAID SPOTS", "CROSSINGS-CH", "UNIT", day-numbers
    header_row_idx = -1     # 0-indexed row with week day-numbers
    date_range_row_idx = -1

    date_range_re = re.compile(r'^\d{1,2}/\d{1,2}-\d{1,2}/\d{1,2}$')
    paid_spots_re = re.compile(r'PAID\s*SPOTS', re.IGNORECASE)

    for idx, row_vals in enumerate(all_row_values):
        # Date range row: multiple cells matching "M/D-M/D"
        range_count = sum(
            1 for v in row_vals
            if v and date_range_re.match(str(v).strip())
        )
        if range_count >= 2 and date_range_row_idx < 0:
            date_range_row_idx = idx

        # Header row: cell 0 matches "PAID SPOTS"
        if row_vals and row_vals[0] and paid_spots_re.search(str(row_vals[0])):
            header_row_idx = idx
            break

    if header_row_idx < 0:
        # Fallback: find row with most integer day-number cells
        best_count = 0
        for idx, row_vals in enumerate(all_row_values):
            cnt = sum(
                1 for v in row_vals
                if v is not None and re.match(r'^\d{1,2}$', str(v).strip())
                and 1 <= int(str(v).strip()) <= 31
            )
            if cnt > best_count:
                best_count = cnt
                header_row_idx = idx

    # ── Build week column list ────────────────────────────────────────────
    week_col_indices: list[int] = []
    week_headers: list[str] = []

    if header_row_idx >= 0:
        row_vals = all_row_values[header_row_idx]
        for col_idx, val in enumerate(row_vals):
            if val is None:
                continue
            s = str(val).strip()
            if (re.match(r'^\d{1,2}$', s) and 1 <= int(s) <= 31) or \
               re.match(r'^\d{1,2}-\d{1,2}$', s):
                week_col_indices.append(col_idx)
                week_headers.append(s)

    # ── Build week_date_ranges ────────────────────────────────────────────
    week_date_ranges: list[tuple[date, date]] = []
    if week_col_indices:
        row13_cells = [str(v).strip() if v is not None else ""
                       for v in all_row_values[date_range_row_idx]] \
            if date_range_row_idx >= 0 else []
        row16_cells = [str(v).strip() if v is not None else ""
                       for v in all_row_values[header_row_idx]]
        week_date_ranges = _build_week_date_ranges_from_headers(
            week_col_indices, row13_cells, row16_cells, year
        )

    n_weeks = len(week_col_indices)
    while len(week_date_ranges) < n_weeks:
        week_date_ranges.append((date.today(), date.today()))

    # ── Find rate column (NET COST PER SPOT) ─────────────────────────────
    rate_col: Optional[int] = None
    if date_range_row_idx >= 0:
        for col_idx, val in enumerate(all_row_values[date_range_row_idx]):
            if val and "NET COST" in str(val).upper():
                rate_col = col_idx
                break

    # ── Find BNS section start row ────────────────────────────────────────
    bns_start_row: Optional[int] = None
    for idx, row_vals in enumerate(all_row_values):
        if row_vals and row_vals[0] and "BONUS" in str(row_vals[0]).upper():
            bns_start_row = idx
            break

    # ── Parse data rows ───────────────────────────────────────────────────
    SKIP_MARKERS = {"TOTAL", "SUBTOTAL", "GRAND", "WEEKLY", "PROGRAM NAME"}
    lines: list[LexusLine] = []

    data_start = header_row_idx + 1 if header_row_idx >= 0 else 0

    for row_idx, row_vals in enumerate(all_row_values[data_start:], data_start):
        # Determine if this row is in the bonus section
        is_bonus_section = bns_start_row is not None and row_idx > bns_start_row

        # Col 0: category label
        col0 = str(row_vals[0]).strip() if row_vals and row_vals[0] else ""

        # Skip TOTAL / WEEKLY rows (but not the BNS header itself — we use
        # bns_start_row to track it, we skip the actual header row below)
        if any(kw in col0.upper() for kw in SKIP_MARKERS):
            continue
        if col0.upper().startswith("BONUS SPOTS"):
            continue   # the header row of the BNS section — skip it

        # Col 1: program name (contains days/time embedded)
        program_raw = str(row_vals[1]).strip() if len(row_vals) > 1 and row_vals[1] else ""
        if not program_raw or program_raw in (" ", ""):
            continue

        # Skip placeholder "Program Name #N" rows
        if re.match(r'^Program\s+Name\s+#\d+$', program_raw, re.IGNORECASE):
            continue

        # Col 2: unit (:15 or :30)
        unit_raw = str(row_vals[2]).strip() if len(row_vals) > 2 and row_vals[2] else ""
        duration = _parse_duration(unit_raw) or 30

        # Extract days and time from program name
        days_str = _extract_days_from_program(program_raw)
        time_str = _extract_time_from_program(program_raw)

        # Rate from rate column (NET)
        rate_net = 0.0
        if not is_bonus_section and rate_col is not None and rate_col < len(row_vals):
            r = row_vals[rate_col]
            if r is not None:
                try:
                    rate_net = float(r)
                except (ValueError, TypeError):
                    pass

        rate_gross = round(rate_net / 0.85, 2)

        # Spots by week
        spots_by_week: list[int] = []
        for col_idx in week_col_indices:
            val = row_vals[col_idx] if col_idx < len(row_vals) else None
            if val is None or val == "" or (isinstance(val, str) and val.strip() == ""):
                spots_by_week.append(0)
            else:
                try:
                    spots_by_week.append(int(float(str(val))))
                except (ValueError, TypeError):
                    spots_by_week.append(0)

        # Skip rows with zero spots throughout
        if not any(s > 0 for s in spots_by_week):
            continue

        line = LexusLine(
            program=program_raw.strip(),
            duration=duration,
            time=time_str,
            days=days_str,
            rate_net=rate_net,
            rate_gross=rate_gross,
            spots_by_week=spots_by_week,
            week_date_ranges=week_date_ranges,
            market=market,
            language=language or "",
            estimate=estimate,
            is_bonus=is_bonus_section,
        )
        lines.append(line)

    # ── Determine broadcast_month from first week with spots ──────────────
    broadcast_month = "Unknown"
    for line in lines:
        for (wk_start, _), spots in zip(line.week_date_ranges, line.spots_by_week):
            if spots > 0:
                mon_str = wk_start.strftime("%b")
                yr_str = wk_start.strftime("%y")
                broadcast_month = f"{mon_str}-{yr_str}"
                break
        if broadcast_month != "Unknown":
            break

    return LexusParseResult(
        lines=lines,
        broadcast_month=broadcast_month,
        week_headers=week_headers,
        language=language,
        estimate=estimate,
        market=market,
        order_type=meta.get("order_type", "revision"),
    )


# ───────────────────────────────────────────────────────────────────────────
# UNIFIED ENTRY POINT
# ───────────────────────────────────────────────────────────────────────────

def parse_lexus_file(path: str | Path) -> LexusParseResult:
    """
    Auto-detect format (JPG vs XLSX) and parse accordingly.

    Args:
        path: Path to a JPG, PNG, or XLSX file

    Returns:
        LexusParseResult
    """
    path = Path(path)
    meta = parse_lexus_filename(path.name)
    ext = path.suffix.lower()

    if ext in (".jpg", ".jpeg", ".png"):
        return parse_lexus_jpg(path, filename_meta=meta)
    elif ext in (".xlsx", ".xls"):
        return parse_lexus_xlsx(path, filename_meta=meta)
    else:
        raise ValueError(f"Unsupported Lexus file extension: {ext}")
