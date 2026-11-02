"""The Admerasia traffic reader covers page 1 only (grid geometry, cell colours, text
and vision legends all read ``pages[0]``); every Admerasia IO on hand is one page.
A longer IO must refuse by name, never match page 1 and drop the rest silently
(the Tatari pages[0] lesson, 2026-11-02)."""

import sys
from pathlib import Path

_root = Path(__file__).resolve().parents[2]
for _p in (_root, _root / "browser_automation"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from browser_automation.parsers import admerasia_traffic as at  # noqa: E402


def test_multipage_io_refuses_before_any_reader_runs(monkeypatch):
    monkeypatch.setattr(at, "_page_count", lambda path: 2)

    def _boom(*a, **k):
        raise AssertionError("page-1 reader ran on a multi-page IO")

    monkeypatch.setattr(at, "_io_header", _boom)
    res = at.resolve_traffic("/nonexistent.pdf", cur=None)
    assert res.ok is False and res.contract_id is None and res.assignments == []
    assert any("2 pages" in w and "page 1 only" in w for w in res.warnings)
