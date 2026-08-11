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


# Real ISCI codes always contain at least one digit (e.g. TYRN39271H). This
# excludes header keywords like CAMPAIGN, ESTIMATE, TRAFFIC. `\s*(.*)` — NOT
# `\s+(.*)` — because a long title can push the ISCI onto a line of its own with
# nothing after it, and such a line must still start a block. When it didn't,
# TYRN43031H (Mandarin) was swallowed as body text of the ISCI above it and
# vanished from the parse entirely.
_ISCI_RE = re.compile(r'^([A-Z][A-Z0-9]*\d[A-Z0-9]{2,})\s*(.*)')

# End-of-table / page markers. These close the current block so the last ISCI on
# a page never absorbs the *next* page's header lines (which carry their own
# "EXACT FLIGHT DATES" and would be mistaken for the spot's flight). "Link to
# spots:" and "Link to new spots" are both in the wild.
_BLOCK_END_RE = re.compile(r'^(Link to (?:new )?spots|Page\s+\d+\s+of)\b',
                           re.IGNORECASE)

_DATE_RE = re.compile(r'\d{1,2}/\d{1,2}/\d{2,4}')
_PARENS_ONLY_RE = re.compile(r'\((?:[A-Za-z][A-Za-z\s]*)\)')

# Dialects HL actually writes. A parenthesised word is only accepted as the
# dialect when it is one of these, so a title like "(Non Offer)" can never be
# read as a language — the "detect by content, not by position" rule.
_KNOWN_DIALECTS = {
    "cantonese", "mandarin", "chinese", "hindi", "punjabi", "south asian",
    "tagalog", "pilipino", "filipino", "vietnamese", "korean", "japanese",
    "hmong", "english", "spanish",
}


def _date_pair(s: str) -> Optional[tuple]:
    """First two M/D/YY dates in `s`, or None.

    First (not last) so a trailing "@ 12 NOON" / "@ 1201p" annotation cannot
    shift the window.
    """
    found = _DATE_RE.findall(s or "")
    return (found[0], found[1]) if len(found) >= 2 else None


def _resolve_dates(block: dict, lines: List[str], claimed: set) -> Optional[tuple]:
    """This spot's own flight window.

    HL prints these rows two ways, and the metadata can land on EITHER side of
    the ISCI:

      A  `TYRN41271H  <title> :30 ACM TV (Cantonese) :30 100% 8/4/26 8/11/26`
         — or the ":30 100% dates" on the following line.

      B  `<long title> :30 ACM TV :30 100% 8/12/26 8/31/26 @ 12P`
         `TYRN43021H (Cantonese)`
         — the row WRAPS and its dates print on the line ABOVE the ISCI.

    So look on the ISCI's own line, then the line above, then the lines below.
    Each metadata line is `claimed` by the first ISCI that takes it, which is
    what stops layout B from reading the NEXT row's dates: every ISCI resolves
    from the line above it, so the line below stays free for the next one.
    Without that, a PDF whose flights differ per dialect would silently give
    every spot its neighbour's window.
    """
    own = _date_pair(block['rest'])
    if own:
        return own

    above = block['idx'] - 1
    if above >= 0 and above not in claimed:
        text = lines[above].strip()
        if not _ISCI_RE.match(text):
            pair = _date_pair(text)
            if pair:
                claimed.add(above)
                return pair

    for i, text in block['after']:
        if i in claimed:
            continue
        pair = _date_pair(text)
        if pair:
            claimed.add(i)
            return pair
    return None


def _resolve_dialect(block: dict, lines: List[str]) -> str:
    """The spot's dialect — searched FORWARD from the ISCI, never backward.

    Scanning the block in reverse for the *last* "(Word)" is what mislabelled
    TYRN43021H: its block had swollen to include the following row's
    "(Mandarin)", so the Cantonese cut was tagged Mandarin and would have aired
    in Mandarin programming. Forward order, plus a known-dialect check, keeps a
    spot's language tied to its own ISCI.
    """
    candidates = [block['rest']] + [t for _i, t in block['after']]
    fallback = ""
    for text in candidates:
        for m in re.finditer(r'\(([A-Za-z][A-Za-z\s]+)\)', text):
            word = m.group(1).strip()
            if word.lower() in _KNOWN_DIALECTS:
                return word
            fallback = fallback or word
    return fallback


def _title_from_meta(block: dict, lines: List[str]) -> str:
    """Title for the wrapped layout, where it prints above the ISCI."""
    above = block['idx'] - 1
    if above < 0:
        return ""
    text = lines[above].strip()
    if _ISCI_RE.match(text) or not _date_pair(text):
        return ""
    cut = re.search(r'\s+:\d+\b', text)
    return (text[:cut.start()] if cut else text).strip()


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

    # Group lines into blocks: each block starts at an ISCI line and runs to the
    # next ISCI / end-of-table marker. `idx` is the ISCI line's index in `lines`,
    # because in one HL layout a row's metadata sits on the line ABOVE its ISCI
    # (see _resolve_dates).
    blocks: List[dict] = []
    current: Optional[dict] = None
    for i, line in enumerate(lines):
        stripped = line.strip()
        m = _ISCI_RE.match(stripped)
        if m:
            current = {'idx': i, 'isci': m.group(1), 'rest': m.group(2).strip(),
                       'after': []}
            blocks.append(current)
        elif _BLOCK_END_RE.match(stripped):
            current = None
        elif current is not None and stripped:
            current['after'].append((i, stripped))

    spots: List[HLTrafficSpot] = []
    first_from_sql = first_to_sql = first_start = first_end = ""
    first_dur = 30
    claimed: set = set()   # metadata lines already used by an earlier ISCI

    for block in blocks:
        isci       = block['isci']
        line1_rest = block['rest']
        after      = block['after']
        rest_text  = " ".join(t for _i, t in after)
        # Text belonging to THIS spot for scalar fields. Both HL layouts covered:
        #   • single-line — everything (dur, rotation, dialect, dates) on line 1
        #   • multi-line  — ISCI/title on line 1, ":30 100% dates" on line 2,
        #                   "(Dialect)" on line 3
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

        pair = _resolve_dates(block, lines, claimed)
        spot_from_sql = spot_to_sql = spot_start = spot_end = ""
        if pair:
            spot_start, spot_end = pair
            spot_from_sql = _date_to_sql(spot_start) or ""
            spot_to_sql   = _date_to_sql(spot_end)   or ""

        dialect_raw = _resolve_dialect(block, lines)
        system_dialect = _HL_DIALECT_MAP.get(dialect_raw, dialect_raw)

        # Title: line-1 text up to the first " :NN" duration marker. Everything
        # after it (duration, "ACM TV", "(Dialect)", rotation, dates) is metadata.
        # In the wrapped layout line 1 is just "(Dialect)", so the printed title
        # lives on the metadata line above — recover it from there.
        title = line1_rest
        cut = re.search(r'\s+:\d+\b', title)
        if cut:
            title = title[:cut.start()]
        title = title.strip()
        if not title or _PARENS_ONLY_RE.fullmatch(title):
            title = _title_from_meta(block, lines)

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
