"""
Parse Icon Media Direct (IMD/WorldLink) traffic instruction PDFs.

One PDF per ISCI/creative. Contains a single spot with flight dates and no
rotation — one creative = 100%.
"""
import io
import re
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class IMDSpot:
    isci: str
    title: str
    duration_sec: int
    rotation_pct: float = 100.0


# Maps first word of product name (lower) → better search term.
_SEARCH_OVERRIDES: dict = {
    "rue": "Rue",
    "gilt": "Gilt",
}


def _search_suggestion(product_name: str) -> str:
    words = product_name.split()
    if not words:
        return product_name
    key = words[0].lower()
    return _SEARCH_OVERRIDES.get(key, words[0])


def _parse_imd_date(s: str) -> Optional[str]:
    """Parse '03/30/26' or '3/30/2026' → '2026-03-30'."""
    m = re.match(r'^(\d{1,2})/(\d{1,2})/(\d{2,4})$', s.strip())
    if not m:
        return None
    month, day, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if year < 100:
        year += 2000
    return f"{year:04d}-{month:02d}-{day:02d}"


def _short(sql_date: str) -> str:
    p = sql_date.split('-')
    return f"{int(p[1])}/{int(p[2])}" if len(p) == 3 else sql_date


@dataclass
class IMDTrafficInstruction:
    advertiser: str           # product name, e.g. "RUE LA LA"
    client_code: str          # "RGG"
    product_code: str         # "RUE"
    search_suggestion: str
    date_from_sql: Optional[str]
    date_to_sql: Optional[str]
    date_from_display: str
    date_to_display: str
    spots: List[IMDSpot] = field(default_factory=list)


def parse_imd_traffic_pdf(pdf_bytes: bytes) -> IMDTrafficInstruction:
    """Parse an Icon Media Direct traffic instruction PDF."""
    import pdfplumber

    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        text = "\n".join(p.extract_text() or "" for p in pdf.pages)

    # Client: "Client Code: RGG Rue Gilt Groupe"
    client_code = ""
    m = re.search(r'Client Code:\s*([A-Z0-9]+)', text)
    if m:
        client_code = m.group(1).strip()

    # Product: "Product Code: RUE RUE LA LA"
    product_code = ""
    product_name = ""
    m = re.search(r'Product Code:\s*([A-Z0-9]+)\s+(.+)', text)
    if m:
        product_code = m.group(1).strip()
        product_name = m.group(2).strip()

    # ISCI: "ISCI/Tape Code: RBS5H"
    isci = ""
    m = re.search(r'ISCI/Tape Code:\s*([A-Z0-9]+)', text)
    if m:
        isci = m.group(1).strip()

    # Title: "Tape Title: RUE BOOK"
    title = ""
    m = re.search(r'Tape Title:\s*(.+)', text)
    if m:
        title = m.group(1).strip()

    # Duration: "Tape Length: :15"
    duration_sec = 30
    m = re.search(r'Tape Length:\s*:(\d+)', text)
    if m:
        duration_sec = int(m.group(1))

    # Flight Dates: "Flight Dates: 03/30/26 - 06/28/26"
    date_from_sql = date_to_sql = None
    date_from_display = date_to_display = ""
    m = re.search(
        r'Flight Dates:\s*(\d{1,2}/\d{1,2}/\d{2,4})\s*-\s*(\d{1,2}/\d{1,2}/\d{2,4})',
        text,
    )
    if m:
        date_from_sql = _parse_imd_date(m.group(1))
        date_to_sql   = _parse_imd_date(m.group(2))
        if date_from_sql:
            date_from_display = _short(date_from_sql)
        if date_to_sql:
            date_to_display = _short(date_to_sql)

    spots = []
    if isci:
        spots.append(IMDSpot(isci=isci, title=title or isci, duration_sec=duration_sec))

    return IMDTrafficInstruction(
        advertiser=product_name,
        client_code=client_code,
        product_code=product_code,
        search_suggestion=_search_suggestion(product_name),
        date_from_sql=date_from_sql,
        date_to_sql=date_to_sql,
        date_from_display=date_from_display,
        date_to_display=date_to_display,
        spots=spots,
    )
