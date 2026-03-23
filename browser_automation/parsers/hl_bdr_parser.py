"""
H/L Buy Detail Report (BDR) Parser

Parses H/L Agency "Buy Detail Report" PDFs using OCR.
These PDFs use custom font encoding (pdfplumber gets gibberish) and are
rendered upside-down (180° rotation).  One page = one estimate = one
Etere contract.

OCR dependencies: PyMuPDF (fitz), pytesseract, Pillow
"""

from __future__ import annotations

import io
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Optional

# ── Market / language mappings ────────────────────────────────────────────────

_MARKET_MAP = {
    "San Francisco": "SFO",
    "Sacramento": "CVC",
    "Los Angeles": "LAX",
    "Seattle": "SEA",
    "Houston": "HOU",
    "Washington DC": "WDC",
    "New York": "NYC",
    "Dallas": "DAL",
    "Central Valley": "CVC",
}

_BLOCK_PREFIX = {
    "FILIPINO": "T",
    "TAGALOG": "T",
    "CHINESE": "M/C",
    "MANDARIN": "M",
    "CANTONESE": "C",
    "VIETNAMESE": "V",
    "KOREAN": "K",
    "JAPANESE": "J",
    "HMONG": "Hm",
    "SOUTH ASIAN": "SA",
    "PUNJABI": "SA/P",
}

# ── BDR day-token normalization ───────────────────────────────────────────────
# OCR often caps everything (WThF → WTHF).  Map known tokens to the mixed-case
# form that day_utils.to_etere() expects, then delegate to the tokenizer.

_DAY_NORM = {
    "MTUWTHF":   "MTuWThF",   # Mon–Fri
    "MTUWTF":    "MTuWThF",
    "MTUWTHFS":  "MTuWThFS",
    "TUWTHF":    "TuWThF",    # Tue–Fri
    "WTHF":      "WThF",      # Wed–Fri
    "WTF":       "WThF",
    "WTHFS":     "WThFSa",
    "SASU":      "SaSu",
    "SA":        "Sa",
    "SU":        "Su",
}


def _normalize_bdr_days(raw: str) -> str:
    from browser_automation.day_utils import to_etere
    key = re.sub(r"\s+", "", raw).upper()
    normalized = _DAY_NORM.get(key, raw)
    return to_etere(normalized)


# ── Dataclasses ───────────────────────────────────────────────────────────────

@dataclass
class BDRLine:
    """One schedule row from a Buy Detail Report."""
    days_raw: str           # raw OCR token, e.g. "WTHF"
    days: str               # Etere day string, e.g. "W-F"
    time: str               # e.g. "4:00p-7:00p"
    rate: Decimal           # already gross
    duration: int           # seconds
    weekly_spots: list[int] # one int per week column
    total_spots: int        # sum across all weeks
    language: str           # e.g. "FILIPINO"
    category: str           # e.g. "VARIOUS" or "DRAMA"
    block_prefix: str       # e.g. "T"


@dataclass
class BDROrder:
    """One page / one estimate from a Buy Detail Report PDF."""
    estimate_number: str
    description: str
    client: str
    market: str             # Etere code, e.g. "SFO"
    flight_start: str       # "MM/DD/YYYY" (from EXACT FLIGHT DATES override)
    flight_end: str         # "MM/DD/YYYY"
    separation_minutes: int # customer separation
    buyer: str
    week_dates: list[str]   # "MM/DD/YYYY" for each week column
    lines: list[BDRLine] = field(default_factory=list)


# ── OCR helper ────────────────────────────────────────────────────────────────

def _ocr_page(pdf_path: str, page_num: int = 0) -> str:
    """
    Render one PDF page at 2× zoom with 180° rotation, then OCR it.
    Returns empty string if OCR dependencies are unavailable.
    """
    try:
        import fitz
        import pytesseract
        from PIL import Image
    except ImportError:
        return ""

    if sys.platform == "win32":
        import os
        default = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
        if os.path.exists(default):
            pytesseract.pytesseract.tesseract_cmd = default

    try:
        doc = fitz.open(pdf_path)
        page = doc[page_num]
        mat = fitz.Matrix(2.0, 2.0).prerotate(90)
        pix = page.get_pixmap(matrix=mat, colorspace=fitz.csRGB)
        img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
        doc.close()
        return pytesseract.image_to_string(img, config="--psm 6")
    except Exception:
        return ""


# ── Date helpers ──────────────────────────────────────────────────────────────

def _parse_date(raw: str) -> str:
    """
    Parse a date string (possibly short-year) to MM/DD/YYYY.
    Input examples: "3/30/2026", "4/1/26", "5/3/2026 03:00 AM"
    """
    raw = raw.strip().split()[0]  # drop time portion if any
    raw = re.sub(r"[^0-9/]", "", raw)
    parts = raw.split("/")
    if len(parts) == 3:
        m, d, y = parts
        if len(y) == 2:
            y = "20" + y
        try:
            return f"{int(m):02d}/{int(d):02d}/{y}"
        except ValueError:
            pass
    return raw


def _week_col_to_date(col: str, reference_year: int) -> str:
    """
    Convert a short week-column date like "3/30" or "4/6" to "MM/DD/YYYY"
    using reference_year from the order's flight dates.
    """
    parts = col.strip().split("/")
    if len(parts) == 2:
        m, d = parts
        return f"{int(m):02d}/{int(d):02d}/{reference_year}"
    return col


def _extract_year(flight_start: str) -> int:
    """Extract 4-digit year from 'MM/DD/YYYY'."""
    try:
        return int(flight_start.split("/")[-1])
    except (ValueError, IndexError):
        return 2026


# ── Page parser ───────────────────────────────────────────────────────────────

def _parse_bdr_page(text: str) -> Optional[BDROrder]:
    """
    Parse the OCR text of a single BDR page into a BDROrder.
    Returns None if the page doesn't look like a BDR order page.
    """
    if "Buy Detail Report" not in text:
        return None

    lines = text.splitlines()

    # ── Header fields ──────────────────────────────────────────────────────
    estimate = ""
    description = ""
    client = ""
    market_str = ""
    flight_start = ""
    flight_end = ""
    separation = 30
    buyer = ""

    for line in lines:
        if "Estimate:" in line:
            m = re.search(r"Estimate:\s*(\S+)", line)
            if m:
                estimate = m.group(1).strip().rstrip(",")

        if "Description:" in line:
            # Stop before street address numbers (e.g., "2030 West El Camino")
            m = re.search(r"Description:\s+(.+?)(?=\s+\d{3,}\s+[A-Za-z]|\s{3,}|$)", line)
            if m:
                description = m.group(1).strip().lstrip("—– ").strip()

        if "Market:" in line and not market_str:
            m = re.search(r"Market:\s+(.+?)(?:\s{2,}|$)", line)
            if m:
                market_str = m.group(1).strip()

        if "Flight Start Date:" in line:
            m = re.search(r"Flight Start Date:\s*(\S+)", line)
            if m:
                flight_start = _parse_date(m.group(1))

        if "Flight End Date:" in line:
            m = re.search(r"Flight End Date:\s*(\S+)", line)
            if m:
                flight_end = _parse_date(m.group(1))

        if "Separation between spots:" in line:
            m = re.search(r"Separation between spots:\s*(\d+)", line)
            if m:
                separation = int(m.group(1))

        if "Buyer:" in line:
            m = re.search(r"Buyer:\s+(.+?)(?:\s{2,}|Fax|$)", line)
            if m:
                buyer = m.group(1).strip()

        # Client name — extract from "Client: <name>" field
        if not client and "Client:" in line:
            m = re.search(r"Client:\s+(.+?)(?:\s{2,}|Estimate:|$)", line)
            if m:
                client = m.group(1).strip()

    if not estimate:
        return None

    # ── EXACT FLIGHT DATES override ────────────────────────────────────────
    exact = re.search(r"EXACT FLIGHT DATES:\s*(\S+)\s*-\s*(\S+)", text)
    if exact:
        flight_start = _parse_date(exact.group(1))
        flight_end = _parse_date(exact.group(2))

    market = next(
        (code for name, code in _MARKET_MAP.items() if name in market_str),
        "SFO",
    )
    year = _extract_year(flight_start)

    # ── Week column header ─────────────────────────────────────────────────
    # e.g.  "Dur  3/30  4/6  4/13  4/20  4/27"
    # OCR sometimes drops "/1" from "6/1" → just "6" — recover by subtracting 7 days.
    week_dates: list[str] = []
    for line in lines:
        if re.match(r"\s*Dur\s+[\d/]", line):
            raw_dates = re.findall(r"\b(\d{1,2}/\d{1,2})\b", line)
            # Detect truncated first date: token before first M/D is a bare number
            tokens_after_dur = re.split(r"\s+", line.strip())[1:]  # skip "Dur"
            if (tokens_after_dur
                    and re.match(r"^\d{1,2}$", tokens_after_dur[0])
                    and raw_dates):
                from datetime import datetime as _dt, timedelta as _td
                try:
                    second_wk = _dt.strptime(f"{raw_dates[0]}/{year}", "%m/%d/%Y")
                    first_wk = second_wk - _td(days=7)
                    raw_dates.insert(0, f"{first_wk.month}/{first_wk.day}")
                except Exception:
                    pass
            week_dates = [_week_col_to_date(d, year) for d in raw_dates]
            break

    # ── Data rows ─────────────────────────────────────────────────────────
    # Each data row is two OCR lines:
    #   "{days} {time} ${rate} {dur} {w1} {w2}... {total}"
    #   "{LANGUAGE} {CATEGORY}"
    #
    # Pre-clean: replace isolated o/O (OCR artefact for 0) only when
    # not adjacent to another letter.
    parsed_lines: list[BDRLine] = []

    # Replace standalone 'o'/'O' with '0' (not inside word)
    def _clean_row(s: str) -> str:
        return re.sub(r"(?<![A-Za-z])[oO](?![A-Za-z])", "0", s)

    for i, raw_line in enumerate(lines):
        line = _clean_row(raw_line.strip())

        # Try to match: {days} {time_range} ${rate} {dur} {numbers…}
        m = re.match(
            r"^([A-Za-z]{2,})"                       # day token (≥2 chars)
            r"\s+"
            r"(\d{1,2}:\d{2}[ap]-\s*\d{1,2}:\d{2}[ap])"  # time range
            r"\s+"
            r"\$?([\d,]+\.?\d*)"                     # rate
            r"\s+"
            r"(\d+)"                                  # duration
            r"\s+"
            r"([\d\s]+)$",                            # spot columns + total
            line,
        )
        if not m:
            continue

        days_raw = m.group(1)
        time_raw = m.group(2).replace(" ", "")
        try:
            rate = Decimal(m.group(3).replace(",", ""))
        except Exception:
            continue
        try:
            dur = int(m.group(4))
        except ValueError:
            continue

        spot_tokens = m.group(5).split()
        spot_ints: list[int] = []
        for tok in spot_tokens:
            try:
                spot_ints.append(int(tok))
            except ValueError:
                spot_ints.append(0)

        if len(spot_ints) < 2:
            continue

        total_spots = spot_ints[-1]
        weekly_spots = spot_ints[:-1]

        # Pad / trim weekly_spots to match week_dates count
        n_weeks = len(week_dates)
        if n_weeks:
            if len(weekly_spots) < n_weeks:
                weekly_spots += [0] * (n_weeks - len(weekly_spots))
            elif len(weekly_spots) > n_weeks:
                weekly_spots = weekly_spots[:n_weeks]

        # If sum differs from stated total, log but continue
        computed = sum(weekly_spots)
        if computed != total_spots:
            print(
                f"[BDR PARSE] ⚠ Row '{days_raw} {time_raw}': "
                f"weekly sum={computed} ≠ stated total={total_spots}"
            )

        # Next non-empty ALL-CAPS line is language + category
        language = "FILIPINO"
        category = ""
        for j in range(i + 1, min(i + 4, len(lines))):
            candidate = lines[j].strip()
            if candidate and re.match(r"^[A-Z][A-Z\s]+$", candidate):
                parts = candidate.split()
                if parts:
                    language = parts[0]
                    category = " ".join(parts[1:])
                break

        block_prefix = _BLOCK_PREFIX.get(language.upper(), "T")
        etere_days = _normalize_bdr_days(days_raw)

        parsed_lines.append(
            BDRLine(
                days_raw=days_raw,
                days=etere_days,
                time=time_raw,
                rate=rate,
                duration=dur,
                weekly_spots=weekly_spots,
                total_spots=total_spots,
                language=language,
                category=category,
                block_prefix=block_prefix,
            )
        )

    if not parsed_lines:
        return None

    return BDROrder(
        estimate_number=estimate,
        description=description,
        client=client or "Northern California Dealers Association",
        market=market,
        flight_start=flight_start,
        flight_end=flight_end,
        separation_minutes=separation,
        buyer=buyer,
        week_dates=week_dates,
        lines=parsed_lines,
    )


# ── Public API ────────────────────────────────────────────────────────────────

def parse_bdr_pdf(pdf_path: str) -> list[BDROrder]:
    """
    Parse all pages of an H/L Buy Detail Report PDF.
    Returns one BDROrder per page that contains valid order data.
    """
    try:
        import fitz
        doc = fitz.open(pdf_path)
        page_count = len(doc)
        doc.close()
    except Exception:
        return []

    orders: list[BDROrder] = []
    for page_num in range(page_count):
        text = _ocr_page(pdf_path, page_num)
        if not text:
            continue
        order = _parse_bdr_page(text)
        if order:
            orders.append(order)
            print(
                f"[BDR PARSE] ✓ Page {page_num + 1}: "
                f"Est {order.estimate_number}, "
                f"{order.market}, "
                f"{len(order.lines)} line(s), "
                f"total {sum(l.total_spots for l in order.lines)} spots"
            )

    return orders


def is_bdr_pdf(pdf_path: str) -> bool:
    """
    Quick check: OCR first page and look for 'Buy Detail Report' marker.
    Used by the order detector.
    """
    text = _ocr_page(pdf_path, page_num=0)
    return "Buy Detail Report" in text and "H/L Agency" in text
