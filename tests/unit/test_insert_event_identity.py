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


# --- 2026-09-18: Korean News NYC/HOU/SEA/WDC — a 1205 surfacing from nextset() was
# swallowed, so the guard fired with a message tagged deterministic and nothing retried.

from src.business_logic.services import daily_programming_run as dpr  # noqa: E402


class _DeadlockAtNextset(_Cur):
    def nextset(self):
        raise Exception(1205, b"Transaction (Process ID 87) was deadlocked ... deadlock victim")


class _WarningAtNextset(_Cur):
    def nextset(self):
        raise Exception(50000, b"some SP-side complaint")


def test_deadlock_raised_from_nextset_propagates_for_retry():
    cur = _DeadlockAtNextset(before=1, found=None)
    with pytest.raises(Exception) as ei:
        _insert_event(cur, 1, "2026-09-18", 33438, 13172, 94024, 863136, 149398, 16555)
    assert dpr._is_deadlock(ei.value) and dpr._is_retryable(ei.value)
    assert not any(sql.startswith("SELECT MAX(id_tpalinse)") for sql, _ in cur.executed), (
        "a deadlocked SP must not be followed by the identity lookup — the transaction is gone"
    )


def test_missing_row_is_retryable_and_names_the_swallowed_sp_error():
    cur = _WarningAtNextset(before=1, found=None)
    with pytest.raises(dpr.InsertLeftNoRow, match="SP error: .*50000") as ei:
        _insert_event(cur, 1, "2026-09-18", 33438, 13172, 94024, 863136, 149398, 16555)
    assert dpr._is_retryable(ei.value) and not dpr._is_deadlock(ei.value)


def test_run_market_retries_a_lost_insert_then_tags_it_for_the_solo_pass(monkeypatch):
    calls = []

    def fake_place_once(conn, cod_user, d, assignment, pending):
        calls.append(1)
        exc = dpr.InsertLeftNoRow("left no new live row")
        return {
            "cu": cod_user,
            "ok": False,
            "skipped": False,
            "message": f"error: {exc}",
            "_deadlock": dpr._is_retryable(exc),
        }

    monkeypatch.setattr(dpr, "_place_once", fake_place_once)
    monkeypatch.setattr(dpr.time, "sleep", lambda s: None)
    res = dpr.run_market(None, 1, "2026-09-18", {}, [])
    assert len(calls) == dpr._DEADLOCK_MAX_ATTEMPTS
    assert res["_deadlock"] is True and res["ok"] is False


def test_every_per_market_except_block_tags_retryable():
    import inspect

    src = inspect.getsource(dpr)
    assert '"_deadlock": _is_deadlock(exc)' not in src
    assert src.count('"_deadlock": _is_retryable(exc)') >= 5
