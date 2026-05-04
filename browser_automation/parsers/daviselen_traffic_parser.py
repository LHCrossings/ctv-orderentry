"""
Parse Davis Elen "Spot Commercial Instructions" PDFs.

One PDF per estimate/language. Returns the estimate number, product info,
and all ISCI codes with their rotation percentages.
"""
import io
import re
from dataclasses import dataclass, field
from typing import List, Optional

import pdfplumber

_MONTHS = {
    "JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
    "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12,
}


def _date_to_sql(date_str: str) -> Optional[str]:
    """Convert 'MAY05/26' → '2026-05-05' for SQL WHERE clauses."""
    m = re.match(r'^([A-Z]{3})(\d{2})/(\d{2})$', date_str)
    if not m:
        return None
    month = _MONTHS.get(m.group(1))
    if not month:
        return None
    day  = int(m.group(2))
    year = 2000 + int(m.group(3))
    return f"{year:04d}-{month:02d}-{day:02d}"


@dataclass
class DaviselenTrafficSpot:
    isci: str
    title: str
    rotation_pct: float


@dataclass
class DaviselenTrafficInstruction:
    estimate: str
    product_code: str
    product_name: str
    duration_sec: int
    start_date: str
    end_date: str
    date_from_sql: Optional[str] = None
    date_to_sql: Optional[str] = None
    spots: List[DaviselenTrafficSpot] = field(default_factory=list)


def parse_daviselen_traffic_pdf(pdf_bytes: bytes) -> DaviselenTrafficInstruction:
    """Parse a single Davis Elen traffic instructions PDF."""
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        text = "\n".join(page.extract_text() or "" for page in pdf.pages)

    estimate_m = re.search(r'Estimate\s+(\d+)', text)
    estimate = estimate_m.group(1) if estimate_m else ""

    result = DaviselenTrafficInstruction(
        estimate=estimate,
        product_code="",
        product_name="",
        duration_sec=30,
        start_date="",
        end_date="",
    )

    # Table data sits between two '---' separator lines.
    # Data rows: PROD_CODE PRODUCT_NAME :30 MAY05/26 MAY31/26 ISCI_CODE TITLE ROTATION% ...
    # Continuation rows (multiple spots): just ISCI_CODE TITLE ROTATION% ... (indented)
    lines = text.split("\n")
    sep_count = 0
    in_table = False
    last_prod_code = ""
    last_prod_name = ""
    last_duration = 30
    last_start = ""
    last_end = ""

    for line in lines:
        stripped = line.strip()
        if re.match(r'^-{10,}', stripped):
            sep_count += 1
            in_table = sep_count == 2  # enter after second separator
            continue

        if not in_table or not stripped:
            continue

        # Skip underscore-separated station/rotation lines (PDF formatting artefacts)
        if re.search(r'_[A-Za-z]_', stripped):
            continue

        # Full data row: starts with a non-space prod code, has a :duration and dates
        full_m = re.match(
            r'^(\S+)\s+'            # prod_code
            r'(.+?)\s+'             # product_name (non-greedy)
            r':(\d+)\s+'            # duration
            r'([A-Z]+\d+/\d+)\s+'  # start_date
            r'([A-Z]+\d+/\d+)\s+'  # end_date
            r'(\w+)\s+'             # isci_code
            r'(.+?)\s+'             # title (non-greedy)
            r'([\d.]+)%',           # rotation_pct
            stripped,
        )
        if full_m:
            last_prod_code = full_m.group(1)
            last_prod_name = full_m.group(2)
            last_duration  = int(full_m.group(3))
            last_start     = full_m.group(4)
            last_end       = full_m.group(5)
            isci           = full_m.group(6)
            title          = full_m.group(7).strip()
            rotation_pct   = float(full_m.group(8))

            if not result.product_code:
                result.product_code  = last_prod_code
                result.product_name  = last_prod_name
                result.duration_sec  = last_duration
                result.start_date    = last_start
                result.end_date      = last_end
                result.date_from_sql = _date_to_sql(last_start)
                result.date_to_sql   = _date_to_sql(last_end)

            result.spots.append(DaviselenTrafficSpot(
                isci=isci, title=title, rotation_pct=rotation_pct,
            ))
            continue

        # Continuation row: indented, just ISCI_CODE TITLE ROTATION% (no dates)
        cont_m = re.match(r'^(\w{6,})\s+(.+?)\s+([\d.]+)%', stripped)
        if cont_m and last_prod_code:
            isci         = cont_m.group(1)
            title        = cont_m.group(2).strip()
            rotation_pct = float(cont_m.group(3))
            result.spots.append(DaviselenTrafficSpot(
                isci=isci, title=title, rotation_pct=rotation_pct,
            ))

    return result
