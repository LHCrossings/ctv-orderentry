"""S3 Cleanup page (/traffic/s3-cleanup): expired program assets that still hold a file
in S3, by category, with Etere's "Delete on -> AWS S3" done in batches.

The work lives in ``business_logic.services.s3_media_purge``; this module only serves the
page, runs the scan off the event loop, and streams the apply log line by line. The apply
re-derives its rows live and acts only on the ids that still fall in the requested
category, so a stale page cannot delete something the scan did not show.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
import queue
import threading
from pathlib import Path

from fastapi import APIRouter, Body, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.templating import Jinja2Templates

from business_logic.services import s3_media_purge as smp

logger = logging.getLogger(__name__)
_ROOT = Path(__file__).resolve().parents[3]
RESTORE_DIR = _ROOT / "logs" / "s3-purge"


def _connect():
    from browser_automation.etere_direct_client import connect

    return connect()


def _s3_client():
    from web.routes.assets import _BUCKET, _client

    return _client(), _BUCKET


def _parse_range(d_from: str | None, d_to: str | None) -> tuple[dt.date | None, dt.date | None]:
    try:
        lo = dt.date.fromisoformat(d_from) if d_from else None
        hi = dt.date.fromisoformat(d_to) if d_to else None
    except ValueError as exc:
        raise HTTPException(400, "dates must be YYYY-MM-DD") from exc
    if lo and hi and lo > hi:
        raise HTTPException(400, "'from' is after 'to'")
    return lo, hi


def _apply_sync(
    category: str,
    ids: list[int],
    today: dt.date,
    log,
    aged_range=(None, None),
    include_short_form: bool = False,
) -> int:
    """Blocking body of the apply: re-scan the requested ids, act on those still in
    ``category``, verify from a fresh connection. Returns the exit code."""
    conn = _connect()
    try:
        s3, bucket = _s3_client()
        wanted = set(ids)
        if category == "unexpired":
            rows = [
                r
                for r in smp.fetch_unexpired(conn, today, include_short_form)
                if int(r["id_filmati"]) in wanted
            ]
            todo = smp.categorize_unexpired(rows)["unexpired"]
        elif category == "aged":
            lo, hi = aged_range
            rows = [
                r
                for r in smp.fetch_aged(conn, today, lo, hi, include_short_form)
                if int(r["id_metafile"]) in wanted
            ]
            sizes = smp.object_sizes(s3, bucket, (r.get("file_name") for r in rows))
            todo = smp.categorize_aged(rows, sizes)["aged"]
        else:
            # "dangling" holds dead S3 references from BOTH sweeps: expired assets and, when
            # the page scanned an old-programming range, not-yet-expired ones whose object is
            # already gone (Maija 10/1: 1,178 selected, 0 acted on, because only the expired
            # query was re-run here). Re-derive from the same sources the scan used.
            rows = [
                r
                for r in smp.fetch_expired(conn, today, include_short_form)
                if int(r["id_metafile"]) in wanted
            ]
            lo, hi = aged_range
            if hi:
                seen = {int(r["id_metafile"]) for r in rows}
                rows += [
                    r
                    for r in smp.fetch_aged(conn, today, lo, hi, include_short_form)
                    if int(r["id_metafile"]) in wanted and int(r["id_metafile"]) not in seen
                ]
            sizes = smp.object_sizes(s3, bucket, (r.get("file_name") for r in rows))
            todo = smp.categorize(rows, sizes)[category]
        skipped = len(wanted) - len(todo)
        log(
            f"[INFO] {len(todo)} of {len(wanted)} selected item(s) still in category '{category}'"
            + (" (short-form INCLUDED)" if include_short_form else "")
            + (f"; {skipped} skipped (changed since the scan)" if skipped else "")
        )
        if not todo:
            log("[DONE] nothing to do")
            return 0
        if category in ("delete", "aged"):
            res = smp.purge(
                conn, s3, bucket, todo, apply=True, restore_dir=RESTORE_DIR, log=log, today=today
            )
            log(
                f"[INFO] deleted {res['deleted_s3']} object(s), removed {res['deleted_db']} reference(s), "
                f"{res['bytes'] / 1e9:,.1f} GB; {res['expired_stamped']} asset(s) newly stamped expired"
            )
            for e in res["s3_errors"]:
                log(f"[WARN] S3 error: {e}")
            failed = {e.split(":")[0] for e in res["s3_errors"]}
            done = [r for r in todo if r["file_name"] not in failed]
        elif category == "dangling":
            res = smp.remove_references(
                conn, todo, apply=True, restore_dir=RESTORE_DIR, log=log, today=today
            )
            log(
                f"[INFO] removed {res['deleted_db']} dead reference(s); {res['expired_stamped']} asset(s) newly stamped expired"
            )
            done = todo
        else:
            res = smp.mark_expired(
                conn, todo, apply=True, restore_dir=RESTORE_DIR, log=log, today=today
            )
            log(f"[INFO] {res['expired_stamped']} asset(s) stamped expired as of {today:%Y-%m-%d}")
            done = todo
        conn.close()
        v = smp.verify(_connect(), s3, bucket, done)
        left = sum(v["fs_rows_left"].values())
        if v["ok"]:
            log(
                f"[DONE] verified from a fresh connection: 0 rows left, 0 objects left (restore: {res['restore']})"
            )
            return 0
        log(f"[WARN] verify: {left} FS row(s) left, {len(v['objects_left'])} object(s) still in S3")
        for k in v["objects_left"][:10]:
            log(f"[WARN] still in S3: {k}")
        return 2
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass


def build_s3_cleanup_router(templates: Jinja2Templates) -> APIRouter:
    router = APIRouter()

    @router.get("/traffic/s3-cleanup", response_class=HTMLResponse)
    async def page(request: Request):
        return templates.TemplateResponse(request, "traffic/s3_cleanup.html")

    @router.get("/api/traffic/s3-cleanup/scan")
    async def scan(
        aged_from: str | None = Query(None),
        aged_to: str | None = Query(None),
        short_form: str | None = Query(None),
    ):
        lo, hi = _parse_range(aged_from, aged_to)
        include_short_form = short_form in ("1", "true", "yes")

        def _run():
            conn = _connect()
            try:
                s3, bucket = _s3_client()
                return smp.scan(
                    conn,
                    s3,
                    bucket,
                    dt.date.today(),
                    aged_from=lo,
                    aged_to=hi,
                    include_short_form=include_short_form,
                )
            finally:
                conn.close()

        try:
            return JSONResponse(await asyncio.get_running_loop().run_in_executor(None, _run))
        except Exception as exc:  # noqa: BLE001 - the operator needs the message
            logger.exception("s3 cleanup scan failed")
            raise HTTPException(500, f"{type(exc).__name__}: {exc}"[:600]) from exc

    @router.post("/api/traffic/s3-cleanup/apply")
    async def apply(payload: dict = Body(...)):
        category = payload.get("category")
        if category not in smp.ACTIONABLE:
            raise HTTPException(400, f"category must be one of {list(smp.ACTIONABLE)}")
        try:
            ids = sorted({int(i) for i in payload.get("ids") or []})
        except (TypeError, ValueError) as exc:
            raise HTTPException(400, "ids must be integers") from exc
        if not ids:
            raise HTTPException(400, "no files selected")
        aged_range = _parse_range(payload.get("aged_from"), payload.get("aged_to"))
        include_short_form = bool(payload.get("short_form"))
        if category == "aged" and not aged_range[1]:
            raise HTTPException(400, "the old-programming action needs its 'to' date")

        q: queue.Queue[str | None] = queue.Queue()
        today = dt.date.today()

        def worker():
            try:
                code = _apply_sync(category, ids, today, q.put, aged_range, include_short_form)
            except Exception as exc:  # noqa: BLE001
                logger.exception("s3 cleanup apply failed")
                q.put(f"[ERROR] {type(exc).__name__}: {exc}"[:600])
                code = 1
            q.put(f"[EXIT:{code}]")
            q.put(None)

        threading.Thread(target=worker, daemon=True).start()

        async def stream():
            loop = asyncio.get_running_loop()
            while True:
                line = await loop.run_in_executor(None, q.get)
                if line is None:
                    break
                yield line + "\n"

        return StreamingResponse(
            stream(),
            media_type="text/plain; charset=utf-8",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    return router
