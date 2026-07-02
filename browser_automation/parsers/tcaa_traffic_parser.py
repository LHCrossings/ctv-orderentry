"""
Parse TCAA (Toyota Dealer's Association) "CABLE Traffic Instructions" PDFs.

One PDF per campaign. The sheet carries one page per station (e.g. Asian
American TV Corp + Crossings TV) with an IDENTICAL creative table on each, so
spots are de-duped by ISCI. Every creative carries a rotation percentage and
"RUN IN ALL PROGRAMMING" — there is no language targeting.

Note: the estimate number printed on the sheet (e.g. "TCAA-9179") does NOT
match the Etere contract's estimate (e.g. "9712"), so this format is driven by
manual contract selection + drop-to-assign, not estimate-based auto-lookup. The
estimate here is captured for display only.
"""
import io
import re
from dataclasses import dataclass, field
from typing import List, Optional

import pdfplumber


def _date_to_sql(date_str: str) -> Optional[str]:
    """Convert 'MM/DD/YY' → 'YYYY-MM-DD' for SQL WHERE clauses."""
    m = re.match(r'^(\d{1,2})/(\d{1,2})/(\d{2})$', date_str)
    if not m:
        return None
    month = int(m.group(1))
    day   = int(m.group(2))
    year  = 2000 + int(m.group(3))
    return f"{year:04d}-{month:02d}-{day:02d}"


@dataclass
class TCAATrafficSpot:
    isci: str
    title: str
    rotation_pct: float
    duration_sec: int = 30


@dataclass
class TCAATrafficInstruction:
    estimate: str = ""
    product: str = ""
    station: str = ""
    start_date: str = ""
    end_date: str = ""
    date_from_sql: Optional[str] = None
    date_to_sql: Optional[str] = None
    crossings_page_found: bool = False
    warning: str = ""
    spots: List[TCAATrafficSpot] = field(default_factory=list)


# Creative row:
#   07/06/26 - 07/26/26 30% 2TCATV26214H WW July Drives Toyota Summer 4Runner :30 NEW...
_ROW_RE = re.compile(
    r'^(\d{1,2}/\d{1,2}/\d{2})\s*-\s*(\d{1,2}/\d{1,2}/\d{2})\s+'  # flight dates
    r'(\d+(?:\.\d+)?)%\s+'                                        # rotation %
    r'(\S+)\s+'                                                   # commercial code (ISCI)
    r'(.+?)\s+'                                                   # title (non-greedy)
    r':(\d+)\b'                                                   # duration
)

# A TCAA sheet carries one page PER STATION (e.g. "Asian American TV Corp." +
# "Crossings TV"). Only the Crossings TV page applies to us, and the two pages'
# creative tables are NOT always identical — so we must parse that page ALONE.
_CROSSINGS_RE = re.compile(r'crossings\s*tv', re.IGNORECASE)


def _parse_page(text: str, result: TCAATrafficInstruction) -> None:
    """Populate result's header + spots from a single page's text."""
    est_m = re.search(r'Estimate\s*#?\s*([A-Za-z0-9][A-Za-z0-9\-]*)', text)
    if est_m:
        result.estimate = est_m.group(1)

    prod_m = re.search(r'Product\s+(.+?)\s+Campaign\s+Dates', text)
    if prod_m:
        result.product = prod_m.group(1).strip()

    stn_m = re.search(r'Station\s+(.+?)\s+Traffic\s+Contact', text)
    if stn_m:
        result.station = stn_m.group(1).strip()

    camp_m = re.search(
        r'Campaign\s+Dates\s+(\d{1,2}/\d{1,2}/\d{2})\s*-\s*(\d{1,2}/\d{1,2}/\d{2})',
        text,
    )
    if camp_m:
        result.start_date    = camp_m.group(1)
        result.end_date      = camp_m.group(2)
        result.date_from_sql = _date_to_sql(camp_m.group(1))
        result.date_to_sql   = _date_to_sql(camp_m.group(2))

    seen: set = set()
    for line in text.split("\n"):
        m = _ROW_RE.match(line.strip())
        if not m:
            continue
        isci = m.group(4)
        if isci in seen:            # dedupe accidental repeats within the page
            continue
        seen.add(isci)
        result.spots.append(TCAATrafficSpot(
            isci=isci,
            title=m.group(5).strip(),
            rotation_pct=float(m.group(3)),
            duration_sec=int(m.group(6)),
        ))

    # Fall back to the first row's flight dates if no campaign-dates header.
    if not result.date_from_sql and result.spots:
        for line in text.split("\n"):
            m = _ROW_RE.match(line.strip())
            if m:
                result.start_date    = m.group(1)
                result.end_date      = m.group(2)
                result.date_from_sql = _date_to_sql(m.group(1))
                result.date_to_sql   = _date_to_sql(m.group(2))
                break


def parse_tcaa_traffic_pdf(pdf_bytes: bytes) -> TCAATrafficInstruction:
    """Parse a TCAA CABLE Traffic Instructions PDF, using ONLY the Crossings TV page.

    TCAA sheets have a page per station; the other stations (e.g. Asian American
    TV Corp.) are not ours and can carry a different creative table. We locate the
    Crossings TV page and parse it in isolation. If no such page is found we fall
    back to the whole document and set a warning, so nothing is assigned silently
    off the wrong station.
    """
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        page_texts = [page.extract_text() or "" for page in pdf.pages]

    result = TCAATrafficInstruction()

    crossings_pages = [t for t in page_texts if _CROSSINGS_RE.search(t)]
    if crossings_pages:
        result.crossings_page_found = True
        for t in crossings_pages:
            _parse_page(t, result)
    else:
        # No Crossings TV page — parse everything but flag it loudly for review.
        result.warning = (
            "No 'Crossings TV' page found in this TCAA sheet — parsed all pages. "
            "Verify these creatives are for Crossings TV before applying."
        )
        _parse_page("\n".join(page_texts), result)

    return result
