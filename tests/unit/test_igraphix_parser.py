"""iGraphix channel-line extraction — market/language come from ONE line.

Regression for SRC 31348/31360/31363 (2026-08-24): iGraphix fixed their own
station-name typo ("Crossing TV" → "Crossings TV"), the extraction regex only
knew the old spelling, and all three orders silently defaulted to LAX/Filipino
(the real buys were CVC Hmong, SFO Vietnamese, SFO Filipino). Both spellings
must extract, and a total miss must REFUSE — identity fields never default.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_root = Path(__file__).parent.parent.parent
for _p in (_root, _root / "src"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from browser_automation.parsers import igraphix_parser as igx  # noqa: E402

# Real channel lines: the agency's OLD spelling (pre-Aug-2026 IOs) and the NEW
# one (SRC 31363/31348/31360). Each with the market/language it must yield.
OLD_LINES = [
    ("Crossing TV Spectrum channel 1519", "LAX", "Filipino"),
    ("Crossing TV Comcast Ch. 398 Central Valley", "CVC", "Filipino"),
    ("Crossing TV - XfinityTV CH. 3131 SF Vietnamese", "SFO", "Vietnamese"),
]
NEW_LINES = [
    ("Crossings TV Comcast Ch. 398 Central Valley (Hmong) $720.00", "CVC", "Hmong"),
    ("Crossings TV - XfinityTV CH. 3131 SF Vietnamese $2,080.00", "SFO", "Vietnamese"),
    ("Crossings TV - XfinityTV Ch. 3131 SF Filipino $1,530.00", "SFO", "Filipino"),
]


def _io_text(channel_line):
    return (
        "Insertion Order\nTo: Crossings TV\nPurchase #: 00031360\n"
        "Advertiser: IGraphix\nc/o Sky River Casino\n"
        f"Insertion Date: DESCRIPTION: AMOUNT:\n{channel_line}\n"
        "1) M-F: 4pm-7pm (30 sec) x 17 spots\nNet Total: $1,530.00\n"
    )


@pytest.mark.parametrize(("line", "market", "language"), OLD_LINES + NEW_LINES)
def test_channel_line_extracts_for_both_spellings(line, market, language):
    desc = igx._extract_channel_description(_io_text(line))
    assert desc != "Unknown Channel"
    assert igx._parse_market_from_channel(desc, "Sky River Casino")[0] == market
    assert igx._parse_language_from_channel(desc)[0] == language


def test_missing_channel_line_is_unknown():
    text = _io_text("").replace("\n\n", "\n")
    assert igx._extract_channel_description(text) == "Unknown Channel"


def test_parse_refuses_when_channel_line_missing(monkeypatch):
    """A miss must raise, never enter LAX/Filipino defaults (SRC 31360 bug)."""

    class _Page:
        def extract_text(self):
            return _io_text("Some Other Network Ch. 999 SF Filipino")

    class _Pdf:
        pages = [_Page()]

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(igx.pdfplumber, "open", lambda _p: _Pdf())
    with pytest.raises(ValueError, match="channel line not found"):
        igx.parse_igraphix_pdf("fake.pdf")
