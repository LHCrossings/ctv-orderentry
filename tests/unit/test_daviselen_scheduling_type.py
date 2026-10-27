"""Every Daviselen line enters as Rotation (PRENOTAZIONE=1) — paid and bonus.

Lee, 2026-10-27: So Cal Toyota orders 1x/week on M-F / M-Su program lines; under
Priority the scheduler's first-fit always placed that spot on Monday. This pins the
automation's add_contract_line call site(s) to scheduling_type=DAVISELEN_SCHEDULING_TYPE
and the constant itself to Rotation.
"""

import ast
import sys
from pathlib import Path

_root = Path(__file__).parent.parent.parent
for _p in (_root, _root / "src", _root / "browser_automation"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import browser_automation.daviselen_automation as dav  # noqa: E402

_SRC = Path(dav.__file__)


def _add_line_calls():
    tree = ast.parse(_SRC.read_text(encoding="utf-8"))
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "add_contract_line"
    ]


def test_constant_is_rotation():
    assert dav.DAVISELEN_SCHEDULING_TYPE == 1


def test_every_add_contract_line_passes_rotation():
    calls = _add_line_calls()
    assert calls, "no add_contract_line call found in daviselen_automation"
    for call in calls:
        kw = {k.arg: k.value for k in call.keywords}
        assert "scheduling_type" in kw, f"line {call.lineno}: scheduling_type missing"
        val = kw["scheduling_type"]
        assert isinstance(val, ast.Name) and val.id == "DAVISELEN_SCHEDULING_TYPE", (
            f"line {call.lineno}: scheduling_type must be DAVISELEN_SCHEDULING_TYPE"
        )


def test_no_priority_literal_remains():
    assert "scheduling_type=0" not in _SRC.read_text(encoding="utf-8")
