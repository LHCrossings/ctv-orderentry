"""
The Vida Agency proposal parser (advertiser: Puget Sound Energy).

The Vida Agency (ANAGRAF 260, Edmonds WA) sends Crossings' own "Crossings TV
Media Proposal" back as the order: a PDF print, one "Flight schedule" table per
page, Seattle only, NET rates, no agency commission on the PSE contracts
(precedent: Vida PSE 2410 / 2411, P_AGENZIA 0.00).

Shape of the document (Puget Sound Flex, 9/23/2026):

    October & December Flight schedule          ← page title (month words, optional year)
    Project Puget Sound Flex                    ← campaign; the advertiser is NOT named
    Market: Seattle, Xfinity Ch. 3131
    Agency The Vida Agency
    Date: 9/23/2026
    FLIGHT SCHEDULE (:30seconds)
    10/12 - 11/8 12/1-12/31                     ← one or more flight ranges (page 2 has none)
    Programming Schedule NET 12-Oct 19-Oct ... Total Units Value Amt
    Vietnamese News /Variety M- Sun 11a-12p $ 40.00 20 19 ... 173 $ 6,920.00 $ 6,920.00
    Bonus Vietnamese ROS $ 4 0.00 10 11 ... 108 $ 4,320.00 $ -
    PAID 40 38 ...
    BONUSES 10 11 ... $ 17,320.00 $ 1 3,000.00
    Production (1) 1 $ 1,950.00 $ 1 ,000.00
    Net amount $ 1 4,000.00

Quirks this reader is built around:
- Money prints with stray spaces inside the number ("$ 4 0.00", "$ 1 ,000.00");
  every amount ends in ".00", so the token is `\\d[\\d ,]*\\.\\d{2}` with the
  spaces/commas stripped. An unreadable amount is None, never 0 (DART lesson).
- A long row name WRAPS onto its own line above the data row (page 2).
- Week labels carry no year ("12-Oct"); the year comes from the title when it
  names one ("March 2027"), else from the proposal date with a roll-forward
  when the month goes backwards. Every week must be a Monday and ascend, or
  the parse refuses (TH Media lesson).
- One page can hold TWO flights (October and December) side by side in one
  week grid; the flight-range line decides which weeks belong to which flight.
  Each flight enters as its own contract (Lee 2026-10-12).
- A dual daypart ("10a-11a/12p-1p") enters as ONE line on the union window
  (Ntooitive / HPSJ precedent, Lee 2026-10-12).
- The bonus row's "$ 40.00" is its VALUE rate; its Net Amt is "-" → $0.
- Production: the Value column ($1,950) is the list price, the Net Amt column
  ($1,000) is what is billed and what the sheet's own "Net amount" foots to.
  The Net Amt is the charge (Lee 2026-10-12), on the first flight's first paid
  line (Production box → CONTRATTISPESE), never a line.

Reconciliation is the document's own arithmetic and RAISES on any mismatch:
per row Σ cells == Total Units and Value == rate × units; the PAID / BONUSES /
Total footer rows per column; the BONUSES money cells (Σ Value, Σ paid Net);
"Net amount" == Σ paid Net + production Net.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import List, Optional

_MONTHS = {
    "jan": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "may": 5,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "oct": 10,
    "nov": 11,
    "dec": 12,
}
_MONTH_WORDS = {
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "may": 5,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
}

_MARKET_MAP = {
    "seattle": "SEA",
    "san francisco": "SFO",
    "sacramento": "CVC",
    "central valley": "CVC",
    "los angeles": "LAX",
    "houston": "HOU",
    "chicago": "CMP",
    "minneapolis": "CMP",
    "washington": "WDC",
    "new york": "NYC",
    "dallas": "DAL",
    "national": "MMT",
}

# "$ 40.00", "$ 4 0.00", "$ 1 ,000.00", "$ -"
_MONEY_RE = re.compile(r"\s*(-|\d[\d ,]*\.\d{2})(.*)", re.S)
_WEEK_LABEL_RE = re.compile(r"^(\d{1,2})-([A-Za-z]{3})$")  # "12-Oct"
_RANGE_RE = re.compile(r"(\d{1,2})/(\d{1,2})\s*-\s*(\d{1,2})/(\d{1,2})")  # "10/12 - 11/8"
_DATE_RE = re.compile(r"(\d{1,2})/(\d{1,2})/(\d{4})")
_LEN_RE = re.compile(r"\(\s*:?(\d{1,3})\s*sec", re.I)  # "(:30seconds)"
_DAY_TOK = r"(?:Mon|Tue|Tu|Wed|Thu|Th|Fri|Sat|Sa|Sun|Su|M|W|F)"
_DAYS_RE = re.compile(rf"\b({_DAY_TOK})\s*-\s*({_DAY_TOK})\b", re.I)
_TIME_RE = re.compile(r"\d{1,2}(?::\d{2})?\s*[ap]\s*-\s*\d{1,2}(?::\d{2})?\s*[ap]", re.I)

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


class VidaParseError(ValueError):
    """The document could not be read or does not foot — refuse to enter."""


# ─── Data model ──────────────────────────────────────────────────────────────


@dataclass
class VidaLine:
    program: str  # "Vietnamese News /Variety", "Bonus Vietnamese ROS"
    days: str  # "M-Su" ("" for a ROS bonus row)
    time_text: str  # "11a-12p", "10a-11a/12p-1p", "" for ROS
    rate: float  # per-spot NET rate as entered (0 ⇒ bonus)
    value_rate: Optional[float]  # the sheet's Value-column rate (bonus rows carry $40 here)
    length_sec: int
    week_dates: List[date]  # Monday of each week in THIS flight
    week_spots: List[int]
    value_total: Optional[float] = None
    net_total: Optional[float] = None
    is_bonus: bool = False
    flight_index: int = 0
    flight_start: Optional[date] = None
    flight_end: Optional[date] = None

    @property
    def total_spots(self) -> int:
        return sum(self.week_spots)

    @property
    def time_ranges(self) -> List[str]:
        """['10a-11a', '12p-1p'] — the pieces of a dual daypart."""
        return [t.replace(" ", "") for t in _TIME_RE.findall(self.time_text)]

    @property
    def language(self) -> str:
        low = self.program.lower()
        for lang in (
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
        ):
            if lang in low:
                return lang.title() if lang != "south asian" else "South Asian"
        return ""

    @property
    def program_short(self) -> str:
        """'Vietnamese News /Variety' → 'News/Variety'; 'Vietnamese Drama' → 'Drama'."""
        p = re.sub(r"\s*/\s*", "/", self.program)
        p = re.sub(r"^(bonus\s+)?", "", p, flags=re.I)
        lang = self.language
        if lang:
            p = re.sub(re.escape(lang), "", p, flags=re.I)
        return " ".join(p.split())

    # Aliases for the web preview normalizer (parser_bridge._normalize_line)
    @property
    def description(self) -> str:
        if self.is_bonus:
            return f"BNS {self.language or self.program} ROS"
        return f"{self.days} {self.time_text} {self.program}".strip()

    @property
    def weekly_spots(self) -> List[int]:
        return self.week_spots

    @property
    def length(self) -> int:
        return self.length_sec

    @property
    def time(self) -> str:
        return self.time_text

    @property
    def start_date(self) -> str:
        return self.flight_start.strftime("%m/%d/%Y") if self.flight_start else ""

    @property
    def end_date(self) -> str:
        return self.flight_end.strftime("%m/%d/%Y") if self.flight_end else ""


@dataclass
class VidaCharge:
    description: str
    amount: float  # the billed (Net Amt) amount
    list_amount: Optional[float] = None


@dataclass
class VidaFlight:
    start: date
    end: date
    week_dates: List[date]
    lines: List[VidaLine] = field(default_factory=list)
    page: int = 1
    title: str = ""

    @property
    def label(self) -> str:
        return f"{self.start.month}/{self.start.day}-{self.end.month}/{self.end.day}/{self.end:%y}"

    @property
    def paid_lines(self) -> List[VidaLine]:
        return [ln for ln in self.lines if not ln.is_bonus]

    @property
    def bonus_lines(self) -> List[VidaLine]:
        return [ln for ln in self.lines if ln.is_bonus]

    @property
    def net_total(self) -> float:
        return round(sum(ln.rate * ln.total_spots for ln in self.paid_lines), 2)


@dataclass
class VidaOrder:
    agency: str
    project: str  # "Puget Sound Flex" — the campaign, not the advertiser
    market_label: str
    market_code: str
    contact: str
    email: str
    order_date: Optional[date]
    length_sec: int
    flights: List[VidaFlight] = field(default_factory=list)
    charges: List[VidaCharge] = field(default_factory=list)
    rates_are_net: bool = True  # NET quoted; the 0% agency commission keeps gross == net
    source_path: str = ""

    @property
    def advertiser(self) -> str:
        return self.project

    @property
    def lines(self) -> List[VidaLine]:
        return [ln for fl in self.flights for ln in fl.lines]

    @property
    def flight_start(self) -> str:
        return min(fl.start for fl in self.flights).strftime("%m/%d/%Y") if self.flights else ""

    @property
    def flight_end(self) -> str:
        return max(fl.end for fl in self.flights).strftime("%m/%d/%Y") if self.flights else ""

    @property
    def market(self) -> str:
        return self.market_code

    @property
    def net_airtime(self) -> float:
        return round(sum(fl.net_total for fl in self.flights), 2)


# ─── Small helpers ───────────────────────────────────────────────────────────


def _money_from(chunk: str) -> tuple[Optional[float], str]:
    """'$'-chunk → (amount or None for '-', remainder text). Spaces and commas
    inside the number are print artifacts ('4 0.00' → 40.00)."""
    m = _MONEY_RE.match(chunk)
    if not m:
        return None, chunk
    tok, rest = m.group(1), m.group(2)
    if tok == "-":
        return None, rest
    return float(tok.replace(" ", "").replace(",", "")), rest


def _ints(text: str) -> List[int]:
    return [int(t) for t in re.findall(r"\b\d+\b", text)]


def _canon_days(a: str, b: str) -> str:
    return f"{_DAY_CANON[a.lower()]}-{_DAY_CANON[b.lower()]}"


def _market_code(label: str) -> str:
    low = (label or "").lower()
    for key, code in _MARKET_MAP.items():
        if key in low:
            return code
    return ""


def _title_year(title: str) -> Optional[int]:
    m = re.search(r"\b(20\d{2})\b", title or "")
    return int(m.group(1)) if m else None


def _resolve_dates(
    month_days: List[tuple[int, int]], base_year: int, base_month: int
) -> List[date]:
    """(month, day) pairs → dates, rolling the year forward whenever the month
    goes backwards relative to the previous one (10 → 11 → 12 → 3 ⇒ next year)."""
    out: List[date] = []
    year, prev = base_year, base_month
    for m, d in month_days:
        if m < prev:
            year += 1
        prev = m
        out.append(date(year, m, d))
    return out


# ─── Page reader ─────────────────────────────────────────────────────────────


def _read_page(text: str, page_no: int, source_path: str) -> dict:
    """One page = one flight-schedule table. Returns a dict with header fields,
    week dates, rows, footer and flight ranges — reconciled, not yet split."""
    lines = [" ".join(txt.split()) for txt in text.splitlines()]
    lines = [txt for txt in lines if txt]
    if not lines:
        raise VidaParseError(f"Vida parser: page {page_no} has no text")

    hdr: dict = {"title": lines[0]}
    for txt in lines[:12]:
        low = txt.lower()
        if low.startswith("project"):
            hdr["project"] = txt[len("project") :].strip(" :")
        elif low.startswith("market"):
            hdr["market"] = txt.split(":", 1)[-1].strip()
        elif low.startswith("agency"):
            hdr["agency"] = txt[len("agency") :].strip(" :")
        elif low.startswith("contact"):
            hdr["contact"] = txt.split(":", 1)[-1].strip()
        elif low.startswith("email"):
            hdr["email"] = txt[len("email") :].strip(" :")
        elif low.startswith("date"):
            m = _DATE_RE.search(txt)
            if m:
                hdr["date"] = date(int(m.group(3)), int(m.group(1)), int(m.group(2)))
        if "flight schedule" in low and "(" in txt:
            m = _LEN_RE.search(txt)
            if m:
                hdr["length_sec"] = int(m.group(1))

    if "date" not in hdr:
        raise VidaParseError(
            f"Vida parser: page {page_no} has no 'Date:' line — "
            "the week columns cannot be given a year"
        )
    if "length_sec" not in hdr:
        raise VidaParseError(
            f"Vida parser: page {page_no} does not state the spot "
            "length ('FLIGHT SCHEDULE (:30seconds)')"
        )

    # ── Column header: week labels by LABEL, never by position ──
    hdr_idx = next(
        (i for i, txt in enumerate(lines) if txt.lower().startswith("programming schedule")), None
    )
    if hdr_idx is None:
        raise VidaParseError(
            f"Vida parser: page {page_no} has no 'Programming Schedule' header row"
        )
    head_tokens = lines[hdr_idx].split()
    labels = [(int(m.group(1)), m.group(2)) for t in head_tokens if (m := _WEEK_LABEL_RE.match(t))]
    if not labels:
        raise VidaParseError(
            f"Vida parser: page {page_no} header row names no week columns: {lines[hdr_idx]!r}"
        )
    if "total" not in lines[hdr_idx].lower() or "units" not in lines[hdr_idx].lower():
        raise VidaParseError(
            f"Vida parser: page {page_no} header row lacks 'Total Units' "
            f"— column layout changed: {lines[hdr_idx]!r}"
        )
    try:
        month_days = [(_MONTHS[mon.lower()], d) for d, mon in labels]
    except KeyError as exc:
        raise VidaParseError(
            f"Vida parser: page {page_no} week label month {exc} not recognised"
        ) from None
    ty = _title_year(hdr["title"])
    base_year = ty if ty else hdr["date"].year
    base_month = 1 if ty else hdr["date"].month
    week_dates = _resolve_dates(month_days, base_year, base_month)
    for w in week_dates:
        if w.weekday() != 0:
            raise VidaParseError(
                f"Vida parser: page {page_no} week column {w:%m/%d/%Y} is a {w:%A}, not a Monday"
            )
    if week_dates != sorted(week_dates) or len(set(week_dates)) != len(week_dates):
        raise VidaParseError(
            f"Vida parser: page {page_no} week columns do not ascend: "
            f"{[w.strftime('%m/%d/%y') for w in week_dates]}"
        )
    n_weeks = len(week_dates)

    # ── Flight ranges (a line of nothing but 'm/d - m/d' pairs, above the header) ──
    ranges: List[tuple[date, date]] = []
    for txt in lines[:hdr_idx]:
        pairs = _RANGE_RE.findall(txt)
        if pairs and not re.search(r"[A-Za-z]", txt):
            md = []
            for a, b, c, d in pairs:
                md.extend([(int(a), int(b)), (int(c), int(d))])
            ds = _resolve_dates(md, base_year, base_month)
            ranges = [(ds[i], ds[i + 1]) for i in range(0, len(ds), 2)]
    if not ranges:
        # Page 2 style: "March 2027 Flight schedule" — the flight is the first week's
        # Monday through the last day of the last week's month.
        last = week_dates[-1]
        nm = date(last.year + (last.month == 12), last.month % 12 + 1, 1)
        ranges = [(week_dates[0], nm - timedelta(days=1))]
    for a, b in ranges:
        if b < a:
            raise VidaParseError(
                f"Vida parser: page {page_no} flight range "
                f"{a:%m/%d/%y}-{b:%m/%d/%y} ends before it starts"
            )

    # ── Data rows: from the header to the PAID footer; names may wrap ──
    rows: List[dict] = []
    footer: dict = {}
    pending_name = ""
    for txt in lines[hdr_idx + 1 :]:
        low = txt.lower()
        if low.startswith("paid "):
            footer["paid"] = _ints(txt)
            continue
        if low.startswith("bonuses"):
            parts = txt.split("$")
            footer["bonus"] = _ints(parts[0])
            money = [_money_from(p)[0] for p in parts[1:]]
            footer["bonus_value"] = money[0] if money else None
            footer["bonus_net"] = money[1] if len(money) > 1 else None
            continue
        if (
            (low.startswith("airtime total") or low.startswith("total "))
            and "total" not in footer
            and _ints(txt)
        ):
            footer["total"] = _ints(txt)  # not "Total Estimated Impressions" below the grid
            continue
        if low.startswith("production") and "$" in txt and "production_seen" not in footer:
            parts = txt.split("$")  # the "Production:" note below the grid has no '$'
            money = [_money_from(p)[0] for p in parts[1:]]
            footer["production_value"] = money[0] if money else None
            footer["production_net"] = money[1] if len(money) > 1 else None
            footer["production_seen"] = True
            continue
        if low.startswith("net amount") and "$" in txt and "net_amount" not in footer:
            parts = txt.split("$")
            footer["net_amount"] = _money_from(parts[1])[0] if len(parts) > 1 else None
            continue
        if "paid" in footer:
            continue  # below the grid: notes, impressions
        if "$" not in txt:
            pending_name = (pending_name + " " + txt).strip()  # wrapped row name
            continue
        head, *chunks = txt.split("$")
        name_text = (pending_name + " " + head).strip()
        pending_name = ""
        money: List[Optional[float]] = []
        ints: List[int] = []
        for i, ch in enumerate(chunks):
            amt, rest = _money_from(ch)
            money.append(amt)
            if i == 0:
                ints = _ints(rest)
        if len(money) < 3:
            raise VidaParseError(
                f"Vida parser: page {page_no} row {name_text!r} has "
                f"{len(money)} money cells, expected rate/Value/Net Amt"
            )
        if len(ints) != n_weeks + 1:
            raise VidaParseError(
                f"Vida parser: page {page_no} row {name_text!r} has {len(ints)} number "
                f"cells for {n_weeks} weeks + Total — a cell was dropped or merged; "
                "refusing to enter"
            )
        rows.append(
            {
                "name": name_text,
                "rate": money[0],
                "cells": ints[:n_weeks],
                "total": ints[n_weeks],
                "value": money[1],
                "net": money[2],
            }
        )

    if not rows:
        raise VidaParseError(f"Vida parser: page {page_no} has no airtime rows")
    if "paid" not in footer:
        raise VidaParseError(f"Vida parser: page {page_no} has no PAID footer row")

    # ── Row → VidaLine (program / days / times by content) ──
    parsed: List[VidaLine] = []
    for r in rows:
        name = r["name"]
        is_bonus = name.lower().startswith("bonus") or r["net"] in (None, 0.0)
        m = _DAYS_RE.search(name)
        if m:
            program = name[: m.start()].strip()
            days = _canon_days(m.group(1), m.group(2))
            time_text = name[m.end() :].strip()
        else:
            program, days, time_text = name, "", ""
        if not is_bonus:
            if not m or not _TIME_RE.search(time_text):
                raise VidaParseError(
                    f"Vida parser: page {page_no} paid row {name!r} has no readable "
                    "day pattern / time window — a paid line's window is never defaulted"
                )
            if r["rate"] is None:
                raise VidaParseError(
                    f"Vida parser: page {page_no} paid row {name!r} has an unreadable rate"
                )
        # The entered rate is the NET per spot; bonus = 0 (its '$40' is the value rate)
        rate = 0.0 if is_bonus else float(r["rate"])
        parsed.append(
            VidaLine(
                program=program,
                days=days,
                time_text=time_text,
                rate=rate,
                value_rate=r["rate"],
                length_sec=hdr["length_sec"],
                week_dates=list(week_dates),
                week_spots=list(r["cells"]),
                value_total=r["value"],
                net_total=r["net"],
                is_bonus=is_bonus,
            )
        )
        # Per-row arithmetic
        if sum(r["cells"]) != r["total"]:
            raise VidaParseError(
                f"Vida parser: page {page_no} row {name!r}: week cells "
                f"sum to {sum(r['cells'])} but Total Units says "
                f"{r['total']}"
            )
        if r["rate"] is not None and r["value"] is not None:
            if abs(r["rate"] * r["total"] - r["value"]) > 0.01:
                raise VidaParseError(
                    f"Vida parser: page {page_no} row {name!r}: "
                    f"${r['rate']:.2f} × {r['total']} != Value "
                    f"${r['value']:,.2f}"
                )
        if not is_bonus and abs(rate * r["total"] - r["net"]) > 0.01:
            raise VidaParseError(
                f"Vida parser: page {page_no} row {name!r}: rate × "
                f"units ${rate * r['total']:,.2f} != Net Amt "
                f"${r['net']:,.2f}"
            )

    # ── Footer rows reconcile per column ──
    paid = [ln for ln in parsed if not ln.is_bonus]
    bonus = [ln for ln in parsed if ln.is_bonus]

    def _col_sums(group: List[VidaLine]) -> List[int]:
        return [sum(ln.week_spots[i] for ln in group) for i in range(n_weeks)]

    def _check(label: str, stated: Optional[List[int]], group: List[VidaLine]) -> None:
        if stated is None:
            return
        want = _col_sums(group) + [sum(ln.total_spots for ln in group)]
        if stated[: n_weeks + 1] != want:
            raise VidaParseError(
                f"Vida parser: page {page_no} '{label}' row "
                f"{stated[: n_weeks + 1]} != lines {want} — a row was "
                "dropped or mis-read; refusing to enter"
            )

    _check("PAID", footer.get("paid"), paid)
    _check("BONUSES", footer.get("bonus"), bonus)
    _check("Total", footer.get("total"), parsed)

    sum_value = round(sum(ln.value_total or 0 for ln in parsed), 2)
    sum_net = round(sum(ln.net_total or 0 for ln in paid), 2)
    if footer.get("bonus_value") is not None and abs(sum_value - footer["bonus_value"]) > 0.01:
        raise VidaParseError(
            f"Vida parser: page {page_no} Σ Value ${sum_value:,.2f} != "
            f"footer ${footer['bonus_value']:,.2f}"
        )
    if footer.get("bonus_net") is not None and abs(sum_net - footer["bonus_net"]) > 0.01:
        raise VidaParseError(
            f"Vida parser: page {page_no} Σ Net Amt ${sum_net:,.2f} != "
            f"footer ${footer['bonus_net']:,.2f}"
        )

    charges: List[VidaCharge] = []
    if footer.get("production_seen"):
        amt = footer.get("production_net")
        if amt is None:
            raise VidaParseError(
                f"Vida parser: page {page_no} Production row has no "
                "readable Net Amt — refusing to guess the charge"
            )
        charges.append(VidaCharge("Production", float(amt), footer.get("production_value")))
    if footer.get("net_amount") is not None:
        want = round(sum_net + sum(c.amount for c in charges), 2)
        if abs(want - footer["net_amount"]) > 0.01:
            raise VidaParseError(
                f"Vida parser: page {page_no} 'Net amount' "
                f"${footer['net_amount']:,.2f} != airtime ${sum_net:,.2f}"
                f" + charges ${sum(c.amount for c in charges):,.2f}"
            )

    return {
        "hdr": hdr,
        "week_dates": week_dates,
        "ranges": ranges,
        "lines": parsed,
        "charges": charges,
        "page": page_no,
    }


def _split_flights(page: dict) -> List[VidaFlight]:
    """Assign each week column to the flight range it intersects; one VidaFlight
    per range, each line carrying only that flight's weeks."""
    week_dates: List[date] = page["week_dates"]
    ranges: List[tuple[date, date]] = page["ranges"]
    owner: List[int] = []
    for w in week_dates:
        hits = [i for i, (a, b) in enumerate(ranges) if w <= b and w + timedelta(days=6) >= a]
        if len(hits) != 1:
            raise VidaParseError(
                f"Vida parser: page {page['page']} week {w:%m/%d/%y} matches "
                f"{len(hits)} flight range(s) {[(a.strftime('%m/%d'), b.strftime('%m/%d')) for a, b in ranges]}"
                " — cannot tell which contract it belongs to"
            )
        owner.append(hits[0])

    flights: List[VidaFlight] = []
    for i, (a, b) in enumerate(ranges):
        idx = [k for k, o in enumerate(owner) if o == i]
        if not idx:
            raise VidaParseError(
                f"Vida parser: page {page['page']} flight "
                f"{a:%m/%d/%y}-{b:%m/%d/%y} owns no week column"
            )
        fl = VidaFlight(
            start=a,
            end=b,
            week_dates=[week_dates[k] for k in idx],
            page=page["page"],
            title=page["hdr"]["title"],
        )
        for ln in page["lines"]:
            spots = [ln.week_spots[k] for k in idx]
            if sum(spots) == 0:
                continue
            fl.lines.append(
                VidaLine(
                    program=ln.program,
                    days=ln.days,
                    time_text=ln.time_text,
                    rate=ln.rate,
                    value_rate=ln.value_rate,
                    length_sec=ln.length_sec,
                    week_dates=fl.week_dates,
                    week_spots=spots,
                    value_total=None,
                    net_total=None,
                    is_bonus=ln.is_bonus,
                    flight_start=a,
                    flight_end=b,
                )
            )
        flights.append(fl)
    return flights


# ─── Entry points ────────────────────────────────────────────────────────────


def parse_vida_pages(pages: List[str], source_path: str = "") -> VidaOrder:
    """Parse the per-page text of a Vida proposal (testable without pdfplumber)."""
    if not pages:
        raise VidaParseError("Vida parser: no pages")
    read = [_read_page(t, i + 1, source_path) for i, t in enumerate(pages)]
    hdr0 = read[0]["hdr"]
    flights: List[VidaFlight] = []
    charges: List[VidaCharge] = []
    for pg in read:
        flights.extend(_split_flights(pg))
        charges.extend(pg["charges"])
    for i, fl in enumerate(flights):
        for ln in fl.lines:
            ln.flight_index = i
    starts = [fl.start for fl in flights]
    if starts != sorted(starts):
        raise VidaParseError(
            "Vida parser: flights do not ascend across pages: "
            f"{[s.strftime('%m/%d/%y') for s in starts]}"
        )
    lengths = {fl.lines[0].length_sec for fl in flights if fl.lines}
    if len(lengths) != 1:
        raise VidaParseError(f"Vida parser: pages disagree on spot length: {sorted(lengths)}")

    market_label = hdr0.get("market", "")
    return VidaOrder(
        agency=hdr0.get("agency", ""),
        project=hdr0.get("project", ""),
        market_label=market_label,
        market_code=_market_code(market_label),
        contact=hdr0.get("contact", ""),
        email=hdr0.get("email", ""),
        order_date=hdr0.get("date"),
        length_sec=lengths.pop(),
        flights=flights,
        charges=charges,
        rates_are_net=True,
        source_path=source_path,
    )


def parse_vida(path: str) -> VidaOrder:
    """Parse a Vida Agency proposal PDF (every page — never pages[0] only)."""
    import pdfplumber

    pages: List[str] = []
    with pdfplumber.open(path) as pdf:
        for p in pdf.pages:
            pages.append(p.extract_text() or "")
    return parse_vida_pages(pages, source_path=path)


def is_vida_text(text: str) -> bool:
    """Detector: the agency's name on a 'Flight schedule' proposal page."""
    low = " ".join((text or "").split()).lower()
    return "the vida agency" in low and "flight schedule" in low
