"""Two Admerasia sheet quirks from the 2611 McDonald's batch (Lee 2026-11-02).

1. "10:300p" is a common Admerasia typo and always means 10:30p. Entry read it right,
   the language-window check read it as 10:30 AM and warned, the verification table
   printed the typo. It is normalised once, on the line.
2. The Chinese IO prints "Grand Total 47" where the others print "Order Total 39"; the
   reconciliation guard only knew the first wording and stayed silent.
"""

import sys
from decimal import Decimal
from pathlib import Path

_root = Path(__file__).resolve().parents[2]
for _p in (_root, _root / "browser_automation"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from browser_automation.parsers.admerasia_parser import (  # noqa: E402
    AdmerasiaLine,
    normalize_time_typos,
    order_total_from_text,
)


def _line(t):
    return AdmerasiaLine(
        days="VARIES", time=t, net_rate=Decimal("29.75"), weekly_spots=[], spot_length=15
    )


def test_extra_minute_digit_is_dropped_on_the_line():
    assert _line("10:300p-12:00a").time == "10:30p-12:00a"
    assert _line("11:300a-12:00p").time == "11:30a-12:00p"
    assert _line("10:30p-12:000a").time == "10:30p-12:00a"
    assert _line("PST 10:300p-12:00a").time == "PST 10:30p-12:00a"


def test_well_formed_times_are_unchanged():
    for t in (
        "6:00a-7:00a",
        "7:00-7:30p",
        "11:30-12:00p",
        "10:30p-12:00a",
        "7-730p",
        "6a-11:59p",
        "",
        "TBD",
    ):
        assert normalize_time_typos(t) == t
        assert _line(t).time == t


def test_total_accepts_order_total_and_grand_total():
    assert order_total_from_text("... Day Part. Order Total 39 $ 2 ,796.50\nNote:") == 39
    assert order_total_from_text("... Day Part. Grand Total 47 $ 1,411.00\nNote:") == 47
    assert order_total_from_text("no total row here") is None
    assert order_total_from_text("") is None
