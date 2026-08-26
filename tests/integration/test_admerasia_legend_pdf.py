"""
Real-PDF checks for the deterministic Admerasia ISCI-legend reader.

Needs genuine pdfplumber + PIL rendering (the unit conftest mocks pdfplumber), and the
IO fixtures in incoming/Used — every test skips cleanly when a fixture is absent.

The regression bar, per the coordinate-change lesson in tasks/lessons.md: the reader
must reproduce the known-good row count and language for EVERY prior IO, and only the
Chinese ones may report a colour shared across languages.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

_root = Path(__file__).parent.parent.parent
for _p in (_root, _root / "browser_automation"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

# tests/conftest.py installs a MagicMock for pdfplumber (so the unit suite collects
# without the browser stack). This module needs the REAL thing: drop the mock, along
# with any module that already captured it, then import for real.
#
# The swap MUST be undone afterwards (see _restore_pdfplumber_mock): tests/integration
# is collected BEFORE tests/unit, and leaving the real pdfplumber installed makes the
# still-mocked unit tests (e.g. test_order_scanner) start really parsing files.
_SAVED_MODULES = {
    k: sys.modules.get(k)
    for k in [m for m in list(sys.modules) if m == "pdfplumber" or m.startswith("pdfplumber.")]
    + ["browser_automation.parsers.admerasia_traffic_legend", "admerasia_traffic_legend"]
}

for _m, _obj in list(_SAVED_MODULES.items()):
    if isinstance(_obj, MagicMock) or _m.endswith("admerasia_traffic_legend"):
        sys.modules.pop(_m, None)

pdfplumber = pytest.importorskip("pdfplumber")
if isinstance(pdfplumber, MagicMock):
    pytest.skip("pdfplumber is mocked", allow_module_level=True)

from browser_automation.parsers.admerasia_traffic_legend import read_text_legend  # noqa: E402


@pytest.fixture(scope="module", autouse=True)
def _restore_pdfplumber_mock():
    """Put the conftest mocks back so later (unit) tests see the environment they expect."""
    yield
    for _m in [
        m
        for m in list(sys.modules)
        if m == "pdfplumber"
        or m.startswith("pdfplumber.")
        or m.endswith("admerasia_traffic_legend")
    ]:
        sys.modules.pop(_m, None)
    for _m, _obj in _SAVED_MODULES.items():
        if _obj is not None:
            sys.modules[_m] = _obj


_USED = _root / "incoming" / "Used"

# (filename, expected row count, expected set of languages)
_CASES = [
    ("Admerasia - McDonald's HOU 14-MD10-2602VT.pdf", 1, {"Vietnamese"}),
    ("Admerasia - McDonald's NYC 14-MD10-2602VT.pdf", 1, {"Vietnamese"}),
    ("Admerasia - McDonald's SEA 15-MD10-2602VT.pdf", 1, {"Vietnamese"}),
    ("Admerasia - McDonald's SFO 14-MD10-2602VT.pdf", 1, {"Vietnamese"}),
    # ISCI printed BEFORE the title, duration written ':15s'
    ("Admerasia - McDonald's SFO 06-MD10-2603VT.pdf", 2, {"Vietnamese"}),
    # 'Taglish' -> Filipino
    ("Admerasia - McDonald's LAX 5-MD10-2602FT.pdf", 1, {"Filipino"}),
    # Chinese: two legend blocks, 5 creatives each
    ("TV-MD26-Chinese IO-Beverages Launch_Crossings.pdf", 10, {"Mandarin", "Cantonese"}),
]


def _legend(name):
    p = _USED / name
    if not p.exists():
        pytest.skip(f"fixture missing: {name}")
    return read_text_legend(str(p))


@pytest.mark.parametrize("name,n_rows,langs", _CASES)
def test_legend_rows_and_languages(name, n_rows, langs):
    L = _legend(name)
    assert L.ok, f"{name}: legend unusable — {L.warnings}"
    assert len(L.rows) == n_rows
    assert {r.language for r in L.rows} == langs
    assert all(r.duration_sec in (15, 30) for r in L.rows)
    assert all(r.isci_code.startswith("MCI") for r in L.rows)


def test_single_language_ios_have_one_isci_per_colour():
    """Guard for the 6 pre-existing orders: no colour may carry two languages, so the
    language-aware path is a no-op for them."""
    for name, _, langs in _CASES:
        if len(langs) > 1:
            continue
        L = _legend(name)
        by_colour = {}
        for r in L.rows:
            by_colour.setdefault(tuple(r.color_rgb), set()).add(r.language)
        assert all(len(v) == 1 for v in by_colour.values()), name


def test_chinese_io_shares_every_colour_across_both_languages():
    """The whole reason this work exists — Mandarin and Cantonese legend blocks use the
    IDENTICAL swatch per creative, so colour alone cannot identify an ISCI."""
    L = _legend("TV-MD26-Chinese IO-Beverages Launch_Crossings.pdf")
    by_colour = {}
    for r in L.rows:
        by_colour.setdefault(tuple(r.color_rgb), set()).add(r.language)
    assert by_colour, "no legend colours sampled"
    assert all(v == {"Mandarin", "Cantonese"} for v in by_colour.values()), by_colour


def test_letter_o_for_zero_is_repaired_and_reported():
    """The Beverages Launch IO literally prints MCIMO46526VH for MCIM046526VH."""
    L = _legend("TV-MD26-Chinese IO-Beverages Launch_Crossings.pdf")
    codes = {r.isci_code for r in L.rows}
    assert "MCIM046526VH" in codes
    assert "MCIC002326VH" in codes
    assert not any("O" in c[4:] for c in codes), codes
    assert any("letter O for zero" in w for w in L.warnings)


def test_duplicate_isci_typo_is_reported():
    """That IO also gives Yap Session and Macro Strawberry Watermelon the same code —
    a real agency error that must surface rather than silently mis-traffic."""
    L = _legend("TV-MD26-Chinese IO-Beverages Launch_Crossings.pdf")
    assert any("is listed 2x" in w for w in L.warnings), L.warnings
