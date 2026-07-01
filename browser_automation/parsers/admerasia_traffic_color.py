"""
Deterministic per-cell fill-colour reader for the Admerasia broadcast grid.

WHY: each spot cell in an Admerasia IO is shaded with the colour of the creative
that airs in it. OCR/text can't see colour; the vector rect fills extract
unreliably (stored as grayscale floats). Sampling the rendered page at 300 DPI in
a tight ring AROUND each printed spot-digit — where the tint sits, away from
gridlines — recovers the fill exactly. Validated 100% on the 7 July-2026 McValue
IOs (2-colour Vietnamese/Filipino and 5-colour SF/Seattle/Chinese), with per-colour
totals reconciling to each order's printed total.

Pairs with:
  • admerasia_positional.read_grid_cells — WHERE each spot is (row, day, count, bbox)
  • the vision ISCI-legend read           — WHICH creative each colour is
This module only reads colour; it does not decide creative identity.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field

import pdfplumber

try:
    from .admerasia_positional import read_grid_cells
except ImportError:  # direct-script / non-package import
    from admerasia_positional import read_grid_cells

_DPI = 300
_SCALE = _DPI / 72.0
_CLUSTER_TOL = 16          # RGB distance; palette colours are far more separated than this


@dataclass
class ColoredCell:
    row: int               # program-row index (top→bottom), from read_grid_cells
    col: int               # index into calendar_days
    day: int               # calendar day-of-month
    count: int             # spots in this cell
    rgb: tuple             # sampled fill colour (0-255)
    cluster: int = -1      # palette index (set after clustering)


@dataclass
class ColorGrid:
    calendar_days: list[int]
    cells: list[ColoredCell] = field(default_factory=list)
    palette: list[tuple] = field(default_factory=list)   # cluster centroids, most-used first


def _near_dark(p) -> bool:
    # Excludes the printed digit's glyph strokes (dark) so only the fill is measured.
    return p[0] < 110 and p[1] < 110 and p[2] < 110


def _dist(a, b) -> float:
    return sum((x - y) ** 2 for x, y in zip(a, b)) ** 0.5


def _cell_rgb(im, x0, x1, top, bottom, pad=2):
    """Median of non-dark pixels in a small box around the cell's digit glyph. The
    tint sits immediately around the centred digit, well inside the cell borders, so
    a tight box + median is robust to anti-aliasing and stray gridline pixels."""
    W, H = im.size
    px0 = max(0, int(x0 * _SCALE) - pad); px1 = min(W, int(x1 * _SCALE) + pad)
    py0 = max(0, int(top * _SCALE) - pad); py1 = min(H, int(bottom * _SCALE) + pad)
    px = [im.getpixel((xx, yy))[:3] for yy in range(py0, py1) for xx in range(px0, px1)]
    px = [p for p in px if not _near_dark(p)]
    if not px:
        return None
    return (int(statistics.median(p[0] for p in px)),
            int(statistics.median(p[1] for p in px)),
            int(statistics.median(p[2] for p in px)))


def read_color_grid(path: str, cluster_tol: float = _CLUSTER_TOL) -> ColorGrid:
    """Read every scheduled grid cell's fill colour and cluster the colours into the
    order's palette. Cell row/col/count come from the shared positional geometry."""
    geo = read_grid_cells(path)
    day_of = {i: d for i, d in enumerate(geo.calendar_days)}

    with pdfplumber.open(path) as pdf:
        im = pdf.pages[0].to_image(resolution=_DPI).original.convert("RGB")

    cells: list[ColoredCell] = []
    for c in geo.cells:
        if c.count <= 0:
            continue
        rgb = _cell_rgb(im, c.x0, c.x1, c.top, c.bottom)
        if rgb is None:
            continue
        cells.append(ColoredCell(row=c.row, col=c.col, day=day_of[c.col],
                                 count=c.count, rgb=rgb))

    # Greedy colour clustering, weighted by spot count; centroids are running means.
    clusters: list[list] = []   # [centroid(list[3]), weight]
    for cell in cells:
        for cl in clusters:
            if _dist(cl[0], cell.rgb) < cluster_tol:
                k, n = cl[1], cell.count
                cl[0] = [(cl[0][i] * k + cell.rgb[i] * n) / (k + n) for i in range(3)]
                cl[1] += n
                break
        else:
            clusters.append([list(cell.rgb), cell.count])
    clusters.sort(key=lambda c: -c[1])
    palette = [tuple(round(v) for v in c[0]) for c in clusters]

    for cell in cells:
        cell.cluster = min(range(len(palette)), key=lambda i: _dist(palette[i], cell.rgb))

    return ColorGrid(calendar_days=geo.calendar_days, cells=cells, palette=palette)
