"""
Deterministic positional reader for the Admerasia broadcast-order GRID.

The day-shift bug lived entirely in pdfplumber's `extract_tables`, which rebuilds
cells from text and collapses merged calendar columns. Individual word positions
(`extract_words`) are exact, so we read the grid from them: the day-number header
row defines the column x-centers, and every printed spot digit is bucketed into the
column whose center it sits under. Validated exact against all five entered
July-2026 contracts (including the dense 12-row Chinese order).

This reads ONLY the calendar grid — one daily-spot row per program row, top to
bottom. The left-hand metadata columns (program name, daypart, rate) are NOT read
here: in these PDFs they're rendered in a character-spaced Type3 font that
`extract_words` garbles ("M cV a lu e 2 .0 ..."). Those facts come from vision,
which reads the rendered text fine. The two are zipped by row order downstream.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

import pdfplumber

# Word-grouping y-tolerance for the grid read — see read_grid_cells().
SPOT_Y_TOL = 0.5

# The 9/2026 McDonald's IOs append a SPONSORSHIP section under the TVC program rows:
# a '15s Spots' row whose calendar band holds free text ('8 EVM AM 8 EVM ROD') and an
# 'OBB & CBB' package row. Those band digits would otherwise read as a phantom program
# row (row-count guard fired on the first such IO). Everything from the first
# sponsorship label down is not TVC grid.
_SPONSORSHIP_LABELS = ("15s spots", "obb & cbb")


def sponsorship_top(words) -> float | None:
    """Top y of the sponsorship section's first label line, or None when absent."""
    lines: dict[int, list] = defaultdict(list)
    for w in words:
        lines[round(w["top"])].append(w)
    tops = []
    for ws in lines.values():
        txt = " ".join(x["text"] for x in sorted(ws, key=lambda x: x["x0"])).lower()
        if any(lbl in txt for lbl in _SPONSORSHIP_LABELS):
            tops.append(min(x["top"] for x in ws))
    return min(tops) if tops else None


@dataclass
class PositionalGrid:
    calendar_days: list[int]
    rows: list[list[int]] = field(default_factory=list)  # one daily_spots list per program row, top→bottom


@dataclass
class GridCell:
    row: int      # program-row index, top→bottom (0-based)
    col: int      # index into calendar_days
    count: int    # spots printed in this cell
    x0: float
    x1: float
    top: float
    bottom: float


@dataclass
class GridGeometry:
    calendar_days: list[int]
    col_x: list[float]        # x-center of each calendar column
    cells: list[GridCell]     # every printed spot-cell (has a digit)


def read_grid_cells(path: str) -> GridGeometry:
    """Single source of grid geometry: locate the calendar columns + program rows and
    return every printed spot-cell with its row/col index, count, and bbox. Both the
    count reader (`read_grid`) and the traffic colour reader build on this, so the two
    can never drift."""
    with pdfplumber.open(path) as pdf:
        # y_tolerance must stay BELOW the ~1.1pt offset between a grid row's spot
        # digits and the program-name text on the same visual line. The program-name
        # column physically overlaps the first calendar columns in these PDFs, so at
        # pdfplumber's default y_tolerance=3 a spot digit sitting over the title gets
        # GLUED to it into one word ("Life" + "1" -> "Life1"), which then fails
        # .isdigit() and the spot silently disappears. Intra-word baseline jitter is
        # zero (a word is one text-show op), so a tight tolerance is safe.
        words = pdf.pages[0].extract_words(y_tolerance=SPOT_Y_TOL)

    digits = [w for w in words if w["text"].isdigit()]
    by_y: dict[int, list] = defaultdict(list)
    for w in digits:
        by_y[round(w["top"])].append(w)
    if not by_y:
        raise ValueError("No digits found — not an Admerasia grid?")

    # Day-number header row = the y-cluster richest in day-of-month values.
    dn_y = max(by_y, key=lambda y: sum(1 for w in by_y[y] if 1 <= int(w["text"]) <= 31))
    day_cols = sorted(
        ((w["x0"] + w["x1"]) / 2, int(w["text"]))
        for w in by_y[dn_y] if 1 <= int(w["text"]) <= 31
    )
    calendar_days = [d for _, d in day_cols]
    col_x = [x for x, _ in day_cols]
    if len(col_x) < 2:
        raise ValueError("Could not read the calendar day-number row")
    colw = (col_x[-1] - col_x[0]) / (len(col_x) - 1)
    tol = 0.45 * colw

    def to_col(w) -> int | None:
        cx = (w["x0"] + w["x1"]) / 2
        best = min(range(len(col_x)), key=lambda i: abs(cx - col_x[i]))
        return best if abs(cx - col_x[best]) < tol else None

    # Program rows = grid-bearing digit words below the day-number header, clustered
    # into rows by their RAW `top`. Bucketing by round(top) first (as the header
    # detection above does) can split ONE program row across two integer buckets when
    # its baseline straddles a .5 boundary — e.g. one cell at top=299.48 → 299 and the
    # rest at 299.81 → 300 — inventing a phantom row and breaking the row-count guard.
    # Clustering the raw tops avoids that: intra-row baseline jitter is sub-pixel
    # (<0.5pt) while adjacent program rows sit ~5-7pt apart, so a small tolerance
    # separates real rows without splitting a jittered one.
    ROW_TOL = 2.0        # between the <0.5pt intra-row jitter and the ~5pt row pitch
    FOOTER_GAP = 45      # a large vertical jump = end of grid (totals / notes block)

    spons_top = sponsorship_top(words)
    grid_words = sorted(
        (
            w
            for w in digits
            if w["top"] > dn_y + 0.5
            and (spons_top is None or w["top"] < spons_top - 0.5)
            and to_col(w) is not None
        ),
        key=lambda w: w["top"],
    )
    cells: list[GridCell] = []
    row_idx = -1
    row_top = prev_top = None
    for w in grid_words:
        top = w["top"]
        if prev_top is not None and top - prev_top > FOOTER_GAP:
            break                       # footer gap — end of grid
        if row_top is None or top - row_top > ROW_TOL:
            row_idx += 1                # start a new program row
            row_top = top
        cells.append(GridCell(row=row_idx, col=to_col(w), count=int(w["text"]),
                              x0=w["x0"], x1=w["x1"], top=w["top"], bottom=w["bottom"]))
        prev_top = top
    return GridGeometry(calendar_days=calendar_days, col_x=col_x, cells=cells)


def read_grid(path: str) -> PositionalGrid:
    """Per-program-row daily spot counts (top→bottom). Rows summing to 0 are dropped,
    preserving the original contract-entry behaviour."""
    g = read_grid_cells(path)
    n = 1 + max((c.row for c in g.cells), default=-1)
    rows = [[0] * len(g.calendar_days) for _ in range(n)]
    for c in g.cells:
        rows[c.row][c.col] += c.count
    rows = [r for r in rows if sum(r) > 0]
    return PositionalGrid(calendar_days=g.calendar_days, rows=rows)
