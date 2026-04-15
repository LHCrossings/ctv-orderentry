"""
DART (Dallas Area Rapid Transit) Order Parser.

Parses Excel (.xlsx) insertion orders from DART for The Asian Channel (KLEG 44.3 Dallas).

Expected xlsx structure:
  Row 2:  Client name (col D)
  Row 4:  Station (col D)
  Row 5:  Contact (col D)
  Row 9:  Order date (col D)
  Row 13: "FLIGHT SCHEDULE (:15 seconds) ..." descriptor (col B)
  Row 14: Headers — Programming | Schedule | Rate | [week dates...] | Total Units | Value | Total Cost
  Row 15+: Data rows (paid lines, then bonus lines starting with "ROS")
  Stop at: PAID / BONUSES / Total / Added Value / Retail Value summary rows
"""

import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import List


@dataclass(frozen=True)
class DartLine:
    """Single line item from a DART insertion order."""
    programming: str        # e.g. "Cantonese Talk"
    schedule: str           # e.g. "M-F 5:30p-6p" or "ROS  Bonus schedule"
    rate: Decimal           # per-spot rate (0 for bonus lines)
    spot_counts: List[int]  # spots per week, one entry per week column
    is_bonus: bool          # True when "ROS" appears in schedule

    @property
    def total_spots(self) -> int:
        return sum(self.spot_counts)


@dataclass
class DartOrder:
    """Complete DART insertion order parsed from xlsx."""
    client: str
    station: str
    contact: str
    order_date: date
    duration_seconds: int
    week_start_dates: List[date]
    lines: List[DartLine]

    @property
    def flight_start(self) -> date:
        return min(self.week_start_dates)

    @property
    def flight_end(self) -> date:
        return max(self.week_start_dates) + timedelta(days=6)

    @property
    def paid_lines(self) -> List[DartLine]:
        return [ln for ln in self.lines if not ln.is_bonus]

    @property
    def bonus_lines(self) -> List[DartLine]:
        return [ln for ln in self.lines if ln.is_bonus]

    @property
    def total_cost(self) -> Decimal:
        return sum(ln.rate * ln.total_spots for ln in self.paid_lines)


# ─────────────────────────────────────────────────────────────────────────────
# Schedule string helpers
# ─────────────────────────────────────────────────────────────────────────────

def _to_24h(time_val: str, ampm: str) -> str:
    """Convert "5:30" + "p" → "17:30", "10" + "a" → "10:00"."""
    if ":" in time_val:
        h_str, m_str = time_val.split(":", 1)
        h, m = int(h_str), int(m_str)
    else:
        h, m = int(time_val), 0

    ampm_lower = ampm.lower()
    if ampm_lower == "p" and h != 12:
        h += 12
    elif ampm_lower == "a" and h == 12:
        h = 0

    # Apply Etere floor/ceiling
    if h < 6:
        h, m = 6, 0
    if h > 23 or (h == 23 and m > 59):
        h, m = 23, 59

    return f"{h:02d}:{m:02d}"


def _parse_time_range(time_str: str) -> tuple[str, str]:
    """
    Parse a time-range string like "5:30p-6p" or "10a-11a" into (from_24h, to_24h).

    Rules:
    - If the end has an a/p suffix but the start doesn't, the start inherits it.
    - Falls back to ("06:00", "23:59") if parsing fails.
    """
    time_str = time_str.strip()
    # Each token: optional hours:minutes, optional a/p suffix
    tokens = re.findall(r'(\d+(?::\d+)?)([aApP]?)', time_str)
    # Filter out empty matches
    tokens = [(v, s) for v, s in tokens if v]
    if len(tokens) < 2:
        return ("06:00", "23:59")

    start_val, start_ampm = tokens[0]
    end_val, end_ampm = tokens[-1]

    # Inherit period: if end has a/p but start doesn't
    if end_ampm and not start_ampm:
        start_ampm = end_ampm

    return (_to_24h(start_val, start_ampm), _to_24h(end_val, end_ampm))


def parse_dart_schedule(schedule: str) -> tuple[str, str, str]:
    """
    Parse a DART schedule field into (etere_days, time_from, time_to).

    Examples:
      "M-F 5:30p-6p"        → ("M-F",  "17:30", "18:00")
      "M-Sun 6p-7p"         → ("M-Su", "18:00", "19:00")
      "M-Sun  10a-11a"      → ("M-Su", "10:00", "11:00")
      "ROS  Bonus schedule" → ("M-Su", "06:00", "23:59")
    """
    schedule = schedule.strip()

    if "ROS" in schedule.upper():
        return ("M-Su", "06:00", "23:59")

    # Split into day-token and time-range on first whitespace run
    parts = re.split(r'\s+', schedule, maxsplit=1)
    if len(parts) != 2:
        return ("M-Su", "06:00", "23:59")

    raw_days, time_str = parts

    # Normalise day abbreviations to Etere format
    days = (raw_days
            .replace("Sun", "Su")
            .replace("Sat", "Sa")
            .replace("Mon", "M")
            .replace("Tue", "Tu")
            .replace("Wed", "W")
            .replace("Thu", "Th")
            .replace("Fri", "F"))

    time_from, time_to = _parse_time_range(time_str)
    return (days, time_from, time_to)


# ─────────────────────────────────────────────────────────────────────────────
# Main parser
# ─────────────────────────────────────────────────────────────────────────────

_STOP_LABELS = frozenset({
    "PAID", "PAID ", "BONUSES", "TOTAL",
    "ADDED VALUE", "RETAIL VALUE",
})


def parse_dart_xlsx(path: str) -> DartOrder:
    """
    Parse a DART insertion order Excel file.

    Args:
        path: Absolute or relative path to the .xlsx file.

    Returns:
        DartOrder populated with all line items and metadata.

    Raises:
        ValueError: If the file is missing required structure (no week date found).
    """
    import openpyxl

    wb = openpyxl.load_workbook(str(path), data_only=True)
    ws = wb.active
    rows = list(ws.iter_rows(values_only=True))

    # ── Header fields ──────────────────────────────────────────────────────
    # Row 2 (index 1), col D (index 3): client name
    client = str(rows[1][3] or "").strip()

    # Row 4 (index 3), col D: station
    station = str(rows[3][3] or "").strip()

    # Row 5 (index 4), col D: contact
    contact = str(rows[4][3] or "").strip()

    # Row 9 (index 8), col D: order date
    order_date_raw = rows[8][3]
    if isinstance(order_date_raw, datetime):
        order_date = order_date_raw.date()
    elif isinstance(order_date_raw, date):
        order_date = order_date_raw
    else:
        order_date = date.today()

    # ── Duration from row 13 (index 12) ───────────────────────────────────
    row13_text = str(rows[12][1] or "")
    dur_match = re.search(r'\(:?(\d+)\s*seconds?\)', row13_text, re.IGNORECASE)
    duration_seconds = int(dur_match.group(1)) if dur_match else 15

    # ── Week columns from row 14 (index 13) ───────────────────────────────
    header_row = rows[13]

    # Find the first column containing a datetime (= first week start date)
    first_week_col = None
    first_week_date = None
    for col_idx, cell_val in enumerate(header_row):
        if isinstance(cell_val, (datetime, date)):
            first_week_col = col_idx
            first_week_date = (cell_val.date()
                               if isinstance(cell_val, datetime)
                               else cell_val)
            break

    if first_week_date is None:
        wb.close()
        raise ValueError(
            f"Could not find week start date in header row of {path}. "
            "Expected a datetime value in the week columns."
        )

    # Count how many week columns follow (formula strings like "=E14+7")
    num_weeks = 1
    for col_idx in range(first_week_col + 1, len(header_row)):
        cell_val = header_row[col_idx]
        if cell_val is None:
            break
        val_str = str(cell_val).strip().upper()
        if val_str in ("TOTAL UNITS", "VALUE", "TOTAL COST"):
            break
        if isinstance(cell_val, (datetime, date)) or str(cell_val).startswith("="):
            num_weeks += 1
        else:
            break

    week_start_dates = [
        first_week_date + timedelta(days=7 * i) for i in range(num_weeks)
    ]

    # ── Data rows from row 15 (index 14) onwards ──────────────────────────
    lines: List[DartLine] = []

    for row in rows[14:]:
        programming_raw = row[1]
        if programming_raw is None:
            continue

        prog_str = str(programming_raw).strip()
        if not prog_str or prog_str.upper().rstrip() in _STOP_LABELS:
            break

        schedule_raw = row[2]
        if schedule_raw is None:
            continue
        schedule = str(schedule_raw).strip()

        # Rate (col D = index 3)
        rate_raw = row[3]
        try:
            rate = Decimal(str(rate_raw or 0))
        except Exception:
            rate = Decimal("0")

        # Spot counts for each week column
        spot_counts: List[int] = []
        for i in range(num_weeks):
            col_idx = first_week_col + i
            val = row[col_idx] if col_idx < len(row) else 0
            try:
                spot_counts.append(int(val or 0))
            except (TypeError, ValueError):
                spot_counts.append(0)

        is_bonus = "ROS" in schedule.upper()

        # Bonus lines carry $0 rate regardless of what the xlsx shows
        if is_bonus:
            rate = Decimal("0")

        lines.append(DartLine(
            programming=prog_str,
            schedule=schedule,
            rate=rate,
            spot_counts=spot_counts,
            is_bonus=is_bonus,
        ))

    wb.close()

    return DartOrder(
        client=client,
        station=station,
        contact=contact,
        order_date=order_date,
        duration_seconds=duration_seconds,
        week_start_dates=week_start_dates,
        lines=lines,
    )
