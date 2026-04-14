"""
Prosio Excel Order Parser

Parses "Media Contract" .xlsm/.xlsx Excel files from Prosio Communications.

Excel Layout (single sheet, typically named "Option 2" or similar):
  Row ~4:  "MEDIA CONTRACT" header
  Row ~6:  Agency: Prosio
  Row ~7:  Advertiser: <client name>
  Row ~8:  Contact: <name>
  Row ~9:  Email: <email>
  Row ~10: Station: Crossings TV (...)
  Row ~11: Language: <language>
  Row ~12: Date: <date>
  Row ~15: Airtime: <start> through <end>
  Row ~17: Column headers: Language | Length | Daypart | Rate per :30s | [week dates] | Total Spots | ...
  Row ~18+: Data rows (col B = "BONUS" for bonus lines)

  Summary rows to skip: "Paid", "Bonuses", "Total Paid +Bonuses", "Voiceover Translation..."

Key Business Rules:
  - Col B = "BONUS" → is_bonus=True, rate=0
  - Week date columns contain datetime objects (Monday of each broadcast week)
  - Market inferred from station name (KBTV/Xfinity TV 398 → CVC; KTSF → SFO)
  - Daypart column may have day prefix: "M-Sun 8p-9p" or "M-Sat 6a-7a; M-Sun 8p-11:30p"
  - Time is extracted by stripping leading day codes (everything before first digit)
  - Semicolons in daypart: each segment extracted separately, passed as "t1; t2" to parse_time_range
  - Day pattern: union of day segments across semicolon-separated parts
"""

from dataclasses import dataclass, field
from decimal import Decimal
from datetime import datetime
from pathlib import Path
from typing import List, Optional
import re
import sys

_project_root = Path(__file__).parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

import openpyxl


# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────

_SKIP_LANG_VALUES = {"paid", "bonuses", "total paid +bonuses"}


def _is_skip_row(col_b, col_c) -> bool:
    """Return True for summary/voiceover rows that must not become Etere lines."""
    if col_c is None:
        return True
    s = str(col_c).strip().lower()
    if s in _SKIP_LANG_VALUES:
        return True
    if s.startswith("voiceover"):
        return True
    return False


def _normalize_days(raw: str) -> str:
    """
    Normalise a day-pattern fragment from Prosio to Etere conventions.
    Strips spaces, then maps 3-letter abbreviations to 2-letter Etere codes.
    Examples: "M- Sun" → "M-Su", "M-Sat" → "M-Sa", "M-Sun" → "M-Su"
    """
    s = re.sub(r'\s+', '', raw)
    replacements = [
        (r'(?i)Sun', 'Su'),
        (r'(?i)Sat', 'Sa'),
        (r'(?i)Mon', 'M'),
        (r'(?i)Tue', 'Tu'),
        (r'(?i)Wed', 'W'),
        (r'(?i)Thu', 'Th'),
        (r'(?i)Fri', 'F'),
    ]
    for pattern, repl in replacements:
        s = re.sub(pattern, repl, s)
    return s


def _extract_days_from_daypart(daypart: str) -> str:
    """
    Extract and normalise the day pattern from a daypart string.

    For semicolon-separated windows, union the day patterns.
    Examples:
        "M- Sun 8p-9p"               → "M-Su"
        "M-Sat 6a-7a; M-Sun 8p-11:30p" → "M-Su"  (M-Sa ∪ M-Su)
    """
    parts = [p.strip() for p in daypart.split(";")]
    day_patterns: set[str] = set()
    for part in parts:
        m = re.match(r'^([A-Za-z][A-Za-z\s\-]*?)\s*\d', part.strip())
        if m:
            raw = m.group(1).strip().rstrip('-').rstrip()
            day_patterns.add(_normalize_days(raw))
    if not day_patterns:
        return "M-Su"
    if len(day_patterns) == 1:
        return day_patterns.pop()
    # M-Sa + M-Su → M-Su (broadest common pattern)
    if {"M-Sa", "M-Su"}.issubset(day_patterns):
        return "M-Su"
    return sorted(day_patterns)[0]


def _extract_time_from_daypart(daypart: str) -> str:
    """
    Strip the leading day codes from each semicolon segment, return the time
    portion(s) joined with "; " so EtereClient.parse_time_range can handle them.

    Examples:
        "M-Sun 8p-9p"                    → "8p-9p"
        "M-Sat 6a-7a; M-Sun 8p-11:30p"  → "6a-7a; 8p-11:30p"
    """
    parts = [p.strip() for p in daypart.split(";")]
    time_parts: list[str] = []
    for part in parts:
        m = re.search(r'(\d+(?::\d+)?\s*[aApPnNmM]+\s*-\s*\d+.*)', part.strip())
        if m:
            time_parts.append(m.group(1).strip())
        else:
            # fallback: take everything after first digit
            m2 = re.search(r'(\d.*)', part.strip())
            if m2:
                time_parts.append(m2.group(1).strip())
    return "; ".join(time_parts) if time_parts else daypart


def _station_to_market(station: str) -> str:
    """Infer Crossings TV market code from station description."""
    s = station.upper()
    if "KBTV" in s or "XFINITY TV 398" in s:
        return "CVC"
    if "KTSF" in s:
        return "SFO"
    if "KCAL" in s:
        return "LAX"
    return "CVC"   # Prosio's typical market


def _date_to_mon_dd(dt: datetime) -> str:
    """Convert a datetime to 'Jun 8' format for EtereClient.consolidate_weeks."""
    return dt.strftime("%b %-d")


def _parse_flight_dates(airtime_str: str) -> tuple[str, str]:
    """Parse 'Airtime: 6/8/2026  through 8/28/2026' → ('06/08/2026', '08/28/2026')."""
    m = re.search(r'(\d+/\d+/\d+)\s+through\s+(\d+/\d+/\d+)', airtime_str)
    if m:
        start = datetime.strptime(m.group(1), "%m/%d/%Y")
        end   = datetime.strptime(m.group(2), "%m/%d/%Y")
        return start.strftime("%m/%d/%Y"), end.strftime("%m/%d/%Y")
    return "", ""


# ─────────────────────────────────────────────────────────────────────────────
# DATACLASSES
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class ProsioLine:
    language: str          # e.g. "Mandarin News", "Mandarin"
    length: str            # e.g. ":30"
    daypart: str           # raw Excel value, e.g. "M-Sun 8p-9p"
    rate: Decimal          # gross rate per :30 (0 for bonus)
    is_bonus: bool
    weekly_spots: List[int]
    total_spots: int

    def get_etere_days(self) -> str:
        return _extract_days_from_daypart(self.daypart)

    def get_etere_time_str(self) -> str:
        """Return the time portion for EtereClient.parse_time_range."""
        return _extract_time_from_daypart(self.daypart)

    def get_duration_seconds(self) -> int:
        m = re.match(r':?(\d+)', self.length.strip())
        return int(m.group(1)) if m else 30

    def get_block_prefixes(self) -> List[str]:
        lang_lower = self.language.lower()
        if "mandarin" in lang_lower:
            return ["M"]
        if "cantonese" in lang_lower:
            return ["C"]
        if "vietnamese" in lang_lower:
            return ["V"]
        if "korean" in lang_lower:
            return ["K"]
        if "filipino" in lang_lower or "tagalog" in lang_lower:
            return ["T"]
        if "hmong" in lang_lower:
            return ["Hm"]
        if "punjabi" in lang_lower or "south asian" in lang_lower:
            return ["SA"]
        return []

    def get_description(self, etere_days: str, time_str: str) -> str:
        """Build Etere line description: '{days} {time} {language} [BNS]'."""
        lang = self.language.strip()
        suffix = " BNS" if self.is_bonus else ""
        return f"{etere_days} {time_str} {lang}{suffix}"


@dataclass
class ProsioOrder:
    agency: str
    advertiser: str
    contact: str
    email: str
    station: str
    language_header: str
    flight_start: str       # "MM/DD/YYYY"
    flight_end: str         # "MM/DD/YYYY"
    market: str             # e.g. "CVC"
    week_start_dates: List[str]   # "Jun 8" strings for consolidate_weeks
    lines: List[ProsioLine] = field(default_factory=list)


# ─────────────────────────────────────────────────────────────────────────────
# MAIN PARSER
# ─────────────────────────────────────────────────────────────────────────────

def parse_prosio_excel(file_path: str) -> ProsioOrder:
    """
    Parse a Prosio .xlsm / .xlsx Media Contract Excel file into a ProsioOrder.

    Raises:
        ValueError: if the column header row cannot be found
    """
    wb = openpyxl.load_workbook(
        file_path, read_only=True, keep_vba=False, data_only=True
    )
    ws = wb.active
    rows = list(ws.iter_rows(values_only=True))
    wb.close()

    # ── Find column header row first ──────────────────────────────────────────
    header_row_idx: Optional[int] = None
    for i, row in enumerate(rows):
        c_val = str(row[2] or "").strip().lower()
        e_val = str(row[4] or "").strip().lower()
        if c_val == "language" and e_val == "daypart":
            header_row_idx = i
            break

    # ── Header metadata (only rows before the table header) ───────────────────
    meta: dict[str, str] = {}
    airtime_str = ""
    meta_limit = header_row_idx if header_row_idx is not None else len(rows)
    for row in rows[:meta_limit]:
        if row[2] is not None:
            cell_c = str(row[2]).strip()
            if cell_c.lower().startswith("airtime:"):
                airtime_str = cell_c
            elif row[3] is not None:
                key = cell_c.rstrip(':').lower()
                meta[key] = str(row[3]).strip()

    flight_start, flight_end = _parse_flight_dates(airtime_str)
    agency     = meta.get("agency", "Prosio").strip()
    advertiser = meta.get("advertiser", "Unknown").strip()
    contact    = meta.get("contact", "").strip()
    email      = meta.get("email", "").strip()
    station    = meta.get("station", "").strip()
    lang_hdr   = meta.get("language", "Mandarin").strip()
    market     = _station_to_market(station)

    if header_row_idx is None:
        raise ValueError(
            f"Could not find column header row (Language|Daypart) in {file_path}"
        )

    header_row = rows[header_row_idx]

    # ── Locate week date columns and Total Spots column ───────────────────────
    week_start_dates: List[str] = []
    week_col_indices: List[int] = []
    total_spots_col: Optional[int] = None

    for col_idx, val in enumerate(header_row):
        if isinstance(val, datetime):
            week_start_dates.append(_date_to_mon_dd(val))
            week_col_indices.append(col_idx)
        elif val == "Total Spots":
            total_spots_col = col_idx

    # ── Parse data rows ───────────────────────────────────────────────────────
    lines: List[ProsioLine] = []
    for row in rows[header_row_idx + 1:]:
        if not any(v is not None for v in row):
            continue

        col_b = row[1]   # "BONUS" flag
        col_c = row[2]   # Language
        col_d = row[3]   # Length (:30)
        col_e = row[4]   # Daypart
        col_f = row[5]   # Rate per :30s

        if _is_skip_row(col_b, col_c):
            continue

        # Must have a daypart to be a valid airtime line
        if col_e is None or str(col_e).strip() == "":
            continue

        is_bonus = str(col_b or "").strip().upper() == "BONUS"
        language = str(col_c or "").strip()
        length   = str(col_d or ":30").strip()
        # Normalise "M- Sun 8p-9p" → "M-Sun 8p-9p" (Excel sometimes adds a space after dash)
        daypart  = re.sub(r'([A-Za-z])-\s+', r'\1-', str(col_e or "").strip())

        try:
            rate = Decimal(str(col_f or "0")).quantize(Decimal("0.01"))
        except Exception:
            rate = Decimal("0.00")

        weekly_spots: List[int] = []
        for col_idx in week_col_indices:
            try:
                weekly_spots.append(int(row[col_idx] or 0))
            except (ValueError, TypeError):
                weekly_spots.append(0)

        if total_spots_col is not None and row[total_spots_col] is not None:
            total_spots = int(row[total_spots_col])
        else:
            total_spots = sum(weekly_spots)

        if total_spots == 0:
            continue

        lines.append(ProsioLine(
            language=language,
            length=length,
            daypart=daypart,
            rate=rate,
            is_bonus=is_bonus,
            weekly_spots=weekly_spots,
            total_spots=total_spots,
        ))

    return ProsioOrder(
        agency=agency,
        advertiser=advertiser,
        contact=contact,
        email=email,
        station=station,
        language_header=lang_hdr,
        flight_start=flight_start,
        flight_end=flight_end,
        market=market,
        week_start_dates=week_start_dates,
        lines=lines,
    )


# ─────────────────────────────────────────────────────────────────────────────
# STANDALONE TEST
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys as _sys
    if len(_sys.argv) < 2:
        print("Usage: python prosio_parser.py <excel_path>")
        _sys.exit(1)

    try:
        order = parse_prosio_excel(_sys.argv[1])
        print("\n" + "=" * 70)
        print("PROSIO ORDER SUMMARY")
        print("=" * 70)
        print(f"Agency:      {order.agency}")
        print(f"Advertiser:  {order.advertiser}")
        print(f"Contact:     {order.contact}")
        print(f"Station:     {order.station}")
        print(f"Market:      {order.market}")
        print(f"Language:    {order.language_header}")
        print(f"Flight:      {order.flight_start} – {order.flight_end}")
        print(f"Weeks ({len(order.week_start_dates)}): {order.week_start_dates}")
        print(f"Lines:       {len(order.lines)}")
        print(f"Total spots: {sum(l.total_spots for l in order.lines)}")
        print("\n" + "=" * 70)
        print("LINES")
        print("=" * 70)
        for line in order.lines:
            d = line.get_etere_days()
            t = line.get_etere_time_str()
            print(f"\n  {'[BONUS]' if line.is_bonus else '[PAID ]'} {line.language}")
            print(f"    Daypart:  {line.daypart!r}")
            print(f"    Days:     {d}")
            print(f"    Time:     {t}")
            print(f"    Rate:     ${line.rate}")
            print(f"    Duration: {line.get_duration_seconds()}s")
            print(f"    Blocks:   {line.get_block_prefixes()}")
            print(f"    Spots:    {line.weekly_spots}  total={line.total_spots}")
            print(f"    Desc:     {line.get_description(d, t)}")
    except Exception as exc:
        print(f"\n✗ Error: {exc}")
        import traceback
        traceback.print_exc()
        _sys.exit(1)
