"""Ingest / Datamover stall watchdog — a file dropped into WIP that is not moving.

2026-10-29 (Lee: "the Datamover keeps acting up, files not uploaded to S3; the only way we
know is the crash popup when we close it"). EtereMM9.exe on the Datamover dies on an unhandled
Delphi exception and limps on in a broken state; while it does, the Etere workflow engine keeps
creating jobs for it that nobody picks up. Stalls seen 10/11, 10/12 (14 h), 10/21 (Korean News,
9 h), 10/23 (95 ingests, 6 h), 10/28 (65 min). Nothing alarmed: the transfers that DID run were
fine, the ones that never started left no error anywhere.

The live view is `wf_job`: one row per OPEN step of every active workflow instance (the row is
deleted when the step completes). A healthy "1.0 Initial Import" (wf_instanceclass 12) is
Start -> Search and set File (SQL, instant) -> Mediainfo (Datamover, seconds) -> aspect/framerate
checks (SQL) -> WIP -> AWS S3 upload (Datamover, seconds; 6.5 GB in 19 s) -> Wait [01:00:00]
-> FileDelete on WIP (EtereRC) -> End. The only legitimately long step is the Wait; every other
step waited under a minute in the five days before this was written, except on the 10/28 crash
morning (65 min). The 06:00 restore burst ("Restore to CIBn", classes 18-21/30) queues the
Datamover's S3 downloads for up to 13 min, so restores get a longer threshold and only their
Datamover steps count. "Delete on CIBn" workflows are housekeeping and are ignored.

Rules (thresholds in minutes, measured on the SQL Server clock):
  * stall    — an Initial Import step other than a Wait open longer than INGEST_STALL_MIN, or a
               restore's Datamover (mm-*) step open longer than RESTORE_STALL_MIN.
  * looping  — an instance restarted LOOP_RESTARTS or more times: the SQL Agent aborts a stuck
               instance after 20 h and restarts it, so a step that can never complete loops for
               months (V-TOPNEWS071725C 563 restarts, HMONG-REPORT031625F/G 712; six such
               instances were aborted 10/29 with scripts/abort_looping_workflow_instances.py).
               They are counted so someone cleans them up, and never alarm. The "Delete on
               CIBn" instances that wait a day and re-check for weeks are NOT loopers: that
               wait-check-wait cycle is the design, and they sit in a Wait step anyway.

Shared by `scripts/check_ingest_stalls.py` (CLI) and the Broadcast Health status task
(`src/web/routes/broadcast_health.py`), which polls every few minutes and shows a stall in the
site header with the restart runbook: close Datamover, Etere Ingest and Etere Active Sync on the
Datamover, sign out of that session and sign back in (10/23 showed restarting MM9 alone is not
enough; EtereRC had to go too).
"""

from __future__ import annotations

import datetime as dt

INGEST_CLASS = 12
RESTORE_CLASSES = frozenset({18, 19, 20, 21, 30})
INGEST_STALL_MIN = 20
RESTORE_STALL_MIN = 45
LOOP_RESTARTS = 5
MAX_ROWS = 100

# Who runs a step, by its action-class ucode prefix (wf_log.program_name over 5 days: mm-* =
# EtereMM9.exe as usrdm1, fsdel = EtereRC.exe, a.wks = EtereActivesync/etalign, the rest = the
# SQL service broker, which never waits).
_WORKERS = (
    ("mm-", "Datamover (EtereMM9)"),
    ("fsdel", "Etere RC (EtereRC)"),
    ("a.wke", "Etere RC (EtereRC)"),
    ("a.wks", "Active Sync (EtereActivesync)"),
)
_SKIP_PREFIXES = ("dlay", "sch-")  # Wait steps and scheduled deletes: long by design


def worker_for(action_ucode: str) -> str:
    u = (action_ucode or "").lower()
    for prefix, name in _WORKERS:
        if u.startswith(prefix):
            return name
    return "SQL workflow engine"


def classify(
    jobs: list[dict],
    ingest_min: int = INGEST_STALL_MIN,
    restore_min: int = RESTORE_STALL_MIN,
    loop_restarts: int = LOOP_RESTARTS,
) -> tuple[list[dict], list[dict]]:
    """Split open jobs into (stalls, looping). Each job: id_instanceclass, start_cnt,
    action_ucode, minutes (open for), plus whatever display fields the caller carries."""
    stalls: list[dict] = []
    looping: list[dict] = []
    for j in jobs:
        ucode = (j.get("action_ucode") or "").lower()
        if ucode.startswith(_SKIP_PREFIXES):
            continue
        if int(j.get("start_cnt") or 0) >= loop_restarts:
            looping.append(j)
            continue
        cls = int(j.get("id_instanceclass") or 0)
        minutes = float(j.get("minutes") or 0)
        if cls == INGEST_CLASS:
            if minutes >= ingest_min:
                stalls.append(dict(j, worker=worker_for(ucode), threshold=ingest_min))
        elif cls in RESTORE_CLASSES and ucode.startswith("mm-"):
            if minutes >= restore_min:
                stalls.append(dict(j, worker=worker_for(ucode), threshold=restore_min))
    stalls.sort(key=lambda s: -s["minutes"])
    return stalls, looping


_SQL = """
SELECT j.id_job, j.id_instance, i.id_instanceclass, RTRIM(c.friendly_name), i.start_cnt,
       ac.ucode, RTRIM(a.friendly_name), j.id_state, j.creation_time,
       DATEDIFF(second, j.creation_time, GETDATE()) / 60.0,
       i.foreign_key, RTRIM(f.COD_PROGRA), LEFT(j.message, 80)
FROM wf_job j
JOIN wf_instance i ON i.id_instance = j.id_instance
JOIN wf_instanceclass c ON c.id_instanceclass = i.id_instanceclass
LEFT JOIN wf_action a ON a.id_action = j.id_action
LEFT JOIN wf_actionclass ac ON ac.id_actionclass = a.id_actionclass
LEFT JOIN FILMATI f ON f.ID_FILMATI = i.foreign_key
WHERE i.activity_state = 6 AND j.delay_time IS NULL
ORDER BY j.creation_time
"""


def open_jobs(conn) -> list[dict]:
    """Every open non-Wait step of an active workflow instance. Read-only."""
    cur = conn.cursor()
    cur.execute(_SQL)
    out = []
    for (
        id_job,
        id_instance,
        cls,
        cls_name,
        start_cnt,
        ucode,
        step,
        id_state,
        created,
        minutes,
        fid,
        code,
        message,
    ) in cur.fetchall():
        out.append(
            {
                "id_job": int(id_job),
                "id_instance": int(id_instance),
                "id_instanceclass": int(cls),
                "workflow": cls_name or "",
                "start_cnt": int(start_cnt or 0),
                "action_ucode": ucode or "",
                "step": step or ucode or "?",
                "id_state": int(id_state or 0),
                "since": created.isoformat(timespec="seconds") if created else None,
                "minutes": round(float(minutes or 0), 1),
                "id_filmati": int(fid) if fid is not None else None,
                "code": code or "",
                "message": (message or "").strip(),
            }
        )
    return out


def scan(conn) -> dict:
    jobs = open_jobs(conn)
    stalls, looping = classify(jobs)
    workers = sorted({s["worker"] for s in stalls})
    return {
        "state": "alert" if stalls else "ok",
        "checked_at": dt.datetime.now().isoformat(timespec="seconds"),
        "thresholds": {"ingest_min": INGEST_STALL_MIN, "restore_min": RESTORE_STALL_MIN},
        "count": len(stalls),
        "oldest_min": max((s["minutes"] for s in stalls), default=0),
        "workers": workers,
        "stalls": stalls[:MAX_ROWS],
        "looping": {
            "count": len(looping),
            "codes": sorted({j["code"] for j in looping if j["code"]})[:20],
        },
        "open_jobs": len(jobs),
    }


RUNBOOK = (
    "On the Datamover: close Datamover, Etere Ingest and Etere Active Sync, sign out of the "
    "session, sign back in so all three start fresh (restarting MM9 alone did not clear 10/23)."
)


def format_report(result: dict) -> str:
    lines = []
    if not result["count"]:
        lines.append(
            f"✓ No ingest or restore step stalled ({result['open_jobs']} open steps, "
            f"ingest > {result['thresholds']['ingest_min']} min / restore Datamover step > "
            f"{result['thresholds']['restore_min']} min would alarm)."
        )
    else:
        lines.append(
            f"⚠ {result['count']} stalled step(s) — oldest {result['oldest_min']:.0f} min, "
            f"waiting on {', '.join(result['workers'])}:"
        )
        for s in result["stalls"]:
            lines.append(
                f"  {s['minutes']:6.0f} min  {s['code'] or '?':30s} {s['workflow']}: {s['step']}"
                f"  (since {s['since']}, instance {s['id_instance']}"
                + (f", {s['message']}" if s["message"] else "")
                + ")"
            )
        lines.append(f"  {RUNBOOK}")
    lp = result.get("looping") or {}
    if lp.get("count"):
        lines.append(
            f"  note: {lp['count']} instance(s) in an abort/restart loop (not alarming): "
            + ", ".join(lp.get("codes") or [])
        )
    return "\n".join(lines)
