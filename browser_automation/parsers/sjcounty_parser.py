"""San Joaquin County (Registrar of Voters) proposal workbook parser.

Source: the Crossings TV proposal Charmaine builds for San Joaquin County
("San Joaquin County Voter Registration General 2026.xlsm"). One sheet, a
Charmaine-family grid with its own column layout:

    Insertion | Time | Value | <one column per flight week (real dates)> | Units | TOTAL

Sections, top to bottom:
  * header block   — "Client:", "Contact:", "Email:", "Phone:", "Creative:"
                     (":30 seconds"), "Flight Date:" ("9/14/2026 through 11/3/2026")
  * paid grid      — header row, one row per language block, closed by a
                     "Total Paid" row (units + dollars per week and overall)
  * bonus grid     — "BONUS (30seconds)" banner, its own header row, one ROS
                     row per language, closed by "Total Bonuses"
  * production     — "Production Services": "Voiceover translation fees: $2,650"
                     (a NOTE line starting with '*' is never money)
  * summary        — "Summary of Contract": Total Airtime / Voiceover
                     Translations only / Total

Money basis: the customer is DIRECT (ANAGRAF 451, no agency, 0% commission),
so gross == net; rates enter verbatim. `rates_are_net` is False.

Business rules (Lee, 2026-08-27):
  * customer = ANAGRAF 451 "San Joaquin County" (the PO names the County, the
    proposal names the Registrar of Voters); market CVC
  * the voiceover/translation fee is NOT a line — it rides the first paid
    line's Production box (CONTRATTISPESE 'Production')
  * the PO number goes in the Customer Order ref (CUSTOMERREF), asked at gather

Everything the sheet prints is reconciled and a mismatch RAISES (the document
is its own oracle — never enter a partial or mis-read order):
  * per line: sum(week cells) == Units and Units × Value == TOTAL
  * paid footer: per-week units, total units, total dollars
  * bonus footer: per-week units, total units
  * summary: Total Airtime == paid dollars, translation == charge, Total == sum
Columns are mapped by header LABEL, never by index (DART lesson) — an added
column is a no-op, a renamed one raises.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import List, Optional

# ─── Helpers ─────────────────────────────────────────────────────────────────

# Bonus rows name the block in the Insertion cell ("Chinese", "Punjabi"); paid
# rows lead with it ("Vietnamese News/Talk & Drama"). Order matters: longer
# names first so "South Asian" wins over nothing and "Cantonese" is not
# swallowed by "Chinese".
_BASE_LANGUAGES = (
    "South Asian",
    "Cantonese",
    "Mandarin",
    "Chinese",
    "Filipino",
    "Vietnamese",
    "Hmong",
    "Punjabi",
    "Korean",
    "Hindi",
    "Japanese",
)

_COST_WORDS = ("voiceover", "voice over", "translation", "production", "dubbing")
_MONEY_RE = re.compile(r"\$?\s*([\d][\d,]*(?:\.\d{1,2})?)")


def _money(v) -> Optional[float]:
    """Money cell → float, or None when unreadable. Never silently 0: a zero is
    a legitimate value (bonus), so it cannot double as 'could not read it'."""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).replace("$", "").replace(",", "").strip()
    if s in ("-", "–"):
        return 0.0
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _int_cell(v) -> Optional[int]:
    """Spot-count cell → int; blank → 0 (an unprinted zero); junk → None."""
    if v is None or (isinstance(v, str) and not v.strip()):
        return 0
    if isinstance(v, (int, float)):
        if float(v) != int(v):
            return None
        return int(v)
    try:
        return int(str(v).strip())
    except ValueError:
        return None


def _norm(s) -> str:
    """Collapse whitespace (the sheet has 'San Joaquin  County')."""
    return " ".join(str(s or "").split())


def _to_date(v) -> Optional[date]:
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    if isinstance(v, str):
        for fmt in ("%m/%d/%Y", "%m/%d/%y", "%Y-%m-%d"):
            try:
                return datetime.strptime(v.strip(), fmt).date()
            except ValueError:
                pass
    return None


def base_language(text: str) -> str:
    """'Chinese News (Mandarin & Cantonese)' → 'Chinese'; 'South Asian News
    (Punjabi)' → 'South Asian'; 'Punjabi' → 'Punjabi'."""
    low = _norm(text).lower()
    for lang in _BASE_LANGUAGES:
        if low.startswith(lang.lower()):
            return lang
    for lang in _BASE_LANGUAGES:
        if lang.lower() in low:
            return lang
    return _norm(text)


# ─── Data model ───────────────────────────────────────────────────────────────


@dataclass
class SJCountyLine:
    insertion: str  # "Chinese News (Mandarin & Cantonese)" | "Chinese" (bonus)
    daypart: str  # "M-Sun 7p-9p/ M-F 11:30p-12a" | "ROS Bonus"
    value: float  # sheet's Value column (paid: the rate; bonus: added value)
    is_bonus: bool
    length_sec: int
    week_dates: List[date]
    week_spots: List[int]
    units: int  # the sheet's own Units cell
    line_total: float  # the sheet's own TOTAL cell

    @property
    def rate(self) -> float:
        """Billed per-spot rate — 0 for a bonus row (its Value is added value)."""
        return 0.0 if self.is_bonus else self.value

    @property
    def total_spots(self) -> int:
        return sum(self.week_spots)

    @property
    def base_language(self) -> str:
        return base_language(self.insertion)

    @property
    def language(self) -> str:
        """Block language for the bridge's language-window check — 'Chinese'
        for the Mandarin & Cantonese block, never one dialect."""
        return self.base_language

    # Aliases so the generic parser_bridge normalizer (web preview) reads these.
    @property
    def description(self) -> str:
        return f"{_norm(self.insertion)} {_norm(self.daypart)}"

    @property
    def weekly_spots(self) -> List[int]:
        return self.week_spots

    @property
    def length(self) -> int:
        return self.length_sec

    @property
    def duration(self) -> str:
        return str(self.length_sec)

    @property
    def days(self) -> str:
        if self.is_bonus:
            return "M-Su"
        from browser_automation.ntooitive_automation import split_daypart_union

        return split_daypart_union(self.daypart)[0]

    @property
    def time(self) -> str:
        if self.is_bonus:
            return "ROS"
        from browser_automation.ntooitive_automation import split_daypart_union

        return split_daypart_union(self.daypart)[1]


@dataclass
class SJCountyCharge:
    """Non-airtime money (voiceover translation). Enters as a CONTRATTISPESE
    'Production' charge on the first paid line — never a line."""

    description: str
    amount: float


@dataclass
class SJCountyOrder:
    title: str
    client: str
    contact: str
    email: str
    phone: str
    market_code: str = "CVC"
    flight_start_date: Optional[date] = None
    flight_end_date: Optional[date] = None
    lines: List[SJCountyLine] = field(default_factory=list)
    charges: List[SJCountyCharge] = field(default_factory=list)
    rates_are_net: bool = False  # direct customer, 0% — gross == net
    paid_units_stated: Optional[int] = None
    paid_total_stated: Optional[float] = None
    bonus_units_stated: Optional[int] = None
    summary_airtime: Optional[float] = None
    summary_production: Optional[float] = None
    summary_total: Optional[float] = None
    source_path: str = ""

    # Bridge aliases
    @property
    def advertiser(self) -> str:
        return self.client

    @property
    def description(self) -> str:
        return self.title

    @property
    def market(self) -> str:
        return self.market_code

    @property
    def paid_lines(self) -> List[SJCountyLine]:
        return [ln for ln in self.lines if not ln.is_bonus]

    @property
    def bonus_lines(self) -> List[SJCountyLine]:
        return [ln for ln in self.lines if ln.is_bonus]

    @property
    def week_dates(self) -> List[date]:
        return sorted({d for ln in self.lines for d in ln.week_dates})

    @property
    def flight_start(self) -> Optional[str]:
        d = self.flight_start_date or (self.week_dates[0] if self.week_dates else None)
        return d.strftime("%m/%d/%Y") if d else None

    @property
    def flight_end(self) -> Optional[str]:
        d = self.flight_end_date
        if d is None and self.week_dates:
            d = self.week_dates[-1] + timedelta(days=6)
        return d.strftime("%m/%d/%Y") if d else None

    @property
    def paid_total(self) -> float:
        return round(sum(ln.rate * ln.total_spots for ln in self.paid_lines), 2)

    @property
    def production_total(self) -> float:
        return round(sum(c.amount for c in self.charges), 2)

    @property
    def total_cost(self) -> float:
        return round(self.paid_total + self.production_total, 2)

    @property
    def total_spots(self) -> int:
        return sum(ln.total_spots for ln in self.lines)


# ─── Workbook reading ─────────────────────────────────────────────────────────

_REQUIRED_COLS = ("insertion", "time", "value", "units", "total")


def _header_map(row_vals: list) -> Optional[dict]:
    """Map a grid header row by LABEL → column index. Returns None unless the
    row carries every required label plus at least one real week date."""
    cols: dict = {"weeks": []}
    for ci, v in enumerate(row_vals):
        d = _to_date(v) if not isinstance(v, str) else None
        if d:
            cols["weeks"].append((ci, d))
            continue
        label = _norm(v).lower()
        if not label:
            continue
        if label == "insertion":
            cols["insertion"] = ci
        elif label == "time":
            cols["time"] = ci
        elif label == "value":
            cols["value"] = ci
        elif label == "units":
            cols["units"] = ci
        elif label in ("total", "total value"):
            cols["total"] = ci
    if not cols["weeks"] or "insertion" not in cols:
        return None
    missing = [k for k in _REQUIRED_COLS if k not in cols]
    if missing:
        raise ValueError(
            f"SJ County: grid header row is missing the {', '.join(missing)} column — renamed?"
        )
    return cols


def _row_text(row_vals: list) -> str:
    return " ".join(_norm(v) for v in row_vals if v not in (None, "")).lower()


def _parse_flight(text: str) -> tuple[Optional[date], Optional[date]]:
    m = re.search(
        r"(\d{1,2}/\d{1,2}/\d{2,4})\s*(?:through|thru|to|-|–)\s*(\d{1,2}/\d{1,2}/\d{2,4})",
        text,
        re.I,
    )
    if not m:
        return None, None
    return _to_date(m.group(1)), _to_date(m.group(2))


def _read_grid_row(row_vals: list, cols: dict, is_bonus: bool, length_sec: int) -> SJCountyLine:
    insertion = _norm(row_vals[cols["insertion"]])
    daypart = _norm(row_vals[cols["time"]])
    value = _money(row_vals[cols["value"]])
    if value is None:
        raise ValueError(f"SJ County: unreadable Value on line '{insertion}'")
    units = _int_cell(row_vals[cols["units"]])
    total = _money(row_vals[cols["total"]])
    if units is None or total is None:
        raise ValueError(f"SJ County: unreadable Units/TOTAL on line '{insertion}'")
    spots: List[int] = []
    for ci, _d in cols["weeks"]:
        n = _int_cell(row_vals[ci] if ci < len(row_vals) else None)
        if n is None:
            raise ValueError(f"SJ County: unreadable week cell on line '{insertion}'")
        spots.append(n)
    if sum(spots) != units:
        raise ValueError(
            f"SJ County: '{insertion}' week cells sum to {sum(spots)} but Units says {units}"
        )
    if abs(round(value * units, 2) - total) > 0.005:
        raise ValueError(
            f"SJ County: '{insertion}' {units} × ${value:,.2f} = ${value * units:,.2f} but TOTAL says ${total:,.2f}"
        )
    return SJCountyLine(
        insertion=insertion,
        daypart=daypart,
        value=value,
        is_bonus=is_bonus,
        length_sec=length_sec,
        week_dates=[d for _ci, d in cols["weeks"]],
        week_spots=spots,
        units=units,
        line_total=total,
    )


def _footer_check(
    row_vals: list, cols: dict, lines: List[SJCountyLine], label: str, check_dollars: bool
) -> tuple[int, Optional[float]]:
    """Reconcile a 'Total Paid' / 'Total Bonuses' row against the lines above."""
    for wi, (ci, d) in enumerate(cols["weeks"]):
        stated = _int_cell(row_vals[ci] if ci < len(row_vals) else None)
        got = sum(ln.week_spots[wi] for ln in lines)
        if stated is None or stated != got:
            raise ValueError(
                f"SJ County: {label} week {d:%m/%d} units {stated} != sum of lines {got}"
            )
    units = _int_cell(row_vals[cols["units"]])
    got_units = sum(ln.total_spots for ln in lines)
    if units is None or units != got_units:
        raise ValueError(f"SJ County: {label} units {units} != sum of lines {got_units}")
    dollars = _money(row_vals[cols["total"]])
    if check_dollars:
        got_d = round(sum(ln.value * ln.total_spots for ln in lines), 2)
        if dollars is None or abs(dollars - got_d) > 0.005:
            raise ValueError(f"SJ County: {label} dollars {dollars} != sum of lines {got_d}")
    return units, dollars


def _load_rows(path: str) -> list[list]:
    """First sheet → list of row value lists (cached formula results). Kept
    as a seam so tests can tamper the cell grid without re-saving the
    workbook (openpyxl drops cached formula values on save)."""
    import openpyxl

    wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
    try:
        return [list(r) for r in wb.worksheets[0].iter_rows(values_only=True)]
    finally:
        wb.close()


def parse_sjcounty(path: str) -> SJCountyOrder:
    """Parse the San Joaquin County proposal workbook (.xlsx/.xlsm)."""
    if not str(path).lower().endswith((".xlsx", ".xlsm")):
        raise ValueError(f"SJ County: expected an .xlsx/.xlsm workbook, got {path}")

    rows = _load_rows(path)

    header: dict = {}
    title = ""
    flight_start = flight_end = None
    length_sec = 30
    lines: List[SJCountyLine] = []
    charges: List[SJCountyCharge] = []
    paid_units = paid_total = bonus_units = None
    summary_airtime = summary_prod = summary_total = None

    section = "header"  # header → paid → between → bonus → production → summary
    cols: Optional[dict] = None
    block: List[SJCountyLine] = []

    for row_vals in rows:
        text = _row_text(row_vals)
        if not text:
            continue
        cells = [v for v in row_vals if v not in (None, "")]

        # ── section transitions by banner text ──
        if section in ("between", "bonus") and text.startswith("production services"):
            section = "production"
            continue
        if section in ("production", "between", "bonus") and text.startswith("summary of"):
            section = "summary"
            continue

        if section == "header":
            if not title and len(cells) == 1 and isinstance(cells[0], str):
                title = _norm(cells[0])
            for i, v in enumerate(row_vals):
                lab = _norm(v).lower().rstrip(":")
                if lab in ("client", "contact", "email", "phone", "creative", "flight date"):
                    nxt = next((x for x in row_vals[i + 1 :] if x not in (None, "")), "")
                    header[lab] = _norm(nxt)
            if "flight date" in header and flight_start is None:
                flight_start, flight_end = _parse_flight(header["flight date"])
            if "creative" in header:
                m = re.search(r":?(\d{1,3})\s*sec", header["creative"], re.I)
                if m:
                    length_sec = int(m.group(1))
            hm = _header_map(row_vals)
            if hm:
                cols, section, block = hm, "paid", []
            continue

        if section in ("paid", "bonus"):
            assert cols is not None
            if text.startswith("total paid") or text.startswith("total bonus"):
                is_paid = text.startswith("total paid")
                if is_paid != (section == "paid"):
                    raise ValueError(f"SJ County: footer '{text[:20]}' in the {section} block")
                u, d = _footer_check(
                    row_vals,
                    cols,
                    block,
                    "Total Paid" if is_paid else "Total Bonuses",
                    check_dollars=is_paid,
                )
                if is_paid:
                    paid_units, paid_total = u, d
                else:
                    bonus_units = u
                lines.extend(block)
                block = []
                section = "between"
                continue
            if _header_map(row_vals):
                raise ValueError("SJ County: a second header row inside an open grid block")
            ln = _read_grid_row(
                row_vals, cols, is_bonus=(section == "bonus"), length_sec=length_sec
            )
            block.append(ln)
            continue

        if section == "between":
            hm = _header_map(row_vals)
            if hm:
                cols, section, block = hm, "bonus", []
                continue
            # "BONUS (30seconds)" banner — may restate the length
            m = re.search(r"(\d{1,3})\s*sec", text)
            if "bonus" in text and m:
                length_sec = int(m.group(1))
            continue

        if section == "production":
            first = _norm(cells[0]) if cells else ""
            if first.startswith("*"):
                continue  # note line ("*Retail Value ($3,525)") — never money
            if "$" in text or any(isinstance(v, (int, float)) for v in cells):
                if not any(w in text for w in _COST_WORDS):
                    raise ValueError(
                        f"SJ County: money in Production Services I cannot classify: '{first}'"
                    )
                amount = None
                for v in cells:
                    if isinstance(v, (int, float)):
                        amount = float(v)
                        break
                if amount is None:
                    m = _MONEY_RE.search(text[text.find("$") :])
                    amount = float(m.group(1).replace(",", "")) if m else None
                if amount is None:
                    raise ValueError(f"SJ County: unreadable production amount: '{first}'")
                desc = re.sub(r"[:\s]*\$?[\d,]+(\.\d+)?\s*$", "", first).strip(" :")
                charges.append(
                    SJCountyCharge(description=desc or "Production", amount=round(amount, 2))
                )
            continue

        if section == "summary":
            label = _norm(cells[0]).lower() if cells else ""
            val = next((_money(v) for v in cells[1:] if _money(v) is not None), None)
            if val is None:
                continue
            if "airtime" in label:
                summary_airtime = val
            elif any(w in label for w in _COST_WORDS):
                summary_prod = val
            elif label.startswith("total"):
                summary_total = val
            continue

    if cols is None or not lines:
        raise ValueError(
            "SJ County: no grid header row (Insertion/Time/Value/Units/TOTAL + week dates) found"
        )
    if block:
        raise ValueError(f"SJ County: the {section} block never closed with its Total row")
    if flight_start is None or flight_end is None:
        raise ValueError("SJ County: no 'Flight Date: m/d/yyyy through m/d/yyyy' header")

    order = SJCountyOrder(
        title=title,
        client=header.get("client", ""),
        contact=header.get("contact", ""),
        email=header.get("email", ""),
        phone=header.get("phone", ""),
        flight_start_date=flight_start,
        flight_end_date=flight_end,
        lines=lines,
        charges=charges,
        paid_units_stated=paid_units,
        paid_total_stated=paid_total,
        bonus_units_stated=bonus_units,
        summary_airtime=summary_airtime,
        summary_production=summary_prod,
        summary_total=summary_total,
        source_path=str(path),
    )
    _reconcile(order)
    return order


def _reconcile(order: SJCountyOrder) -> None:
    for ln in order.lines:
        if ln.total_spots != ln.units:
            raise ValueError(
                f"SJ County: '{ln.insertion}' week cells sum to {ln.total_spots} but Units says {ln.units}"
            )
        expect = round(ln.value * ln.units, 2)
        if abs(expect - ln.line_total) > 0.005:
            raise ValueError(
                f"SJ County: '{ln.insertion}' {ln.units} × ${ln.value:,.2f} = ${expect:,.2f} but TOTAL says ${ln.line_total:,.2f}"
            )
        if not ln.is_bonus and ln.value <= 0:
            raise ValueError(f"SJ County: paid line '{ln.insertion}' has no rate")
        if ln.total_spots and not ln.daypart:
            raise ValueError(f"SJ County: '{ln.insertion}' has spots but no Time/daypart")
    for ln in order.lines:
        for d, n in zip(ln.week_dates, ln.week_spots):
            if n and order.flight_end_date and d > order.flight_end_date:
                raise ValueError(
                    f"SJ County: '{ln.insertion}' has spots in week {d:%m/%d}, after the flight end"
                )
    if not order.paid_lines:
        raise ValueError("SJ County: no paid lines")
    if order.paid_total_stated is None or abs(order.paid_total - order.paid_total_stated) > 0.005:
        raise ValueError(
            f"SJ County: paid lines total ${order.paid_total:,.2f} != Total Paid ${order.paid_total_stated}"
        )
    if order.summary_airtime is None or abs(order.summary_airtime - order.paid_total) > 0.005:
        raise ValueError(
            f"SJ County: Summary Total Airtime {order.summary_airtime} != paid lines ${order.paid_total:,.2f}"
        )
    if order.charges or order.summary_production:
        if (
            order.summary_production is None
            or abs((order.summary_production or 0) - order.production_total) > 0.005
        ):
            raise ValueError(
                f"SJ County: Summary production {order.summary_production} != Production Services ${order.production_total:,.2f}"
            )
    if order.summary_total is None or abs(order.summary_total - order.total_cost) > 0.005:
        raise ValueError(
            f"SJ County: Summary Total {order.summary_total} != airtime + production ${order.total_cost:,.2f}"
        )
    if order.bonus_lines and order.bonus_units_stated is None:
        raise ValueError("SJ County: bonus block has no Total Bonuses row")


if __name__ == "__main__":  # pragma: no cover
    import sys

    o = parse_sjcounty(sys.argv[1])
    print(o.title, "|", o.client, "|", o.flight_start, "→", o.flight_end)
    for ln in o.lines:
        print(
            f"  {'BNS' if ln.is_bonus else '   '} {ln.insertion:<38} {ln.daypart:<28} ${ln.rate:>6.2f} {ln.week_spots} = {ln.units}"
        )
    for c in o.charges:
        print(f"  charge {c.description}: ${c.amount:,.2f}")
    print(
        f"  paid ${o.paid_total:,.2f} + production ${o.production_total:,.2f} = ${o.total_cost:,.2f}"
    )
