"""Finish's reads never lose a 1205 to Daily Programming (Maija 10/13, WDC 10/13).

SQL Server's deadlock graph (scripts/deadlock_report.py, 2026-10-13 07:51:27 PT, spid 132)
showed the Finish day list as victim: load_inventory's PI/PSA scan held S on a FILMATI row
whose last-aired subquery waited on a Daily Programming thread's uncommitted TPALINSE keys,
while that thread's _apply_filmati_sync wanted X on the same FILMATI row. Two guards: the
inventory read is dirty by design, and the read endpoints retry a 1205 like apply does."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.business_logic.services import finish_plan  # noqa: E402
from web.routes import finish  # noqa: E402


class Deadlock(Exception):
    def __init__(self):
        super().__init__(1205, b"Transaction (Process ID 132) was deadlocked ... deadlock victim")


class _Cur:
    def __init__(self):
        self.sql = None

    def execute(self, sql, params=None):
        self.sql = sql

    def fetchall(self):
        return []


def test_inventory_read_never_waits_on_daily_programming_uncommitted_rows():
    cur = _Cur()
    finish_plan.load_inventory(cur, 8, "2026-10-13")
    assert "FROM FILMATI f WITH (NOLOCK)" in cur.sql, cur.sql
    assert "FROM TPALINSE t WITH (NOLOCK)" in cur.sql, cur.sql


def test_read_retrying_retries_a_1205_then_returns_the_result():
    outcomes = iter([Deadlock(), Deadlock(), {"programs": []}])
    slept = []

    def fn():
        o = next(outcomes)
        if isinstance(o, Exception):
            raise o
        return o

    assert finish.read_retrying("day WDC 2026-10-13", fn, sleep=slept.append) == {"programs": []}
    assert len(slept) == 2 and all(0.5 < s < 2.0 for s in slept)


def test_read_retrying_gives_up_after_the_last_attempt():
    slept = []

    def fn():
        raise Deadlock()

    with pytest.raises(Deadlock):
        finish.read_retrying("day WDC 2026-10-13", fn, attempts=3, sleep=slept.append)
    assert len(slept) == 2


def test_read_retrying_never_retries_anything_but_a_deadlock():
    calls = []

    def fn():
        calls.append(1)
        raise RuntimeError("not a deadlock")

    with pytest.raises(RuntimeError):
        finish.read_retrying("plan", fn, sleep=lambda s: None)
    assert calls == [1]


def test_day_and_plan_endpoints_read_through_the_retry():
    import inspect

    src = inspect.getsource(finish.build_finish_router)
    assert src.count("read_retrying(") == 2
