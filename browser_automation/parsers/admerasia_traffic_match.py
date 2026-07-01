"""
Match Admerasia scheduled spots to their creative (ISCI) by grid-cell colour.

Pure logic — no PDF/DB/vision I/O — so it is fully unit-testable. Callers supply:
  • color_grid   : ColorGrid from admerasia_traffic_color.read_color_grid
  • row_meta     : {grid_row_index: (duration_sec, win_start_frames, win_end_frames)}
                   (grid-row duration + daypart window, from the entry vision read)
  • cluster_isci : {palette_cluster_index: isci_code}  (from the vision legend match)
  • flight_start : date of calendar-grid column 0
  • spots        : [{"id", "date": date, "ora": frames, "duration": frames}]  (TPALINSE)
  • filmati_by_isci : {isci: {"filmati_id": int, "duration": frames}}

A grid cell (row, day) is one colour = one creative; every spot on that day in that
row's (duration, daypart) group takes it. Returns per-spot assignments + warnings;
guardrail failures are reported, never silently assigned.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

_FPS = 29.97
_DUR_TOL = 5           # frames; creative length must match ordered length within ±5


@dataclass
class SpotAssignment:
    tp_id: int
    filmati_id: int | None
    isci: str | None
    duration_ok: bool
    ok: bool
    reason: str = ""


@dataclass
class MatchResult:
    assignments: list[SpotAssignment] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def writable(self):
        return [a for a in self.assignments if a.ok]


def match_creatives(color_grid, row_meta, cluster_isci, flight_start, spots,
                    filmati_by_isci) -> MatchResult:
    res = MatchResult()

    # index cells by (row, column)
    cell_by_rc = {(c.row, c.col): c for c in color_grid.cells}
    col_of_day = {d: i for i, d in enumerate(color_grid.calendar_days)}

    # Guardrail: each grid row must be single-duration (a :15 row can't hold :30 colour)
    row_iscis = defaultdict(set)
    for c in color_grid.cells:
        isci = cluster_isci.get(c.cluster)
        if isci:
            row_iscis[c.row].add(isci)
    for r, iscis in row_iscis.items():
        durs = {filmati_by_isci.get(i, {}).get("duration") for i in iscis}
        durs.discard(None)
        secs = {round(d / _FPS) for d in durs}
        if len(secs) > 1:
            res.warnings.append(f"grid row {r} mixes creative durations {sorted(secs)}s — check colour legend")

    matched_counts = defaultdict(int)   # (row,col) -> spots matched, for reconciliation

    for s in spots:
        dur_sec = round(s["duration"] / _FPS)
        col = (s["date"] - flight_start).days
        # find the grid row whose duration + daypart window contains this spot
        cand = [r for r, (d, ws, we) in row_meta.items()
                if d == dur_sec and ws <= s["ora"] < we]
        if len(cand) != 1:
            res.assignments.append(SpotAssignment(s["id"], None, None, False, False,
                f"spot dur={dur_sec}s @{s['ora']}fr matched {len(cand)} grid rows"))
            continue
        cell = cell_by_rc.get((cand[0], col))
        if cell is None:
            res.assignments.append(SpotAssignment(s["id"], None, None, False, False,
                f"no grid cell for row {cand[0]} day {color_grid.calendar_days[col] if 0<=col<len(color_grid.calendar_days) else '?'}"))
            continue
        isci = cluster_isci.get(cell.cluster)
        fil = filmati_by_isci.get(isci) if isci else None
        if not fil:
            res.assignments.append(SpotAssignment(s["id"], None, isci, False, False,
                f"no FILMATI for ISCI {isci}"))
            continue
        # USER GUARDRAIL: assigned creative length must equal ordered spot length
        dur_ok = abs(fil["duration"] - s["duration"]) <= _DUR_TOL
        res.assignments.append(SpotAssignment(
            s["id"], fil["filmati_id"], isci, dur_ok, dur_ok,
            "" if dur_ok else f"length mismatch: creative {round(fil['duration']/_FPS)}s vs ordered {dur_sec}s"))
        matched_counts[(cand[0], col)] += 1

    # Reconciliation: each grid cell's printed count must equal spots matched to it
    for (r, col), cell in cell_by_rc.items():
        got = matched_counts.get((r, col), 0)
        if got != cell.count:
            res.warnings.append(
                f"row {r} day {color_grid.calendar_days[col]}: grid shows {cell.count} spot(s) "
                f"but {got} scheduled spot(s) matched")

    bad = [a for a in res.assignments if not a.ok]
    if bad:
        res.warnings.append(f"{len(bad)} spot(s) could not be assigned/validated (see per-spot reasons)")
    return res
