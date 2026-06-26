"""
Parse H&L Partners traffic instruction PDFs.

One PDF covers multiple dialects (Cantonese, Mandarin, Hindi/SouthAsian, Tagalog/Filipino, etc.)
each with their own ISCI code. Returns the estimate number, advertiser, all ISCI codes
with their dialects (raw and system-normalised), and flight dates.

HL-specific dialect normalisations:
  "Hindi"   → "SouthAsian"  (covers Hindi + Punjabi time windows)
  "Tagalog" → "Filipino"
"""
import io
import re
from dataclasses import dataclass, field
from typing import List, Optional

import pdfplumber

# Raw dialect name as written by HL → system dialect used in _CTV_LANG_WINDOWS
_HL_DIALECT_MAP = {
    "Hindi":     "SouthAsian",
    "Tagalog":   "Filipino",
    "Pilipino":  "Filipino",
}


def _date_to_sql(s: str) -> Optional[str]:
    """Convert 'M/D/YY' or 'M/D/YYYY' → 'YYYY-MM-DD' for SQL WHERE clauses."""
    m = re.match(r'^(\d{1,2})/(\d{1,2})/(\d{2,4})$', s.strip())
    if not m:
        return None
    month, day, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if year < 100:
        year += 2000
    return f"{year:04d}-{month:02d}-{day:02d}"


@dataclass
class HLTrafficSpot:
    isci: str
    title: str
    duration_sec: int
    dialect: str         # raw from PDF: "Cantonese", "Hindi", "Tagalog", etc.
    system_dialect: str  # normalised: "Cantonese", "SouthAsian", "Filipino", etc.
    rotation_pct: float
    # Per-spot flight dates. One PDF can carry several flights (e.g. 6/2–6/8,
    # 6/9–6/30, 6/30–7/6), each with its own ISCI per dialect. The spot must be
    # assigned only to scheduled spots inside *its* window, so dates are tracked
    # per spot — not once at the instruction level.
    date_from_sql: Optional[str] = None  # "YYYY-MM-DD" for SQL
    date_to_sql: Optional[str] = None
    start_date: str = ""                 # display, e.g. "6/2/26"
    end_date: str = ""


@dataclass
class HLTrafficInstruction:
    advertiser: str
    estimate: str
    duration_sec: int
    date_from_sql: Optional[str]
    date_to_sql: Optional[str]
    start_date: str   # display format, e.g. "6/2/26"
    end_date: str     # display format, e.g. "6/21/26"
    spots: List[HLTrafficSpot] = field(default_factory=list)


def parse_hl_traffic_pdf(pdf_bytes: bytes) -> HLTrafficInstruction:
    """Parse a single H&L Partners traffic instruction PDF."""
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        text = "\n".join(page.extract_text() or "" for page in pdf.pages)

    # --- Header fields ---
    advertiser = ""
    adv_m = re.search(r'ADVERTISER:\s*(.+?)(?:\s{2,}|\s+DATE:)', text)
    if adv_m:
        advertiser = adv_m.group(1).strip()

    estimate = ""
    est_m = re.search(r'ESTIMATE NUMBER:\s*(\d+)', text, re.IGNORECASE)
    if est_m:
        estimate = est_m.group(1)

    # Header flight dates: "6/2/26 – 7/6/26 @ 12 NOON"
    # We use these as fallback; per-ISCI dates (if present) take priority.
    hdr_from_sql = hdr_to_sql = hdr_start = hdr_end = ""
    hdr_m = re.search(
        r'EXACT FLIGHT DATES:\s*(\d{1,2}/\d{1,2}/\d{2,4})\s*[–\-]\s*(\d{1,2}/\d{1,2}/\d{2,4})',
        text, re.IGNORECASE,
    )
    if hdr_m:
        hdr_start    = hdr_m.group(1)
        hdr_end      = hdr_m.group(2)
        hdr_from_sql = _date_to_sql(hdr_start) or ""
        hdr_to_sql   = _date_to_sql(hdr_end)   or ""

    # --- ISCI blocks ---
    # Each block starts with an ISCI code (≥6 uppercase alphanumeric chars) at
    # the beginning of a line, followed by the title on the same line.
    # The `:duration  rotation%  start_date  end_date` data appears on the next
    # line (occasionally partially on the first line).
    # The dialect appears in parentheses: "(Cantonese)" on its own line.
    #
    # Example:
    #   TYRN39271H 2026 Hybrid Selection - Non Offer Spring Update :30 ACM TV
    #   :30 100% 6/2/26 6/21/26
    #   (Cantonese)

    lines = text.split("\n")
    # Real ISCI codes always contain at least one digit (e.g. TYRN39271H).
    # This excludes header keywords like CAMPAIGN, ESTIMATE, TRAFFIC, etc.
    _ISCI_RE = re.compile(r'^([A-Z][A-Z0-9]*\d[A-Z0-9]{2,})\s+(.*)')
    # End-of-table / page markers. These close the current block so the last
    # ISCI on a page never absorbs the *next* page's header lines (which carry
    # their own dates — "EXACT FLIGHT DATES: 6/2/26 – 7/6/26" — and would
    # otherwise be mistaken for the spot's flight dates on a multi-page PDF).
    _BLOCK_END_RE = re.compile(r'^(Link to new spots|Page\s+\d+\s+of)\b', re.IGNORECASE)

    # Group lines into blocks: each block starts at an ISCI line.
    blocks: List[List[str]] = []
    current: Optional[List[str]] = None
    for line in lines:
        stripped = line.strip()
        m = _ISCI_RE.match(stripped)
        if m:
            current = [stripped]
            blocks.append(current)
        elif _BLOCK_END_RE.match(stripped):
            current = None
        elif current is not None:
            if stripped:
                current.append(stripped)

    spots: List[HLTrafficSpot] = []
    first_from_sql = first_to_sql = first_start = first_end = ""
    first_dur = 30

    for block in blocks:
        if not block:
            continue
        isci_m = _ISCI_RE.match(block[0])
        if not isci_m:
            continue

        isci       = isci_m.group(1)
        line1_rest = isci_m.group(2).strip()
        rest_text  = " ".join(block[1:])  # lines 2+
        # Whole block after the ISCI code. Both HL layouts are covered:
        #   • single-line  — everything (dur, rotation, dialect, dates) on line 1
        #   • multi-line   — ISCI/title on line 1, ":30 100% dates" on line 2,
        #                    "(Dialect)" on line 3
        full_text = (line1_rest + " " + rest_text).strip()

        # Duration: look for ":NN" — prefer on line 2+ (avoid ":30" in title)
        dur = 30
        dur_m = re.search(r':(\d+)', rest_text)
        if dur_m:
            dur = int(dur_m.group(1))
        else:
            # Fall back to last :NN in line 1 title
            for dm in re.finditer(r':(\d+)', line1_rest):
                dur = int(dm.group(1))

        # Rotation: "NN%" anywhere in the block
        rot = 100.0
        rot_m = re.search(r'(\d+(?:\.\d+)?)%', full_text)
        if rot_m:
            rot = float(rot_m.group(1))

        # Dates: M/D/YY pairs — take the FIRST two slash-dates after the ISCI.
        # First (not last) so a trailing "@ 12 NOON"/"@ 1201p" annotation or any
        # stray date can't shift the window. Block-end markers already prevent
        # cross-page bleed, so the first pair is always this spot's own flight.
        date_matches = re.findall(r'\d{1,2}/\d{1,2}/\d{2,4}', full_text)
        spot_from_sql = spot_to_sql = spot_start = spot_end = ""
        if len(date_matches) >= 2:
            spot_start    = date_matches[0]
            spot_end      = date_matches[1]
            spot_from_sql = _date_to_sql(spot_start) or ""
            spot_to_sql   = _date_to_sql(spot_end)   or ""

        # Dialect: last "(Word)" in entire block
        dialect_raw = ""
        for b_line in reversed(block):
            dial_m = re.search(r'\(([A-Za-z][A-Za-z\s]+)\)', b_line)
            if dial_m:
                dialect_raw = dial_m.group(1).strip()
                break

        system_dialect = _HL_DIALECT_MAP.get(dialect_raw, dialect_raw)

        # Title: line-1 text up to the first " :NN" duration marker. Everything
        # after it (duration, "ACM TV", "(Dialect)", rotation, dates) is metadata.
        title = line1_rest
        cut = re.search(r'\s+:\d+\b', title)
        if cut:
            title = title[:cut.start()]
        title = title.strip()

        spots.append(HLTrafficSpot(
            isci=isci,
            title=title,
            duration_sec=dur,
            dialect=dialect_raw,
            system_dialect=system_dialect,
            rotation_pct=rot,
            date_from_sql=spot_from_sql or None,
            date_to_sql=spot_to_sql or None,
            start_date=spot_start,
            end_date=spot_end,
        ))

        if not first_from_sql and spot_from_sql:
            first_from_sql = spot_from_sql
            first_to_sql   = spot_to_sql
            first_start    = spot_start
            first_end      = spot_end
            first_dur      = dur

    # Instruction-level dates are for DISPLAY only (per-spot dates drive the
    # actual assignment). Prefer the header EXACT FLIGHT DATES — the full flight
    # across every table — else fall back to the first spot's window.
    from_sql   = hdr_from_sql or first_from_sql
    to_sql     = hdr_to_sql   or first_to_sql
    start_disp = hdr_start    or first_start
    end_disp   = hdr_end      or first_end

    return HLTrafficInstruction(
        advertiser=advertiser,
        estimate=estimate,
        duration_sec=first_dur,
        date_from_sql=from_sql or None,
        date_to_sql=to_sql or None,
        start_date=start_disp,
        end_date=end_disp,
        spots=spots,
    )
