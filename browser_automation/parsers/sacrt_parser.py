"""
SacRT / Sacramento Regional Transit order parser.

Reads the Crossings TV "MEDIA PLAN" PDF that 3Fold Communications directs to us
for Sacramento Regional Transit (SacRT, Etere customer 442). 3Fold only brokered
the buy — SacRT is BILLED DIRECT: no agency on the contract, no commission.

Same template family as the LRCCD media plan, but a single flight (no
FALL/SPRING sections) and a single AIRTIME :30 section. Single market: CVC.

There are no weekly spot columns: each line gives a flight-total spot count plus
a start/end date. Lines are therefore entered with spots_per_week=0, which makes
EtereDirectClient auto-select Rotation scheduling (flight > 7 days).

Rates are printed NET, but with no agency and no commission the net rate IS the
rate entered — no gross-up. rates_are_net stays False so the parser bridge does
not warn "gross-up required" (that warning is for agency orders).

Bonus rows whose Language Block contains "ROS" use the universal ROS window for
that language (ros_definitions.ROS_SCHEDULES) — the PDF's semicolon-joined
multi-daypart cells collapse to exactly those windows. Non-ROS rows carry their
own printed days + time.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Optional

CLIENT_NAME = "Sacramento Regional Transit"
MARKET = "CVC"

# Language Block names as printed → normalized ("Pubjabi" is a recurring typo).
_LANGUAGES = {
    'chinese': 'Chinese',
    'filipino': 'Filipino',
    'tagalog': 'Filipino',
    'hmong': 'Hmong',
    'korean': 'Korean',
    'punjabi': 'Punjabi',
    'pubjabi': 'Punjabi',
    'vietnamese': 'Vietnamese',
    'south asian': 'South Asian',
    'cantonese': 'Cantonese',
    'japanese': 'Japanese',
}
_LANG_RE = re.compile('|'.join(_LANGUAGES), re.IGNORECASE)

# ─── Row patterns ────────────────────────────────────────────────────────────

# One airtime data row, anchored on the reliable tail. Examples:
#   "SacRT - Awareness CVC Chinese Weekday PM M-F (7p-12a) 30 seconds 8/3/2026 10/30/2026 COM 20 $ 43.00 $ 860.00"
#   "SacRT - Awareness CVC Children's ) 12a); Sa-Su (8p-12a) 30 seconds 8/3/2026 10/30/2026 BNS 9 $ - $ -"
# (the second is a wrapped row — its Language Block overflowed onto the
#  PREVIOUS text line: "Chinese ROS (excluding M-Sa (6a-7a); M-F (7p-")
_ROW_RE = re.compile(
    r'^(?P<spot>.+?)\s+'
    r'(?P<market>CVC)\s+'
    r'(?P<mid>.+?)\s+'
    r'(?P<dur>\d+)\s+seconds\s+'
    r'(?P<start>\d{1,2}/\d{1,2}/\d{4})\s+'
    r'(?P<end>\d{1,2}/\d{1,2}/\d{4})\s+'
    r'(?P<type>COM|BNS)\s+'
    r'(?P<spots>\d+)\s+'
    r'\$\s*(?P<rate>[\d,]+\.\d{2}|-)\s+'
    r'\$\s*(?P<total>[\d,]+\.\d{2}|-)\s*$',
    re.IGNORECASE,
)

_CAMPAIGN_RE = re.compile(r'Campaign\s+Name:\s*(.+?)\s+(?:Market|Billing)', re.IGNORECASE)

# Day pattern immediately before a parenthesized time window, e.g. "M-F (7p-12a)".
_DAYS_TIME_RE = re.compile(r'\b([A-Za-z]{1,2}(?:-[A-Za-z]{1,2})?)\s*\(([^)]*)\)')


# ─── Daypart helpers ─────────────────────────────────────────────────────────

def _clean(text: str) -> str:
    """Collapse whitespace for display."""
    return re.sub(r'\s+', ' ', text).strip()


def _ros_days_time(language: str) -> tuple[str, str]:
    try:
        from browser_automation.ros_definitions import ROS_SCHEDULES
        sched = ROS_SCHEDULES.get(language)
        if sched:
            return sched['days'], sched['time']
    except Exception:
        pass
    return 'M-Su', '6a-11:59p'


# ─── Data classes ────────────────────────────────────────────────────────────

@dataclass
class SacRTLine:
    language: str          # normalized: Chinese / Filipino / Hmong / Korean / Punjabi / Vietnamese
    block: str             # raw Language Block + Day Part text (wrapped rows rejoined)
    duration: int          # seconds
    total_spots: int       # flight-total spots (no weekly breakdown)
    rate: float            # per-spot rate as printed (NET = entered, direct bill; 0.0 for bonus)
    is_bonus: bool
    is_ros: bool           # Language Block contains "ROS" → universal ROS window
    days_raw: str          # printed day pattern, e.g. "M-F" (empty for mangled ROS wraps)
    time_raw: str          # printed time window, e.g. "7p-12a"
    start: date
    end: date

    @property
    def days(self) -> str:
        return _ros_days_time(self.language)[0] if self.is_ros else self.days_raw

    @property
    def time(self) -> str:
        return _ros_days_time(self.language)[1] if self.is_ros else self.time_raw

    @property
    def market(self) -> str:
        return MARKET

    @property
    def description(self) -> str:
        if self.is_ros:
            prefix = "BNS " if self.is_bonus else ""
            return f"{prefix}{self.language} ROS"[:60]
        label = f"{self.language} {self.days_raw} ({self.time_raw})"
        return (f"BNS {label}" if self.is_bonus else label)[:60]


@dataclass
class SacRTDocument:
    """The whole PDF — a single flight, entered as one Etere contract."""
    lines: list[SacRTLine] = field(default_factory=list)
    client: str = CLIENT_NAME
    campaign: str = ""
    # Rates are printed NET, but SacRT is billed direct (no agency, no
    # commission) so the net rate is entered as-is — no gross-up, no warning.
    rates_are_net: bool = False

    @property
    def markets(self) -> list[str]:
        return [MARKET]

    @property
    def flight_start_date(self) -> Optional[date]:
        return min((ln.start for ln in self.lines), default=None)

    @property
    def flight_end_date(self) -> Optional[date]:
        return max((ln.end for ln in self.lines), default=None)

    @property
    def flight_start(self) -> str:
        d = self.flight_start_date
        return d.strftime('%m/%d/%Y') if d else ""

    @property
    def flight_end(self) -> str:
        d = self.flight_end_date
        return d.strftime('%m/%d/%Y') if d else ""

    @property
    def paid_lines(self) -> list[SacRTLine]:
        return [ln for ln in self.lines if not ln.is_bonus]

    @property
    def bonus_lines(self) -> list[SacRTLine]:
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

    @property
    def description(self) -> str:
        return self.campaign or "SacRT"


# ─── Parser ──────────────────────────────────────────────────────────────────

def _parse_days_time(block: str) -> tuple[str, str]:
    """
    Pull (days, time) from a row's middle text, e.g.
    "Korean Weekday M-F (8a-10a)" → ("M-F", "8a-10a").
    Takes the FIRST day-pattern + parenthetical pair; inner spaces in the time
    window are stripped ("2p- 4p" → "2p-4p").
    """
    m = _DAYS_TIME_RE.search(block)
    if not m:
        return '', ''
    return m.group(1), re.sub(r'\s+', '', m.group(2))


def parse_sacrt_pdf(path: str) -> SacRTDocument:
    """
    Parse a SacRT (Sacramento Regional Transit) Crossings TV media-plan PDF.

    Raises:
        RuntimeError: if pdfplumber is not installed
        ValueError: if no airtime data rows are found, or a non-ROS row is
            missing its day/time window
    """
    try:
        import pdfplumber
    except ImportError:
        raise RuntimeError("pdfplumber is required: uv add pdfplumber")

    doc = SacRTDocument()
    prev_line = ""

    with pdfplumber.open(str(path)) as pdf:
        for page in pdf.pages:
            text = page.extract_text() or ""
            for raw in text.splitlines():
                line = raw.strip()
                if not line:
                    continue

                if not doc.campaign:
                    c = _CAMPAIGN_RE.search(line)
                    if c:
                        doc.campaign = c.group(1).strip()

                m = _ROW_RE.match(line)
                if not m:
                    prev_line = line
                    continue

                mid = m.group('mid').strip()
                # Wrapped row: the Language Block overflowed onto the previous
                # text line, leaving no language name in this row — rejoin.
                block = mid
                if not _LANG_RE.search(block):
                    block = f"{prev_line} {mid}"
                prev_line = ""

                lang_m = _LANG_RE.search(block)
                if not lang_m:
                    raise ValueError(f"No language found in row: {line!r}")
                language = _LANGUAGES[lang_m.group(0).lower()]

                is_ros = bool(re.search(r'\bROS\b', block))
                days_raw, time_raw = _parse_days_time(block)
                if not is_ros and (not days_raw or not time_raw):
                    raise ValueError(f"No day/time window found in row: {line!r}")

                rate_raw = m.group('rate').strip()
                rate = 0.0 if rate_raw == '-' else float(rate_raw.replace(',', ''))

                doc.lines.append(SacRTLine(
                    language=language,
                    block=_clean(block),
                    duration=int(m.group('dur')),
                    total_spots=int(m.group('spots')),
                    rate=rate,
                    is_bonus=(m.group('type').upper() == 'BNS'),
                    is_ros=is_ros,
                    days_raw=days_raw,
                    time_raw=time_raw,
                    start=datetime.strptime(m.group('start'), '%m/%d/%Y').date(),
                    end=datetime.strptime(m.group('end'), '%m/%d/%Y').date(),
                ))

    if not doc.lines:
        raise ValueError(
            f"No SacRT airtime rows found in {Path(path).name}. "
            "Expected '<Spot Name> CVC <Language Block> ...' rows."
        )
    return doc
