"""
Polaris Media Group Order Parser

Parses Excel (.xlsx) insertion orders from Polaris Media Group for Crossings TV.

Expected xlsx structure (Sheet: "Crossings TV"):
  Header block (anywhere near the top):
    Label rows: col C = label, col D = value
      AGENCY              → "Polaris"
      Advertiser          → advertiser name
      PREPARED BY:        → preparer name
      Flight Date:        → e.g. "4/16 THROUGH 4/20"
      TOTAL GROSS BUDGET: → numeric budget

  Column-header row: col C = "Media /MARKET", col D = "DAYS", col E = "Time", etc.
  Data rows begin immediately after the column-header row.

Data row columns (0-indexed):
  2: Market header cell — only present on first row of each market section,
     e.g. "CROSSINGS TV                          SAN FRANCISCO"
  3: Days  (e.g. "M-F", "Sat", "Sat- Sun ", "Sat& Sun")
  4: Time range  (e.g. "6a-7a", "7p-7:30p", "11:30p-12a")
  5: Programming / program name  (e.g. "Mandarin News", "Cantonese Talk")
  6: Gross Rate per :30s  (numeric) — "TOTAL " string signals end of data
  7: Units / total spot count  (integer)
  8: Gross cost  (for verification only)
"""

import re
import sys
from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import List

_project_root = Path(__file__).parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from browser_automation.day_utils import to_etere

# ─────────────────────────────────────────────────────────────────────────────
# MARKET DETECTION
# ─────────────────────────────────────────────────────────────────────────────

_MARKET_KEYWORDS = [
    ("SAN FRANCISCO", "SFO"),
    ("SACRAMENTO",    "CVC"),
    ("SEATTLE",       "SEA"),
    ("LOS ANGELES",   "LAX"),
    ("CHICAGO",       "CMP"),
    ("HOUSTON",       "HOU"),
    ("WASHINGTON",    "WDC"),
    ("NEW YORK",      "NYC"),
    ("DALLAS",        "DAL"),
]


def _detect_market(cell_text: str) -> str:
    """Extract market code from a market header cell like 'CROSSINGS TV   SAN FRANCISCO'."""
    upper = cell_text.upper()
    for keyword, code in _MARKET_KEYWORDS:
        if keyword in upper:
            return code
    print(f"[POLARIS PARSER] ⚠ Unrecognised market cell: {cell_text!r} — defaulting to SFO")
    return "SFO"


# ─────────────────────────────────────────────────────────────────────────────
# FLIGHT DATE PARSING
# ─────────────────────────────────────────────────────────────────────────────

def _parse_flight_dates(raw: str, year: int | None = None) -> tuple[str, str]:
    """
    Parse "4/16 THROUGH 4/20" or "9/29-10/5" → ("4/16/<year>", "4/20/<year>").

    Year is the sheet's own year cell when the caller found one; otherwise
    the current year, advanced to the following year if the computed end
    date has already passed.
    """
    parts = re.findall(r'\d{1,2}/\d{1,2}', raw)
    today = date.today()
    if year is not None and len(parts) >= 2:
        m1, d1 = parts[0].split('/')
        m2, d2 = parts[1].split('/')
        return (f"{int(m1)}/{int(d1)}/{year}", f"{int(m2)}/{int(d2)}/{year}")
    year = today.year

    if len(parts) < 2:
        return (f"{today.month}/{today.day}/{year}", f"{today.month}/{today.day}/{year}")

    def _to_date(md: str, yr: int) -> date:
        m, d = md.split('/')
        return date(yr, int(m), int(d))

    start = _to_date(parts[0], year)
    end   = _to_date(parts[1], year)

    if end < today:
        year += 1
        start = _to_date(parts[0], year)
        end   = _to_date(parts[1], year)

    return (f"{start.month}/{start.day}/{year}", f"{end.month}/{end.day}/{year}")



# ─────────────────────────────────────────────────────────────────────────────
# DATACLASSES
# ─────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class PolarisLine:
    """Single line item from a Polaris insertion order."""
    days: str           # Etere-format day pattern, e.g. "M-F", "Sa", "Sa-Su"
    time_str: str       # Raw time string as printed, e.g. "7p-7:30p"
    program: str        # Program name, e.g. "Mandarin News"
    rate: Decimal       # Gross rate per :30s spot
    total_spots: int    # Total spots for the flight (the "Unit" column)
    market: str         # Market code, e.g. "SFO"
    duration: int = 30  # Spot length in seconds (AAPI schedule: the "Unit" column, ":30")

    @property
    def is_bonus(self) -> bool:
        return self.rate == Decimal("0") and self.total_spots > 0

    def get_time_from_to(self) -> tuple[str, str]:
        """Return (time_from, time_to) in HH:MM 24-hour format."""
        from browser_automation.etere_client import EtereClient
        return EtereClient.parse_time_range(self.time_str)

    def get_description(self) -> str:
        """Build Etere line description: '[BNS ]Days Program'."""
        label = "BNS " if self.is_bonus else ""
        return f"{label}{self.days} {self.program}"


@dataclass
class PolarisOrder:
    """Complete Polaris insertion order parsed from xlsx."""
    advertiser: str
    prepared_by: str
    flight_start: str   # M/D/YYYY
    flight_end: str     # M/D/YYYY
    gross_budget: Decimal
    lines: List[PolarisLine]
    warnings: List[str] = field(default_factory=list)  # sheet oddities the operator must see

    @property
    def markets(self) -> List[str]:
        """Unique markets in order-of-appearance."""
        seen: dict[str, None] = {}
        for ln in self.lines:
            seen[ln.market] = None
        return list(seen)

    @property
    def total_spots(self) -> int:
        return sum(ln.total_spots for ln in self.lines)

    def lines_for_market(self, market: str) -> List[PolarisLine]:
        return [ln for ln in self.lines if ln.market == market]


# ─────────────────────────────────────────────────────────────────────────────
# MAIN PARSER
# ─────────────────────────────────────────────────────────────────────────────

def parse_polaris_pdf(path: str) -> PolarisOrder:
    """
    Parse a Polaris insertion order PDF file.

    The PDF layout mirrors the Excel format: a header block followed by a
    table with columns Media/MARKET | DAYS | Time | Programming |
    Gross Rate per :30s | Unit | GROSS.
    """
    import pdfplumber

    print(f"\n[POLARIS PARSER] Reading PDF: {path}")

    with pdfplumber.open(str(path)) as pdf:
        page = pdf.pages[0]
        raw_text = page.extract_text() or ""
        tables = page.extract_tables()

    # ── Header fields from raw text ────────────────────────────────────────
    advertiser   = ""
    prepared_by  = ""
    flight_raw   = ""
    gross_budget = Decimal("0")

    for line in raw_text.splitlines():
        stripped = line.strip()
        upper = stripped.upper()
        if upper.startswith("ADVERTISER"):
            advertiser = re.sub(r"(?i)^ADVERTISER\s*", "", stripped).strip()
        elif upper.startswith("PREPARED BY"):
            prepared_by = re.sub(r"(?i)^PREPARED BY:?\s*", "", stripped).strip()
        elif upper.startswith("FLIGHT DATE"):
            flight_raw = re.sub(r"(?i)^FLIGHT DATE:?\s*", "", stripped).strip()
        elif "TOTAL GROSS BUDGET" in upper:
            m = re.search(r"[\$]?([\d,]+\.?\d*)", stripped)
            if m:
                try:
                    gross_budget = Decimal(m.group(1).replace(",", "")).quantize(
                        Decimal("0.01"), ROUND_HALF_UP
                    )
                except Exception:
                    pass

    flight_start, flight_end = _parse_flight_dates(flight_raw)

    print(f"[POLARIS PARSER] Advertiser:  {advertiser}")
    print(f"[POLARIS PARSER] Prepared by: {prepared_by}")
    print(f"[POLARIS PARSER] Flight:      {flight_start} – {flight_end}")
    print(f"[POLARIS PARSER] Budget:      ${gross_budget:,}")

    # ── Table rows ─────────────────────────────────────────────────────────
    lines: List[PolarisLine] = []
    current_market = "SFO"

    if not tables:
        raise ValueError(f"No tables found in Polaris PDF: {path}")

    table = tables[0]
    for row in table:
        if row is None or all(c is None or str(c).strip() == "" for c in row):
            continue

        cells = [str(c or "").strip() for c in row]

        # Market header cell (col 0) — update market, but still process row data
        if cells[0] and "CROSSINGS TV" in cells[0].upper():
            current_market = _detect_market(cells[0])

        # Column-header row
        if cells[1].upper() in ("DAYS", "DAY"):
            continue

        # TOTAL row signals end
        if "TOTAL" in cells[4].upper() or "TOTAL" in cells[1].upper():
            break

        days_str  = cells[1]
        time_str  = cells[2]
        units_str = cells[5]

        if not days_str or not time_str:
            continue

        # Program name may overflow into the rate cell — recombine and extract
        program = cells[3]
        rate_cell = cells[4]
        m_rate = re.search(r'\$\s*([\d,]+(?:\.\d+)?)', rate_cell)
        if not m_rate:
            continue
        if not program:
            continue
        overflow = rate_cell[:m_rate.start()].strip()
        if overflow:
            program = program + overflow

        try:
            rate = Decimal(m_rate.group(1).replace(",", "")).quantize(
                Decimal("0.01"), ROUND_HALF_UP
            )
        except Exception:
            continue

        try:
            total_spots = int(units_str.replace(",", "").strip() or "0")
        except (TypeError, ValueError):
            continue

        if total_spots <= 0:
            continue

        days = to_etere(days_str)
        line = PolarisLine(
            days=days,
            time_str=time_str,
            program=program,
            rate=rate,
            total_spots=total_spots,
            market=current_market,
        )
        lines.append(line)

        rate_label = "BONUS" if line.is_bonus else f"${rate}"
        print(f"[POLARIS PARSER]   {days:<8s}  {time_str:<15s}  "
              f"{program:<30s}  {rate_label}/spot  {total_spots} spots  [{current_market}]")

    if not lines:
        raise ValueError(f"No lines parsed from Polaris PDF: {path}")

    print(f"[POLARIS PARSER] Total: {len(lines)} lines, "
          f"{sum(ln.total_spots for ln in lines)} spots")

    return PolarisOrder(
        advertiser=advertiser,
        prepared_by=prepared_by,
        flight_start=flight_start,
        flight_end=flight_end,
        gross_budget=gross_budget,
        lines=lines,
    )


def parse_polaris_file(path: str) -> PolarisOrder:
    """Dispatch to PDF or xlsx parser based on file extension."""
    if str(path).lower().endswith(".pdf"):
        return parse_polaris_pdf(path)
    return parse_polaris_xlsx(path)


def _norm(v) -> str:
    return " ".join(str(v or "").split()).strip().lower()


def _schedule_header_index(rows: List[tuple]) -> int | None:
    """Row index of the AAPI TV Schedule column-header row, or None when the
    workbook is the older positional insertion order."""
    for i, row in enumerate(rows[:40]):
        cells = {_norm(v) for v in row}
        if {"media name", "program", "days"} <= cells:
            return i
    return None


def parse_polaris_xlsx(path: str) -> PolarisOrder:
    """
    Parse a Polaris insertion order Excel file.

    One order type, two readers (the Crispin rule): Polaris has sent two
    layouts of the "Crossings TV" sheet, and the dispatcher routes on the
    column-header row so the automation, bridge and gather never learn there
    are two.

      * Positional insertion order (Prop C, April 2026): Media/MARKET | DAYS |
        Time | Programming | Gross Rate per :30s | Unit | GROSS.
      * AAPI TV Schedule (Affordable Santa Clara, Sept 2026): Media Name |
        Program | Days | Time Period | Daypart | Unit | <week> | # Spot |
        GROSS Rate | NET Rate | TOTAL GROSS COST, columns mapped by label.

    Raises:
        ValueError: If no lines can be parsed or required fields are missing.
    """
    import openpyxl

    print(f"\n[POLARIS PARSER] Reading: {path}")

    wb = openpyxl.load_workbook(str(path), data_only=True)
    ws = wb["Crossings TV"] if "Crossings TV" in wb.sheetnames else wb.active
    rows = list(ws.iter_rows(values_only=True))
    wb.close()

    hdr = _schedule_header_index(rows)
    if hdr is not None:
        return _parse_schedule_rows(rows, hdr, path)
    return _parse_positional_rows(rows, path)


# ── AAPI TV Schedule layout (label-mapped) ────────────────────────────────────

_SCHEDULE_LABELS = {
    "media": ("media name",),
    "program": ("program",),
    "days": ("days",),
    "time": ("time period",),
    "unit": ("unit",),
    "spots": ("# spot", "# spots"),
    "gross_rate": ("gross rate",),
    "net_rate": ("net rate",),
    "gross_cost": ("total gross cost",),
}

# The block's "DMA: xx" cell names the station the spots air on.
_DMA_MARKETS = {
    "SF": "SFO",
    "LA": "LAX",
    "SAC": "CVC",
    "SEA": "SEA",
    "CHI": "CMP",
    "HOU": "HOU",
    "DC": "WDC",
    "NY": "NYC",
    "NYC": "NYC",
    "DAL": "DAL",
}


def _money(v) -> Decimal | None:
    """A money cell, or None when it cannot be read — never a silent 0."""
    if v is None or (isinstance(v, str) and not v.strip()):
        return None
    try:
        return Decimal(str(v).replace("$", "").replace(",", "").strip()).quantize(
            Decimal("0.01"), ROUND_HALF_UP
        )
    except Exception:
        return None


def _int_cell(v) -> int | None:
    if v is None or (isinstance(v, str) and not v.strip()):
        return None
    try:
        return int(Decimal(str(v).strip()))
    except Exception:
        return None


def _map_schedule_columns(header: tuple) -> dict[str, int]:
    cols: dict[str, int] = {}
    for idx, v in enumerate(header):
        n = _norm(v)
        for key, labels in _SCHEDULE_LABELS.items():
            if key not in cols and any(n == lab or n.startswith(lab) for lab in labels):
                cols[key] = idx
    missing = [k for k in _SCHEDULE_LABELS if k not in cols]
    if missing:
        raise ValueError(
            f"Polaris schedule: column header(s) not found: {', '.join(missing)} "
            f"— header row reads {[str(v) for v in header if v is not None]}"
        )
    return cols


def _parse_schedule_rows(rows: List[tuple], hdr: int, path: str) -> PolarisOrder:
    header = rows[hdr]
    cols = _map_schedule_columns(header)

    # Week columns: numeric day-of-month headers (I10 = 29 for "WEEK OF 9/29").
    week_cols = [
        i for i, v in enumerate(header)
        if i not in cols.values() and (isinstance(v, (int, float)) or isinstance(v, date))
    ]
    if len(week_cols) != 1:
        raise ValueError(
            f"Polaris schedule: expected exactly one week column, found {len(week_cols)} "
            "— Polaris buys arrive one week at a time; a multi-week sheet needs week "
            "consolidation before it can be entered"
        )
    week_col = week_cols[0]

    # ── Header block above the grid ───────────────────────────────────────
    advertiser = prepared_by = flight_raw = ""
    gross_budget: Decimal | None = None
    year: int | None = None
    for row in rows[:hdr]:
        label = _norm(row[2] if len(row) > 2 else None)
        value = row[3] if len(row) > 3 else None
        if label == "advertiser":
            advertiser = str(value or "").strip()
        elif label.startswith("prepared by"):
            prepared_by = str(value or "").strip()
        elif label.startswith("flight date"):
            flight_raw = str(value or "").strip()
        elif "total gross budget" in label:
            gross_budget = _money(value)
        for v in row:
            if isinstance(v, int) and 2000 <= v <= 2100:
                year = v
    if not advertiser:
        raise ValueError("Polaris schedule: no Advertiser cell found")
    if gross_budget is None:
        raise ValueError("Polaris schedule: TOTAL GROSS BUDGET is missing or unreadable")

    flight_start, flight_end = _parse_flight_dates(flight_raw, year)
    print(f"[POLARIS PARSER] Advertiser:  {advertiser}")
    print(f"[POLARIS PARSER] Prepared by: {prepared_by}")
    print(f"[POLARIS PARSER] Flight:      {flight_start} – {flight_end}")
    print(f"[POLARIS PARSER] Budget:      ${gross_budget:,}")

    # ── Blocks: one language per block, blank row between, "Total" row ends ─
    def cell(row, key):
        i = cols[key]
        return row[i] if i < len(row) else None

    blocks: List[List[tuple]] = [[]]
    total_row: tuple | None = None
    for row in rows[hdr + 1:]:
        media = _norm(cell(row, "media"))
        if media.startswith("total"):
            total_row = row
            break
        if all(v is None or (isinstance(v, str) and not v.strip()) for v in row):
            if blocks[-1]:
                blocks.append([])
            continue
        blocks[-1].append(row)
    if total_row is None:
        raise ValueError("Polaris schedule: no 'Total' row found under the grid")

    lines: List[PolarisLine] = []
    ratios: set[Decimal] = set()
    cost_sum = Decimal("0")
    for block in blocks:
        if not block:
            continue
        market = None
        for row in block:
            m = re.match(r"dma:\s*([a-z]+)", _norm(cell(row, "media")))
            if m:
                market = _DMA_MARKETS.get(m.group(1).upper())
                if market is None:
                    raise ValueError(f"Polaris schedule: unrecognised DMA {m.group(1)!r}")
        for row in block:
            program = " ".join(str(cell(row, "program") or "").split())
            if not program:
                continue
            spots = _int_cell(cell(row, "spots"))
            week_spots = _int_cell(row[week_col] if week_col < len(row) else None)
            if spots is None or week_spots is None:
                raise ValueError(f"Polaris schedule: unreadable spot count on {program!r}")
            if spots != week_spots:
                raise ValueError(
                    f"Polaris schedule: {program!r} week column says {week_spots} but "
                    f"# Spot says {spots}"
                )
            gross_cost = _money(cell(row, "gross_cost"))
            if spots <= 0:
                if gross_cost not in (None, Decimal("0.00")):
                    raise ValueError(f"Polaris schedule: {program!r} has 0 spots but a cost")
                continue
            if market is None:
                raise ValueError(
                    f"Polaris schedule: no 'DMA:' cell in the block holding {program!r} "
                    "— the market decides where spots air and is never defaulted"
                )
            rate = _money(cell(row, "gross_rate"))
            net = _money(cell(row, "net_rate"))
            if rate is None or gross_cost is None:
                raise ValueError(f"Polaris schedule: unreadable GROSS Rate / cost on {program!r}")
            if rate * spots != gross_cost:
                raise ValueError(
                    f"Polaris schedule: {program!r} {spots} × ${rate} = ${rate * spots} "
                    f"but TOTAL GROSS COST reads ${gross_cost}"
                )
            if rate > 0 and net is not None:
                ratios.add((net / rate).quantize(Decimal("0.0001")))
            days_raw = str(cell(row, "days") or "").strip()
            time_str = " ".join(str(cell(row, "time") or "").split())
            if not days_raw or not time_str:
                raise ValueError(f"Polaris schedule: {program!r} is missing days or time")
            unit = re.search(r"(\d+)", str(cell(row, "unit") or ""))
            if not unit:
                raise ValueError(f"Polaris schedule: {program!r} has no spot length (Unit)")
            line = PolarisLine(
                days=to_etere(days_raw),
                time_str=time_str,
                program=program,
                rate=rate,
                total_spots=spots,
                market=market,
                duration=int(unit.group(1)),
            )
            lines.append(line)
            cost_sum += gross_cost
            rate_label = "BONUS" if line.is_bonus else f"${rate}"
            print(f"[POLARIS PARSER]   {line.days:<8s}  {time_str:<15s}  "
                  f"{program:<30s}  {rate_label}/spot  :{line.duration}  {spots} spots  [{market}]")

    if not lines:
        raise ValueError(f"No lines parsed from Polaris xlsx: {path}")
    if len(ratios) > 1:
        raise ValueError(
            f"Polaris schedule: NET/GROSS ratio differs across paid lines ({sorted(ratios)})"
        )

    # ── Reconcile against the sheet's own totals ───────────────────────────
    spot_sum = sum(ln.total_spots for ln in lines)
    if cost_sum != gross_budget:
        raise ValueError(
            f"Polaris schedule: lines total ${cost_sum} but TOTAL GROSS BUDGET is ${gross_budget}"
        )
    nums = {_money(v) for v in total_row if _money(v) is not None}
    if cost_sum not in nums:
        raise ValueError(
            f"Polaris schedule: Total row {sorted(nums)} does not carry the lines' ${cost_sum}"
        )
    # The Total row's spot figure is the sheet's own formula and has been wrong
    # (Sept 2026: `=SUM(J11:J17)` counted 13 of 25). Dollars are the hard oracle;
    # a spot-count disagreement is reported so the operator can tell the agency.
    warnings: List[str] = []
    total_spots_cell = _int_cell(cell(total_row, "spots"))
    if total_spots_cell is not None and total_spots_cell != spot_sum:
        warnings.append(
            f"the sheet's Total row says {total_spots_cell} spots but the lines hold "
            f"{spot_sum} (dollars reconcile at ${cost_sum}) — the agency's total formula "
            "skips rows; entering the lines as printed"
        )
        print(f"[POLARIS PARSER] ⚠ {warnings[-1]}")

    print(f"[POLARIS PARSER] Total: {len(lines)} lines, {spot_sum} spots, ${cost_sum:,} "
          f"(reconciled to budget and Total row)")
    return PolarisOrder(
        advertiser=advertiser,
        prepared_by=prepared_by,
        flight_start=flight_start,
        flight_end=flight_end,
        gross_budget=gross_budget,
        lines=lines,
        warnings=warnings,
    )


# ── Positional insertion-order layout (Prop C, April 2026) ────────────────────


def _parse_positional_rows(rows: List[tuple], path: str) -> PolarisOrder:
    """Header rows are located by scanning for their label text (col C) rather
    than by hardcoded row indices, so the reader is robust to leading blank
    rows or minor layout shifts."""
    # ── Scan header rows by label ──────────────────────────────────────────
    advertiser   = ""
    prepared_by  = ""
    flight_raw   = ""
    gross_budget = Decimal("0")
    data_start   = len(rows)  # will be updated when column-header row is found

    for i, row in enumerate(rows):
        label = str(row[2] or "").strip().upper()
        value = row[3]

        if label == "ADVERTISER":
            advertiser = str(value or "").strip()
        elif label in ("PREPARED BY:", "PREPARED BY"):
            prepared_by = str(value or "").strip()
        elif label in ("FLIGHT DATE:", "FLIGHT DATE"):
            flight_raw = str(value or "").strip()
        elif "TOTAL GROSS BUDGET" in label:
            try:
                gross_budget = Decimal(str(value or 0)).quantize(
                    Decimal("0.01"), ROUND_HALF_UP
                )
            except Exception:
                pass
        elif label in ("MEDIA /MARKET", "MEDIA/MARKET") and str(value or "").strip().upper() == "DAYS":
            # This is the column-header row; data begins on the next row
            data_start = i + 1
            break

    flight_start, flight_end = _parse_flight_dates(flight_raw)

    print(f"[POLARIS PARSER] Advertiser:  {advertiser}")
    print(f"[POLARIS PARSER] Prepared by: {prepared_by}")
    print(f"[POLARIS PARSER] Flight:      {flight_start} – {flight_end}")
    print(f"[POLARIS PARSER] Budget:      ${gross_budget:,}")

    # ── Data rows ─────────────────────────────────────────────────────────
    lines: List[PolarisLine] = []
    current_market = "SFO"  # fallback if no market header is seen

    for row in rows[data_start:]:
        # Col 2: optional market header
        market_cell = str(row[2] or "").strip()
        if market_cell and "CROSSINGS TV" in market_cell.upper():
            current_market = _detect_market(market_cell)

        # Col 3: days — skip blank or label rows
        days_raw = row[3]
        if days_raw is None:
            continue
        days_str = str(days_raw).strip()
        if not days_str or days_str.upper() in ("DAYS",):
            continue

        # Col 6: rate — "TOTAL" string signals end of data
        rate_raw = row[6]
        if isinstance(rate_raw, str) and "TOTAL" in rate_raw.upper():
            break

        # Col 4: time
        time_raw = row[4]
        if time_raw is None:
            continue
        time_str = str(time_raw).strip()
        if not time_str:
            continue

        # Col 5: program name
        program = str(row[5] or "").strip()
        if not program:
            continue

        # Col 6: rate (numeric)
        try:
            rate = Decimal(str(rate_raw or 0)).quantize(Decimal("0.01"), ROUND_HALF_UP)
        except Exception:
            continue

        # Col 7: units / total spots
        try:
            total_spots = int(row[7] or 0)
        except (TypeError, ValueError):
            continue

        if total_spots <= 0:
            continue

        days = to_etere(days_str)
        line = PolarisLine(
            days=days,
            time_str=time_str,
            program=program,
            rate=rate,
            total_spots=total_spots,
            market=current_market,
        )
        lines.append(line)

        rate_label = "BONUS" if line.is_bonus else f"${rate}"
        print(f"[POLARIS PARSER]   {days:<8s}  {time_str:<15s}  "
              f"{program:<30s}  {rate_label}/spot  {total_spots} spots  [{current_market}]")

    if not lines:
        raise ValueError(f"No lines parsed from Polaris xlsx: {path}")

    print(f"[POLARIS PARSER] Total: {len(lines)} lines, "
          f"{sum(ln.total_spots for ln in lines)} spots")

    return PolarisOrder(
        advertiser=advertiser,
        prepared_by=prepared_by,
        flight_start=flight_start,
        flight_end=flight_end,
        gross_budget=gross_budget,
        lines=lines,
    )


# ─────────────────────────────────────────────────────────────────────────────
# STANDALONE TEST
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys as _sys

    if len(_sys.argv) < 2:
        print("Usage: python polaris_parser.py <xlsx_path>")
        _sys.exit(1)

    try:
        order = parse_polaris_file(_sys.argv[1])

        print("\n" + "=" * 70)
        print("POLARIS ORDER SUMMARY")
        print("=" * 70)
        print(f"Advertiser:  {order.advertiser}")
        print(f"Prepared by: {order.prepared_by}")
        print(f"Flight:      {order.flight_start} – {order.flight_end}")
        print(f"Budget:      ${order.gross_budget:,}")
        print(f"Markets:     {order.markets}")
        print(f"Total Lines: {len(order.lines)}")
        print(f"Total Spots: {order.total_spots}")

        print("\n" + "=" * 70)
        print("LINES")
        print("=" * 70)
        for ln in order.lines:
            tf, tt = ln.get_time_from_to()
            print(f"\n  [{ln.market}] {ln.days}  {ln.time_str}  →  {tf}–{tt}")
            print(f"  Program:  {ln.program}")
            print(f"  Rate:     {'BONUS' if ln.is_bonus else f'${ln.rate}'}")
            print(f"  Spots:    {ln.total_spots}")
            print(f"  Desc:     {ln.get_description()}")

    except Exception as exc:
        print(f"\n✗ Error: {exc}")
        import traceback
        traceback.print_exc()
        _sys.exit(1)
