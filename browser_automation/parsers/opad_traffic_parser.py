"""
Parse opAD Media "TRAFFIC INSTRUCTIONS" PDFs (NYS Dept of Health / NYSOH).

One page per sheet: an ``EST#`` line, a ``***LANGUAGE***`` banner, then one or
more FLIGHTS. A flight is a ``FROM:`` / ``TO:`` pair (two lines, or one line since
the 11/24/26 revision) followed by one table per spot length (``:30s`` / ``:15s``
headed ``ISCI Code Length Rotation``). A revision sheet carries several flights,
each with its own rotation, and the last ``TO:`` can carry a clock cutoff
(``10PM Cutoff``): the creative stops at that time on the flight's last day.
opAD ships ONE creative language per sheet and every NYS creative ever ingested
is Mandarin (``…MH``), so the sheet's language names the creative, not a subset
of the contract's lines: every line of that length inside the flight gets the
creative (what Lee does by hand).

The sheet is its own oracle: within each flight each length's rotations must add
to 100%, every flight must have both dates, flights must not overlap, text after
a ``TO:`` date that is not a cutoff REFUSES, and a sheet with no ISCI rows
REFUSES (a parser that matches nothing is indistinguishable from an empty
document).
"""

import io
import re
from dataclasses import dataclass, field
from typing import List, Optional

import pdfplumber

OPAD_AGENCY_ID = 21  # CONTRATTITESTATA.AGENZIA for opAD Media Solutions

_MONTHS = {
    "JANUARY": 1,
    "FEBRUARY": 2,
    "MARCH": 3,
    "APRIL": 4,
    "MAY": 5,
    "JUNE": 6,
    "JULY": 7,
    "AUGUST": 8,
    "SEPTEMBER": 9,
    "OCTOBER": 10,
    "NOVEMBER": 11,
    "DECEMBER": 12,
}

# Banner language → CTV_LineLanguage code (what the card shows next to the lines).
_LANGUAGE_CODES = {
    "MANDARIN": "M",
    "CANTONESE": "C",
    "CHINESE": "M/C",
    "KOREAN": "K",
    "VIETNAMESE": "V",
    "FILIPINO": "T",
    "TAGALOG": "T",
    "HINDI": "SA",
    "PUNJABI": "P",
    "JAPANESE": "J",
    "HMONG": "Hm",
}

# "FROM: MONDAY, NOVEMBER 9, 2026" — the weekday is optional, the year is not.
_DATE_RE = re.compile(
    r"(?:[A-Z]+,\s*)?([A-Z]+)\s+(\d{1,2}),?\s*(\d{4})",
    re.IGNORECASE,
)
# "NYS36ENRGQMH :30 100%" — an ISCI is one token of letters/digits (8+ chars). A
# revision row can carry a remark after the percentage ("NEW SPOT IN ROTATION").
_ROW_RE = re.compile(r"^([A-Z0-9]{8,})\s+:(\d{1,3})\s+([\d.]+)\s*%\s*(.*)$")
_LENGTH_HEADER_RE = re.compile(r"^:(\d{1,3})s\b", re.IGNORECASE)
# "10PM Cutoff" / "10:00 PM cut-off" after the TO: date.
_CUTOFF_RE = re.compile(r"(\d{1,2})(?::(\d{2}))?\s*([AP])\.?M\.?\s*CUT-?\s*OFF\.?", re.IGNORECASE)


@dataclass
class OpadTrafficSpot:
    isci: str
    duration_sec: int
    rotation_pct: float
    note: str = ""  # remark printed after the percentage


@dataclass
class OpadTrafficPeriod:
    """One flight of the sheet: its own dates, optional last-day cutoff, own rotation."""

    start_date: str  # display, "11/30/26"
    date_from_sql: str
    end_date: str = ""
    date_to_sql: str = ""
    cutoff: Optional[str] = None  # "22:00" = stop at 10 PM on the last day
    spots: List[OpadTrafficSpot] = field(default_factory=list)

    @property
    def durations(self) -> List[int]:
        seen: List[int] = []
        for s in self.spots:
            if s.duration_sec not in seen:
                seen.append(s.duration_sec)
        return seen

    def spots_for(self, duration_sec: int) -> List[OpadTrafficSpot]:
        return [s for s in self.spots if s.duration_sec == duration_sec]


@dataclass
class OpadTrafficInstruction:
    estimate: str
    advertiser: str  # the RE: line, e.g. "NYSDOH | NYSOH | Open Enrollment"
    language: str  # banner text, e.g. "MANDARIN"
    language_code: str  # CTV_LineLanguage code, e.g. "M"
    start_date: str  # display, first flight's start
    end_date: str  # display, last flight's end
    date_from_sql: Optional[str] = None
    date_to_sql: Optional[str] = None
    periods: List[OpadTrafficPeriod] = field(default_factory=list)

    @property
    def spots(self) -> List[OpadTrafficSpot]:
        """Every flight's rows in sheet order (one flight = that flight's rows)."""
        return [s for p in self.periods for s in p.spots]

    @property
    def durations(self) -> List[int]:
        seen: List[int] = []
        for s in self.spots:
            if s.duration_sec not in seen:
                seen.append(s.duration_sec)
        return seen

    def spots_for(self, duration_sec: int) -> List[OpadTrafficSpot]:
        return [s for s in self.spots if s.duration_sec == duration_sec]


def is_opad_traffic_text(text: str) -> bool:
    """True for an opAD traffic sheet: their domain or name plus the EST# line."""
    upper = text.upper()
    if "TRAFFIC INSTRUCTIONS" not in upper or "EST#" not in upper:
        return False
    return "OPADMEDIA.COM" in upper or "OPAD MEDIA" in upper


def _read_date(value: str):
    """Return (display 'M/D/YY', sql 'YYYY-MM-DD', text after the date) or None."""
    dm = _DATE_RE.search(value)
    if not dm or dm.group(1).upper() not in _MONTHS:
        return None
    month = _MONTHS[dm.group(1).upper()]
    day, year = int(dm.group(2)), int(dm.group(3))
    return (
        f"{month}/{day}/{year % 100:02d}",
        f"{year:04d}-{month:02d}-{day:02d}",
        value[dm.end() :].strip(),
    )


def _close_period(period: OpadTrafficPeriod, value: str) -> None:
    """Set the flight's end from the text after ``TO:`` (date + optional cutoff)."""
    got = _read_date(value)
    if not got:
        raise ValueError(f"opAD traffic sheet: cannot read TO: date from {value.strip()!r}")
    period.end_date, period.date_to_sql, rest = got
    # The sheet draws an arrow glyph (a private-use character) in front of the remark.
    rest = re.sub(r"^\W+", "", rest)
    if rest:
        cm = _CUTOFF_RE.fullmatch(rest)
        if not cm:
            raise ValueError(
                f"opAD traffic sheet: cannot read {rest!r} after the TO: date {period.end_date}"
            )
        hour = int(cm.group(1)) % 12 + (12 if cm.group(3).upper() == "P" else 0)
        period.cutoff = f"{hour:02d}:{int(cm.group(2) or 0):02d}"
    if period.date_to_sql < period.date_from_sql:
        raise ValueError(
            f"opAD traffic sheet: TO {period.end_date} is before FROM {period.start_date}"
        )


def parse_opad_traffic_text(text: str) -> OpadTrafficInstruction:
    """Parse the extracted text of one opAD traffic sheet."""
    est_m = re.search(r"EST#?\s*:?\s*(\d+)", text, re.IGNORECASE)
    if not est_m:
        raise ValueError("opAD traffic sheet: no EST# line")
    estimate = est_m.group(1)

    re_m = re.search(r"^RE:\s*(.+)$", text, re.IGNORECASE | re.MULTILINE)
    advertiser = re_m.group(1).strip() if re_m else ""

    lang_m = re.search(r"\*{2,}\s*([A-Z /]+?)\s*\*{2,}", text)
    language = lang_m.group(1).strip().upper() if lang_m else ""
    language_code = _LANGUAGE_CODES.get(language, "")

    periods: List[OpadTrafficPeriod] = []
    current: Optional[OpadTrafficPeriod] = None
    current_len: Optional[int] = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        # The memo header has its own "To: <desk>" / "From: <sender>" lines; a flight
        # line is the FROM:/TO: whose value is a date.
        fm = re.match(r"^FROM:\s*(.+)$", line, re.IGNORECASE)
        if fm:
            parts = re.split(r"\bTO:\s*", fm.group(1), maxsplit=1, flags=re.IGNORECASE)
            got = _read_date(parts[0])
            if not got:
                continue
            if current is not None and not current.date_to_sql:
                raise ValueError(
                    f"opAD traffic sheet: the flight from {current.start_date} has no TO: date"
                )
            if got[2]:
                raise ValueError(
                    f"opAD traffic sheet: cannot read {got[2]!r} after the FROM: date {got[0]}"
                )
            current = OpadTrafficPeriod(start_date=got[0], date_from_sql=got[1])
            current_len = None
            periods.append(current)
            if len(parts) == 2:
                _close_period(current, parts[1])
            continue
        tm = re.match(r"^TO:\s*(.+)$", line, re.IGNORECASE)
        if tm:
            if not _read_date(tm.group(1)):
                continue
            if current is None or current.date_to_sql:
                raise ValueError("opAD traffic sheet: a TO: date with no FROM: date before it")
            _close_period(current, tm.group(1))
            continue
        hm = _LENGTH_HEADER_RE.match(line)
        if hm:
            current_len = int(hm.group(1))
            continue
        rm = _ROW_RE.match(line)
        if not rm:
            continue
        if current is None:
            raise ValueError(f"opAD traffic sheet: {rm.group(1)} is listed before any FROM: flight")
        row_len = int(rm.group(2))
        if current_len is not None and row_len != current_len:
            raise ValueError(
                f"opAD traffic sheet: {rm.group(1)} is :{row_len} under the :{current_len}s table"
            )
        current.spots.append(
            OpadTrafficSpot(
                rm.group(1), row_len, float(rm.group(3)), re.sub(r"^\W+", "", rm.group(4)).strip()
            )
        )

    if not periods:
        raise ValueError("opAD traffic sheet: no FROM: date line")
    if not any(p.spots for p in periods):
        raise ValueError(
            "opAD traffic sheet: no ISCI rows found — the layout may have changed; nothing to assign"
        )
    prev: Optional[OpadTrafficPeriod] = None
    for p in periods:
        label = f"{p.start_date}–{p.end_date}"
        if not p.date_to_sql:
            raise ValueError(f"opAD traffic sheet: the flight from {p.start_date} has no TO: date")
        if not p.spots:
            raise ValueError(f"opAD traffic sheet: the flight {label} has no ISCI rows")
        if prev is not None and p.date_from_sql <= prev.date_to_sql:
            raise ValueError(
                f"opAD traffic sheet: the flight {label} starts on or before the end of "
                f"{prev.start_date}–{prev.end_date}"
            )
        for dur in p.durations:
            grp = p.spots_for(dur)
            total = sum(s.rotation_pct for s in grp)
            if abs(total - 100.0) > 0.5:
                raise ValueError(
                    f"opAD traffic sheet: :{dur} rotations in {label} add to {total:g}%, not 100%"
                )
            iscis = [s.isci for s in grp]
            if len(set(iscis)) != len(iscis):
                raise ValueError(
                    f"opAD traffic sheet: duplicate ISCI in the :{dur}s table of {label}"
                )
        prev = p

    return OpadTrafficInstruction(
        estimate=estimate,
        advertiser=advertiser,
        language=language,
        language_code=language_code,
        start_date=periods[0].start_date,
        end_date=periods[-1].end_date,
        date_from_sql=periods[0].date_from_sql,
        date_to_sql=periods[-1].date_to_sql,
        periods=periods,
    )


def parse_opad_traffic_pdf(pdf_bytes: bytes) -> OpadTrafficInstruction:
    """Parse one opAD traffic PDF (every page's text is read)."""
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        text = "\n".join(page.extract_text() or "" for page in pdf.pages)
    return parse_opad_traffic_text(text)
