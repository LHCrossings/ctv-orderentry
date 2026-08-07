"""
DART (Dallas Area Rapid Transit) Order Parser.

Parses Excel (.xlsx) insertion orders from DART for The Asian Channel (KLEG 44.3 Dallas).

Expected xlsx structure:
  Row 2:  Client name (col D)
  Row 4:  Station (col D)
  Row 5:  Contact (col D)
  Row 9:  Order date (col D)
  Row 13: "FLIGHT SCHEDULE (:15 seconds) ..." descriptor (col B)
  Row 14: Headers — Programming | Schedule | Length | Rate | [week dates...]
                    | Total Units | Value | Total Cost
  Row 15+: Data rows (paid lines, then bonus lines starting with "ROS")
  Stop at: PAID / BONUSES / Total / Added Value / Retail Value summary rows

**Columns are located by their HEADER LABEL, never by a fixed index, and every week
date is read from its own header cell.** DART inserted a "Length" column between
Schedule and Rate (Aug/Sept 2026 order): the old parser read the rate from a fixed
col D, got ":15s", swallowed the `Decimal` failure in a bare `except` and entered a
$3,000 order with **$0 on every line**. The same order's week columns are 8/17, 9/14,
9/21 — not consecutive — but the old parser read only the FIRST date and synthesized
`first + 7i`, so the flight entered as 8/17-9/6 instead of 8/17-9/27.

Both bugs were silent. The parser now reconciles each line against the sheet's own
Total Units and Total Cost cells, and the paid total against the PAID summary row,
raising rather than entering something that does not foot.
"""

import re
from dataclasses import dataclass
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
    # Per-line spot length from the "Length" column. DART mixes lengths on one
    # order (paid :15s, bonus :30s), so an order-level duration is not enough.
    # None when the sheet has no Length column — caller falls back to the order's.
    spot_length: int | None = None

    @property
    def total_spots(self) -> int:
        return sum(self.spot_counts)

    # Aliases used by the generic parser_bridge normalizer
    @property
    def program(self) -> str:
        return self.programming

    @property
    def daypart(self) -> str:
        return self.schedule

    @property
    def weekly_spots(self) -> List[int]:
        return self.spot_counts


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
    def markets(self) -> List[str]:
        return ["DAL"]

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

# Header label (normalized) → field name. Anything unlisted is ignored, so DART can
# add columns without breaking the parse; a RENAMED column fails loudly in
# _require_columns instead of silently reading the wrong one.
_COLUMN_LABELS = {
    "programming": "programming",
    "schedule": "schedule",
    "length": "length",
    "rate": "rate",
    "total units": "total_units",
    "value": "value",
    "total cost": "total_cost",
}
_REQUIRED_COLUMNS = ("programming", "schedule", "rate")


def _norm_header(value) -> str:
    """'Rate  ' → 'rate';  'Total  Units' → 'total units'."""
    return re.sub(r'\s+', ' ', str(value or "")).strip().lower()


def _as_date(value):
    """A date/datetime cell as a `date`, else None."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return None


def _money(value) -> Decimal | None:
    """A money/number cell as Decimal, else None. Strips $ and thousands commas.

    Returns None (not 0) when the cell is not a number — a rate that cannot be read
    must be reported, never quietly turned into a free spot.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float, Decimal)):
        return Decimal(str(value))
    text = re.sub(r'[$,\s]', '', str(value))
    if not text:
        return None
    try:
        return Decimal(text)
    except Exception:
        return None


def _spot_length(value) -> int | None:
    """':15s' / ':30' / 30 → 15 / 30 / 30. None when unreadable."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return int(value)
    m = re.search(r'(\d+)', str(value or ""))
    return int(m.group(1)) if m else None


def _find_header_row(rows) -> tuple[int, dict[str, int], list[int], list[date]]:
    """Locate the grid header and map it.

    Returns (row_index, {field: col_index}, week_col_indexes, week_start_dates).

    The header row is FOUND rather than assumed (it has been row 14 so far, but a
    banner or an extra intro line moves it — see the SCWA lesson in tasks/lessons.md).
    """
    for idx, row in enumerate(rows):
        labels = {_norm_header(v): i for i, v in enumerate(row) if v is not None}
        if "programming" not in labels or "schedule" not in labels:
            continue
        cols = {
            field: labels[label]
            for label, field in _COLUMN_LABELS.items() if label in labels
        }
        # Week columns = the date-valued header cells (each week's own start date;
        # they are NOT necessarily consecutive — this order skips 8/24 to 9/14).
        weeks = [(i, d) for i, v in enumerate(row) if (d := _as_date(v)) is not None]
        return idx, cols, [i for i, _ in weeks], [d for _, d in weeks]
    raise ValueError(
        "Could not find the DART grid header row (needs 'Programming' + 'Schedule')."
    )


def _require_columns(cols: dict[str, int], path: str) -> None:
    missing = [c for c in _REQUIRED_COLUMNS if c not in cols]
    if missing:
        raise ValueError(
            f"DART sheet {path} is missing required column(s): {', '.join(missing)}. "
            f"Found: {sorted(cols)}. A renamed column must be added to _COLUMN_LABELS "
            f"— never fall back to a positional guess."
        )


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

    # ── Grid header: find it, map columns by label, read every week date ──
    header_idx, cols, week_cols, week_start_dates = _find_header_row(rows)
    _require_columns(cols, str(path))
    if not week_start_dates:
        wb.close()
        raise ValueError(
            f"Could not find any week start date in the header row of {path}. "
            "Expected a date value per week column."
        )

    def cell(row, field):
        idx = cols.get(field)
        return row[idx] if idx is not None and idx < len(row) else None

    # ── Data rows ─────────────────────────────────────────────────────────
    lines: List[DartLine] = []
    problems: List[str] = []
    summary_paid_cost: Decimal | None = None

    for row in rows[header_idx + 1:]:
        programming_raw = cell(row, "programming")
        if programming_raw is None:
            continue

        prog_str = str(programming_raw).strip()
        if not prog_str:
            continue
        if prog_str.upper().rstrip() in _STOP_LABELS:
            # The PAID summary row carries the order's own paid total — keep it as
            # the reconciliation target, then stop reading data rows.
            if prog_str.upper().rstrip() == "PAID":
                summary_paid_cost = _money(cell(row, "total_cost"))
            break

        schedule_raw = cell(row, "schedule")
        if schedule_raw is None:
            continue
        schedule = str(schedule_raw).strip()
        is_bonus = "ROS" in schedule.upper()

        rate = _money(cell(row, "rate"))
        if rate is None and not is_bonus:
            problems.append(
                f"{prog_str!r}: rate cell is not a number "
                f"({cell(row, 'rate')!r}) — refusing to enter it as $0"
            )
        # Bonus lines carry $0 regardless of what the xlsx shows.
        rate = Decimal("0") if is_bonus else (rate or Decimal("0"))

        spot_counts = [
            (int(v) if isinstance(v := row[c] if c < len(row) else 0, (int, float))
             and not isinstance(v, bool) else 0)
            for c in week_cols
        ]

        # Reconcile this row against the sheet's own arithmetic.
        total_spots = sum(spot_counts)
        stated_units = cell(row, "total_units")
        if isinstance(stated_units, (int, float)) and int(stated_units) != total_spots:
            problems.append(
                f"{prog_str!r}: week columns sum to {total_spots} spots but the "
                f"sheet's Total Units says {int(stated_units)}"
            )
        stated_cost = _money(cell(row, "total_cost"))
        if not is_bonus and stated_cost is not None:
            if rate * total_spots != stated_cost:
                problems.append(
                    f"{prog_str!r}: rate {rate} x {total_spots} spots = "
                    f"{rate * total_spots} but the sheet's Total Cost says {stated_cost}"
                )

        lines.append(DartLine(
            programming=prog_str,
            schedule=schedule,
            rate=rate,
            spot_counts=spot_counts,
            is_bonus=is_bonus,
            spot_length=_spot_length(cell(row, "length")),
        ))

    wb.close()

    if not lines:
        raise ValueError(f"No data rows found under the grid header in {path}.")

    # Grand total vs the order's own PAID row — the guard that would have caught
    # the rate column shifting from D to E instead of entering $0 on every line.
    paid_total = sum(
        (ln.rate * ln.total_spots for ln in lines if not ln.is_bonus), Decimal("0")
    )
    if summary_paid_cost is not None and paid_total != summary_paid_cost:
        problems.append(
            f"paid lines total {paid_total} but the sheet's PAID Total Cost "
            f"says {summary_paid_cost}"
        )
    if problems:
        raise ValueError(
            f"DART sheet {path} does not reconcile — refusing to enter it:\n  - "
            + "\n  - ".join(problems)
        )

    return DartOrder(
        client=client,
        station=station,
        contact=contact,
        order_date=order_date,
        duration_seconds=duration_seconds,
        week_start_dates=week_start_dates,
        lines=lines,
    )
