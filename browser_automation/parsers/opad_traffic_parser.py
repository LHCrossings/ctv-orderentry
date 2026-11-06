"""
Parse opAD Media "TRAFFIC INSTRUCTIONS" PDFs (NYS Dept of Health / NYSOH).

One page per sheet: an ``EST#`` line, a ``***LANGUAGE***`` banner, a ``FROM:`` /
``TO:`` flight, then one table per spot length (``:30s`` / ``:15s`` headed
``ISCI Code Length Rotation``). opAD ships ONE creative language per sheet and
every NYS creative ever ingested is Mandarin (``…MH``), so the sheet's language
names the creative, not a subset of the contract's lines: every line of that
length inside the flight gets the creative (what Lee does by hand).

The sheet is its own oracle: each length's rotations must add to 100%, the
flight must have both dates, and a sheet with no ISCI rows REFUSES (a parser
that matches nothing is indistinguishable from an empty document).
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
# "NYS36ENRGQMH :30 100%" — an ISCI is one token of letters/digits (8+ chars).
_ROW_RE = re.compile(r"^([A-Z0-9]{8,})\s+:(\d{1,3})\s+([\d.]+)\s*%\s*$")
_LENGTH_HEADER_RE = re.compile(r"^:(\d{1,3})s\b", re.IGNORECASE)


@dataclass
class OpadTrafficSpot:
    isci: str
    duration_sec: int
    rotation_pct: float


@dataclass
class OpadTrafficInstruction:
    estimate: str
    advertiser: str  # the RE: line, e.g. "NYSDOH | NYSOH | Open Enrollment"
    language: str  # banner text, e.g. "MANDARIN"
    language_code: str  # CTV_LineLanguage code, e.g. "M"
    start_date: str  # display, "11/9/26"
    end_date: str
    date_from_sql: Optional[str] = None
    date_to_sql: Optional[str] = None
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


def is_opad_traffic_text(text: str) -> bool:
    """True for an opAD traffic sheet: their domain or name plus the EST# line."""
    upper = text.upper()
    if "TRAFFIC INSTRUCTIONS" not in upper or "EST#" not in upper:
        return False
    return "OPADMEDIA.COM" in upper or "OPAD MEDIA" in upper


def _parse_date(label: str, text: str) -> tuple:
    """Return (display 'M/D/YY', sql 'YYYY-MM-DD') for the ``label:`` line."""
    # The memo header has its own "From: <sender>" line; the flight line is the
    # FROM:/TO: whose value is a date, so walk every one and keep the first that is.
    values = re.findall(rf"^{label}:\s*(.+)$", text, re.IGNORECASE | re.MULTILINE)
    if not values:
        raise ValueError(f"opAD traffic sheet: no {label}: date line")
    dm = None
    for v in values:
        dm = _DATE_RE.search(v)
        if dm and dm.group(1).upper() in _MONTHS:
            break
        dm = None
    if not dm:
        raise ValueError(
            f"opAD traffic sheet: cannot read {label}: date from "
            + " / ".join(v.strip() for v in values)
        )
    month = _MONTHS[dm.group(1).upper()]
    day, year = int(dm.group(2)), int(dm.group(3))
    display = f"{month}/{day}/{year % 100:02d}"
    return display, f"{year:04d}-{month:02d}-{day:02d}"


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

    start_disp, start_sql = _parse_date("FROM", text)
    end_disp, end_sql = _parse_date("TO", text)
    if end_sql < start_sql:
        raise ValueError(f"opAD traffic sheet: TO {end_disp} is before FROM {start_disp}")

    spots: List[OpadTrafficSpot] = []
    current_len: Optional[int] = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        hm = _LENGTH_HEADER_RE.match(line)
        if hm:
            current_len = int(hm.group(1))
            continue
        rm = _ROW_RE.match(line)
        if not rm:
            continue
        row_len = int(rm.group(2))
        if current_len is not None and row_len != current_len:
            raise ValueError(
                f"opAD traffic sheet: {rm.group(1)} is :{row_len} under the :{current_len}s table"
            )
        spots.append(OpadTrafficSpot(rm.group(1), row_len, float(rm.group(3))))

    if not spots:
        raise ValueError(
            "opAD traffic sheet: no ISCI rows found — the layout may have changed; nothing to assign"
        )
    by_len: dict = {}
    for s in spots:
        by_len.setdefault(s.duration_sec, []).append(s)
    for dur, grp in by_len.items():
        total = sum(s.rotation_pct for s in grp)
        if abs(total - 100.0) > 0.5:
            raise ValueError(f"opAD traffic sheet: :{dur} rotations add to {total:g}%, not 100%")
        iscis = [s.isci for s in grp]
        if len(set(iscis)) != len(iscis):
            raise ValueError(f"opAD traffic sheet: duplicate ISCI in the :{dur}s table")

    return OpadTrafficInstruction(
        estimate=estimate,
        advertiser=advertiser,
        language=language,
        language_code=language_code,
        start_date=start_disp,
        end_date=end_disp,
        date_from_sql=start_sql,
        date_to_sql=end_sql,
        spots=spots,
    )


def parse_opad_traffic_pdf(pdf_bytes: bytes) -> OpadTrafficInstruction:
    """Parse one opAD traffic PDF (every page's text is read)."""
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        text = "\n".join(page.extract_text() or "" for page in pdf.pages)
    return parse_opad_traffic_text(text)
