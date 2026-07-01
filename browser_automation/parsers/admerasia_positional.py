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


@dataclass
class PositionalGrid:
    calendar_days: list[int]
    rows: list[list[int]] = field(default_factory=list)  # one daily_spots list per program row, top→bottom


def read_grid(path: str) -> PositionalGrid:
    with pdfplumber.open(path) as pdf:
        words = pdf.pages[0].extract_words()

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

    grid_words = sorted(
        (w for w in digits if w["top"] > dn_y + 0.5 and to_col(w) is not None),
        key=lambda w: w["top"],
    )
    rows: list[list[int]] = []
    daily: list[int] | None = None
    row_top = prev_top = None
    for w in grid_words:
        top = w["top"]
        if prev_top is not None and top - prev_top > FOOTER_GAP:
            break                       # footer gap — end of grid
        if daily is None or top - row_top > ROW_TOL:
            if daily is not None and sum(daily) > 0:
                rows.append(daily)      # flush the completed program row
            daily = [0] * len(col_x)    # start a new program row
            row_top = top
        daily[to_col(w)] += int(w["text"])
        prev_top = top
    if daily is not None and sum(daily) > 0:
        rows.append(daily)

    return PositionalGrid(calendar_days=calendar_days, rows=rows)
