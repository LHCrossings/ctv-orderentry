"""
Harrah's Northern California — Charmaine's house "Crossings TV Media Proposal"
workbook with a MONTHLY ESTIMATE grid (direct client, Central Valley).

Harrah's Northern California buys direct (ANAGRAF 485, no agency, 0%,
Broadcast). The signed copy the client returns is a scanned image with no text
layer, so the reader takes Charmaine's ORIGINAL workbook (`Crossings TV
Proposal_Harrah's Northern California_Option 1.xlsx`) — never the scan.

Shape of the sheet (one tab, "OPTION 1"; cells by label, never by address):

    OPTION 1
    Crossings TV Media Proposal
    Market          Central Valley of California      Contact:  Forest Chen
    Client          Harrah's Northen California       Email:    ...
    AE Email        Charmaine.Lane@crossingstv.com    Phone:    ...
    Flight schedule Monthly Estimate                  Address:  4640 Coal Mine Rd | Ione, CA 95640
    Date            10/1/2026                         Market    Central Valley
      Xfinity TV 398 (Central Valley of California) and KBTV 8.2(Sacramento)
    Language Block | Day Part/Program | Net | Promo | Length | Monthly | Total Spots | Total Value | NET
    Cantonese News/Talk & Mandarin News | M-F 7p-9p | 51 | 46 | :30s | 25 | 25 | 1275 | 1150
    ...
    BONUS | Hmong | Sat-Sun 6p-8p | 42.5 | 36 | :30s | 12 | 12 | 510 | 0
    BONUS | Cantonese | Cantonese ROS Bonus | 34 | 0 | :30s | 10 | 10 | 340 | 0
    Total Paid        66 | 66 | 4836.5 | 2560        ← Σ paid spots, Σ ALL rows' value, Σ paid NET
    Total Bonuses     52 | 52
    Discount rate     0.4707                          ← 1 − Σ paid NET / Σ value
     6 months contract:
    *Hmong Ad Placement bonus
    Optional: ... voiceover / donut prices ...

Money basis: `Net` is the rate-card rate (× Monthly = Total Value), `Promo` is
what the client pays (× Monthly = the row's NET). PROMO is the entered rate; a
direct 0% client makes gross == net. A row flagged BONUS in the margin enters at
$0 (booking code 10) even when it prints a Promo rate (the Hmong "Ad Placement
bonus" row does).

The grid is a MONTHLY estimate: every count is per broadcast month, and the
sheet's "6 months contract" note is the only statement of the flight length.
The automation asks for the start date and the month count and writes one
Rotation line per row per broadcast month (the 2023 Harrah's SoCal precedent,
contract 833).

Reconciliation is the sheet's own arithmetic and RAISES on any mismatch:
per row Monthly == Total Spots, Net × Monthly == Total Value, Promo × Monthly ==
NET (bonus rows NET == 0); the Total Paid / Total Bonuses footers; the Discount
rate. An unreadable amount is None, never 0.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, List, Optional

CLIENT_NAME = "Harrah's Northern California"

_MARKET_MAP = {
    "central valley": "CVC",
    "sacramento": "CVC",
    "san francisco": "SFO",
    "los angeles": "LAX",
    "seattle": "SEA",
    "houston": "HOU",
    "chicago": "CMP",
    "minneapolis": "CMP",
    "washington": "WDC",
    "new york": "NYC",
    "dallas": "DAL",
    "national": "MMT",
}

_LEN_RE = re.compile(r":?\s*(\d{1,3})\s*(?:s\b|sec)", re.I)  # ":30s"
_TIME_RE = re.compile(r"\d{1,2}(?::\d{2})?\s*[ap]\s*-\s*\d{1,2}(?::\d{2})?\s*[ap]", re.I)
_DAY_TOK = r"(?:Mon|Tue|Tu|Wed|Thu|Th|Fri|Sat|Sa|Sun|Su|M|W|F)"
_DAYS_RE = re.compile(rf"^\s*({_DAY_TOK})\s*-\s*({_DAY_TOK})\b\s*(.*)$", re.I)
_MONTHS_RE = re.compile(r"(\d{1,2})\s*months?\s*contract", re.I)

_DAY_CANON = {
    "mon": "M",
    "m": "M",
    "tue": "Tu",
    "tu": "Tu",
    "wed": "W",
    "w": "W",
    "thu": "Th",
    "th": "Th",
    "fri": "F",
    "f": "F",
    "sat": "Sa",
    "sa": "Sa",
    "sun": "Su",
    "su": "Su",
}

_LANGS = (
    "vietnamese",
    "mandarin",
    "cantonese",
    "chinese",
    "korean",
    "filipino",
    "tagalog",
    "japanese",
    "hmong",
    "punjabi",
    "south asian",
    "hindi",
)


class HarrahsParseError(ValueError):
    """The workbook could not be read or does not foot — refuse to enter."""


# ─── Data model ──────────────────────────────────────────────────────────────


@dataclass
class HarrahsLine:
    language_block: str  # "Cantonese News/Talk & Mandarin  News", "Hmong"
    program: str  # "M-F 7p-9p", "Sat-Sun 6p-8p", "Cantonese ROS Bonus"
    value_rate: Optional[float]  # the Net column — rate-card value per spot
    rate: float  # the Promo rate as ENTERED: 0 for a bonus row
    length_sec: int
    monthly_spots: int
    stated_total_spots: int  # the sheet's Total Spots cell (== monthly on an estimate)
    value_total: Optional[float]
    net_total: Optional[float]
    is_bonus: bool
    promo_printed: Optional[float] = None  # the Promo cell as printed (bonus rows may show one)
    sheet_row: int = 0

    # ── derived ──
    @property
    def is_ros(self) -> bool:
        return "ros" in self.program.lower()

    @property
    def days(self) -> str:
        m = _DAYS_RE.match(self.program)
        return _canon_days(m.group(1), m.group(2)) if m else ""

    @property
    def time_text(self) -> str:
        m = _TIME_RE.search(self.program)
        return m.group(0).replace(" ", "") if m else ""

    @property
    def language(self) -> str:
        """House language of the row. Cantonese AND Mandarin on one row → 'Chinese'
        (the two blocks together are the Chinese schedule — Snoqualmie precedent)."""
        low = self.language_block.lower()
        if "mandarin" in low and "cantonese" in low:
            return "Chinese"
        for lang in _LANGS:
            if lang in low:
                return "South Asian" if lang == "south asian" else lang.title()
        return ""

    @property
    def program_short(self) -> str:
        """'Cantonese News/Talk & Mandarin News' → 'News/Talk'; 'Vietnamese
        News/Variety' → 'News/Variety'; 'Hmong' → ''."""
        p = " ".join(self.language_block.split())
        p = re.sub(r"\s*/\s*", "/", p)
        if "&" in p and self.language == "Chinese":
            p = p.split("&")[0]
        for lang in _LANGS:
            p = re.sub(re.escape(lang), "", p, flags=re.I)
        p = " ".join(p.split()).strip("& ").strip()
        return "/".join(s[:1].upper() + s[1:] for s in p.split("/")) if p else ""

    # ── aliases for the web preview normalizer (parser_bridge._normalize_line) ──
    @property
    def description(self) -> str:
        if self.is_bonus:
            if self.is_ros:
                return f"BNS {self.language or self.language_block} ROS"
            return f"BNS {self.days} {self.time_text} {self.language}".strip()
        return f"{self.days} {self.time_text} {self.language} {self.program_short}".strip()

    @property
    def total_spots(self) -> int:
        return self.monthly_spots

    @property
    def length(self) -> int:
        return self.length_sec

    @property
    def time(self) -> str:
        return self.time_text

    @property
    def duration(self) -> int:
        return self.length_sec


@dataclass
class HarrahsOrder:
    client: str
    contact: str
    email: str
    phone: str
    address: str
    market_label: str
    market_code: str
    flight_label: str  # "Monthly Estimate"
    proposal_date: Optional[date]
    option: str  # "OPTION 1"
    length_sec: int
    months: Optional[int]  # from the "6 months contract" note; None when absent
    lines: List[HarrahsLine] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)
    stated_paid_spots: int = 0
    stated_bonus_spots: int = 0
    stated_value_total: Optional[float] = None
    stated_net_total: Optional[float] = None
    stated_discount_rate: Optional[float] = None
    rates_are_net: bool = True  # direct 0% — the PROMO rate is what is billed
    source_path: str = ""

    @property
    def advertiser(self) -> str:
        return self.client

    @property
    def market(self) -> str:
        return self.market_code

    @property
    def paid_lines(self) -> List[HarrahsLine]:
        return [ln for ln in self.lines if not ln.is_bonus]

    @property
    def bonus_lines(self) -> List[HarrahsLine]:
        return [ln for ln in self.lines if ln.is_bonus]

    @property
    def net_per_month(self) -> float:
        return round(sum(ln.rate * ln.monthly_spots for ln in self.paid_lines), 2)

    @property
    def paid_spots_per_month(self) -> int:
        return sum(ln.monthly_spots for ln in self.paid_lines)

    @property
    def bonus_spots_per_month(self) -> int:
        return sum(ln.monthly_spots for ln in self.bonus_lines)

    # the flight is decided at gather (start date + months); nothing on the sheet
    @property
    def flight_start(self) -> str:
        return ""

    @property
    def flight_end(self) -> str:
        return ""


# ─── Cell helpers ────────────────────────────────────────────────────────────


def _s(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return " ".join(str(v).split())


def _norm_label(v: Any) -> str:
    return _s(v).lower().rstrip(":").strip()


def _money(v: Any) -> Optional[float]:
    """A money cell: number → float; '-' / '' / None → None (never 0)."""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return round(float(v), 4)
    t = _s(v).replace("$", "").replace(",", "").strip()
    if t in ("", "-"):
        return None
    try:
        return round(float(t), 4)
    except ValueError:
        return None


def _int(v: Any) -> Optional[int]:
    if v is None or _s(v) == "":
        return None
    try:
        f = float(_s(v).replace(",", ""))
    except ValueError:
        return None
    return int(f) if f.is_integer() else None


def _canon_days(a: str, b: str) -> str:
    return f"{_DAY_CANON[a.lower()]}-{_DAY_CANON[b.lower()]}"


def _market_code(label: str) -> str:
    low = (label or "").lower()
    for k, v in _MARKET_MAP.items():
        if k in low:
            return v
    return ""


def _to_date(v: Any) -> Optional[date]:
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    m = re.search(r"(\d{1,2})/(\d{1,2})/(\d{2,4})", _s(v))
    if not m:
        return None
    y = int(m.group(3))
    y = y + 2000 if y < 100 else y
    return date(y, int(m.group(1)), int(m.group(2)))


def _right_of(row: list, idx: int) -> Any:
    """The first non-empty cell to the right of `idx`."""
    for v in row[idx + 1 :]:
        if _s(v) != "":
            return v
    return None


def _header_value(rows: List[list], label: str) -> Any:
    """The cell right of the first cell whose text is `label` (case-insensitive,
    trailing colon ignored). Labels can repeat ("Market" twice) — the first wins."""
    want = label.lower()
    for row in rows:
        for i, v in enumerate(row):
            if _norm_label(v) == want:
                return _right_of(row, i)
    return None


def _near(a: Optional[float], b: Optional[float], tol: float = 0.011) -> bool:
    return a is not None and b is not None and abs(a - b) <= tol


# ─── Reader ──────────────────────────────────────────────────────────────────


def parse_harrahs_rows(rows: List[list], source_path: str = "") -> HarrahsOrder:
    """Read the proposal from a list of row lists (one per sheet row, cell
    values as openpyxl returns them). Testable without a workbook."""
    rows = [list(r) for r in rows]

    client = _s(_header_value(rows, "Client"))
    if not client:
        raise HarrahsParseError("Client cell not found")
    market_label = _s(_header_value(rows, "Market"))
    market_code = _market_code(market_label)
    flight_label = _s(_header_value(rows, "Flight schedule"))
    proposal_date = _to_date(_header_value(rows, "Date"))
    option = ""
    for row in rows:
        for v in row:
            if re.fullmatch(r"option\s*\d+", _s(v), re.I):
                option = _s(v).upper()
                break
        if option:
            break

    # ── grid header by label ──
    hdr_i = hdr = None
    for i, row in enumerate(rows):
        labels = [_norm_label(v) for v in row]
        if "language block" in labels and "monthly" in labels:
            hdr_i, hdr = i, labels
            break
    if hdr is None:
        raise HarrahsParseError("grid header row (Language Block … Monthly) not found")

    def col(label: str, *, last: bool = False) -> int:
        hits = [i for i, v in enumerate(hdr) if v == label]
        if not hits:
            raise HarrahsParseError(f"grid header has no '{label}' column")
        return hits[-1] if last else hits[0]

    c_block = col("language block")
    c_prog = col("day part/program") if "day part/program" in hdr else col("day part/ program")
    c_net_rate = col("net")
    c_promo = col("promo")
    c_len = col("length")
    c_month = col("monthly")
    c_tot = col("total spots")
    c_value = col("total value")
    c_net_total = col("net", last=True)
    if c_net_total == c_net_rate or c_net_total < c_value:
        raise HarrahsParseError("grid header: expected a NET total column after Total Value")

    # ── data rows until "Total Paid" ──
    lines: List[HarrahsLine] = []
    footer_i = None
    for i in range(hdr_i + 1, len(rows)):
        row = rows[i] + [None] * (len(hdr) - len(rows[i]))
        block = _s(row[c_block])
        if block.lower().startswith("total paid"):
            footer_i = i
            break
        if not block and _s(row[c_prog]) == "":
            continue
        flag = any(_s(v).upper() == "BONUS" for v in row[:c_block])
        program = _s(row[c_prog])
        value_rate = _money(row[c_net_rate])
        promo = _money(row[c_promo])
        monthly = _int(row[c_month])
        stated_total = _int(row[c_tot])
        value_total = _money(row[c_value])
        net_total = _money(row[c_net_total])
        m_len = _LEN_RE.search(_s(row[c_len]))
        where = f"row {i + 1} '{block or program}'"
        if not m_len:
            raise HarrahsParseError(f"{where}: unreadable Length {_s(row[c_len])!r}")
        if monthly is None or stated_total is None:
            raise HarrahsParseError(f"{where}: unreadable Monthly / Total Spots")
        if monthly != stated_total:
            raise HarrahsParseError(
                f"{where}: Monthly {monthly} != Total Spots {stated_total} — not a monthly estimate"
            )
        if value_rate is None or value_total is None:
            raise HarrahsParseError(f"{where}: unreadable Net rate / Total Value")
        if not _near(value_rate * monthly, value_total):
            raise HarrahsParseError(
                f"{where}: Net {value_rate} × {monthly} = {value_rate * monthly:.2f} "
                f"!= Total Value {value_total}"
            )
        is_bonus = flag or (net_total is not None and net_total == 0 and (promo or 0) == 0)
        if is_bonus:
            if net_total not in (None, 0):
                raise HarrahsParseError(f"{where}: BONUS row carries NET {net_total}")
            rate = 0.0
        else:
            if promo is None:
                raise HarrahsParseError(f"{where}: paid row has no Promo rate")
            if net_total is None:
                raise HarrahsParseError(f"{where}: paid row has no NET total")
            if not _near(promo * monthly, net_total):
                raise HarrahsParseError(
                    f"{where}: Promo {promo} × {monthly} = {promo * monthly:.2f} != NET {net_total}"
                )
            rate = promo
        ln = HarrahsLine(
            language_block=block,
            program=program,
            value_rate=value_rate,
            rate=rate,
            length_sec=int(m_len.group(1)),
            monthly_spots=monthly,
            stated_total_spots=stated_total,
            value_total=value_total,
            net_total=net_total,
            is_bonus=is_bonus,
            promo_printed=promo,
            sheet_row=i + 1,
        )
        if not ln.language:
            raise HarrahsParseError(f"{where}: no language in the Language Block cell")
        if not ln.is_bonus or not ln.is_ros:
            if not ln.days or not ln.time_text:
                raise HarrahsParseError(
                    f"{where}: daypart {program!r} has no day pattern / time window"
                )
        lines.append(ln)
    if footer_i is None:
        raise HarrahsParseError("'Total Paid' footer not found")
    if not lines:
        raise HarrahsParseError("no grid rows between the header and 'Total Paid'")

    # ── footers ──
    paid = [ln for ln in lines if not ln.is_bonus]
    bonus = [ln for ln in lines if ln.is_bonus]
    frow = rows[footer_i] + [None] * (len(hdr) - len(rows[footer_i]))
    stated_paid = _int(frow[c_month])
    stated_paid_tot = _int(frow[c_tot])
    stated_value = _money(frow[c_value])
    stated_net = _money(frow[c_net_total])
    sum_paid = sum(ln.monthly_spots for ln in paid)
    sum_value_all = round(sum(ln.value_total or 0 for ln in lines), 2)
    sum_net = round(sum(ln.rate * ln.monthly_spots for ln in paid), 2)
    if stated_paid != sum_paid or stated_paid_tot != sum_paid:
        raise HarrahsParseError(
            f"Total Paid spots {stated_paid}/{stated_paid_tot} != Σ paid rows {sum_paid}"
        )
    if not _near(stated_value, sum_value_all):
        raise HarrahsParseError(
            f"Total Paid value {stated_value} != Σ Total Value of every row {sum_value_all:.2f}"
        )
    if not _near(stated_net, sum_net):
        raise HarrahsParseError(f"Total Paid NET {stated_net} != Σ paid NET {sum_net:.2f}")

    stated_bonus = None
    stated_rate = None
    notes: List[str] = []
    months = None
    for i in range(footer_i + 1, len(rows)):
        row = rows[i] + [None] * (len(hdr) - len(rows[i]))
        texts = [_s(v) for v in row if _s(v)]
        if not texts:
            continue
        first = texts[0].lower()
        if first.startswith("total bonus"):
            stated_bonus = _int(row[c_month])
            if stated_bonus is None or stated_bonus != sum(ln.monthly_spots for ln in bonus):
                raise HarrahsParseError(
                    f"Total Bonuses {stated_bonus} != Σ bonus rows "
                    f"{sum(ln.monthly_spots for ln in bonus)}"
                )
            continue
        if first.startswith("discount rate"):
            nums = [_money(v) for v in row if isinstance(v, (int, float))]
            stated_rate = nums[0] if nums else None
            continue
        if any(_norm_label(v) == "discount rate" for v in row):
            nums = [_money(v) for v in row if isinstance(v, (int, float))]
            stated_rate = nums[0] if nums else stated_rate
            continue
        for t in texts:
            if re.search(r"[A-Za-z]{3}", t) and not t.lower().startswith(
                ("approved by", "client signature", "billing address", "thank you")
            ):
                notes.append(t)
                m = _MONTHS_RE.search(t)
                if m:
                    months = int(m.group(1))
    if bonus and stated_bonus is None:
        raise HarrahsParseError("'Total Bonuses' footer not found")
    if stated_rate is not None and sum_value_all:
        expect = 1 - sum_net / sum_value_all
        if abs(stated_rate - expect) > 0.0005:
            raise HarrahsParseError(
                f"Discount rate {stated_rate:.4f} != 1 - {sum_net}/{sum_value_all} = {expect:.4f}"
            )

    lengths = {ln.length_sec for ln in lines}
    return HarrahsOrder(
        client=client,
        contact=_s(_header_value(rows, "Contact")),
        email=_s(_header_value(rows, "Email")),
        phone=_s(_header_value(rows, "Phone")),
        address=_s(_header_value(rows, "Address")),
        market_label=market_label,
        market_code=market_code,
        flight_label=flight_label,
        proposal_date=proposal_date,
        option=option,
        length_sec=max(lengths),
        months=months,
        lines=lines,
        notes=notes,
        stated_paid_spots=sum_paid,
        stated_bonus_spots=stated_bonus or 0,
        stated_value_total=stated_value,
        stated_net_total=stated_net,
        stated_discount_rate=stated_rate,
        source_path=source_path,
    )


def workbook_rows(path: str) -> List[list]:
    """Cell values of the OPTION sheet (else the first sheet) as row lists."""
    import openpyxl

    wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
    try:
        ws = next((s for s in wb.worksheets if s.title.strip().upper().startswith("OPTION")), None)
        ws = ws or wb.worksheets[0]
        return [list(r) for r in ws.iter_rows(values_only=True)]
    finally:
        wb.close()


def parse_harrahs(path: str) -> HarrahsOrder:
    p = Path(path)
    if p.suffix.lower() not in (".xlsx", ".xlsm"):
        raise HarrahsParseError(
            f"{p.name}: Harrah's proposals are read from Charmaine's workbook; "
            "the signed PDF is a scan with no text layer"
        )
    return parse_harrahs_rows(workbook_rows(str(p)), source_path=str(p))


def is_harrahs_text(text: str) -> bool:
    """Harrah's NORTHERN California on a Crossings TV Media Proposal (the sheet
    spells it 'Northen'). Harrah's Resort Southern California is ANAGRAF 223 via
    an agency and must never route here."""
    low = " ".join((text or "").split()).lower()
    return "harrah" in low and ("northern california" in low or "northen california" in low)


def is_harrahs_workbook(path: str) -> bool:
    try:
        rows = workbook_rows(path)[:12]
    except Exception:
        return False
    return is_harrahs_text(" ".join(_s(v) for r in rows for v in r))


if __name__ == "__main__":
    import sys

    o = parse_harrahs(sys.argv[1])
    print(f"{o.client} | {o.market_label} → {o.market_code} | {o.flight_label} | {o.option}")
    print(f"months={o.months}  :{o.length_sec}s  date={o.proposal_date}")
    for ln in o.lines:
        print(
            f"  {'BNS' if ln.is_bonus else '   '} {ln.description:<36} {ln.days:<5} "
            f"{ln.time_text:<8} promo={ln.rate:<6} net={ln.value_rate:<6} "
            f"{ln.monthly_spots:>3}/mo  value={ln.value_total}  NET={ln.net_total}"
        )
    print(
        f"  paid {o.paid_spots_per_month}/mo ${o.net_per_month:,.2f}/mo; "
        f"bonus {o.bonus_spots_per_month}/mo; discount {o.stated_discount_rate}"
    )
    for n in o.notes:
        print("  note:", n)
