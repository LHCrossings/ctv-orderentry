"""Multi-estimate H&L PDFs: each contract's code/description must carry its OWN
estimate number, not the first estimate's number plus an ' Est N' suffix.

Regression for ACM Q4 TOYOTA (contracts 3026-3028, 2026-08-24): all three
contracts entered as 'HL Toyota 13937 CV Est 1393x' and had to be renamed by
hand to 'HL Toyota 13938 CV' / 'HL Toyota 13939 CV'.
"""

import sys
from pathlib import Path

# Add browser_automation + repo root to path (same pattern as sibling tests)
_root = Path(__file__).parent.parent.parent
for _p in (_root, _root / "browser_automation"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from browser_automation.customer_defaults import per_estimate_text


def test_swaps_first_estimate_number_in_code():
    assert per_estimate_text("HL Toyota 13937 CV", "13937", "13938") == "HL Toyota 13938 CV"
    assert per_estimate_text("HL Toyota 13937 CV", "13937", "13939") == "HL Toyota 13939 CV"


def test_first_estimate_is_unchanged():
    assert per_estimate_text("HL Toyota 13937 CV", "13937", "13937") == "HL Toyota 13937 CV"


def test_swaps_number_in_description():
    assert per_estimate_text("Toyota CVC Est 13937", "13937", "13938") == "Toyota CVC Est 13938"


def test_falls_back_to_suffix_when_number_absent():
    # A user-typed code without the estimate number must still yield unique codes
    assert per_estimate_text("HL Toyota Q4 CV", "13937", "13938") == "HL Toyota Q4 CV Est 13938"


def test_both_hl_automations_use_the_shared_helper():
    import browser_automation.hl_automation as hla
    import browser_automation.hl_bdr_automation as bdr

    assert hla._per_estimate_text is per_estimate_text
    assert bdr._per_estimate_text is per_estimate_text
