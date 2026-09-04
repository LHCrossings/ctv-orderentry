"""CTV_Customers.order_type is the join key every gather uses; a mis-cased key hides the row.

Four live rows carried 'ADMERASIA', 'opAD', 'brentan' (T&T's old name) and a pasted client
name (2026-09-04); each parser silently fell back to its hardcoded defaults.
"""

import sys
from pathlib import Path

import pytest

_root = Path(__file__).parent.parent.parent
for _p in (_root, _root / "src"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from data_access.repositories.customer_repository import CustomerRepository  # noqa: E402
from domain.enums import OrderType  # noqa: E402


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("admerasia", OrderType.ADMERASIA),
        ("ADMERASIA", OrderType.ADMERASIA),
        (" opAD ", OrderType.OPAD),
        ("rwny", OrderType.RWNY),
        ("tt", OrderType.TT),
    ],
)
def test_parse_key_is_case_and_space_insensitive(raw, expected):
    assert OrderType.parse_key(raw) == expected


@pytest.mark.parametrize("raw", ["", None, "brentan", "Resorts WOrld New York", "McDonald's"])
def test_parse_key_rejects_unknown_keys_and_names(raw):
    assert OrderType.parse_key(raw) is None


def test_repository_row_with_mis_cased_key_still_loads():
    row = (
        "42",
        "McDonald's",
        "ADMERASIA",
        "McD",
        None,
        "agency",
        3,
        0,
        5,
        "McD",
        "McDonald's",
        0,
        1,
        "",
    )
    cust = CustomerRepository._row_to_customer(row)
    assert cust.order_type is OrderType.ADMERASIA
    assert (cust.separation_customer, cust.separation_order, cust.separation_event) == (3, 5, 0)
