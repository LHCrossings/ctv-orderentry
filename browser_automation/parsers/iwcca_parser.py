"""IW Group / Covered California — "TELEVISION ORDER" insertion-order PDF parser.

Source: the IOs IW Group issues for Covered California (ANAGRAF 386), one PDF per
language, e.g. "CCA_Crossings TV_000035382_Chinese.pdf". Two pages:

  page 1 header  Station: <Crossings TV | KBTV (Crossings TV)>
                 Flight Dates: 10/05/2026 - 12/20/2026 (11 weeks)  OrderNo: 35382  Date: 9/22/2026
                 Campaign: FY26-27 Covered California Brannding -  (wraps to a 2nd line)
                 Description: Crossings TV - Y26-27 Brand Awareness Ch
  page 1 grid    Program | Days Time DP Len | <12 week columns> | Total Spots | Cost/Spot | Total Net
                 each line = a DATA row (M-F 8p-10p 30 10 10 … 77 50.00 3,850.00)
                 followed by a DESCRIPTION row ("Mandarin - :30 – Rooted in Care …";
                 an "AV " prefix marks an added-value line → entered as BNS, Lee)
                 Subtotal: <per-week sums> <total> <net>  /  Total Net: <net>
  summary        OCT '26 NOV '26 DEC '26 Total / Spots a b c T / Total Net x y z T
                 (broadcast months: a week belongs to the month whose 1st it contains)

Money basis: NET (the IO says "Please bill invoice NET"). `rates_are_net` is True;
entry grosses up from the ANAGRAF agency commission (IW Group 15%), the backwrite
takes the NET per-spot rate and grosses at full precision.

Geometry: the week grid prints ZERO CELLS AS BLANKS (the flight has dark weeks), so
a cell is matched to its week by centre distance against the day-number header
(Crispin Brand Time Schedule rule). Rows are clustered on RAW `top` floats.
Field columns are read by header LABEL, never by index (DART rule).

Everything the IO prints is reconciled and a mismatch RAISES:
  * per line: Σ week cells == Total Spots, Total Spots × Cost/Spot == Total Net,
    AV line ⇒ $0
  * Subtotal row: per-week sums and totals
  * monthly summary: spots and net per broadcast month
  * Total Net
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

_ROW_TOL = 2.0  # cluster words into rows on raw `top`; row pitch here is ~12pt
_COL_TOL = 8.0  # week-cell centre vs header day-number centre; pitch is 24pt
_TAIL_PAD = 14.0  # right of the last week column: Total Spots / Cost/Spot / Total Net

_MONTHS = {
    "JAN": 1,
    "FEB": 2,
    "MAR": 3,
    "APR": 4,
    "MAY": 5,
    "JUN": 6,
    "JUL": 7,
    "AUG": 8,
    "SEP": 9,
    "OCT": 10,
    "NOV": 11,
    "DEC": 12,
}

_LANGUAGES = [
    ("MANDARIN", "Mandarin"),
    ("CANTONESE", "Cantonese"),
    ("VIETNAMESE", "Vietnamese"),
    ("VIET", "Vietnamese"),
    ("FILIPINO", "Filipino"),
    ("TAGALOG", "Filipino"),
    ("TAGLISH", "Filipino"),
    ("KOREAN", "Korean"),
    ("HMONG", "Hmong"),
    ("PUNJABI", "Punjabi"),
    ("HINDI", "Hindi"),
    ("SOUTH ASIAN", "South Asian"),
    ("JAPANESE", "Japanese"),
    ("CHINESE", "Chinese"),
]

_DAYS = {
    "M-SU": "M-Su",
    "M-SA": "M-Sa",
    "M-F": "M-F",
    "SA-SU": "Sa-Su",
    "SA": "Sa",
    "SU": "Su",
    "M-TH": "M-Th",
}
_TIME_RE = re.compile(r"^\d{1,2}(?::\d{2})?[ap]-\d{1,2}(?::\d{2})?[ap]$", re.I)


class IWCCAParseError(ValueError):
    """The IO could not be read or does not reconcile — never enter it."""


def _norm(s) -> str:
    return " ".join(str(s or "").split())


def _num(text: str) -> Optional[float]:
    s = (text or "").replace(",", "").replace("$", "").strip()
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _mmddyyyy(d: date) -> str:
    return f"{d.month:02d}/{d.day:02d}/{d.year}"


def _centre(w: dict) -> float:
    return (w["x0"] + w["x1"]) / 2.0


def _cluster_rows(words: List[dict]) -> List[Tuple[float, List[dict]]]:
    """Group words into visual rows on RAW `top`, ascending (never round first)."""
    rows: List[Tuple[float, List[dict]]] = []
    for w in sorted(words, key=lambda w: w["top"]):
        if rows and w["top"] - rows[-1][0] <= _ROW_TOL:
            rows[-1][1].append(w)
        else:
            rows.append((w["top"], [w]))
    return [(top, sorted(ws, key=lambda w: w["x0"])) for top, ws in rows]


def broadcast_month(week_start: date) -> Tuple[int, int]:
    """(year, month) of the broadcast month a Mon–Sun week belongs to: the month
    whose 1st falls inside the week, else the month the week starts in."""
    for k in range(7):
        d = week_start + timedelta(days=k)
        if d.day == 1:
            return d.year, d.month
    return week_start.year, week_start.month


@dataclass
class IWCCALine:
    days: str  # canonical 'M-F'
    time: str  # '8p-10p' as printed
    dp: str  # daypart code column ('P' or '')
    length_sec: int
    weekly_spots: List[int]  # one entry per week column, zeros for blank cells
    total_spots: int
    net_rate: float  # per spot, NET
    net_total: float
    description_text: str  # the description row, 'AV ' prefix removed
    language: str
    is_bonus: bool

    @property
    def rate(self) -> float:  # duck-typed by the generic normalizer
        return self.net_rate

    @property
    def duration(self) -> str:
        return str(self.length_sec)

    @property
    def description(self) -> str:
        return f"{'BNS ' if self.is_bonus else ''}{self.days} {self.time} {self.language} :{self.length_sec}"


@dataclass
class IWCCAOrder:
    station: str
    flight_start: str  # MM/DD/YYYY
    flight_end: str
    weeks_stated: int
    order_no: str  # OrderNo → Etere Customer Order Ref
    io_date: str
    campaign: str
    description_io: str  # the IO's own Description field (verbatim)
    language: str  # order-level: 'Chinese' / 'Vietnamese' / 'Filipino'
    market_hint: str  # 'CVC' when the station line names KBTV, else ''
    week_start_dates: List[str] = field(default_factory=list)  # MM/DD/YYYY per column
    lines: List[IWCCALine] = field(default_factory=list)
    total_net: float = 0.0
    monthly: List[Tuple[Tuple[int, int], int, float]] = field(
        default_factory=list
    )  # ((y,m), spots, net)
    source_path: str = ""
    rates_are_net: bool = True

    # duck-typed by the generic normalizer
    @property
    def client(self) -> str:
        return "Covered California"

    @property
    def agency(self) -> str:
        return "IW Group"

    @property
    def estimate_number(self) -> str:
        return self.order_no

    @property
    def description(self) -> str:
        return self.campaign

    @property
    def market(self) -> str:
        return self.market_hint

    @property
    def notes(self) -> str:
        """What goes into the Etere contract notes: Campaign | Description (Lee)."""
        return " | ".join(p for p in (self.campaign, self.description_io) if p)

    @property
    def paid_lines(self) -> List[IWCCALine]:
        return [ln for ln in self.lines if not ln.is_bonus]

    @property
    def bonus_lines(self) -> List[IWCCALine]:
        return [ln for ln in self.lines if ln.is_bonus]

    @property
    def total_spots(self) -> int:
        return sum(ln.total_spots for ln in self.lines)

    @property
    def total_cost(self) -> float:
        return round(sum(ln.net_total for ln in self.lines), 2)


# ─── Extraction (patchable in tests) ────────────────────────────────────────


def _extract(path: str) -> List[Tuple[str, List[dict]]]:
    """[(page text, page words)] — the only pdfplumber touch point."""
    import pdfplumber

    out: List[Tuple[str, List[dict]]] = []
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            out.append((page.extract_text() or "", page.extract_words(y_tolerance=0.5)))
    return out


# ─── Detection ───────────────────────────────────────────────────────────────


def is_iwcca_text(text: str) -> bool:
    """Detector: IW Group's 'TELEVISION ORDER' form with Covered California as the
    campaign client (the CLIENT is the definer — 'IW Group' alone is Lexus too)."""
    t = " ".join(text.split()).lower()
    return "television order" in t and "covered california" in t and "orderno" in t


def language_from_filename(path: str) -> str:
    stem = Path(path).stem.upper()
    for key, lang in _LANGUAGES:
        if re.search(rf"(^|[^A-Z]){key}([^A-Z]|$)", stem):
            return lang
    return ""


def _language_in(text: str) -> str:
    upper = text.upper()
    for key, lang in _LANGUAGES:
        if re.search(rf"(^|[^A-Z]){re.escape(key)}([^A-Z]|$)", upper):
            return lang
    return ""


# ─── Parse ───────────────────────────────────────────────────────────────────


def parse_iwcca(path: str) -> IWCCAOrder:
    pages = _extract(path)
    if not pages:
        raise IWCCAParseError("empty PDF")
    full_text = "\n".join(t for t, _ in pages)
    if not is_iwcca_text(full_text):
        raise IWCCAParseError("not an IW Group / Covered California TELEVISION ORDER")

    text1, words1 = pages[0]

    # ── header ──
    m = re.search(
        r"Station:\s*(.*?)\s+Flight Dates:\s*(\d{1,2}/\d{1,2}/\d{4})\s*-\s*(\d{1,2}/\d{1,2}/\d{4})"
        r"\s*\((\d+)\s*weeks?\)\s*OrderNo:\s*(\d+)\s*Date:\s*(\d{1,2}/\d{1,2}/\d{4})",
        text1,
    )
    if not m:
        raise IWCCAParseError("Station / Flight Dates / OrderNo header line not found")
    station, fs, fe, weeks_stated, order_no, io_date = m.groups()
    flight_start = datetime.strptime(fs, "%m/%d/%Y").date()
    flight_end = datetime.strptime(fe, "%m/%d/%Y").date()
    if flight_end < flight_start:
        raise IWCCAParseError(f"flight ends before it starts: {fs} - {fe}")

    lines_txt = [_norm(ln) for ln in text1.splitlines()]
    campaign_parts: List[str] = []
    description_io = ""
    for i, ln in enumerate(lines_txt):
        if ln.startswith("Campaign:"):
            body = re.sub(r"\s*Page:\s*\d+\s*of\s*\d+\s*$", "", ln[len("Campaign:") :]).strip()
            campaign_parts.append(body)
            for cont in lines_txt[i + 1 :]:
                if cont.startswith("Description:") or not cont:
                    break
                campaign_parts.append(cont)
        elif ln.startswith("Description:"):
            description_io = ln[len("Description:") :].strip()
    campaign = " ".join(p for p in campaign_parts if p).strip()
    if not campaign:
        raise IWCCAParseError("Campaign line not found")

    market_hint = "CVC" if "KBTV" in station.upper() else ""
    order_language = language_from_filename(path)

    # ── grid geometry: month row + day-number row ──
    rows = _cluster_rows(words1)
    day_hdr: Optional[Tuple[float, List[dict]]] = None
    month_hdr: Optional[List[dict]] = None
    for idx, (top, ws) in enumerate(rows):
        nums = [w for w in ws if re.fullmatch(r"\d{2}", w["text"])]
        if len(nums) >= 6 and any(w["text"] == "Spots" for w in ws):
            day_hdr = (top, nums)
            # month names sit on the row(s) just above
            for _, prev in reversed(rows[:idx]):
                mons = [w for w in prev if w["text"].upper() in _MONTHS]
                if len(mons) >= len(nums):
                    month_hdr = mons
                    break
            break
    if day_hdr is None or month_hdr is None:
        raise IWCCAParseError(
            "week header (month / day-number rows) not found — cannot align the grid"
        )
    day_ws = sorted(day_hdr[1], key=lambda w: w["x0"])
    mon_ws = sorted(month_hdr, key=lambda w: w["x0"])
    if len(mon_ws) != len(day_ws):
        raise IWCCAParseError(
            f"{len(mon_ws)} month labels vs {len(day_ws)} day numbers in the week header"
        )
    weeks: List[Tuple[float, date]] = []
    year, prev_month = flight_start.year, None
    for mo_w, dy_w in zip(mon_ws, day_ws):
        month = _MONTHS[mo_w["text"].upper()]
        if prev_month is not None and month < prev_month:
            year += 1
        prev_month = month
        weeks.append((_centre(dy_w), date(year, month, int(dy_w["text"]))))
    for a, b in zip(weeks, weeks[1:]):
        if (b[1] - a[1]).days != 7:
            raise IWCCAParseError(f"week columns {a[1]} → {b[1]} are not 7 days apart")
    if weeks[0][1].weekday() != 0:
        raise IWCCAParseError(f"first week column {weeks[0][1]} is not a Monday")
    grid_lo = weeks[0][0] - _COL_TOL - 4
    grid_hi = weeks[-1][0] + _COL_TOL + 4

    def week_cells(ws: List[dict]) -> List[int]:
        out = [0] * len(weeks)
        for w in ws:
            cx = _centre(w)
            if cx < grid_lo or cx > grid_hi:
                continue
            val = _num(w["text"])
            if val is None or val != int(val):
                continue
            best = None
            for k, (col_x, _) in enumerate(weeks):
                dist = abs(cx - col_x)
                if dist <= _COL_TOL and (best is None or dist < best[0]):
                    best = (dist, k)
            if best is None:
                raise IWCCAParseError(
                    f"grid value {w['text']!r} at x={cx:.0f} sits between week columns"
                )
            out[best[1]] = int(val)
        return out

    def tail(ws: List[dict]) -> List[float]:
        """Numeric tokens right of the week grid, in print order."""
        vals: List[float] = []
        for w in ws:
            if w["x0"] < grid_hi + _TAIL_PAD - _COL_TOL:
                continue
            v = _num(w["text"])
            if v is not None:
                vals.append(v)
        return vals

    # ── field columns by header label ──
    label_x: Dict[str, float] = {}
    for top, ws in rows:
        texts = {w["text"] for w in ws}
        if {"Days", "Time", "Len"} <= texts:
            for w in ws:
                if w["text"] in ("Days", "Time", "DP", "Len"):
                    label_x[w["text"]] = _centre(w)
            break
    for lbl in ("Days", "Time", "Len"):
        if lbl not in label_x:
            raise IWCCAParseError(f"column {lbl!r} missing from the grid header")

    def near(ws: List[dict], lbl: str, tol: float = 22.0) -> str:
        x = label_x.get(lbl)
        if x is None:
            return ""
        cands = [w for w in ws if abs(_centre(w) - x) <= tol and _centre(w) < grid_lo]
        return " ".join(w["text"] for w in sorted(cands, key=lambda w: w["x0"]))

    # ── data rows + their description rows ──
    data_idx = [
        i
        for i, (_, ws) in enumerate(rows)
        if ws and ws[0]["text"].upper() in _DAYS and any(_TIME_RE.match(w["text"]) for w in ws[:3])
    ]
    if not data_idx:
        raise IWCCAParseError("no schedule rows found")
    subtotal_row = next((ws for _, ws in rows if ws and ws[0]["text"].startswith("Subtotal")), None)
    if subtotal_row is None:
        raise IWCCAParseError("Subtotal row not found")

    parsed: List[IWCCALine] = []
    for n, i in enumerate(data_idx):
        _, ws = rows[i]
        nxt = data_idx[n + 1] if n + 1 < len(data_idx) else None
        desc_words: List[str] = []
        for j in range(i + 1, nxt if nxt is not None else len(rows)):
            row_ws = rows[j][1]
            if row_ws and row_ws[0]["text"].startswith("Subtotal"):
                break
            desc_words.extend(w["text"] for w in row_ws)
        desc_raw = _norm(" ".join(desc_words))
        if not desc_raw:
            raise IWCCAParseError(f"schedule row {n + 1} has no description row")
        is_bonus = bool(re.match(r"^AV\b", desc_raw))
        desc_text = re.sub(r"^AV\s*", "", desc_raw).strip()

        days_raw = near(ws, "Days", 12).upper()
        days = _DAYS.get(days_raw)
        if not days:
            raise IWCCAParseError(f"row {n + 1}: unrecognised day pattern {days_raw!r}")
        time_raw = near(ws, "Time", 20)
        if not _TIME_RE.match(time_raw or ""):
            raise IWCCAParseError(f"row {n + 1}: unreadable time {time_raw!r}")
        dp = near(ws, "DP", 8) if "DP" in label_x else ""
        len_raw = near(ws, "Len", 8)
        if not len_raw.isdigit():
            raise IWCCAParseError(f"row {n + 1}: unreadable length {len_raw!r}")

        cells = week_cells(ws)
        t = tail(ws)
        if len(t) != 3:
            raise IWCCAParseError(
                f"row {n + 1} {days} {time_raw}: expected Total Spots / Cost per Spot / Total Net, got {t}"
            )
        total_spots, rate, net_total = int(t[0]), t[1], t[2]
        if sum(cells) != total_spots:
            raise IWCCAParseError(
                f"row {n + 1} {days} {time_raw}: week cells sum to {sum(cells)} ≠ printed Total Spots {total_spots}"
            )
        if abs(total_spots * rate - net_total) > 0.005:
            raise IWCCAParseError(
                f"row {n + 1} {days} {time_raw}: {total_spots} × {rate:.2f} = {total_spots * rate:.2f} ≠ printed {net_total:.2f}"
            )
        if is_bonus and (rate or net_total):
            raise IWCCAParseError(
                f"row {n + 1}: AV line carries money ({rate:.2f} / {net_total:.2f})"
            )
        if not is_bonus and rate <= 0:
            raise IWCCAParseError(f"row {n + 1} {days} {time_raw}: paid line with no rate")
        for k, v in enumerate(cells):
            if v and (weeks[k][1] > flight_end or weeks[k][1] + timedelta(days=6) < flight_start):
                raise IWCCAParseError(
                    f"row {n + 1}: {v} spot(s) in the week of {weeks[k][1]} lie outside the flight"
                )

        language = _language_in(desc_text) or order_language
        if not language:
            raise IWCCAParseError(f"row {n + 1}: no language in {desc_text!r} or the file name")
        parsed.append(
            IWCCALine(
                days=days,
                time=time_raw,
                dp=dp,
                length_sec=int(len_raw),
                weekly_spots=cells,
                total_spots=total_spots,
                net_rate=rate,
                net_total=net_total,
                description_text=desc_text,
                language=language,
                is_bonus=is_bonus,
            )
        )

    # ── Subtotal row ──
    sub_cells = week_cells(subtotal_row)
    sub_tail = tail(subtotal_row)
    col_sums = [sum(ln.weekly_spots[k] for ln in parsed) for k in range(len(weeks))]
    if sub_cells != col_sums:
        raise IWCCAParseError(f"Subtotal week cells {sub_cells} ≠ line sums {col_sums}")
    if len(sub_tail) != 2:
        raise IWCCAParseError(f"Subtotal row tail unreadable: {sub_tail}")
    sum_spots = sum(ln.total_spots for ln in parsed)
    sum_net = round(sum(ln.net_total for ln in parsed), 2)
    if int(sub_tail[0]) != sum_spots or abs(sub_tail[1] - sum_net) > 0.005:
        raise IWCCAParseError(
            f"Subtotal {int(sub_tail[0])} spots / {sub_tail[1]:.2f} ≠ lines {sum_spots} / {sum_net:.2f}"
        )

    # ── Total Net ──
    tm = re.search(r"Total Net:\s*([\d,]+\.\d{2})", full_text)
    if not tm:
        raise IWCCAParseError("'Total Net:' not found")
    total_net = _num(tm.group(1)) or 0.0
    if abs(total_net - sum_net) > 0.005:
        raise IWCCAParseError(f"Total Net {total_net:.2f} ≠ lines {sum_net:.2f}")

    # ── monthly summary (broadcast months) ──
    monthly: List[Tuple[Tuple[int, int], int, float]] = []
    sm = re.search(
        r"((?:[A-Z]{3} '\d{2}\s+)+)Total\s*\n\s*Spots((?:\s+\d+)+)\s*\n\s*Total Net((?:\s+[\d,]+\.\d{2})+)",
        full_text,
    )
    if not sm:
        raise IWCCAParseError("monthly Spots / Total Net summary not found")
    labels = re.findall(r"([A-Z]{3}) '(\d{2})", sm.group(1))
    spots_vals = [int(x) for x in sm.group(2).split()]
    net_vals = [_num(x) or 0.0 for x in sm.group(3).split()]
    if not (len(labels) + 1 == len(spots_vals) == len(net_vals)):
        raise IWCCAParseError(
            f"monthly summary shape mismatch: {labels} / {spots_vals} / {net_vals}"
        )
    if spots_vals[-1] != sum_spots or abs(net_vals[-1] - sum_net) > 0.005:
        raise IWCCAParseError(
            f"monthly summary total {spots_vals[-1]} / {net_vals[-1]:.2f} ≠ lines {sum_spots} / {sum_net:.2f}"
        )
    by_month: Dict[Tuple[int, int], Tuple[int, float]] = {}
    for k, (_, wstart) in enumerate(weeks):
        key = broadcast_month(wstart)
        sp = sum(ln.weekly_spots[k] for ln in parsed)
        nt = sum(ln.weekly_spots[k] * ln.net_rate for ln in parsed)
        s0, n0 = by_month.get(key, (0, 0.0))
        by_month[key] = (s0 + sp, n0 + nt)
    for (mon, yy), sp, nt in zip(labels, spots_vals, net_vals):
        key = (2000 + int(yy), _MONTHS[mon])
        got = by_month.get(key, (0, 0.0))
        if got[0] != sp or abs(got[1] - nt) > 0.005:
            raise IWCCAParseError(
                f"{mon} '{yy}: IO says {sp} spots / {nt:.2f}, week columns give {got[0]} / {got[1]:.2f}"
            )
        monthly.append((key, sp, nt))

    if not order_language:
        langs = {ln.language for ln in parsed}
        order_language = (
            langs.pop()
            if len(langs) == 1
            else ("Chinese" if langs <= {"Mandarin", "Cantonese"} else "")
        )

    return IWCCAOrder(
        station=_norm(station),
        flight_start=_mmddyyyy(flight_start),
        flight_end=_mmddyyyy(flight_end),
        weeks_stated=int(weeks_stated),
        order_no=order_no,
        io_date=io_date,
        campaign=campaign,
        description_io=description_io,
        language=order_language,
        market_hint=market_hint,
        week_start_dates=[_mmddyyyy(d) for _, d in weeks],
        lines=parsed,
        total_net=total_net,
        monthly=monthly,
        source_path=path,
    )


if __name__ == "__main__":  # pragma: no cover
    import sys

    for p in sys.argv[1:]:
        o = parse_iwcca(p)
        print(
            f"{Path(p).name}: OrderNo {o.order_no}  {o.flight_start}–{o.flight_end}  {o.language}  market {o.market_hint or '?'}"
        )
        print(f"  Campaign: {o.campaign}\n  Description: {o.description_io}")
        print(f"  weeks: {o.week_start_dates}")
        for ln in o.lines:
            print(
                f"  {ln.description:<34} {ln.weekly_spots} = {ln.total_spots} @ {ln.net_rate:.2f} net = {ln.net_total:,.2f}"
            )
        print(f"  total {o.total_spots} spots  ${o.total_net:,.2f} net  monthly {o.monthly}")
