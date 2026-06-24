"""Fight the Bite media partnership parser — Excel (.xlsm/.xlsx) and PDF."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

# ─────────────────────────────────────────────────────────────────────────────
# Dataclasses
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class FTBLine:
    language: str               # "Hmong", "Chinese", "Vietnamese", "Hmong Chinese Vietnamese"
    days: str                   # Etere day pattern: "Sa-Su", "M-Su"
    time_range: str             # "HH:MM-HH:MM"
    rate: float                 # per-spot rate (0.0 for bonus)
    weekly_spots: list[int]     # spot count per week column
    week_start_dates: list[str] # "Jun 8" etc., parallel to weekly_spots
    is_bonus: bool = False


@dataclass
class FTBOrder:
    title: str
    paid_lines: list[FTBLine] = field(default_factory=list)
    bonus_lines: list[FTBLine] = field(default_factory=list)
    flight_end: str = ""       # Sunday of the last week, e.g. "Oct 11"
    year: int = 2026           # calendar year of the flight
    duration: int = 30         # spot duration in seconds
    total_cost: float = 0.0
    source: str = "excel"      # "excel" or "pdf"


# ─────────────────────────────────────────────────────────────────────────────
# Language → Etere daypart mapping (user-confirmed for this campaign)
# ─────────────────────────────────────────────────────────────────────────────

_LANG_DAYPART: dict[str, tuple[str, str]] = {
    "hmong":      ("Sa-Su", "18:00-20:00"),  # Sat-Sun 6p-8p
    "chinese":    ("M-Su",  "06:00-23:59"),  # broad 0600-2359 (user confirmed)
    "vietnamese": ("M-Su",  "10:00-13:00"),  # M-Sun 10a-1p
}

_MONTH_ABBR: dict[int, str] = {
    1: "Jan", 2: "Feb", 3: "Mar", 4: "Apr", 5: "May", 6: "Jun",
    7: "Jul", 8: "Aug", 9: "Sep", 10: "Oct", 11: "Nov", 12: "Dec",
}


def _fmt_date(dt: datetime) -> str:
    """Convert datetime to 'Jun 8' string for consolidate_weeks."""
    return f"{_MONTH_ABBR[dt.month]} {dt.day}"


def _week_end_date(week_start: datetime) -> str:
    """Return the Sunday of the broadcast week starting on week_start, as MM/DD/YYYY."""
    return (week_start + timedelta(days=6)).strftime("%m/%d/%Y")


def _daypart_for(language: str) -> tuple[str, str]:
    lang_lower = language.lower()
    for key, (days, time_range) in _LANG_DAYPART.items():
        if key in lang_lower:
            return days, time_range
    return "M-Su", "06:00-23:59"


# ─────────────────────────────────────────────────────────────────────────────
# Excel parser
# ─────────────────────────────────────────────────────────────────────────────

def _parse_excel(path: str) -> Optional[FTBOrder]:
    import openpyxl
    wb = openpyxl.load_workbook(path, data_only=True)
    ws = wb.active

    rows = list(ws.iter_rows(values_only=True))

    # Locate the header row that contains "Insertion" (heavy-up paid table header)
    hdr_idx = None
    for i, row in enumerate(rows):
        if any(str(c).strip().lower() == "insertion" for c in row if c is not None):
            hdr_idx = i
            break
    if hdr_idx is None:
        print("[FTB] Could not find 'Insertion' header row in Excel")
        return None

    hdr = rows[hdr_idx]

    # Week columns: positions that hold datetime values in the header row
    week_cols: list[tuple[int, datetime]] = [
        (col_idx, cell)
        for col_idx, cell in enumerate(hdr)
        if isinstance(cell, datetime)
    ]
    if not week_cols:
        print("[FTB] No week-date columns found in header")
        return None

    week_start_dates = [_fmt_date(dt) for _, dt in week_cols]
    flight_end = _week_end_date(week_cols[-1][1])
    year = week_cols[0][1].year

    paid_lines: list[FTBLine] = []
    bonus_lines: list[FTBLine] = []
    in_bonus = False

    for row in rows[hdr_idx + 1:]:
        row_str = " ".join(str(c).lower() for c in row if c is not None)

        if "bonus schedule" in row_str:
            in_bonus = True
            continue

        lang_raw = str(row[1]).strip() if row[1] is not None else ""
        time_raw = str(row[2]).strip() if row[2] is not None else ""
        rate_raw = row[3]

        if not lang_raw:
            continue
        if "total" in lang_raw.lower() or "total" in time_raw.lower():
            continue
        if "weekly cost" in row_str or "approved" in row_str or lang_raw.startswith("*"):
            continue

        try:
            rate = float(rate_raw) if rate_raw is not None else 0.0
        except (ValueError, TypeError):
            continue

        weekly_spots = []
        for col_idx, _ in week_cols:
            v = row[col_idx] if col_idx < len(row) else None
            try:
                weekly_spots.append(int(v) if v is not None else 0)
            except (ValueError, TypeError):
                weekly_spots.append(0)

        if not any(weekly_spots):
            continue

        if in_bonus:
            days, time_range = "M-Su", "06:00-23:59"
        else:
            days, time_range = _daypart_for(lang_raw)

        line = FTBLine(
            language=lang_raw,
            days=days,
            time_range=time_range,
            rate=rate,
            weekly_spots=weekly_spots,
            week_start_dates=week_start_dates,
            is_bonus=in_bonus,
        )
        (bonus_lines if in_bonus else paid_lines).append(line)

    total_cost = sum(sum(ln.weekly_spots) * ln.rate for ln in paid_lines)

    return FTBOrder(
        title="Fight The Bite",
        paid_lines=paid_lines,
        bonus_lines=bonus_lines,
        flight_end=flight_end,
        year=year,
        duration=30,
        total_cost=total_cost,
        source="excel",
    )


# ─────────────────────────────────────────────────────────────────────────────
# PDF parser
# ─────────────────────────────────────────────────────────────────────────────

_PDF_MONTH: dict[str, int] = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}


def _parse_pdf_date(token: str, year: int = 2026) -> Optional[datetime]:
    """Parse '8-Jun' or '15-Jun' into a datetime."""
    m = re.match(r"(\d{1,2})-([A-Za-z]{3})", token.strip())
    if not m:
        return None
    day = int(m.group(1))
    mon = _PDF_MONTH.get(m.group(2).lower())
    if mon is None:
        return None
    return datetime(year, mon, day)


def _parse_pdf(path: str) -> Optional[FTBOrder]:
    import pdfplumber

    with pdfplumber.open(path) as pdf:
        text = "\n".join(p.extract_text() or "" for p in pdf.pages)

    if "fight the bite" not in text.lower():
        return None

    lines_text = text.splitlines()
    year = 2026  # default; refined below from header dates

    # Locate the header line containing "Insertion" and week date tokens
    hdr_line_idx = None
    week_starts: list[datetime] = []
    for i, ln in enumerate(lines_text):
        if "Insertion" in ln:
            tokens = ln.split()
            dates = [t for t in tokens if re.match(r"\d{1,2}-[A-Za-z]{3}$", t)]
            if dates:
                hdr_line_idx = i
                for tok in dates:
                    dt = _parse_pdf_date(tok, year)
                    if dt:
                        week_starts.append(dt)
                break

    if hdr_line_idx is None or not week_starts:
        print("[FTB PDF] Could not locate week-date header line")
        return None

    year = week_starts[0].year
    week_start_dates = [_fmt_date(dt) for dt in week_starts]
    flight_end = _week_end_date(week_starts[-1])
    n_weeks = len(week_starts)

    _KNOWN = ["Hmong", "Chinese", "Vietnamese"]

    paid_lines: list[FTBLine] = []
    bonus_lines: list[FTBLine] = []
    in_bonus = False

    for raw_ln in lines_text[hdr_line_idx + 1:]:
        stripped = raw_ln.strip()
        if not stripped:
            continue
        if "bonus schedule" in stripped.lower():
            in_bonus = True
            continue
        if stripped.startswith("*") or "approved" in stripped.lower():
            continue

        if in_bonus:
            # The bonus data row has a dollar rate and integer spot counts
            rate_match = re.search(r"\$(\d+\.\d{2})", stripped)
            if not rate_match:
                continue
            rate = float(rate_match.group(1))
            remainder = stripped[rate_match.end():]
            nums = re.findall(r"\b(\d+)\b", remainder)
            weekly_spots = [int(n) for n in nums[:n_weeks]]
            while len(weekly_spots) < n_weeks:
                weekly_spots.append(0)
            if any(weekly_spots):
                bonus_lines.append(FTBLine(
                    language="Hmong Chinese Vietnamese",
                    days="M-Su",
                    time_range="06:00-23:59",
                    rate=rate,
                    weekly_spots=weekly_spots,
                    week_start_dates=week_start_dates,
                    is_bonus=True,
                ))
            continue

        # Paid rows start with a known language name
        matched_lang = next((lang for lang in _KNOWN if stripped.startswith(lang)), None)
        if not matched_lang:
            continue
        if "total" in stripped.lower():
            continue

        rate_match = re.search(r"\$(\d+\.\d{2})", stripped)
        if not rate_match:
            continue
        rate = float(rate_match.group(1))
        remainder = stripped[rate_match.end():]
        nums = re.findall(r"\b(\d+)\b", remainder)
        weekly_spots = [int(n) for n in nums[:n_weeks]]
        while len(weekly_spots) < n_weeks:
            weekly_spots.append(0)

        if not any(weekly_spots):
            continue

        days, time_range = _daypart_for(matched_lang)
        paid_lines.append(FTBLine(
            language=matched_lang,
            days=days,
            time_range=time_range,
            rate=rate,
            weekly_spots=weekly_spots,
            week_start_dates=week_start_dates,
            is_bonus=False,
        ))

    total_cost = sum(sum(ln.weekly_spots) * ln.rate for ln in paid_lines)

    return FTBOrder(
        title="Fight The Bite",
        paid_lines=paid_lines,
        bonus_lines=bonus_lines,
        flight_end=flight_end,
        year=year,
        duration=30,
        total_cost=total_cost,
        source="pdf",
    )


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def parse_fightthebite_file(path: str) -> Optional[FTBOrder]:
    """Parse a Fight the Bite order from Excel (.xlsm/.xlsx) or PDF."""
    ext = Path(path).suffix.lower()
    if ext in (".xlsm", ".xlsx", ".xls"):
        return _parse_excel(path)
    if ext == ".pdf":
        return _parse_pdf(path)
    # Unknown extension: try Excel first, then PDF
    return _parse_excel(path) or _parse_pdf(path)
