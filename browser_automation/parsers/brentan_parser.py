"""
Brentan (Brentan Media Services) order parser.

Reads Crossings TV media proposal Excel workbooks produced by Brentan Media.
Same workbook template as ACM: multi-market sections; language-block rows
(paid + bonus ROS); week-date columns for spot counts.

Differences from ACM:
  * Agency name is "Brentan Media Services".
  * Rate column is "GROSS RATE" → rates_are_net=False (no gross-up needed).
  * Order-date metadata label is "Order date" (ACM uses "Date").
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

# ─── Data Classes ────────────────────────────────────────────────────────────

@dataclass
class BrentanLine:
    language_block: str   # e.g. "Filipino News and Talk" or "Filipino" (bonus)
    daypart: str          # e.g. "M-F 4p-5p; 6p-7p" or "ROS"
    rate: float           # per-spot GROSS rate (0.0 for bonus)
    week_spots: list[int] = field(default_factory=list)
    week_dates: list[date] = field(default_factory=list)
    is_bonus: bool = False

    @property
    def total_spots(self) -> int:
        return sum(self.week_spots)

    @property
    def days(self) -> str:
        if self.is_bonus:
            return "M-Su"
        return _split_daypart(self.daypart)[0]

    @property
    def time(self) -> str:
        if self.is_bonus:
            return ""
        return _split_daypart(self.daypart)[1]

    @property
    def description(self) -> str:
        if self.is_bonus:
            return f"BNS {self.language_block.strip()} ROS"
        label = self.language_block.strip()
        return f"{label} {self.daypart}"


@dataclass
class BrentanMarketSection:
    market_code: str
    lines: list[BrentanLine] = field(default_factory=list)

    @property
    def paid_lines(self) -> list[BrentanLine]:
        return [ln for ln in self.lines if not ln.is_bonus]

    @property
    def bonus_lines(self) -> list[BrentanLine]:
        return [ln for ln in self.lines if ln.is_bonus]

    @property
    def flight_start(self) -> Optional[date]:
        for ln in self.lines:
            if ln.week_dates:
                return ln.week_dates[0]
        return None

    @property
    def flight_end(self) -> Optional[date]:
        last: Optional[date] = None
        for ln in self.lines:
            if ln.week_dates:
                d = ln.week_dates[-1] + timedelta(days=6)
                if last is None or d > last:
                    last = d
        return last


@dataclass
class BrentanOrder:
    agency: str                       # the media agency (Brentan Media Services), agency_id 439
    order_date: Optional[date]
    market_sections: list[BrentanMarketSection]
    rates_are_net: bool = False       # Brentan rate column is "GROSS RATE"
    client: str = ""                  # the advertiser/customer (e.g. "CA Conservation Corps")

    @property
    def markets(self) -> list[str]:
        return [m.market_code for m in self.market_sections]

    @property
    def lines(self) -> list[BrentanLine]:
        """All lines across all markets (flattened for bridge display)."""
        result: list[BrentanLine] = []
        for m in self.market_sections:
            result.extend(m.lines)
        return result

    @property
    def flight_start(self) -> str:
        for m in self.market_sections:
            d = m.flight_start
            if d:
                return d.strftime('%m/%d/%Y')
        return ""

    @property
    def flight_end(self) -> str:
        last: Optional[date] = None
        for m in self.market_sections:
            d = m.flight_end
            if d and (last is None or d > last):
                last = d
        return last.strftime('%m/%d/%Y') if last else ""


# ─── Market Detection ────────────────────────────────────────────────────────

_MARKET_KEYWORDS: list[tuple[str, str]] = [
    ("SAN FRANCISCO", "SFO"),
    ("CENTRAL VALLEY", "CVC"),
    ("SACRAMENTO",     "CVC"),
    ("LOS ANGELES",    "LAX"),
    ("SEATTLE",        "SEA"),
    ("CHICAGO",        "CMP"),
    ("MINNEAPOLIS",    "CMP"),
    ("HOUSTON",        "HOU"),
    ("WASHINGTON",     "WDC"),
    ("NEW YORK",       "NYC"),
]


def _detect_market(text: str) -> Optional[str]:
    upper = text.upper()
    for keyword, code in _MARKET_KEYWORDS:
        if keyword in upper:
            return code
    return None


def _client_from_filename(path: str) -> str:
    """
    Extract the advertiser/client name from the Brentan file name.

    The workbook itself carries only the agency (Brentan Media Services);
    the client lives in the file name, e.g.
        "Crossings TV CA Conservation Corps_Brentan Media_2026.xlsx"
                       └──────── client ────────┘
    Returns "" when the conventional pattern is not found (gather then prompts).
    """
    stem = Path(path).stem
    m = re.search(r'crossings\s+tv\s+(.+?)\s*_\s*brentan', stem, re.IGNORECASE)
    if m:
        return m.group(1).strip()
    return ""


# ─── Daypart Splitting ───────────────────────────────────────────────────────

_DAY_NORM = [
    (re.compile(r'\bSun\b', re.IGNORECASE), 'Su'),
    (re.compile(r'\bSat\b', re.IGNORECASE), 'Sa'),
    (re.compile(r'\bMon\b', re.IGNORECASE), 'M'),
    (re.compile(r'\bTue\b', re.IGNORECASE), 'T'),
    (re.compile(r'\bWed\b', re.IGNORECASE), 'W'),
    (re.compile(r'\bThu\b', re.IGNORECASE), 'R'),
    (re.compile(r'\bFri\b', re.IGNORECASE), 'F'),
]


def _normalize_days(s: str) -> str:
    for pattern, repl in _DAY_NORM:
        s = pattern.sub(repl, s)
    return s


def _split_daypart(daypart: str) -> tuple[str, str]:
    """Split "M-F 4p-5p; 6p-7p" → ("M-F", "4p-5p; 6p-7p"); "Sat-Sun 6p-8p" → ("Sa-Su", "6p-8p")."""
    daypart = daypart.strip()
    m = re.match(r'^([A-Za-z][-–,A-Za-z]*?)\s+(\d.+)$', daypart)
    if m:
        return _normalize_days(m.group(1).strip()), m.group(2).strip()
    return _normalize_days(daypart), ""


# ─── Skip Labels ─────────────────────────────────────────────────────────────

_SKIP_LABELS = frozenset({
    'paid', 'bonus', 'summary of investment', 'markets',
    'total airtime', 'language block', 'total paid units',
    'total paid+bonus', 'san francisco', 'central valley',
    'total', 'airtime',
})


# ─── Parser ──────────────────────────────────────────────────────────────────

def parse_brentan_xlsx(path: str) -> BrentanOrder:
    """
    Parse a Brentan Media Services Crossings TV proposal workbook.

    Args:
        path: Path to the .xlsx file

    Returns:
        BrentanOrder populated with all market sections and lines

    Raises:
        RuntimeError: if openpyxl is not installed
        ValueError: if no market sections are found
    """
    try:
        import openpyxl
    except ImportError:
        raise RuntimeError("openpyxl is required: uv add openpyxl")

    wb = openpyxl.load_workbook(str(path), data_only=True)
    ws = wb.active

    rows = list(ws.iter_rows(values_only=True))

    # ── Metadata ──────────────────────────────────────────────────────────────
    agency = "Brentan Media Services"
    order_date: Optional[date] = None

    for row in rows[:18]:
        label = str(row[3] or '').strip().lower()
        if label in ('order date', 'date'):
            v = row[4]
            if hasattr(v, 'date'):
                order_date = v.date()
            break

    # ── Parse market sections ─────────────────────────────────────────────────
    markets: list[BrentanMarketSection] = []
    current_market: Optional[BrentanMarketSection] = None
    week_dates: list[date] = []
    num_week_cols = 0

    for row in rows:
        if not any(v is not None for v in row):
            continue

        cell_b = str(row[1] or '').strip()   # BONUS marker
        cell_c = str(row[2] or '').strip()   # Language block / section header
        cell_d = str(row[3] or '').strip()   # Day Part / Program
        cell_e = row[4]                       # Gross rate per spot (or header label)

        cell_c_lower = cell_c.lower().strip()

        # ── Stop at the summary section ───────────────────────────────────
        # Everything below "Summary of investment" is totals/added-value notes,
        # not airtime lines. Its per-market rows (e.g. "Los Angeles") would
        # otherwise be mis-read as paid lines with garbage day/spot values.
        if cell_c_lower == 'summary of investment':
            break

        # ── Market section header (contains station/market identifier) ─────
        if 'california' in cell_c_lower or 'xfinity' in cell_c_lower:
            market_code = _detect_market(cell_c)
            if market_code:
                current_market = BrentanMarketSection(market_code=market_code)
                markets.append(current_market)
                week_dates = []
                num_week_cols = 0
            continue

        # ── Column header row ─────────────────────────────────────────────
        if cell_c == 'Language Block':
            week_dates = []
            for col_idx in range(5, len(row)):
                v = row[col_idx]
                if hasattr(v, 'date'):
                    week_dates.append(v.date())
                else:
                    break  # stop at first non-datetime
            num_week_cols = len(week_dates)
            continue

        # ── Skip totals / navigation rows ─────────────────────────────────
        if cell_c_lower.rstrip() in _SKIP_LABELS:
            continue

        if current_market is None or num_week_cols == 0:
            continue

        # ── Bonus line ────────────────────────────────────────────────────
        if cell_b.upper() == 'BONUS':
            week_spots = [
                int(row[col_idx]) if isinstance(row[col_idx], (int, float)) else 0
                for col_idx in range(5, 5 + num_week_cols)
            ]
            current_market.lines.append(BrentanLine(
                language_block=cell_c,
                daypart='ROS',
                rate=0.0,
                week_spots=week_spots,
                week_dates=list(week_dates),
                is_bonus=True,
            ))
            continue

        # ── Paid line ─────────────────────────────────────────────────────
        if cell_c and cell_d:
            rate = float(cell_e) if isinstance(cell_e, (int, float)) else 0.0
            week_spots = [
                int(row[col_idx]) if isinstance(row[col_idx], (int, float)) else 0
                for col_idx in range(5, 5 + num_week_cols)
            ]
            current_market.lines.append(BrentanLine(
                language_block=cell_c,
                daypart=cell_d,
                rate=rate,
                week_spots=week_spots,
                week_dates=list(week_dates),
                is_bonus=False,
            ))

    wb.close()

    if not markets:
        raise ValueError(
            f"No market sections found in {Path(path).name}. "
            "Expected 'California-...' section headers."
        )

    return BrentanOrder(
        agency=agency,
        order_date=order_date,
        market_sections=markets,
        client=_client_from_filename(path),
    )
