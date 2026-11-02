"""
Parse Tatari "Traffic Instructions" PDFs (WorldLink/Tatari format).

One PDF per advertiser/week. Contains ISCI codes with rotation split %
and a flight date range. Times and dayparts are ignored.

A PDF may run to several pages and EACH PAGE is its own flight: the page's
Ad Name / ISCI / Duration / Split table is the rotation for the page's
Description / Buy Type / Flight row (Lectric eBikes week of 11/2/26: page 1 =
11/04-11/08 Wed-Sun, page 2 = 11/02-11/03 Mon-Tue, different weights, one ISCI
on both pages). The flight dates therefore belong on the SPOT, never on the
instruction; ``periods`` groups the spots by flight window in page order and
the instruction-level dates are the overall min/max, for contract search only.
"""

import datetime
import io
import re
from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Tuple

import pdfplumber


@dataclass
class TatariSpot:
    isci: str
    title: str
    duration_sec: int  # 15 or 30
    rotation_pct: float
    date_from_sql: Optional[str] = None  # this page's own Flight window
    date_to_sql: Optional[str] = None
    date_from_display: str = ""
    date_to_display: str = ""


@dataclass
class TatariPeriod:
    """One flight window: every spot whose page carries the same Flight dates."""

    date_from_sql: Optional[str]
    date_to_sql: Optional[str]
    date_from_display: str
    date_to_display: str
    spots: List[TatariSpot] = field(default_factory=list)


# Advertiser names in Tatari PDFs that don't match Etere contract descriptions verbatim.
# Maps the PDF advertiser string (case-insensitive prefix match) → better search term.
_SEARCH_OVERRIDES: dict = {
    "bettersleep": "Better",
    "marsmen": "Mars",
    "lectricebikes": "Lectric",
    "sundaysfordogs": "Sundays",
    "woofpet": "Woof",
    "pholicious": "Pholicious",
}


def _search_suggestion(advertiser: str) -> str:
    key = advertiser.lower().replace(" ", "")
    for prefix, suggestion in _SEARCH_OVERRIDES.items():
        if key.startswith(prefix):
            return suggestion
    # Default: first word is specific enough to find the contract
    return advertiser.split()[0] if advertiser else advertiser


@dataclass
class TatariTrafficInstruction:
    advertiser: str
    search_suggestion: str  # pre-filled search term for the contract finder
    date_from_sql: Optional[str]  # "2026-05-18" (earliest flight start in the file)
    date_to_sql: Optional[str]  # "2026-05-24" (latest flight end in the file)
    date_from_display: str  # "5/18"
    date_to_display: str  # "5/24"
    spots: List[TatariSpot] = field(default_factory=list)
    periods: List[TatariPeriod] = field(default_factory=list)


def _parse_date(part: str, year: int) -> Optional[str]:
    m = re.match(r"^(\d{1,2})/(\d{1,2})$", part.strip())
    if not m:
        return None
    month, day = int(m.group(1)), int(m.group(2))
    return f"{year:04d}-{month:02d}-{day:02d}"


# One page as the parser sees it: its text and its extracted tables.
PageData = Tuple[str, List[list]]


def _page_spots(tables: List[list]) -> List[TatariSpot]:
    """Rows of the page's Ad Name / ISCI / Duration / Split table (dates unset)."""
    spots: List[TatariSpot] = []
    for table in tables:
        if not table or not table[0]:
            continue
        header = [str(c or "").strip() for c in table[0]]
        if "ISCI" not in header or "Duration" not in header:
            continue
        isci_col = header.index("ISCI")
        dur_col = header.index("Duration")
        split_col = next((i for i, h in enumerate(header) if "Split" in h), -1)
        name_col = next((i for i, h in enumerate(header) if "Ad Name" in h), -1)

        for row in table[1:]:
            if not row or len(row) <= max(isci_col, dur_col):
                continue
            isci = str(row[isci_col] or "").strip()
            if not isci:
                continue
            dur_str = str(row[dur_col] or "30s").strip().lower()
            dm = re.search(r"\d+", dur_str)
            dur_sec = int(dm.group()) if dm else 30
            pct_raw = str(row[split_col] or "0").strip() if split_col >= 0 else "0"
            pm = re.search(r"[\d.]+", pct_raw)
            pct = float(pm.group()) if pm else 0.0
            title = str(row[name_col] or isci).strip() if name_col >= 0 and row[name_col] else isci
            spots.append(TatariSpot(isci=isci, title=title, duration_sec=dur_sec, rotation_pct=pct))
    return spots


def _page_flight(tables: List[list], year: int) -> Optional[Tuple[str, str, str, str]]:
    """The page's Flight window from its Description / Buy Type / Flight table.

    Several rows on one page (one per daypart) share the page's rotation, so the
    window is the earliest start and the latest end across them. Returns
    ``(from_sql, to_sql, from_display, to_display)`` or None when the page has
    no Flight row.
    """
    windows: List[Tuple[str, str, str, str]] = []
    for table in tables:
        if not table or not table[0]:
            continue
        header = [str(c or "").strip() for c in table[0]]
        if "Flight" not in header:
            continue
        flight_col = header.index("Flight")
        for row in table[1:]:
            if not row or len(row) <= flight_col:
                continue
            flight_str = str(row[flight_col] or "").strip()
            m = re.search(r"(\d{1,2}/\d{1,2})\s*[-–]\s*(\d{1,2}/\d{1,2})", flight_str)
            if not m:
                continue
            from_disp, to_disp = m.group(1), m.group(2)
            from_sql = _parse_date(from_disp, year)
            to_sql = _parse_date(to_disp, year)
            if not from_sql or not to_sql:
                continue
            if to_sql < from_sql:  # a week that crosses New Year (12/29 - 01/04)
                to_sql = _parse_date(to_disp, year + 1)
            windows.append((from_sql, to_sql, from_disp, to_disp))
    if not windows:
        return None
    first = min(windows, key=lambda w: w[0])
    last = max(windows, key=lambda w: w[1])
    return (first[0], last[1], first[2], last[3])


def _group_periods(spots: List[TatariSpot]) -> List[TatariPeriod]:
    """Group spots by their own flight window, preserving page order."""
    periods: List[TatariPeriod] = []
    by_key: dict = {}
    for sp in spots:
        key = (sp.date_from_sql, sp.date_to_sql)
        per = by_key.get(key)
        if per is None:
            per = TatariPeriod(
                date_from_sql=sp.date_from_sql,
                date_to_sql=sp.date_to_sql,
                date_from_display=sp.date_from_display,
                date_to_display=sp.date_to_display,
            )
            by_key[key] = per
            periods.append(per)
        per.spots.append(sp)
    return periods


def build_instruction(pages: Sequence[PageData], year: int) -> TatariTrafficInstruction:
    """Assemble the instruction from every page's (text, tables).

    Each page that carries an ISCI table is one flight: its spots take the page's
    Flight window. A page with ISCI rows but no readable Flight row refuses (the
    window decides WHERE the creative runs; it is never defaulted), as does a
    Flight row on a page with no ISCI rows. A page with neither is skipped.
    """
    advertiser = ""
    spots: List[TatariSpot] = []
    seen: set = set()
    for page_no, (text, tables) in enumerate(pages, start=1):
        if not advertiser:
            # "Station: Crossings TV  Advertiser: Sundays for Dogs"
            for line in (text or "").split("\n"):
                m = re.search(r"Advertiser:\s*(.+?)$", line)
                if m:
                    advertiser = m.group(1).strip()
                    break

        page_spots = _page_spots(tables)
        flight = _page_flight(tables, year)
        if page_spots and flight is None:
            raise ValueError(
                f"page {page_no} lists {len(page_spots)} ISCI row(s) but no Flight "
                f"dates; cannot tell which days the rotation covers"
            )
        if flight is not None and not page_spots:
            raise ValueError(
                f"page {page_no} carries Flight {flight[2]} - {flight[3]} but no ISCI "
                f"rows; nothing to rotate for that window"
            )
        if flight is None:
            continue
        from_sql, to_sql, from_disp, to_disp = flight
        for sp in page_spots:
            key = (sp.isci, from_sql, to_sql)
            if key in seen:  # the same ISCI twice on one page / two pages, one flight
                continue
            seen.add(key)
            sp.date_from_sql = from_sql
            sp.date_to_sql = to_sql
            sp.date_from_display = from_disp
            sp.date_to_display = to_disp
            spots.append(sp)

    periods = _group_periods(spots)
    first = min(periods, key=lambda p: p.date_from_sql) if periods else None
    last = max(periods, key=lambda p: p.date_to_sql) if periods else None

    return TatariTrafficInstruction(
        advertiser=advertiser,
        search_suggestion=_search_suggestion(advertiser),
        date_from_sql=first.date_from_sql if first else None,
        date_to_sql=last.date_to_sql if last else None,
        date_from_display=first.date_from_display if first else "",
        date_to_display=last.date_to_display if last else "",
        spots=spots,
        periods=periods,
    )


def parse_tatari_traffic_pdf(
    pdf_bytes: bytes, year: Optional[int] = None
) -> TatariTrafficInstruction:
    """Parse a Tatari traffic instructions PDF, EVERY page, via pdfplumber tables.

    ``year`` is the flight year (the PDF prints month/day only); defaults to the
    current year.
    """
    if year is None:
        year = datetime.date.today().year
    pages: List[PageData] = []
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            pages.append((page.extract_text() or "", page.extract_tables()))
    return build_instruction(pages, year)
