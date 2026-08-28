"""Prince of Peace (POP) — Crossings TV Sales Confirmation PDF parser.

Source: the house "SALES CONFIRMATION - CROSSINGS TV" PDF Charmaine issues for
Prince of Peace Enterprises (advertiser Kwan Loong Oil), e.g.
"Prince of Peace_KLO_September 2026.pdf". One page:

  * header block — Client / Advertiser / Contact / Estimate / Address /
    Billing Type / Market / Phone / Date Order Written / Contract Number /
    Revision / Station Representative
  * line grid — Line Number | Start Date | End Date | # spt per | Per ____ |
    TP/Program/Lang Ordered | # of days, wks, mos | Spot type | Total # of
    Units | Length | Gross Unit Rate | Gross Line Total
  * footer — "Gross Amount <units> spots $<total>", "Net Amount of Contract
    <units> $<total>"
  * Additional Notes box, creative Dropbox link

Money basis: DIRECT customer (ANAGRAF 90 "Prince of Peace Enterprises, Inc."),
no agency, 0% commission — gross == net, rates enter verbatim; `rates_are_net`
is False.

Structure: MONTH-ONLY lines (a flight + a total, no week columns) → Rotation
scheduling (spots_per_week=0), the universal rule. "VIET M-F 10A-1P" carries
language + days + daypart in one cell; "VIET Various" + spot type BNS is the
bonus ROS line.

Everything the sheet prints is reconciled and a mismatch RAISES:
  * per line: Units × Rate == Gross Line Total
  * footer: sum(Units) == Gross Amount units and Net Amount units;
    sum(line totals) == Gross Amount $ == Net Amount $
Columns are mapped by header LABEL, never by index (DART lesson).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import List, Optional

_LANGUAGES = {
    "VIET": "Vietnamese",
    "VIETNAMESE": "Vietnamese",
    "CHINESE": "Chinese",
    "MANDARIN": "Mandarin",
    "CANTONESE": "Cantonese",
    "FILIPINO": "Filipino",
    "TAGALOG": "Filipino",
    "KOREAN": "Korean",
    "PUNJABI": "Punjabi",
    "HINDI": "Hindi",
    "SOUTH ASIAN": "South Asian",
    "HMONG": "Hmong",
    "JAPANESE": "Japanese",
}

_MARKETS = {
    "CV": "CVC",
    "CVC": "CVC",
    "SF": "SFO",
    "SFO": "SFO",
    "LA": "LAX",
    "LAX": "LAX",
    "SEA": "SEA",
    "HOU": "HOU",
    "CMP": "CMP",
    "WDC": "WDC",
    "NYC": "NYC",
    "MMT": "MMT",
    "DAL": "DAL",
}

# Header labels → attribute (mapped by LABEL, never by column index)
_COLS = {
    "line_number": "Line Number",
    "start_date": "Start Date",
    "end_date": "End Date",
    "per_count": "# spt per",
    "per_unit": "Per",
    "ordered": "TP/Program/Lang Ordered",
    "periods": "# of days",
    "spot_type": "Spot",
    "units": "Total # of",
    "length": "Length",
    "rate": "Gross Unit",
    "line_total": "Gross Line",
}


class POPParseError(ValueError):
    """The confirmation could not be read or does not reconcile — never enter it."""


def _norm(s) -> str:
    return " ".join(str(s or "").replace("\n", " ").split())


def _money(v) -> Optional[float]:
    """'$ 40.00' → 40.0; '$ -' → 0.0 (a printed dash IS zero); junk → None.
    Never silently 0 for unreadable input."""
    if v is None:
        return None
    s = _norm(v).replace("$", "").replace(",", "").replace(" ", "")
    if s in ("-", "–", "—"):
        return 0.0
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _int(v) -> Optional[int]:
    s = _norm(v)
    if not s:
        return None
    try:
        return int(s)
    except ValueError:
        return None


def _date(v) -> Optional[date]:
    s = _norm(v)
    for fmt in ("%m/%d/%Y", "%m/%d/%y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def _mmddyyyy(d: date) -> str:
    return f"{d.month:02d}/{d.day:02d}/{d.year}"


_DAY_ALIASES = [
    (re.compile(r"^M\s*-\s*SU(N)?$", re.I), "M-Su"),
    (re.compile(r"^M\s*-\s*F(RI)?$", re.I), "M-F"),
    (re.compile(r"^M\s*-\s*SA(T)?$", re.I), "M-Sa"),
    (re.compile(r"^SA(T)?\s*-\s*SU(N)?$", re.I), "Sa-Su"),
    (re.compile(r"^SA(T)?$", re.I), "Sa"),
    (re.compile(r"^SU(N)?$", re.I), "Su"),
]

_TIME_RE = re.compile(r"(\d{1,2}(?::\d{2})?\s*[AP]M?\s*-\s*\d{1,2}(?::\d{2})?\s*[AP]M?)", re.I)


def split_ordered(cell: str) -> tuple[str, str, str, bool]:
    """'VIET M-F 10A-1P' → ('Vietnamese', 'M-F', '10A-1P', False);
    'VIET SAT - SUN 10A-1P' → ('Vietnamese', 'Sa-Su', '10A-1P', False);
    'VIET Various' → ('Vietnamese', '', '', True)  (ROS)."""
    text = _norm(cell)
    upper = text.upper()
    language = ""
    for key in sorted(_LANGUAGES, key=len, reverse=True):
        if upper.startswith(key + " ") or upper == key:
            language = _LANGUAGES[key]
            text = text[len(key) :].strip()
            break
    if not language:
        raise POPParseError(f"line names no known language: {cell!r}")
    if re.fullmatch(r"(various|ros|run of schedule)", text, re.I):
        return language, "", "", True
    m = _TIME_RE.search(text)
    if not m:
        raise POPParseError(f"line has no daypart time: {cell!r}")
    time_str = re.sub(r"\s+", "", m.group(1))
    days_raw = _norm(text[: m.start()])
    days = ""
    for rx, canon in _DAY_ALIASES:
        if rx.match(days_raw):
            days = canon
            break
    if not days:
        raise POPParseError(f"line has an unrecognised day pattern {days_raw!r}: {cell!r}")
    return language, days, time_str, False


@dataclass
class POPLine:
    line_number: int
    start_date: str  # MM/DD/YYYY
    end_date: str
    language: str
    days: str  # canonical 'M-F' / 'Sa-Su' / '' for ROS
    time: str  # '10A-1P' / '' for ROS
    is_bonus: bool
    total_spots: int
    length_sec: int
    rate: float
    line_total: float
    spot_type: str = "COM"
    ordered_text: str = ""
    market: str = ""

    @property
    def description(self) -> str:
        if self.is_bonus:
            return f"BNS {self.language} ROS"
        return f"{self.days} {self.language}"

    @property
    def duration(self) -> str:
        return str(self.length_sec)


@dataclass
class POPOrder:
    client: str
    advertiser: str
    contact: str
    estimate: str
    billing_type: str
    market: str  # code, e.g. 'CVC'
    market_text: str
    date_written: str
    contract_number: str
    revision: str
    station_rep: str
    flight_start: str
    flight_end: str
    lines: List[POPLine] = field(default_factory=list)
    gross_units: int = 0
    gross_total: float = 0.0
    net_units: int = 0
    net_total: float = 0.0
    notes: str = ""
    creative_link: str = ""
    source_path: str = ""
    rates_are_net: bool = False  # direct, 0% — gross == net

    @property
    def description(self) -> str:
        return f"{self.advertiser} {self.estimate}".strip()

    @property
    def paid_lines(self) -> list[POPLine]:
        return [ln for ln in self.lines if not ln.is_bonus]

    @property
    def bonus_lines(self) -> list[POPLine]:
        return [ln for ln in self.lines if ln.is_bonus]

    @property
    def paid_total(self) -> float:
        return round(sum(ln.line_total for ln in self.paid_lines), 2)

    @property
    def total_spots(self) -> int:
        return sum(ln.total_spots for ln in self.lines)


# ─── Extraction (patchable in tests) ────────────────────────────────────────


def _extract(path: str) -> tuple[str, list[list[list]]]:
    """(page text, tables) for every page — the only pdfplumber touch point."""
    import pdfplumber

    text_parts: list[str] = []
    tables: list[list[list]] = []
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            text_parts.append(page.extract_text() or "")
            tables.extend(page.extract_tables() or [])
    return "\n".join(text_parts), tables


# ─── Header ──────────────────────────────────────────────────────────────────


def _field(text: str, label: str, stop_labels: tuple[str, ...]) -> str:
    """Value printed after `label` on its line, up to the next known label."""
    stop = "|".join(re.escape(s) for s in stop_labels if s != label)
    m = re.search(rf"{re.escape(label)}\s+(.*?)(?:\s+(?:{stop})\b|$)", text, re.M)
    return _norm(m.group(1)) if m else ""


_HEADER_LABELS = (
    "Client",
    "Advertiser",
    "Contact",
    "Estimate",
    "Address",
    "Billing Type",
    "Market",
    "Phone",
    "Date Order Written",
    "Fax",
    "Contract Number",
    "Email",
    "Revision",
    "Station Representative",
)


def is_pop_text(text: str) -> bool:
    """Detector: the house Sales Confirmation with Prince of Peace as the client
    (the CLIENT is the definer, never the template — Lee)."""
    t = " ".join(text.split()).lower()
    return "sales confirmation" in t and "prince of peace" in t


# ─── Parse ───────────────────────────────────────────────────────────────────


def parse_pop(path: str) -> POPOrder:
    text, tables = _extract(path)
    if not is_pop_text(text):
        raise POPParseError("not a Prince of Peace Sales Confirmation")

    hdr = {lbl: _field(text, lbl, _HEADER_LABELS) for lbl in _HEADER_LABELS}
    market_text = hdr["Market"]
    market_code = _MARKETS.get(market_text.split()[0].upper(), "") if market_text else ""
    if not market_code:
        raise POPParseError(f"unknown market {market_text!r}")

    # ── line grid: the table whose header row carries the line labels ──
    grid, header_ri = None, -1
    for t in tables:
        for ri, row in enumerate(t):
            cells = [_norm(c) for c in row]
            if any(c.startswith(_COLS["line_number"]) for c in cells) and any(
                c.startswith(_COLS["units"]) for c in cells
            ):
                grid, header_ri = t, ri
                break
        if grid is not None:
            break
    if grid is None:
        raise POPParseError("line grid header not found")
    header = [_norm(c) for c in grid[header_ri]]
    col: dict[str, int] = {}
    for key, label in _COLS.items():
        idx = next((i for i, h in enumerate(header) if h.lower().startswith(label.lower())), None)
        if idx is None:
            raise POPParseError(f"column {label!r} missing from the line grid")
        col[key] = idx

    lines: list[POPLine] = []
    for row in grid[header_ri + 1 :]:
        cells = [_norm(c) for c in row]
        ln_no = _int(cells[col["line_number"]])
        if ln_no is None:
            continue  # footer rows carry no line number
        units = _int(cells[col["units"]])
        rate = _money(cells[col["rate"]])
        start, end = _date(cells[col["start_date"]]), _date(cells[col["end_date"]])
        length_m = re.search(r"(\d+)", cells[col["length"]] or "")
        if units is None or rate is None or start is None or end is None or not length_m:
            raise POPParseError(f"unreadable line row: {cells}")
        spot_type = cells[col["spot_type"]].upper()
        language, days, time_str, ros = split_ordered(cells[col["ordered"]])
        is_bonus = spot_type == "BNS" or ros
        if is_bonus and rate not in (0.0, None):
            raise POPParseError(f"bonus line carries a rate: {cells}")
        # Gross Line Total: the table cell is often blank (pdfplumber splits the
        # column) — read it from the text row that starts with this line's fields.
        ltot = _money(cells[col["line_total"]])
        if ltot is None:
            rx = re.compile(
                rf"^{ln_no}\s+{re.escape(cells[col['start_date']])}\s+{re.escape(cells[col['end_date']])}.*?"
                rf"{re.escape(spot_type)}\s+{units}\s+:?\d+\s+\$\s*([\d,.]+|-)\s+\$\s*([\d,.\s]+|-)\s*$",
                re.M,
            )
            m = rx.search(text)
            if not m:
                raise POPParseError(
                    f"line {ln_no} total not found in text: {cells[col['ordered']]}"
                )
            ltot = _money(m.group(2))
            if ltot is None:
                raise POPParseError(f"line {ln_no} total unreadable: {m.group(2)!r}")
        if abs(units * rate - ltot) > 0.005:
            raise POPParseError(
                f"line {ln_no} {cells[col['ordered']]}: {units} × {rate} = {units * rate:.2f} ≠ printed {ltot:.2f}"
            )
        lines.append(
            POPLine(
                line_number=ln_no,
                start_date=_mmddyyyy(start),
                end_date=_mmddyyyy(end),
                language=language,
                days=days,
                time=time_str,
                is_bonus=is_bonus,
                total_spots=units,
                length_sec=int(length_m.group(1)),
                rate=rate,
                line_total=ltot,
                spot_type=spot_type,
                ordered_text=cells[col["ordered"]],
                market=market_code,
            )
        )
    if not lines:
        raise POPParseError("no line rows")

    # ── footer reconciliation (the document is its own oracle) ──
    gm = re.search(r"Gross Amount\s+(\d+)\s+spots\s+\$\s*([\d,.\s]+)", text)
    nm = re.search(r"Net Amount of Contract\s+(\d+)\s+\$\s*([\d,.\s]+)", text)
    if not gm or not nm:
        raise POPParseError("Gross Amount / Net Amount footer not found")
    gross_units, gross_total = int(gm.group(1)), _money(gm.group(2))
    net_units, net_total = int(nm.group(1)), _money(nm.group(2))
    if gross_total is None or net_total is None:
        raise POPParseError("footer totals unreadable")
    sum_units = sum(ln.total_spots for ln in lines)
    sum_total = round(sum(ln.line_total for ln in lines), 2)
    if sum_units != gross_units or sum_units != net_units:
        raise POPParseError(f"units: lines {sum_units} ≠ Gross {gross_units} / Net {net_units}")
    if abs(sum_total - gross_total) > 0.005 or abs(gross_total - net_total) > 0.005:
        raise POPParseError(
            f"dollars: lines {sum_total:.2f} ≠ Gross {gross_total:.2f} / Net {net_total:.2f}"
        )

    notes = ""
    for t in tables:
        flat = [_norm(c) for row in t for c in row if _norm(c)]
        if len(flat) == 1 and t is not grid:
            notes = flat[0]
    link_m = re.search(r"Link:\s*(\S+)", text)

    starts = [datetime.strptime(ln.start_date, "%m/%d/%Y").date() for ln in lines]
    ends = [datetime.strptime(ln.end_date, "%m/%d/%Y").date() for ln in lines]
    return POPOrder(
        client=hdr["Client"],
        advertiser=hdr["Advertiser"],
        contact=hdr["Contact"],
        estimate=hdr["Estimate"],
        billing_type=hdr["Billing Type"],
        market=market_code,
        market_text=market_text,
        date_written=hdr["Date Order Written"],
        contract_number=hdr["Contract Number"],
        revision=hdr["Revision"],
        station_rep=hdr["Station Representative"],
        flight_start=_mmddyyyy(min(starts)),
        flight_end=_mmddyyyy(max(ends)),
        lines=lines,
        gross_units=gross_units,
        gross_total=gross_total,
        net_units=net_units,
        net_total=net_total,
        notes=notes,
        creative_link=link_m.group(1) if link_m else "",
        source_path=path,
    )
