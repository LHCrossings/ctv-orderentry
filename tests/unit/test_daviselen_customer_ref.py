"""Daviselen Customer Order Ref (Lee 10/16): entered as "Est <n>, Order <m>" on every
Daviselen contract; the backwrite front page shows that whole text and the run sheet
shows the estimate number alone."""

import sys
from pathlib import Path
from types import SimpleNamespace

_root = Path(__file__).resolve().parents[2]
for _p in [str(_root), str(_root / "src")]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

from backwrite.eterebridge_runner import daviselen_estimates, is_daviselen  # noqa: E402
from browser_automation.parsers.daviselen_parser import (  # noqa: E402
    get_default_customer_order_ref,
)


def test_default_ref_is_estimate_then_order():
    order = SimpleNamespace(estimate_number="74", order_number="32064")
    assert get_default_customer_order_ref(order) == "Est 74, Order 32064"


def test_front_page_gets_whole_ref_and_run_sheet_gets_the_estimate():
    assert daviselen_estimates("Est 74, Order 32064", "Daviselen McD 74") == (
        "Est 74, Order 32064",
        "74",
    )
    # hand-typed with a trailing space (3078)
    assert daviselen_estimates("Est 1502, Order 31904 ", "Daviselen Toyota 1502") == (
        "Est 1502, Order 31904",
        "1502",
    )


def test_empty_ref_falls_back_to_the_code_number():
    assert daviselen_estimates("", "Daviselen Toyota 1517") == ("", "1517")
    assert daviselen_estimates(None, "Daviselen McD 57") == ("", "57")


def test_is_daviselen_by_order_type_or_code():
    assert is_daviselen("daviselen", "")
    assert is_daviselen(None, "Daviselen Toyota 1514")
    assert not is_daviselen("admerasia", "Admerasia McD 19SE 2607")


def test_automation_passes_the_ref_to_the_header():
    src = open("browser_automation/daviselen_automation.py").read()
    assert 'customer_order_ref=user_input.get("customer_order_ref", "")' in src
    assert "get_default_customer_order_ref(order)" in src
