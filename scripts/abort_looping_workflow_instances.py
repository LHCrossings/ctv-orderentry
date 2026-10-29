"""Abort the workflow instances stuck in the SQL Agent's abort/restart loop (Lee, 2026-10-29).

A looping instance is an ACTIVE workflow instance whose open step is not a Wait, has sat
for over an hour, and which the SQL Agent has already restarted LOOP_RESTARTS or more times
(it aborts any instance running past 1201 min with `wf_runtime_instanceAbort` and restarts
it in the same step; a step that can never complete — "Video file not found" on an asset
with no file, "Too many metafile selected" on a duplicate CIB copy — then loops forever).
On 10/29 that was six instances: Initial Import for HMONG-REPORT031625F/G (712 restarts),
V-TOPNEWS071725C (563, plus its Delete on CIB1 at 562), DAAITV053 (111), and Delete on
CIB3 for MCIM032324VH (750).

NOT touched: the "Delete on CIBn" instances in their Wait [1d] — that loop is the design
(wait a day, check the asset is still scheduled on that CIB's markets, wait again, delete
the file once it is not); hundreds complete every day.

The abort is Etere's own: `exec wf_runtime_instanceAbort @id_instance` (the same call the
Agent logs as w.abort). Nothing is deleted: the instance goes to activity_state 8 (abort)
and stays there, and can be restarted from the Workflow Manager if anyone wants it back.

    uv run python scripts/abort_looping_workflow_instances.py            # list (read-only)
    uv run python scripts/abort_looping_workflow_instances.py --rehearse # abort in a txn, verify, ROLLBACK
    uv run python scripts/abort_looping_workflow_instances.py --apply    # abort, verify, COMMIT, re-read

A JSON backup of the wf_instance + wf_job rows is written to logs/ before any write.
"""

import datetime as dt
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from browser_automation.etere_direct_client import connect
from business_logic.services.ingest_watchdog import LOOP_RESTARTS, open_jobs

MIN_OPEN_MINUTES = 60
ACTIVE, ABORTED = 6, 8


def targets(conn) -> list[dict]:
    return [
        j
        for j in open_jobs(conn)
        if j["start_cnt"] >= LOOP_RESTARTS and j["minutes"] >= MIN_OPEN_MINUTES
    ]


def _state(cur, ids: list[int]) -> dict[int, tuple[int, int]]:
    """{id_instance: (activity_state, open wf_job rows)}."""
    out = {}
    for i in ids:
        cur.execute(
            "SELECT i.activity_state, (SELECT COUNT(*) FROM wf_job j WHERE j.id_instance = i.id_instance)"
            " FROM wf_instance i WHERE i.id_instance = %s",
            (i,),
        )
        row = cur.fetchone()
        out[i] = (int(row[0]), int(row[1])) if row else (None, None)
    return out


def _backup(conn, ids: list[int]) -> Path:
    cur = conn.cursor()
    dump = {
        "written": dt.datetime.now().isoformat(timespec="seconds"),
        "wf_instance": [],
        "wf_job": [],
    }
    for table in ("wf_instance", "wf_job"):
        cur.execute(
            f"SELECT * FROM {table} WHERE id_instance IN ({','.join('%s' for _ in ids)})",
            tuple(ids),
        )
        cols = [d[0] for d in cur.description]
        for row in cur.fetchall():
            dump[table].append(
                {c: (v.isoformat() if hasattr(v, "isoformat") else v) for c, v in zip(cols, row)}
            )
    path = ROOT / "logs" / f"wf-looping-instances-backup-{dt.date.today():%Y%m%d}.json"
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(dump, indent=1, default=str), encoding="utf-8")
    return path


def main():
    mode = "apply" if "--apply" in sys.argv else "rehearse" if "--rehearse" in sys.argv else "list"
    with connect() as conn:
        rows = targets(conn)
        print(
            f"{len(rows)} looping instance(s) (open non-Wait step >= {MIN_OPEN_MINUTES} min, restarted >= {LOOP_RESTARTS}x):"
        )
        for j in rows:
            print(
                f"  instance {j['id_instance']:>8}  x{j['start_cnt']:<4} {j['code'] or '?':24s} "
                f"{j['workflow']}: {j['step']}  open {j['minutes']:.0f} min"
                + (f"  [{j['message']}]" if j["message"] else "")
            )
        if mode == "list" or not rows:
            return
        ids = [j["id_instance"] for j in rows]
        bk = _backup(conn, ids)
        print(f"backup: {bk}")

        cur = conn.cursor()
        before = _state(cur, ids)
        assert all(s == ACTIVE for s, _ in before.values()), before
        for i in ids:
            cur.execute("EXEC wf_runtime_instanceAbort @id_instance = %s", (i,))
            try:
                while cur.nextset():
                    pass
            except Exception as exc:  # noqa: BLE001 — a late server error must surface, never hide
                raise RuntimeError(f"instanceAbort {i}: {exc}") from exc
        after = _state(cur, ids)
        bad = {i: s for i, s in after.items() if s[0] != ABORTED or s[1] != 0}
        if bad:
            conn.rollback()
            print(
                f"ROLLED BACK — not every instance reached state {ABORTED} with no open job: {bad}"
            )
            sys.exit(2)
        print(f"in transaction: all {len(ids)} instances now activity_state {ABORTED}, 0 open jobs")
        if mode == "rehearse":
            conn.rollback()
            print("REHEARSAL — rolled back, nothing changed")
            return
        conn.commit()
        print("COMMITTED")

    # Readback from a fresh connection: the only proof the write persisted.
    with connect() as conn2:
        cur2 = conn2.cursor()
        final = _state(cur2, ids)
        left = targets(conn2)
        print("fresh-connection readback:", {i: s for i, s in final.items()})
        print(f"looping instances left: {len(left)}")
        sys.exit(0 if all(s[0] == ABORTED for s in final.values()) and not left else 3)


if __name__ == "__main__":
    main()
