"""
Vision-based reader for the Admerasia / McDonald's broadcast-order calendar grid.

WHY: pdfplumber's table extraction collapses merged calendar cells and loses the
exact column each spot sits under, producing count-preserving day shifts that the
spot-total check cannot catch (see contract 2935 lines 3 & 6). Claude reads the
rendered grid the way a person does — by visual column alignment — so it recovers
the true per-day placement.

This module ONLY reads the grid (per-row daily spot counts + rate + daypart + the
printed totals). Header facts (order #, language, market, campaign period) stay with
the text-based helpers in admerasia_parser.py. The output feeds the existing
AdmerasiaLine/AdmerasiaOrder dataclasses unchanged.

Model: claude-opus-4-8 (built-in high-res PDF vision). Structured output via
messages.parse(). Two independent passes must agree, and each row's spot counts
must reconcile to the printed Total Spots — any failure raises for human review.
"""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path

from pydantic import BaseModel, Field

MODEL = "claude-opus-4-8"


class AdmerasiaVisionError(RuntimeError):
    """Raised when the vision read cannot be trusted (guardrails failed)."""


# ─── Output schema ────────────────────────────────────────────────────────────

class VisionLine(BaseModel):
    spot_length: int = Field(description="Spot length in seconds for THIS row, from the Length column section it falls under (15 or 30).")
    program: str = Field(description="Program Name cell text, e.g. '(M-F) Mandarin Drama' or 'Magandang Buhay'.")
    daypart: str = Field(description="The Day Part air-time window exactly as written but WITHOUT any timezone prefix (drop PST/PT/ET/CT/EST/etc.). E.g. '6:00a-7:00a', '6:00-6:30p', '10:00p-11:30p', '11:30-12:00p'.")
    net_rate: float = Field(description="The Unit Cost (Net) dollar amount for this row, e.g. 21.25, 29.75, 55.00.")
    daily_spots: list[int] = Field(description="One integer per calendar-day column, in the SAME left-to-right order and SAME length as calendar_days. The number of spots in the cell directly under that day's column; 0 where the cell is blank. Read strictly by visual column alignment — never shift a value left or right.")
    printed_total: int = Field(description="The 'Total Spots' value printed at the right end of THIS row.")


class VisionGrid(BaseModel):
    order_number: str = Field(description="Order Number from the header, e.g. '04-MD10-2607FT'. Empty string if not found.")
    language: str = Field(description="Chinese, Vietnamese, or Filipino (from the ISCI/version section).")
    market: str = Field(description="The DMA / market name from the header, e.g. 'New York', 'Seattle', 'Houston', 'Los Angeles'.")
    campaign_period: str = Field(description="Campaign Period exactly as written, e.g. '7/7/2026 - 8/10/2026'. Empty string if not found.")
    calendar_days: list[int] = Field(description="The row of day-of-MONTH numbers in the grid header, left to right (e.g. [7,8,9,...,31,1,2,...,9]). These define the columns; daily_spots aligns to this 1:1.")
    lines: list[VisionLine] = Field(description="Every PROGRAM row in the Broadcast Order grid (both the :15 and :30 sections). Do NOT include the Total/Grand-Total footer rows.")
    order_total: int = Field(description="The grand total spots printed in the 'Order Total' or 'Grand Total' box. 0 if none is printed.")
    warnings: list[str] = Field(default_factory=list, description="Anything ambiguous or hard to read — cite the row.")


_SYSTEM = """You read the calendar grid of an Admerasia / McDonald's television broadcast order into structured data. Accuracy of DAY placement is the entire job.

Grid layout:
- The 'Broadcast Order' table has a header with a row of day-of-week letters (T W R F S U then M T W R F S U, repeating) and DIRECTLY BELOW it a row of day-of-MONTH numbers (7 8 9 10 ... 31 1 2 ... 9). Output that day-number row, left to right, as calendar_days.
- The left 'Length' column shows :15 or :30 and applies to every program row beneath it until it changes. Each program row's spot_length is the section it sits in.
- Each program row has: Program Name, Day Part (air time), Unit Cost (Net) (a dollar amount), then one cell under each calendar-day column. A cell holds the number of spots that day (usually 1, sometimes 2); a blank cell means 0 spots that day.
- At the right end of each row is the printed 'Total Spots'. At the bottom is the 'Order Total' / 'Grand Total'.

Rules:
- For each program row, output daily_spots as a list with EXACTLY one integer per calendar_days column, in the same left-to-right order. Put the cell's number under each day; 0 for blank. The list length MUST equal len(calendar_days).
- Determine each spot's day by VISUAL COLUMN ALIGNMENT — the cell vertically under a given day-number column belongs to that day. Cells can be shaded (white / purple / blue / orange / etc.); the shading is a creative assignment and does NOT change the count — read the printed digit, and treat a shaded-but-empty cell as 0.
- sum(daily_spots) for a row MUST equal that row's printed Total Spots. If you can't make them match, still give your best reading and add a specific warning.
- daypart: copy the time window but strip any timezone prefix (PST, PT, ET, CT, EST, ...). Keep am/pm exactly.
- Do NOT include footer/total rows as program rows. Do NOT invent rows.
- Read the header facts (order_number, language, market, campaign_period) from the document text."""

_INSTRUCTION = (
    "You are given the PDF plus TWO high-resolution images: the LEFT and RIGHT halves "
    "of the same Broadcast Order grid (they overlap by a few day columns — use the "
    "day-of-month header numbers visible in each to align them). Read the grid into the "
    "required schema, producing ONE unified result whose calendar_days and per-row "
    "daily_spots span the WHOLE grid left-to-right. Count each cell from the high-res "
    "images by visual column alignment, and double-check every row's daily_spots sum to "
    "its printed Total Spots."
)


def _grid_half_pngs(path: str) -> list[bytes]:
    """Render the broadcast-order grid as two overlapping high-res PNG halves.

    A tight, split, high-DPI crop gives the model enough pixels per calendar
    column to count cells exactly (a full-page render is downscaled too far)."""
    import pdfplumber
    from collections import defaultdict

    with pdfplumber.open(path) as pdf:
        p = pdf.pages[0]
        digs = [w for w in p.extract_words() if w["text"].isdigit()]
        byy: dict = defaultdict(list)
        for w in digs:
            byy[round(w["top"])].append(w)
        # day-number row = the row with the most day-of-month values
        dn_y = max(byy, key=lambda y: sum(1 for w in byy[y] if 1 <= int(w["text"]) <= 31))
        w_pt = p.width
        top = max(0, dn_y - 38)
        bot = min(p.height, dn_y + 185)   # covers headers + up to ~12 program rows
        out = []
        for x0, x1 in ((0, w_pt * 0.57), (w_pt * 0.43, w_pt)):
            img = p.crop((x0, top, x1, bot)).to_image(resolution=300)
            import io
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            out.append(buf.getvalue())
    return out


# ─── One extraction pass ──────────────────────────────────────────────────────

def _extract_once(path: str, model: str = MODEL, max_tokens: int = 16000) -> VisionGrid:
    import anthropic

    pdf_b64 = base64.standard_b64encode(Path(path).read_bytes()).decode("utf-8")
    content = [
        {"type": "document",
         "source": {"type": "base64", "media_type": "application/pdf", "data": pdf_b64}},
    ]
    for png in _grid_half_pngs(path):
        content.append({
            "type": "image",
            "source": {"type": "base64", "media_type": "image/png",
                       "data": base64.standard_b64encode(png).decode("utf-8")},
        })
    content.append({"type": "text", "text": _INSTRUCTION})

    client = anthropic.Anthropic()  # ANTHROPIC_API_KEY from env (.env loaded by caller)
    resp = client.messages.parse(
        model=model,
        max_tokens=max_tokens,
        system=_SYSTEM,
        messages=[{"role": "user", "content": content}],
        output_format=VisionGrid,
    )
    return resp.parsed_output


# ─── ISCI legend (colour → creative) ───────────────────────────────────────────
# Used by the traffic auto-assigner. The deterministic pixel reader nails which
# COLOUR each grid cell is, but not which CREATIVE a colour maps to: the ISCI list
# sits at a different spot on every IO and the codes are a garbled Type3 font, so
# pixel/positional heuristics mislabel. Vision reads the small (2-5 row) legend the
# way a human does — trivially — and returns the colour of each ISCI's row.


class LegendRow(BaseModel):
    isci_code: str = Field(description="The ISCI/house code for this row, exactly as printed in the rendered image (e.g. 'MCIV089526VH'). Read the glyphs from the image, not the text layer. The 4th char is a letter and chars 5-10 are digits — never confuse letter O with digit 0.")
    duration_sec: int = Field(description="Spot length for this creative in seconds (15 or 30), from the ':15'/':30' on the row.")
    color_name: str = Field(description="Plain-language name of THIS row's background/highlight fill colour, e.g. 'white', 'lavender/mauve', 'light cyan', 'gold/orange', 'pale green'. This is the colour used to mark this creative's cells in the calendar grid below.")
    color_rgb: list[int] = Field(description="Best-estimate [R,G,B] (each 0-255) of this row's fill colour. White ≈ [255,255,255].")


class VisionLegend(BaseModel):
    rows: list[LegendRow] = Field(description="Every creative row in the top ISCI/version list, top-to-bottom, one per ISCI. Do NOT include address, header, or grid rows.")
    warnings: list[str] = Field(default_factory=list, description="Anything ambiguous — cite the ISCI.")


_LEGEND_SYSTEM = """You read the small ISCI / creative-version list near the top of an Admerasia / McDonald's TV insertion order. Each row lists one creative: language, spot length (:15 or :30), an ISCI/house code (e.g. MCIV089526VH), and a title. Crucially, each row is highlighted with a FILL COLOUR — that colour is how the creative is marked in the calendar grid lower on the page. One row may be plain white (that is still a valid, meaningful colour).

Your job: output every creative row, top-to-bottom, with its exact ISCI code, duration, and the row's fill colour (a plain name AND a best-estimate RGB). Read the ISCI codes from the rendered image glyphs (the embedded text is garbled) — the 4th character is a letter, the next six are digits; never read a letter 'O' where a digit '0' belongs. Do not include the mailing address, page header, or the calendar grid — only the ISCI/version list rows."""

_LEGEND_INSTRUCTION = (
    "You are given the PDF plus a high-resolution image of the top of the page. Read the "
    "ISCI/version list into the schema: one row per creative, top-to-bottom, each with its "
    "exact ISCI code, spot length, and the fill colour of that row (name + RGB). Include a "
    "plain-white row if present — white is a meaningful colour here."
)


def _legend_png(path: str, dpi: int = 400) -> bytes:
    """High-res render of the page area ABOVE the calendar grid (where the ISCI list
    lives). High DPI + tight crop gives the model clean glyphs and true fill colours."""
    import io

    import pdfplumber
    from collections import defaultdict

    with pdfplumber.open(path) as pdf:
        p = pdf.pages[0]
        digs = [w for w in p.extract_words() if w["text"].isdigit()]
        byy: dict = defaultdict(list)
        for w in digs:
            byy[round(w["top"])].append(w)
        dn_y = max(byy, key=lambda y: sum(1 for w in byy[y] if 1 <= int(w["text"]) <= 31))
        crop = p.crop((0, 0, p.width, min(p.height, dn_y + 2)))
        img = crop.to_image(resolution=dpi)
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()


def _extract_legend_once(path: str, model: str = MODEL, max_tokens: int = 4000) -> VisionLegend:
    import anthropic

    pdf_b64 = base64.standard_b64encode(Path(path).read_bytes()).decode("utf-8")
    png = _legend_png(path)
    content = [
        {"type": "document",
         "source": {"type": "base64", "media_type": "application/pdf", "data": pdf_b64}},
        {"type": "image",
         "source": {"type": "base64", "media_type": "image/png",
                    "data": base64.standard_b64encode(png).decode("utf-8")}},
        {"type": "text", "text": _LEGEND_INSTRUCTION},
    ]
    client = anthropic.Anthropic()
    resp = client.messages.parse(
        model=model,
        max_tokens=max_tokens,
        system=_LEGEND_SYSTEM,
        messages=[{"role": "user", "content": content}],
        output_format=VisionLegend,
    )
    return resp.parsed_output


def extract_isci_legend(path: str, refresh: bool = False) -> VisionLegend:
    """Vision read of the ISCI legend → ordered [(isci_code, duration_sec, colour)].
    Two independent passes must agree on the ISCI set + durations (colours may differ
    slightly), else AdmerasiaVisionError. Cached in a `<file>.adm-legend.json` sidecar."""
    sc = str(path) + ".adm-legend.json"
    if not refresh and os.path.exists(sc):
        return VisionLegend.model_validate(json.loads(Path(sc).read_text())["legend"])

    a = _extract_legend_once(path)
    b = _extract_legend_once(path)
    ka = [(r.isci_code, r.duration_sec) for r in a.rows]
    kb = [(r.isci_code, r.duration_sec) for r in b.rows]
    if ka != kb:
        raise AdmerasiaVisionError(
            "Admerasia ISCI-legend vision read disagreed between passes — manual review:\n"
            f"  pass1: {ka}\n  pass2: {kb}"
        )
    try:
        Path(sc).write_text(json.dumps({"legend": a.model_dump()}, indent=2, default=str))
    except Exception as exc:
        print(f"[ADM-VISION] Warning: could not cache legend: {exc}")
    return a


# ─── Guardrails ───────────────────────────────────────────────────────────────

def _norm_dp(dp: str) -> str:
    return dp.replace(" ", "").lower()


def _check_metadata_agreement(a: VisionGrid, b: VisionGrid) -> list[str]:
    """
    Two passes must agree on the ROW STRUCTURE + METADATA: row count and, per row
    (matched top-to-bottom by position — daypart isn't unique, e.g. three 9-10p rows
    in the Chinese order), (spot_length, daypart, printed_total). We do NOT require
    the per-cell daily_spots to agree — the authoritative spot counts come from the
    deterministic positional reader; vision only supplies the metadata it reads
    reliably from the rendered text.
    """
    errs: list[str] = []
    if len(a.lines) != len(b.lines):
        return [f"row count differs between passes ({len(a.lines)} vs {len(b.lines)})"]
    for i, (x, y) in enumerate(zip(a.lines, b.lines), start=1):
        if x.spot_length != y.spot_length or _norm_dp(x.daypart) != _norm_dp(y.daypart):
            errs.append(f"row {i}: structure differs ({x.spot_length}s {x.daypart} vs {y.spot_length}s {y.daypart})")
        if x.printed_total != y.printed_total:
            errs.append(f"row {i} ({x.daypart}): printed Total Spots differ ({x.printed_total} vs {y.printed_total})")
        if abs(x.net_rate - y.net_rate) > 0.005:
            errs.append(f"row {i} ({x.daypart}): net rate differs (${x.net_rate} vs ${y.net_rate})")
    return errs


# ─── Public: validated metadata extraction with caching ───────────────────────

def _sidecar(path: str) -> str:
    return str(path) + ".adm.json"


def extract_grid(path: str, refresh: bool = False) -> VisionGrid:
    """
    Vision read of the Admerasia order, used for ROW STRUCTURE + METADATA
    (spot_length, daypart, net_rate, printed_total) and as a soft cross-check on
    the grid. Two independent passes must agree on the metadata (row count,
    dayparts, printed totals) — otherwise AdmerasiaVisionError is raised.

    The authoritative per-day spot counts are NOT taken from here; they come from
    the deterministic positional reader and are reconciled against each row's
    printed_total downstream.

    Cached in a `<file>.adm.json` sidecar so preview + entry share the read.
    """
    sc = _sidecar(path)
    if not refresh and os.path.exists(sc):
        return VisionGrid.model_validate(json.loads(Path(sc).read_text())["grid"])

    pass1 = _extract_once(path)
    pass2 = _extract_once(path)
    disagree = _check_metadata_agreement(pass1, pass2)
    if disagree:
        raise AdmerasiaVisionError(
            "Admerasia vision metadata read disagreed between passes — manual review required:\n  - "
            + "\n  - ".join(disagree)
        )

    try:
        Path(sc).write_text(json.dumps({"grid": pass1.model_dump()}, indent=2, default=str))
    except Exception as exc:
        print(f"[ADM-VISION] Warning: could not cache extraction: {exc}")
    return pass1
