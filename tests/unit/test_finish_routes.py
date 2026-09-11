"""Fill & Finish endpoints answer JSON on failure (Ashe 9/11: bare "Internal Server
Error" text reached the page as an unparseable body and the trace was lost)."""

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from fastapi import HTTPException  # noqa: E402
from fastapi.responses import JSONResponse  # noqa: E402

from web.routes import finish  # noqa: E402


def test_run_json_returns_result_when_work_succeeds():
    assert asyncio.run(finish.run_json("x", lambda: {"ok": 1})) == {"ok": 1}


def test_run_json_turns_exception_into_json_500_with_message(tmp_path, monkeypatch):
    monkeypatch.setattr(finish, "_ERROR_LOG", tmp_path / "server-errors.log")

    def boom():
        raise RuntimeError("deadlock victim 1205")

    resp = asyncio.run(finish.run_json("apply DAL 2026-09-10", boom))
    assert isinstance(resp, JSONResponse) and resp.status_code == 500
    body = json.loads(resp.body)
    assert body["status"] == "error"
    assert body["message"] == "RuntimeError: deadlock victim 1205"
    # the traceback is kept on disk for the next "why did that 500?" question
    text = (tmp_path / "server-errors.log").read_text()
    assert "apply DAL 2026-09-10" in text and "deadlock victim 1205" in text and "Traceback" in text


def test_run_json_lets_http_exceptions_through():
    def denied():
        raise HTTPException(status_code=400, detail="unknown market")

    try:
        asyncio.run(finish.run_json("x", denied))
    except HTTPException as exc:
        assert exc.status_code == 400
    else:
        raise AssertionError("HTTPException must propagate unchanged")
