"""
San Mateo County Voters — Charmaine's house "Media Campaign" proposal PDF.

One page. Header: "<Client> Media Campaign" / "<Title>" / Client: / Phone: / Email:,
then the flight ("10/5 through 11/2") and the spot length (":15 seconds TV spot"),
then one grid:

    Insertion | Time | Cost (:15seconds TV spot) | <week columns "5-Oct" …> | Units | Airtime total

Paid rows carry a Time (daypart) and a Cost; bonus rows say "ROS Bonus" in the Time
column with "$ -". The Insertion label wraps onto the row ABOVE (or two rows) and so
can the Time ("M-F 1p-2p & Sat-Sun" / "1p-4p"). Footer rows: Paid (per-week units,
total units, airtime $), Bonuses, Airtime cost, one row per non-airtime charge
("Editing $ 375.00" → the Production box on the first paid line, never airtime), and
Total Amount. Nothing below Total Amount (impressions, signature, creative notes) is
read.

Read by WORD COORDINATES: cells map to the header column whose x-centre is nearest.
A currency cell spans from its "$" to its amount (Excel's accounting format prints
them apart), so its centre sits under the column header. Every total the sheet
prints is reconciled and a mismatch RAISES. The sheet carries no year — it comes
from the file name, else from the Monday rule on the first week column.

Detection is client-keyed ("San Mateo County Voters" — NOT "San Mateo County
Environmental Health Program", a different ANAGRAF customer via an agency).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import List, Optional

import pdfplumber

CLIENT_NAME = "San Mateo County Voters"
_LABEL = "San Mateo"

_MONTHS = {
    m: i
    for i, m in enumerate(
        ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), 1
    )
}
_WEEK_HDR = re.compile(r"^(\d{1,2})-([A-Za-z]{3})$")
_NUM = re.compile(r"^-?[\d,]*\.?\d+$")


def is_sanmateo_text(text: str) -> bool:
    return CLIENT_NAME.lower() in " ".join((text or "").split()).lower()


def tidy_label(text: str) -> str:
    """'Chinese ( Mandarin)' → 'Chinese (Mandarin)'; 'Chinese(Cantonese) News' →
    'Chinese (Cantonese) News'; 'Filipino News/ Drama/Variety' → 'Filipino News/Drama/Variety'."""
    t = " ".join((text or "").split())
    t = re.sub(r"\(\s+", "(", t)
    t = re.sub(r"\s+\)", ")", t)
    t = re.sub(r"(?<=\w)\(", " (", t)
    t = re.sub(r"\s*/\s*", "/", t)
    return t


# ─── Data model (bridge-compatible with the HPSJ shape) ──────────────────────


@dataclass
class SanMateoLine:
    insertion: str  # "Chinese (Cantonese) News" | "Chinese" (bonus)
    time_raw: str  # "M-F 7p-8p" | "M-F 1p-2p & Sat-Sun 1p-4p" | "ROS Bonus"
    cost: float  # the billed rate; 0 on bonus
    is_bonus: bool
    length_sec: int
    week_dates: List[date]
    week_spots: List[int]
    units: int  # sheet's Units cell
    total_cost: float  # sheet's Airtime total cell

    @property
    def rate(self) -> float:
        return 0.0 if self.is_bonus else self.cost

    @property
    def total_spots(self) -> int:
        return sum(self.week_spots)

    @property
    def base_language(self) -> str:
        from browser_automation.parsers.hpsj_parser import base_language

        return base_language(self.insertion)

    @property
    def language(self) -> str:
        return self.base_language

    @property
    def daypart(self) -> str:
        return "ROS" if self.is_bonus else self.time_raw

    def day_time(self) -> tuple[str, str]:
        """('M-Su', '1p-2p; 1p-4p') — one Etere line on the union (Lee's HPSJ rule)."""
        from browser_automation.parsers.hpsj_parser import daypart_union

        try:
            return daypart_union(self.time_raw)
        except ValueError as exc:
            raise ValueError(f"{_LABEL}: '{self.insertion}' {exc}") from None

    # Aliases for the generic parser_bridge normalizer (web preview)
    @property
    def description(self) -> str:
        return f"{self.insertion} {self.daypart}"

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
        return "M-Su" if self.is_bonus else self.day_time()[0]

    @property
    def time(self) -> str:
        return "ROS" if self.is_bonus else self.day_time()[1]


@dataclass
class SanMateoCharge:
    """Non-airtime money (Editing / Translation …). Production box on the first paid line."""

    description: str
    amount: float


@dataclass
class SanMateoOrder:
    title: str  # "General Election"
    campaign: str  # "San Mateo County Voters Media Campaign"
    client: str
    phone: str
    email: str
    length_sec: int
    market_code: str = "SFO"
    flight_start_date: Optional[date] = None
    flight_end_date: Optional[date] = None
    lines: List[SanMateoLine] = field(default_factory=list)
    charges: List[SanMateoCharge] = field(default_factory=list)
    rates_are_net: bool = False  # direct customer, 0% — gross == net
    paid_units_stated: Optional[int] = None
    paid_units_by_week: List[int] = field(default_factory=list)
    paid_cost_stated: Optional[float] = None
    bonus_units_stated: Optional[int] = None
    bonus_units_by_week: List[int] = field(default_factory=list)
    airtime_cost_stated: Optional[float] = None
    grand_total_stated: Optional[float] = None
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
    def paid_lines(self) -> List[SanMateoLine]:
        return [ln for ln in self.lines if not ln.is_bonus]

    @property
    def bonus_lines(self) -> List[SanMateoLine]:
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


# ─── Word geometry ───────────────────────────────────────────────────────────

ROW_TOL = 3.0  # data rows are ≥ 8pt apart; one logical row's cells jitter ≤ 1.5pt
JOIN_GAP = 8.0  # words of one cell sit 0-6pt apart; columns are ≥ 20pt apart
CURRENCY_GAP = 80.0  # a "$" and its amount sit 24-56pt apart; the next column is ≥ 100pt away


def _rows(words: list) -> list[list]:
    """Cluster words into visual rows on RAW `top` (never round: .5 boundaries
    manufacture phantom rows)."""
    out: list[list] = []
    for w in sorted(words, key=lambda w: (w["top"], w["x0"])):
        if out and w["top"] - out[-1][-1]["top"] <= ROW_TOL:
            out[-1].append(w)
        else:
            out.append([w])
    return [sorted(r, key=lambda w: w["x0"]) for r in out]


def _cells(row: list) -> list[dict]:
    """Join adjacent word fragments into cells: numeric fragments split by
    kerning ('6 75.00'), multi-word labels ('Airtime total'), and a lone '$'
    with the amount it marks — the currency cell spans $…amount."""
    cells: list[dict] = []
    for w in row:
        t = w["text"]
        if not t.strip():
            continue
        if cells and w["x0"] - cells[-1]["x1"] < JOIN_GAP:
            prev = cells[-1]
            glue = (
                ""
                if (
                    _NUM.match(t.replace("$", ""))
                    and _NUM.match(prev["text"].replace("$", "").replace(" ", "") or "0")
                )
                else " "
            )
            prev["text"] = (prev["text"] + glue + t).strip()
            prev["x1"] = w["x1"]
            continue
        cells.append({"text": t, "x0": w["x0"], "x1": w["x1"], "top": w["top"]})
    merged: list[dict] = []
    for c in cells:
        if (
            merged
            and merged[-1]["text"] == "$"
            and c["text"].strip()
            and _money(c["text"]) is not None
            and c["x0"] - merged[-1]["x1"] < CURRENCY_GAP
        ):
            merged[-1] = {**c, "text": "$" + c["text"], "x0": merged[-1]["x0"]}
        else:
            merged.append(c)
    for c in merged:
        c["cx"] = (c["x0"] + c["x1"]) / 2
    return merged


def _money(text: str) -> Optional[float]:
    t = text.replace("$", "").replace(" ", "").strip()
    if t in ("-", ""):
        return 0.0
    if not _NUM.match(t):
        return None
    try:
        return float(t.replace(",", ""))
    except ValueError:
        return None


def _int(text: str) -> Optional[int]:
    t = text.replace(",", "").strip()
    return int(t) if t.isdigit() else None


def _nearest(anchors: dict, cx: float, tols: dict) -> Optional[str]:
    best, best_d = None, float("inf")
    for name, ax in anchors.items():
        d = abs(ax - cx)
        if d < best_d:
            best, best_d = name, d
    return best if best is not None and best_d <= tols[best] else None


# ─── Header text ─────────────────────────────────────────────────────────────


def _field(text: str, label: str) -> str:
    m = re.search(rf"^{label}\s*:\s*(.+)$", text, re.IGNORECASE | re.MULTILINE)
    return m.group(1).strip() if m else ""


def _flight_md(text: str) -> tuple[Optional[tuple[int, int]], Optional[tuple[int, int]]]:
    m = re.search(r"(\d{1,2})/(\d{1,2})\s+through\s+(\d{1,2})/(\d{1,2})", text, re.IGNORECASE)
    if not m:
        return None, None
    return (int(m.group(1)), int(m.group(2))), (int(m.group(3)), int(m.group(4)))


def _pick_year(first_week: tuple[int, int], source_path: str) -> int:
    """The sheet prints no year. Use the file name's 4-digit year when it has one,
    else the year nearest today in which the first week column is a Monday
    (a weekday shifts by one or two every year, so this is unambiguous)."""
    m = re.search(r"(20\d{2})", Path(source_path).stem)
    if m:
        candidates = [int(m.group(1))]
    else:
        y = date.today().year
        candidates = [y, y + 1, y - 1]
    month, day = first_week
    for year in candidates:
        if date(year, month, day).weekday() == 0:
            return year
    raise ValueError(
        f"{_LABEL}: first week column {day}-{month:02d} is not a Monday in {candidates} — "
        "check the sheet's dates (or name the file with the year)"
    )


# ─── Grid ────────────────────────────────────────────────────────────────────


def _week_dates(week_cells: list[dict], year: int) -> list[date]:
    """'5-Oct', '12-Oct', … → Mondays in ascending order (the columns are the
    only source of week identity; a hiatus week is simply absent)."""
    out: list[date] = []
    for c in week_cells:
        d, mon = _WEEK_HDR.match(c["text"]).groups()
        month = _MONTHS[mon.lower()]
        cand = date(year, month, int(d))
        if out and cand <= out[-1]:
            cand = date(year + 1, month, int(d))
            year += 1
        if cand.weekday() != 0:
            raise ValueError(f"{_LABEL}: week column {c['text']} is a {cand:%A}, not a Monday")
        out.append(cand)
    return out


def parse_sanmateo(pdf_path: str) -> SanMateoOrder:
    with pdfplumber.open(pdf_path) as pdf:
        page = pdf.pages[0]
        text = "\n".join(p.extract_text() or "" for p in pdf.pages)
        words = page.extract_words(y_tolerance=1.5, x_tolerance=1.5)

    if not is_sanmateo_text(text):
        raise ValueError(f"{_LABEL}: '{CLIENT_NAME}' not found — not a San Mateo proposal")

    head_lines = [ln.strip() for ln in text.split("\n") if ln.strip()]
    client_i = next((i for i, ln in enumerate(head_lines) if ln.lower().startswith("client")), None)
    if client_i is None:
        raise ValueError(f"{_LABEL}: no 'Client:' line")
    campaign = head_lines[0] if client_i > 0 else ""
    title = head_lines[client_i - 1] if client_i >= 2 else campaign
    m = re.search(r":\s*(\d{1,3})\s*seconds", text, re.IGNORECASE)
    if not m:
        raise ValueError(f"{_LABEL}: no ':NN seconds' spot length")
    length_sec = int(m.group(1))

    rows = [_cells(r) for r in _rows(words)]
    hdr_i = next(
        (i for i, r in enumerate(rows) if sum(1 for c in r if _WEEK_HDR.match(c["text"])) >= 2),
        None,
    )
    if hdr_i is None:
        raise ValueError(f"{_LABEL}: no week-column header row (d-Mon) found")
    hdr = rows[hdr_i]
    week_cells = [c for c in hdr if _WEEK_HDR.match(c["text"])]
    d0, mon0 = _WEEK_HDR.match(week_cells[0]["text"]).groups()
    year = _pick_year((_MONTHS[mon0.lower()], int(d0)), pdf_path)
    weeks = _week_dates(week_cells, year)

    f_start = f_end = None
    md_a, md_b = _flight_md(text)
    if md_a and md_b:
        f_start = date(year, *md_a)
        f_end = date(year, *md_b)
        if f_end < f_start:  # a flight that crosses New Year
            f_end = f_end.replace(year=year + 1)
        if f_start < weeks[0] - timedelta(days=200):
            f_start = f_start.replace(year=f_start.year + 1)
            f_end = f_end.replace(year=f_end.year + 1)

    order = SanMateoOrder(
        title=title,
        campaign=campaign,
        client=_field(text, "Client") or CLIENT_NAME,
        phone=_field(text, "Phone"),
        email=_field(text, "Email"),
        length_sec=length_sec,
        flight_start_date=f_start,
        flight_end_date=f_end,
        source_path=pdf_path,
    )

    anchors: dict = {}
    for c in hdr:
        t = c["text"].lower()
        if t.startswith("insertion"):
            anchors["insertion"] = c["cx"]
        elif t == "time":
            anchors["time"] = c["cx"]
        elif t.startswith("cost"):
            anchors["cost"] = c["cx"]
        elif t == "units":
            anchors["units"] = c["cx"]
        elif t.endswith("total"):
            anchors["total"] = c["cx"]
    for i, c in enumerate(week_cells):
        anchors[f"w{i}"] = c["cx"]
    missing = [k for k in ("insertion", "time", "cost", "units", "total") if k not in anchors]
    if missing:
        raise ValueError(f"{_LABEL}: header columns not found: {missing} — layout changed")
    # Each column owns the half-gap to its neighbours.
    order_x = sorted(anchors.items(), key=lambda kv: kv[1])
    tols: dict = {}
    for i, (name, x) in enumerate(order_x):
        gaps = []
        if i > 0:
            gaps.append(x - order_x[i - 1][1])
        if i + 1 < len(order_x):
            gaps.append(order_x[i + 1][1] - x)
        tols[name] = 0.5 * min(gaps)

    def place(row: list) -> tuple[dict, list[dict]]:
        by_col: dict = {}
        extra: list[dict] = []
        for c in row:
            col = _nearest(anchors, c["cx"], tols)
            if col is None:
                extra.append(c)
            elif col in by_col and col in ("insertion", "time"):
                # a wrapped label whose fragments sit a few points apart
                by_col[col] = {
                    **by_col[col],
                    "text": by_col[col]["text"] + " " + c["text"],
                    "x1": c["x1"],
                }
            elif col in by_col:
                raise ValueError(
                    f"{_LABEL}: two cells landed in column {col}: "
                    f"{by_col[col]['text']!r} / {c['text']!r}"
                )
            else:
                by_col[col] = c
        return by_col, extra

    lines: List[SanMateoLine] = []
    pending: list[dict] = []  # wrapped Insertion / Time fragments printed ABOVE a data row
    footer_from: Optional[int] = None
    for ri, row in enumerate(rows[hdr_i + 1 :], start=hdr_i + 1):
        joined = " ".join(c["text"] for c in row)
        low = joined.lower()
        if low.startswith("paid"):
            footer_from = ri
            break
        by_col, extra = place(row)
        week_vals = [by_col.get(f"w{i}") for i in range(len(weeks))]
        if not any(v is not None for v in week_vals):
            pending.extend(row)
            continue
        ins_parts = [by_col["insertion"]["text"]] if "insertion" in by_col else []
        time_parts = [by_col["time"]["text"]] if "time" in by_col else []
        above_ins = [
            c["text"]
            for c in pending
            if abs(c["cx"] - anchors["insertion"]) < abs(c["cx"] - anchors["time"])
        ]
        above_time = [
            c["text"]
            for c in pending
            if abs(c["cx"] - anchors["insertion"]) >= abs(c["cx"] - anchors["time"])
        ]
        ins_parts = above_ins + ins_parts
        time_parts = above_time + time_parts
        pending = []
        if extra:
            raise ValueError(
                f"{_LABEL}: unplaced cell(s) {[c['text'] for c in extra]} in row {joined!r}"
            )
        insertion = tidy_label(" ".join(ins_parts))
        time_raw = " ".join(" ".join(time_parts).split())
        is_bonus = "bonus" in time_raw.lower()
        cost = _money(by_col["cost"]["text"]) if "cost" in by_col else None
        if cost is None:
            raise ValueError(f"{_LABEL}: '{insertion}' has an unreadable Cost")
        if is_bonus and cost != 0:
            raise ValueError(f"{_LABEL}: bonus row '{insertion}' carries a cost of ${cost:,.2f}")
        if not is_bonus and cost <= 0:
            raise ValueError(f"{_LABEL}: paid row '{insertion}' has no Cost")
        if not is_bonus and not time_raw:
            raise ValueError(f"{_LABEL}: paid row '{insertion}' has no Time")
        spots = []
        for v in week_vals:
            n = _int(v["text"]) if v else 0
            if v and n is None:
                raise ValueError(f"{_LABEL}: '{insertion}' week cell {v['text']!r} is not a count")
            spots.append(n or 0)
        units = _int(by_col["units"]["text"]) if "units" in by_col else None
        tcost = _money(by_col["total"]["text"]) if "total" in by_col else None
        if units is None or tcost is None:
            raise ValueError(f"{_LABEL}: '{insertion}' is missing Units / Airtime total")
        lines.append(
            SanMateoLine(
                insertion=insertion,
                time_raw=time_raw,
                cost=cost,
                is_bonus=is_bonus,
                length_sec=length_sec,
                week_dates=list(weeks),
                week_spots=spots,
                units=units,
                total_cost=tcost,
            )
        )
    if footer_from is None:
        raise ValueError(f"{_LABEL}: no 'Paid' footer row")
    if pending:
        raise ValueError(f"{_LABEL}: label text with no data row: {[c['text'] for c in pending]!r}")

    for row in rows[footer_from:]:
        joined = " ".join(c["text"] for c in row)
        low = joined.lower()
        by_col, extra = place(row)
        week_vals = [by_col.get(f"w{i}") for i in range(len(weeks))]
        total = _money(by_col["total"]["text"]) if "total" in by_col else None
        if low.startswith("paid"):
            order.paid_units_by_week = [_int(v["text"]) if v else 0 for v in week_vals]
            order.paid_units_stated = _int(by_col["units"]["text"]) if "units" in by_col else None
            order.paid_cost_stated = total
        elif low.startswith("bonus"):
            order.bonus_units_by_week = [_int(v["text"]) if v else 0 for v in week_vals]
            order.bonus_units_stated = _int(by_col["units"]["text"]) if "units" in by_col else None
        elif low.startswith("airtime cost"):
            order.airtime_cost_stated = total
        elif low.startswith("total amount") or low.startswith("total"):
            order.grand_total_stated = total
            break
        elif "total" in by_col and by_col["total"]["text"].startswith("$"):
            label = " ".join(c["text"] for c in row if not c["text"].startswith("$")).strip()
            if total is None or not label:
                raise ValueError(f"{_LABEL}: unreadable charge row {joined!r}")
            order.charges.append(SanMateoCharge(description=label, amount=total))
        # anything else between the footers (blank spacer rows) is ignored
    order.lines = lines
    _reconcile(order)
    return order


def _reconcile(order: SanMateoOrder) -> None:
    def close(a: float, b: float) -> bool:
        return abs(a - b) <= 0.005

    if not order.lines:
        raise ValueError(f"{_LABEL}: no grid rows read")
    for ln in order.lines:
        if ln.total_spots != ln.units:
            raise ValueError(
                f"{_LABEL}: '{ln.insertion}' week cells sum to {ln.total_spots} but Units says {ln.units}"
            )
        if not close(ln.units * ln.cost, ln.total_cost):
            raise ValueError(
                f"{_LABEL}: '{ln.insertion}' {ln.units} × ${ln.cost:,.2f} = ${ln.units * ln.cost:,.2f} "
                f"but Airtime total says ${ln.total_cost:,.2f}"
            )
        if order.flight_end_date:
            for d, n in zip(ln.week_dates, ln.week_spots):
                if n and d > order.flight_end_date:
                    raise ValueError(
                        f"{_LABEL}: '{ln.insertion}' has spots in week {d:%m/%d}, after the flight end"
                    )
        if not ln.is_bonus:
            ln.day_time()  # raises on an unreadable daypart
    if not order.paid_lines:
        raise ValueError(f"{_LABEL}: no paid rows")
    n_weeks = len(order.week_dates)
    paid_wk = [sum(ln.week_spots[i] for ln in order.paid_lines) for i in range(n_weeks)]
    bonus_wk = [sum(ln.week_spots[i] for ln in order.bonus_lines) for i in range(n_weeks)]
    if (
        order.paid_units_stated is None
        or order.paid_units_by_week != paid_wk
        or order.paid_units_stated != sum(paid_wk)
    ):
        raise ValueError(
            f"{_LABEL}: paid rows per week {paid_wk} (={sum(paid_wk)}) != Paid row "
            f"{order.paid_units_by_week} (={order.paid_units_stated})"
        )
    if order.bonus_lines and (
        order.bonus_units_stated is None
        or order.bonus_units_by_week != bonus_wk
        or order.bonus_units_stated != sum(bonus_wk)
    ):
        raise ValueError(
            f"{_LABEL}: bonus rows per week {bonus_wk} (={sum(bonus_wk)}) != Bonuses row "
            f"{order.bonus_units_by_week} (={order.bonus_units_stated})"
        )
    paid_cost = round(sum(ln.total_cost for ln in order.paid_lines), 2)
    for name, stated in (
        ("Paid", order.paid_cost_stated),
        ("Airtime cost", order.airtime_cost_stated),
    ):
        if stated is None or not close(paid_cost, stated):
            raise ValueError(
                f"{_LABEL}: paid line totals ${paid_cost:,.2f} != sheet {name} ${stated}"
            )
    if not close(order.paid_total, paid_cost):
        raise ValueError(
            f"{_LABEL}: rate × spots ${order.paid_total:,.2f} != line totals ${paid_cost:,.2f}"
        )
    if order.grand_total_stated is None or not close(order.total_cost, order.grand_total_stated):
        raise ValueError(
            f"{_LABEL}: airtime ${paid_cost:,.2f} + charges ${order.production_total:,.2f} = "
            f"${order.total_cost:,.2f} != sheet Total Amount ${order.grand_total_stated}"
        )


if __name__ == "__main__":  # pragma: no cover
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    o = parse_sanmateo(sys.argv[1])
    print(
        o.campaign,
        "|",
        o.title,
        "|",
        o.client,
        "|",
        o.flight_start,
        "→",
        o.flight_end,
        f":{o.length_sec}s",
    )
    for ln in o.lines:
        print(
            f"  {'BNS' if ln.is_bonus else '   '} {ln.insertion:<36} {ln.time_raw:<28} "
            f"${ln.rate:>6.2f} {ln.week_spots} = {ln.units}  ${ln.total_cost:,.2f}"
        )
    for c in o.charges:
        print(f"  charge {c.description}: ${c.amount:,.2f}")
    print(
        f"  paid ${o.paid_total:,.2f} + production ${o.production_total:,.2f} = ${o.total_cost:,.2f}"
    )
