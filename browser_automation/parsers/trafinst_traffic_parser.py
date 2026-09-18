"""
Parse the "Traffic Instructions" agency sheet (first seen from Mynt Agency for the
WorldLink client Pacagen, 2026-09-18; system export named ``TrafInstNoComm__*.PDF``).

Layout (one page per sheet, more when the rotation is long)::

    Traffic Instructions                                   Page 1 of 1
    To: Traffic Department              From: Mynt Agency
    Client:PCGN Pacagen                 Date:09/10/26
    Product:PCGN Pacagen
    :Q326 July-Sept 2026                          <- estimate (label cell blank)
    Access:030 :30's
    Market: National Cable CROSS - CROSSINGS
        Run ISCI/Ad-ID          Run Dates
    Code/Name  With 800#  Length  % to Run  Start  End  Spot Sent
    ISCI: INVISIBLE26H (778) 000-0000 030 25.0 09/07/26 09/27/26 / /
    Pacagen Invisible 2026                        <- creative title, next line

The FORMAT is the layout, not the agency: the ``From:`` line names whoever sent it and
is carried as ``agency`` so another WorldLink client's agency using the same template
parses too. Each row carries its own flight dates, so — like Direct Donor — dates live
on the SPOT and the sheet is exposed as ``periods`` grouped by (start, end); the
instruction-level range is the union, for contract search only.

Reconciliation (the sheet is its own oracle): within a period the ``% to Run`` values
must sum to 100, and an (ISCI, start, end) may appear only once. Either failure raises
rather than assigning a wrong rotation.
"""

import io
import re
from dataclasses import dataclass, field
from typing import List, Optional

FORMAT_KEY = "trafinst"

# Layout markers — all three appear in the column header of every sheet of this
# template and in none of the other traffic formats we read.
_MARKERS = ("TRAFFIC INSTRUCTIONS", "WITH 800#", "% TO RUN")


def is_trafinst_text(text: str) -> bool:
    upper = re.sub(r"\s+", " ", text or "").upper()
    return all(m in upper for m in _MARKERS)


@dataclass
class TrafInstSpot:
    isci: str
    title: str
    duration_sec: int
    rotation_pct: float
    date_from_sql: Optional[str] = None
    date_to_sql: Optional[str] = None
    date_from_display: str = ""
    date_to_display: str = ""


@dataclass
class TrafInstPeriod:
    """One flight window: every row that carries the same Start/End."""

    date_from_sql: Optional[str]
    date_to_sql: Optional[str]
    date_from_display: str
    date_to_display: str
    spots: List[TrafInstSpot] = field(default_factory=list)


@dataclass
class TrafInstTrafficInstruction:
    agency: str  # "Mynt Agency" (From:)
    advertiser: str  # "Pacagen" (Client: name without its code)
    client_code: str  # "PCGN"
    product: str  # "Pacagen"
    estimate: str  # "Q326 July-Sept 2026"
    market: str  # "National Cable CROSS - CROSSINGS"
    search_suggestion: str
    date_from_sql: Optional[str]
    date_to_sql: Optional[str]
    date_from_display: str
    date_to_display: str
    spots: List[TrafInstSpot] = field(default_factory=list)
    periods: List[TrafInstPeriod] = field(default_factory=list)


class TrafInstParseError(ValueError):
    pass


_ROW_RE = re.compile(
    r"^ISCI:\s*(?P<isci>[A-Z0-9][A-Z0-9-]{3,})\s+"
    r"\(\d{3}\)\s*\d{3}-\d{4}\s+"  # 800# column — discard
    r"(?P<len>\d{2,3})\s+"  # Length "030"
    r"(?P<pct>\d+(?:\.\d+)?)\s+"  # % to Run
    r"(?P<start>\d{1,2}/\d{1,2}/\d{2,4})\s+"
    r"(?P<end>\d{1,2}/\d{1,2}/\d{2,4})",
    re.MULTILINE,
)
_PCT_TOL = 0.5


def _sql_date(s: str) -> str:
    m, d, y = (int(p) for p in s.split("/"))
    if y < 100:
        y += 2000
    return f"{y:04d}-{m:02d}-{d:02d}"


def _display(sql: str) -> str:
    y, m, d = sql.split("-")
    return f"{int(m)}/{int(d)}"


def _header(text: str, label: str) -> str:
    """Value of a ``Label:`` field. The sheet prints two columns per line
    (``Client:PCGN Pacagen   Time: 14:18:25``, ``To: Traffic Department From: Mynt
    Agency``), so the label may sit mid-line and the value ends at the next
    ``Word:`` label or end of line."""
    m = re.search(
        rf"(?:^|\s){label}\s*:\s*(.+?)(?=\s+[A-Za-z-]+:\s|\s*$)",
        text,
        re.MULTILINE | re.IGNORECASE,
    )
    return m.group(1).strip() if m else ""


def _code_and_name(value: str) -> tuple:
    """'PCGN Pacagen' → ('PCGN', 'Pacagen'); a value with no code → ('', value)."""
    m = re.match(r"^([A-Z0-9]{2,6})\s+(.+)$", value)
    return (m.group(1), m.group(2).strip()) if m else ("", value)


def parse_trafinst_traffic_text(text: str) -> TrafInstTrafficInstruction:
    if not is_trafinst_text(text):
        raise TrafInstParseError("not a Traffic Instructions agency sheet")
    lines = [ln.rstrip() for ln in text.splitlines()]

    # "To: Traffic Department From: Mynt Agency" — From: shares its line with To:
    agency = _header(text, "From")
    client_code, advertiser = _code_and_name(_header(text, "Client"))
    _, product = _code_and_name(_header(text, "Product"))
    market = _header(text, "Market")
    # The estimate's label cell prints blank, leaving a line that starts with ':'.
    estimate = ""
    for ln in lines:
        if re.match(r"^\s*:\s*\S", ln) and not ln.lstrip().startswith(":30"):
            estimate = ln.split(":", 1)[1].strip()
            break

    spots: List[TrafInstSpot] = []
    seen = set()
    for i, ln in enumerate(lines):
        m = _ROW_RE.match(ln.strip())
        if not m:
            continue
        # Title = the next non-blank line that is not itself a row or the page footer.
        title = ""
        for nxt in lines[i + 1 : i + 3]:
            nxt = nxt.strip()
            if nxt and not nxt.startswith("ISCI:") and not nxt.lower().startswith("page "):
                title = nxt
                break
        start, end = _sql_date(m.group("start")), _sql_date(m.group("end"))
        key = (m.group("isci"), start, end)
        if key in seen:
            raise TrafInstParseError(f"ISCI {key[0]} listed twice for {start}–{end}")
        seen.add(key)
        spots.append(
            TrafInstSpot(
                isci=m.group("isci"),
                title=title,
                duration_sec=int(m.group("len")),
                rotation_pct=float(m.group("pct")),
                date_from_sql=start,
                date_to_sql=end,
                date_from_display=_display(start),
                date_to_display=_display(end),
            )
        )
    if not spots:
        raise TrafInstParseError("no ISCI rows found")

    periods: List[TrafInstPeriod] = []
    by_key: dict = {}
    for sp in spots:
        k = (sp.date_from_sql, sp.date_to_sql)
        per = by_key.get(k)
        if per is None:
            per = TrafInstPeriod(
                sp.date_from_sql, sp.date_to_sql, sp.date_from_display, sp.date_to_display
            )
            by_key[k] = per
            periods.append(per)
        per.spots.append(sp)
    for per in periods:
        # Percentages are per (period, length): a :15 rotation and a :30 rotation can
        # share a flight and each sums to 100 on its own.
        by_len: dict = {}
        for sp in per.spots:
            by_len[sp.duration_sec] = by_len.get(sp.duration_sec, 0.0) + sp.rotation_pct
        for dur, total in by_len.items():
            if abs(total - 100.0) > _PCT_TOL:
                raise TrafInstParseError(
                    f"% to Run for :{dur} in {per.date_from_display}–{per.date_to_display} "
                    f"sums to {total:g}, not 100"
                )

    date_from = min(sp.date_from_sql for sp in spots)
    date_to = max(sp.date_to_sql for sp in spots)
    return TrafInstTrafficInstruction(
        agency=agency,
        advertiser=advertiser,
        client_code=client_code,
        product=product,
        estimate=estimate,
        market=market,
        search_suggestion=advertiser.split()[0] if advertiser else client_code,
        date_from_sql=date_from,
        date_to_sql=date_to,
        date_from_display=_display(date_from),
        date_to_display=_display(date_to),
        spots=spots,
        periods=periods,
    )


def parse_trafinst_traffic_pdf(pdf_bytes: bytes) -> TrafInstTrafficInstruction:
    """Every page — a long rotation continues on page 2 with the header repeated."""
    import pdfplumber

    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        text = "\n".join(p.extract_text() or "" for p in pdf.pages)
    return parse_trafinst_traffic_text(text)
