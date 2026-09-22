"""The revision prompts must offer 'abort' and say what 'n' does (WL 216153, 2026-09-21)."""

import builtins
import re
import sys
from pathlib import Path

import pytest

_root = Path(__file__).parent.parent.parent
for _p in (_root, _root / "src", _root / "browser_automation"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import browser_automation.worldlink_automation as wl  # noqa: E402

SRC = Path(wl.__file__).read_text()


def _feed(monkeypatch, answers):
    it = iter(answers)
    monkeypatch.setattr(builtins, "input", lambda *_a, **_k: next(it))


def test_yes_and_no_return_normalised(monkeypatch):
    _feed(monkeypatch, ["Y"])
    assert wl._ask_apply("Apply?", 9) == "y"
    _feed(monkeypatch, ["no"])
    assert wl._ask_apply("Apply?", 9) == "n"


def test_abort_raises(monkeypatch):
    _feed(monkeypatch, ["abort"])
    with pytest.raises(wl.RevisionAborted):
        wl._ask_apply("Apply?", 9)
    _feed(monkeypatch, ["a"])
    with pytest.raises(wl.RevisionAborted):
        wl._ask_apply("Apply?", 9)


def test_garbage_reprompts(monkeypatch):
    _feed(monkeypatch, ["maybe", "", "y"])
    assert wl._ask_apply("Apply?", 9) == "y"


def test_no_bare_apply_prompt_remains():
    body = SRC[SRC.index("def process_worldlink_order_direct") :]
    assert not re.search(r"input\([^)]*Apply\?", body)
    assert not re.search(r"input\([^)]*Queue re-attribution", body)


def test_abort_is_caught_before_generic_except():
    body = SRC[SRC.index("def process_worldlink_order_direct") :]
    assert body.index("except RevisionAborted") < body.index("except Exception as exc")
