"""
Crossings TV Media Plan parser (Marquice Wong's house template, any advertiser).

One page. A header block, one airtime table, a summary box and, optionally, a
"Translation Services" section:

    Crossings TV Media Plan OPTION 1
    Date: 11/20/2026 Station: Crossings TV
    Adertiser: Soroudi Acct Rep: Marquice Wong          (the sheet spells it "Adertiser")
    Client Email: ... Email: ...
    Client Phone: ... Phone: ...
    Market Los Angeles

    Language Block | Day Part/Program | Length | Start Date | End Date | Spot Type |
    Total Spot # | SPOT COST | Total Airtime Cost | Spot Value | Line Total Value
    Punjabi | M-Fri(2p-4p) | 30 seconds | 12/1/2026 | 1/1/2027 | COM | 45 | $ 80.00 | $ 3,600.00 | ...
    Punjabi | ROS BONUS    | 30 seconds | 12/1/2026 | 1/1/2027 | BON | 45 | $ -     | $ -        | ...
                                                     Subtotal | 90 |         | $ 3,600.00 | ...

    Translation Services ... Fee: $600

Rules:
  * The same template is read for Sierra Donor Services by ``sierra_parser``; that
    client keeps its own order type. Detection sends every OTHER advertiser here
    (the route is keyed on the client, the reader on the template).
  * Columns are mapped by header label, never by position.
  * A row's count is the TOTAL for its own Start/End dates (no weekly columns), so
    each row enters as one Rotation line.
  * ``ROS BONUS`` takes the language's house ROS window (``ros_definitions``).
  * ``Fee: $N`` under Translation Services is production: it goes in the first paid
    line's Production box, never a line.
  * The sheet is its own oracle: per row ``rate x spots == Total Airtime Cost``, and
    the rows must add to the Subtotal row (spots and cost). A mismatch REFUSES.
  * A row with two dayparts ("M-F (7p-12a), Sat-Sun 8p-12a") carries one count for
    both; the sheet does not say how to split it, so it REFUSES by name.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import List, Optional

from browser_automation.ros_definitions import ROS_SCHEDULES

TEMPLATE_TITLE = "Crossings TV Media Plan"

_MARKET_MAP = {
    "central valley": "CVC",
    "sacramento": "CVC",
    "san francisco": "SFO",
    "los angeles": "LAX",
    "seattle": "SEA",
    "houston": "HOU",
    "chicago": "CMP",
    "minneapolis": "CMP",
    "washington": "WDC",
    "new york": "NYC",
    "dallas": "DAL",
}

# Day tokens as the AEs type them -> the canonical token parse_day_bits reads.
_DAY_TOKENS = {
    "m": "M", "mon": "M", "monday": "M",
    "t": "Tu", "tu": "Tu", "tue": "Tu", "tues": "Tu", "tuesday": "Tu",
    "w": "W", "wed": "W", "wednesday": "W",
    "th": "Th", "thu": "Th", "thur": "Th", "thurs": "Th", "thursday": "Th", "r": "Th",
    "f": "F", "fri": "F", "friday": "F",
    "sa": "Sa", "sat": "Sa", "saturday": "Sa",
    "su": "Su", "sun": "Su", "sunday": "Su",
}  # fmt: skip

# Header label -> field. Matched on the label text with whitespace collapsed.
_COLUMNS = {
    "language": ("language block",),
    "daypart": ("day part",),
    "length": ("length",),
    "start": ("start date",),
    "end": ("end date",),
    "spot_type": ("spot type",),
    "spots": ("total spot",),
    "rate": ("spot cost",),
    "cost": ("total airtime",),
}

_MONEY_RE = re.compile(r"\$\s*([\d][\d ,]*(?:\.\d+)?)")
_TIME_RE = re.compile(
    r"(\d{1,2}(?::\d{2})?\s*[ap]m?)\s*-\s*(\d{1,2}(?::\d{2})?\s*[ap]m?)", re.IGNORECASE
)


@dataclass
class MediaPlanLine:
    language: str  # "Punjabi"
    daypart_raw: str  # "M-Fri(2p-4p)" / "ROS BONUS"
    days: str  # "M-F"
    time_str: str  # "2p-4p"
    duration_seconds: int
    start_date: date
    end_date: date
    spot_type: str  # "COM" / "BON"
    is_bonus: bool
    total_spots: int
    rate: float  # per spot, 0.0 for a bonus row


@dataclass
class MediaPlanOrder:
    advertiser: str  # as the sheet prints it: "Soroudi"
    acct_rep: str
    market_label: str  # "Los Angeles"
    market: str  # "LAX" ("" when the label maps to no station)
    order_date: Optional[date]
    client_email: str
    client_phone: str
    option: str  # "OPTION 1"
    lines: List[MediaPlanLine] = field(default_factory=list)
    production: float = 0.0  # Translation Services fee
    production_note: str = ""  # "Translation: Punjabi"
    rates_are_net: bool = False
    pdf_path: str = ""

    @property
    def paid_lines(self) -> List[MediaPlanLine]:
        return [ln for ln in self.lines if not ln.is_bonus]

    @property
    def bonus_lines(self) -> List[MediaPlanLine]:
        return [ln for ln in self.lines if ln.is_bonus]

    @property
    def airtime_total(self) -> float:
        return round(sum(ln.rate * ln.total_spots for ln in self.paid_lines), 2)

    @property
    def start(self) -> date:
        return min(ln.start_date for ln in self.lines)

    @property
    def end(self) -> date:
        return max(ln.end_date for ln in self.lines)


def is_mediaplan_text(text: str) -> bool:
    """True for the house Media Plan template (any advertiser)."""
    return (
        TEMPLATE_TITLE in text
        and "Language Block" in text
        and re.search(r"\bAd(?:v)?ertiser\s*:", text) is not None
    )


# ─── Cells ────────────────────────────────────────────────────────────────────


def _flat(v) -> str:
    return re.sub(r"\s+", " ", str(v or "")).strip()


def _money(v) -> Optional[float]:
    """'$ 3,600.00' -> 3600.0, '$ -' -> 0.0, unreadable or empty -> None."""
    s = _flat(v)
    if not s:
        return None
    if re.fullmatch(r"\$?\s*-", s):
        return 0.0
    m = _MONEY_RE.search(s) or re.fullmatch(r"([\d][\d ,]*(?:\.\d+)?)", s)
    if not m:
        return None
    return float(m.group(1).replace(" ", "").replace(",", ""))


def _date(v, what: str) -> date:
    try:
        return datetime.strptime(_flat(v), "%m/%d/%Y").date()
    except ValueError:
        raise ValueError(f"Media Plan: cannot read the {what} {_flat(v)!r}")


def _day(token: str) -> str:
    got = _DAY_TOKENS.get(token.strip().lower().rstrip("."))
    if not got:
        raise ValueError(f"Media Plan: cannot read the day {token!r}")
    return got


def parse_daypart(raw: str) -> tuple[str, str]:
    """'M-Fri(2p-4p)' -> ('M-F', '2p-4p'); 'Sat-Sun 8p-12a' -> ('Sa-Su', '8p-12a')."""
    text = _flat(raw)
    times = _TIME_RE.findall(text)
    if len(times) != 1 or "," in text or ";" in text:
        raise ValueError(
            f"Media Plan: the daypart {text!r} is not one day pattern and one time window. "
            "A row with two dayparts carries one spot count for both; ask the AE to split it."
        )
    m = _TIME_RE.search(text)
    day_part = text[: m.start()].strip(" (")
    if not day_part:
        raise ValueError(f"Media Plan: the daypart {text!r} names no days")
    days = "-".join(_day(t) for t in re.split(r"\s*-\s*", day_part))
    time_str = "-".join(re.sub(r"\s+|m$", "", t.lower()) for t in times[0])
    return days, time_str


# ─── Sheet ────────────────────────────────────────────────────────────────────


def _header_field(text: str, label: str, stops: tuple[str, ...]) -> str:
    """The value after ``label`` up to the next known label on the line, or line end."""
    stop = "|".join(re.escape(s) for s in stops)
    m = re.search(
        rf"{label}\s*:?\s*(.*?)\s*(?:(?:{stop})\s*:|$)", text, re.IGNORECASE | re.MULTILINE
    )
    return m.group(1).strip() if m else ""


def _column_map(header: list) -> dict:
    cols: dict = {}
    for j, cell in enumerate(header):
        label = _flat(cell).lower()
        for name, keys in _COLUMNS.items():
            if name not in cols and any(k in label for k in keys):
                cols[name] = j
    missing = [n for n in _COLUMNS if n not in cols]
    if missing:
        raise ValueError(f"Media Plan: the airtime table has no column for {', '.join(missing)}")
    return cols


def build_order(text: str, tables: list, pdf_path: str = "") -> MediaPlanOrder:
    """Build the order from one page's text and tables."""
    table, hdr_i = None, -1
    for t in tables:
        for i, row in enumerate(t or []):
            if any("language block" in _flat(c).lower() for c in row or []):
                table, hdr_i = t, i
                break
        if table:
            break
    if table is None:
        raise ValueError("Media Plan: no airtime table (no 'Language Block' header row)")
    cols = _column_map(table[hdr_i])

    def cell(row, name):
        j = cols[name]
        return row[j] if j < len(row) else None

    lines: List[MediaPlanLine] = []
    subtotal: Optional[tuple] = None
    for row in table[hdr_i + 1 :]:
        if not row or not any(_flat(c) for c in row):
            continue
        if any(_flat(c).lower() == "subtotal" for c in row):
            subtotal = (_flat(cell(row, "spots")), _money(cell(row, "cost")))
            break
        language = _flat(cell(row, "language"))
        daypart = _flat(cell(row, "daypart"))
        if not language:
            raise ValueError(f"Media Plan: a row has no Language Block ({daypart!r})")
        spot_type = _flat(cell(row, "spot_type")).upper()
        if spot_type not in ("COM", "BON", "BNS"):
            raise ValueError(f"Media Plan: {language} {daypart}: unknown Spot Type {spot_type!r}")
        is_bonus = spot_type != "COM"
        spots_raw = _flat(cell(row, "spots"))
        if not spots_raw.isdigit() or int(spots_raw) == 0:
            raise ValueError(
                f"Media Plan: {language} {daypart}: cannot read Total Spot # {spots_raw!r}"
            )
        spots = int(spots_raw)
        rate = _money(cell(row, "rate"))
        cost = _money(cell(row, "cost"))
        if rate is None or cost is None:
            raise ValueError(
                f"Media Plan: {language} {daypart}: cannot read the spot cost or the total"
            )
        if is_bonus and (rate or cost):
            raise ValueError(f"Media Plan: {language} {daypart}: a bonus row carries money")
        if abs(rate * spots - cost) > 0.01:
            raise ValueError(
                f"Media Plan: {language} {daypart}: {spots} x ${rate:,.2f} is "
                f"${rate * spots:,.2f}, but the sheet's Total Airtime Cost is ${cost:,.2f}"
            )
        lm = re.search(r"(\d+)\s*sec", _flat(cell(row, "length")), re.IGNORECASE)
        if not lm:
            raise ValueError(f"Media Plan: {language} {daypart}: cannot read the Length")
        if "ROS" in daypart.upper():
            ros = ROS_SCHEDULES.get(language)
            if not ros:
                raise ValueError(f"Media Plan: {daypart}: no house ROS window for {language!r}")
            days, time_str = ros["days"], ros["time"]
        else:
            days, time_str = parse_daypart(daypart)
        start, end = _date(cell(row, "start"), "Start Date"), _date(cell(row, "end"), "End Date")
        if end < start:
            raise ValueError(f"Media Plan: {language} {daypart}: End Date is before Start Date")
        lines.append(
            MediaPlanLine(
                language=language,
                daypart_raw=daypart,
                days=days,
                time_str=time_str,
                duration_seconds=int(lm.group(1)),
                start_date=start,
                end_date=end,
                spot_type=spot_type,
                is_bonus=is_bonus,
                total_spots=spots,
                rate=0.0 if is_bonus else rate,
            )
        )

    if not lines:
        raise ValueError("Media Plan: the airtime table has no rows")
    if not any(not ln.is_bonus for ln in lines):
        raise ValueError("Media Plan: the airtime table has no paid row")
    if subtotal is None:
        raise ValueError("Media Plan: the airtime table has no Subtotal row")
    spots_total = sum(ln.total_spots for ln in lines)
    cost_total = round(sum(ln.rate * ln.total_spots for ln in lines), 2)
    if not subtotal[0].isdigit() or int(subtotal[0]) != spots_total:
        raise ValueError(
            f"Media Plan: the rows hold {spots_total} spots, the Subtotal row says {subtotal[0]!r}"
        )
    if subtotal[1] is None or abs(subtotal[1] - cost_total) > 0.01:
        raise ValueError(
            f"Media Plan: the rows add to ${cost_total:,.2f}, the Subtotal row says "
            f"{'nothing' if subtotal[1] is None else f'${subtotal[1]:,.2f}'}"
        )

    labels = ("Station", "Acct Rep", "Email", "Phone", "Client Email", "Client Phone")
    advertiser = _header_field(text, r"Ad(?:v)?ertiser", labels)
    if not advertiser:
        raise ValueError("Media Plan: no Advertiser line")
    market_label = ""
    mm = re.search(r"^Market\s*:?\s*(.+)$", text, re.IGNORECASE | re.MULTILINE)
    if mm:
        market_label = mm.group(1).strip()
    market = next((c for k, c in _MARKET_MAP.items() if k in market_label.lower()), "")
    dm = re.search(r"^Date\s*:\s*(\d{1,2}/\d{1,2}/\d{4})", text, re.MULTILINE)
    om = re.search(rf"{re.escape(TEMPLATE_TITLE)}\s*(.*)$", text, re.MULTILINE)

    production, production_note = 0.0, ""
    if re.search(r"Translation Services", text, re.IGNORECASE):
        tail = text[re.search(r"Translation Services", text, re.IGNORECASE).end() :]
        fm = re.search(r"^Fee\s*:?\s*(.+)$", tail, re.IGNORECASE | re.MULTILINE)
        fee = _money(fm.group(1)) if fm else None
        if fee is None:
            raise ValueError("Media Plan: a Translation Services section with no readable Fee")
        production = fee
        lg = re.search(r"^\*?\s*Languages?\s*:\s*(.+)$", tail, re.IGNORECASE | re.MULTILINE)
        production_note = f"Translation: {lg.group(1).strip()}" if lg else "Translation"

    return MediaPlanOrder(
        advertiser=advertiser,
        acct_rep=_header_field(text, "Acct Rep", labels),
        market_label=market_label,
        market=market,
        order_date=_date(dm.group(1), "Date") if dm else None,
        client_email=_header_field(text, "Client Email", ("Email", "Phone")),
        client_phone=_header_field(text, "Client Phone", ("Email", "Phone")),
        option=om.group(1).strip() if om else "",
        lines=lines,
        production=round(production, 2),
        production_note=production_note,
        pdf_path=pdf_path,
    )


def parse_mediaplan(path: str) -> MediaPlanOrder:
    """Parse one Media Plan PDF. The template is one page; more pages REFUSE."""
    import pdfplumber

    with pdfplumber.open(path) as pdf:
        pages = [(p.extract_text() or "", p.extract_tables()) for p in pdf.pages]
    with_grid = [p for p in pages if "Language Block" in p[0]]
    if len(with_grid) != 1:
        raise ValueError(
            f"Media Plan: expected one airtime table, found {len(with_grid)} "
            f"across {len(pages)} page(s)"
        )
    text = "\n".join(p[0] for p in pages)
    return build_order(text, with_grid[0][1], pdf_path=str(path))
