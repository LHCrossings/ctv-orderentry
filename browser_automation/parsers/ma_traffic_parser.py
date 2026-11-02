"""
Parse Marketing Architects (WorldLink/MA) traffic instruction PDFs.

One PDF per advertiser/duration/month. Contains ISCI codes with % Run rotation
and flight date ranges, one per ROW. A PDF may carry several date segments
(week 1 = one creative at 100%, the rest of the month = four at 25%), so the
flight dates belong on the SPOT, never on the instruction: ``periods`` groups
the rows by (start, end) in file order and the instruction-level dates are the
overall min/max, for contract search only. Every page's text is read.

(Until 2026-11-02 the parser read page 1 only and kept the ONE segment with the
most ISCIs as "the primary rotation"; every other segment was dropped.)
"""

import io
import re
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class MASpot:
    isci: str
    title: str
    duration_sec: int  # 15 or 30
    rotation_pct: float
    date_from_sql: Optional[str] = None  # this row's own flight window
    date_to_sql: Optional[str] = None
    date_from_display: str = ""
    date_to_display: str = ""


@dataclass
class MAPeriod:
    """One flight window: every row carrying the same start/end dates."""

    date_from_sql: Optional[str]
    date_to_sql: Optional[str]
    date_from_display: str
    date_to_display: str
    spots: List[MASpot] = field(default_factory=list)


# Maps client code (from "Client XXXX - ..." header) to a better search term.
_SEARCH_OVERRIDES: dict = {
    "prnt": "4imprint",
    "afca": "Affordable",
    "drve": "Drive",
    "cote": "Coterie",
    "frmb": "Framebridge",
    "hct1": "HurryCane",
}


def _search_suggestion(client_code: str, client_name: str) -> str:
    key = client_code.lower()
    if key in _SEARCH_OVERRIDES:
        return _SEARCH_OVERRIDES[key]
    return client_name.split()[0] if client_name else client_code


def _parse_ma_date(s: str) -> Optional[str]:
    """Parse '5/26/2025' or '5/26/25' → '2025-05-26'. Tolerates PDF space artifacts."""
    s = re.sub(r"(/\d{3})\s+(\d)", r"\1\2", s.strip())
    m = re.match(r"^(\d{1,2})/(\d{1,2})/(\d{2,4})$", s)
    if not m:
        return None
    month, day, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if year < 100:
        year += 2000
    return f"{year:04d}-{month:02d}-{day:02d}"


def _short(sql_date: str) -> str:
    """'2025-05-26' → '5/26'"""
    p = sql_date.split("-")
    return f"{int(p[1])}/{int(p[2])}" if len(p) == 3 else sql_date


@dataclass
class MATrafficInstruction:
    advertiser: str  # "4imprint"
    client_code: str  # "PRNT"
    product_code: str  # "PRTV"
    search_suggestion: str
    date_from_sql: Optional[str]  # earliest segment start in the file
    date_to_sql: Optional[str]  # latest segment end in the file
    date_from_display: str
    date_to_display: str
    spots: List[MASpot] = field(default_factory=list)
    periods: List[MAPeriod] = field(default_factory=list)


# Data rows: ISCI  title  phone  :dur  pct  start_date  end_date
_ROW_PAT = re.compile(
    r"^([A-Z0-9]{2,}-\d+XX[A-Z0-9]+[A-Z])\s+"  # ISCI (contains XX)
    r"(.+?)\s+"  # creative title
    r"\d{3}-\d{3}-\d{4}\s+"  # (800)TFN — discard
    r":\d+\s+"  # length column — discard
    r"(\d+)\s+"  # % Run
    r"(\d{1,2}/\d{1,2}/\d{4})\s+"  # start date
    r"(\d{1,2}/\d{1,2}/\d{4})",  # end date
    re.MULTILINE,
)


def build_instruction(text: str) -> MATrafficInstruction:
    """Assemble the instruction from the PDF's full text (every page joined)."""
    # Fix PDF year artifact: "6/29/202 5" → "6/29/2025"
    # Only applies when preceded by "/" (inside a date) and followed by whitespace/EOL.
    text = re.sub(r"(/\d{3})\s+(\d)(?=\s|$)", r"\1\2", text, flags=re.MULTILINE)

    # Client: "Client PRNT - 4imprint Traffic Instructions"
    client_code = ""
    client_name = ""
    m = re.search(r"Client\s+([A-Z0-9]+)\s+-\s+(.+?)\s+Traffic Instructions", text)
    if m:
        client_code = m.group(1).strip()
        client_name = m.group(2).strip()

    # Product: "Product PRTV - 4imprint TV"
    product_code = ""
    m = re.search(r"Product\s+([A-Z0-9]+)\s+-\s+", text)
    if m:
        product_code = m.group(1).strip()

    # Duration from header: "Length :15 - (015)"
    duration_sec = 30
    m = re.search(r"Length\s+(:\d+)", text)
    if m:
        dm = re.search(r"\d+", m.group(1))
        duration_sec = int(dm.group()) if dm else 30

    # Group rows by (start_date, end_date) in file order; pdfplumber can double-render
    # a row, so within one segment an ISCI is updated, never appended.
    segments: dict = {}  # {(start_sql, end_sql): {isci: (title, pct)}}
    for row in _ROW_PAT.finditer(text):
        isci = row.group(1)
        title = row.group(2).strip()
        pct = float(row.group(3))
        start_sql = _parse_ma_date(row.group(4))
        end_sql = _parse_ma_date(row.group(5))
        if not start_sql or not end_sql:
            continue
        segments.setdefault((start_sql, end_sql), {})[isci] = (title, pct)

    periods: List[MAPeriod] = []
    spots: List[MASpot] = []
    for (start_sql, end_sql), rows in segments.items():
        per = MAPeriod(
            date_from_sql=start_sql,
            date_to_sql=end_sql,
            date_from_display=_short(start_sql),
            date_to_display=_short(end_sql),
        )
        for isci, (title, pct) in rows.items():
            sp = MASpot(
                isci=isci,
                title=title,
                duration_sec=duration_sec,
                rotation_pct=pct,
                date_from_sql=start_sql,
                date_to_sql=end_sql,
                date_from_display=per.date_from_display,
                date_to_display=per.date_to_display,
            )
            per.spots.append(sp)
            spots.append(sp)
        periods.append(per)

    date_from_sql = min(k[0] for k in segments) if segments else None
    date_to_sql = max(k[1] for k in segments) if segments else None

    return MATrafficInstruction(
        advertiser=client_name,
        client_code=client_code,
        product_code=product_code,
        search_suggestion=_search_suggestion(client_code, client_name),
        date_from_sql=date_from_sql,
        date_to_sql=date_to_sql,
        date_from_display=_short(date_from_sql) if date_from_sql else "",
        date_to_display=_short(date_to_sql) if date_to_sql else "",
        spots=spots,
        periods=periods,
    )


def parse_ma_traffic_pdf(pdf_bytes: bytes) -> MATrafficInstruction:
    """Parse a Marketing Architects traffic instruction PDF, EVERY page."""
    import pdfplumber

    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        text = "\n".join(page.extract_text() or "" for page in pdf.pages)
    return build_instruction(text)
