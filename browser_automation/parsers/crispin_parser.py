"""
Crispin LLC media-proposal parser (advertiser: Bay Area AQMD).

Crispin is an AGENCY parser: the agency is fixed (Crispin LLC → ANAGRAF agency
446) and the advertiser is looked up in ANAGRAF. Bay Area AQMD exists TWICE in
Etere (183 = Allison & Partners / AGENZIA 187, 448 = Crispin / AGENZIA 446); the
correct customer is disambiguated by the agency link (see crispin_automation).

Source layout — a single-market "Crossings TV Media Proposal" workbook:
  - header block (Agency / Advertiser / Contact / …) as label→value cell pairs
  - a market banner row ("San Francisco Bay Area - Xfinity Channel 3131 …")
  - a column-header row: Language | Daypart | Unit Value | Discounted Rate |
    Length | <week-date columns…> | Total Spots | Total Value | Proposed Amount
  - airtime rows, then Total Paid / Total Bonuses / Total footer rows

Rate rule (Lee): use the **Discounted Rate** column, never Unit Value. A
discounted rate of 0 marks a bonus line (the :15s ROS added-value spots).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import List, Optional

import openpyxl

# ─── Market detection ────────────────────────────────────────────────────────
_MARKET_MAP = [
    (("san francisco", "bay area", "sfo"), "SFO"),
    (("sacramento", "central valley", "cvc"), "CVC"),
    (("seattle", "sea"), "SEA"),
    (("los angeles", "lax"), "LAX"),
    (("houston", "hou"), "HOU"),
    (("chicago", "minneapolis", "cmp"), "CMP"),
    (("washington", "wdc"), "WDC"),
    (("new york", "nyc"), "NYC"),
]

# Base languages used to normalise a line's language block for ROS mapping.
_BASE_LANGUAGES = ["Cantonese", "Mandarin", "Filipino", "Vietnamese",
                   "Korean", "Hmong", "Punjabi", "Japanese", "Hindi",
                   "South Asian", "Chinese"]

# Time range at the END of a daypart string, e.g. "M-F 7p-8p" → "7p-8p".
_TIME_RE = re.compile(
    r"(\d{1,2}(?::\d{2})?\s*[apAP]?\s*[-–]\s*\d{1,2}(?::\d{2})?\s*[apAP])"
)


def split_daypart(daypart: str) -> tuple[str, str]:
    """'M-F 7p-8p' → ('M-F', '7p-8p'). Returns (days, '') if no time found."""
    dp = (daypart or "").strip()
    m = _TIME_RE.search(dp)
    if not m:
        return dp, ""
    return dp[: m.start()].strip(), m.group(1).strip()


def _num(v) -> float:
    """Coerce a money/count cell to float. '$120.00', '42.5', '-', None, '' → float."""
    if v is None:
        return 0.0
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).replace("$", "").replace(",", "").strip()
    if s in ("", "-", "–"):
        return 0.0
    try:
        return float(s)
    except ValueError:
        return 0.0


def _to_date(v) -> Optional[date]:
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    return None


# ─── Data model ──────────────────────────────────────────────────────────────

@dataclass
class CrispinLine:
    language_block: str          # raw, e.g. "Cantonese News" or "Cantonese"
    daypart: str                 # raw, e.g. "M-F 7p-8p" or "ROS"
    unit_value: float            # standard rate card (informational)
    rate: float                  # DISCOUNTED rate — the billed rate (0 ⇒ bonus)
    length_sec: int              # 30 / 15 from the Length column
    week_dates: List[date]       # Monday of each flight week
    week_spots: List[int]        # spots per week (parallel to week_dates)

    @property
    def is_bonus(self) -> bool:
        return round(self.rate, 4) == 0.0

    @property
    def total_spots(self) -> int:
        return sum(self.week_spots)

    # Aliases so the generic parser_bridge normalizer (web preview) picks these up.
    @property
    def weekly_spots(self) -> List[int]:
        return self.week_spots

    @property
    def length(self) -> int:
        return self.length_sec

    @property
    def base_language(self) -> str:
        """Normalised language for ROS mapping ('Cantonese News' → 'Cantonese')."""
        low = self.language_block.strip().lower()
        for lang in _BASE_LANGUAGES:
            if low.startswith(lang.lower()):
                return lang
        return self.language_block.strip()


@dataclass
class CrispinOrder:
    agency: str
    advertiser: str
    contact: str
    email: str
    market_code: str
    market_label: str
    order_date: Optional[date]
    lines: List[CrispinLine] = field(default_factory=list)
    rates_are_net: bool = False   # no agency commission → billed rate, no gross-up
    source_path: str = ""

    @property
    def paid_lines(self) -> List[CrispinLine]:
        return [ln for ln in self.lines if not ln.is_bonus]

    @property
    def bonus_lines(self) -> List[CrispinLine]:
        return [ln for ln in self.lines if ln.is_bonus]

    @property
    def week_dates(self) -> List[date]:
        return self.lines[0].week_dates if self.lines else []

    @property
    def flight_start(self) -> Optional[str]:
        wd = self.week_dates
        return wd[0].strftime("%m/%d/%Y") if wd else None

    @property
    def flight_end(self) -> Optional[str]:
        """Sunday of the last flight week."""
        wd = self.week_dates
        return (wd[-1] + timedelta(days=6)).strftime("%m/%d/%Y") if wd else None


# ─── Parser ──────────────────────────────────────────────────────────────────

_HEADER_LABELS = {"agency", "advertiser", "contact", "email", "station",
                  "languages", "payment terms", "revision"}


def _pick_sheet(wb):
    """Return the worksheet holding the airtime grid (Language + Daypart header)."""
    for ws in wb.worksheets:
        for row in ws.iter_rows(values_only=True):
            cells = [str(c).strip().lower() for c in row if c is not None]
            if "language" in cells and "daypart" in cells:
                return ws
    return wb.active


def parse_crispin_xlsx(path: str) -> CrispinOrder:
    wb = openpyxl.load_workbook(path, data_only=True)
    ws = _pick_sheet(wb)
    rows = list(ws.iter_rows(values_only=True))

    # ── Header label→value pairs (label cell, value = next non-empty cell) ──
    hdr: dict[str, str] = {}
    for r in rows:
        for i, c in enumerate(r):
            if c is None:
                continue
            label = str(c).strip().lower()
            # 'Revision ( revised the start date)' → key on the leading word
            key = next((k for k in _HEADER_LABELS if label.startswith(k)), None)
            if key and key not in hdr:
                for c2 in r[i + 1:]:
                    if c2 is not None and str(c2).strip():
                        hdr[key] = str(c2).strip() if not isinstance(c2, (datetime, date)) else c2
                        break

    agency = str(hdr.get("agency", "")).strip()
    advertiser = str(hdr.get("advertiser", "")).strip()
    contact = str(hdr.get("contact", "")).strip()
    email = str(hdr.get("email", "")).strip()
    order_date = _to_date(hdr.get("revision"))

    # ── Market banner + column-header row ──
    market_label = ""
    market_locked = False
    header_ri = None
    col: dict[str, int] = {}
    week_cols: List[int] = []
    week_dates: List[date] = []

    for ri, r in enumerate(rows):
        cells_l = [str(c).strip().lower() if c is not None else "" for c in r]
        if header_ri is None and "language" in cells_l and "daypart" in cells_l:
            header_ri = ri
            for ci, cl in enumerate(cells_l):
                if cl == "language":
                    col["lang"] = ci
                elif cl == "daypart":
                    col["daypart"] = ci
                elif cl.startswith("unit value"):
                    col["unit"] = ci
                elif cl.startswith("discounted"):
                    col["disc"] = ci
                elif cl == "length":
                    col["length"] = ci
                elif cl.startswith("total spots"):
                    col["total_spots"] = ci
            # week columns = header cells that are real dates
            for ci, cval in enumerate(r):
                d = _to_date(cval)
                if d is not None:
                    week_cols.append(ci)
                    week_dates.append(d)
            break
        # Market banner (before the grid): a row naming a market. The real
        # banner ("San Francisco Bay Area - Xfinity Channel 3131 / KQTA 15.3")
        # carries a channel marker — prefer and lock onto it, since the
        # Advertiser header row can also contain a city name.
        if not market_locked:
            joined = " ".join(cells_l)
            is_banner = any(k in joined for k in ("xfinity", "kqta", "channel"))
            for keys, code in _MARKET_MAP:
                if any(k in joined for k in keys):
                    market_label = " ".join(str(c).strip() for c in r if c is not None)
                    market_locked = is_banner
                    break

    if header_ri is None:
        raise ValueError("Crispin parser: could not find the 'Language/Daypart' column header row")
    if not week_cols:
        raise ValueError("Crispin parser: no weekly date columns found in the header row")
    for req in ("lang", "daypart", "disc", "length"):
        if req not in col:
            raise ValueError(f"Crispin parser: missing '{req}' column in header row")

    market_code = "SFO"
    for keys, code in _MARKET_MAP:
        if any(k in market_label.lower() for k in keys):
            market_code = code
            break

    # ── Airtime rows ──
    # The airtime block is followed by a Total Paid / Total Bonuses / Total
    # footer and then unrelated sections (translation costs, impressions table,
    # T&Cs). Capture the two footer totals for reconciliation, then stop; and
    # only accept a row as a line when its daypart is real (a time range or ROS)
    # so stray text/impression rows can never masquerade as airtime.
    lines: List[CrispinLine] = []
    footer_paid = footer_bonus = None
    in_totals = False
    for r in rows[header_ri + 1:]:
        joined_l = " ".join(str(c).strip().lower() for c in r if c is not None)
        if "total paid" in joined_l:
            if "total_spots" in col and col["total_spots"] < len(r):
                footer_paid = int(_num(r[col["total_spots"]]))
            in_totals = True
            continue
        if "total bonus" in joined_l:
            if "total_spots" in col and col["total_spots"] < len(r):
                footer_bonus = int(_num(r[col["total_spots"]]))
            in_totals = True
            continue
        if in_totals:
            break  # past the airtime block — footers captured, stop

        lang_raw = r[col["lang"]] if col["lang"] < len(r) else None
        if lang_raw is None or not str(lang_raw).strip():
            continue

        daypart = str(r[col["daypart"]]).strip() if col["daypart"] < len(r) and r[col["daypart"]] else ""
        # Airtime guard: a real line has a time range (paid) or "ROS" (bonus).
        if not (daypart.upper() == "ROS" or _TIME_RE.search(daypart)):
            continue
        unit_value = _num(r[col["unit"]]) if "unit" in col and col["unit"] < len(r) else 0.0
        rate = _num(r[col["disc"]]) if col["disc"] < len(r) else 0.0
        length_cell = str(r[col["length"]]) if col["length"] < len(r) else ""
        m = re.search(r"(\d+)", length_cell)
        length_sec = int(m.group(1)) if m else 30

        spots = [int(_num(r[ci])) if ci < len(r) else 0 for ci in week_cols]

        lines.append(CrispinLine(
            language_block=str(lang_raw).strip(),
            daypart=daypart,
            unit_value=unit_value,
            rate=rate,
            length_sec=length_sec,
            week_dates=list(week_dates),
            week_spots=spots,
        ))

    if not lines:
        raise ValueError("Crispin parser: no airtime lines found")

    order = CrispinOrder(
        agency=agency,
        advertiser=advertiser,
        contact=contact,
        email=email,
        market_code=market_code,
        market_label=market_label,
        order_date=order_date,
        lines=lines,
        source_path=path,
    )

    # ── Reconcile against the footer (Brentan/SCWA totals lesson) ──
    paid_sum = sum(ln.total_spots for ln in order.paid_lines)
    bonus_sum = sum(ln.total_spots for ln in order.bonus_lines)
    if footer_paid is not None and paid_sum != footer_paid:
        raise ValueError(
            f"Crispin parser: paid spot total {paid_sum} != footer 'Total Paid' {footer_paid} "
            f"— a line was likely dropped; refusing to enter."
        )
    if footer_bonus is not None and bonus_sum != footer_bonus:
        raise ValueError(
            f"Crispin parser: bonus spot total {bonus_sum} != footer 'Total Bonuses' {footer_bonus}."
        )

    return order
