"""
Parse Direct Donor TV (WorldLink/AATV) traffic instruction ODS files.

One ODS/XLS file per advertiser/month. Contains ISCI codes with ALLOCATION
weights and a flight date range per ROW.  Format: "04/27/26 thru 05/31/2026".

A file may carry several flights (e.g. week 1 = one ISCI at 100%, weeks 2-4 =
three ISCIs at 33/33/34) and the same ISCI may appear once per flight, so the
flight dates belong on the spot, never on the instruction. ``periods`` groups
the spots by flight window in file order; the instruction-level dates are the
overall min/max, for contract search only.
"""

import io
import re
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class DirectDonorSpot:
    isci: str
    title: str
    duration_sec: int  # 60 or 120
    rotation_pct: float  # 0–100, derived from ALLOCATION (0.33 → 33.0)
    date_from_sql: Optional[str] = None  # this row's own VALID FLIGHT DATES
    date_to_sql: Optional[str] = None
    date_from_display: str = ""
    date_to_display: str = ""


@dataclass
class DirectDonorPeriod:
    """One flight window: every spot whose row carries the same VALID FLIGHT DATES."""

    date_from_sql: Optional[str]
    date_to_sql: Optional[str]
    date_from_display: str
    date_to_display: str
    spots: List[DirectDonorSpot] = field(default_factory=list)


_SEARCH_OVERRIDES: dict = {
    "americanheart": "American",
    "covenant": "Covenant",
    "shriners": "Shriners",
    "savethechildren": "Save",
    "feedingamerica": "Feeding",
    "save": "Save",
    "feeding": "Feeding",
    "stjude": "St. Jude",
    "wounded": "Wounded",
}


def _search_suggestion(advertiser: str) -> str:
    key = advertiser.lower().replace(" ", "")
    for prefix, suggestion in _SEARCH_OVERRIDES.items():
        if key.startswith(prefix):
            return suggestion
    return advertiser


def _parse_flight_date(s: str) -> Optional[str]:
    """Parse '04/27/26' or '05/31/2026' → '2026-04-27'."""
    m = re.match(r"^(\d{1,2})/(\d{1,2})/(\d{2,4})$", s.strip())
    if not m:
        return None
    month, day, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if year < 100:
        year += 2000
    return f"{year:04d}-{month:02d}-{day:02d}"


_FLIGHT_RE = re.compile(
    r"(\d{1,2}/\d{1,2}/\d{2,4})\s*(?:thru|through|-+)\s*(\d{1,2}/\d{1,2}/\d{2,4})",
    re.IGNORECASE,
)


def _short(d: str) -> str:
    p = d.split("/")
    return f"{int(p[0])}/{int(p[1])}" if len(p) >= 2 else d


def _parse_flight_range(s: str):
    """'8/31/2026 thru 9/06/2026' → (sql_from, sql_to, disp_from, disp_to); Nones if absent."""
    m = _FLIGHT_RE.search(s or "")
    if not m:
        return None, None, "", ""
    return (
        _parse_flight_date(m.group(1)),
        _parse_flight_date(m.group(2)),
        _short(m.group(1)),
        _short(m.group(2)),
    )


@dataclass
class DirectDonorTrafficInstruction:
    advertiser: str
    search_suggestion: str
    date_from_sql: Optional[str]
    date_to_sql: Optional[str]
    date_from_display: str
    date_to_display: str
    spots: List[DirectDonorSpot] = field(default_factory=list)
    periods: List[DirectDonorPeriod] = field(default_factory=list)


def _group_periods(spots: List[DirectDonorSpot]) -> List[DirectDonorPeriod]:
    """Group spots by their own flight window, preserving file order."""
    periods: List[DirectDonorPeriod] = []
    by_key: dict = {}
    for sp in spots:
        key = (sp.date_from_sql, sp.date_to_sql)
        per = by_key.get(key)
        if per is None:
            per = DirectDonorPeriod(
                date_from_sql=sp.date_from_sql,
                date_to_sql=sp.date_to_sql,
                date_from_display=sp.date_from_display,
                date_to_display=sp.date_to_display,
            )
            by_key[key] = per
            periods.append(per)
        per.spots.append(sp)
    return periods


def parse_directdonor_traffic_ods(
    file_bytes: bytes, filename: str = ""
) -> DirectDonorTrafficInstruction:
    """Parse a Direct Donor TV traffic instruction ODS or XLSX file."""
    import pandas as pd

    ext = filename.lower().rsplit(".", 1)[-1] if filename else "ods"
    engine = "xlrd" if ext == "xls" else ("openpyxl" if ext in ("xlsx", "xlsm") else "odf")
    df = pd.read_excel(io.BytesIO(file_bytes), engine=engine, header=None, dtype=str)

    # Extract advertiser from "Advertiser: ..." row
    advertiser = ""
    for _, row in df.iterrows():
        for cell in row:
            if isinstance(cell, str) and cell.strip().lower().startswith("advertiser:"):
                advertiser = cell.strip()[len("advertiser:") :].strip()
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
            date_from_sql=None,
            date_to_sql=None,
            date_from_display="",
            date_to_display="",
        )

    isci_col = col_map.get("ISCI", -1)
    title_col = col_map.get("TITLE", 0)
    alloc_col = col_map.get("ALLOCATION", -1)
    length_col = col_map.get("LENGTH", -1)
    flight_col = col_map.get("VALID FLIGHT DATES", -1)

    spots: List[DirectDonorSpot] = []
    seen: set = set()

    for _, row in df.iloc[header_idx + 1 :].iterrows():
        if isci_col < 0 or isci_col >= len(row):
            continue
        isci = str(row.iloc[isci_col]).strip()
        if not isci or isci in ("nan", "None"):
            continue
        # ISCI codes are alphanumeric, ≥6 chars; stop at notes rows
        if not re.match(r"^[A-Za-z0-9]{6,}$", isci):
            continue

        # Flight dates are PER ROW — the same ISCI legitimately repeats once per flight
        # (100% in week 1, then 33% in weeks 2-4), so dedupe on (ISCI, window).
        flight_str = (
            str(row.iloc[flight_col]).strip() if flight_col >= 0 and flight_col < len(row) else ""
        )
        row_from, row_to, row_from_disp, row_to_disp = _parse_flight_range(flight_str)
        key = (isci, row_from, row_to)
        if key in seen:
            continue
        seen.add(key)

        title = (
            str(row.iloc[title_col]).strip() if title_col >= 0 and title_col < len(row) else isci
        )
        if title in ("nan", "None", ""):
            title = isci

        # Duration from LENGTH column: ":120" → 120, ":60" → 60
        dur_raw = (
            str(row.iloc[length_col]).strip()
            if length_col >= 0 and length_col < len(row)
            else ":120"
        )
        dm = re.search(r"\d+", dur_raw)
        dur_sec = int(dm.group()) if dm else 120

        # ALLOCATION is a decimal weight (0.33 → 33.0%)
        alloc_raw = (
            str(row.iloc[alloc_col]).strip() if alloc_col >= 0 and alloc_col < len(row) else "0"
        )
        am = re.search(r"[\d.]+", alloc_raw)
        alloc = float(am.group()) if am else 0.0
        rotation_pct = round(alloc * 100, 1) if alloc <= 1.0 else round(alloc, 1)

        spots.append(
            DirectDonorSpot(
                isci=isci,
                title=title,
                duration_sec=dur_sec,
                rotation_pct=rotation_pct,
                date_from_sql=row_from,
                date_to_sql=row_to,
                date_from_display=row_from_disp,
                date_to_display=row_to_disp,
            )
        )

    periods = _group_periods(spots)

    # Instruction-level window = the union of every row's flight (contract search only).
    dated = [p for p in periods if p.date_from_sql and p.date_to_sql]
    first = min(dated, key=lambda p: p.date_from_sql) if dated else None
    last = max(dated, key=lambda p: p.date_to_sql) if dated else None

    return DirectDonorTrafficInstruction(
        advertiser=advertiser,
        search_suggestion=_search_suggestion(advertiser),
        date_from_sql=first.date_from_sql if first else None,
        date_to_sql=last.date_to_sql if last else None,
        date_from_display=first.date_from_display if first else "",
        date_to_display=last.date_to_display if last else "",
        spots=spots,
        periods=periods,
    )
