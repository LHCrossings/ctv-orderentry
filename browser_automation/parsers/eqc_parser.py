"""
Emerald Queen Casino (EQC) / TH Media order parser.

Reads the Crossings TV "Flight schedule" workbook TH Media sends for Emerald
Queen Casino. Single market (Seattle), single advertiser.

Layout (one sheet):
    C3  TV Station = Crossings TV     C4  Market: Seattle
    row 8:  B=PROGRAM  D=SCHEDULE  E=RATE  F..  = week-start dates (one per col)
    rows 9+ : one program per row
        - paid rows have a GROSS rate (> 0)
        - bonus rows have rate 0 (the three language "added value" blocks)
    footer rows: "Paid Units" / "Bonus Units" / "Total Units" / "GROSS" — stop there.

IMPORTANT business rules (confirmed with the buyer):
  * A season proposal stacks TWO tables (Oct–Mar, then Apr–Sep below the first
    footer) — every PROGRAM/SCHEDULE header in the sheet is read (2026-09-29: the
    2026-2027 sheet's second table was never entered).
  * Each date column is ONE week (Mon–Sun). EQC buys non-consecutive weeks
    (typically every other week), so weeks are NEVER consolidated — the
    automation emits one contract line per program per week-column.
  * Rates are GROSS (the rate column sums to the GROSS footer) → no gross-up.
  * Quarters are entered as separate contracts; this parser just exposes the
    week dates and the automation groups them by quarter.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Optional

# ─── Day normalization (shared idiom with tt_parser) ─────────────────────────

_DAY_NORM = [
    (re.compile(r"\bSun\b", re.IGNORECASE), "Su"),
    (re.compile(r"\bSat\b", re.IGNORECASE), "Sa"),
    (re.compile(r"\bMon\b", re.IGNORECASE), "M"),
    (re.compile(r"\bTue\b", re.IGNORECASE), "T"),
    (re.compile(r"\bWed\b", re.IGNORECASE), "W"),
    (re.compile(r"\bThu\b", re.IGNORECASE), "R"),
    (re.compile(r"\bFri\b", re.IGNORECASE), "F"),
]


def _normalize_days(s: str) -> str:
    """'M-Sun' → 'M-Su'; 'Sat & Sun' → 'Sa,Su'; 'M-F' → 'M-F'."""
    for pattern, repl in _DAY_NORM:
        s = pattern.sub(repl, s)
    s = re.sub(r"\s*&\s*", ",", s)  # "Sa & Su" → "Sa,Su"
    s = re.sub(r"\s+", "", s)  # drop residual spaces ("Sa - Su" never happens here)
    return s.strip(" ,")


def _split_schedule(schedule: str) -> tuple[str, str]:
    """
    Split a SCHEDULE cell into (days, time_raw).

      'M-Sun 8p-9p'            → ('M-Su',  '8p-9p')
      'Sat & Sun  8p-11p'      → ('Sa,Su', '8p-11p')
      'M-Sun 10a-11a& 12p-1p'  → ('M-Su',  '10a-11a& 12p-1p')
      'M-Sun 7p-12a'           → ('M-Su',  '7p-12a')

    The time portion starts at the first time token (a digit run followed by an
    optional am/pm marker and a hyphen). Everything before it is the day part.
    """
    schedule = (schedule or "").strip()
    m = re.search(r"\d{1,2}(?::\d{2})?\s*[ap]?\s*[-–]", schedule)
    if m:
        days_part = schedule[: m.start()].strip()
        time_part = schedule[m.start() :].strip()
    else:
        days_part, time_part = schedule, ""
    return _normalize_days(days_part), time_part


# ─── Data classes ────────────────────────────────────────────────────────────


@dataclass
class EQCLine:
    program: str  # e.g. "Shanghai Primetime News" or "Chinese" (bonus)
    schedule: str  # raw SCHEDULE cell, e.g. "M-Sun 8p-9p"
    rate: float  # per-spot GROSS rate (0.0 for bonus)
    week_spots: list[int] = field(default_factory=list)  # one count per week-column
    week_dates: list[date] = field(default_factory=list)  # Monday of each week-column
    is_bonus: bool = False

    @property
    def days(self) -> str:
        return _split_schedule(self.schedule)[0]

    @property
    def time_raw(self) -> str:
        return _split_schedule(self.schedule)[1]

    @property
    def total_spots(self) -> int:
        return sum(self.week_spots)

    @property
    def description(self) -> str:
        prog = self.program.strip()
        base = f"BNS {prog}" if self.is_bonus else prog
        sched = self.schedule.strip()
        desc = f"{base} {sched}".strip()
        return desc[:60]


@dataclass
class EQCOrder:
    market_code: str  # "SEA"
    lines: list[EQCLine] = field(default_factory=list)
    week_dates: list[date] = field(default_factory=list)
    order_date: Optional[date] = None
    client: str = "Emerald Queen Casino"  # advertiser (ANAGRAF customer 20)
    agency: str = "TH Media"  # buyer (agency 19)
    rates_are_net: bool = False  # rates are GROSS
    repairs: list[str] = field(default_factory=list)  # week-date years corrected from the sheet
    tables: int = 1  # program tables found in the sheet (a season proposal stacks two)

    @property
    def markets(self) -> list[str]:
        return [self.market_code]

    @property
    def paid_lines(self) -> list[EQCLine]:
        return [ln for ln in self.lines if not ln.is_bonus]

    @property
    def bonus_lines(self) -> list[EQCLine]:
        return [ln for ln in self.lines if ln.is_bonus]

    @property
    def flight_start(self) -> str:
        return self.week_dates[0].strftime("%m/%d/%Y") if self.week_dates else ""

    @property
    def flight_end(self) -> str:
        if not self.week_dates:
            return ""
        return (self.week_dates[-1] + timedelta(days=6)).strftime("%m/%d/%Y")


# ─── Week-date repair ────────────────────────────────────────────────────────
_TITLE_SPAN_RE = re.compile(r"(20\d\d)\s*[-–/]\s*(20\d\d)")


def _title_span(rows) -> Optional[tuple[int, int]]:
    """The sheet title's season span ("2026-2027   Flight schedule"), if it has one."""
    for row in rows[:8]:
        for v in row:
            m = _TITLE_SPAN_RE.search(str(v)) if isinstance(v, str) else None
            if m:
                a, b = int(m.group(1)), int(m.group(2))
                return (a, b) if a <= b else (b, a)
    return None


def repair_week_years(
    week_dates: list[date], span: Optional[tuple[int, int]] = None
) -> tuple[list[date], list[str]]:
    """The buyer's week cells carry the year they were typed with, and a season that
    crosses New Year keeps the old one: the 2026-2027 proposal listed 01/11/2026 after
    12/21/2026 (a Sunday, months in the past). Every week-start is a Monday and the
    columns run left to right in time, so a date that steps backwards is rolled forward
    a year until it is after its predecessor. The result must be a Monday and, when the
    title names a season span, fall inside it — otherwise the sheet is refused rather
    than guessed at. Returns (dates, repair notes)."""
    out: list[date] = []
    notes: list[str] = []
    for d in week_dates:
        fixed = d
        if out and fixed <= out[-1]:
            while fixed <= out[-1]:
                fixed = fixed.replace(year=fixed.year + 1)
            notes.append(
                f"week column {d.strftime('%m/%d/%Y')} ({d.strftime('%A')}) comes after "
                f"{out[-1].strftime('%m/%d/%Y')}; read as {fixed.strftime('%m/%d/%Y')}"
            )
        if fixed.weekday() != 0:
            raise ValueError(
                f"EQC week column {d.strftime('%m/%d/%Y')} is a {fixed.strftime('%A')}, not a Monday — "
                "check the sheet's week dates before entering."
            )
        if span and not (span[0] <= fixed.year <= span[1]):
            raise ValueError(
                f"EQC week column {fixed.strftime('%m/%d/%Y')} falls outside the sheet's season "
                f"{span[0]}-{span[1]} — check the sheet's week dates before entering."
            )
        out.append(fixed)
    return out, notes


# ─── Market detection ────────────────────────────────────────────────────────

_MARKET_KEYWORDS: list[tuple[str, str]] = [
    ("SEATTLE", "SEA"),
    ("SAN FRANCISCO", "SFO"),
    ("CENTRAL VALLEY", "CVC"),
    ("SACRAMENTO", "CVC"),
    ("LOS ANGELES", "LAX"),
    ("HOUSTON", "HOU"),
    ("WASHINGTON", "WDC"),
    ("NEW YORK", "NYC"),
]


def _detect_market(text: str) -> Optional[str]:
    upper = (text or "").upper()
    for keyword, code in _MARKET_KEYWORDS:
        if keyword in upper:
            return code
    return None


# ─── Footer / skip labels ────────────────────────────────────────────────────

_STOP_LABELS = frozenset(
    {
        "paid units",
        "bonus units",
        "total units (paid + bonus)",
        "total units",
        "gross",
        "gross ",
    }
)


# ─── Parser ──────────────────────────────────────────────────────────────────

_PAID_LABEL = "paid units"
_BONUS_LABEL = "bonus units"
_GROSS_LABEL = "gross amount"


def _is_header(row) -> bool:
    b = str(row[1] or "").strip().upper() if len(row) > 1 else ""
    d = str(row[3] or "").strip().upper() if len(row) > 3 else ""
    return b == "PROGRAM" and "SCHEDULE" in d


def _week_columns(row) -> tuple[list[int], list[date]]:
    cols, dates = [], []
    for col_idx in range(5, len(row)):  # E is rate (idx 4); dates start at F (idx 5)
        v = row[col_idx]
        if hasattr(v, "date"):  # datetime cell
            cols.append(col_idx)
            dates.append(v.date() if hasattr(v, "hour") else v)
    return cols, dates


def _cell_int(row, c) -> int:
    return int(row[c]) if (c < len(row) and isinstance(row[c], (int, float))) else 0


def _read_blocks(rows) -> list[dict]:
    """Every PROGRAM/SCHEDULE table in the sheet, in order. The 2026-2027 proposal has
    two: October-March, then April-September below the first footer. Each block =
    {header_idx, cols, dates, programs: [(program, schedule, rate, [spots per col])],
    footer: {label: [cell per col]}}."""
    blocks: list[dict] = []
    i = 0
    while i < len(rows):
        if not _is_header(rows[i]):
            i += 1
            continue
        cols, dates = _week_columns(rows[i])
        block = {"header_idx": i, "cols": cols, "dates": dates, "programs": [], "footer": {}}
        i += 1
        while i < len(rows) and not _is_header(rows[i]):
            row = rows[i]
            program = str(row[1] or "").strip() if len(row) > 1 else ""
            schedule = str(row[3] or "").strip() if len(row) > 3 else ""
            label_d = schedule.lower()
            if label_d in (_PAID_LABEL, _BONUS_LABEL, _GROSS_LABEL):
                block["footer"][label_d] = [_cell_int(row, c) for c in cols]
            elif program and schedule and label_d not in _STOP_LABELS:
                rate_cell = row[4] if len(row) > 4 else None
                rate = float(rate_cell) if isinstance(rate_cell, (int, float)) else 0.0
                block["programs"].append(
                    (program, schedule, rate, [_cell_int(row, c) for c in cols])
                )
            i += 1
        if cols and block["programs"]:
            blocks.append(block)
    return blocks


def _reconcile_block(n: int, block: dict) -> None:
    """The sheet's own footer is the oracle: per week column, paid and bonus spot sums
    must equal the Paid Units / Bonus Units rows, and rate x spots across the block must
    equal the Gross Amount row (printed once per pair of columns). Raise on any miss —
    a dropped row or a mis-read cell must refuse to enter, never enter short."""
    footer = block["footer"]
    paid = [0] * len(block["cols"])
    bonus = [0] * len(block["cols"])
    gross = 0.0
    for _prog, _sched, rate, spots in block["programs"]:
        for k, n_spots in enumerate(spots):
            if rate:
                paid[k] += n_spots
                gross += rate * n_spots
            else:
                bonus[k] += n_spots
    for label, ours in ((_PAID_LABEL, paid), (_BONUS_LABEL, bonus)):
        if label in footer and footer[label] != ours:
            raise ValueError(
                f"EQC table {n}: {label} row {footer[label]} does not match the program rows "
                f"{ours} — refusing to enter a sheet that does not foot."
            )
    if _GROSS_LABEL in footer:
        sheet_gross = float(sum(footer[_GROSS_LABEL]))
        if abs(sheet_gross - gross) > 0.01:
            raise ValueError(
                f"EQC table {n}: Gross Amount row totals ${sheet_gross:,.2f} but rate x spots is "
                f"${gross:,.2f} — refusing to enter a sheet that does not foot."
            )


def parse_eqc_xlsx(path: str) -> EQCOrder:
    """
    Parse a TH Media / Emerald Queen Casino Crossings TV flight-schedule workbook.

    Reads EVERY program table in the sheet (a season proposal stacks two: Oct-Mar and
    Apr-Sep), repairs week-column years the buyer left behind, reconciles each table
    against its own footer, and merges the same program across tables into one EQCLine
    whose week_spots line up with the order's combined week_dates.

    Raises:
        RuntimeError: if openpyxl is not installed
        ValueError: if no header/week-date row is found, a week date is not a Monday /
                    outside the title's season, or a table does not foot
    """
    try:
        import openpyxl
    except ImportError:
        raise RuntimeError("openpyxl is required: uv add openpyxl")

    wb = openpyxl.load_workbook(str(path), data_only=True)
    ws = wb.active
    rows = list(ws.iter_rows(values_only=True))  # row[i] is 0-based: col B == index 1
    wb.close()

    # ── Market (default SEA) ─────────────────────────────────────────────────
    market_code = "SEA"
    order_date: Optional[date] = None
    for row in rows[:8]:
        for v in row:
            if v is None:
                continue
            mc = _detect_market(str(v))
            if mc:
                market_code = mc
                break
        for v in row:
            if hasattr(v, "year") and hasattr(v, "month") and not hasattr(v, "hour"):
                order_date = v  # a plain date, rare in header

    blocks = _read_blocks(rows)
    if not blocks:
        raise ValueError(
            "Could not locate the EQC header row (B='PROGRAM', D='SCHEDULE') with week-date columns."
        )
    for n, block in enumerate(blocks, 1):
        _reconcile_block(n, block)

    # ── Week dates: all tables in sheet order, years repaired as one sequence ──
    raw_dates = [d for b in blocks for d in b["dates"]]
    week_dates, repairs = repair_week_years(raw_dates, _title_span(rows))

    # ── Merge programs across tables: one line per (program, schedule, rate) ───
    n_weeks = len(week_dates)
    merged: dict[tuple[str, str, float], EQCLine] = {}
    offset = 0
    for block in blocks:
        for program, schedule, rate, spots in block["programs"]:
            key = (program.strip().lower(), _normalize_days(schedule).lower(), rate)
            line = merged.get(key)
            if line is None:
                line = EQCLine(
                    program=program,
                    schedule=schedule,
                    rate=rate,
                    week_spots=[0] * n_weeks,
                    week_dates=list(week_dates),
                    is_bonus=(rate == 0.0),
                )
                merged[key] = line
            for k, n_spots in enumerate(spots):
                line.week_spots[offset + k] += n_spots
        offset += len(block["cols"])

    lines = list(merged.values())
    if not lines:
        raise ValueError("No program rows found in EQC workbook.")

    return EQCOrder(
        market_code=market_code,
        lines=lines,
        week_dates=week_dates,
        order_date=order_date,
        repairs=repairs,
        tables=len(blocks),
    )
