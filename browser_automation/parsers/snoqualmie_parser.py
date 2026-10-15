"""
Snoqualmie Casino & Hotel proposal parser (direct client, Seattle).

Snoqualmie buys direct (ANAGRAF 417, no agency, 0% commission, Broadcast
billing) off Crossings' own "Crossings TV Media Proposal" PDF — the same house
template family as Illinois Lottery / Vida, with a NET + PROMO rate pair.

Shape of the document (Entertainment :15, revised 10/7/2026):

    OPTION 2
    2026 Crossings TV Media Proposal- 15 seconds video (Subtitle)   ← title: year + length
    Client Snoqualmie Casino & Hotel
    Contact: Amanda Beltran
    Email: abeltran@snocasino.com
    Phone: Phone: 425-888-8152 | Fax: 425-888-7996
    Address: 37500 SE North Bend Way, Snoqualmie, WA 98065
    Billing Cycle Broadcast
    Market: Seattle
    Estimate Flight                                    ← the Estimate cell is blank
    Date: week of 10/12 through 11/11                  ← flight (no year)
    Date Revision 10/7/2026
    Airtime :15s video
    Day Part/
    Language Block Program NET PROMO 12-Oct 19-Oct ... 9-Nov Total Units VALUE NET
    Mandarin/Cantonese News/Talk M-F 7p-9p $ 30.00 $ 22.00 7 7 7 7 1 29 $ 8 70.00 $ 638.00
    Filipino News/talk M-F 4p-5p, 6p-7p $ 24.00 $ 22.00 10 10 9 9 1 39 $ 9 36.00 $ 858.00
    BONUS Chinese ROS $ 24.00 $ - 7 7 7 7 0 28 $ 6 72.00 $ -
    Paid 53 53 48 46 6 206
    Bonus 28 28 28 28 0 112 $ 7,674.00 $ 4 ,430.00     ← Σ VALUE, Σ NET
    Total 81 81 76 74 6 318
    Subtitle (Raina)-Chinese, Viet, Korean 3 $ 375.00 $ 285.00   ← charge rows (qty, list, net)
    Subtitle (Chef)- Chinese, Viet,Korean 3 $ 375.00 $ 285.00
    Final cost $ 8,424.00 $ 5,000.00                   ← Σ VALUE + list charges, Σ NET + net charges
    *we will use English spot to run during Filipino programming
    Traffic information (ROTATE 3 SPOTS EVENLY)
    Sacred Riana should end 10/30 ...
    Translation services ...

Money basis: the NET column is the rate-card rate (× units = VALUE), the PROMO
column is what the client pays (× units = the row's NET total). The PROMO rate
is the entered rate; a direct 0% client makes gross == net. A bonus row's
PROMO is "-" (→ $0 line, booking code 10) and its NET column is its value rate.

Quirks this reader is built around (shared with the Vida / Illinois Lottery
readers of the same template):
- Money prints with stray spaces inside the number ("$ 8 70.00", "$ 4 ,430.00");
  every amount ends in ".00". An unreadable amount is None, never 0.
- Week labels carry no year ("12-Oct"); the year comes from the title, else the
  revision date, rolling forward when the month goes backwards. Every week must
  be a Monday and ascend, or the parse refuses (TH Media lesson).
- A long row name may WRAP onto the line above its data row.
- A dual daypart ("4p-5p, 6p-7p") enters as ONE line on the union window.
- The charge rows (Subtitle ...) are production: Lee enters them as ONE amount
  on the first paid line's Production box; the parser keeps each row.

Reconciliation is the document's own arithmetic and RAISES on any mismatch:
per row Σ cells == Total Units, NET rate × units == VALUE, PROMO × units == NET;
the Paid / Bonus / Total footer rows per column; the Bonus row's money cells;
"Final cost" == Σ VALUE + Σ charge list and == Σ paid NET + Σ charge net.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import List, Optional

CLIENT_NAME = "Snoqualmie Casino"

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

# "$ 30.00", "$ 8 70.00", "$ 4 ,430.00", "$ -"
_MONEY_RE = re.compile(r"\s*(-|\d[\d ,]*\.\d{2})(.*)", re.S)
_WEEK_LABEL_RE = re.compile(r"^(\d{1,2})-([A-Za-z]{3})$")  # "12-Oct"
_MD_RE = re.compile(r"(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?")
_DATE_RE = re.compile(r"(\d{1,2})/(\d{1,2})/(\d{4})")
_LEN_RE = re.compile(r":?\s*(\d{1,3})\s*(?:s\b|sec)", re.I)  # ":15s", "15 seconds"
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


class SnoqualmieParseError(ValueError):
    """The document could not be read or does not foot — refuse to enter."""


# ─── Data model ──────────────────────────────────────────────────────────────


@dataclass
class SnoqualmieLine:
    program: str  # "Mandarin/Cantonese News/Talk", "BONUS Chinese ROS"
    days: str  # "M-F", "Sa-Su" ("" for a ROS bonus row)
    time_text: str  # "7p-9p", "4p-5p, 6p-7p", "" for ROS
    rate: float  # the PROMO rate — what is entered and billed (0 ⇒ bonus)
    value_rate: Optional[float]  # the NET column — rate-card value per spot
    length_sec: int
    week_dates: List[date]
    week_spots: List[int]
    value_total: Optional[float] = None
    net_total: Optional[float] = None
    is_bonus: bool = False
    flight_start: Optional[date] = None
    flight_end: Optional[date] = None

    @property
    def total_spots(self) -> int:
        return sum(self.week_spots)

    @property
    def time_ranges(self) -> List[str]:
        """['4p-5p', '6p-7p'] — the pieces of a dual daypart."""
        return [t.replace(" ", "") for t in _TIME_RE.findall(self.time_text)]

    @property
    def language(self) -> str:
        """House language of the row. 'Mandarin/Cantonese ...' → 'Chinese' (the
        two blocks together are the Chinese schedule — Illinois Lottery precedent)."""
        low = self.program.lower()
        if "mandarin" in low and "cantonese" in low:
            return "Chinese"
        for lang in _LANGS:
            if lang in low:
                return "South Asian" if lang == "south asian" else lang.title()
        return ""

    @property
    def program_short(self) -> str:
        """'Mandarin/Cantonese News/Talk' → 'News/Talk'; 'Filipino News/talk' → 'News/Talk'."""
        p = re.sub(r"\s*/\s*", "/", self.program)
        p = re.sub(r"^(bonus\s+)?", "", p, flags=re.I)
        p = re.sub(r"(?i)mandarin/cantonese|cantonese/mandarin", "", p)
        lang = self.language
        if lang:
            p = re.sub(re.escape(lang), "", p, flags=re.I)
        p = " ".join(p.split())
        return "/".join(s[:1].upper() + s[1:] for s in p.split("/"))

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
class SnoqualmieCharge:
    description: str  # "Subtitle (Raina)-Chinese, Viet, Korean"
    quantity: int
    list_amount: Optional[float]  # retail value
    amount: float  # the discounted / billed amount


@dataclass
class SnoqualmieOrder:
    client: str
    contact: str
    email: str
    phone: str
    address: str
    billing_cycle: str  # "Broadcast" / "Calendar" as printed
    market_label: str
    market_code: str
    estimate: str
    start: date
    end: date
    order_date: Optional[date]
    length_sec: int
    week_dates: List[date]
    lines: List[SnoqualmieLine] = field(default_factory=list)
    charges: List[SnoqualmieCharge] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)  # traffic / rotation notes as printed
    title: str = ""
    rates_are_net: bool = True  # direct 0% — the PROMO rate is what is billed
    source_path: str = ""

    @property
    def advertiser(self) -> str:
        return self.client

    @property
    def campaign(self) -> str:
        """'... Proposal_Entertainment_15secs_ Revised.pdf' → 'Entertainment'. The
        sheet itself never names the campaign; the file name usually does."""
        stem = Path(self.source_path).stem if self.source_path else ""
        m = re.search(r"proposal_\s*([A-Za-z][A-Za-z ]*?)\s*(?:_|$)", stem, re.I)
        return m.group(1).strip() if m else ""

    @property
    def paid_lines(self) -> List[SnoqualmieLine]:
        return [ln for ln in self.lines if not ln.is_bonus]

    @property
    def bonus_lines(self) -> List[SnoqualmieLine]:
        return [ln for ln in self.lines if ln.is_bonus]

    @property
    def net_airtime(self) -> float:
        return round(sum(ln.rate * ln.total_spots for ln in self.paid_lines), 2)

    @property
    def charges_total(self) -> float:
        return round(sum(ch.amount for ch in self.charges), 2)

    @property
    def flight_start(self) -> str:
        return self.start.strftime("%m/%d/%Y")

    @property
    def flight_end(self) -> str:
        return self.end.strftime("%m/%d/%Y")

    @property
    def market(self) -> str:
        return self.market_code

    @property
    def label(self) -> str:
        return f"{self.start.month}/{self.start.day}-{self.end.month}/{self.end.day}/{self.end:%y}"


# ─── Small helpers ───────────────────────────────────────────────────────────


def _money_from(chunk: str) -> tuple[Optional[float], str]:
    """'$'-chunk → (amount or None for '-', remainder text). Spaces and commas
    inside the number are print artifacts ('8 70.00' → 870.00)."""
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


def _resolve_dates(
    month_days: List[tuple[int, int]], base_year: int, base_month: int
) -> List[date]:
    """(month, day) pairs → dates, rolling the year forward whenever the month
    goes backwards relative to the previous one (10 → 11 → 1 ⇒ next year)."""
    out: List[date] = []
    year, prev = base_year, base_month
    for m, d in month_days:
        if m < prev:
            year += 1
        prev = m
        try:
            out.append(date(year, m, d))
        except ValueError as exc:
            raise SnoqualmieParseError(f"Snoqualmie parser: bad date {m}/{d}/{year}: {exc}")
    return out


def _after_label(txt: str, label: str) -> str:
    return txt[len(label) :].strip(" :-")


# ─── Reader ──────────────────────────────────────────────────────────────────


def parse_snoqualmie_text(text: str, source_path: str = "") -> SnoqualmieOrder:
    """Parse the extracted text of a Snoqualmie proposal (testable without pdfplumber)."""
    lines = [" ".join(txt.split()) for txt in (text or "").splitlines()]
    lines = [txt for txt in lines if txt]
    if not lines:
        raise SnoqualmieParseError("Snoqualmie parser: the page has no text")

    # ── Header: by line-anchored label, never by position ──
    hdr: dict = {}
    hdr_idx = next(
        (
            i
            for i, txt in enumerate(lines)
            if txt.lower().startswith("language block") and "total" in txt.lower()
        ),
        None,
    )
    if hdr_idx is None:
        raise SnoqualmieParseError(
            "Snoqualmie parser: no 'Language Block ... Total Units' header row"
        )
    for txt in lines[:hdr_idx]:
        low = txt.lower()
        if "crossings tv media proposal" in low:
            hdr["title"] = txt
        elif low.startswith("client"):
            hdr["client"] = _after_label(txt, "Client")
        elif low.startswith("contact"):
            hdr["contact"] = _after_label(txt, "Contact")
        elif low.startswith("email"):
            hdr["email"] = _after_label(txt, "Email")
        elif low.startswith("phone"):
            hdr["phone"] = re.sub(r"(?i)^(phone:?\s*)+", "", txt).strip()
        elif low.startswith("address"):
            hdr["address"] = _after_label(txt, "Address")
        elif low.startswith("billing cycle"):
            hdr["billing_cycle"] = _after_label(txt, "Billing Cycle")
        elif low.startswith("market"):
            hdr["market"] = _after_label(txt, "Market")
        elif low.startswith("estimate"):
            # "Estimate Flight" — the Estimate cell sits before the next label
            hdr["estimate"] = re.sub(r"(?i)\s*flight\s*$", "", _after_label(txt, "Estimate"))
        elif low.startswith("date revision") or re.match(r"^date\b.*\d{1,2}/\d{1,2}/\d{4}", low):
            m = _DATE_RE.search(txt)
            if m and "revision" in low or ("flight" not in hdr and m and "through" not in low):
                hdr["date"] = date(int(m.group(3)), int(m.group(1)), int(m.group(2)))
        if "through" in low or re.search(r"\d/\d{1,2}\s*-\s*\d{1,2}/", low):
            md = _MD_RE.findall(txt)
            if len(md) >= 2:
                hdr["flight_md"] = md[:2]
        if low.startswith("airtime"):
            m = _LEN_RE.search(txt)
            if m:
                hdr["length_sec"] = int(m.group(1))
    if "length_sec" not in hdr and "title" in hdr:
        m = re.search(r"(\d{1,3})\s*sec", hdr["title"], re.I)
        if m:
            hdr["length_sec"] = int(m.group(1))

    for key, what in (
        ("client", "'Client' line"),
        ("flight_md", "flight 'Date: week of m/d through m/d' line"),
        ("length_sec", "spot length ('Airtime :15s')"),
    ):
        if key not in hdr:
            raise SnoqualmieParseError(f"Snoqualmie parser: no {what}")

    ty = re.search(r"\b(20\d{2})\b", hdr.get("title", ""))
    if ty:
        base_year, base_month = int(ty.group(1)), 1
    elif "date" in hdr:
        base_year, base_month = hdr["date"].year, hdr["date"].month
    else:
        raise SnoqualmieParseError(
            "Snoqualmie parser: neither the title nor a 'Date Revision' line names a "
            "year — the week columns cannot be dated"
        )

    # ── Week columns by LABEL ──
    head_tokens = lines[hdr_idx].split()
    labels = [(int(m.group(1)), m.group(2)) for t in head_tokens if (m := _WEEK_LABEL_RE.match(t))]
    if not labels:
        raise SnoqualmieParseError(
            f"Snoqualmie parser: header row names no week columns: {lines[hdr_idx]!r}"
        )
    hl = lines[hdr_idx].lower()
    if "units" not in hl or "promo" not in hl:
        raise SnoqualmieParseError(
            f"Snoqualmie parser: header row lacks 'PROMO' / 'Total Units' — column "
            f"layout changed: {lines[hdr_idx]!r}"
        )
    try:
        month_days = [(_MONTHS[mon.lower()], d) for d, mon in labels]
    except KeyError as exc:
        raise SnoqualmieParseError(
            f"Snoqualmie parser: week label month {exc} not recognised"
        ) from None
    week_dates = _resolve_dates(month_days, base_year, base_month)
    for w in week_dates:
        if w.weekday() != 0:
            raise SnoqualmieParseError(
                f"Snoqualmie parser: week column {w:%m/%d/%Y} is a {w:%A}, not a Monday"
            )
    if week_dates != sorted(week_dates) or len(set(week_dates)) != len(week_dates):
        raise SnoqualmieParseError(
            "Snoqualmie parser: week columns do not ascend: "
            f"{[w.strftime('%m/%d/%y') for w in week_dates]}"
        )
    n_weeks = len(week_dates)

    # ── Flight range: "week of 10/12 through 11/11" (year from the same base) ──
    md = []
    for mm, dd, yy in hdr["flight_md"]:
        md.append((int(mm), int(dd)))
    fl_years = [yy for _m, _d, yy in hdr["flight_md"] if yy]
    if fl_years:
        y0 = int(fl_years[0])
        y0 = y0 + 2000 if y0 < 100 else y0
        f_start, f_end = _resolve_dates(md, y0, md[0][0])
    else:
        f_start, f_end = _resolve_dates(md, week_dates[0].year, week_dates[0].month)
    if f_end < f_start:
        raise SnoqualmieParseError(
            f"Snoqualmie parser: flight {f_start:%m/%d/%y}-{f_end:%m/%d/%y} ends before it starts"
        )
    if not (week_dates[0] <= f_start <= week_dates[0] + timedelta(days=6)):
        raise SnoqualmieParseError(
            f"Snoqualmie parser: flight start {f_start:%m/%d/%y} is not in the first "
            f"week column ({week_dates[0]:%m/%d/%y})"
        )
    if f_end < week_dates[-1]:
        raise SnoqualmieParseError(
            f"Snoqualmie parser: flight end {f_end:%m/%d/%y} precedes the last week "
            f"column ({week_dates[-1]:%m/%d/%y})"
        )

    # ── Data rows: header → 'Paid' footer; names may wrap onto the line above ──
    rows: List[dict] = []
    footer: dict = {}
    charges: List[SnoqualmieCharge] = []
    notes: List[str] = []
    pending_name = ""
    in_notes = False
    for txt in lines[hdr_idx + 1 :]:
        low = txt.lower()
        if "paid" not in footer:
            if low.startswith("paid "):
                footer["paid"] = _ints(txt)
                continue
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
                if i == 1:
                    ints = _ints(rest)  # the cells follow the PROMO cell
            if len(money) != 4:
                raise SnoqualmieParseError(
                    f"Snoqualmie parser: row {name_text!r} has {len(money)} money "
                    "cells, expected NET / PROMO / VALUE / NET"
                )
            if len(ints) != n_weeks + 1:
                raise SnoqualmieParseError(
                    f"Snoqualmie parser: row {name_text!r} has {len(ints)} number "
                    f"cells for {n_weeks} weeks + Total — a cell was dropped or merged; "
                    "refusing to enter"
                )
            rows.append(
                {
                    "name": name_text,
                    "value_rate": money[0],
                    "promo": money[1],
                    "cells": ints[:n_weeks],
                    "total": ints[n_weeks],
                    "value": money[2],
                    "net": money[3],
                }
            )
            continue

        # ── Below the grid ──
        if low.startswith("bonus") and "bonus" not in footer:
            parts = txt.split("$")
            footer["bonus"] = _ints(parts[0])
            money = [_money_from(p)[0] for p in parts[1:]]
            footer["bonus_value"] = money[0] if money else None
            footer["bonus_net"] = money[1] if len(money) > 1 else None
            continue
        if low.startswith("total ") and "total" not in footer and "$" not in txt:
            footer["total"] = _ints(txt)
            continue
        if low.startswith("final cost"):
            money = [_money_from(p)[0] for p in txt.split("$")[1:]]
            footer["final_value"] = money[0] if money else None
            footer["final_net"] = money[1] if len(money) > 1 else None
            continue
        if "final_net" not in footer and "$" in txt:
            # A charge row: "<description> <qty> $ <list> $ <net>"
            head, *chunks = txt.split("$")
            money = [_money_from(ch)[0] for ch in chunks]
            qty_m = re.search(r"(\d+)\s*$", head)
            qty = int(qty_m.group(1)) if qty_m else 1
            desc = head[: qty_m.start()].strip() if qty_m else head.strip()
            if len(money) < 2 or money[-1] is None:
                raise SnoqualmieParseError(
                    f"Snoqualmie parser: charge row {desc!r} has no readable "
                    "discounted amount — refusing to guess the charge"
                )
            charges.append(SnoqualmieCharge(desc, qty, money[0], float(money[-1])))
            continue
        if low.startswith("translation services"):
            in_notes = False
            continue
        if low.startswith("total for") and "$" in txt:
            m = re.search(r"total for\s+(\d+)\s+spots?", low)
            footer["charges_total"] = _money_from(txt.split("$")[1])[0]
            footer["charges_spots"] = int(m.group(1)) if m else None
            continue
        if low.startswith("traffic information") or txt.startswith("*"):
            in_notes = True
            notes.append(txt)
            continue
        if in_notes:
            notes.append(txt)

    if not rows:
        raise SnoqualmieParseError("Snoqualmie parser: no airtime rows under the header")
    if "paid" not in footer:
        raise SnoqualmieParseError("Snoqualmie parser: no 'Paid' footer row")

    # ── Row → line (program / days / time by content) ──
    parsed: List[SnoqualmieLine] = []
    for r in rows:
        name = r["name"]
        is_bonus = name.lower().startswith("bonus") or r["promo"] in (None, 0.0)
        m = _DAYS_RE.search(name)
        if m:
            program = name[: m.start()].strip()
            days = _canon_days(m.group(1), m.group(2))
            time_text = name[m.end() :].strip()
        else:
            program, days, time_text = name, "", ""
        if not is_bonus:
            if not m or not _TIME_RE.search(time_text):
                raise SnoqualmieParseError(
                    f"Snoqualmie parser: paid row {name!r} has no readable day "
                    "pattern / time window — a paid line's window is never defaulted"
                )
            if r["promo"] is None:
                raise SnoqualmieParseError(
                    f"Snoqualmie parser: paid row {name!r} has an unreadable PROMO rate"
                )
        rate = 0.0 if is_bonus else float(r["promo"])
        parsed.append(
            SnoqualmieLine(
                program=program,
                days=days,
                time_text=time_text,
                rate=rate,
                value_rate=r["value_rate"],
                length_sec=hdr["length_sec"],
                week_dates=list(week_dates),
                week_spots=list(r["cells"]),
                value_total=r["value"],
                net_total=r["net"],
                is_bonus=is_bonus,
                flight_start=f_start,
                flight_end=f_end,
            )
        )
        if sum(r["cells"]) != r["total"]:
            raise SnoqualmieParseError(
                f"Snoqualmie parser: row {name!r}: week cells sum to "
                f"{sum(r['cells'])} but Total Units says {r['total']}"
            )
        if r["value_rate"] is not None and r["value"] is not None:
            if abs(r["value_rate"] * r["total"] - r["value"]) > 0.01:
                raise SnoqualmieParseError(
                    f"Snoqualmie parser: row {name!r}: NET rate ${r['value_rate']:.2f} × "
                    f"{r['total']} != VALUE ${r['value']:,.2f}"
                )
        if not is_bonus:
            if r["net"] is None:
                raise SnoqualmieParseError(f"Snoqualmie parser: paid row {name!r} has no NET total")
            if abs(rate * r["total"] - r["net"]) > 0.01:
                raise SnoqualmieParseError(
                    f"Snoqualmie parser: row {name!r}: PROMO ${rate:.2f} × {r['total']} "
                    f"= ${rate * r['total']:,.2f} != NET ${r['net']:,.2f}"
                )

    # ── Footer rows reconcile per column ──
    paid = [ln for ln in parsed if not ln.is_bonus]
    bonus = [ln for ln in parsed if ln.is_bonus]

    def _col_sums(group: List[SnoqualmieLine]) -> List[int]:
        return [sum(ln.week_spots[i] for ln in group) for i in range(n_weeks)]

    def _check(label: str, stated: Optional[List[int]], group: List[SnoqualmieLine]) -> None:
        if stated is None:
            return
        want = _col_sums(group) + [sum(ln.total_spots for ln in group)]
        if stated[: n_weeks + 1] != want:
            raise SnoqualmieParseError(
                f"Snoqualmie parser: '{label}' row {stated[: n_weeks + 1]} != lines "
                f"{want} — a row was dropped or mis-read; refusing to enter"
            )

    _check("Paid", footer.get("paid"), paid)
    _check("Bonus", footer.get("bonus"), bonus)
    _check("Total", footer.get("total"), parsed)

    sum_value = round(sum(ln.value_total or 0 for ln in parsed), 2)
    sum_net = round(sum(ln.net_total or 0 for ln in paid), 2)
    if footer.get("bonus_value") is not None and abs(sum_value - footer["bonus_value"]) > 0.01:
        raise SnoqualmieParseError(
            f"Snoqualmie parser: Σ VALUE ${sum_value:,.2f} != footer ${footer['bonus_value']:,.2f}"
        )
    if footer.get("bonus_net") is not None and abs(sum_net - footer["bonus_net"]) > 0.01:
        raise SnoqualmieParseError(
            f"Snoqualmie parser: Σ NET ${sum_net:,.2f} != footer ${footer['bonus_net']:,.2f}"
        )
    ch_net = round(sum(c.amount for c in charges), 2)
    ch_list = round(sum(c.list_amount or 0 for c in charges), 2)
    if footer.get("final_net") is not None and abs(sum_net + ch_net - footer["final_net"]) > 0.01:
        raise SnoqualmieParseError(
            f"Snoqualmie parser: 'Final cost' net ${footer['final_net']:,.2f} != airtime "
            f"${sum_net:,.2f} + charges ${ch_net:,.2f}"
        )
    if (
        footer.get("final_value") is not None
        and abs(sum_value + ch_list - footer["final_value"]) > 0.01
    ):
        raise SnoqualmieParseError(
            f"Snoqualmie parser: 'Final cost' value ${footer['final_value']:,.2f} != "
            f"Σ VALUE ${sum_value:,.2f} + charge list ${ch_list:,.2f}"
        )
    if footer.get("charges_total") is not None and abs(ch_net - footer["charges_total"]) > 0.01:
        raise SnoqualmieParseError(
            f"Snoqualmie parser: translation 'Total for N spots' ${footer['charges_total']:,.2f}"
            f" != Σ charge rows ${ch_net:,.2f}"
        )

    market_label = hdr.get("market", "")
    return SnoqualmieOrder(
        client=hdr["client"],
        contact=hdr.get("contact", ""),
        email=hdr.get("email", ""),
        phone=hdr.get("phone", ""),
        address=hdr.get("address", ""),
        billing_cycle=hdr.get("billing_cycle", ""),
        market_label=market_label,
        market_code=_market_code(market_label),
        estimate=hdr.get("estimate", ""),
        start=f_start,
        end=f_end,
        order_date=hdr.get("date"),
        length_sec=hdr["length_sec"],
        week_dates=week_dates,
        lines=parsed,
        charges=charges,
        notes=notes,
        title=hdr.get("title", ""),
        rates_are_net=True,
        source_path=source_path,
    )


def parse_snoqualmie(path: str) -> SnoqualmieOrder:
    """Parse a Snoqualmie proposal PDF (every page's text, concatenated)."""
    import pdfplumber

    pages: List[str] = []
    with pdfplumber.open(path) as pdf:
        for p in pdf.pages:
            pages.append(p.extract_text() or "")
    return parse_snoqualmie_text("\n".join(pages), source_path=path)


def is_snoqualmie_text(text: str) -> bool:
    """Detector: the client's name on a 'Crossings TV Media Proposal' page."""
    low = " ".join((text or "").split()).lower()
    return "snoqualmie" in low and "crossings tv media proposal" in low


if __name__ == "__main__":
    import sys

    o = parse_snoqualmie(sys.argv[1])
    print(o.client, o.market_code, o.label, f":{o.length_sec}", o.billing_cycle, repr(o.campaign))
    for ln in o.lines:
        print(
            f"  {'BNS' if ln.is_bonus else '   '} {ln.language:<9} {ln.program_short:<16} "
            f"{ln.days:<5} {ln.time_text:<14} ${ln.rate:5.2f} {ln.week_spots} = {ln.total_spots}"
        )
    for ch in o.charges:
        print(f"  charge {ch.description!r} x{ch.quantity} list {ch.list_amount} net {ch.amount}")
    print(f"  airtime ${o.net_airtime:,.2f} + charges ${o.charges_total:,.2f}")
    for n in o.notes:
        print("  note:", n)
