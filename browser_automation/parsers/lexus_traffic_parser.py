"""
Parse IW Group "Television Traffic Sheet" PDFs for Lexus.

One PDF per market. Only the HD section is used; SD is ignored.
Rows are grouped by (duration, flighting date range) into periods.
"""
import io
import re
from dataclasses import dataclass, field
from typing import List, Optional

import pdfplumber

_COVERAGE_TO_MARKET = {
    "new york":       "NYC",
    "san francisco":  "SFO",
    "los angeles":    "LAX",
    "seattle":        "SEA",
    "chicago":        "CMP",
    "minneapolis":    "CMP",
    "houston":        "HOU",
    "washington":     "WDC",
    "sacramento":     "CVC",
    "central valley": "CVC",
    "fresno":         "CVC",
    "dallas":         "DAL",
}


def _parse_date(date_str: str) -> Optional[str]:
    """'M/D/YY' or 'MM/DD/YY' → 'YYYY-MM-DD'."""
    m = re.match(r'^(\d{1,2})/(\d{1,2})/(\d{2})$', date_str.strip())
    if not m:
        return None
    month, day, year = int(m.group(1)), int(m.group(2)), 2000 + int(m.group(3))
    return f"{year:04d}-{month:02d}-{day:02d}"


@dataclass
class LexusTrafficSpot:
    isci: str
    title: str
    rotation_pct: float
    notes: str = ""


@dataclass
class LexusTrafficPeriod:
    duration_sec: int
    date_from_sql: Optional[str]
    date_to_sql: Optional[str]
    date_label: str
    spots: List[LexusTrafficSpot] = field(default_factory=list)


@dataclass
class LexusTrafficInstruction:
    advertiser: str
    campaign: str
    coverage_area: str
    market_code: str
    search_suggestion: str
    periods: List[LexusTrafficPeriod] = field(default_factory=list)


def parse_lexus_traffic_pdf(pdf_bytes: bytes) -> LexusTrafficInstruction:
    """Parse a single IW Group Television Traffic Sheet PDF."""
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        text = "\n".join(page.extract_text() or "" for page in pdf.pages)

    advertiser_m = re.search(r'Advertiser:\s*(.+)', text)
    campaign_m   = re.search(r'Campaign:\s*(.+)', text)
    coverage_m   = re.search(r'Coverage Area:\s*(.+)', text)

    advertiser = advertiser_m.group(1).strip() if advertiser_m else ""
    campaign   = campaign_m.group(1).strip()   if campaign_m   else ""
    coverage   = coverage_m.group(1).strip()   if coverage_m   else ""

    market_code = ""
    for key, code in _COVERAGE_TO_MARKET.items():
        if key in coverage.lower():
            market_code = code
            break

    search_suggestion = f"{advertiser} {market_code}".strip() if market_code else advertiser

    result = LexusTrafficInstruction(
        advertiser=advertiser,
        campaign=campaign,
        coverage_area=coverage,
        market_code=market_code,
        search_suggestion=search_suggestion,
    )

    # Parse table — only the HD section, stop at SD.
    # ISCI codes are uppercase+digit tokens ≥10 chars, followed by NN%.
    # Each row: Language Campaign :dur Title... ISCI_CODE PCT% DATE - DATE [notes]
    lines = text.split("\n")
    in_hd = False
    period_map: dict = {}   # (duration_sec, date_from, date_to) → LexusTrafficPeriod
    period_order: list = []  # insertion order

    for line in lines:
        stripped = line.strip()

        if stripped == "HD":
            in_hd = True
            continue
        if stripped == "SD":
            break   # done — SD section ignored entirely

        if not in_hd or not stripped:
            continue

        # Skip section-header-only lines: "TV Spot", ":30", ":15"
        if stripped in ("TV Spot",) or re.match(r'^:\d+$', stripped):
            continue

        # Anchor on ISCI code: all-caps+digits, ≥10 chars, followed by NN%
        anchor_m = re.search(r'\b([A-Z][A-Z0-9]{9,14})\s+(\d+)%', stripped)
        if not anchor_m:
            continue

        isci         = anchor_m.group(1)
        rotation_pct = float(anchor_m.group(2))

        # Date range follows the anchor
        after = stripped[anchor_m.end():]
        date_m = re.search(r'(\d{1,2}/\d{1,2}/\d{2})\s*-\s*(\d{1,2}/\d{1,2}/\d{2})', after)
        if not date_m:
            continue
        date_from  = _parse_date(date_m.group(1))
        date_to    = _parse_date(date_m.group(2))
        date_label = f"{date_m.group(1)}–{date_m.group(2)}"
        notes      = after[date_m.end():].strip()

        # Prefix: Language Campaign :duration Title
        prefix   = stripped[:anchor_m.start()].strip()
        prefix_m = re.match(r'^(\w+)\s+(\w+)\s+:(\d+)\s+(.+)$', prefix)
        if not prefix_m:
            continue

        duration_sec = int(prefix_m.group(3))
        title        = prefix_m.group(4).strip()

        key = (duration_sec, date_from, date_to)
        if key not in period_map:
            period_map[key] = LexusTrafficPeriod(
                duration_sec=duration_sec,
                date_from_sql=date_from,
                date_to_sql=date_to,
                date_label=date_label,
            )
            period_order.append(key)

        period_map[key].spots.append(
            LexusTrafficSpot(isci=isci, title=title, rotation_pct=rotation_pct, notes=notes)
        )

    # Order: :30 before :15, then chronologically within each duration
    result.periods = sorted(
        [period_map[k] for k in period_order],
        key=lambda p: (-p.duration_sec, p.date_from_sql or ""),
    )
    return result
