"""
Time Advertising parser + daypart extraction — regression for the Graton SF
2026-08-21 mis-entry (contract 3022): the SF export prints a zero-spot summary
fragment ('$180.00 0 $ -') 1.1pt above the Mandarin program label, pdfplumber's
extract_text merges them into one line, the old end-of-string anchor missed
'8pm-10pm', and a PAID line silently entered as ROS 06:00-23:59 instead of
20:00-22:00. Both real Graton IOs are committed as fixtures — the SF one is the
only known file with the glue, the CVC one is the clean control — and each
carries its own GROSS TOTAL, so they are their own oracles.
"""

import sys
from pathlib import Path

import pytest

_root = Path(__file__).parent.parent.parent
for p in [str(_root), str(_root / "src")]:
    if p not in sys.path:
        sys.path.insert(0, p)

from browser_automation.timeadvertising_automation import (  # noqa: E402
    _line_description,
    _line_times,
)

FIXTURE_DIR = _root / "tests" / "fixtures" / "timeadvertising"


@pytest.fixture
def ta_parser(real_pdfplumber):
    """timeadvertising_parser with the REAL pdfplumber bound (the module-top
    import binds the conftest MagicMock; rebind and restore)."""
    from browser_automation.parsers import timeadvertising_parser as tp
    mocked = tp.pdfplumber
    tp.pdfplumber = real_pdfplumber
    yield tp
    tp.pdfplumber = mocked


def test_sfo_glued_summary_row_parses_clean(ta_parser):
    order = ta_parser.parse_timeadvertising_pdf(str(FIXTURE_DIR / "graton_sep2026_sfo.pdf"))
    assert order.market == "SFO"
    assert len(order.lines) == 3
    mand = order.lines[1]
    assert "Mand. News/Drama 8pm-10pm" in mand.program   # the glued junk rides along...
    assert _line_times(mand.program) == ("20:00", "22:00")           # ...but the daypart survives
    assert _line_description(mand.program) == "Mand. News/Drama 8pm-10pm"  # ...and the junk is dropped
    assert [ln.total_spots for ln in order.lines] == [12, 8, 10]
    assert sum(ln.rate * ln.total_spots for ln in order.lines) == 3600.0


def test_cvc_control_unchanged(ta_parser):
    order = ta_parser.parse_timeadvertising_pdf(str(FIXTURE_DIR / "graton_sep2026_cvc.pdf"))
    assert order.market == "CVC"
    assert [_line_times(ln.program) for ln in order.lines] == [
        ("19:00", "20:00"), ("20:00", "22:00"), ("06:00", "23:59"),
    ]
    assert sum(ln.rate * ln.total_spots for ln in order.lines) == 1200.0


def test_gross_total_guard():
    from browser_automation.parsers.timeadvertising_parser import _reconcile_gross_total

    class _Ln:
        def __init__(self, rate, total_spots):
            self.rate, self.total_spots = rate, total_spots

    lines = [_Ln(180.0, 12), _Ln(180.0, 8), _Ln(0.0, 10)]
    _reconcile_gross_total(lines, "GROSS TOTAL: $ 3,600.00", "x.pdf")   # match → no raise
    _reconcile_gross_total(lines, "no total printed here", "x.pdf")     # absent → no raise
    with pytest.raises(ValueError, match="GROSS TOTAL"):
        _reconcile_gross_total(lines, "GROSS TOTAL: $ 3,780.00", "x.pdf")

# The exact program string from the Graton SF run that mis-entered.
POLLUTED = "M-F: Mand. News/Drama 8pm-10pm $180.00 0 $ -"


def test_daypart_survives_glued_rate_junk():
    assert _line_times(POLLUTED) == ("20:00", "22:00")


def test_description_drops_glued_rate_junk():
    assert _line_description(POLLUTED) == "Mand. News/Drama 8pm-10pm"


def test_clean_program_unchanged():
    clean = "M-F: Cant. News/Talk 7pm-8pm"
    assert _line_times(clean) == ("19:00", "20:00")
    assert _line_description(clean) == "Cant. News/Talk 7pm-8pm"


def test_ros_line_still_defaults_to_full_day():
    ros = "M-Sun: ROS Free spots"
    assert _line_times(ros) == ("06:00", "23:59")
    assert _line_description(ros) == "ROS Free spots"
