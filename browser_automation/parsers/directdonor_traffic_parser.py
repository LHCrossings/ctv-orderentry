"""
Parse Direct Donor TV (WorldLink/AATV) traffic instruction ODS files.

One ODS file per advertiser/month. Contains ISCI codes with ALLOCATION
weights and a flight date range.  Format: "04/27/26 thru 05/31/2026".
"""
import io
import re
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class DirectDonorSpot:
    isci: str
    title: str
    duration_sec: int    # 60 or 120
    rotation_pct: float  # 0–100, derived from ALLOCATION (0.33 → 33.0)


_SEARCH_OVERRIDES: dict = {
    "americanheart":   "American",
    "covenant":        "Covenant",
    "shriners":        "Shriners",
    "savethechildren": "Save",
    "feedingamerica":  "Feeding",
    "save":            "Save",
    "feeding":         "Feeding",
    "stjude":          "St. Jude",
    "wounded":         "Wounded",
}


def _search_suggestion(advertiser: str) -> str:
    key = advertiser.lower().replace(" ", "")
    for prefix, suggestion in _SEARCH_OVERRIDES.items():
        if key.startswith(prefix):
            return suggestion
    return advertiser


def _parse_flight_date(s: str) -> Optional[str]:
    """Parse '04/27/26' or '05/31/2026' → '2026-04-27'."""
    m = re.match(r'^(\d{1,2})/(\d{1,2})/(\d{2,4})$', s.strip())
    if not m:
        return None
    month, day, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if year < 100:
        year += 2000
    return f"{year:04d}-{month:02d}-{day:02d}"


@dataclass
class DirectDonorTrafficInstruction:
    advertiser: str
    search_suggestion: str
    date_from_sql: Optional[str]
    date_to_sql: Optional[str]
    date_from_display: str
    date_to_display: str
    spots: List[DirectDonorSpot] = field(default_factory=list)


def parse_directdonor_traffic_ods(file_bytes: bytes) -> DirectDonorTrafficInstruction:
    """Parse a Direct Donor TV traffic instruction ODS file."""
    import pandas as pd

    df = pd.read_excel(io.BytesIO(file_bytes), engine="odf", header=None, dtype=str)

    # Extract advertiser from "Advertiser: ..." row
    advertiser = ""
    for _, row in df.iterrows():
        for cell in row:
            if isinstance(cell, str) and cell.strip().lower().startswith("advertiser:"):
                advertiser = cell.strip()[len("advertiser:"):].strip()
                break
        if advertiser:
            break

    # Find header row containing "ISCI"
    header_idx = None
    col_map: dict = {}
    for idx, row in df.iterrows():
        vals = [str(v).strip().upper() for v in row]
        if "ISCI" in vals:
            header_idx = idx
            for ci, v in enumerate(vals):
                col_map[v] = ci
            break

    if header_idx is None:
        return DirectDonorTrafficInstruction(
            advertiser=advertiser,
            search_suggestion=_search_suggestion(advertiser),
            date_from_sql=None, date_to_sql=None,
            date_from_display="", date_to_display="",
        )

    isci_col   = col_map.get("ISCI", -1)
    title_col  = col_map.get("TITLE", 0)
    alloc_col  = col_map.get("ALLOCATION", -1)
    length_col = col_map.get("LENGTH", -1)
    flight_col = col_map.get("VALID FLIGHT DATES", -1)

    spots: List[DirectDonorSpot] = []
    date_from_sql = date_to_sql = None
    date_from_display = date_to_display = ""
    seen: set = set()

    for _, row in df.iloc[header_idx + 1:].iterrows():
        if isci_col < 0 or isci_col >= len(row):
            continue
        isci = str(row.iloc[isci_col]).strip()
        if not isci or isci in ("nan", "None") or isci in seen:
            continue
        # ISCI codes are alphanumeric, ≥6 chars; stop at notes rows
        if not re.match(r'^[A-Za-z0-9]{6,}$', isci):
            continue
        seen.add(isci)

        title = str(row.iloc[title_col]).strip() if title_col >= 0 and title_col < len(row) else isci
        if title in ("nan", "None", ""):
            title = isci

        # Duration from LENGTH column: ":120" → 120, ":60" → 60
        dur_raw = str(row.iloc[length_col]).strip() if length_col >= 0 and length_col < len(row) else ":120"
        dm = re.search(r'\d+', dur_raw)
        dur_sec = int(dm.group()) if dm else 120

        # ALLOCATION is a decimal weight (0.33 → 33.0%)
        alloc_raw = str(row.iloc[alloc_col]).strip() if alloc_col >= 0 and alloc_col < len(row) else "0"
        am = re.search(r'[\d.]+', alloc_raw)
        alloc = float(am.group()) if am else 0.0
        rotation_pct = round(alloc * 100, 1) if alloc <= 1.0 else round(alloc, 1)

        # Flight dates — parse from first data row that has them
        if not date_from_sql and flight_col >= 0 and flight_col < len(row):
            flight_str = str(row.iloc[flight_col]).strip()
            m = re.search(
                r'(\d{1,2}/\d{1,2}/\d{2,4})\s*(?:thru|through|-+)\s*(\d{1,2}/\d{1,2}/\d{2,4})',
                flight_str, re.IGNORECASE,
            )
            if m:
                date_from_sql = _parse_flight_date(m.group(1))
                date_to_sql   = _parse_flight_date(m.group(2))

                def _short(d: str) -> str:
                    p = d.split("/")
                    return f"{int(p[0])}/{int(p[1])}" if len(p) >= 2 else d

                date_from_display = _short(m.group(1))
                date_to_display   = _short(m.group(2))

        spots.append(DirectDonorSpot(
            isci=isci, title=title, duration_sec=dur_sec, rotation_pct=rotation_pct,
        ))

    return DirectDonorTrafficInstruction(
        advertiser=advertiser,
        search_suggestion=_search_suggestion(advertiser),
        date_from_sql=date_from_sql,
        date_to_sql=date_to_sql,
        date_from_display=date_from_display,
        date_to_display=date_to_display,
        spots=spots,
    )
