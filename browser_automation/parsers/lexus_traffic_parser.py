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


# IW Group printed 'M/D/YY' through August 2026 and 'MM/DD/YYYY' from the September
# LDA sheets on (Lee 9/28: three sheets parsed to zero rows, so the card assigned
# nothing and every spot was assigned by hand). Accept both.
_DATE = r'\d{1,2}/\d{1,2}/(?:\d{4}|\d{2})'
# November 2026 sheets print the flight with an en dash ('11/04/26 – 11/30/26');
# accept hyphen, en dash and em dash.
_DATE_RANGE_RE = re.compile(rf'({_DATE})\s*[-\u2013\u2014]\s*({_DATE})')

# IW Group's November 2026 sheets carry an 'HD' marker inside the row — after the
# file name, after the ISCI, or fused onto the ISCI as a second trailing H
# ('TYIW26TL929HH'). The delivered .mov files are named 'TYIW26TL929H_…', and an
# ISCI is at most 12 characters, so a 13-character '…HH' is the ISCI plus the
# marker. The code we look up (and the code Lee ingests) is the 12-character form.
_HD_TOKEN_RE = re.compile(r'(?<![\w-])HD(?![\w-])')
_ISCI_HD_SUFFIX_RE = re.compile(r'^([A-Z]{4}\d{2}[A-Z]{2}\d{3}H)H$')

# The word in a contract description that names the sheet's language
# ('Lexus SEA Viet Est 207 26Q4', 'Lexus SFO Hinglish Est 208 26Q4').
_LANGUAGE_SEARCH_WORD = {
    "hinglish":   "Hinglish",
    "english":    "Hinglish",
    "vietnamese": "Viet",
    "mandarin":   "Chinese",
    "cantonese":  "Chinese",
    "chinese":    "Chinese",
}


def normalize_isci(isci: str) -> str:
    """'TYIW26TL929HH' (ISCI + HD marker) → 'TYIW26TL929H'; anything else unchanged."""
    m = _ISCI_HD_SUFFIX_RE.match(isci)
    return m.group(1) if m else isci


def _parse_date(date_str: str) -> Optional[str]:
    """'M/D/YY', 'MM/DD/YY' or 'MM/DD/YYYY' → 'YYYY-MM-DD'."""
    m = re.match(r'^(\d{1,2})/(\d{1,2})/(\d{2}|\d{4})$', date_str.strip())
    if not m:
        return None
    month, day, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if year < 100:
        year += 2000
    return f"{year:04d}-{month:02d}-{day:02d}"


@dataclass
class LexusTrafficSpot:
    isci: str
    title: str
    rotation_pct: float
    notes: str = ""
    isci_printed: str = ""   # as the sheet printed it, when it differs from ``isci``
    language: str = ""       # the row's Language column (Hinglish, Vietnamese, Mandarin)
    model: str = ""          # the row's Campaign column (RX, NX, ES, TX)


@dataclass
class LexusTrafficPeriod:
    duration_sec: int
    date_from_sql: Optional[str]
    date_to_sql: Optional[str]
    date_label: str
    spots: List[LexusTrafficSpot] = field(default_factory=list)

    @property
    def rotation_total(self) -> float:
        """Sum of the rows' rotation % — 100 (or 99 for thirds) when the sheet is
        consistent. The NYC Nov 2026 sheet dated its ES rows 12/1–12/31, so November
        summed to 66 and December to 132; the card shows the total so that is visible."""
        return round(sum(s.rotation_pct for s in self.spots), 1)


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
    return parse_lexus_traffic_text(text)


def parse_lexus_traffic_text(text: str) -> LexusTrafficInstruction:
    """Parse the extracted text of a Television Traffic Sheet (testable without a PDF)."""
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
    language = ""

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

        # Drop the stray HD markers (Nov 2026) so the ISCI sits next to its %.
        stripped = re.sub(r'\s+', ' ', _HD_TOKEN_RE.sub(' ', stripped)).strip()

        # Anchor on ISCI code: all-caps+digits, ≥10 chars, followed by NN%
        anchor_m = re.search(r'\b([A-Z][A-Z0-9]{9,14})\s+(\d+)%', stripped)
        if not anchor_m:
            continue

        isci_printed = anchor_m.group(1)
        isci         = normalize_isci(isci_printed)
        rotation_pct = float(anchor_m.group(2))

        # Date range follows the anchor
        after = stripped[anchor_m.end():]
        date_m = _DATE_RANGE_RE.search(after)
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
        row_language = prefix_m.group(1)
        row_model    = prefix_m.group(2)
        if not language:
            language = row_language
            word = _LANGUAGE_SEARCH_WORD.get(language.lower())
            if word and market_code:
                result.search_suggestion = f"{advertiser} {market_code} {word}".strip()

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
            LexusTrafficSpot(
                isci=isci, title=title, rotation_pct=rotation_pct, notes=notes,
                isci_printed=isci_printed if isci_printed != isci else "",
                language=row_language, model=row_model,
            )
        )

    # Order: :30 before :15, then chronologically within each duration
    result.periods = sorted(
        [period_map[k] for k in period_order],
        key=lambda p: (-p.duration_sec, p.date_from_sql or ""),
    )
    return result
