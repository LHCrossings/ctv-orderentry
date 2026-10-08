"""Finish retries a SQL Server 1205 deadlock instead of showing it to the team (Lee 10/8).

Every market shares TPALINSE/trafficPalinse, so two operators finishing different markets,
the scheduler, or Exec Editor can make a Finish write the deadlock victim. The server rolls
the victim back, so a fresh attempt is safe; anything that is not a 1205 must not be retried."""

import sys
from contextlib import contextmanager
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.business_logic.services import finish_service as fs  # noqa: E402


class Deadlock(Exception):
    def __init__(self):
        super().__init__(1205, b"Transaction (Process ID 80) was deadlocked ... deadlock victim")


@contextmanager
def _conn():
    yield object()


def _connect_factory(calls):
    def connect():
        calls.append("connect")
        return _conn()

    return connect


def _run(monkeypatch, outcomes, attempts=3):
    """`outcomes`: per attempt, a dict apply_window returns or an exception it raises."""
    calls, slept, log = [], [], []
    it = iter(outcomes)

    def fake_apply(conn, market, date, lo, hi, apply, log=print, refill=False):
        calls.append("apply")
        o = next(it)
        if isinstance(o, Exception):
            raise o
        return dict(o)

    monkeypatch.setattr(fs, "apply_window", fake_apply)
    monkeypatch.setattr(fs.random, "uniform", lambda a, b: 0.5)
    r = fs.apply_window_retrying(
        _connect_factory(calls),
        4,
        "2026-10-08",
        39600.0,
        41400.0,
        True,
        log=log.append,
        attempts=attempts,
        sleep=slept.append,
    )
    return r, calls, slept, log


def test_deadlock_dict_is_retried_on_a_fresh_connection_and_succeeds(monkeypatch):
    dl = {"status": "error", "message": "(1205, ...)", "deadlock": True}
    r, calls, slept, log = _run(monkeypatch, [dl, dl, {"status": "applied"}])
    assert r["status"] == "applied" and r["attempts"] == 3
    assert calls == ["connect", "apply"] * 3  # a new connection per attempt
    assert slept == [1.5, 2.5]  # attempt + jitter: grows, never a fixed lockstep delay
    assert all("1205" in line for line in log) and len(log) == 2


def test_deadlock_raised_before_the_transaction_is_retried_too(monkeypatch):
    r, calls, slept, _ = _run(monkeypatch, [Deadlock(), {"status": "finished"}])
    assert r["status"] == "finished" and r["attempts"] == 2
    assert len(slept) == 1


def test_non_deadlock_error_is_not_retried(monkeypatch):
    bad = {"status": "error", "message": "after explode the ID airs 2.0s", "deadlock": False}
    r, calls, slept, _ = _run(monkeypatch, [bad, {"status": "applied"}])
    assert r == bad and "attempts" not in r
    assert calls == ["connect", "apply"] and slept == []


def test_non_deadlock_exception_propagates_unchanged(monkeypatch):
    with pytest.raises(RuntimeError, match="boom"):
        _run(monkeypatch, [RuntimeError("boom"), {"status": "applied"}])


def test_exhausted_deadlocks_return_an_operator_message_not_the_raw_1205(monkeypatch):
    dl = {"status": "error", "message": "(1205, b'...deadlock victim...')", "deadlock": True}
    r, calls, slept, _ = _run(monkeypatch, [dl, dl, dl])
    assert r["status"] == "error" and r["attempts"] == 3 and r["deadlock"]
    assert "3 times" in r["message"] and "click Finish again" in r["message"]
    assert len(slept) == 2  # no sleep after the last attempt


def test_success_first_time_carries_no_attempts_key(monkeypatch):
    r, calls, slept, _ = _run(monkeypatch, [{"status": "applied"}])
    assert r == {"status": "applied"} and slept == []


def test_apply_window_error_dicts_carry_the_deadlock_flag():
    """The wrapper keys on `deadlock`; both of apply_window's except paths must set it."""
    import inspect

    src = inspect.getsource(fs.apply_window)
    assert src.count('"status": "error"') == 2
    assert src.count('"deadlock": _is_deadlock(exc)') == 2


def test_route_and_cli_use_the_retrying_entry_point():
    root = Path(__file__).resolve().parents[2]
    route = (root / "src/web/routes/finish.py").read_text()
    cli = (root / "scripts/finish_apply.py").read_text()
    for text in (route, cli):
        assert "apply_window_retrying(" in text
        assert "import apply_window\n" not in text and "import apply_window " not in text
