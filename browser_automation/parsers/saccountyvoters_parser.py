"""
Sacramento County Voter Registration Order Parser

Parses the two-phase insertion order PDF from Sacramento County.

PDF has a unique header format with two campaign phases on one page:
- Phase 1: :15s, 4/7/2026 – 5/4/2026
- Phase 2: :30s, 5/5/2026 – 6/2/2026

7 tables total: T1/T4=paid, T2/T5=bonus, T3/T6=phase summary, T7=contract summary.
Rate OCR artifacts: "$ 2 5.00" → 25.0 (strip all non-digit/non-decimal chars).
"""

import pdfplumber
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

_project_root = Path(__file__).parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))


# ─────────────────────────────────────────────────────────────────────────────
# DATACLASSES
# ─────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class SacCountyVotersLine:
    """A single line item from a Sacramento County Voters order."""
    language: str          # "Chinese(Cantonese) News", "Korean", etc.
    daypart: str           # "M-F 7p-8p/ 11:30p-12a" or "ROS Bonus"
    rate: float            # Per-spot rate (0.0 for bonus)
    weekly_spots: List[int]
    total_spots: int
    is_bonus: bool         # True when daypart == "ROS Bonus"


@dataclass(frozen=True)
class SacCountyVotersPhase:
    """One phase of the Sacramento County Voters campaign."""
    phase_number: int          # 1 or 2
    duration_seconds: int      # 15 or 30
    flight_start: str          # MM/DD/YYYY
    flight_end: str            # MM/DD/YYYY
    week_columns: List[str]    # ["6-Apr", "13-Apr", ...] raw labels
    lines: List[SacCountyVotersLine]


@dataclass(frozen=True)
class SacCountyVotersOrder:
    """Complete parsed Sacramento County Voters Registration order."""
    client: str       # "Sacramento County Voter/Registration"
    contact: str      # "Karalyn Fox"
    email: str        # "FoxK@saccounty.gov"
    campaign: str     # "Voter Registration Media Campaign"
    market: str       # "CVC"
    phases: List[SacCountyVotersPhase]


# ─────────────────────────────────────────────────────────────────────────────
# MONTH MAPPING
# ─────────────────────────────────────────────────────────────────────────────

_MONTH_NAMES = {
    "Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4,
    "May": 5, "Jun": 6, "Jul": 7, "Aug": 8,
    "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12,
}

_WEEK_DATE_RE = re.compile(r'^(\d{1,2})-(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)$')


def _week_label_to_date(label: str, year: int) -> str:
    """
    Convert a week column label like "6-Apr" to "04/06/YYYY" (MM/DD/YYYY).

    Returns empty string if label doesn't match expected pattern.
    """
    m = _WEEK_DATE_RE.match(label.strip())
    if not m:
        return ""
    day = int(m.group(1))
    month = _MONTH_NAMES.get(m.group(2), 0)
    if not month:
        return ""
    return f"{month:02d}/{day:02d}/{year}"


# ─────────────────────────────────────────────────────────────────────────────
# RATE PARSING
# ─────────────────────────────────────────────────────────────────────────────

def _parse_rate(cell: str) -> float:
    """
    Parse a rate cell with possible OCR spacing artifacts.

    "$ 2 5.00" → 25.0
    "$25.00"   → 25.0
    "$-"       → 0.0
    """
    if not cell or not re.search(r'\d', cell):
        return 0.0
    cleaned = re.sub(r'[^\d.]', '', cell)
    try:
        return float(cleaned)
    except ValueError:
        return 0.0


# ─────────────────────────────────────────────────────────────────────────────
# TABLE IDENTIFICATION
# ─────────────────────────────────────────────────────────────────────────────

def _is_data_table(rows: List[List[Optional[str]]]) -> bool:
    """
    Return True if this table is a data table (paid or bonus lines).

    Criteria:
    - At least 3 columns
    - Row 0, col 0 == "Insertion"
    - Row 0, col 1 == "Time"
    - At least one week-date column header matching "d-Mon" pattern
    - Row 0, col 0 does NOT contain "Phase", "Summary", "Total"
    """
    if not rows or len(rows[0]) < 3:
        return False

    first_cell = (rows[0][0] or "").strip()
    second_cell = (rows[0][1] or "").strip()

    # Skip summary / phase summary tables
    skip_keywords = ("phase", "summary", "total", "contract")
    if any(kw in first_cell.lower() for kw in skip_keywords):
        return False

    if first_cell.lower() != "insertion":
        return False
    if second_cell.lower() != "time":
        return False

    # Must have at least one week-date column
    _DATE_COL_RE = re.compile(r'^\d{1,2}-(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)$',
                               re.IGNORECASE)
    has_date_col = any(
        _DATE_COL_RE.match((cell or "").strip())
        for cell in rows[0]
    )
    return has_date_col


# ─────────────────────────────────────────────────────────────────────────────
# ROW PARSING
# ─────────────────────────────────────────────────────────────────────────────

def _parse_data_table(
    rows: List[List[Optional[str]]],
    phase_number: int,
) -> Tuple[List[str], List[SacCountyVotersLine]]:
    """
    Parse a single data table into week column labels and line items.

    Returns (week_columns, lines).
    """
    if not rows:
        return [], []

    header = rows[0]
    n_cols = len(header)

    # Find week-date column indices
    _DATE_COL_RE = re.compile(r'^\d{1,2}-(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)$',
                               re.IGNORECASE)
    week_col_indices = [
        i for i, cell in enumerate(header)
        if _DATE_COL_RE.match((cell or "").strip())
    ]
    week_columns = [(header[i] or "").strip() for i in week_col_indices]

    # Find "Units" / "Total" column (rightmost non-week numeric column)
    # Typically: Insertion | Time | Rate | week1 | week2 | ... | Total
    # We look for a col header containing "unit" or "total"
    units_col_idx = None
    for i, cell in enumerate(header):
        c = (cell or "").strip().lower()
        if "unit" in c or "total" in c:
            units_col_idx = i

    # Column layout: [0]=Language(Insertion), [1]=Daypart(Time), [2]=Rate, ...weeks..., [units_col]=Total
    lines: List[SacCountyVotersLine] = []

    for row_idx, row in enumerate(rows[1:], start=1):
        # Skip header rows, empty rows, and summary rows
        if not row or all((cell or "").strip() == "" for cell in row):
            continue

        # Pad row to header length
        row = list(row) + [None] * (n_cols - len(row))

        language_cell = (row[0] or "").strip()
        daypart_cell = (row[1] or "").strip()
        rate_cell = (row[2] or "").strip() if len(row) > 2 else ""

        # Skip empty / summary rows
        if not language_cell and not daypart_cell:
            continue

        # Skip summary/total rows (by language, daypart, or rate column)
        if "total" in language_cell.lower():
            continue
        if "total" in daypart_cell.lower():
            continue
        if "total" in (row[2] or "").strip().lower():
            continue

        is_bonus = daypart_cell.strip().lower() == "ros bonus"
        rate = _parse_rate(rate_cell) if not is_bonus else 0.0

        # Weekly spots
        weekly_spots = []
        for i in week_col_indices:
            if i < len(row):
                val = (row[i] or "").strip()
                try:
                    weekly_spots.append(int(val) if val else 0)
                except ValueError:
                    weekly_spots.append(0)
            else:
                weekly_spots.append(0)

        # Total spots
        total_spots = 0
        if units_col_idx is not None and units_col_idx < len(row):
            val = (row[units_col_idx] or "").strip()
            try:
                total_spots = int(val) if val else sum(weekly_spots)
            except ValueError:
                total_spots = sum(weekly_spots)
        else:
            total_spots = sum(weekly_spots)

        if total_spots == 0 and not any(weekly_spots):
            continue

        lines.append(SacCountyVotersLine(
            language=language_cell,
            daypart=daypart_cell,
            rate=rate,
            weekly_spots=weekly_spots,
            total_spots=total_spots,
            is_bonus=is_bonus,
        ))

    return week_columns, lines


# ─────────────────────────────────────────────────────────────────────────────
# MAIN PARSER
# ─────────────────────────────────────────────────────────────────────────────

def parse_saccountyvoters_pdf(pdf_path: str) -> SacCountyVotersOrder:
    """
    Parse a Sacramento County Voter Registration insertion order PDF.

    Returns SacCountyVotersOrder with 2 phases.

    Raises:
        ValueError: If critical fields cannot be parsed.
    """
    print(f"\n[SACCOUNTYVOTERS PARSER] Reading: {pdf_path}")

    with pdfplumber.open(pdf_path) as pdf:
        # Collect full text and all tables from all pages
        full_text = ""
        all_tables: List[List[List[Optional[str]]]] = []

        for page in pdf.pages:
            page_text = page.extract_text() or ""
            full_text += page_text + "\n"
            tables = page.extract_tables() or []
            all_tables.extend(tables)

        # ── Header fields ─────────────────────────────────────────────────────
        def _field(pattern: str) -> str:
            m = re.search(pattern, full_text, re.IGNORECASE | re.MULTILINE)
            return m.group(1).strip() if m else ""

        client  = _field(r'Client:\s*([^\n]+)')
        contact = _field(r'Contact:\s*([^\n]+)')
        email   = _field(r'Email:\s*([^\n]+)')

        # Campaign: first non-empty meaningful line (strip artifacts)
        campaign = ""
        for line in full_text.split('\n'):
            line = line.strip()
            if line and not re.match(r'^(Client|Contact|Email|Phase|Market):', line, re.IGNORECASE):
                # Take the first substantial line that looks like a campaign name
                if len(line) > 5 and not re.match(r'^\d', line):
                    campaign = line
                    break

        print(f"[SACCOUNTYVOTERS PARSER] Client:   {client}")
        print(f"[SACCOUNTYVOTERS PARSER] Contact:  {contact}")
        print(f"[SACCOUNTYVOTERS PARSER] Email:    {email}")
        print(f"[SACCOUNTYVOTERS PARSER] Campaign: {campaign}")

        # ── Phase date/duration lines ─────────────────────────────────────────
        # Pattern: "Phase 1 Length: :15 seconds  4/7/2026 through 5/4/2026"
        # or split across lines depending on OCR
        phase_pattern = re.compile(
            r'Phase\s+(\d)\s+Length:\s*:(\d+)\s*seconds?\s+([\d/]+)\s+through\s+([\d/]+)',
            re.IGNORECASE
        )
        phase_matches = list(phase_pattern.finditer(full_text))

        if len(phase_matches) < 2:
            raise ValueError(
                f"Expected 2 Phase Length lines, found {len(phase_matches)}. "
                "Check PDF text extraction."
            )

        phase_info: List[Tuple[int, int, str, str]] = []
        for m in phase_matches[:2]:
            ph_num     = int(m.group(1))
            ph_dur_sec = int(m.group(2))
            ph_start   = _normalize_date(m.group(3))
            ph_end     = _normalize_date(m.group(4))
            phase_info.append((ph_num, ph_dur_sec, ph_start, ph_end))
            print(f"[SACCOUNTYVOTERS PARSER] Phase {ph_num}: :{ph_dur_sec}s  {ph_start} – {ph_end}")

        # ── Filter data tables ────────────────────────────────────────────────
        data_tables = [t for t in all_tables if _is_data_table(t)]
        print(f"[SACCOUNTYVOTERS PARSER] Total tables: {len(all_tables)}, data tables: {len(data_tables)}")

        if len(data_tables) < 4:
            raise ValueError(
                f"Expected ≥4 data tables (2 paid + 2 bonus), found {len(data_tables)}."
            )

        # Data tables in order: [Phase1Paid, Phase1Bonus, Phase2Paid, Phase2Bonus]
        # If any table has only "ROS Bonus" rows it's a bonus table, else paid.
        # Assign by order they appear (PDF order = Phase1Paid, Phase1Bonus, Phase2Paid, Phase2Bonus)
        ph1_paid_rows, ph1_bonus_rows, ph2_paid_rows, ph2_bonus_rows = (
            data_tables[0], data_tables[1], data_tables[2], data_tables[3]
        )

        # ── Parse each table ──────────────────────────────────────────────────
        ph1_year = int(phase_info[0][2].split('/')[-1])
        ph2_year = int(phase_info[1][2].split('/')[-1])

        ph1_paid_weeks, ph1_paid_lines   = _parse_data_table(ph1_paid_rows, 1)
        _,              ph1_bonus_lines  = _parse_data_table(ph1_bonus_rows, 1)
        ph2_paid_weeks, ph2_paid_lines   = _parse_data_table(ph2_paid_rows, 2)
        _,              ph2_bonus_lines  = _parse_data_table(ph2_bonus_rows, 2)

        print(f"[SACCOUNTYVOTERS PARSER] Phase 1 weeks: {ph1_paid_weeks}")
        print(f"[SACCOUNTYVOTERS PARSER] Phase 1 paid lines:  {len(ph1_paid_lines)}")
        print(f"[SACCOUNTYVOTERS PARSER] Phase 1 bonus lines: {len(ph1_bonus_lines)}")
        print(f"[SACCOUNTYVOTERS PARSER] Phase 2 weeks: {ph2_paid_weeks}")
        print(f"[SACCOUNTYVOTERS PARSER] Phase 2 paid lines:  {len(ph2_paid_lines)}")
        print(f"[SACCOUNTYVOTERS PARSER] Phase 2 bonus lines: {len(ph2_bonus_lines)}")

        phases = [
            SacCountyVotersPhase(
                phase_number=phase_info[0][0],
                duration_seconds=phase_info[0][1],
                flight_start=phase_info[0][2],
                flight_end=phase_info[0][3],
                week_columns=ph1_paid_weeks,
                lines=ph1_paid_lines + ph1_bonus_lines,
            ),
            SacCountyVotersPhase(
                phase_number=phase_info[1][0],
                duration_seconds=phase_info[1][1],
                flight_start=phase_info[1][2],
                flight_end=phase_info[1][3],
                week_columns=ph2_paid_weeks,
                lines=ph2_paid_lines + ph2_bonus_lines,
            ),
        ]

        return SacCountyVotersOrder(
            client=client or "Sacramento County Voter/Registration",
            contact=contact,
            email=email,
            campaign=campaign,
            market="CVC",
            phases=phases,
        )


def _normalize_date(date_str: str) -> str:
    """
    Normalize date string to MM/DD/YYYY format.

    "4/7/2026" → "04/07/2026"
    "4/7/26"   → "04/07/2026"
    """
    parts = date_str.strip().split('/')
    if len(parts) != 3:
        return date_str
    mm, dd, yy = parts
    mm = mm.zfill(2)
    dd = dd.zfill(2)
    if len(yy) == 2:
        yy = f"20{yy}" if int(yy) <= 50 else f"19{yy}"
    return f"{mm}/{dd}/{yy}"


# ─────────────────────────────────────────────────────────────────────────────
# STANDALONE TEST ENTRY POINT
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: uv run python browser_automation/parsers/saccountyvoters_parser.py <pdf_path>")
        sys.exit(1)

    try:
        order = parse_saccountyvoters_pdf(sys.argv[1])

        print("\n" + "=" * 70)
        print("SACRAMENTO COUNTY VOTERS ORDER SUMMARY")
        print("=" * 70)
        print(f"Client:   {order.client}")
        print(f"Contact:  {order.contact}")
        print(f"Email:    {order.email}")
        print(f"Campaign: {order.campaign}")
        print(f"Market:   {order.market}")

        for phase in order.phases:
            paid_lines  = [l for l in phase.lines if not l.is_bonus]
            bonus_lines = [l for l in phase.lines if l.is_bonus]
            print(f"\n{'─'*70}")
            print(f"Phase {phase.phase_number}: :{phase.duration_seconds}s")
            print(f"  Flight:  {phase.flight_start} – {phase.flight_end}")
            print(f"  Weeks:   {phase.week_columns}")
            print(f"  Paid:    {len(paid_lines)} lines")
            print(f"  Bonus:   {len(bonus_lines)} lines")

            print(f"\n  Paid lines:")
            for line in paid_lines:
                print(f"    [{line.language}] {line.daypart!r}  ${line.rate}  "
                      f"spots={line.weekly_spots}  total={line.total_spots}")

            print(f"\n  Bonus lines:")
            for line in bonus_lines:
                print(f"    [{line.language}] {line.daypart!r}  "
                      f"spots={line.weekly_spots}  total={line.total_spots}")

    except Exception as exc:
        print(f"\n✗ Error: {exc}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
