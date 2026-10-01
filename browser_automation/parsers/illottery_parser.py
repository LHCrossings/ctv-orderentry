"""
Illinois Lottery / Flowers Communications Group — house "Crossings TV Proposal" PDF parser.

Layout (one landscape page; first order: FY'27 MCM Media Plan, Oct 2026 - Feb 2027):

    Client        Flowers Communications Group
    Advertiser    Illinois Lottery
    Market        Chicago-Twin Cities (Xfinity TV 3131)
    Flight Estimate October, November, December, January, February 2027
    Contact / Email / Address / Channel / Asian Group
    Gross $ 17,644.00      Net $ 14,997.40      Proposal Date 7/27/2026
    Holiday Lunar New Year 2/6
                                   Gross Rate  Gross Rate  Discounted
    Programming Schedule :15seconds per:30s     per:15s     Rate Gross   12-Oct 19-Oct ... 22-Feb  Units  Value  Proposed  Impressions
    Cantonese & Mandarin News &                                                     (title wraps onto the row ABOVE)
    Drama              M-F 7p-9p     $ 70.00     $ 42.00     $ 33.00      5 5 5 ...        144  $6,048.00 $4,752.00  587,579
    ...
    Bonus Chinese ROS                $ 40.00     $ 24.00     $ -          8 8 8 ...        172  $4,128.00 $ -        457,716
    Total                                                                              1356 $39,888.00 $17,644.00 4,413,536
    Translation included ...                                                           Disc Rate 55.77%

Rules:
  * every column is found by its HEADER word and x position (week labels, Units, Value,
    Proposed, Impressions, the three rate headers); a cell belongs to the nearest header
    centre. Money prints with stray spaces ("$ 7 0.00") - tokens are joined per column;
  * the billed rate is the DISCOUNTED RATE (GROSS). Value (= per-:15 rate x units) and
    Impressions are informational. Net = gross x (1 - commission) is reconciliation only;
  * a row whose name wraps puts the first words on the row above: a text-only row in the
    name region is attached to the NEXT data row, and claimed once;
  * reconciles per line (sum of week cells == Units; rate x Units == Proposed) and for the
    sheet (Total Units / Value / Proposed, Gross header == Total Proposed), else RAISES;
  * week labels ("12-Oct") take their year from the Flight Estimate's year (the LAST
    month's); every week start must be a Monday and the columns ascend, else raise.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import List, Optional

import pdfplumber

ADVERTISER_NAME = "Illinois Lottery"
AGENCY_NAME = "Flowers Communications Group"

_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}  # fmt: skip
_MARKET_BY_NAME = [
    ("chicago", "CMP"),
    ("twin cities", "CMP"),
    ("minneapolis", "CMP"),
    ("san francisco", "SFO"),
    ("sacramento", "CVC"),
    ("los angeles", "LAX"),
    ("seattle", "SEA"),
    ("houston", "HOU"),
    ("washington", "WDC"),
    ("new york", "NYC"),
    ("dallas", "DAL"),
]
_WEEK_LABEL = re.compile(r"^(\d{1,2})-([A-Za-z]{3})$")
_DAY_START = re.compile(
    r"^(?:M-F|M-Su|M-Sa|Sa-Su|Sat-Sun|Mon-Sun|Mon-Fri|Sat|Sun|M-Th|Tu-Sa)(?:$|\d)", re.I
)
_ROW_TOL = 2.0


def is_illottery_text(text: str) -> bool:
    t = " ".join((text or "").split()).lower()
    return "advertiser illinois lottery" in t or (
        "illinois lottery" in t and "flowers communications" in t
    )


class ILLotteryParseError(ValueError):
    pass


@dataclass
class ILLotteryLine:
    block: str  # "Cantonese & Mandarin News & Drama" | "Chinese ROS" (bonus)
    daypart: str  # "M-F 7p-9p" | "Sat-Sun 8p-12a" | "M-F 4p-5p, 6p-7p" | "" (bonus)
    rate_per30: float
    rate_per15: float
    rate: float  # Discounted Rate (GROSS) - the billed rate; 0 on bonus
    is_bonus: bool
    week_spots: List[int]
    units: int  # the sheet's Units cell
    value: float  # the sheet's Value cell (informational)
    proposed: float  # the sheet's Proposed cell (gross dollars)
    impressions: Optional[int] = None

    @property
    def total_spots(self) -> int:
        return sum(self.week_spots)


@dataclass
class ILLotteryOrder:
    agency: str
    advertiser: str
    market_name: str
    market_code: str
    flight_estimate: str
    contact: str
    email: str
    channel: str
    asian_group: str
    gross_stated: float
    net_stated: float
    proposal_date: Optional[date]
    holiday: str
    length_sec: int
    week_dates: List[date] = field(default_factory=list)
    lines: List[ILLotteryLine] = field(default_factory=list)
    total_units_stated: int = 0
    total_value_stated: float = 0.0
    total_proposed_stated: float = 0.0
    rates_are_net: bool = False  # GROSS quoted - always False

    @property
    def paid_lines(self) -> List[ILLotteryLine]:
        return [ln for ln in self.lines if not ln.is_bonus]

    @property
    def bonus_lines(self) -> List[ILLotteryLine]:
        return [ln for ln in self.lines if ln.is_bonus]

    @property
    def flight_start(self) -> date:
        return self.week_dates[0]

    @property
    def flight_end(self) -> date:
        return self.week_dates[-1] + timedelta(days=6)

    @property
    def gross_total(self) -> float:
        return round(sum(ln.proposed for ln in self.paid_lines), 2)

    @property
    def total_spots(self) -> int:
        return sum(ln.total_spots for ln in self.lines)

    @property
    def implied_commission(self) -> float:
        """1 - Net/Gross from the header, as a percentage (15.0)."""
        if not self.gross_stated:
            return 0.0
        return round((1.0 - self.net_stated / self.gross_stated) * 100.0, 2)


# ─── helpers ──────────────────────────────────────────────────────────────────


def _rows(words: list[dict]) -> list[list[dict]]:
    out: list[list[dict]] = []
    for w in sorted(words, key=lambda w: (w["top"], w["x0"])):
        if out and abs(w["top"] - out[-1][0]["top"]) <= _ROW_TOL:
            out[-1].append(w)
        else:
            out.append([w])
    for r in out:
        r.sort(key=lambda w: w["x0"])
    return out


def _centre(w: dict) -> float:
    return (w["x0"] + w["x1"]) / 2.0


def _money(text: str) -> Optional[float]:
    t = re.sub(r"[\s$,]", "", text or "")
    if t in ("", "-"):
        return 0.0 if t == "-" else None
    try:
        return round(float(t), 2)
    except ValueError:
        return None


def _int(text: str) -> Optional[int]:
    t = re.sub(r"[\s,]", "", text or "")
    return int(t) if t.isdigit() else None


def _rx(text: str, pattern: str) -> str:
    m = re.search(pattern, text, re.IGNORECASE | re.MULTILINE)
    return m.group(1).strip() if m else ""


def _week_years(flight_estimate: str) -> dict[int, int]:
    """'October, November, December, January, February 2027' -> {10: 2026, 11: 2026,
    12: 2026, 1: 2027, 2: 2027}: the year printed belongs to the LAST month, and the
    months walk backwards from it."""
    m = re.search(r"((?:[A-Za-z]+,?\s*)+?)\s*(\d{4})\s*$", flight_estimate.strip())
    if not m:
        raise ILLotteryParseError(f"Flight Estimate has no year: {flight_estimate!r}")
    months = [
        _MONTHS[t[:3].lower()]
        for t in re.findall(r"[A-Za-z]+", m.group(1))
        if t[:3].lower() in _MONTHS
    ]
    if not months:
        raise ILLotteryParseError(f"Flight Estimate names no months: {flight_estimate!r}")
    year = int(m.group(2))
    out: dict[int, int] = {}
    for mo in reversed(months):
        out[mo] = year
        # the month to the left is earlier; stepping back from January drops a year
        if mo == 1:
            year -= 1
    return out


def split_block_daypart(text: str) -> tuple[str, str]:
    """'Cantonese & Mandarin News & Drama M-F 7p-9p' -> (block, 'M-F 7p-9p');
    'Filipino News /Talk M-F 4p-5p, 6p-7p' keeps the dual window together;
    'Bonus Chinese ROS' -> ('Bonus Chinese ROS', '')."""
    tokens = text.split()
    for i, t in enumerate(tokens):
        if i and _DAY_START.match(t):
            return " ".join(tokens[:i]).strip(), " ".join(tokens[i:]).strip()
    return text.strip(), ""


# ─── grid ─────────────────────────────────────────────────────────────────────


def _find_header(rows: list[list[dict]]) -> tuple[int, dict[str, float], list[tuple[float, str]]]:
    """Index of the 'Programming Schedule' header row, {column: centre x} for the
    non-week columns, and [(centre x, label)] for the week columns."""
    for i, row in enumerate(rows):
        texts = [w["text"] for w in row]
        weeks = [(_centre(w), w["text"]) for w in row if _WEEK_LABEL.match(w["text"])]
        if "Programming" in texts and len(weeks) >= 2:
            anchors: dict[str, float] = {}
            for w in row:
                t = w["text"].lower()
                if t == "units":
                    anchors["units"] = _centre(w)
                elif t == "value":
                    anchors["value"] = _centre(w)
                elif t == "proposed":
                    anchors["proposed"] = _centre(w)
                elif t.startswith("impress"):
                    anchors["impressions"] = _centre(w)
            # the rate headers sit on the rows ABOVE (multi-line labels)
            for prev in rows[max(0, i - 6) : i]:
                for w in prev:
                    t = w["text"].lower()
                    if t == "per:30s":
                        anchors["per30"] = _centre(w)
                    elif t == "per:15s":
                        anchors["per15"] = _centre(w)
                    elif t == "discounted":
                        anchors["rate"] = _centre(w)
            missing = [
                c
                for c in ("units", "value", "proposed", "per30", "per15", "rate")
                if c not in anchors
            ]
            if missing:
                raise ILLotteryParseError(f"schedule header is missing column(s) {missing}")
            return i, anchors, sorted(weeks)
    raise ILLotteryParseError("no 'Programming Schedule' header row with week labels found")


def _assign(
    row: list[dict], anchors: dict[str, float], weeks: list[tuple[float, str]], name_limit: float
):
    """Split a row into the name region (left of the first rate column) and per-column
    token lists by nearest header centre."""
    cols = [(x, k) for k, x in anchors.items()] + [(x, f"w{j}") for j, (x, _) in enumerate(weeks)]
    name: list[str] = []
    cells: dict[str, list[str]] = {}
    for w in row:
        if w["x1"] <= name_limit:
            name.append(w["text"])
            continue
        if w["text"] == "$":
            continue
        cx = _centre(w)
        key = min(cols, key=lambda c: abs(c[0] - cx))[1]
        cells.setdefault(key, []).append(w["text"])
    return " ".join(name).strip(), {k: "".join(v) for k, v in cells.items()}


def _grid(
    rows: list[list[dict]], length_hint: int
) -> tuple[list[dict], dict, list[tuple[float, str]]]:
    hi, anchors, weeks = _find_header(rows)
    name_limit = anchors["per30"] - 30.0
    pending_title: list[str] = []
    data: list[dict] = []
    totals: dict = {}
    for row in rows[hi + 1 :]:
        texts = [w["text"] for w in row]
        if texts and texts[0].lower() == "total":
            _, cells = _assign(row, anchors, weeks, name_limit)
            totals = cells
            break
        name, cells = _assign(row, anchors, weeks, name_limit)
        units = _int(cells.get("units", ""))
        has_weeks = any(k.startswith("w") for k in cells)
        if units is None and not has_weeks:
            if name and not cells:
                pending_title.append(name)  # a wrapped row name, claimed by the NEXT data row
            elif cells.keys() <= {"impressions"} and data and data[-1].get("impressions") is None:
                data[-1]["impressions"] = _int(cells["impressions"])  # a wrapped impressions cell
            continue
        if units is None:
            raise ILLotteryParseError(f"row {name!r} has week cells but no Units")
        full_name = " ".join(pending_title + [name]).strip()
        pending_title = []
        data.append(
            {
                "name": full_name,
                "cells": cells,
                "units": units,
                "impressions": _int(cells.get("impressions", "")),
            }
        )
    if pending_title:
        raise ILLotteryParseError(f"dangling row name with no schedule row: {pending_title}")
    if not data:
        raise ILLotteryParseError("schedule header found but no rows under it")
    if not totals:
        raise ILLotteryParseError("no Total row under the schedule")
    return data, totals, weeks


# ─── parse ────────────────────────────────────────────────────────────────────


def parse_illottery(path: str) -> ILLotteryOrder:
    with pdfplumber.open(path) as pdf:
        words: list[dict] = []
        texts: list[str] = []
        for pg in pdf.pages:
            words.extend(pg.extract_words(y_tolerance=1.0))
            texts.append(pg.extract_text() or "")
    text = "\n".join(texts)
    if not is_illottery_text(text):
        raise ILLotteryParseError("not an Illinois Lottery / Flowers proposal")

    agency = _rx(text, r"^Client\s+(.+?)\s*$") or AGENCY_NAME
    advertiser = _rx(text, r"^Advertiser\s+(.+?)\s*$") or ADVERTISER_NAME
    market_name = _rx(text, r"^Market\s+(.+?)\s*$")
    market_code = next((c for k, c in _MARKET_BY_NAME if k in market_name.lower()), "")
    flight_estimate = _rx(text, r"^Flight Estimate\s+(.+?)\s*$")
    contact = _rx(text, r"^Contact\s+(.+?)\s*$")
    email = _rx(text, r"^Email\s+(\S+@\S+)")
    channel = _rx(text, r"^Channel\s+(.+?)\s*$")
    asian_group = _rx(text, r"^Asian Group\s+(.+?)\s*$")
    gross = _money(_rx(text, r"^Gross\s+\$\s*([\d ,.]+?)\s*$"))
    net = _money(_rx(text, r"^Net\s+\$\s*([\d ,.]+?)\s*$"))
    if gross is None or net is None:
        raise ILLotteryParseError("Gross / Net header amounts not found")
    pd_raw = _rx(text, r"Proposal Date\s+(\d{1,2}/\d{1,2}/\d{4})")
    proposal_date = None
    if pd_raw:
        mo, d, y = (int(x) for x in pd_raw.split("/"))
        proposal_date = date(y, mo, d)
    holiday = _rx(text, r"^Holiday\s+(.+?)\s*$")
    m = re.search(r"Programming Schedule\s*:\s*(\d{2,3})\s*sec", text, re.IGNORECASE)
    length_sec = int(m.group(1)) if m else 30

    data, totals, weeks = _grid(_rows(words), length_sec)

    years = _week_years(flight_estimate)
    week_dates: list[date] = []
    for _, label in weeks:
        dm = _WEEK_LABEL.match(label)
        day, mon = int(dm.group(1)), _MONTHS.get(dm.group(2).lower())
        if mon is None or mon not in years:
            raise ILLotteryParseError(
                f"week column {label!r} is outside the Flight Estimate months {flight_estimate!r}"
            )
        week_dates.append(date(years[mon], mon, day))
    for i, d in enumerate(week_dates):
        if d.weekday() != 0:
            raise ILLotteryParseError(f"week column {weeks[i][1]} = {d} is not a Monday")
        if i and d <= week_dates[i - 1]:
            raise ILLotteryParseError(f"week columns do not ascend at {weeks[i][1]} ({d})")

    lines: list[ILLotteryLine] = []
    for row in data:
        name, cells = row["name"], row["cells"]
        block, daypart = split_block_daypart(name)
        is_bonus = block.lower().startswith("bonus")
        week_spots = [_int(cells.get(f"w{j}", "")) or 0 for j in range(len(weeks))]
        for j in range(len(weeks)):
            raw = cells.get(f"w{j}", "")
            if raw and _int(raw) is None:
                raise ILLotteryParseError(
                    f"{name}: unreadable week cell {raw!r} under {weeks[j][1]}"
                )
        per30, per15, rate = (_money(cells.get(k, "")) for k in ("per30", "per15", "rate"))
        value, proposed = _money(cells.get("value", "")), _money(cells.get("proposed", ""))
        if per30 is None or per15 is None or rate is None or value is None or proposed is None:
            raise ILLotteryParseError(f"{name}: unreadable money cell(s) {cells}")
        if is_bonus:
            block = re.sub(r"^bonus\s+", "", block, flags=re.I)
        units = row["units"]
        if sum(week_spots) != units:
            raise ILLotteryParseError(
                f"{name}: week cells sum to {sum(week_spots)} but Units says {units}"
            )
        if abs(round(rate * units, 2) - proposed) > 0.005:
            raise ILLotteryParseError(
                f"{name}: {rate} x {units} = {round(rate * units, 2)} but Proposed says {proposed}"
            )
        if abs(round(per15 * units, 2) - value) > 0.005:
            raise ILLotteryParseError(
                f"{name}: {per15} x {units} = {round(per15 * units, 2)} but Value says {value}"
            )
        if is_bonus and rate:
            raise ILLotteryParseError(f"{name}: bonus row carries a rate {rate}")
        if not is_bonus and not rate:
            raise ILLotteryParseError(f"{name}: paid row has no rate")
        if not is_bonus and not daypart:
            raise ILLotteryParseError(f"{name}: paid row has no day/time")
        lines.append(
            ILLotteryLine(
                block=block,
                daypart=daypart,
                rate_per30=per30,
                rate_per15=per15,
                rate=rate,
                is_bonus=is_bonus,
                week_spots=week_spots,
                units=units,
                value=value,
                proposed=proposed,
                impressions=row["impressions"],
            )
        )

    order = ILLotteryOrder(
        agency=agency,
        advertiser=advertiser,
        market_name=market_name,
        market_code=market_code,
        flight_estimate=flight_estimate,
        contact=contact,
        email=email,
        channel=channel,
        asian_group=asian_group,
        gross_stated=gross,
        net_stated=net,
        proposal_date=proposal_date,
        holiday=holiday,
        length_sec=length_sec,
        week_dates=week_dates,
        lines=lines,
        total_units_stated=_int(totals.get("units", "")) or 0,
        total_value_stated=_money(totals.get("value", "")) or 0.0,
        total_proposed_stated=_money(totals.get("proposed", "")) or 0.0,
    )

    # Reconcile against the sheet's own totals - RAISE, never enter short.
    if order.total_spots != order.total_units_stated:
        raise ILLotteryParseError(
            f"rows total {order.total_spots} spots but Total says {order.total_units_stated}"
        )
    if abs(round(sum(ln.value for ln in lines), 2) - order.total_value_stated) > 0.005:
        raise ILLotteryParseError(
            f"rows Value {round(sum(ln.value for ln in lines), 2)} vs Total {order.total_value_stated}"
        )
    if abs(order.gross_total - order.total_proposed_stated) > 0.005:
        raise ILLotteryParseError(
            f"rows Proposed ${order.gross_total:,.2f} vs Total ${order.total_proposed_stated:,.2f}"
        )
    if abs(order.gross_total - gross) > 0.005:
        raise ILLotteryParseError(
            f"rows Proposed ${order.gross_total:,.2f} vs header Gross ${gross:,.2f}"
        )
    if not order.paid_lines:
        raise ILLotteryParseError("no paid lines")
    return order


if __name__ == "__main__":  # pragma: no cover
    import sys

    o = parse_illottery(sys.argv[1])
    print(
        f"{o.advertiser} via {o.agency}   {o.market_name} ({o.market_code})   :{o.length_sec}   {o.flight_estimate}"
    )
    print(
        f"Weeks {o.flight_start} -> {o.flight_end} ({len(o.week_dates)})   gross ${o.gross_stated:,.2f} net ${o.net_stated:,.2f} (implied {o.implied_commission}%)   {o.holiday}"
    )
    for ln in o.lines:
        tag = "BNS" if ln.is_bonus else "   "
        print(
            f"  {tag} {ln.block[:34]:34} {ln.daypart:<18} ${ln.rate:>6.2f}  {ln.week_spots}  = {ln.units:>4}  ${ln.proposed:>9,.2f}"
        )
    print(
        f"  {sum(x.total_spots for x in o.paid_lines)} paid + {sum(x.total_spots for x in o.bonus_lines)} bonus = {o.total_spots}   gross ${o.gross_total:,.2f}"
    )
