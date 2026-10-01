"""
Gauger + Associates "Broadcast Order" PDF parser (NET rates).

Layout (one page; first order: Shea Homes IO 90658, Oct 2026):

    Broadcast Order
    Gauger + Associates                 Order number 90658
    ...                                  Order date 09/30/26
    Client: SHA        To: Crossings TV
    Job: SHA 997-26    Attn: Charmaine Lane
    Market: San Francisco
    Product: Crossings TV  :30
    Broadcast month: October 2026
    Ad name: Opal-Emerald
    Desc: Three Week Campaign ...
      Hindi News and Variety: Monday -Friday 1p-2p total 21 spots (over 3 weeks)
      Punjabi News: M-F 2p-4p 18 spots (over 3 weeks)
      ...
      50 paid spots: Net cost $4,913
    Dates      Days    Time   Program               Rating Len CPP Spots Total
    10/8-10/29 M-F     1p-2p  Hindi News and Vari...        30      21   $2,142.00
    10/8-10/29 Mon - Sun 1p-4p Hindi/Punjabi Bonus          30      24
    TOTAL SPOTS: 74
    CPP:   NET: $4,913.00

Rules:
  * the TABLE is the structured source (dates, days, time, length, spots, dollars); the
    Program cell is truncated ("Vari...") and misspelt ("Pujabi"), so the program name is
    completed from the Desc block, matched by spot count;
  * every column is found by its HEADER LABEL and x position — never by index;
  * money is NET (the IO says so twice); the per-spot net rate must divide the line total
    to the cent, and the sheet reconciles three ways (line totals vs NET, paid spots vs
    "N paid spots", all spots vs TOTAL SPOTS) or the parse RAISES;
  * a row with no Total and/or "Bonus" in the program is a bonus line ($0).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import List, Optional

import pdfplumber

AGENCY_NAME = "Gauger + Associates"

_MARKET_BY_NAME = {
    "san francisco": "SFO",
    "sacramento": "CVC",
    "central valley": "CVC",
    "los angeles": "LAX",
    "seattle": "SEA",
    "houston": "HOU",
    "chicago": "CMP",
    "minneapolis": "CMP",
    "washington": "WDC",
    "new york": "NYC",
    "dallas": "DAL",
}

_DAY_ALIASES = {
    "M-F": "M-F",
    "MON-FRI": "M-F",
    "MONDAY-FRIDAY": "M-F",
    "M-SU": "M-Su",
    "M-SUN": "M-Su",
    "MON-SUN": "M-Su",
    "MONDAY-SUNDAY": "M-Su",
    "M-SA": "M-Sa",
    "MON-SAT": "M-Sa",
    "SA-SU": "Sa-Su",
    "SAT-SUN": "Sa-Su",
    "SATURDAY-SUNDAY": "Sa-Su",
    "SAT": "Sa",
    "SATURDAY": "Sa",
    "SUN": "Su",
    "SUNDAY": "Su",
}

_TABLE_COLUMNS = ("Dates", "Days", "Time", "Program", "Rating", "Len", "CPP", "Spots", "Total")
_ROW_TOL = 2.0  # pt; intra-row jitter is ~0, row pitch ~10


def is_gauger_text(text: str) -> bool:
    t = " ".join((text or "").split()).lower()
    return "broadcast order" in t and "gauger + associates" in t


class GaugerParseError(ValueError):
    pass


@dataclass
class GaugerLine:
    date_from: date
    date_to: date
    days: str  # normalised: "M-F" | "Sa-Su" | "M-Su" | ...
    time: str  # as printed: "1p-2p"
    program: str  # completed from the Desc block when the table truncates it
    program_cell: str  # what the table printed
    length_sec: int
    spots: int
    net_total: float  # 0.0 on a bonus line
    is_bonus: bool

    @property
    def net_rate(self) -> float:
        if self.is_bonus or not self.spots:
            return 0.0
        return round(self.net_total / self.spots, 2)

    @property
    def description(self) -> str:
        base = f"{self.days} {self.time} {self.program}".strip()
        return f"BNS {base}" if self.is_bonus else base


@dataclass
class GaugerOrder:
    order_number: str
    order_date: Optional[date]
    client_code: str  # "SHA"
    job: str
    market_name: str  # "San Francisco"
    market_code: str  # "SFO"
    ad_name: str
    broadcast_month: str
    length_sec: int
    contact: str
    email: str
    revision: str
    lines: List[GaugerLine] = field(default_factory=list)
    paid_spots_stated: int = 0
    total_spots_stated: int = 0
    net_total_stated: float = 0.0
    agency: str = AGENCY_NAME
    rates_are_net: bool = True

    @property
    def paid_lines(self) -> List[GaugerLine]:
        return [ln for ln in self.lines if not ln.is_bonus]

    @property
    def bonus_lines(self) -> List[GaugerLine]:
        return [ln for ln in self.lines if ln.is_bonus]

    @property
    def flight_start(self) -> date:
        return min(ln.date_from for ln in self.lines)

    @property
    def flight_end(self) -> date:
        return max(ln.date_to for ln in self.lines)

    @property
    def net_total(self) -> float:
        return round(sum(ln.net_total for ln in self.paid_lines), 2)

    @property
    def total_spots(self) -> int:
        return sum(ln.spots for ln in self.lines)

    @property
    def customer_ref(self) -> str:
        return f"Order {self.order_number}"


# ─── helpers ──────────────────────────────────────────────────────────────────


def _money(text: str) -> Optional[float]:
    t = (text or "").replace("$", "").replace(",", "").strip()
    if not t:
        return None
    try:
        return round(float(t), 2)
    except ValueError:
        return None


def normalize_days(text: str) -> str:
    """'Mon - Sun' → 'M-Su', 'Sat-Sun' → 'Sa-Su', 'M-F' → 'M-F'. Unknown spellings raise:
    the day pattern decides where spots air and is never guessed."""
    key = re.sub(r"\s+", "", (text or "")).upper().replace("–", "-")
    if key in _DAY_ALIASES:
        return _DAY_ALIASES[key]
    raise GaugerParseError(f"unrecognised day pattern {text!r}")


def _parse_mdy(text: str, year_hint: Optional[int]) -> date:
    m = re.fullmatch(r"(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?", text.strip())
    if not m:
        raise GaugerParseError(f"unreadable date {text!r}")
    mo, d, y = int(m.group(1)), int(m.group(2)), m.group(3)
    if y:
        yy = int(y)
        yy = yy + 2000 if yy < 100 else yy
    elif year_hint:
        yy = year_hint
    else:
        raise GaugerParseError(f"date {text!r} has no year and the order gives no year")
    return date(yy, mo, d)


def _parse_date_range(text: str, year_hint: Optional[int]) -> tuple[date, date]:
    parts = re.split(r"\s*[-–]\s*", text.strip())
    if len(parts) != 2:
        raise GaugerParseError(f"unreadable date range {text!r}")
    a = _parse_mdy(parts[0], year_hint)
    b = _parse_mdy(parts[1], year_hint)
    if b < a:  # a range that crosses New Year without a year on the cells
        b = date(b.year + 1, b.month, b.day)
    return a, b


def _rows(words: list[dict]) -> list[list[dict]]:
    """Cluster words into visual rows on their raw `top` (never round())."""
    out: list[list[dict]] = []
    for w in sorted(words, key=lambda w: (w["top"], w["x0"])):
        if out and abs(w["top"] - out[-1][0]["top"]) <= _ROW_TOL:
            out[-1].append(w)
        else:
            out.append([w])
    for r in out:
        r.sort(key=lambda w: w["x0"])
    return out


def _row_text(row: list[dict]) -> str:
    return " ".join(w["text"] for w in row)


def _header_field(text: str, label: str) -> str:
    m = re.search(rf"{re.escape(label)}\s*:?\s*(.+)", text)
    return m.group(1).strip() if m else ""


# ─── table ────────────────────────────────────────────────────────────────────


def _find_header(rows: list[list[dict]]) -> tuple[int, dict[str, float]]:
    """Index of the header row and {column label: header x0}. Raises when a column
    is missing: a renamed column must fail, not fall back to a position."""
    for i, row in enumerate(rows):
        labels = {w["text"]: w["x0"] for w in row}
        if sum(c in labels for c in _TABLE_COLUMNS) >= 5:
            missing = [c for c in _TABLE_COLUMNS if c not in labels]
            if missing:
                raise GaugerParseError(f"table header is missing column(s) {missing}")
            return i, {c: labels[c] for c in _TABLE_COLUMNS}
    raise GaugerParseError("no Dates/Days/Time/.../Spots/Total table header found")


def _cells(row: list[dict], anchors: dict[str, float]) -> dict[str, str]:
    """A word belongs to the LAST column whose header starts at or left of the word's
    centre (text is left-aligned under its header, numbers are right-aligned but their
    centre still sits right of the header)."""
    cols = sorted(anchors.items(), key=lambda kv: kv[1])
    out: dict[str, list[str]] = {c: [] for c in anchors}
    for w in row:
        cx = (w["x0"] + w["x1"]) / 2.0 + 3.0
        owner = cols[0][0]
        for name, x in cols:
            if x <= cx:
                owner = name
        out[owner].append(w["text"])
    return {c: " ".join(v).strip() for c, v in out.items()}


def _table_rows(rows: list[list[dict]]) -> list[dict[str, str]]:
    hi, anchors = _find_header(rows)
    out: list[dict[str, str]] = []
    for row in rows[hi + 1 :]:
        text = _row_text(row)
        if text.upper().startswith("TOTAL SPOTS"):
            break
        cells = _cells(row, anchors)
        if not re.match(r"\d{1,2}/\d{1,2}", cells["Dates"]):
            continue  # a wrapped program name or stray line; the Dates cell anchors a row
        out.append(cells)
    if not out:
        raise GaugerParseError("table header found but no schedule rows under it")
    return out


# ─── Desc block ───────────────────────────────────────────────────────────────

_DESC_LINE = re.compile(
    r"^\s*(?P<name>[A-Za-z][A-Za-z/&' ]+?):\s+(?P<rest>.*?\b(?P<spots>\d+)\s+spots?\b.*)$"
)


def _desc_entries(lines: list[str]) -> list[dict]:
    """'Hindi News and Variety: Monday -Friday 1p-2p total 21 spots (over 3 weeks)' →
    {name, spots, bonus}. The Desc block is the only place the full program names live."""
    out = []
    for ln in lines:
        m = _DESC_LINE.match(ln)
        if not m:
            continue
        name, rest = m.group("name").strip(), m.group("rest")
        if name.lower().endswith("paid spots") or "net cost" in rest.lower():
            continue
        out.append({"name": name, "spots": int(m.group("spots")), "bonus": "bonus" in rest.lower()})
    return out


def _program_name(cell: str, spots: int, is_bonus: bool, entries: list[dict]) -> str:
    """The table's Program cell, completed from the Desc block when exactly one Desc
    entry carries the same spot count and bonus flag."""
    printed = re.sub(r"\s*\.\.\.$", "", cell).strip()
    printed = re.sub(r"\s+bonus$", "", printed, flags=re.I).strip()
    hits = [e for e in entries if e["spots"] == spots and e["bonus"] == is_bonus]
    if len(hits) == 1:
        return hits[0]["name"]
    return printed


# ─── parse ────────────────────────────────────────────────────────────────────


def parse_gauger(path: str) -> GaugerOrder:
    with pdfplumber.open(path) as pdf:
        words: list[dict] = []
        texts: list[str] = []
        for pg in pdf.pages:
            words.extend(pg.extract_words(y_tolerance=1.0))
            texts.append(pg.extract_text() or "")
    text = "\n".join(texts)
    if not is_gauger_text(text):
        raise GaugerParseError("not a Gauger + Associates Broadcast Order")
    lines = [ln.strip() for ln in text.splitlines()]
    joined = " ".join(lines)

    order_number = (
        _header_field(joined, "Order number").split()[0] if "Order number" in joined else ""
    )
    if not order_number.isdigit():
        raise GaugerParseError(f"order number not found ({order_number!r})")
    m = re.search(r"Order date\s+(\d{1,2}/\d{1,2}/\d{2,4})", joined)
    order_date = _parse_mdy(m.group(1), None) if m else None
    year_hint = order_date.year if order_date else None
    m = re.search(r"Broadcast month:\s*([A-Za-z]+\s+(\d{4}))", joined)
    broadcast_month = m.group(1).strip() if m else ""
    if m:
        year_hint = int(m.group(2))

    client_code = (re.search(r"Client:\s*([A-Z0-9]+)", joined) or [None, ""])[1]
    job = (re.search(r"Job:\s*(.+?)\s+Attn:", joined) or [None, ""])[1].strip()
    market_name = (
        re.search(r"Market:\s*([A-Za-z .]+?)(?:\s+[A-Z][a-z]+,\s*[A-Z]{2}\b|\s+Product:|$)", joined)
        or [None, ""]
    )[1].strip()
    market_code = ""
    for key, code in _MARKET_BY_NAME.items():
        if key in market_name.lower():
            market_code = code
            break
    ad_name = (re.search(r"Ad name:\s*(.+?)(?:\s+Desc:|$)", joined) or [None, ""])[1].strip()
    revision = (
        re.search(r"REVISION:\s*([A-Za-z0-9 ]+?)(?:\s{2,}|\s+Flight|$)", joined) or [None, ""]
    )[1].strip()
    m = re.search(r"Product:\s*.+?:(\d{2,3})\b", joined)
    length_sec = int(m.group(1)) if m else 30
    email = (re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", joined) or [None, ""])[0] or ""
    contact = ""
    for i, ln in enumerate(lines):
        if ln.startswith("***PLEASE CONFIRM") and i + 1 < len(lines):
            # the signer's name is interleaved with the disclaimer lines
            for cand in lines[i + 1 : i + 4]:
                if re.fullmatch(r"[A-Z][a-z]+(?: [A-Z][a-z]+)+", cand.strip()):
                    contact = cand.strip()
                    break
            break

    m = re.search(r"(\d+)\s+paid spots:\s*Net cost\s*\$?([\d,]+(?:\.\d{2})?)", joined, re.I)
    paid_spots_stated = int(m.group(1)) if m else 0
    m = re.search(r"TOTAL SPOTS:\s*(\d+)", joined)
    total_spots_stated = int(m.group(1)) if m else 0
    m = re.search(r"NET:\s*\$([\d,]+\.\d{2})", joined)
    net_total_stated = _money(m.group(1)) if m else None
    if net_total_stated is None:
        raise GaugerParseError("NET total not found on the order")

    entries = _desc_entries(lines)
    parsed: list[GaugerLine] = []
    for cells in _table_rows(_rows(words)):
        d_from, d_to = _parse_date_range(cells["Dates"], year_hint)
        days = normalize_days(cells["Days"])
        spots = int(cells["Spots"]) if cells["Spots"].isdigit() else None
        if spots is None:
            raise GaugerParseError(
                f"unreadable Spots cell {cells['Spots']!r} on row {cells['Dates']} {cells['Program']}"
            )
        total = _money(cells["Total"])
        # Only the Program cell makes a row bonus; a paid row missing its Total refuses.
        is_bonus = bool(re.search(r"\bbonus\b", cells["Program"], re.I))
        if is_bonus and total:
            raise GaugerParseError(f"bonus row carries money: {cells}")
        if not is_bonus and total is None:
            raise GaugerParseError(f"paid row has no Total: {cells}")
        if not is_bonus:
            rate = round(total / spots, 2)
            if abs(rate * spots - total) > 0.005:
                raise GaugerParseError(
                    f"{cells['Program']}: ${total:,.2f} / {spots} spots is not a cent rate"
                )
        length = int(cells["Len"]) if cells["Len"].isdigit() else length_sec
        parsed.append(
            GaugerLine(
                date_from=d_from,
                date_to=d_to,
                days=days,
                time=cells["Time"].replace(" ", ""),
                program=_program_name(cells["Program"], spots, is_bonus, entries),
                program_cell=cells["Program"],
                length_sec=length,
                spots=spots,
                net_total=0.0 if is_bonus else float(total),
                is_bonus=is_bonus,
            )
        )

    order = GaugerOrder(
        order_number=order_number,
        order_date=order_date,
        client_code=client_code,
        job=job,
        market_name=market_name,
        market_code=market_code,
        ad_name=ad_name,
        broadcast_month=broadcast_month,
        length_sec=length_sec,
        contact=contact,
        email=email,
        revision=revision,
        lines=parsed,
        paid_spots_stated=paid_spots_stated,
        total_spots_stated=total_spots_stated,
        net_total_stated=net_total_stated,
    )

    # Reconcile three ways against the order's own arithmetic — RAISE, never enter short.
    paid_spots = sum(ln.spots for ln in order.paid_lines)
    if paid_spots_stated and paid_spots != paid_spots_stated:
        raise GaugerParseError(
            f"paid spots {paid_spots} in the table vs '{paid_spots_stated} paid spots' in the Desc"
        )
    if total_spots_stated and order.total_spots != total_spots_stated:
        raise GaugerParseError(
            f"table spots {order.total_spots} vs TOTAL SPOTS {total_spots_stated}"
        )
    if abs(order.net_total - net_total_stated) > 0.005:
        raise GaugerParseError(
            f"line totals ${order.net_total:,.2f} vs NET ${net_total_stated:,.2f}"
        )
    if not order.paid_lines:
        raise GaugerParseError("no paid lines")
    return order


if __name__ == "__main__":  # pragma: no cover
    import sys

    o = parse_gauger(sys.argv[1])
    print(
        f"Order {o.order_number}  {o.client_code} {o.job}  {o.market_name} ({o.market_code})  "
        f"ad {o.ad_name!r}  {o.broadcast_month}  :{o.length_sec}  {o.revision}"
    )
    print(f"Flight {o.flight_start} → {o.flight_end}   contact {o.contact} <{o.email}>")
    for ln in o.lines:
        print(
            f"  {ln.date_from}–{ln.date_to} {ln.description:<40} :{ln.length_sec} "
            f"{ln.spots:>3} spots  net ${ln.net_rate:>7.2f}  ${ln.net_total:>9,.2f}   [{ln.program_cell}]"
        )
    print(
        f"  {sum(x.spots for x in o.paid_lines)} paid + {sum(x.spots for x in o.bonus_lines)} bonus "
        f"= {o.total_spots} spots   NET ${o.net_total:,.2f}"
    )
