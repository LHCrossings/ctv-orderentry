"""
Sacramento County Water Agency (SCWA) Order Parser

Parses the Crossings TV house-format media proposal PDF used for SCWA.

PDF structure:
- Header: Advertiser, Contact, Campaign Name, Market, dates
- Table 0: Spot Name | Market | Language Block | Fix/ROS | Length |
           Start | End Date | Spot Type | Total Unit # | Promo Unit Cost |
           Line Total Cost | Unit Value | Line Total
- One row per language block (Chinese, Filipino, Hmong, South Asian, Vietnamese)
- "Total Unit #" = total spots for the entire flight (no weekly breakdown)
- Rate to use: "Promo Unit Cost" column
- "Fix/ROS" column: "ROS" means run-of-schedule, "Fixed" means specific daypart
"""

import pdfplumber
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

_project_root = Path(__file__).parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))


# ─────────────────────────────────────────────────────────────────────────────
# DATACLASSES
# ─────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class SCWALine:
    """A single airtime line from an SCWA order."""
    language_block: str     # e.g. "Chinese (Mandarin /Cantonese, excluding Children)"
    fix_ros: str            # "ROS" or "Fixed"
    duration_seconds: int   # 30
    start_date: str         # "04/01/2026" (MM/DD/YYYY)
    end_date: str           # "04/30/2026" (MM/DD/YYYY)
    spot_type: str          # "COM"
    total_spots: int        # total for the full flight
    rate: float             # Promo Unit Cost


@dataclass(frozen=True)
class SCWAOrder:
    """Complete parsed SCWA order."""
    advertiser: str         # "Sacramento County Water Agency"
    contact: str            # "Matthew Robinson"
    email: str              # "robinsonma@saccounty.gov"
    campaign: str           # "26'April"
    market: str             # "CVC"
    billing_cycle: str      # "Calendar"
    lines: List[SCWALine]


# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _parse_rate(cell: str) -> float:
    """Parse a dollar-amount cell: "$ 23.00" → 23.0, "$-" → 0.0."""
    if not cell or not re.search(r'\d', cell):
        return 0.0
    cleaned = re.sub(r'[^\d.]', '', cell)
    try:
        return float(cleaned)
    except ValueError:
        return 0.0


def _parse_duration(cell: str) -> int:
    """Parse "30 seconds" → 30, "15 seconds" → 15."""
    m = re.search(r'(\d+)', cell or "")
    return int(m.group(1)) if m else 30


def _normalize_date(date_str: str) -> str:
    """Normalize M/D/YYYY or M/D/YY to MM/DD/YYYY."""
    parts = (date_str or "").strip().split('/')
    if len(parts) != 3:
        return date_str
    mm, dd, yy = parts
    mm = mm.zfill(2)
    dd = dd.zfill(2)
    if len(yy) == 2:
        yy = f"20{yy}" if int(yy) <= 50 else f"19{yy}"
    return f"{mm}/{dd}/{yy}"


def _field(text: str, pattern: str) -> str:
    """Extract first capture group from regex against full text."""
    m = re.search(pattern, text, re.IGNORECASE)
    return m.group(1).strip() if m else ""


# ─────────────────────────────────────────────────────────────────────────────
# MAIN PARSER
# ─────────────────────────────────────────────────────────────────────────────

def parse_scwa_pdf(pdf_path: str) -> SCWAOrder:
    """
    Parse a Sacramento County Water Agency (SCWA) media proposal PDF.

    Returns SCWAOrder.

    Raises:
        ValueError: If critical fields cannot be parsed.
    """
    print(f"\n[SCWA PARSER] Reading: {pdf_path}")

    with pdfplumber.open(pdf_path) as pdf:
        page = pdf.pages[0]
        full_text = page.extract_text() or ""
        tables = page.extract_tables() or []

    # ── Header fields ──────────────────────────────────────────────────────
    # PDF is two-column, so pdfplumber merges address into the same line.
    # "Advertiser Sacramento County Water Agency Address: 827 ..." → stop at "Address:"
    # "Campaign Name: 26'April Sacramento, CA 95814"              → stop before city/state
    advertiser    = _field(full_text, r'Advertiser\s+(.*?)\s+Address:')
    contact       = _field(full_text, r'Contact:\s*([^\n]+)')
    email         = _field(full_text, r'Email:\s*([^\n]+)')
    campaign_raw  = _field(full_text, r"Campaign Name:\s*([^\n]+)")
    # Strip city/state suffix if merged: "26'April Sacramento, CA 95814" → "26'April"
    campaign      = re.sub(r'\s+\w[\w\s]+,\s*[A-Z]{2}\s+\d{5}.*$', '', campaign_raw).strip()
    market        = _field(full_text, r'Market:\s*(\w+)')
    billing_cycle = _field(full_text, r'Billing Cycle:\s*([^\n]+)')

    print(f"[SCWA PARSER] Advertiser:  {advertiser}")
    print(f"[SCWA PARSER] Contact:     {contact}")
    print(f"[SCWA PARSER] Campaign:    {campaign}")
    print(f"[SCWA PARSER] Market:      {market}")

    # ── Find airtime table ─────────────────────────────────────────────────
    # Table 0 is the airtime table; it has "Language Block" in the header row
    airtime_table = None
    for table in tables:
        if not table or not table[0]:
            continue
        header_text = " ".join(str(c or "") for c in table[0])
        if "Language Block" in header_text and "Total Unit" in header_text:
            airtime_table = table
            break

    if airtime_table is None:
        raise ValueError("[SCWA PARSER] Could not find airtime table in PDF.")

    header = airtime_table[0]

    # Map column names to indices
    def _col(name: str) -> Optional[int]:
        for i, cell in enumerate(header):
            if name.lower() in (cell or "").lower():
                return i
        return None

    col_language  = _col("Language Block")
    col_fix_ros   = _col("Fix/ROS")
    col_length    = _col("Length")
    col_start     = _col("Start")
    col_end       = _col("End Date")
    col_spot_type = _col("Spot Type")
    col_total     = _col("Total Unit")
    col_rate      = _col("Promo Unit Cost")

    # Validate required columns
    missing = [name for name, idx in [
        ("Language Block", col_language),
        ("Fix/ROS",        col_fix_ros),
        ("Total Unit #",   col_total),
        ("Promo Unit Cost",col_rate),
    ] if idx is None]
    if missing:
        raise ValueError(f"[SCWA PARSER] Missing columns: {missing}")

    # ── Parse rows ─────────────────────────────────────────────────────────
    lines: List[SCWALine] = []

    for row in airtime_table[1:]:
        if not row:
            continue

        def cell(idx: Optional[int]) -> str:
            if idx is None or idx >= len(row):
                return ""
            return (row[idx] or "").strip()

        language = cell(col_language)

        # Skip blank / subtotal rows
        if not language or "subtotal" in language.lower():
            continue

        fix_ros      = cell(col_fix_ros) or "ROS"
        duration_str = cell(col_length)
        start_raw    = cell(col_start)
        end_raw      = cell(col_end)
        spot_type    = cell(col_spot_type) or "COM"
        total_raw    = cell(col_total)
        rate_raw     = cell(col_rate)

        try:
            total_spots = int(total_raw) if total_raw else 0
        except ValueError:
            total_spots = 0

        if total_spots == 0:
            print(f"[SCWA PARSER]   Skipping '{language}' — 0 spots")
            continue

        line = SCWALine(
            language_block=language,
            fix_ros=fix_ros,
            duration_seconds=_parse_duration(duration_str),
            start_date=_normalize_date(start_raw),
            end_date=_normalize_date(end_raw),
            spot_type=spot_type,
            total_spots=total_spots,
            rate=_parse_rate(rate_raw),
        )
        lines.append(line)
        print(f"[SCWA PARSER]   {language!r}  {fix_ros}  {total_spots} spots  ${line.rate}")

    if not lines:
        raise ValueError("[SCWA PARSER] No airtime lines parsed from PDF.")

    print(f"[SCWA PARSER] Total lines: {len(lines)}")

    return SCWAOrder(
        advertiser=advertiser or "Sacramento County Water Agency",
        contact=contact,
        email=email,
        campaign=campaign,
        market=market or "CVC",
        billing_cycle=billing_cycle,
        lines=lines,
    )


# ─────────────────────────────────────────────────────────────────────────────
# STANDALONE TEST
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: uv run python browser_automation/parsers/scwa_parser.py <pdf_path>")
        sys.exit(1)

    try:
        order = parse_scwa_pdf(sys.argv[1])

        print("\n" + "=" * 70)
        print("SCWA ORDER SUMMARY")
        print("=" * 70)
        print(f"Advertiser:  {order.advertiser}")
        print(f"Contact:     {order.contact}")
        print(f"Email:       {order.email}")
        print(f"Campaign:    {order.campaign}")
        print(f"Market:      {order.market}")
        print(f"Lines:       {len(order.lines)}")
        total_spots = sum(l.total_spots for l in order.lines)
        total_cost  = sum(l.total_spots * l.rate for l in order.lines)
        print(f"Total spots: {total_spots}")
        print(f"Total cost:  ${total_cost:,.2f}")
        print()
        for line in order.lines:
            print(f"  {line.language_block}")
            print(f"    {line.fix_ros}  :{line.duration_seconds}s  "
                  f"{line.start_date}–{line.end_date}  "
                  f"{line.total_spots} spots  ${line.rate}")

    except Exception as exc:
        import traceback
        print(f"\n✗ Error: {exc}")
        traceback.print_exc()
        sys.exit(1)
