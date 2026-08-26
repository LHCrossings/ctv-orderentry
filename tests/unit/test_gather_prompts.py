"""Gather-prompt input handling.

`prompt_customer_id` exists because a prompt phrased as a question ("Use stored
customer ID '426'? [Enter=yes / type new ID]") invites the answer `y`, and
`resp if resp else stored` then stores the literal string "y". That survived the
whole gather and died much later on `int(customer_id)` inside processing — after
the operator had answered every other question. Both DART and Polaris had it.
"""

import builtins
import sys
from pathlib import Path

import pytest

_root = Path(__file__).parent.parent.parent
for _p in (_root, _root / "browser_automation"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from browser_automation.customer_defaults import prompt_customer_id  # noqa: E402


@pytest.fixture
def answers(monkeypatch):
    """Feed a scripted list of keystrokes to input()."""

    def _install(values):
        it = iter(values)
        monkeypatch.setattr(builtins, "input", lambda _p="": next(it))

    return _install


@pytest.mark.parametrize("keystroke", ["y", "Y", "yes", "YES", ""])
def test_accept_keystrokes_keep_the_stored_id(answers, keystroke):
    """Enter and y/yes all mean "keep the default" — never a literal ID."""
    answers([keystroke])
    assert prompt_customer_id("426") == "426"


def test_returned_value_always_survives_int(answers):
    """The exact failure: int('y') raised ValueError deep inside DART entry."""
    answers(["y"])
    assert int(prompt_customer_id("426")) == 426


def test_typed_id_overrides_the_default(answers):
    answers(["999"])
    assert prompt_customer_id("426") == "999"


def test_non_numeric_reprompts_rather_than_returning_junk(answers):
    """A bad ID must fail at gather time, not mid-entry."""
    answers(["abc", "77"])
    assert prompt_customer_id("426") == "77"


def test_no_default_and_no_input_is_a_cancel(answers):
    answers([""])
    assert prompt_customer_id() is None


def test_no_default_still_validates(answers):
    answers(["not-an-id", "426"])
    assert prompt_customer_id() == "426"
