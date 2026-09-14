"""A freshly inserted playlist row is identified by 'did not exist before', never by
(asset, ORA) alone (Ashe 2026-09-14: DAL 9/12 25:30 seated a soft-deleted twin)."""

import sys
from pathlib import Path

import pytest

for _p in (Path(__file__).resolve().parents[2], Path(__file__).resolve().parents[2] / "src"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from src.business_logic.services.daily_programming_run import _insert_event  # noqa: E402


class _Cur:
    """Scripted cursor: the watermark SELECT, the EXEC, then the identity SELECT."""

    def __init__(self, before, found):
        self.before, self.found = before, found
        self.executed: list[tuple[str, tuple | None]] = []
        self._next = None

    def execute(self, sql, params=None):
        self.executed.append((sql, params))
        if sql.startswith("SELECT ISNULL(MAX"):
            self._next = (self.before,)
        elif sql.startswith("EXEC"):
            self._next = None
        else:
            self._next = (self.found,)

    def nextset(self):
        return False

    def fetchone(self):
        return self._next


def test_returns_the_row_created_by_this_call_not_a_dead_twin():
    cur = _Cur(before=15750508, found=15758610)
    nid = _insert_event(cur, 10, "2026-09-12", 1, 2, 3, 2793520, 139940, 1798)
    assert nid == 15758610
    sql, params = cur.executed[-1]
    assert "LIVELLO=0" in sql, "a soft-deleted row of the same asset at the same ORA must not match"
    assert "ID_TPALINSE>%s" in sql and params[-1] == 15750508, "only ids newer than the watermark"
    assert "ID_FILMATI=%s" in sql and "ORA=%s" in sql and "PART=0" in sql
    assert cur.executed[1][0].startswith("EXEC Traffic_InsertEvent 0,'2026-09-12',10,1,2,3,2793520")


def test_missing_new_row_fails_loudly_instead_of_seating_nothing():
    cur = _Cur(before=15750508, found=None)
    with pytest.raises(RuntimeError, match="no new live row"):
        _insert_event(cur, 10, "2026-09-12", 1, 2, 3, 2793520, 139940, 1798)
