"""
Deterministic ISCI-legend reader for Admerasia IOs — text + positional swatch colour.

WHY THIS EXISTS: the legend used to be read by vision, which returns (isci, duration,
colour) but no LANGUAGE. A Chinese IO carries TWO legend blocks (Mandarin + Cantonese)
that reuse the IDENTICAL swatch colours, so colour alone maps one grid colour to two
ISCIs and the language is unrecoverable. The language is, however, printed as plain
text on every legend row ("Cantonese :15 Beverages Core Portfolio 7 Drinks MCIC087526VH"),
and the row's fill colour can be sampled from the rendered page — both deterministic.

Reading the legend from the text layer also removes a vision call from the common path.
Vision remains the fallback (see admerasia_traffic.resolve_traffic) for any IO whose
legend text does not parse.

Row-format variants handled (all 7 known Admerasia IOs parse):
  • "Mandarin :15 <title> MCIM106526VH"          — ISCI last  (Chinese, HOU/NYC/SEA/SFO-14)
  • "Vietnamese :15s MCIV005526VH <title> (50%)" — ISCI first (SFO 06-MD10-2603VT)
  • "Taglish :15 <title> MCIT104525VH"           — non-obvious language name (LAX)
  • leading boilerplate on the same visual line ("Order Number: …", "Version: Original")

ISCI typos: the Beverages Launch IO prints letter "O" for digit zero
(`MCIMO46526VH` for `MCIM046526VH`). :func:`normalize_isci` repairs the numeric body.
This module reports what it reads; it does not decide creative identity.
"""

from __future__ import annotations

import re
import statistics
from dataclasses import dataclass, field

import pdfplumber

_DPI = 300
_SCALE = _DPI / 72.0

# Language names as Admerasia writes them → our system language.
_LANG_ALIASES = {
    "mandarin": "Mandarin",
    "cantonese": "Cantonese",
    "chinese": "Chinese",
    "vietnamese": "Vietnamese",
    "filipino": "Filipino",
    "tagalog": "Filipino",
    "taglish": "Filipino",
    "korean": "Korean",
    "hmong": "Hmong",
    "punjabi": "Punjabi",
    "hindi": "Hindi",
    "japanese": "Japanese",
    "english": "English",
}
_LANG_RE = re.compile(r"\b(" + "|".join(sorted(_LANG_ALIASES, key=len, reverse=True)) + r")\b",
                      re.IGNORECASE)

# MCI + language letter + 6-digit body (may contain letter-O typos) + trailing letters.
_ISCI_RE = re.compile(r"\bMCI[A-Z][0-9O]{5,7}[A-Z]{0,3}\b")
_DUR_RE = re.compile(r":(\d{2})s?\b")
# Header boilerplate that shares a visual line with a legend row.
_BOILER_RE = re.compile(
    r"\b(Order\s+Number|Order\s+Date|Version|Ref|Campaign|Campaign\s+Period|DMA)\s*:\s*\S+",
    re.IGNORECASE)
_ROW_TOL = 3.0          # pt; words within this of each other are one visual line
_NEAR_DARK = 110        # pixel below this on all channels = glyph stroke, not fill


@dataclass
class TextLegendRow:
    """Mirrors admerasia_vision.LegendRow (isci_code/duration_sec/color_rgb/color_name)
    so it is a drop-in replacement, plus the `language` the vision read cannot give."""
    isci_code: str
    duration_sec: int | None
    color_rgb: list
    color_name: str = ""
    language: str | None = None
    title: str = ""
    raw_isci: str = ""          # as printed, before O→0 repair


@dataclass
class TextLegend:
    rows: list = field(default_factory=list)
    warnings: list = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """Usable only if every row yielded an ISCI *and* a language — a partial read
        must fall back to vision rather than silently drop a creative."""
        return bool(self.rows) and all(r.language for r in self.rows)


def normalize_isci(code: str) -> str:
    """Repair letter-O-for-zero in the numeric body ('MCIMO46526VH' → 'MCIM046526VH').

    Only the body between the language letter (index 3) and any trailing alpha suffix
    is touched, so a legitimate letter elsewhere is preserved."""
    m = re.fullmatch(r"(MCI[A-Z])([0-9O]+)([A-Z]*)", code.upper())
    if not m:
        return code.upper()
    return m.group(1) + m.group(2).replace("O", "0") + m.group(3)


def _sample_fill(im, x0, x1, top, bottom, pad=1):
    """Median of non-dark pixels over a text span — the row's highlight fill."""
    W, H = im.size
    px0 = max(0, int(x0 * _SCALE) - pad)
    px1 = min(W, int(x1 * _SCALE) + pad)
    py0 = max(0, int(top * _SCALE) - pad)
    py1 = min(H, int(bottom * _SCALE) + pad)
    if px1 <= px0 or py1 <= py0:
        return None
    px = [im.getpixel((x, y))[:3] for y in range(py0, py1) for x in range(px0, px1)]
    px = [p for p in px if not (p[0] < _NEAR_DARK and p[1] < _NEAR_DARK and p[2] < _NEAR_DARK)]
    if not px:
        return None
    return [int(statistics.median(p[i] for p in px)) for i in range(3)]


def read_text_legend(path: str) -> TextLegend:
    """Read the ISCI legend from page 1's text layer + sampled row fill colours.

    One row per ISCI token occurrence (NOT deduped by code — a duplicated ISCI is a
    real agency typo worth surfacing; see the Beverages Launch IO, where Yap Session
    and Macro Strawberry Watermelon both print MCIMO47526VH)."""
    res = TextLegend()
    with pdfplumber.open(path) as pdf:
        page = pdf.pages[0]
        words = page.extract_words()
        im = page.to_image(resolution=_DPI).original.convert("RGB")

    tokens = [w for w in words if _ISCI_RE.fullmatch(w["text"])]
    if not tokens:
        res.warnings.append("no ISCI tokens in the page text layer")
        return res

    seen_span: set = set()
    for w in sorted(tokens, key=lambda w: (w["top"], w["x0"])):
        # Overlapping text layers can repeat the same token at ~equal coordinates.
        span = (round(w["top"], 0), round(w["x0"], 0), w["text"])
        if span in seen_span:
            continue
        seen_span.add(span)

        line = sorted([q for q in words if abs(q["top"] - w["top"]) < _ROW_TOL],
                      key=lambda q: q["x0"])
        text = " ".join(q["text"] for q in line)
        core = _BOILER_RE.sub("", text)

        lm = _LANG_RE.search(core)
        dm = _DUR_RE.search(core)
        title = _ISCI_RE.sub("", core)
        if lm:
            title = title.replace(lm.group(0), "", 1)
        if dm:
            title = title.replace(dm.group(0), "", 1)

        raw = w["text"].upper()
        res.rows.append(TextLegendRow(
            isci_code=normalize_isci(raw),
            duration_sec=int(dm.group(1)) if dm else None,
            color_rgb=_sample_fill(im, w["x0"], w["x1"], w["top"], w["bottom"]) or [255, 255, 255],
            language=_LANG_ALIASES[lm.group(1).lower()] if lm else None,
            title=" ".join(title.split()),
            raw_isci=raw,
        ))

    for r in res.rows:
        if r.raw_isci != r.isci_code:
            res.warnings.append(
                f"IO prints {r.raw_isci} (letter O for zero) — read as {r.isci_code}")
        if not r.language:
            res.warnings.append(f"no language on the legend row for {r.isci_code}")
        if r.duration_sec is None:
            res.warnings.append(f"no duration on the legend row for {r.isci_code}")

    # A repeated (language, ISCI) pair means the IO gave two creatives the same code.
    by_key: dict = {}
    for r in res.rows:
        by_key.setdefault((r.language, r.isci_code), []).append(r.title)
    for (lang, isci), titles in by_key.items():
        if len(titles) > 1:
            res.warnings.append(
                f"{lang or '?'} ISCI {isci} is listed {len(titles)}x "
                f"({'; '.join(t[:40] for t in titles)}) — likely an IO typo")

    return res
