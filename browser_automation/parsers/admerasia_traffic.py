"""
Admerasia traffic auto-assigner — orchestration.

Drop-first: given an Admerasia IO PDF (and a live DB connection), find the entered
contract by the IO order number, read each grid cell's colour, read the ISCI legend
(vision), and produce per-spot creative assignments for review.

Vision is used ONLY for the colour->creative legend. Everything else is
deterministic: cell colour (pixel), grid row -> contract line-group (per-date spot
count vector), and spot matching (duration + daypart window + date).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

try:
    from .admerasia_traffic_color import read_color_grid
    from .admerasia_traffic_match import match_creatives
    from .admerasia_vision import extract_isci_legend, extract_grid
    from . import admerasia_parser as _ap
except ImportError:
    from admerasia_traffic_color import read_color_grid
    from admerasia_traffic_match import match_creatives
    from admerasia_vision import extract_isci_legend, extract_grid
    import admerasia_parser as _ap

_FPS = 29.97


def _daypart_to_frames(daypart: str):
    """'6:00a-7:00a' / '9:00p-10:00p' / '10:30p-12:00a' / '11:30-12:00p' → (start, end)
    frames, end exclusive. 12:00p = noon; a 12:00a END = end-of-broadcast-day (24:00)."""
    if not daypart:
        return None
    parts = daypart.replace(" ", "").lower().split("-")
    if len(parts) != 2:
        return None

    def tok(t):
        m = re.match(r"(\d{1,2})(?::(\d{2}))?([ap])?", t)
        return (int(m.group(1)), int(m.group(2) or 0), m.group(3)) if m else None

    a, b = tok(parts[0]), tok(parts[1])
    if not a or not b:
        return None

    def mins(h, mn, suf, is_end=False):
        if suf == "a":
            return (1440 if (h == 12 and is_end) else (0 if h == 12 else h * 60)) + mn
        if suf == "p":
            return (12 * 60 if h == 12 else (h + 12) * 60) + mn
        return None  # unknown period — resolved by caller

    end_suf = b[2] or a[2] or "p"
    end = mins(b[0], b[1], end_suf, is_end=True)
    if a[2]:
        start = mins(a[0], a[1], a[2])
    else:
        # try the end's period; if that runs backwards (e.g. 11:30-12:00p), flip it
        s_same = mins(a[0], a[1], end_suf)
        start = s_same if (s_same is not None and s_same < end) else \
            mins(a[0], a[1], "a" if end_suf == "p" else "p")
    if start is None or end is None or end <= start:
        return None
    if end - start > 240:      # >4h daypart = vision typo (e.g. '10:300p'); don't trust it
        return None
    return (round(start * 60 * _FPS), round(end * 60 * _FPS))


@dataclass
class TrafficResult:
    order_number: str
    contract_id: int | None
    contract_code: str | None
    assignments: list = field(default_factory=list)   # SpotAssignment
    warnings: list = field(default_factory=list)
    legend: list = field(default_factory=list)         # [(isci, duration_sec, rgb, color_name)]
    ok: bool = False


def _dist(a, b):
    return sum((x - y) ** 2 for x, y in zip(a, b)) ** 0.5


def _io_header(pdf_path: str):
    """(order_number, flight_start) from the IO text. flight_start is the campaign
    start = the date of calendar-grid column 0 (what the entry parser aligns to) —
    NOT the earliest contract line, which may begin a day or two into the grid."""
    import pdfplumber
    with pdfplumber.open(pdf_path) as pdf:
        text = pdf.pages[0].extract_text() or ""
    order_no = _ap._extract_order_number(text)
    weeks = _ap._calculate_week_starts(_ap._extract_campaign_period(text))
    return order_no, (weeks[0] if weeks else None)


def find_contract(cur, order_number: str):
    """Primary: exact CUSTOMERREF match (the IO order number stored verbatim at entry).
    Returns dict {id, code} or None (caller handles fallbacks / disambiguation)."""
    cur.execute(
        "SELECT ID_CONTRATTITESTATA AS id, COD_CONTRATTO AS code"
        " FROM CONTRATTITESTATA WHERE CUSTOMERREF = %s",
        (order_number,),
    )
    rows = cur.fetchall()
    return rows[0] if len(rows) == 1 else (rows if rows else None)


def _assign_clusters(palette, legend_rows):
    """One-to-one map palette cluster index -> ISCI, minimising colour distance to the
    vision legend RGBs. One-to-one self-corrects moderate legend colour error."""
    import itertools
    leg = [(r.isci_code, tuple(r.color_rgb)) for r in legend_rows]
    K = len(palette)
    if K > len(leg):
        return None  # more grid colours than legend creatives → caller warns
    best = None
    for perm in itertools.permutations(range(len(leg)), K):
        cost = sum(_dist(palette[i], leg[perm[i]][1]) for i in range(K))
        if best is None or cost < best[0]:
            best = (cost, perm)
    return {i: leg[best[1][i]][0] for i in range(K)}


def _filmati_by_isci(cur, iscis):
    if not iscis:
        return {}
    ph = ",".join(["%s"] * len(iscis))
    cur.execute(
        f"SELECT ID_FILMATI AS fid, COD_PROGRA AS cod, DURATA AS dur FROM FILMATI WHERE COD_PROGRA IN ({ph})",
        tuple(iscis),
    )
    return {r["cod"]: {"filmati_id": r["fid"], "duration": r["dur"]} for r in cur.fetchall()}


def _contract_spots(cur, contract_id):
    """Scheduled spots (id/date/ora/duration) for the contract."""
    cur.execute(
        "SELECT t.ID_TPALINSE AS id, CONVERT(VARCHAR(10),t.DATA,120) AS dt, t.ORA AS ora, t.DURATION AS dur"
        " FROM TPALINSE t JOIN trafficPalinse tp ON tp.id_tpalinse = t.ID_TPALINSE"
        " JOIN CONTRATTIRIGHE cr ON cr.ID_CONTRATTIRIGHE = tp.ID_ContrattiRighe"
        " WHERE cr.ID_CONTRATTITESTATA = %d AND t.LIVELLO = 0",
        (contract_id,),
    )
    spots = [{"id": r["id"], "date": date.fromisoformat(r["dt"]), "ora": r["ora"], "duration": r["dur"]}
             for r in cur.fetchall()]
    return spots


def resolve_traffic(pdf_path: str, cur, legend=None) -> TrafficResult:
    """Full pipeline. `cur` is a live as_dict DB cursor. `legend` (list of LegendRow)
    may be injected for testing; otherwise read via vision."""
    order_no, flight_start = _io_header(pdf_path)
    res = TrafficResult(order_number=order_no, contract_id=None, contract_code=None)
    if flight_start is None:
        res.warnings.append("Could not determine campaign start (grid column 0 date) from the IO")
        return res

    found = find_contract(cur, order_no)
    if not found:
        res.warnings.append(f"No contract found with CUSTOMERREF = {order_no!r}")
        return res
    if isinstance(found, list):
        res.warnings.append(f"{len(found)} contracts share CUSTOMERREF {order_no!r} — disambiguation needed")
        return res
    res.contract_id, res.contract_code = found["id"], found["code"]

    color_grid = read_color_grid(pdf_path)
    if legend is None:
        legend = extract_isci_legend(pdf_path).rows
    res.legend = [(r.isci_code, r.duration_sec, tuple(r.color_rgb), r.color_name) for r in legend]

    cluster_isci = _assign_clusters(color_grid.palette, legend)
    if cluster_isci is None:
        res.warnings.append(f"grid has {len(color_grid.palette)} colours but legend has {len(legend)} creatives")
        return res

    filmati = _filmati_by_isci(cur, [r.isci_code for r in legend])
    missing = [r.isci_code for r in legend if r.isci_code not in filmati]
    if missing:
        res.warnings.append(f"ISCIs not found in FILMATI: {missing}")

    spots = _contract_spots(cur, res.contract_id)
    if not spots:
        res.warnings.append("Contract has no scheduled spots to assign")
        return res

    # per-row duration from the row's own cells' ISCI (single-duration by coherence)
    row_dur: dict = {}
    for c in color_grid.cells:
        isci = cluster_isci.get(c.cluster)
        d = filmati.get(isci, {}).get("duration") if isci else None
        if d:
            row_dur.setdefault(c.row, round(d / _FPS))

    # per-row daypart window from the entry vision read — only to break ties when two
    # same-duration programmes air the same day.
    row_window: dict = {}
    try:
        vis = extract_grid(pdf_path)
        for i, ln in enumerate(vis.lines):
            w = _daypart_to_frames(ln.daypart)
            if w:
                row_window[i] = w
    except Exception as exc:  # noqa: BLE001
        res.warnings.append(f"daypart windows unavailable ({exc}); same-day same-duration ties may fail")

    m = match_creatives(color_grid, row_dur, row_window, cluster_isci, flight_start, spots, filmati)
    res.assignments = m.assignments
    res.warnings.extend(m.warnings)
    res.ok = bool(m.writable) and not any(not a.ok for a in m.assignments)
    return res
