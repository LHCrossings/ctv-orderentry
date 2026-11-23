"""
TCAA annual grid reader — Charmaine's "Crossings TV _TCAA_<year>.xlsx" proposal.

A different sheet from the standard media proposal (`crispin_parser.parse_crispin_xlsx`):

    G1  TCAA                       <- the agency
    G2  Western Washington Toyota  <- the advertiser
    H3  2027                       <- the year
    B5  Station | CRTV-Cable
    row 7   January | February | ... | TOTAL        (month banners, merged)
    row 8   Program | TIME | Gross | <week date> ...  (three weeks per month)
    rows    "M-Sat  Mandarin News" | 6a-7a | 25 | 12 | 12 | ...   paid rows
            "Chinese ROS"          | 6a-12a | 0 | 10 | 10 | ...   bonus rows ($0)
    footer  Total Paid / Total Bonuses (per week), Total Spots / Gross (per month + year)

The day pattern is the first token of the Program cell; a $0 row with no day token
runs all week inside its TIME window. The sheet states no spot length (`length_sec`
is 0 = not stated; the export page asks for it) and no buyer contact.

Returns the same `CrispinOrder` the standard reader returns, so the Agency XML
exporter does not learn there are two layouts. Every week date must be a Monday,
ascending, in the sheet's own year; every row and the whole grid are reconciled
against the sheet's totals. Anything that fails raises with the cell named.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import List, Optional

import openpyxl
from openpyxl.utils import get_column_letter

from browser_automation.parsers.crispin_parser import CrispinLine, CrispinOrder

_DAY_TOKEN = r"(?:m|mon|t|tu|tue|tues|w|wed|th|thu|thur|thurs|f|fri|sa|sat|su|sun)"
_DAY_PREFIX = re.compile(rf"^({_DAY_TOKEN}(?:\s*[-–]\s*{_DAY_TOKEN})?)\s+(.+)$", re.IGNORECASE)
_HEADER_SCAN_ROWS = 20


class TcaaGridError(ValueError):
    """The TCAA grid could not be read without a guess."""


def _norm(v) -> str:
    return " ".join(str(v).split()).lower() if v is not None else ""


def _header_row(ws) -> Optional[int]:
    for r, row in enumerate(
        ws.iter_rows(min_row=1, max_row=_HEADER_SCAN_ROWS, values_only=True), start=1
    ):
        cells = {_norm(c) for c in row if c is not None}
        if {"program", "time", "gross"} <= cells:
            return r
    return None


def is_tcaa_grid_sheet(ws) -> bool:
    return _header_row(ws) is not None


def tcaa_grid_sheet_names(path: str) -> List[str]:
    wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
    try:
        return [ws.title for ws in wb.worksheets if is_tcaa_grid_sheet(ws)]
    finally:
        wb.close()


def _cell(col: int, row: int) -> str:
    return f"{get_column_letter(col)}{row}"


def _number(v, where: str) -> float:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise TcaaGridError(f"cell {where} should hold a number, found {v!r}")
    return float(v)


def _count(v, where: str) -> int:
    if v is None or v == "":
        return 0
    n = _number(v, where)
    if n < 0 or n != int(n):
        raise TcaaGridError(f"cell {where} should hold a whole spot count, found {v!r}")
    return int(n)


def _segments(ws, row: int, week_cols: List[int]) -> List[tuple]:
    """A footer row's cells → [(first week index, last week index, value)].

    A value covers the week columns from its own cell up to the next filled cell
    (the month totals sit in the first of three merged week columns)."""
    filled = [i for i, c in enumerate(week_cols) if ws.cell(row, c).value not in (None, "")]
    out = []
    for k, i in enumerate(filled):
        last = (filled[k + 1] - 1) if k + 1 < len(filled) else len(week_cols) - 1
        out.append((i, last, _number(ws.cell(row, week_cols[i]).value, _cell(week_cols[i], row))))
    return out


def parse_tcaa_grid_xlsx(path: str, sheet_name: Optional[str] = None) -> CrispinOrder:
    wb = openpyxl.load_workbook(path, data_only=True)
    if sheet_name is not None:
        if sheet_name not in wb.sheetnames:
            raise TcaaGridError(f"tab '{sheet_name}' is not in the workbook")
        ws = wb[sheet_name]
    else:
        ws = next((s for s in wb.worksheets if is_tcaa_grid_sheet(s)), None)
        if ws is None:
            raise TcaaGridError("no tab has the Program / TIME / Gross header row")
    hr = _header_row(ws)
    if hr is None:
        raise TcaaGridError(f"tab '{ws.title}' has no Program / TIME / Gross header row")

    # ── columns by header label ──
    cols = {}
    week_cols: List[int] = []
    week_dates: List[date] = []
    for c in range(1, ws.max_column + 1):
        v = ws.cell(hr, c).value
        if isinstance(v, datetime):
            v = v.date()
        if isinstance(v, date):
            week_cols.append(c)
            week_dates.append(v)
        elif _norm(v) in ("program", "time", "gross"):
            cols[_norm(v)] = c
    if not week_dates:
        raise TcaaGridError(f"row {hr} has no week date cells")
    total_col = None
    for r in range(1, hr + 1):
        for c in range(week_cols[-1] + 1, ws.max_column + 1):
            if _norm(ws.cell(r, c).value) == "total":
                total_col = c

    # ── header block above the grid ──
    year = None
    titles: List[str] = []
    station = ""
    for r in range(1, hr):
        row_vals = [ws.cell(r, c).value for c in range(1, ws.max_column + 1)]
        labels = [_norm(v) for v in row_vals]
        if "station" in labels:
            rest = [v for v in row_vals[labels.index("station") + 1 :] if v not in (None, "")]
            station = " ".join(str(rest[0]).split()) if rest else ""
            continue
        for v in row_vals:
            if isinstance(v, (int, float)) and not isinstance(v, bool) and 2000 <= v <= 2100:
                year = int(v)
            elif isinstance(v, str) and v.strip() and _norm(v) != "total":
                if _norm(v) not in _MONTHS:
                    titles.append(" ".join(v.split()))
    if len(titles) < 2:
        raise TcaaGridError(
            "the agency and advertiser lines above the grid were not found "
            f"(read {titles or 'nothing'})"
        )
    agency, advertiser = titles[0], titles[1]

    # ── week dates: Monday, ascending, in the sheet's year ──
    for i, (c, d) in enumerate(zip(week_cols, week_dates)):
        where = _cell(c, hr)
        if d.weekday() != 0:
            raise TcaaGridError(
                f"week date {where} is {d.month}/{d.day}/{d.year}, a {d.strftime('%A')}; "
                "every week column must start on a Monday"
            )
        if i and d <= week_dates[i - 1]:
            raise TcaaGridError(
                f"week date {where} ({d.month}/{d.day}/{d.year}) is not after the column before it"
            )
        if year is not None and d.year != year:
            raise TcaaGridError(
                f"week date {where} is {d.month}/{d.day}/{d.year} but the sheet says {year}"
            )

    # ── rows ──
    lines: List[CrispinLine] = []
    footer = {}
    for r in range(hr + 1, ws.max_row + 1):
        raw = ws.cell(r, cols["program"]).value
        label = _norm(raw)
        if not label:
            continue
        if label in ("total paid", "total bonuses", "total bonus", "total spots", "gross"):
            footer[label.replace("bonuses", "bonus")] = r
            continue
        if footer:
            continue  # closing remarks below the footer
        rate_cell = ws.cell(r, cols["gross"]).value
        if rate_cell in (None, ""):
            raise TcaaGridError(f"row {r} ('{raw}') has no rate in the Gross column")
        rate = _number(rate_cell, _cell(cols["gross"], r))
        time_txt = "".join(str(ws.cell(r, cols["time"]).value or "").split())
        if not time_txt:
            raise TcaaGridError(f"row {r} ('{raw}') has no time window")
        text = " ".join(str(raw).split())
        m = _DAY_PREFIX.match(text)
        if m:
            days, program = re.sub(r"\s*[-–]\s*", "-", m.group(1)), m.group(2)
        elif rate == 0:
            days, program = "M-Su", text  # a bonus row runs all week inside its window
        else:
            raise TcaaGridError(f"row {r}: paid row '{text}' does not start with a day pattern")
        spots = [_count(ws.cell(r, c).value, _cell(c, r)) for c in week_cols]
        if total_col is not None:
            stated = _count(ws.cell(r, total_col).value, _cell(total_col, r))
            if stated != sum(spots):
                raise TcaaGridError(
                    f"row {r} ('{text}'): week cells add to {sum(spots)}, TOTAL says {stated}"
                )
        lines.append(
            CrispinLine(
                language_block=program,
                daypart=f"{days} {time_txt}",
                unit_value=rate,
                rate=rate,
                length_sec=0,
                week_dates=list(week_dates),
                week_spots=spots,
                total_spots_stated=sum(spots),
            )
        )
    if not lines:
        raise TcaaGridError("the grid has no airtime rows")
    for need in ("total paid", "gross"):
        if need not in footer:
            raise TcaaGridError(f"footer row '{need.title()}' was not found")

    # ── reconcile against the sheet's own footer ──
    def _check(label: str, values: List[float], what: str) -> None:
        r = footer.get(label)
        if r is None:
            return
        for first, last, stated in _segments(ws, r, week_cols):
            got = sum(values[first : last + 1])
            if round(got, 2) != round(stated, 2):
                raise TcaaGridError(
                    f"{what} from {_cell(week_cols[first], r)}: rows add to {got:,.2f}, "
                    f"the sheet says {stated:,.2f}"
                )
        if total_col is not None and ws.cell(r, total_col).value not in (None, ""):
            stated = _number(ws.cell(r, total_col).value, _cell(total_col, r))
            if round(sum(values), 2) != round(stated, 2):
                raise TcaaGridError(
                    f"{what} for the year: rows add to {sum(values):,.2f}, "
                    f"the sheet says {stated:,.2f}"
                )

    n = len(week_cols)
    paid = [sum(ln.week_spots[i] for ln in lines if not ln.is_bonus) for i in range(n)]
    bonus = [sum(ln.week_spots[i] for ln in lines if ln.is_bonus) for i in range(n)]
    money = [sum(ln.week_spots[i] * ln.rate for ln in lines) for i in range(n)]
    _check("total paid", paid, "Total Paid")
    _check("total bonus", bonus, "Total Bonuses")
    _check("total spots", [p + b for p, b in zip(paid, bonus)], "Total Spots")
    _check("gross", money, "Gross")

    seattle = "washington" in advertiser.lower()
    return CrispinOrder(
        agency=agency,
        advertiser=advertiser,
        contact="",
        email="",
        market_code="SEA" if seattle else "",
        market_label=" - ".join(p for p in ("Seattle" if seattle else "", station) if p),
        order_date=None,
        lines=lines,
        rates_are_net=False,
        source_path=str(path),
        title=f"Crossings TV: {agency}",
        sheet_name=ws.title,
    )


_MONTHS = (
    "january",
    "february",
    "march",
    "april",
    "may",
    "june",
    "july",
    "august",
    "september",
    "october",
    "november",
    "december",
)
