"""
Emerald Queen Casino (EQC) / TH Media order parser.

Reads the Crossings TV "Flight schedule" workbook TH Media sends for Emerald
Queen Casino. Single market (Seattle), single advertiser.

Layout (one sheet):
    C3  TV Station = Crossings TV     C4  Market: Seattle
    row 8:  B=PROGRAM  D=SCHEDULE  E=RATE  F..  = week-start dates (one per col)
    rows 9+ : one program per row
        - paid rows have a GROSS rate (> 0)
        - bonus rows have rate 0 (the three language "added value" blocks)
    footer rows: "Paid Units" / "Bonus Units" / "Total Units" / "GROSS" — stop there.

IMPORTANT business rules (confirmed with the buyer):
  * Each date column is ONE week (Mon–Sun). EQC buys non-consecutive weeks
    (typically every other week), so weeks are NEVER consolidated — the
    automation emits one contract line per program per week-column.
  * Rates are GROSS (the rate column sums to the GROSS footer) → no gross-up.
  * Quarters are entered as separate contracts; this parser just exposes the
    week dates and the automation groups them by quarter.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Optional

# ─── Day normalization (shared idiom with tt_parser) ─────────────────────────

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
    """'M-Sun' → 'M-Su'; 'Sat & Sun' → 'Sa,Su'; 'M-F' → 'M-F'."""
    for pattern, repl in _DAY_NORM:
        s = pattern.sub(repl, s)
    s = re.sub(r'\s*&\s*', ',', s)   # "Sa & Su" → "Sa,Su"
    s = re.sub(r'\s+', '', s)        # drop residual spaces ("Sa - Su" never happens here)
    return s.strip(' ,')


def _split_schedule(schedule: str) -> tuple[str, str]:
    """
    Split a SCHEDULE cell into (days, time_raw).

      'M-Sun 8p-9p'            → ('M-Su',  '8p-9p')
      'Sat & Sun  8p-11p'      → ('Sa,Su', '8p-11p')
      'M-Sun 10a-11a& 12p-1p'  → ('M-Su',  '10a-11a& 12p-1p')
      'M-Sun 7p-12a'           → ('M-Su',  '7p-12a')

    The time portion starts at the first time token (a digit run followed by an
    optional am/pm marker and a hyphen). Everything before it is the day part.
    """
    schedule = (schedule or '').strip()
    m = re.search(r'\d{1,2}(?::\d{2})?\s*[ap]?\s*[-–]', schedule)
    if m:
        days_part = schedule[:m.start()].strip()
        time_part = schedule[m.start():].strip()
    else:
        days_part, time_part = schedule, ""
    return _normalize_days(days_part), time_part


# ─── Data classes ────────────────────────────────────────────────────────────

@dataclass
class EQCLine:
    program: str               # e.g. "Shanghai Primetime News" or "Chinese" (bonus)
    schedule: str              # raw SCHEDULE cell, e.g. "M-Sun 8p-9p"
    rate: float                # per-spot GROSS rate (0.0 for bonus)
    week_spots: list[int] = field(default_factory=list)   # one count per week-column
    week_dates: list[date] = field(default_factory=list)  # Monday of each week-column
    is_bonus: bool = False

    @property
    def days(self) -> str:
        return _split_schedule(self.schedule)[0]

    @property
    def time_raw(self) -> str:
        return _split_schedule(self.schedule)[1]

    @property
    def total_spots(self) -> int:
        return sum(self.week_spots)

    @property
    def description(self) -> str:
        prog = self.program.strip()
        base = f"BNS {prog}" if self.is_bonus else prog
        sched = self.schedule.strip()
        desc = f"{base} {sched}".strip()
        return desc[:60]


@dataclass
class EQCOrder:
    market_code: str                                  # "SEA"
    lines: list[EQCLine] = field(default_factory=list)
    week_dates: list[date] = field(default_factory=list)
    order_date: Optional[date] = None
    client: str = "Emerald Queen Casino"              # advertiser (ANAGRAF customer 20)
    agency: str = "TH Media"                          # buyer (agency 19)
    rates_are_net: bool = False                       # rates are GROSS

    @property
    def markets(self) -> list[str]:
        return [self.market_code]

    @property
    def paid_lines(self) -> list[EQCLine]:
        return [ln for ln in self.lines if not ln.is_bonus]

    @property
    def bonus_lines(self) -> list[EQCLine]:
        return [ln for ln in self.lines if ln.is_bonus]

    @property
    def flight_start(self) -> str:
        return self.week_dates[0].strftime('%m/%d/%Y') if self.week_dates else ""

    @property
    def flight_end(self) -> str:
        if not self.week_dates:
            return ""
        return (self.week_dates[-1] + timedelta(days=6)).strftime('%m/%d/%Y')


# ─── Market detection ────────────────────────────────────────────────────────

_MARKET_KEYWORDS: list[tuple[str, str]] = [
    ("SEATTLE",        "SEA"),
    ("SAN FRANCISCO",  "SFO"),
    ("CENTRAL VALLEY", "CVC"),
    ("SACRAMENTO",     "CVC"),
    ("LOS ANGELES",    "LAX"),
    ("HOUSTON",        "HOU"),
    ("WASHINGTON",     "WDC"),
    ("NEW YORK",       "NYC"),
]


def _detect_market(text: str) -> Optional[str]:
    upper = (text or "").upper()
    for keyword, code in _MARKET_KEYWORDS:
        if keyword in upper:
            return code
    return None


# ─── Footer / skip labels ────────────────────────────────────────────────────

_STOP_LABELS = frozenset({
    'paid units', 'bonus units', 'total units (paid + bonus)',
    'total units', 'gross', 'gross ',
})


# ─── Parser ──────────────────────────────────────────────────────────────────

def parse_eqc_xlsx(path: str) -> EQCOrder:
    """
    Parse a TH Media / Emerald Queen Casino Crossings TV flight-schedule workbook.

    Returns an EQCOrder with one EQCLine per program row (paid + bonus) and the
    shared list of week-start dates.

    Raises:
        RuntimeError: if openpyxl is not installed
        ValueError: if the header/week-date row cannot be located
    """
    try:
        import openpyxl
    except ImportError:
        raise RuntimeError("openpyxl is required: uv add openpyxl")

    wb = openpyxl.load_workbook(str(path), data_only=True)
    ws = wb.active
    rows = list(ws.iter_rows(values_only=True))  # row[i] is 0-based: col B == index 1

    # ── Market (default SEA) ─────────────────────────────────────────────────
    market_code = "SEA"
    order_date: Optional[date] = None
    for row in rows[:8]:
        for v in row:
            if v is None:
                continue
            mc = _detect_market(str(v))
            if mc:
                market_code = mc
                break
        for v in row:
            if hasattr(v, 'year') and hasattr(v, 'month') and not hasattr(v, 'hour'):
                order_date = v  # a plain date, rare in header

    # ── Locate header row (B=PROGRAM, D=SCHEDULE) and week-date columns ───────
    header_idx: Optional[int] = None
    week_dates: list[date] = []
    week_cols: list[int] = []
    for i, row in enumerate(rows):
        b = str(row[1] or '').strip().upper() if len(row) > 1 else ''
        d = str(row[3] or '').strip().upper() if len(row) > 3 else ''
        if b == 'PROGRAM' and 'SCHEDULE' in d:
            header_idx = i
            for col_idx in range(5, len(row)):       # E is rate (idx 4); dates start at F (idx 5)
                v = row[col_idx]
                if hasattr(v, 'date'):               # datetime cell
                    week_dates.append(v.date() if hasattr(v, 'hour') else v)
                    week_cols.append(col_idx)
            break

    if header_idx is None or not week_cols:
        wb.close()
        raise ValueError(
            "Could not locate the EQC header row (B='PROGRAM', D='SCHEDULE') "
            "with week-date columns."
        )

    # ── Parse program rows ───────────────────────────────────────────────────
    lines: list[EQCLine] = []
    for row in rows[header_idx + 1:]:
        program = str(row[1] or '').strip() if len(row) > 1 else ''
        schedule = str(row[3] or '').strip() if len(row) > 3 else ''
        rate_cell = row[4] if len(row) > 4 else None

        label_d = str(row[3] or '').strip().lower() if len(row) > 3 else ''
        if label_d in _STOP_LABELS:
            break  # reached the totals/GROSS footer block

        if not program or not schedule:
            continue

        rate = float(rate_cell) if isinstance(rate_cell, (int, float)) else 0.0
        week_spots = [
            int(row[c]) if (c < len(row) and isinstance(row[c], (int, float))) else 0
            for c in week_cols
        ]

        lines.append(EQCLine(
            program=program,
            schedule=schedule,
            rate=rate,
            week_spots=week_spots,
            week_dates=list(week_dates),
            is_bonus=(rate == 0.0),
        ))

    wb.close()

    if not lines:
        raise ValueError("No program rows found in EQC workbook.")

    return EQCOrder(
        market_code=market_code,
        lines=lines,
        week_dates=week_dates,
        order_date=order_date,
    )
