"""
Parse Marketing Architects (WorldLink/MA) traffic instruction PDFs.

One PDF per advertiser/duration/month. Contains ISCI codes with % Run rotation
and flight date ranges. Multiple date segments within one PDF are supported;
the segment with the most unique ISCIs is used for the primary rotation.
"""
import io
import re
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class MASpot:
    isci: str
    title: str
    duration_sec: int    # 15 or 30
    rotation_pct: float


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
    s = re.sub(r'(/\d{3})\s+(\d)', r'\1\2', s.strip())
    m = re.match(r'^(\d{1,2})/(\d{1,2})/(\d{2,4})$', s)
    if not m:
        return None
    month, day, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if year < 100:
        year += 2000
    return f"{year:04d}-{month:02d}-{day:02d}"


def _short(sql_date: str) -> str:
    """'2025-05-26' → '5/26'"""
    p = sql_date.split('-')
    return f"{int(p[1])}/{int(p[2])}" if len(p) == 3 else sql_date


@dataclass
class MATrafficInstruction:
    advertiser: str           # "4imprint"
    client_code: str          # "PRNT"
    product_code: str         # "PRTV"
    search_suggestion: str
    date_from_sql: Optional[str]
    date_to_sql: Optional[str]
    date_from_display: str
    date_to_display: str
    spots: List[MASpot] = field(default_factory=list)


def parse_ma_traffic_pdf(pdf_bytes: bytes) -> MATrafficInstruction:
    """Parse a Marketing Architects traffic instruction PDF."""
    import pdfplumber

    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        text = pdf.pages[0].extract_text() or ""

    # Fix PDF year artifact: "6/29/202 5" → "6/29/2025"
    # Only applies when preceded by "/" (inside a date) and followed by whitespace/EOL.
    text = re.sub(r'(/\d{3})\s+(\d)(?=\s|$)', r'\1\2', text, flags=re.MULTILINE)

    # Client: "Client PRNT - 4imprint Traffic Instructions"
    client_code = ""
    client_name = ""
    m = re.search(r'Client\s+([A-Z0-9]+)\s+-\s+(.+?)\s+Traffic Instructions', text)
    if m:
        client_code = m.group(1).strip()
        client_name = m.group(2).strip()

    # Product: "Product PRTV - 4imprint TV"
    product_code = ""
    m = re.search(r'Product\s+([A-Z0-9]+)\s+-\s+', text)
    if m:
        product_code = m.group(1).strip()

    # Duration from header: "Length :15 - (015)"
    duration_sec = 30
    m = re.search(r'Length\s+(:\d+)', text)
    if m:
        dm = re.search(r'\d+', m.group(1))
        duration_sec = int(dm.group()) if dm else 30

    # Data rows: ISCI  title  phone  :dur  pct  start_date  end_date
    row_pat = re.compile(
        r'^([A-Z0-9]{2,}-\d+XX[A-Z0-9]+[A-Z])\s+'  # ISCI (contains XX)
        r'(.+?)\s+'                                   # creative title
        r'\d{3}-\d{3}-\d{4}\s+'                      # (800)TFN — discard
        r':\d+\s+'                                    # length column — discard
        r'(\d+)\s+'                                   # % Run
        r'(\d{1,2}/\d{1,2}/\d{4})\s+'                # start date
        r'(\d{1,2}/\d{1,2}/\d{4})',                   # end date
        re.MULTILINE,
    )

    # Group rows by (start_date, end_date); deduplicate pdfplumber double-renders
    # by only updating (not appending) within each segment.
    segments: dict = {}  # {(start_sql, end_sql): {isci: (title, pct)}}

    for row in row_pat.finditer(text):
        isci      = row.group(1)
        title     = row.group(2).strip()
        pct       = float(row.group(3))
        start_sql = _parse_ma_date(row.group(4))
        end_sql   = _parse_ma_date(row.group(5))
        if not start_sql or not end_sql:
            continue
        key = (start_sql, end_sql)
        if key not in segments:
            segments[key] = {}
        segments[key][isci] = (title, pct)

    if not segments:
        return MATrafficInstruction(
            advertiser=client_name, client_code=client_code, product_code=product_code,
            search_suggestion=_search_suggestion(client_code, client_name),
            date_from_sql=None, date_to_sql=None,
            date_from_display="", date_to_display="",
        )

    date_from_sql = min(k[0] for k in segments)
    date_to_sql   = max(k[1] for k in segments)

    # Primary rotation = segment with the most unique ISCIs (largest creative set)
    primary = segments[max(segments, key=lambda k: len(segments[k]))]

    spots = [
        MASpot(isci=isci, title=t, duration_sec=duration_sec, rotation_pct=pct)
        for isci, (t, pct) in primary.items()
    ]

    return MATrafficInstruction(
        advertiser=client_name,
        client_code=client_code,
        product_code=product_code,
        search_suggestion=_search_suggestion(client_code, client_name),
        date_from_sql=date_from_sql,
        date_to_sql=date_to_sql,
        date_from_display=_short(date_from_sql),
        date_to_display=_short(date_to_sql),
        spots=spots,
    )
