"""California Black Media (CBM) — SOS campaign house-proposal parser.

The buyer (Dana Levine) sends two documents for each Secretary of State campaign:

  * Charmaine's "Crossings TV Media Proposal" (agency line
    ``AMERICAN COMMUNITY MEDIA: SOS``): a key/value header, one grid per market
    (``California-Los Angeles Spectrum 1519`` …) with its own Paid/Bonus footers,
    and a ``Summary of investment`` table — this is the ORDER (Lee, 2026-10-26:
    "proposal only"), the only document that carries dayparts and rates;
  * CBM's Docusign "Advertising Insertion Order" — campaign name, flight, the
    per-market ``17 (11+6)`` counts and the total. It carries no lines, so it is
    OPTIONAL: when it sits beside the proposal it supplies the campaign name,
    the flight end and the spot length, and its counts must reconcile.

Rates are NET; the customer is California Black Media (ANAGRAF 404, direct, 0%),
precedent contract 2203 ``CBM 2511``. Week headers (``26-Oct``) carry no year:
the IO's flight pins it, else the Monday nearest today (CAFOB's ``infer_year``).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import List, Optional

import pdfplumber

from browser_automation.parsers.acm_parser import _split_daypart
from browser_automation.parsers.cafob_parser import infer_year

CLIENT_NAME = "California Black Media"
DEFAULT_CUSTOMER_ID = 404

_MONTHS = {
    m: i
    for i, m in enumerate(
        ("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"), 1
    )
}
_LANGUAGES = (
    ("VIETNAMESE", "Vietnamese"),
    ("VIET", "Vietnamese"),
    ("MANDARIN", "Mandarin"),
    ("CANTONESE", "Cantonese"),
    ("CHINESE", "Chinese"),
    ("FILIPINO", "Filipino"),
    ("FILPINO", "Filipino"),  # Charmaine's ACoM Asian Law Caucus sheet spells it so (10/28)
    ("TAGALOG", "Filipino"),
    ("KOREAN", "Korean"),
    ("HMONG", "Hmong"),
    ("JAPANESE", "Japanese"),
    ("SOUTH ASIAN", "South Asian"),
    ("PUNJABI", "Punjabi"),
    ("HINDI", "Hindi"),
)
_MARKET_HINTS = (
    ("SAN FRANCISCO", "SFO"),
    ("SF BAY", "SFO"),
    ("SACRAMENTO", "CVC"),
    ("CENTRAL VALLEY", "CVC"),
    ("LOS ANGELES", "LAX"),
    ("SEATTLE", "SEA"),
    ("HOUSTON", "HOU"),
    ("CHICAGO", "CMP"),
    ("MINNEAPOLIS", "CMP"),
    ("WASHINGTON", "WDC"),
    ("NEW YORK", "NYC"),
    ("DALLAS", "DAL"),
)
_AGENCY_RE = re.compile(r"Agency\s*:\s*(.+?)\s*:\s*(\S.*)")


class CBMParseError(ValueError):
    pass


# ─── detection ───────────────────────────────────────────────────────────────


def _up(text: str) -> str:
    return " ".join((text or "").split()).upper()


def is_cbm_proposal_text(text: str) -> bool:
    """Charmaine's proposal for the SOS campaign: agency 'AMERICAN COMMUNITY MEDIA: SOS'."""
    up = _up(text)
    return "CROSSINGS TV MEDIA PROPOSAL" in up and bool(
        re.search(r"AGENCY:\s*AMERICAN COMMUNITY MEDIA\s*:\s*SOS\b", up)
    )


def is_acom_proposal_text(text: str) -> bool:
    """Any Charmaine proposal whose Agency line reads 'AMERICAN COMMUNITY MEDIA: <client>'
    (the SOS one is CBM's; every other client enters as ACM — Lee 2026-10-28)."""
    up = _up(text)
    return "CROSSINGS TV MEDIA PROPOSAL" in up and bool(
        re.search(r"AGENCY:\s*AMERICAN COMMUNITY MEDIA\s*:\s*\S", up)
    )


def is_cbm_io_text(text: str) -> bool:
    """CBM's own Insertion Order (Docusign): the CBM representative signs it."""
    up = _up(text)
    return "INSERTION ORDER" in up and (
        "CBM REPRESENTATIVE" in up or "CALIFORNIA BLACK MEDIA" in up
    )


def is_cbm_text(text: str) -> bool:
    return is_cbm_proposal_text(text) or is_cbm_io_text(text)


# ─── helpers ─────────────────────────────────────────────────────────────────


def _norm(s) -> str:
    return " ".join(str(s or "").replace("\n", " ").split())


def _money(cell: str) -> Optional[float]:
    """'$ 7 14.00' → 714.0 (the sheet splits digits); '$ -' → 0.0; unreadable → None."""
    s = re.sub(r"[\s$,]", "", str(cell or ""))
    if s == "-":
        return 0.0
    if not re.fullmatch(r"-?\d+(\.\d+)?", s):
        return None
    return float(s)


def _int(cell: str) -> Optional[int]:
    s = _norm(cell).replace(",", "")
    return int(s) if re.fullmatch(r"\d+", s) else None


def _field(text: str, label: str) -> str:
    m = re.search(rf"{re.escape(label)}\s*:?\s*(.*)", text)
    return _norm(m.group(1)) if m else ""


def market_of(text: str) -> str:
    up = _norm(text).upper()
    for key, code in _MARKET_HINTS:
        if key in up:
            return code
    return ""


def language_of(block: str) -> tuple[str, str]:
    """'Hindi News/Talk' → ('Hindi', 'News/Talk')."""
    up = _norm(block).upper()
    for key, lang in _LANGUAGES:
        if up.startswith(key):
            return lang, _norm(block)[len(key) :].strip(" -:/")
    return "", _norm(block)


def _mdy(s: str) -> date:
    m, d, y = (int(x) for x in s.split("/"))
    return date(y + 2000 if y < 100 else y, m, d)


def _year_for(mon: int, day: int, prev: Optional[date], year_hint: Optional[int], today) -> int:
    if prev is not None:
        return prev.year + (1 if mon < prev.month else 0)
    if year_hint:
        return year_hint
    return infer_year(mon, day, today)


def _week_header(
    cell: str, prev: Optional[date], year_hint: Optional[int], today
) -> Optional[tuple[date, Optional[date]]]:
    """A grid column header → (column start, column end or None).

    '26-Oct' is a broadcast week (end None → +6 days). A short flight is printed as
    ONE column carrying its date range, '10/30-11/3' (Asian Law Caucus, 10/28):
    the end is the range's own end, and the start need not be a Monday."""
    c = _norm(cell)
    m = re.fullmatch(r"(\d{1,2})-([A-Za-z]{3})", c)
    if m:
        day, mon = int(m.group(1)), _MONTHS.get(m.group(2).upper())
        if not mon:
            return None
        return date(_year_for(mon, day, prev, year_hint, today), mon, day), None
    m = re.fullmatch(
        r"(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?\s*-\s*(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?", c
    )
    if m:
        m1, d1, y1, m2, d2, y2 = m.groups()
        mon, day = int(m1), int(d1)
        year = (
            int(y1) + (2000 if y1 and len(y1) == 2 else 0)
            if y1
            else _year_for(mon, day, prev, year_hint, today)
        )
        start = date(year, mon, day)
        mon2, day2 = int(m2), int(d2)
        year2 = (
            int(y2) + (2000 if y2 and len(y2) == 2 else 0)
            if y2
            else (year + (1 if mon2 < mon else 0))
        )
        end = date(year2, mon2, day2)
        if end < start:
            raise CBMParseError(f"week column {c!r} ends before it starts")
        return start, end
    return None


# ─── model ───────────────────────────────────────────────────────────────────


@dataclass
class CBMInsertionOrder:
    """CBM's Advertising Insertion Order — campaign, flight, counts, total (no lines)."""

    io_date: str  # MM/DD/YYYY
    campaign: str  # 'SOS Vote Education Campaign 3'
    flight_start: date
    flight_end: date
    length_sec: Optional[int]
    language: str
    market_counts: dict  # {'LAX': (paid, bonus)}
    total: float
    source_path: str = ""

    @property
    def total_spots(self) -> int:
        return sum(p + b for p, b in self.market_counts.values())


@dataclass
class CBMLine:
    market: str
    language_block: str  # 'Hindi News/Talk'
    daypart: str  # 'M-F 1p-2p' / 'ROS'
    net_rate: float
    weekly_spots: List[int]
    total_spots: int
    net_total: float
    language: str
    program: str
    is_bonus: bool = False
    length_sec: int = 30

    @property
    def rate(self) -> float:
        return 0.0 if self.is_bonus else self.net_rate

    @property
    def days(self) -> str:
        if self.is_bonus:
            return _ros(self.language)["days"]
        return _split_daypart(self.daypart)[0]

    @property
    def time(self) -> str:
        if self.is_bonus:
            return _ros(self.language)["time"]
        return _split_daypart(self.daypart)[1]

    @property
    def duration(self) -> str:
        return str(self.length_sec)

    @property
    def description(self) -> str:
        """ACM 2881 shape (Lee 10/26): block + daypart; bonus 'BNS <language> ROS'."""
        if self.is_bonus:
            return f"BNS {self.language} ROS"
        return f"{self.language_block} {self.daypart}"[:60]


def _ros(language: str) -> dict:
    from browser_automation.ros_definitions import ROS_SCHEDULES

    return (
        ROS_SCHEDULES.get(language)
        or ROS_SCHEDULES.get("South Asian")
        or {
            "days": "M-Su",
            "time": "6a-11:59p",
        }
    )


@dataclass
class CBMMarketSection:
    market: str
    banner: str  # 'California-Los Angeles Spectrum 1519'
    lines: List[CBMLine] = field(default_factory=list)
    paid_units: int = 0
    paid_net: float = 0.0  # airtime only (the grid's Paid footer minus production)
    bonus_units: int = 0
    production: List[tuple] = field(default_factory=list)  # (description, units, amount)
    week_end: Optional[date] = None  # set when the grid's one column is a date range

    @property
    def production_total(self) -> float:
        return round(sum(a for _, _, a in self.production), 2)

    @property
    def paid_lines(self) -> List[CBMLine]:
        return [ln for ln in self.lines if not ln.is_bonus]

    @property
    def bonus_lines(self) -> List[CBMLine]:
        return [ln for ln in self.lines if ln.is_bonus]


@dataclass
class CBMOrder:
    agency_label: str  # 'AMERICAN COMMUNITY MEDIA'
    campaign_tag: str  # 'SOS' — the word after the agency on the sheet
    contact: str
    email: str
    billing_cycle: str
    market_text: str  # 'LA, SF, CVC'
    week_dates: List[date]
    sections: List[CBMMarketSection]
    total_units: int
    total_paid_units: int
    total_bonus_units: int
    total_net: float
    source_path: str = ""
    io: Optional[CBMInsertionOrder] = None
    companion_path: str = ""  # the other document of the pair, when found
    rates_are_net: bool = True
    no_separation: bool = False  # sheet says 'Airtime: No separation requirement'
    week_end: Optional[date] = None  # the range column's end (short flights)
    total_contract: Optional[float] = None  # 'Total Contract' when the sheet carries production

    @property
    def client(self) -> str:
        return CLIENT_NAME

    @property
    def production(self) -> List[tuple]:
        return [p for s in self.sections for p in s.production]

    @property
    def production_total(self) -> float:
        return round(sum(s.production_total for s in self.sections), 2)

    @property
    def agency(self) -> str:
        return self.agency_label

    @property
    def campaign_name(self) -> str:
        return self.io.campaign if self.io else self.campaign_tag

    @property
    def length_sec(self) -> int:
        return (self.io.length_sec if self.io and self.io.length_sec else None) or 30

    @property
    def markets(self) -> List[str]:
        return [s.market for s in self.sections]

    @property
    def market_code(self) -> str:
        return self.sections[0].market if len(self.sections) == 1 else ""

    @property
    def lines(self) -> List[CBMLine]:
        return [ln for s in self.sections for ln in s.lines]

    @property
    def paid_lines(self) -> List[CBMLine]:
        return [ln for ln in self.lines if not ln.is_bonus]

    @property
    def flight_start(self) -> str:
        d = self.io.flight_start if self.io else self.week_dates[0]
        return f"{d.month:02d}/{d.day:02d}/{d.year}"

    @property
    def flight_end(self) -> str:
        if self.io:
            d = self.io.flight_end
        elif self.week_end:
            d = self.week_end
        else:
            d = self.week_dates[-1] + timedelta(days=6)
        return f"{d.month:02d}/{d.day:02d}/{d.year}"

    @property
    def estimate_number(self) -> str:
        return ""

    @property
    def description(self) -> str:
        return f"{self.client} - {self.campaign_name} {self.flight_start}-{self.flight_end}"

    @property
    def total_spots(self) -> int:
        return sum(ln.total_spots for ln in self.lines)

    @property
    def total_cost(self) -> float:
        return round(sum(ln.net_total for ln in self.paid_lines), 2)


# ─── the IO ──────────────────────────────────────────────────────────────────

_RUN_DATE = re.compile(r"(\d{1,2}/\d{1,2}/\d{2,4})\s*-\s*(\d{1,2}/\d{1,2}/\d{2,4})")
_MARKET_COUNT = re.compile(
    r"(Los Angeles|San Francisco|Central Valley|Sacramento|Seattle|Houston|Chicago|"
    r"Minneapolis|Washington|New York|Dallas)\s+(\d+)\s*\((\d+)\s*\+\s*(\d+)\)",
    re.IGNORECASE,
)


def parse_cbm_io_text(text: str, source_path: str = "") -> CBMInsertionOrder:
    flat = _norm(text)
    if not is_cbm_io_text(flat):
        raise CBMParseError("not a California Black Media Insertion Order")
    m = re.search(r"Insertion Order:\s*(.+?)\s+Parent Company", flat)
    campaign = _norm(m.group(1)) if m else ""
    if not campaign:
        raise CBMParseError("IO: 'Insertion Order: <campaign>' not found")
    m = re.search(r"Date:\s*(\d{1,2}/\d{1,2}/\d{4})", flat)
    io_date = _mdy(m.group(1)) if m else None
    runs = [(_mdy(a), _mdy(b)) for a, b in _RUN_DATE.findall(flat.split("ADDED VALUE")[0])]
    runs = [(a, b) for a, b in runs if a <= b]
    if not runs:
        raise CBMParseError("IO: no run dates (m/d/yy-m/d/yy) found")
    m = re.search(r"(\d+)\s*[×x]\s*:(\d{2,3})\b", flat)
    length_sec = int(m.group(2)) if m else None
    m = re.search(r"TOTAL:\s*\$\s*([\d,]+\.\d{2})", flat)
    if not m:
        raise CBMParseError("IO: 'TOTAL: $' not found")
    total = _money(m.group(1))
    counts = {}
    for name, both, paid, bonus in _MARKET_COUNT.findall(flat):
        code = market_of(name)
        if int(both) != int(paid) + int(bonus):
            raise CBMParseError(f"IO: {name} {both} ≠ {paid}+{bonus}")
        if code in counts:
            raise CBMParseError(f"IO: market {name} listed twice")
        counts[code] = (int(paid), int(bonus))
    if not counts:
        raise CBMParseError("IO: no per-market 'N (paid+bonus)' counts found")
    lang = ""
    for key, name in _LANGUAGES:
        if re.search(rf"\b{key}\b", flat.upper()):
            lang = name
            break
    return CBMInsertionOrder(
        io_date=f"{io_date.month:02d}/{io_date.day:02d}/{io_date.year}" if io_date else "",
        campaign=campaign,
        flight_start=min(a for a, _ in runs),
        flight_end=max(b for _, b in runs),
        length_sec=length_sec,
        language=lang,
        market_counts=counts,
        total=total or 0.0,
        source_path=source_path,
    )


def parse_cbm_io(path: str) -> CBMInsertionOrder:
    with pdfplumber.open(path) as pdf:
        text = "\n".join(pg.extract_text() or "" for pg in pdf.pages)
    return parse_cbm_io_text(text, str(path))


# ─── the proposal ────────────────────────────────────────────────────────────


def _col(header: List[str], label: str, nth: int = 0) -> int:
    hits = [i for i, c in enumerate(header) if label.lower() in c.lower()]
    if len(hits) <= nth:
        raise CBMParseError(f"column {label!r} missing from the grid header: {header}")
    return hits[nth]


def _parse_grid(
    table: List[List[str]], hdr_i: int, year_hint: Optional[int], today
) -> tuple[CBMMarketSection, List[date]]:
    banner = next((_norm(r[0]) for r in table[:hdr_i] if _norm(r[0])), "")
    if not banner:
        raise CBMParseError("market grid has no 'California-<market>' banner row")
    market = market_of(banner)
    if not market:
        raise CBMParseError(f"market not recognised in banner {banner!r}")
    header = [_norm(c) for c in table[hdr_i]]
    c_lang, c_dp, c_rate, c_units, c_total = (
        _col(header, "Language Block"),
        _col(header, "Day Part"),
        _col(header, "NET", 0),
        _col(header, "Total Unit"),
        _col(header, "NET", 1),
    )
    weeks: List[tuple[int, date]] = []
    week_end: Optional[date] = None
    prev = None
    for i, c in enumerate(header):
        wh = _week_header(c, prev, year_hint, today)
        if wh is not None:
            d, end = wh
            weeks.append((i, d))
            prev = d
            if end is not None:
                week_end = end
    if not weeks:
        raise CBMParseError(
            f"{banner}: no week columns ('26-Oct' / '10/30-11/3') in the grid header"
        )
    if week_end is not None and len(weeks) != 1:
        raise CBMParseError(
            f"{banner}: a date-range column only makes sense as the grid's single column"
        )
    week_dates = [d for _, d in weeks]

    section = CBMMarketSection(market=market, banner=banner, week_end=week_end)
    paid_row = bonus_row = None
    for row in table[hdr_i + 1 :]:
        cells = [_norm(c) for c in row]
        if not any(cells):
            continue
        label = cells[c_lang].upper()
        if label == "PAID":
            paid_row = cells
            continue
        if label == "BONUS":
            bonus_row = cells
            continue
        block, dp = cells[c_lang], cells[c_dp]
        if label.startswith("PRODUCTION"):
            # 'Production services | :30s Filipino & Punjabi | $3,000.00 | 0 | 2 | $3,000.00':
            # money for the Production box, never an airtime line (Lee, Crispin rule).
            amount = _money(cells[c_total])
            units = _int(cells[c_units]) if cells[c_units] else 0
            if amount is None or not amount:
                raise CBMParseError(f"{banner} {block}: production row with no amount: {cells}")
            if any(_int(cells[ci]) for ci, _ in weeks):
                raise CBMParseError(f"{banner} {block}: production row carries week spots: {cells}")
            section.production.append((f"{block} {dp}".strip(), units or 0, amount))
            continue
        if not block or not dp:
            raise CBMParseError(f"{banner}: row with blank language/daypart: {cells}")
        spots = []
        for ci, _d in weeks:
            v = _int(cells[ci]) if cells[ci] else 0
            if v is None:
                raise CBMParseError(f"{banner} {block}: unreadable week cell {cells[ci]!r}")
            spots.append(v)
        units = _int(cells[c_units])
        rate = _money(cells[c_rate])
        net = _money(cells[c_total])
        if units is None or rate is None or net is None:
            raise CBMParseError(
                f"{banner} {block}: unreadable units/rate/net "
                f"{cells[c_units]!r} {cells[c_rate]!r} {cells[c_total]!r}"
            )
        if sum(spots) != units:
            raise CBMParseError(
                f"{banner} {block}: week cells sum to {sum(spots)}, Total Units says {units}"
            )
        is_bonus = dp.upper() == "ROS" or block.upper().startswith(("BNS", "BONUS"))
        if is_bonus and rate != 0:
            raise CBMParseError(f"{banner} {block}: a ROS/bonus row with a rate {rate}")
        if not is_bonus and rate == 0:
            raise CBMParseError(f"{banner} {block}: a paid row with no rate")
        if abs(rate * units - net) > 0.005:
            raise CBMParseError(
                f"{banner} {block}: {rate} × {units} = {rate * units:.2f} ≠ NET {net:.2f}"
            )
        lang, program = language_of(block)
        if not lang:
            raise CBMParseError(f"{banner} {block}: no language word recognised")
        section.lines.append(
            CBMLine(
                market=market,
                language_block=block,
                daypart=dp,
                net_rate=rate,
                weekly_spots=spots,
                total_spots=units,
                net_total=net,
                language=lang,
                program=program,
                is_bonus=is_bonus,
            )
        )
    if not section.lines:
        raise CBMParseError(f"{banner}: no airtime rows")
    if paid_row is None:
        raise CBMParseError(f"{banner}: Paid footer row not found")
    if bonus_row is None and section.bonus_lines:
        raise CBMParseError(
            f"{banner}: Bonus footer row not found although the grid has bonus rows"
        )

    # ── the grid's own footers ──
    for name, footer, rows in (
        ("Paid", paid_row, section.paid_lines),
        ("Bonus", bonus_row, section.bonus_lines),
    ):
        if footer is None:
            continue  # a paid-only grid prints no Bonus footer
        for k, (ci, d) in enumerate(weeks):
            want = _int(footer[ci]) if footer[ci] else 0
            got = sum(ln.weekly_spots[k] for ln in rows)
            if want != got:
                raise CBMParseError(
                    f"{banner} {name} week {d}: lines give {got}, footer says {want}"
                )
        units = _int(footer[c_units])
        if units is None or units != sum(ln.total_spots for ln in rows):
            raise CBMParseError(
                f"{banner} {name} units {footer[c_units]!r} ≠ lines {sum(ln.total_spots for ln in rows)}"
            )
    paid_net = _money(paid_row[c_total])
    airtime_net = round(sum(ln.net_total for ln in section.paid_lines), 2)
    # The Paid footer sums airtime AND production (Asian Law Caucus: 4,224 + 3,000 = 7,224).
    if paid_net is None or abs(paid_net - airtime_net - section.production_total) > 0.005:
        raise CBMParseError(
            f"{banner} Paid NET {paid_row[c_total]!r} ≠ airtime {airtime_net:.2f}"
            + (f" + production {section.production_total:.2f}" if section.production else "")
        )
    section.paid_units = sum(ln.total_spots for ln in section.paid_lines)
    section.bonus_units = sum(ln.total_spots for ln in section.bonus_lines)
    section.paid_net = airtime_net
    return section, week_dates


def _parse_summary(table: List[List[str]]) -> dict:
    """{'LAX': (paid, bonus, both, net), 'TOTAL': (...)} from 'Summary of investment'."""
    hdr_i = next(i for i, r in enumerate(table) if any("Paid+Bonus" in _norm(c) for c in r))
    header = [_norm(c) for c in table[hdr_i]]
    c_mkt, c_paid, c_both, c_net = (
        _col(header, "Markets"),
        _col(header, "Total Paid"),
        _col(header, "Paid+Bonus"),
        _col(header, "Net"),
    )
    # A paid-only proposal prints no 'Bonus Units' column (Asian Law Caucus, 10/28).
    c_bonus = _col(header, "Bonus Units") if any("Bonus Units" in c for c in header) else None
    out = {}
    for row in table[hdr_i + 1 :]:
        cells = [_norm(c) for c in row]
        if not any(cells):
            continue
        label = cells[c_mkt].upper()
        if label.startswith("PRODUCTION"):
            # 'Production | 2 | n/a | $3,000.00' — units, then the amount
            amount = _money(cells[c_net])
            if amount is None:
                raise CBMParseError(f"summary Production: unreadable {cells}")
            out["PRODUCTION"] = (_int(cells[c_paid]) or 0, None, None, amount)
            continue
        if label.startswith("TOTAL CONTRACT"):
            amount = _money(cells[c_net])
            if amount is None:
                raise CBMParseError(f"summary Total Contract: unreadable {cells}")
            out["CONTRACT"] = (None, None, None, amount)
            continue
        if label.startswith("TOTAL"):
            key = "TOTAL"
        else:
            # rows are per MARKET (SOS: LAX/SF/CVC) or per LANGUAGE (Asian Law Caucus: Punjabi/Filipino)
            key = market_of(cells[c_mkt]) or ("LANG:" + language_of(cells[c_mkt])[0])
            if key == "LANG:":
                raise CBMParseError(f"summary: {cells[c_mkt]!r} is neither a market nor a language")
        vals = (
            _int(cells[c_paid]),
            _int(cells[c_bonus]) if c_bonus is not None else 0,
            _int(cells[c_both]),
            _money(cells[c_net]),
        )
        if any(v is None for v in vals):
            raise CBMParseError(f"summary {cells[c_mkt]}: unreadable {cells}")
        out[key] = vals
    if "TOTAL" not in out:
        raise CBMParseError("summary: 'Total Airtime' row not found")
    return out


def parse_cbm_proposal(
    path: str, today: Optional[date] = None, year_hint: Optional[int] = None
) -> CBMOrder:
    """CBM's SOS proposal — the ACoM template reader behind its SOS gate."""
    return parse_acom_proposal(path, today, year_hint, require_sos=True)


def parse_acom_proposal(
    path: str,
    today: Optional[date] = None,
    year_hint: Optional[int] = None,
    *,
    require_sos: bool = False,
) -> CBMOrder:
    """Charmaine's 'Crossings TV Media Proposal' for an American Community Media client.
    `require_sos` keeps CBM's gate; the ACM parser reads every other client through here."""
    with pdfplumber.open(path) as pdf:
        texts = [pg.extract_text() or "" for pg in pdf.pages]
        tables = [t for pg in pdf.pages for t in (pg.extract_tables() or [])]
    text = "\n".join(texts)
    if require_sos and not is_cbm_proposal_text(text):
        raise CBMParseError(f"not a {CLIENT_NAME} (SOS) proposal: {Path(path).name}")
    if not is_acom_proposal_text(text):
        raise CBMParseError(f"not an AMERICAN COMMUNITY MEDIA proposal: {Path(path).name}")
    no_separation = bool(re.search(r"AIRTIME:\s*NO SEPARATION", _up(text)))

    m = _AGENCY_RE.search(text)
    agency_label, campaign_tag = (_norm(m.group(1)), _norm(m.group(2))) if m else ("", "")
    contact = _field(text, "Contact")
    email = _field(text, "Email")
    billing = _field(text, "Billing Cycle") or "Broadcast"
    market_text = _field(text, "Market")

    sections: List[CBMMarketSection] = []
    week_dates: Optional[List[date]] = None
    summary = None
    for t in tables:
        hdr = next(
            (i for i, r in enumerate(t) if any("Language Block" in _norm(c) for c in r)),
            None,
        )
        if hdr is not None:
            sec, wd = _parse_grid(t, hdr, year_hint, today)
            if week_dates is None:
                week_dates = wd
            elif wd != week_dates:
                raise CBMParseError(f"{sec.banner}: week columns {wd} differ from {week_dates}")
            if any(s.market == sec.market for s in sections):
                raise CBMParseError(f"market {sec.market} appears twice")
            sections.append(sec)
        elif any(any("Paid+Bonus" in _norm(c) for c in r) for r in t):
            summary = _parse_summary(t)
    if not sections:
        raise CBMParseError("no market grids (Language Block / Total Units) found")
    if summary is None:
        raise CBMParseError("'Summary of investment' table not found")
    week_end = sections[0].week_end
    if any(s.week_end != week_end for s in sections):
        raise CBMParseError("market grids disagree on the date-range column")
    if week_end is None:
        if week_dates[0].weekday() != 0:
            raise CBMParseError(f"first week column {week_dates[0]} is not a Monday")
        for a, b in zip(week_dates, week_dates[1:]):
            if (b - a).days != 7:
                raise CBMParseError(f"week columns {a} → {b} are not 7 days apart")

    # ── summary vs grids ──
    production_total = round(sum(s.production_total for s in sections), 2)
    tp = sum(s.paid_units for s in sections)
    tb = sum(s.bonus_units for s in sections)
    tn = round(sum(s.paid_net for s in sections), 2)
    by_market = {
        k: v
        for k, v in summary.items()
        if k not in ("TOTAL", "PRODUCTION", "CONTRACT") and not k.startswith("LANG:")
    }
    by_lang = {k[5:]: v for k, v in summary.items() if k.startswith("LANG:")}
    if by_market:
        for sec in sections:
            got = (sec.paid_units, sec.bonus_units, sec.paid_units + sec.bonus_units, sec.paid_net)
            want = by_market.get(sec.market)
            if want is None:
                raise CBMParseError(f"summary has no row for {sec.market} ({sec.banner})")
            if want[:3] != got[:3] or abs(want[3] - got[3]) > 0.005:
                raise CBMParseError(f"summary {sec.market} {want} ≠ grid {got}")
        extra = set(by_market) - {s.market for s in sections}
        if extra:
            raise CBMParseError(f"summary lists markets with no grid: {sorted(extra)}")
    if by_lang:
        lines = [ln for s in sections for ln in s.lines]
        for lang, want in by_lang.items():
            paid = sum(ln.total_spots for ln in lines if ln.language == lang and not ln.is_bonus)
            bonus = sum(ln.total_spots for ln in lines if ln.language == lang and ln.is_bonus)
            net = round(
                sum(ln.net_total for ln in lines if ln.language == lang and not ln.is_bonus), 2
            )
            got = (paid, bonus, paid + bonus, net)
            if want[:3] != got[:3] or abs(want[3] - got[3]) > 0.005:
                raise CBMParseError(f"summary {lang} {want} ≠ grid lines {got}")
        extra = set(by_lang) - {ln.language for ln in lines}
        if extra:
            raise CBMParseError(f"summary lists languages with no lines: {sorted(extra)}")
    if not by_market and not by_lang:
        raise CBMParseError("summary has no market or language rows")
    tot = summary["TOTAL"]
    if tot[:3] != (tp, tb, tp + tb) or abs(tot[3] - tn) > 0.005:
        raise CBMParseError(f"Total Airtime {tot} ≠ grids {(tp, tb, tp + tb, tn)}")
    if production_total or "PRODUCTION" in summary:
        want_p = summary.get("PRODUCTION", (0, None, None, 0.0))[3]
        if abs(want_p - production_total) > 0.005:
            raise CBMParseError(
                f"summary Production {want_p:.2f} ≠ grid production rows {production_total:.2f}"
            )
    total_contract = None
    if "CONTRACT" in summary:
        total_contract = summary["CONTRACT"][3]
        if abs(total_contract - (tn + production_total)) > 0.005:
            raise CBMParseError(
                f"Total Contract {total_contract:.2f} ≠ airtime {tn:.2f} + production {production_total:.2f}"
            )

    return CBMOrder(
        agency_label=agency_label,
        campaign_tag=campaign_tag,
        contact=contact,
        email=email,
        billing_cycle=billing,
        market_text=market_text,
        week_dates=week_dates,
        sections=sections,
        total_units=tp + tb,
        total_paid_units=tp,
        total_bonus_units=tb,
        total_net=tn,
        source_path=str(path),
        no_separation=no_separation,
        week_end=week_end,
        total_contract=total_contract,
    )


# ─── the pair ────────────────────────────────────────────────────────────────


def _pdf_text(path: Path) -> str:
    try:
        with pdfplumber.open(str(path)) as pdf:
            return "\n".join(pg.extract_text() or "" for pg in pdf.pages[:2])
    except Exception:
        return ""


def find_companions(path: str) -> tuple[List[Path], List[Path]]:
    """Other CBM PDFs in the same folder: (proposals, IOs), never the file itself."""
    p = Path(path)
    proposals, ios = [], []
    for other in sorted(p.parent.glob("*.pdf")) if p.parent.is_dir() else []:
        if other.resolve() == p.resolve() or other.name.startswith("."):
            continue
        txt = _pdf_text(other)
        if is_cbm_proposal_text(txt):
            proposals.append(other)
        elif is_cbm_io_text(txt):
            ios.append(other)
    return proposals, ios


def reconcile_with_io(order: CBMOrder, io: CBMInsertionOrder) -> None:
    """The IO's per-market (paid+bonus) counts and total must equal the proposal's."""
    want = {s.market: (s.paid_units, s.bonus_units) for s in order.sections}
    if io.market_counts != want:
        raise CBMParseError(f"IO per-market counts {io.market_counts} ≠ proposal {want}")
    if abs(io.total - order.total_net) > 0.005:
        raise CBMParseError(f"IO total ${io.total:,.2f} ≠ proposal ${order.total_net:,.2f}")
    if io.flight_start != order.week_dates[0]:
        raise CBMParseError(
            f"IO flight starts {io.flight_start}, proposal's first week is {order.week_dates[0]}"
        )
    last = order.week_dates[-1]
    if not (last <= io.flight_end <= last + timedelta(days=6)):
        raise CBMParseError(f"IO flight end {io.flight_end} is not in the last week {last}")


def parse_cbm(path: str, today: Optional[date] = None) -> CBMOrder:
    """Parse the proposal (the order). Given the IO instead, find the proposal beside
    it; either way attach the IO when one sits in the folder and reconcile."""
    text = _pdf_text(Path(path))
    io: Optional[CBMInsertionOrder] = None
    proposal_path = Path(path)
    if is_cbm_io_text(text) and not is_cbm_proposal_text(text):
        io = parse_cbm_io_text(text, str(path))
        proposals, _ = find_companions(path)
        if not proposals:
            raise CBMParseError(
                f"{Path(path).name} is the CBM Insertion Order — it carries no lines. "
                "Drop the 'Crossings TV Media Proposal' PDF beside it."
            )
        if len(proposals) > 1:
            raise CBMParseError(
                f"several SOS proposals beside the IO: {[p.name for p in proposals]}"
            )
        proposal_path = proposals[0]
    order = parse_cbm_proposal(
        str(proposal_path), today=today, year_hint=io.flight_start.year if io else None
    )
    if io is None:
        _, ios = find_companions(str(proposal_path))
        for cand in ios:
            try:
                c = parse_cbm_io(str(cand))
                reconcile_with_io(order, c)
            except CBMParseError:
                continue
            io = c
            break
    else:
        reconcile_with_io(order, io)
    order.source_path = str(path)
    if io is not None:
        order.io = io
        order.companion_path = (
            io.source_path
            if Path(path).resolve() == proposal_path.resolve()
            else str(proposal_path)
        )
    return order


if __name__ == "__main__":  # pragma: no cover
    import sys

    for p in sys.argv[1:]:
        o = parse_cbm(p)
        print(
            f"{Path(p).name}: {o.client} ({o.agency_label}: {o.campaign_tag}) | {','.join(o.markets)} | "
            f"{o.flight_start}–{o.flight_end} | :{o.length_sec} | {o.total_spots} spots ${o.total_cost:,.2f}"
            f" | IO: {o.io.campaign if o.io else 'none'}"
        )
        for ln in o.lines:
            print(
                f"   {ln.market} {ln.description:<40} {ln.weekly_spots} = {ln.total_spots} @ {ln.net_rate}"
            )
