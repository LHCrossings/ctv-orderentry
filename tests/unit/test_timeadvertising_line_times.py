"""
Time Advertising daypart extraction — regression for the Graton SF 2026-08-21
mis-entry (contract 3022): pdfplumber glued a neighbor row's rate cells onto
the program line, the old end-of-string anchor missed '8pm-10pm', and a PAID
line silently entered as ROS 06:00-23:59 instead of 20:00-22:00.
"""

import sys
from pathlib import Path

_root = Path(__file__).parent.parent.parent
for p in [str(_root), str(_root / "src")]:
    if p not in sys.path:
        sys.path.insert(0, p)

from browser_automation.timeadvertising_automation import (  # noqa: E402
    _line_description,
    _line_times,
)

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
