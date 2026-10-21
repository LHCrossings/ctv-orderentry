"""
Viewpoint Ads proposal parser (political, Charmaine's house one-page option sheet).

Viewpoint Ads (ANAGRAF 486, 15% commission, Broadcast) places political buys off
Crossings' own one-page proposal. Lee created the client ahead of the first
order as ANAGRAF 487 "Mayor Lurie Proposition" (linked to agency 486) — the
sheet itself prints "Advertiser TBD ( Mayor Lurie Proposition)" because the
committee's finished name was not known when the options went out.

Shape of the document (Option 1, SF, prepared 10/20/2026):

    OPTION 1
    AGENCY Viewpoint Ads
    Advertiser TBD ( Mayor Lurie Proposition)
    Contact: Maggie Hartzell
    Email: maggie@viewpointads.com
    Phone number: 262-806-2919
    PREPARED BY: CHARMAINE LANE
    Flight Date Estimate: 10/23 through 11/3 ends at 7am      ← no year anywhere
    Billing: Broadcast month                                   ← absent on Option 2
    TOTAL GROSS BUDGET: $30,340.00
    TOTAL NET BUDGET $25,789.00
    *Note: No separation requirement
    Gross Rate per
    Media /MARKET DAYS Time Programming :30s Unit GROSS NET COST
    M-F 6a-7a Mandarin News $120 24 $2,880.00 $2,448.00
    ...
    CROSSINGS TV                                   ← the merged market cell prints
    M-F 9p-10p Mandarin Drama $212 22 ...            as all-caps fragments between
    SAN FRANCISCO Mandarin                           (and glued onto) the rows; a
    XFINITY TV 3131 AND M-F 10p-11:30p Entertainment $212 21 ...   wrapped program
    KQTA 15.3 M-F 11:30p-12a Cantonese News $212 12 ...            word sits ABOVE
    Sat 6a-7a Mandarin News $90 8 $720.00 $612.00                  its row
    Sat- Sun 8p-9p Mandarin News $140 14 $1,960.00 $1,666.00
    Sat& Sun 9p-12a Mandarin Entertainm $140 12 ...  ← Option 2 truncates the cell
    TOTAL 169 $30,340.00 $25,789.00
    Approved by ____  Client Signature

Money basis: rates are GROSS per :30 unit (the header says so) and the NET column
is gross × (1 − commission). Rates enter verbatim; the ANAGRAF commission on the
agency nets them (never a multiplier in the parser). The sheet's own ratio is
exposed as `commission_pct` so the gather can compare it with ANAGRAF.

No week columns: each row is a TOTAL over the flight, so every line enters as a
Rotation line on the flight window with max-per-day derived from the active days
(the Polaris "Yes on 50" 2025 precedent, contract 2198).

Dates: the sheet carries no year. The year comes from the PDF's creation date
(or today's date when a caller passes none), rolling the flight forward when it
would otherwise sit more than half a year in the past. "ends at 7am" is kept as
`end_cutoff` so a line whose window ends after the cutoff stops the day before
(election-day morning news is the only line that airs on the last day).

Reconciliation is the document's own arithmetic and RAISES on any mismatch:
per row rate × units == GROSS and NET / GROSS matches every other row; Σ units,
Σ GROSS and Σ NET against the TOTAL row and the TOTAL GROSS / NET BUDGET lines.
A row the reader cannot shape refuses the whole sheet — never a skipped line.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import List, Optional

AGENCY_NAME = "Viewpoint Ads"

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

# Programming vocabulary used only to complete a cell the sheet truncated
# ("Entertainm" → "Entertainment"); the row keeps its own wording otherwise.
_PROGRAM_WORDS = (
    "Entertainment",
    "Variety",
    "Drama",
    "News",
    "Talk",
    "Movie",
    "Music",
    "Sports",
    "Lifestyle",
    "Documentary",
    "Magazine",
    "Series",
    "Comedy",
    "Kids",
    "Children",
)

_DAY_TOK = r"(?:Mon|Tues|Tue|Tu|Wed|Thur|Thu|Th|Fri|Sat|Sa|Sun|Su|M|T|W|F)"
_TIME_TOK = r"\d{1,2}(?::\d{2})?\s*[ap]"
_MONEY_TOK = r"\$\s?(\d[\d,]*(?:\.\d{2})?)"
# "<noise> M-F 6a-7a Mandarin News $120 24 $2,880.00 $2,448.00"
_ROW_RE = re.compile(
    rf"^(?P<pre>.*?)\b(?P<days>{_DAY_TOK}(?:\s*[-&]\s*{_DAY_TOK})?)\s+"
    rf"(?P<time>{_TIME_TOK}\s*-\s*{_TIME_TOK})\s+"
    rf"(?P<program>.*?)\s*\$\s?(?P<rate>[\d,]+(?:\.\d{{2}})?)\s+(?P<units>\d+)\s+"
    rf"\$\s?(?P<gross>[\d,]+\.\d{{2}})\s+\$\s?(?P<net>[\d,]+\.\d{{2}})\s*$",
    re.I,
)
_MD_RE = re.compile(r"(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?")
_CUTOFF_RE = re.compile(r"ends?\s+(?:at\s+)?(\d{1,2})(?::(\d{2}))?\s*([ap])\.?m?", re.I)
_LEN_RE = re.compile(r":(\d{1,3})\s*s\b", re.I)  # ":30s"
_WEEKDAY_ORDER = ["M", "Tu", "W", "Th", "F", "Sa", "Su"]
_DAY_CANON = {
    "mon": "M",
    "m": "M",
    "tues": "Tu",
    "tue": "Tu",
    "tu": "Tu",
    "t": "Tu",
    "wed": "W",
    "w": "W",
    "thur": "Th",
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


class ViewpointParseError(ValueError):
    """The document could not be read or does not foot — refuse to enter."""


# ─── Data model ──────────────────────────────────────────────────────────────


@dataclass
class ViewpointLine:
    program: str  # "Mandarin News", "Cantonese Talk", "Mandarin Entertainment"
    days: str  # "M-F", "Sa", "Sa-Su"
    time_text: str  # "6a-7a", "11:30p-12a"
    rate: float  # GROSS per unit — entered verbatim, ANAGRAF nets it
    total_spots: int  # the row's Unit cell — a flight total, no week columns
    gross_total: float
    net_total: float
    length_sec: int
    flight_start: date
    flight_end: date  # cutoff-aware: the last day THIS line may air
    is_bonus: bool = False

    @property
    def language(self) -> str:
        low = self.program.lower()
        if "mandarin" in low and "cantonese" in low:
            return "Chinese"
        for lang in _LANGS:
            if lang in low:
                return "South Asian" if lang == "south asian" else lang.title()
        return ""

    @property
    def program_short(self) -> str:
        """'Mandarin News' → 'News'."""
        p = self.program
        lang = self.language
        if lang:
            p = re.sub(re.escape(lang), "", p, flags=re.I)
        return " ".join(p.split())

    # Aliases for the web preview normalizer (parser_bridge._normalize_line)
    @property
    def description(self) -> str:
        return f"{self.days} {self.time_text} {self.program}".strip()

    @property
    def weekly_spots(self) -> List[int]:
        return []  # no week columns on this sheet; total_spots is the flight total

    @property
    def length(self) -> int:
        return self.length_sec

    @property
    def time(self) -> str:
        return self.time_text

    @property
    def start_date(self) -> str:
        return self.flight_start.strftime("%m/%d/%Y")

    @property
    def end_date(self) -> str:
        return self.flight_end.strftime("%m/%d/%Y")


@dataclass
class ViewpointOrder:
    agency: str
    advertiser_raw: str  # "TBD ( Mayor Lurie Proposition)" as printed
    contact: str
    email: str
    phone: str
    prepared_by: str
    option: str  # "Option 1"
    start: date
    end: date
    end_cutoff: str  # "07:00" when the flight line says "ends at 7am", else ""
    billing: str  # "Broadcast month" as printed, "" when absent
    gross_budget: Optional[float]
    net_budget: Optional[float]
    length_sec: int
    market_label: str
    market_code: str
    lines: List[ViewpointLine] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)  # "*Note: ..." lines as printed
    warnings: List[str] = field(default_factory=list)  # repairs the reader made
    year_source: str = ""  # "PDF created 10/20/2026" / "today's date"
    rates_are_net: bool = False  # GROSS rates; ANAGRAF commission nets them
    source_path: str = ""

    @property
    def advertiser(self) -> str:
        """'TBD ( Mayor Lurie Proposition)' → 'Mayor Lurie Proposition'."""
        raw = " ".join(self.advertiser_raw.split())
        m = re.search(r"\(\s*(.+?)\s*\)", raw)
        if m and (self.advertiser_tbd or not raw[: m.start()].strip()):
            return m.group(1)
        return raw

    @property
    def advertiser_tbd(self) -> bool:
        return bool(re.match(r"(?i)\s*tbd\b", self.advertiser_raw))

    @property
    def client(self) -> str:
        return self.advertiser

    @property
    def paid_lines(self) -> List[ViewpointLine]:
        return [ln for ln in self.lines if not ln.is_bonus]

    @property
    def bonus_lines(self) -> List[ViewpointLine]:
        return [ln for ln in self.lines if ln.is_bonus]

    @property
    def total_spots(self) -> int:
        return sum(ln.total_spots for ln in self.lines)

    @property
    def gross_airtime(self) -> float:
        return round(sum(ln.rate * ln.total_spots for ln in self.paid_lines), 2)

    @property
    def net_airtime(self) -> float:
        return round(sum(ln.net_total for ln in self.paid_lines), 2)

    @property
    def commission_pct(self) -> float:
        """The sheet's own net/gross ratio as a percentage (15.0 for Viewpoint)."""
        g = self.gross_airtime
        return round((1 - self.net_airtime / g) * 100, 2) if g else 0.0

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


def _money(tok: str) -> float:
    return float(tok.replace(",", "").replace(" ", ""))


def _canon_days(tok: str) -> str:
    """'M-F' → 'M-F', 'Sat- Sun' / 'Sat& Sun' → 'Sa-Su', 'Sat' → 'Sa',
    'Tu&Th' → 'Tu,Th' (non-adjacent pair)."""
    parts = [p for p in re.split(r"\s*[-&]\s*", tok.strip()) if p]
    canon = [_DAY_CANON[p.lower()] for p in parts]
    if len(canon) == 1:
        return canon[0]
    a, b = canon[0], canon[1]
    if "-" in tok or _WEEKDAY_ORDER.index(b) == _WEEKDAY_ORDER.index(a) + 1:
        return f"{a}-{b}"
    return f"{a},{b}"


def _market_code(label: str) -> str:
    low = " ".join((label or "").split()).lower()
    for key, code in _MARKET_MAP.items():
        if key in low:
            return code
    return ""


def _is_noise_token(tok: str) -> bool:
    """The merged market cell prints in CAPS and digits ('CROSSINGS', 'TV',
    '3131', 'AND', 'KQTA', '15.3'); programming words are Title case."""
    return not re.search(r"[a-z]", tok)


def _split_noise(text: str) -> tuple[str, str]:
    """→ (market-cell noise, programming words) for a grid fragment."""
    noise, words = [], []
    for tok in text.split():
        (noise if _is_noise_token(tok) else words).append(tok)
    return " ".join(noise), " ".join(words)


def _complete_program(program: str, warnings: List[str]) -> str:
    """Complete a truncated last word ('Mandarin Entertainm') from the
    programming vocabulary; anything else is kept as printed."""
    words = program.split()
    if not words:
        return program
    last = words[-1]
    if any(last.lower() == w.lower() for w in _PROGRAM_WORDS):
        return program
    hits = [w for w in _PROGRAM_WORDS if len(last) >= 4 and w.lower().startswith(last.lower())]
    if len(hits) == 1:
        warnings.append(f"program cell '{program}' read as '{' '.join(words[:-1] + hits)}'")
        words[-1] = hits[0]
    return " ".join(words)


def _resolve_md(month: int, day: int, base: date) -> date:
    """Date a month/day the sheet prints without a year: the base year, rolled
    forward when that lands more than half a year before the base date
    (a December sheet for a January flight)."""
    try:
        d = date(base.year, month, day)
    except ValueError as exc:
        raise ViewpointParseError(f"Viewpoint parser: bad date {month}/{day}: {exc}") from None
    if d < base - timedelta(days=183):
        d = date(base.year + 1, month, day)
    return d


def _hhmm(hour: int, minute: int, ap: str) -> str:
    h = hour % 12 + (12 if ap.lower() == "p" else 0)
    return f"{h:02d}:{minute:02d}"


def _time_end_hhmm(time_text: str) -> str:
    """'11:30p-12a' → '23:59', '6a-7a' → '07:00' (EtereClient's rules, local copy
    so the parser stays import-light)."""
    from browser_automation.etere_client import EtereClient

    return EtereClient.parse_time_range(time_text)[1]


# ─── Reader ──────────────────────────────────────────────────────────────────


def parse_viewpoint_text(
    text: str, source_path: str = "", base_date: Optional[date] = None, year_source: str = ""
) -> ViewpointOrder:
    """Parse the extracted text of a Viewpoint proposal (testable without pdfplumber).

    `base_date` dates the year-less flight (the PDF's creation date, else today).
    """
    lines = [" ".join(txt.split()) for txt in (text or "").splitlines()]
    lines = [txt for txt in lines if txt]
    if not lines:
        raise ViewpointParseError("Viewpoint parser: the page has no text")
    if base_date is None:
        base_date = date.today()
        year_source = year_source or "today's date"

    hdr_idx = next(
        (
            i
            for i, txt in enumerate(lines)
            if re.search(r"\bDAYS\b", txt) and re.search(r"\bGROSS\b", txt) and "NET" in txt
        ),
        None,
    )
    if hdr_idx is None:
        raise ViewpointParseError(
            "Viewpoint parser: no 'DAYS Time Programming ... GROSS NET' header row"
        )

    # ── Header: by line-anchored label, never by position ──
    hdr: dict = {}
    notes: List[str] = []
    for txt in lines[:hdr_idx]:
        low = txt.lower()
        if re.match(r"option\s+\d+", low):
            hdr["option"] = txt.title()
        elif low.startswith("agency"):
            hdr["agency"] = txt[len("agency") :].strip(" :")
        elif low.startswith("advertiser"):
            hdr["advertiser"] = txt[len("advertiser") :].strip(" :")
        elif low.startswith("contact"):
            hdr["contact"] = txt[len("contact") :].strip(" :")
        elif low.startswith("email"):
            hdr["email"] = txt[len("email") :].strip(" :")
        elif low.startswith("phone"):
            hdr["phone"] = re.sub(r"(?i)^phone(\s*number)?\s*:?\s*", "", txt).strip()
        elif low.startswith("prepared by"):
            hdr["prepared_by"] = txt[len("prepared by") :].strip(" :").title()
        elif low.startswith("flight"):
            hdr["flight_line"] = txt
        elif low.startswith("billing"):
            hdr["billing"] = txt[len("billing") :].strip(" :")
        elif low.startswith("total gross budget"):
            m = re.search(_MONEY_TOK, txt)
            hdr["gross_budget"] = _money(m.group(1)) if m else None
        elif low.startswith("total net budget"):
            m = re.search(_MONEY_TOK, txt)
            hdr["net_budget"] = _money(m.group(1)) if m else None
        elif txt.startswith("*"):
            notes.append(txt)
    for key, what in (
        ("agency", "'AGENCY' line"),
        ("advertiser", "'Advertiser' line"),
        ("flight_line", "'Flight Date' line"),
    ):
        if key not in hdr:
            raise ViewpointParseError(f"Viewpoint parser: no {what}")

    # ── Flight: "10/23 through 11/3 ends at 7am" ──
    fl = hdr["flight_line"]
    md = _MD_RE.findall(fl)
    if len(md) < 2:
        raise ViewpointParseError(
            f"Viewpoint parser: flight line names fewer than two dates: {fl!r}"
        )
    (m1, d1, y1), (m2, d2, y2) = md[0], md[1]
    if y1 or y2:
        yy = int(y1 or y2)
        yy = yy + 2000 if yy < 100 else yy
        base = date(yy, 1, 1) + timedelta(days=200)  # any date in that year
        year_source = f"the sheet's year {yy}"
        f_start, f_end = _resolve_md(int(m1), int(d1), base), _resolve_md(int(m2), int(d2), base)
    else:
        f_start = _resolve_md(int(m1), int(d1), base_date)
        f_end = _resolve_md(int(m2), int(d2), f_start)
    if f_end < f_start:
        raise ViewpointParseError(
            f"Viewpoint parser: flight {f_start:%m/%d/%Y}-{f_end:%m/%d/%Y} ends before it starts"
        )
    cm = _CUTOFF_RE.search(fl)
    end_cutoff = _hhmm(int(cm.group(1)), int(cm.group(2) or 0), cm.group(3)) if cm else ""

    # ── Spot length from the header row (":30s") ──
    lm = _LEN_RE.search(lines[hdr_idx]) or _LEN_RE.search(" ".join(lines[: hdr_idx + 1]))
    if not lm:
        raise ViewpointParseError(
            f"Viewpoint parser: header row names no spot length (':30s'): {lines[hdr_idx]!r}"
        )
    length_sec = int(lm.group(1))

    # ── Grid rows → TOTAL ──
    rows: List[dict] = []
    warnings: List[str] = []
    noise_parts: List[str] = []
    pending = ""
    total_row: Optional[tuple] = None
    for txt in lines[hdr_idx + 1 :]:
        low = txt.lower()
        if low.startswith("total"):
            m = re.match(rf"total\s+(\d+)\s+{_MONEY_TOK}\s+{_MONEY_TOK}", txt, re.I)
            if not m:
                raise ViewpointParseError(f"Viewpoint parser: unreadable TOTAL row: {txt!r}")
            total_row = (int(m.group(1)), _money(m.group(2)), _money(m.group(3)))
            break
        if "$" not in txt:
            noise, words = _split_noise(txt)
            if noise:
                noise_parts.append(noise)
            if words:
                pending = (pending + " " + words).strip()  # wrapped program word(s)
            continue
        m = _ROW_RE.match(txt)
        if not m:
            raise ViewpointParseError(
                f"Viewpoint parser: cannot shape row {txt!r} as DAYS Time Programming "
                "$rate units $gross $net — refusing to enter"
            )
        noise, words = _split_noise(m.group("pre"))
        if noise:
            noise_parts.append(noise)
        if words:
            pending = (pending + " " + words).strip()
        program = " ".join((pending + " " + m.group("program")).split())
        pending = ""
        program = _complete_program(program, warnings)
        rows.append(
            {
                "days": m.group("days"),
                "time": re.sub(r"\s+", "", m.group("time")),
                "program": program,
                "rate": _money(m.group("rate")),
                "units": int(m.group("units")),
                "gross": _money(m.group("gross")),
                "net": _money(m.group("net")),
                "text": txt,
            }
        )
    if not rows:
        raise ViewpointParseError("Viewpoint parser: no airtime rows under the header")
    if total_row is None:
        raise ViewpointParseError("Viewpoint parser: no TOTAL row — the grid is cut off")
    if pending:
        raise ViewpointParseError(
            f"Viewpoint parser: programming text {pending!r} belongs to no row"
        )

    # ── Rows → lines, each reconciled against its own arithmetic ──
    parsed: List[ViewpointLine] = []
    ratios: List[float] = []
    for r in rows:
        is_bonus = r["rate"] == 0 or bool(re.match(r"(?i)\s*(bonus|bns)\b", r["program"]))
        if not is_bonus and abs(r["rate"] * r["units"] - r["gross"]) > 0.01:
            raise ViewpointParseError(
                f"Viewpoint parser: row {r['text']!r}: ${r['rate']:.2f} × {r['units']} = "
                f"${r['rate'] * r['units']:,.2f} != GROSS ${r['gross']:,.2f}"
            )
        if r["gross"]:
            ratios.append(r["net"] / r["gross"])
        if not r["program"]:
            raise ViewpointParseError(f"Viewpoint parser: row {r['text']!r} names no programming")
        end_t = _time_end_hhmm(r["time"])
        line_end = f_end
        if end_cutoff and end_t > end_cutoff:
            line_end = f_end - timedelta(days=1)
        if line_end < f_start:
            raise ViewpointParseError(
                f"Viewpoint parser: row {r['text']!r} cannot air inside "
                f"{f_start:%m/%d}-{f_end:%m/%d} ending {end_cutoff}"
            )
        parsed.append(
            ViewpointLine(
                program=r["program"],
                days=_canon_days(r["days"]),
                time_text=r["time"],
                rate=r["rate"],
                total_spots=r["units"],
                gross_total=r["gross"],
                net_total=r["net"],
                length_sec=length_sec,
                flight_start=f_start,
                flight_end=line_end,
                is_bonus=is_bonus,
            )
        )
    if ratios and max(ratios) - min(ratios) > 0.0005:
        raise ViewpointParseError(
            "Viewpoint parser: NET / GROSS differs between rows "
            f"({min(ratios):.4f}..{max(ratios):.4f}) — a net cell was mis-read"
        )

    # ── Totals: the TOTAL row and the budget lines ──
    s_units = sum(ln.total_spots for ln in parsed)
    s_gross = round(sum(ln.gross_total for ln in parsed), 2)
    s_net = round(sum(ln.net_total for ln in parsed), 2)
    t_units, t_gross, t_net = total_row
    if s_units != t_units:
        raise ViewpointParseError(
            f"Viewpoint parser: rows carry {s_units} units but TOTAL says {t_units} — "
            "a row was dropped or mis-read; refusing to enter"
        )
    if abs(s_gross - t_gross) > 0.01:
        raise ViewpointParseError(
            f"Viewpoint parser: Σ GROSS ${s_gross:,.2f} != TOTAL ${t_gross:,.2f}"
        )
    if abs(s_net - t_net) > 0.01:
        raise ViewpointParseError(f"Viewpoint parser: Σ NET ${s_net:,.2f} != TOTAL ${t_net:,.2f}")
    if hdr.get("gross_budget") is not None and abs(s_gross - hdr["gross_budget"]) > 0.01:
        raise ViewpointParseError(
            f"Viewpoint parser: Σ GROSS ${s_gross:,.2f} != TOTAL GROSS BUDGET "
            f"${hdr['gross_budget']:,.2f}"
        )
    if hdr.get("net_budget") is not None and abs(s_net - hdr["net_budget"]) > 0.01:
        raise ViewpointParseError(
            f"Viewpoint parser: Σ NET ${s_net:,.2f} != TOTAL NET BUDGET ${hdr['net_budget']:,.2f}"
        )

    market_label = " ".join(noise_parts)
    return ViewpointOrder(
        agency=hdr["agency"],
        advertiser_raw=hdr["advertiser"],
        contact=hdr.get("contact", ""),
        email=hdr.get("email", ""),
        phone=hdr.get("phone", ""),
        prepared_by=hdr.get("prepared_by", ""),
        option=hdr.get("option", ""),
        start=f_start,
        end=f_end,
        end_cutoff=end_cutoff,
        billing=hdr.get("billing", ""),
        gross_budget=hdr.get("gross_budget"),
        net_budget=hdr.get("net_budget"),
        length_sec=length_sec,
        market_label=market_label,
        market_code=_market_code(market_label),
        lines=parsed,
        notes=notes,
        warnings=warnings,
        year_source=year_source,
        source_path=source_path,
    )


def _pdf_base_date(pdf) -> tuple[Optional[date], str]:
    """The PDF's CreationDate ("D:20261020115234-07'00'") → (date, label)."""
    raw = str((pdf.metadata or {}).get("CreationDate") or "")
    m = re.search(r"(\d{4})(\d{2})(\d{2})", raw)
    if not m:
        return None, ""
    try:
        d = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None, ""
    return d, f"the PDF's creation date {d.month}/{d.day}/{d.year}"


def parse_viewpoint(path: str) -> ViewpointOrder:
    """Parse a Viewpoint proposal PDF (every page's text, concatenated)."""
    import pdfplumber

    pages: List[str] = []
    with pdfplumber.open(path) as pdf:
        base, label = _pdf_base_date(pdf)
        for p in pdf.pages:
            pages.append(p.extract_text() or "")
    return parse_viewpoint_text(
        "\n".join(pages), source_path=path, base_date=base, year_source=label
    )


def is_viewpoint_text(text: str) -> bool:
    """Detector: the agency's name on Charmaine's option sheet (AGENCY line +
    the GROSS/NET grid header)."""
    low = " ".join((text or "").split()).lower()
    return "viewpoint ads" in low and "prepared by" in low and "gross" in low


if __name__ == "__main__":
    import sys

    o = parse_viewpoint(sys.argv[1])
    print(
        f"{o.option} | {o.agency} | {o.advertiser} (TBD={o.advertiser_tbd}) | "
        f"{o.market_code} ({o.market_label}) | {o.label} cutoff {o.end_cutoff or '-'} | "
        f":{o.length_sec} | billing {o.billing or '-'} | year from {o.year_source}"
    )
    for ln in o.lines:
        print(
            f"  {'BNS' if ln.is_bonus else '   '} {ln.days:<5} {ln.time_text:<12} "
            f"{ln.program:<24} ${ln.rate:7.2f} x {ln.total_spots:>3} = ${ln.gross_total:>9,.2f}"
            f"  net ${ln.net_total:>9,.2f}  {ln.flight_start:%m/%d}-{ln.flight_end:%m/%d}"
        )
    print(
        f"  {o.total_spots} spots  gross ${o.gross_airtime:,.2f}  net ${o.net_airtime:,.2f}"
        f"  ({o.commission_pct:.2f}% commission on the sheet)"
    )
    for n in o.notes:
        print("  note:", n)
    for w in o.warnings:
        print("  ⚠", w)
