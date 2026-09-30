"""
Health Plan of San Joaquin (HPSJ) — house "Crossings TV Media Proposal" PDF.

One page: a header table (Advertiser / Contact / Email / Phone / Address / Market /
DATE), an `Estimate- 10/5 THROUGH 12/7` flight line, then one grid:

    Language Block | Day Part/Program | Creative | Unit Value (Gross) | Promo Unit Cost (Gross)
    | <week columns "5-Oct" …> | Total Units | Total Value | Total Cost | Estimated Impressions

Paid rows carry a Promo Unit Cost (the billed rate; Unit Value is the undiscounted
gross value). Rows flagged BONUS in the margin carry "$ -" cost and a ROS daypart.
A "Translation Cost (…)" row is production money (→ the Production box on the first
paid line, never airtime). Footers: "Paid Units" (per week + total + total value +
total cost), "Bonus Units", "Discount rate".

Read by WORD COORDINATES, not extract_tables — pdfplumber merges five week cells
of the SOUTH ASIAN bonus row into one ("2 2 2 2 2"). Cells map to the header
column whose x-centre is nearest (Admerasia / Brand Time Schedule technique).
Every total the sheet prints is reconciled and a mismatch RAISES.

Detection is client-keyed ("Health Plan of San Joaquin"); the template is shared
with SCWA / San Joaquin County / Ntooitive and must never be the discriminator.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import List, Optional

import pdfplumber

CLIENT_NAME = "Health Plan of San Joaquin"

_MONTHS = {
    m: i
    for i, m in enumerate(
        ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), 1
    )
}
_WEEK_HDR = re.compile(r"^(\d{1,2})-([A-Za-z]{3})$")
_NUM = re.compile(r"^-?[\d,]*\.?\d+$")
_BASE_LANGUAGES = (
    "Vietnamese",
    "Filipino",
    "South Asian",
    "Chinese",
    "Mandarin",
    "Cantonese",
    "Korean",
    "Hmong",
    "Punjabi",
    "Japanese",
)


def is_hpsj_text(text: str) -> bool:
    return CLIENT_NAME.lower() in " ".join((text or "").split()).lower()


def base_language(text: str) -> str:
    low = " ".join((text or "").split()).lower()
    for lang in _BASE_LANGUAGES:
        if low.startswith(lang.lower()):
            return lang
    for lang in _BASE_LANGUAGES:
        if lang.lower() in low:
            return lang
    return " ".join((text or "").split())


# ─── Daypart union (one Etere line per proposal row) ──────────────────────────

_DAY_ORDER = ("lun", "mar", "mer", "gio", "ven", "sab", "dom")
_DAY_TOKEN = {"lun": "M", "mar": "T", "mer": "W", "gio": "Th", "ven": "F", "sab": "Sa", "dom": "Su"}


def _canonical_days(bits: dict) -> str:
    on = [bits.get(k, False) for k in _DAY_ORDER]
    if all(on):
        return "M-Su"
    if on == [True] * 5 + [False] * 2:
        return "M-F"
    if on == [True] * 6 + [False]:
        return "M-Sa"
    if on == [False] * 5 + [True] * 2:
        return "Sa-Su"
    return ",".join(_DAY_TOKEN[k] for k, v in zip(_DAY_ORDER, on) if v)


def daypart_union(daypart: str) -> tuple[str, str]:
    """'M-F 4p-7pm/ Sat- Sun 4p-6p' → ('M-Su', '4p-7pm; 4p-6p');
    'M-SUN 10A-11A & 12P-1P' → ('M-Su', '10A-11A; 12P-1P').

    Each '/'- or '&'-separated segment contributes its own day pattern (a
    segment without one inherits the previous) and its time range; days are
    OR-ed, times are joined with ';' so `EtereClient.parse_time_range` takes
    the earliest start and latest end — ONE Etere line per proposal row (Lee).
    """
    from browser_automation.etere_direct_client import parse_day_bits
    from browser_automation.ntooitive_automation import find_time_ranges

    dp = re.sub(r"\s*-\s*", "-", " ".join((daypart or "").split()))
    bits: dict = {k: False for k in _DAY_ORDER}
    times: list[str] = []
    for seg in re.split(r"\s*[/&]\s*", dp):
        ranges = find_time_ranges(seg)
        head = seg[: seg.find(ranges[0])].strip() if ranges else seg.strip()
        if head:
            seg_bits = parse_day_bits(head)
            if not any(seg_bits.values()):
                raise ValueError(f"HPSJ: unreadable day pattern {head!r} in {daypart!r}")
            for k, v in seg_bits.items():
                bits[k] = bits[k] or v
        times.extend(ranges)
    if not any(bits.values()):
        raise ValueError(f"HPSJ: no day pattern in {daypart!r}")
    if not times:
        raise ValueError(f"HPSJ: no time range in {daypart!r}")
    return _canonical_days(bits), "; ".join(times)


# ─── Data model (bridge-compatible with the SJ County shape) ─────────────────


@dataclass
class HPSJLine:
    insertion: str  # "Vietnamese News/Talk" | "Vietnamese" (bonus)
    daypart: str  # "M-SUN 11A-12P" | "M-SUN 10A-11A & 12P-1P" | "ROS"
    value: float  # Unit Value (Gross) — informational
    cost: float  # Promo Unit Cost (Gross) — the billed rate; 0 on bonus
    is_bonus: bool
    length_sec: int
    week_dates: List[date]
    week_spots: List[int]
    units: int  # sheet's Total Units cell
    total_value: float  # sheet's Total Value cell
    total_cost: float  # sheet's Total Cost cell

    @property
    def rate(self) -> float:
        return 0.0 if self.is_bonus else self.cost

    @property
    def total_spots(self) -> int:
        return sum(self.week_spots)

    @property
    def base_language(self) -> str:
        return base_language(self.insertion)

    @property
    def language(self) -> str:
        return self.base_language

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
        return "M-Su" if self.is_bonus else daypart_union(self.daypart)[0]

    @property
    def time(self) -> str:
        return "ROS" if self.is_bonus else daypart_union(self.daypart)[1]


@dataclass
class HPSJCharge:
    """Non-airtime money (translation). Production box on the first paid line."""

    description: str
    amount: float  # what the client pays (the Cost column)
    value: float = 0.0  # retail value printed beside it


@dataclass
class HPSJOrder:
    title: str
    client: str
    contact: str
    email: str
    phone: str
    market_code: str = "CVC"
    proposal_date: Optional[date] = None
    flight_start_date: Optional[date] = None
    flight_end_date: Optional[date] = None
    lines: List[HPSJLine] = field(default_factory=list)
    charges: List[HPSJCharge] = field(default_factory=list)
    rates_are_net: bool = False  # direct customer, 0% — gross == net
    paid_units_stated: Optional[int] = None
    paid_units_by_week: List[int] = field(default_factory=list)
    bonus_units_stated: Optional[int] = None
    bonus_units_by_week: List[int] = field(default_factory=list)
    grand_value_stated: Optional[float] = None
    grand_cost_stated: Optional[float] = None
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
    def paid_lines(self) -> List[HPSJLine]:
        return [ln for ln in self.lines if not ln.is_bonus]

    @property
    def bonus_lines(self) -> List[HPSJLine]:
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

ROW_TOL = 3.0  # rows are ~16pt apart; one logical row's cells jitter ≤ 1pt
JOIN_GAP = 3.5  # "2" + ",695.00" printed as two words 0-3pt apart


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
    kerning ('$ 2 ,695.00'), and multi-word labels ('Total Units')."""
    cells: list[dict] = []
    for w in row:
        t = w["text"]
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
    # a lone "$" is a currency marker for the next cell
    merged: list[dict] = []
    for c in cells:
        if merged and merged[-1]["text"] == "$":
            merged[-1] = {**c, "text": ("$" + c["text"]), "x0": c["x0"]}
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
    m = re.search(rf"{label}\s*:?\s*(.+)", text, re.IGNORECASE)
    return m.group(1).strip() if m else ""


def _mdy(s: str, year_hint: int) -> Optional[date]:
    m = re.match(r"^(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?$", s.strip())
    if not m:
        return None
    y = m.group(3)
    year = int(y) + (2000 if y and len(y) == 2 else 0) if y else year_hint
    return date(year, int(m.group(1)), int(m.group(2)))


def _parse_flight(text: str, year_hint: int) -> tuple[Optional[date], Optional[date]]:
    m = re.search(
        r"Estimate\s*-?\s*(\d{1,2}/\d{1,2}(?:/\d{2,4})?)\s+THROUGH\s+(\d{1,2}/\d{1,2}(?:/\d{2,4})?)",
        text,
        re.IGNORECASE,
    )
    if not m:
        return None, None
    a, b = _mdy(m.group(1), year_hint), _mdy(m.group(2), year_hint)
    if a and b and b < a:  # a flight that crosses New Year
        b = b.replace(year=b.year + 1)
    return a, b


# ─── Grid ────────────────────────────────────────────────────────────────────


def _week_dates(week_cells: list[dict], year_hint: int) -> list[date]:
    """'5-Oct', '12-Oct', … → Mondays in ascending order (the columns are the
    only source of week identity; a hiatus week is simply absent)."""
    out: list[date] = []
    year = year_hint
    for c in week_cells:
        d, mon = _WEEK_HDR.match(c["text"]).groups()
        month = _MONTHS[mon.lower()]
        cand = date(year, month, int(d))
        if out and cand <= out[-1]:
            cand = date(year + 1, month, int(d))
            year += 1
        if cand.weekday() != 0:
            raise ValueError(f"HPSJ: week column {c['text']} is a {cand:%A}, not a Monday")
        out.append(cand)
    return out


def parse_hpsj(pdf_path: str) -> HPSJOrder:
    with pdfplumber.open(pdf_path) as pdf:
        page = pdf.pages[0]
        text = "\n".join(p.extract_text() or "" for p in pdf.pages)
        words = page.extract_words(y_tolerance=1.5, x_tolerance=1.5)

    if not is_hpsj_text(text):
        raise ValueError("HPSJ: 'Health Plan of San Joaquin' not found — not an HPSJ proposal")

    title_m = re.search(r"Crossings TV Media Proposal\s*\n\s*(.+)", text)
    title = title_m.group(1).strip() if title_m else "Crossings TV Media Proposal"
    client = _field(text, "Advertiser") or CLIENT_NAME
    date_raw = _field(text, r"\bDATE\b")
    proposal_date = _mdy(date_raw.split()[0], date.today().year) if date_raw else None
    year_hint = proposal_date.year if proposal_date else date.today().year
    f_start, f_end = _parse_flight(text, year_hint)
    if f_start and proposal_date and f_start < proposal_date - timedelta(days=200):
        f_start = f_start.replace(year=f_start.year + 1)
        f_end = f_end.replace(year=f_end.year + 1) if f_end else None
    market_raw = _field(text, "Market")
    market_code = (
        "CVC"
        if "central valley" in market_raw.lower() or not market_raw
        else _market_code(market_raw)
    )

    order = HPSJOrder(
        title=title,
        client=client,
        contact=_field(text, "Contact"),
        email=_field(text, "Email"),
        phone=_field(text, "Phone"),
        market_code=market_code,
        proposal_date=proposal_date,
        flight_start_date=f_start,
        flight_end_date=f_end,
        source_path=pdf_path,
    )

    rows = [_cells(r) for r in _rows(words)]

    # Header row = the one carrying the week columns
    hdr_i = next(
        (i for i, r in enumerate(rows) if sum(1 for c in r if _WEEK_HDR.match(c["text"])) >= 2),
        None,
    )
    if hdr_i is None:
        raise ValueError("HPSJ: no week-column header row (d-Mon) found")
    hdr = rows[hdr_i]
    week_cells = [c for c in hdr if _WEEK_HDR.match(c["text"])]
    weeks = _week_dates(week_cells, year_hint)
    anchors: dict = {}
    for c in hdr:
        t = c["text"].lower()
        if t.startswith("language block"):
            anchors["lang"] = c["cx"]
        elif t in ("language", "impressions") or t.startswith("estimated"):
            anchors["impr"] = c["cx"]  # "Estimated Impressions by language" — ignored
        elif t.startswith("program") or t.startswith("day part"):
            anchors["daypart"] = c["cx"]
        elif t.startswith("creative"):
            anchors["creative"] = c["cx"]
        elif t == "(gross)":
            anchors["value" if "value" not in anchors else "cost"] = c["cx"]
        elif t.startswith("total units"):
            anchors["units"] = c["cx"]
        elif t.startswith("total value"):
            anchors["tvalue"] = c["cx"]
        elif t.startswith("total cost"):
            anchors["tcost"] = c["cx"]
    for i, c in enumerate(week_cells):
        anchors[f"w{i}"] = c["cx"]
    missing = [
        k
        for k in ("lang", "daypart", "creative", "value", "cost", "units", "tvalue", "tcost")
        if k not in anchors
    ]
    if missing:
        raise ValueError(f"HPSJ: header columns not found: {missing} — layout changed")
    # Each column owns the half-gap to its neighbours (a wide money cell's
    # centre drifts further from its header than a one-digit week cell's).
    order_x = sorted(anchors.items(), key=lambda kv: kv[1])
    tols: dict = {}
    for i, (name, x) in enumerate(order_x):
        gaps = []
        if i > 0:
            gaps.append(x - order_x[i - 1][1])
        if i + 1 < len(order_x):
            gaps.append(order_x[i + 1][1] - x)
        tols[name] = 0.5 * min(gaps)

    lines: List[HPSJLine] = []
    pending_label: list[dict] = []  # wrapped label fragments printed ABOVE a data row
    after_paid = False
    for row in rows[hdr_i + 1 :]:
        joined = " ".join(c["text"] for c in row)
        low = joined.lower()
        by_col: dict = {}
        extra_label: list[dict] = []
        for c in row:
            col = _nearest(anchors, c["cx"], tols)
            if col is None:
                # left margin BONUS marker / label text between anchors
                extra_label.append(c)
            elif col in by_col and col.startswith("w"):
                raise ValueError(
                    f"HPSJ: two cells landed in week column {col}: {by_col[col]['text']!r} / {c['text']!r}"
                )
            else:
                by_col.setdefault(col, c)
        week_vals = [by_col.get(f"w{i}") for i in range(len(weeks))]
        has_weeks = any(v is not None for v in week_vals)

        if low.startswith("paid units"):
            order.paid_units_by_week = [_int(v["text"]) if v else 0 for v in week_vals]
            order.paid_units_stated = _int(by_col["units"]["text"]) if "units" in by_col else None
            order.grand_value_stated = (
                _money(by_col["tvalue"]["text"]) if "tvalue" in by_col else None
            )
            order.grand_cost_stated = _money(by_col["tcost"]["text"]) if "tcost" in by_col else None
            after_paid = True
            continue
        if (
            after_paid
            and not has_weeks
            and ("tvalue" in by_col or "tcost" in by_col)
            and not extra_label
        ):
            # the sheet prints "$ 14,240.00  $ 7,500.00  2,320,679" on its own line under Paid Units
            if order.grand_value_stated is None and "tvalue" in by_col:
                order.grand_value_stated = _money(by_col["tvalue"]["text"])
            if order.grand_cost_stated is None and "tcost" in by_col:
                order.grand_cost_stated = _money(by_col["tcost"]["text"])
            after_paid = False
            continue
        if low.startswith("bonus units"):
            order.bonus_units_by_week = [_int(v["text"]) if v else 0 for v in week_vals]
            order.bonus_units_stated = _int(by_col["units"]["text"]) if "units" in by_col else None
            continue
        if (
            low.startswith("discount rate")
            or low.startswith("translation services")
            or low.startswith("translation fee")
            or low.startswith("(retail")
            or low.startswith("*")
            or low.startswith("thank")
        ):
            continue
        if "translation" in low and not has_weeks:
            amount = _money(by_col["tcost"]["text"]) if "tcost" in by_col else None
            value = _money(by_col["tvalue"]["text"]) if "tvalue" in by_col else None
            if amount is None:
                raise ValueError(f"HPSJ: translation row has no Total Cost: {joined!r}")
            label = " ".join(
                c["text"] for c in row if _money(c["text"]) is None and c["text"] not in ("n/a",)
            )
            order.charges.append(
                HPSJCharge(description=label.strip(), amount=amount, value=value or 0.0)
            )
            continue
        if not has_weeks:
            # a wrapped daypart/language fragment for the NEXT data row
            pending_label.extend(row)
            continue

        lang_parts = [by_col["lang"]["text"]] if "lang" in by_col else []
        dp_parts = [by_col["daypart"]["text"]] if "daypart" in by_col else []
        for c in pending_label:
            (
                lang_parts
                if abs(c["cx"] - anchors["lang"]) < abs(c["cx"] - anchors["daypart"])
                else dp_parts
            ).insert(0, c["text"])
        pending_label = []
        is_bonus = any(
            c["text"].upper() == "BONUS" and c["cx"] < anchors["lang"] for c in extra_label
        )
        stray = [c["text"] for c in extra_label if c["text"].upper() != "BONUS"]
        if stray:
            raise ValueError(f"HPSJ: unplaced cell(s) {stray} in row {joined!r}")
        insertion = " ".join(" ".join(lang_parts).split())
        daypart = " ".join(" ".join(dp_parts).split())
        creative = by_col["creative"]["text"] if "creative" in by_col else ""
        m = re.search(r":?(\d{1,3})\s*s", creative)
        if not m:
            raise ValueError(f"HPSJ: '{insertion}' has no spot length in Creative ({creative!r})")
        length = int(m.group(1))
        value = _money(by_col["value"]["text"]) if "value" in by_col else None
        cost = _money(by_col["cost"]["text"]) if "cost" in by_col else None
        if value is None or cost is None:
            raise ValueError(f"HPSJ: '{insertion}' has an unreadable Unit Value / Promo Unit Cost")
        if is_bonus and cost != 0:
            raise ValueError(f"HPSJ: BONUS row '{insertion}' carries a cost of ${cost:,.2f}")
        if not is_bonus and cost <= 0:
            raise ValueError(f"HPSJ: paid row '{insertion}' has no Promo Unit Cost")
        spots = []
        for v in week_vals:
            n = _int(v["text"]) if v else 0
            if v and n is None:
                raise ValueError(f"HPSJ: '{insertion}' week cell {v['text']!r} is not a count")
            spots.append(n or 0)
        units = _int(by_col["units"]["text"]) if "units" in by_col else None
        tvalue = _money(by_col["tvalue"]["text"]) if "tvalue" in by_col else None
        tcost = _money(by_col["tcost"]["text"]) if "tcost" in by_col else None
        if units is None or tvalue is None or tcost is None:
            raise ValueError(
                f"HPSJ: '{insertion}' is missing Total Units / Total Value / Total Cost"
            )
        lines.append(
            HPSJLine(
                insertion=insertion,
                daypart=daypart,
                value=value,
                cost=cost,
                is_bonus=is_bonus,
                length_sec=length,
                week_dates=list(weeks),
                week_spots=spots,
                units=units,
                total_value=tvalue,
                total_cost=tcost,
            )
        )
    order.lines = lines
    _reconcile(order)
    return order


def _market_code(raw: str) -> str:
    low = raw.lower()
    for key, code in (
        ("central valley", "CVC"),
        ("sacramento", "CVC"),
        ("san francisco", "SFO"),
        ("bay area", "SFO"),
        ("los angeles", "LAX"),
        ("seattle", "SEA"),
        ("houston", "HOU"),
        ("chicago", "CMP"),
        ("washington", "WDC"),
        ("new york", "NYC"),
        ("national", "MMT"),
    ):
        if key in low:
            return code
    return "CVC"


def _reconcile(order: HPSJOrder) -> None:
    def close(a: float, b: float) -> bool:
        return abs(a - b) <= 0.005

    if not order.lines:
        raise ValueError("HPSJ: no grid rows read")
    for ln in order.lines:
        if ln.total_spots != ln.units:
            raise ValueError(
                f"HPSJ: '{ln.insertion}' week cells sum to {ln.total_spots} but Total Units says {ln.units}"
            )
        if not close(ln.units * ln.value, ln.total_value):
            raise ValueError(
                f"HPSJ: '{ln.insertion}' {ln.units} × ${ln.value:,.2f} = ${ln.units * ln.value:,.2f} but Total Value says ${ln.total_value:,.2f}"
            )
        if not close(ln.units * ln.cost, ln.total_cost):
            raise ValueError(
                f"HPSJ: '{ln.insertion}' {ln.units} × ${ln.cost:,.2f} = ${ln.units * ln.cost:,.2f} but Total Cost says ${ln.total_cost:,.2f}"
            )
        if not ln.is_bonus and not ln.daypart:
            raise ValueError(f"HPSJ: paid row '{ln.insertion}' has no daypart")
        if order.flight_end_date:
            for d, n in zip(ln.week_dates, ln.week_spots):
                if n and d > order.flight_end_date:
                    raise ValueError(
                        f"HPSJ: '{ln.insertion}' has spots in week {d:%m/%d}, after the flight end"
                    )
    if not order.paid_lines:
        raise ValueError("HPSJ: no paid rows")
    n_weeks = len(order.week_dates)
    paid_wk = [sum(ln.week_spots[i] for ln in order.paid_lines) for i in range(n_weeks)]
    bonus_wk = [sum(ln.week_spots[i] for ln in order.bonus_lines) for i in range(n_weeks)]
    if (
        order.paid_units_stated is None
        or order.paid_units_by_week != paid_wk
        or order.paid_units_stated != sum(paid_wk)
    ):
        raise ValueError(
            f"HPSJ: paid rows per week {paid_wk} (={sum(paid_wk)}) != Paid Units row {order.paid_units_by_week} (={order.paid_units_stated})"
        )
    if order.bonus_lines and (
        order.bonus_units_stated is None
        or order.bonus_units_by_week != bonus_wk
        or order.bonus_units_stated != sum(bonus_wk)
    ):
        raise ValueError(
            f"HPSJ: bonus rows per week {bonus_wk} (={sum(bonus_wk)}) != Bonus Units row {order.bonus_units_by_week} (={order.bonus_units_stated})"
        )
    grand_value = round(
        sum(ln.total_value for ln in order.lines) + sum(c.value for c in order.charges), 2
    )
    grand_cost = round(
        sum(ln.total_cost for ln in order.lines) + sum(c.amount for c in order.charges), 2
    )
    if order.grand_value_stated is None or not close(grand_value, order.grand_value_stated):
        raise ValueError(
            f"HPSJ: lines + translation value ${grand_value:,.2f} != sheet total value ${order.grand_value_stated}"
        )
    if order.grand_cost_stated is None or not close(grand_cost, order.grand_cost_stated):
        raise ValueError(
            f"HPSJ: lines + translation cost ${grand_cost:,.2f} != sheet total cost ${order.grand_cost_stated}"
        )
    if not close(order.total_cost, grand_cost):
        raise ValueError(
            f"HPSJ: paid airtime + production ${order.total_cost:,.2f} != sheet total cost ${grand_cost:,.2f}"
        )


if __name__ == "__main__":  # pragma: no cover
    import sys

    o = parse_hpsj(sys.argv[1])
    print(o.title, "|", o.client, "|", o.market_code, "|", o.flight_start, "→", o.flight_end)
    for ln in o.lines:
        print(
            f"  {'BNS' if ln.is_bonus else '   '} {ln.insertion:<24} {ln.daypart:<26} :{ln.length_sec}s ${ln.rate:>6.2f} {ln.week_spots} = {ln.units}"
        )
    for c in o.charges:
        print(f"  charge {c.description}: ${c.amount:,.2f} (value ${c.value:,.2f})")
    print(
        f"  paid ${o.paid_total:,.2f} + production ${o.production_total:,.2f} = ${o.total_cost:,.2f}"
    )
