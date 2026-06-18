"""
LRCCD / 3Fold Communications order parser.

Reads the Crossings TV "MEDIA PLAN" PDF that 3Fold Communications places for the
Los Rios Community College District (LRCCD, Etere customer 218; 3Fold is Etere
agency 203).

A single PDF carries TWO orders — a FALL flight and a SPRING flight — and each
order has an AIRTIME :30 and an AIRTIME :15 section, so every line carries its
own duration (30 or 15). Single market: CVC.

There are no weekly spot columns: each line gives a flight-total spot count plus
a start/end date. Lines are therefore entered with spots_per_week=0, which makes
EtereDirectClient auto-select Rotation scheduling (flight > 7 days).

Rates are GROSS; the 3Fold agency commission (15%) is recorded automatically by
create_contract_header via the ANAGRAF auto-populate path → rates_are_net=False.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Optional

CLIENT_NAME = "Los Rios Community College District"
MARKET = "CVC"

# ─── Row / header patterns ───────────────────────────────────────────────────

# Section header that names the flight, e.g. "FALL 2026" / "SPRING 2027".
_HEADER_RE = re.compile(r'^(FALL|SPRING)\s+(\d{4})\s*$', re.IGNORECASE)

# One airtime data row. Examples:
#   "Fall Enrollment CVC Chinese Weekday M-F (7p-12a) 30 seconds 7/20/2026 8/23/2026 COM 9 $ 52.00 $ 468.00"
#   "Fall Enrollment CVC Chinese ROS ROS 30 seconds 7/20/2026 8/23/2026 BNS 3 $ - $ -"
#   "Fall Enrollment CVC Filipino Weekday M - F ( 4 p -7p) 30 seconds 7/20/2026 8/23/2026 COM 7 $ 42.00 $ 294.00"
_ROW_RE = re.compile(
    r'^(?P<season>Fall|Spring)\s+Enrollment\s+'
    r'(?P<market>CVC)\s+'
    r'(?P<lang>Chinese|Filipino|Hmong|Vietnamese)\s+'
    r'(?P<daypart>.+?)\s+'
    r'(?P<dur>\d+)\s+seconds\s+'
    r'(?P<start>\d{1,2}/\d{1,2}/\d{4})\s+'
    r'(?P<end>\d{1,2}/\d{1,2}/\d{4})\s+'
    r'(?P<type>COM|BNS)\s+'
    r'(?P<spots>\d+)\s+'
    r'\$\s*(?P<rate>[\d,]+\.\d{2}|-)\s+'
    r'\$\s*(?P<total>[\d,]+\.\d{2}|-)\s*$',
    re.IGNORECASE,
)


# ─── Daypart helpers ─────────────────────────────────────────────────────────

def _clean_daypart(dp: str) -> str:
    """Tidy a raw daypart for display: "Weekday M - F ( 4 p -7p)" → "Weekday M-F (4p-7p)"."""
    dp = re.sub(r'\(([^)]*)\)', lambda m: '(' + re.sub(r'\s+', '', m.group(1)) + ')', dp)
    dp = re.sub(r'\s*-\s*', '-', dp)
    dp = re.sub(r'\s+', ' ', dp).strip()
    return dp


def _days_time(daypart: str, language: str) -> tuple[str, str]:
    """
    Resolve (days, time_range) for a line from its raw daypart text.

    Lines with a parenthetical time window carry their own days+time. Lines
    without one are ROS (run-of-schedule) and fall back to the language's
    standard ROS window from ros_definitions.
    """
    if '(' not in daypart:
        try:
            from browser_automation.ros_definitions import ROS_SCHEDULES
            sched = ROS_SCHEDULES.get(language)
            if sched:
                return sched['days'], sched['time']
        except Exception:
            pass
        return 'M-Su', '6a-11:59p'

    m = re.search(r'\(([^)]*)\)', daypart)
    time_raw = re.sub(r'\s+', '', m.group(1)) if m else ''   # "4 p -7p" → "4p-7p"

    pre = daypart[:daypart.index('(')]
    pre = re.sub(r'(?i)\b(weekday|weekend|ros)\b', ' ', pre)  # drop program words
    pre = re.sub(r'\s*-\s*', '-', pre)                        # "M - F" → "M-F"
    pre = re.sub(r'\s+', ' ', pre).strip()
    if not pre:
        pre = 'Sa-Su' if 'weekend' in daypart.lower() else 'M-Su'
    return pre, time_raw


# ─── Data classes ────────────────────────────────────────────────────────────

@dataclass
class LRCCDLine:
    language: str          # Chinese / Filipino / Hmong / Vietnamese
    daypart: str           # raw daypart/program text, e.g. "Weekday M-F (7p-12a)" or "ROS ROS"
    duration: int          # 30 or 15
    total_spots: int       # flight-total spots (no weekly breakdown)
    rate: float            # per-spot GROSS rate (0.0 for bonus)
    is_bonus: bool
    start: date
    end: date

    @property
    def is_ros(self) -> bool:
        return '(' not in self.daypart

    @property
    def days(self) -> str:
        return _days_time(self.daypart, self.language)[0]

    @property
    def time(self) -> str:
        return _days_time(self.daypart, self.language)[1]

    @property
    def market(self) -> str:
        return MARKET

    @property
    def description(self) -> str:
        if self.is_bonus:
            if self.is_ros:
                return f"BNS {self.language} ROS"
            return f"BNS {self.language} {_clean_daypart(self.daypart)}"[:60]
        return f"{self.language} {_clean_daypart(self.daypart)}"[:60]


@dataclass
class LRCCDOrder:
    """One flight (FALL or SPRING) — becomes one Etere contract."""
    season: str                                  # "Fall 2026" / "Spring 2027"
    lines: list[LRCCDLine] = field(default_factory=list)

    @property
    def flight_start(self) -> Optional[date]:
        return min((ln.start for ln in self.lines), default=None)

    @property
    def flight_end(self) -> Optional[date]:
        return max((ln.end for ln in self.lines), default=None)

    @property
    def paid_lines(self) -> list[LRCCDLine]:
        return [ln for ln in self.lines if not ln.is_bonus]

    @property
    def bonus_lines(self) -> list[LRCCDLine]:
        return [ln for ln in self.lines if ln.is_bonus]

    @property
    def total_spots(self) -> int:
        return sum(ln.total_spots for ln in self.lines)

    @property
    def paid_spots(self) -> int:
        return sum(ln.total_spots for ln in self.paid_lines)

    @property
    def total_cost(self) -> float:
        return sum(ln.rate * ln.total_spots for ln in self.lines)


@dataclass
class LRCCDDocument:
    """The whole PDF: both flights, plus bridge-compatible header properties."""
    orders: list[LRCCDOrder] = field(default_factory=list)
    client: str = CLIENT_NAME
    rates_are_net: bool = False
    description: str = "26-27 Enrollment"

    @property
    def markets(self) -> list[str]:
        return [MARKET]

    @property
    def lines(self) -> list[LRCCDLine]:
        out: list[LRCCDLine] = []
        for o in self.orders:
            out.extend(o.lines)
        return out

    @property
    def flight_start(self) -> str:
        ds = [o.flight_start for o in self.orders if o.flight_start]
        return min(ds).strftime('%m/%d/%Y') if ds else ""

    @property
    def flight_end(self) -> str:
        ds = [o.flight_end for o in self.orders if o.flight_end]
        return max(ds).strftime('%m/%d/%Y') if ds else ""

    @property
    def total_spots(self) -> int:
        return sum(o.total_spots for o in self.orders)

    @property
    def total_cost(self) -> float:
        return sum(o.total_cost for o in self.orders)


# ─── Parser ──────────────────────────────────────────────────────────────────

def parse_lrccd_pdf(path: str) -> LRCCDDocument:
    """
    Parse a 3Fold / LRCCD Crossings TV media-plan PDF.

    Returns an LRCCDDocument with one LRCCDOrder per flight (FALL, SPRING).

    Raises:
        RuntimeError: if pdfplumber is not installed
        ValueError: if no airtime data rows are found
    """
    try:
        import pdfplumber
    except ImportError:
        raise RuntimeError("pdfplumber is required: uv add pdfplumber")

    seasons: dict[str, LRCCDOrder] = {}
    sequence: list[str] = []
    current_label: Optional[str] = None

    with pdfplumber.open(str(path)) as pdf:
        for page in pdf.pages:
            text = page.extract_text() or ""
            for raw in text.splitlines():
                line = raw.strip()
                if not line:
                    continue

                h = _HEADER_RE.match(line)
                if h:
                    current_label = f"{h.group(1).capitalize()} {h.group(2)}"
                    continue

                m = _ROW_RE.match(line)
                if not m:
                    continue

                start = datetime.strptime(m.group('start'), '%m/%d/%Y').date()
                end = datetime.strptime(m.group('end'), '%m/%d/%Y').date()

                # Prefer the named section header; fall back to the spot-name
                # season word + year if a header was somehow missed.
                label = current_label or f"{m.group('season').capitalize()} {start.year}"
                if label not in seasons:
                    seasons[label] = LRCCDOrder(season=label)
                    sequence.append(label)

                rate_raw = m.group('rate').strip()
                rate = 0.0 if rate_raw == '-' else float(rate_raw.replace(',', ''))

                seasons[label].lines.append(LRCCDLine(
                    language=m.group('lang').capitalize(),
                    daypart=m.group('daypart').strip(),
                    duration=int(m.group('dur')),
                    total_spots=int(m.group('spots')),
                    rate=rate,
                    is_bonus=(m.group('type').upper() == 'BNS'),
                    start=start,
                    end=end,
                ))

    orders = [seasons[k] for k in sequence]
    if not orders:
        raise ValueError(
            f"No LRCCD airtime rows found in {Path(path).name}. "
            "Expected '<Season> Enrollment CVC <Language> ...' rows."
        )
    return LRCCDDocument(orders=orders)
