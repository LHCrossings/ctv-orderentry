"""
Every `_process_*_order` handler calls its automation with keyword arguments the
automation actually accepts.

opAD 3180 (2026-10-15): the handler passed `user_input=` to an automation whose
parameter had been renamed `pre_gathered_inputs` in the direct-DB conversion,
so the whole gather ran clean and entry died on a TypeError. Intertrend had the
same drift. A gather that succeeds is not evidence the handler agrees (Crispin
lesson); this test reads every call site against `inspect.signature`.
"""

import ast
import importlib
import inspect
import sys
from pathlib import Path

_root = Path(__file__).parent.parent.parent
for p in [str(_root), str(_root / "src"), str(_root / "browser_automation")]:
    if p not in sys.path:
        sys.path.insert(0, p)

SERVICE = _root / "src" / "business_logic" / "services" / "order_processing_service.py"


def _drifts():
    tree = ast.parse(SERVICE.read_text())
    out = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.FunctionDef) and node.name.startswith("_process_")):
            continue
        imports = {}
        for n in ast.walk(node):
            if isinstance(n, ast.ImportFrom):
                for a in n.names:
                    imports[a.asname or a.name] = (n.module, a.name)
        for n in ast.walk(node):
            if not (isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in imports):
                continue
            mod, fn = imports[n.func.id]
            try:
                sig = inspect.signature(getattr(importlib.import_module(mod), fn))
            except Exception as exc:  # pragma: no cover — an import error is its own finding
                out.append(f"{node.name}: cannot import {mod}.{fn}: {exc}")
                continue
            params = sig.parameters
            if any(p.kind == p.VAR_KEYWORD for p in params.values()):
                continue
            bad = [k.arg for k in n.keywords if k.arg and k.arg not in params]
            if bad:
                out.append(f"{node.name} line {n.lineno}: {fn}(...{bad}...) not in {sig}")
    return out


def test_every_handler_keyword_exists_on_its_automation():
    assert _drifts() == []


def test_the_sweep_sees_a_drift(tmp_path, monkeypatch):
    """The sweep must FAIL on the shape it was written for."""
    import tests.unit.test_handler_kwargs_match_automations as me

    fake = tmp_path / "svc.py"
    fake.write_text(
        "def _process_x_order(self, order):\n"
        "    from browser_automation.opad_automation import process_opad_order\n"
        "    return process_opad_order(pdf_path='x', user_input={})\n"
    )
    monkeypatch.setattr(me, "SERVICE", fake)
    assert any("user_input" in d for d in me._drifts())
