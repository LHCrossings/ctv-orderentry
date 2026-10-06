"""California Alliance of Family Owned Businesses PAC — house proposal PDF parser.

The buyer (National Media, Mike Adam) sends the Crossings SF house proposal: a
one-page PDF with a key/value header block and one grid:

    Client National Media: California Alliance of Family Owned Businesses PAC
    Billing Cycle Broadcast / Market: SF BAY AREA / Channel XFINITY TV 3131 & KQTA 15.3
    Estimate Flight Date: WEEK OF 10/5- 11/3 / Airtime :30s video / 30 minute separation rules
    Language Block | Day Part/Program | GROSS | 5-Oct 12-Oct … | Total Units | GROSS
    VIETNAMESE NEWS | M-F 11A-11:30A | $ 120.00 | 3 5 5 5 2 | 20 | $ 2 ,400.00
    TOTAL | | | 7 10 10 10 4 | 41 | $ 4,920.00

Rules (Lee, 2026-10-06; oracle = Maija's hand entry, contract 3155):
  * detection is CLIENT-keyed ("California Alliance of Family Owned Businesses");
  * rates are GROSS; the agency on ANAGRAF (Matson Media LLC, 483, 0%) is the buyer;
  * the sheet carries NO year — week columns ("5-Oct") take the year in which the
    first column is a Monday nearest today, and must ascend 7 days apart;
  * columns are mapped by HEADER LABEL; money cells may carry stray spaces
    ("$ 2 ,400.00"); every line, the TOTAL row and the grand total must reconcile
    or the parse refuses.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import List, Optional

import pdfplumber

CLIENT_NAME = "California Alliance of Family Owned Businesses PAC"
CLIENT_KEY = "california alliance of family owned businesses"

_MONTHS = {
    m: i
    for i, m in enumerate(
        ("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"), 1
    )
}
_LANGUAGES = (
    ("VIETNAMESE", "Vietnamese"),
    ("VIET", "Vietnamese"),
    ("MANDARIN", "Mandarin"),
    ("CANTONESE", "Cantonese"),
    ("CHINESE", "Chinese"),
    ("FILIPINO", "Filipino"),
    ("TAGALOG", "Filipino"),
    ("KOREAN", "Korean"),
    ("HMONG", "Hmong"),
    ("JAPANESE", "Japanese"),
    ("SOUTH ASIAN", "South Asian"),
    ("PUNJABI", "Punjabi"),
    ("HINDI", "South Asian"),
)
_MARKET_HINTS = (
    ("SF BAY", "SFO"),
    ("SAN FRANCISCO", "SFO"),
    ("SACRAMENTO", "CVC"),
    ("CENTRAL VALLEY", "CVC"),
    ("LOS ANGELES", "LAX"),
    ("SEATTLE", "SEA"),
    ("HOUSTON", "HOU"),
    ("CHICAGO", "CMP"),
    ("MINNEAPOLIS", "CMP"),
    ("WASHINGTON", "WDC"),
    ("NEW YORK", "NYC"),
)


class CAFOBParseError(ValueError):
    pass


def is_cafob_text(text: str) -> bool:
    return CLIENT_KEY in " ".join((text or "").split()).lower()


# ─── helpers ─────────────────────────────────────────────────────────────────


def _norm(s) -> str:
    return " ".join(str(s or "").replace("\n", " ").split())


def _money(cell: str) -> Optional[float]:
    """'$ 2 ,400.00' → 2400.0; unreadable → None (never 0 — a zero rate is a value)."""
    s = re.sub(r"[\s$,]", "", str(cell or ""))
    if not re.fullmatch(r"-?\d+(\.\d+)?", s):
        return None
    return float(s)


def _int(cell: str) -> Optional[int]:
    s = _norm(cell)
    return int(s) if re.fullmatch(r"\d+", s) else None


def _field(text: str, label: str) -> str:
    m = re.search(rf"{re.escape(label)}\s*:?\s*(.*)", text)
    return _norm(m.group(1)) if m else ""


def infer_year(month: int, day: int, today: Optional[date] = None) -> int:
    """The sheet prints week starts without a year. Prefer the year (last / this /
    next) in which the date is a Monday, nearest to today."""
    today = today or date.today()
    cands = []
    for y in (today.year - 1, today.year, today.year + 1):
        try:
            d = date(y, month, day)
        except ValueError:
            continue
        cands.append((d.weekday() != 0, abs((d - today).days), y))
    if not cands:
        raise CAFOBParseError(f"impossible week date {month}/{day}")
    return sorted(cands)[0][2]


def language_of(block: str) -> tuple[str, str]:
    """'VIETNAMESE TALK/VARIETY' → ('Vietnamese', 'Talk/Variety')."""
    up = _norm(block).upper()
    for key, lang in _LANGUAGES:
        if up.startswith(key):
            program = _norm(block)[len(key) :].strip(" -:/")
            return lang, program.title() if program.isupper() else program
    return "", _norm(block)


def market_of(text: str) -> str:
    up = _norm(text).upper()
    for key, code in _MARKET_HINTS:
        if key in up:
            return code
    return ""


# ─── model ───────────────────────────────────────────────────────────────────


@dataclass
class CAFOBLine:
    language_block: str  # 'VIETNAMESE NEWS'
    daypart: str  # 'M-F 11A-11:30A'
    gross_rate: float
    weekly_spots: List[int]
    total_spots: int
    gross_total: float
    length_sec: int
    language: str
    program: str
    is_bonus: bool = False

    @property
    def days(self) -> str:
        return self.daypart.split(" ", 1)[0]

    @property
    def time(self) -> str:
        parts = self.daypart.split(" ", 1)
        return parts[1] if len(parts) > 1 else ""

    @property
    def rate(self) -> float:  # duck-typed by the generic bridge normalizer
        return 0.0 if self.is_bonus else self.gross_rate

    @property
    def duration(self) -> str:
        return str(self.length_sec)

    @property
    def description(self) -> str:
        return f"{'BNS ' if self.is_bonus else ''}{self.days} {self.language} {self.program} {self.time}".strip()


@dataclass
class CAFOBOrder:
    client: str
    agency_label: str  # 'National Media' — the label printed before the client name
    contact: str
    email: str
    billing_cycle: str  # 'Broadcast' / 'Calendar'
    market_text: str
    channel: str
    flight_start: str  # MM/DD/YYYY
    flight_end: str
    length_sec: int
    separation_minutes: Optional[int]
    week_dates: List[date] = field(default_factory=list)
    lines: List[CAFOBLine] = field(default_factory=list)
    total_units: int = 0
    total_gross: float = 0.0
    source_path: str = ""
    rates_are_net: bool = False  # GROSS sheet

    @property
    def market(self) -> str:
        return market_of(self.market_text)

    @property
    def market_code(self) -> str:
        return self.market

    @property
    def agency(self) -> str:
        return self.agency_label

    @property
    def estimate_number(self) -> str:
        return ""

    @property
    def description(self) -> str:
        return f"{self.client} {self.flight_start}-{self.flight_end}"

    @property
    def paid_lines(self) -> List[CAFOBLine]:
        return [ln for ln in self.lines if not ln.is_bonus]

    @property
    def total_spots(self) -> int:
        return sum(ln.total_spots for ln in self.lines)

    @property
    def total_cost(self) -> float:
        return round(sum(ln.gross_total for ln in self.paid_lines), 2)

    @property
    def notes(self) -> str:
        return f"{self.separation_minutes} minute separation" if self.separation_minutes else ""


# ─── parse ───────────────────────────────────────────────────────────────────


def _week_header(cell: str, prev: Optional[date], today: Optional[date]) -> Optional[date]:
    """'5-Oct' → date. The first column infers its year (Monday nearest today); later
    columns follow the previous one, rolling the year over at a month decrease."""
    m = re.fullmatch(r"(\d{1,2})-([A-Za-z]{3})", _norm(cell))
    if not m:
        return None
    day, mon = int(m.group(1)), _MONTHS.get(m.group(2).upper())
    if not mon:
        return None
    if prev is None:
        return date(infer_year(mon, day, today), mon, day)
    return date(prev.year + (1 if mon < prev.month else 0), mon, day)


def parse_cafob(path: str, today: Optional[date] = None) -> CAFOBOrder:
    with pdfplumber.open(path) as pdf:
        texts = [pg.extract_text() or "" for pg in pdf.pages]
        tables = [t for pg in pdf.pages for t in (pg.extract_tables() or [])]
    text = "\n".join(texts)
    if not is_cafob_text(text):
        raise CAFOBParseError(f"not a {CLIENT_NAME} proposal: {Path(path).name}")

    # ── header block ──
    m = re.search(r"Client\s+(.+?):\s*(.+)", text)
    agency_label, client = (_norm(m.group(1)), _norm(m.group(2))) if m else ("", CLIENT_NAME)
    contact = _field(text, "Contact")
    email = _field(text, "Email")
    billing = _field(text, "Billing Cycle") or "Broadcast"
    market_text = _field(text, "Market")
    chan = re.search(r"Market:.*?\n(.*?)\n.*?Channel\s+(.*)", text)
    channel = _norm(f"{chan.group(1)} {chan.group(2)}") if chan else _field(text, "Channel")
    m = re.search(r"WEEK OF\s*(\d{1,2})/(\d{1,2})\s*-\s*(\d{1,2})/(\d{1,2})", text)
    if not m:
        raise CAFOBParseError("flight 'WEEK OF m/d - m/d' not found")
    fs_m, fs_d, fe_m, fe_d = (int(x) for x in m.groups())
    m = re.search(r"Airtime\s*:(\d{2,3})s", text)
    if not m:
        raise CAFOBParseError("spot length ('Airtime :30s') not found")
    length_sec = int(m.group(1))
    m = re.search(r"(\d{1,3})\s*minute separation", text, re.IGNORECASE)
    separation = int(m.group(1)) if m else None

    # ── grid: the table whose header names Language Block + Total Units ──
    grid, hdr_i = None, -1
    for t in tables:
        for i, row in enumerate(t):
            cells = [_norm(c) for c in row]
            if any("Language Block" in c for c in cells) and any("Total Unit" in c for c in cells):
                grid, hdr_i = t, i
                break
        if grid:
            break
    if grid is None:
        raise CAFOBParseError("airtime grid (Language Block / Total Units header) not found")
    header = [_norm(c) for c in grid[hdr_i]]

    def col(label: str, nth: int = 0) -> int:
        hits = [i for i, c in enumerate(header) if label.lower() in c.lower()]
        if len(hits) <= nth:
            raise CAFOBParseError(f"column {label!r} missing from the grid header: {header}")
        return hits[nth]

    c_lang, c_dp, c_rate, c_units, c_total = (
        col("Language Block"),
        col("Day Part"),
        col("GROSS", 0),
        col("Total Unit"),
        col("GROSS", 1),
    )
    weeks: List[tuple[int, date]] = []
    prev = None
    for i, c in enumerate(header):
        d = _week_header(c, prev, today)
        if d is not None:
            weeks.append((i, d))
            prev = d
    if not weeks:
        raise CAFOBParseError("no week columns ('5-Oct') in the grid header")
    week_dates = [d for _, d in weeks]
    if week_dates[0].weekday() != 0:
        raise CAFOBParseError(f"first week column {week_dates[0]} is not a Monday")
    for a, b in zip(week_dates, week_dates[1:]):
        if (b - a).days != 7:
            raise CAFOBParseError(f"week columns {a} → {b} are not 7 days apart")

    flight_start = date(week_dates[0].year, fs_m, fs_d)
    if flight_start != week_dates[0]:
        raise CAFOBParseError(
            f"flight start {fs_m}/{fs_d} is not the first week column {week_dates[0]}"
        )
    flight_end = date(flight_start.year + (1 if fe_m < fs_m else 0), fe_m, fe_d)
    if not (week_dates[-1] <= flight_end <= week_dates[-1] + timedelta(days=6)):
        raise CAFOBParseError(
            f"flight end {flight_end} does not fall in the last week column {week_dates[-1]}"
        )

    # ── rows ──
    lines: List[CAFOBLine] = []
    total_row = None
    for row in grid[hdr_i + 1 :]:
        cells = [_norm(c) for c in row]
        if not any(cells):
            continue
        if cells[c_lang].upper().startswith("TOTAL"):
            total_row = cells
            break
        block, dp = cells[c_lang], cells[c_dp]
        if not block or not dp:
            raise CAFOBParseError(f"row with blank language/daypart: {cells}")
        spots = []
        for ci, _d in weeks:
            v = _int(cells[ci]) if cells[ci] else 0
            if v is None:
                raise CAFOBParseError(f"{block}: unreadable week cell {cells[ci]!r}")
            spots.append(v)
        units = _int(cells[c_units])
        rate = _money(cells[c_rate])
        gross = _money(cells[c_total])
        if units is None or rate is None or gross is None:
            raise CAFOBParseError(
                f"{block}: unreadable units/rate/gross {cells[c_units]!r} {cells[c_rate]!r} {cells[c_total]!r}"
            )
        if sum(spots) != units:
            raise CAFOBParseError(
                f"{block}: week cells sum to {sum(spots)}, Total Units says {units}"
            )
        is_bonus = rate == 0 or block.upper().startswith(("BNS", "BONUS"))
        if abs(rate * units - gross) > 0.005:
            raise CAFOBParseError(
                f"{block}: {rate} × {units} = {rate * units:.2f} ≠ GROSS {gross:.2f}"
            )
        lang, program = language_of(block)
        if not lang:
            raise CAFOBParseError(f"{block}: no language word recognised")
        lines.append(
            CAFOBLine(
                language_block=block,
                daypart=dp,
                gross_rate=rate,
                weekly_spots=spots,
                total_spots=units,
                gross_total=gross,
                length_sec=length_sec,
                language=lang,
                program=program,
                is_bonus=is_bonus,
            )
        )
    if not lines:
        raise CAFOBParseError("no airtime rows in the grid")
    if total_row is None:
        raise CAFOBParseError("TOTAL row not found under the grid")

    # ── reconcile against the sheet's own TOTAL row ──
    for k, (ci, d) in enumerate(weeks):
        want = _int(total_row[ci]) if total_row[ci] else 0
        got = sum(ln.weekly_spots[k] for ln in lines)
        if want != got:
            raise CAFOBParseError(f"week {d}: lines give {got} spots, TOTAL row says {want}")
    total_units = _int(total_row[c_units])
    total_gross = _money(total_row[c_total])
    if total_units is None or total_gross is None:
        raise CAFOBParseError(f"TOTAL row units/gross unreadable: {total_row}")
    if total_units != sum(ln.total_spots for ln in lines):
        raise CAFOBParseError(
            f"TOTAL units {total_units} ≠ lines {sum(ln.total_spots for ln in lines)}"
        )
    if abs(total_gross - sum(ln.gross_total for ln in lines)) > 0.005:
        raise CAFOBParseError(
            f"TOTAL gross {total_gross:.2f} ≠ lines {sum(ln.gross_total for ln in lines):.2f}"
        )

    return CAFOBOrder(
        client=client or CLIENT_NAME,
        agency_label=agency_label,
        contact=contact,
        email=email,
        billing_cycle=billing,
        market_text=market_text,
        channel=channel,
        flight_start=f"{flight_start.month:02d}/{flight_start.day:02d}/{flight_start.year}",
        flight_end=f"{flight_end.month:02d}/{flight_end.day:02d}/{flight_end.year}",
        length_sec=length_sec,
        separation_minutes=separation,
        week_dates=week_dates,
        lines=lines,
        total_units=total_units,
        total_gross=total_gross,
        source_path=str(path),
    )


if __name__ == "__main__":  # pragma: no cover
    import sys

    for p in sys.argv[1:]:
        o = parse_cafob(p)
        print(
            f"{Path(p).name}: {o.client} via {o.agency_label} | {o.market} | {o.flight_start}–{o.flight_end} | "
            f":{o.length_sec} | sep {o.separation_minutes} | {o.total_spots} spots ${o.total_cost:,.2f}"
        )
        for ln in o.lines:
            print(f"   {ln.description:<40} {ln.weekly_spots} = {ln.total_spots} @ {ln.gross_rate}")
