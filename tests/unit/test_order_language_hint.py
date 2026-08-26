"""The language-catalog prompt falls back to the language the parser read off the IO.

Admerasia contract 3009: six prompts, every one showing "[?]", because its line
descriptions are pure dayparts ("W 11:30a-12:00p") and `guess_language` scans the
description for a language word. The order object knew it was Vietnamese all along.
"""

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

_root = Path(__file__).parent.parent.parent
for _p in (_root, _root / "src", _root / "browser_automation"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from browser_automation.line_language import guess_language  # noqa: E402
from business_logic.services.order_processing_service import (  # noqa: E402
    _order_language_name,
)


def _order(order_input):
    return SimpleNamespace(order_input=order_input)


def test_language_read_from_the_parsed_order_object():
    """Admerasia/DART gathers return the parsed order under inputs['order']."""
    orders = [_order({"order": SimpleNamespace(language="Vietnamese")})]
    assert _order_language_name(orders) == "Vietnamese"


def test_language_read_from_a_flat_inputs_key():
    orders = [_order({"language": "Korean"})]
    assert _order_language_name(orders) == "Korean"


@pytest.mark.parametrize(
    "orders",
    [
        [],
        [_order(None)],
        [_order({})],
        [_order({"order": SimpleNamespace()})],
        [_order({"order": None, "language": ""})],
    ],
)
def test_absent_language_is_empty_not_an_error(orders):
    """Most parsers have no language field — the pass must degrade, never raise."""
    assert _order_language_name(orders) == ""


def test_non_string_language_is_ignored():
    orders = [_order({"order": SimpleNamespace(language=42)})]
    assert _order_language_name(orders) == ""


def test_vietnamese_io_yields_the_v_code():
    """End of the chain: the hint has to survive guess_language() to be useful."""
    name = _order_language_name([_order({"order": SimpleNamespace(language="Vietnamese")})])
    assert guess_language(name) == "V"


def test_chinese_io_maps_to_the_aggregate_that_the_caller_suppresses():
    """A Chinese IO's lines are individually Mandarin or Cantonese (the daypart
    decides), so the catalog deliberately offers no guess rather than M/C — a wrong
    suggestion that Enter would accept is worse than "[?]"."""
    name = _order_language_name([_order({"order": SimpleNamespace(language="Chinese")})])
    assert name == "Chinese"
    assert guess_language(name) == "M/C"


def test_description_guess_takes_precedence_over_the_order_language():
    """The catalog computes `guess_language(desc) or order_guess`. A line that names
    its own language must never be flattened to the IO header's."""
    order_guess = guess_language("Chinese")
    assert guess_language("M-F 7-8p Cantonese") or order_guess == "C"
    assert guess_language("M-F 8-9p Mandarin") or order_guess == "M"
    # ...and a daypart-only description falls through to the order language.
    assert (guess_language("W 11:30a-12:00p") or "V") == "V"
