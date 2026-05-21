"""
Parse Tatari "Traffic Instructions" PDFs (WorldLink/Tatari format).

One PDF per advertiser/week. Contains ISCI codes with rotation split %
and a flight date range. Times and dayparts are ignored.
"""
import datetime
import io
import re
from dataclasses import dataclass, field
from typing import List, Optional

import pdfplumber


@dataclass
class TatariSpot:
    isci: str
    title: str
    duration_sec: int    # 15 or 30
    rotation_pct: float


# Advertiser names in Tatari PDFs that don't match Etere contract descriptions verbatim.
# Maps the PDF advertiser string (case-insensitive prefix match) → better search term.
_SEARCH_OVERRIDES: dict = {
    "bettersleep":  "Better",
    "marsmen":      "Mars",
    "lectricebikes": "Lectric",
}


def _search_suggestion(advertiser: str) -> str:
    key = advertiser.lower().replace(" ", "")
    for prefix, suggestion in _SEARCH_OVERRIDES.items():
        if key.startswith(prefix):
            return suggestion
    return advertiser


@dataclass
class TatariTrafficInstruction:
    advertiser: str
    search_suggestion: str          # pre-filled search term for the contract finder
    date_from_sql: Optional[str]    # "2026-05-18"
    date_to_sql: Optional[str]      # "2026-05-24"
    date_from_display: str          # "5/18"
    date_to_display: str            # "5/24"
    spots: List[TatariSpot] = field(default_factory=list)


def _parse_date(part: str, year: int) -> Optional[str]:
    m = re.match(r'^(\d{1,2})/(\d{1,2})$', part.strip())
    if not m:
        return None
    month, day = int(m.group(1)), int(m.group(2))
    return f"{year:04d}-{month:02d}-{day:02d}"


def parse_tatari_traffic_pdf(pdf_bytes: bytes) -> TatariTrafficInstruction:
    """Parse a single Tatari traffic instructions PDF via pdfplumber table extraction."""
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        page = pdf.pages[0]
        text = page.extract_text() or ""
        tables = page.extract_tables()

    # Advertiser from "Station: Crossings TV  Advertiser: Sundays for Dogs"
    advertiser = ""
    for line in text.split("\n"):
        m = re.search(r'Advertiser:\s*(.+?)$', line)
        if m:
            advertiser = m.group(1).strip()
            break

    # Spots from the Ad Name / ISCI / Duration / Split table
    spots: List[TatariSpot] = []
    seen: set = set()
    for table in tables:
        if not table or not table[0]:
            continue
        header = [str(c or "").strip() for c in table[0]]
        if "ISCI" not in header or "Duration" not in header:
            continue
        isci_col  = header.index("ISCI")
        dur_col   = header.index("Duration")
        split_col = next((i for i, h in enumerate(header) if "Split" in h), -1)
        name_col  = next((i for i, h in enumerate(header) if "Ad Name" in h), -1)

        for row in table[1:]:
            if not row or len(row) <= max(isci_col, dur_col):
                continue
            isci = str(row[isci_col] or "").strip()
            if not isci or isci in seen:
                continue
            dur_str = str(row[dur_col] or "30s").strip().lower()
            dm = re.search(r'\d+', dur_str)
            dur_sec = int(dm.group()) if dm else 30
            pct_raw = str(row[split_col] or "0").strip() if split_col >= 0 else "0"
            pm = re.search(r'[\d.]+', pct_raw)
            pct = float(pm.group()) if pm else 0.0
            title = str(row[name_col] or isci).strip() if name_col >= 0 and row[name_col] else isci

            seen.add(isci)
            spots.append(TatariSpot(isci=isci, title=title, duration_sec=dur_sec, rotation_pct=pct))

    # Flight dates from the Description / Buy Type / Flight table — take the first date
    date_from_sql = date_to_sql = date_from_display = date_to_display = None
    current_year = datetime.date.today().year
    for table in tables:
        if not table or not table[0]:
            continue
        header = [str(c or "").strip() for c in table[0]]
        if "Flight" not in header:
            continue
        flight_col = header.index("Flight")
        for row in table[1:]:
            if not row or len(row) <= flight_col:
                continue
            flight_str = str(row[flight_col] or "").strip()
            m = re.search(r'(\d{1,2}/\d{1,2})\s*[-–]\s*(\d{1,2}/\d{1,2})', flight_str)
            if m:
                date_from_display = m.group(1)
                date_to_display   = m.group(2)
                date_from_sql     = _parse_date(date_from_display, current_year)
                date_to_sql       = _parse_date(date_to_display, current_year)
                break
        if date_from_sql:
            break

    return TatariTrafficInstruction(
        advertiser=advertiser,
        search_suggestion=_search_suggestion(advertiser),
        date_from_sql=date_from_sql,
        date_to_sql=date_to_sql,
        date_from_display=date_from_display or "",
        date_to_display=date_to_display or "",
        spots=spots,
    )
