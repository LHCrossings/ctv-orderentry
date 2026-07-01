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

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date

try:
    from .admerasia_traffic_color import read_color_grid
    from .admerasia_traffic_match import match_creatives
    from .admerasia_vision import extract_isci_legend
    from . import admerasia_parser as _ap
except ImportError:
    from admerasia_traffic_color import read_color_grid
    from admerasia_traffic_match import match_creatives
    from admerasia_vision import extract_isci_legend
    import admerasia_parser as _ap

_FPS = 29.97


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


def _order_number(pdf_path: str) -> str:
    import pdfplumber
    with pdfplumber.open(pdf_path) as pdf:
        text = pdf.pages[0].extract_text() or ""
    return _ap._extract_order_number(text)


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


def _contract_lines_and_spots(cur, contract_id):
    """Return (line-groups, spots, flight_start). A group is a distinct
    (duration, ORA_INIZIO, ORA_FINE); spots carry id/date/ora/duration."""
    cur.execute(
        "SELECT DISTINCT cr.DURATA AS dur, cr.ORA_INIZIO AS oi, cr.ORA_FINE AS ofn,"
        " CONVERT(VARCHAR(10), MIN(cr.DATA_INIZIO) OVER (), 120) AS flight0"
        " FROM CONTRATTIRIGHE cr WHERE cr.ID_CONTRATTITESTATA = %d",
        (contract_id,),
    )
    grp = cur.fetchall()
    groups = [{"dur": g["dur"], "oi": g["oi"], "ofn": g["ofn"]} for g in grp]
    flight0 = date.fromisoformat(grp[0]["flight0"]) if grp else None
    cur.execute(
        "SELECT t.ID_TPALINSE AS id, CONVERT(VARCHAR(10),t.DATA,120) AS dt, t.ORA AS ora, t.DURATION AS dur"
        " FROM TPALINSE t JOIN trafficPalinse tp ON tp.id_tpalinse = t.ID_TPALINSE"
        " JOIN CONTRATTIRIGHE cr ON cr.ID_CONTRATTIRIGHE = tp.ID_ContrattiRighe"
        " WHERE cr.ID_CONTRATTITESTATA = %d AND t.LIVELLO = 0",
        (contract_id,),
    )
    spots = [{"id": r["id"], "date": date.fromisoformat(r["dt"]), "ora": r["ora"], "duration": r["dur"]}
             for r in cur.fetchall()]
    return groups, spots, flight0


def _map_rows_to_groups(color_grid, groups, spots, flight_start, warnings):
    """Map each grid row -> a contract line-group by matching per-date spot-count
    vectors (deterministic; no vision). Returns {row_index: group}."""
    # per-group date->count
    def in_group(s, g):
        return s["duration"] == g["dur"] and g["oi"] <= s["ora"] < g["ofn"]
    group_counts = []
    for g in groups:
        dc = defaultdict(int)
        for s in spots:
            if in_group(s, g):
                dc[s["date"]] += 1
        group_counts.append(dict(dc))
    # per-row date->count from the grid
    row_counts = defaultdict(lambda: defaultdict(int))
    for c in color_grid.cells:
        d = flight_start.fromordinal(flight_start.toordinal() + c.col)
        row_counts[c.row][d] += c.count
    mapping = {}
    used = set()
    for r in sorted(row_counts):
        rc = dict(row_counts[r])
        # exact match first, else best overlap
        exact = [gi for gi, gc in enumerate(group_counts) if gc == rc and gi not in used]
        if exact:
            mapping[r] = groups[exact[0]]; used.add(exact[0]); continue
        scored = sorted(
            ((sum(min(rc.get(d, 0), gc.get(d, 0)) for d in set(rc) | set(gc)), gi)
             for gi, gc in enumerate(group_counts) if gi not in used),
            reverse=True,
        )
        if scored and scored[0][0] > 0:
            gi = scored[0][1]; mapping[r] = groups[gi]; used.add(gi)
            warnings.append(f"grid row {r} matched line-group by best-overlap (counts differ slightly) — verify")
        else:
            warnings.append(f"grid row {r} could not be matched to any contract line-group")
    return mapping


def resolve_traffic(pdf_path: str, cur, legend=None) -> TrafficResult:
    """Full pipeline. `cur` is a live as_dict DB cursor. `legend` (list of LegendRow)
    may be injected for testing; otherwise read via vision."""
    order_no = _order_number(pdf_path)
    res = TrafficResult(order_number=order_no, contract_id=None, contract_code=None)

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

    groups, spots, flight_start = _contract_lines_and_spots(cur, res.contract_id)
    if not spots:
        res.warnings.append("Contract has no scheduled spots to assign")
        return res

    row_group = _map_rows_to_groups(color_grid, groups, spots, flight_start, res.warnings)
    row_meta = {r: (round(g["dur"] / _FPS), g["oi"], g["ofn"]) for r, g in row_group.items()}

    m = match_creatives(color_grid, row_meta, cluster_isci, flight_start, spots, filmati)
    res.assignments = m.assignments
    res.warnings.extend(m.warnings)
    res.ok = bool(m.writable) and not any(not a.ok for a in m.assignments)
    return res
