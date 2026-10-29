"""Remove the phantom "<code>: <description>" file records from Etere's file catalog.

2026-10-29, Lee: the Delete on CIB3 workflow for MCIM032324VH had looped 750 times on
"Too many metafile selected" because the asset carried TWO CIB3 records: the real
`MD30M310.mp4` and a zero-byte record named `MD30M310: ACM More Than You Expect 30 MAN.mp4`
— a name Windows cannot even create (the colon), so nothing exists on disk. Fifty more such
records sit on CIB3/4/5/6, ids in the January-February 2025 block (the migration), each
beside a real copy of the same asset. They are inert until a workflow has to pick a file on
that device. Clearing one by hand in Etere was painful: the S3 copy has two FS_FILE rows under
one metafile, so removing "one link" removed the metafile and both links, and the file had to
be re-uploaded.

FS_FILMATI is a view: FS_METAFILE (one row per copy) → FS_XFILEFILM (link to the asset) →
FS_FILE (physical file rows). A phantom is its OWN metafile, so Etere's own
`fs_metafile_delete @id_metafile` removes exactly it — metafile, link and file row — and
should leave the real copies, the asset and the playlist untouched. The procedure is
encrypted, so `--rehearse` PROVES that inside a transaction (every phantom's metafile, link
and file row gone; each asset's real-copy count unchanged; FS_METAFILE shrunk by exactly the
target count) and rolls back; anything else rolls back and exits 2. Run --rehearse before --apply.

Target = a file record whose FILE_ID contains a colon and whose physical size is 0 or NULL,
on an asset that still has at least one real (colon-free) record. Never anything else.

    uv run python scripts/delete_phantom_file_records.py            # list (read-only)
    uv run python scripts/delete_phantom_file_records.py --rehearse # delete in a txn, verify, ROLLBACK
    uv run python scripts/delete_phantom_file_records.py --apply    # delete, verify, COMMIT, re-read

A JSON backup of the FS_METAFILE / FS_XFILEFILM / FS_FILE rows is written to logs/ first.
"""

import datetime as dt
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from browser_automation.etere_direct_client import connect

_TARGETS = """
SELECT ff.ID_METAFILE, ff.ID_METADEVICE, RTRIM(d.DESCRIPTION), ff.ID_FILMATI, RTRIM(ff.COD_PROGRA),
       ff.FILE_ID, ff.PHYSICAL_FILENAME, ISNULL(ff.PHYSICAL_SIZE, 0),
       (SELECT COUNT(*) FROM FS_FILMATI g WHERE g.ID_FILMATI = ff.ID_FILMATI AND g.FILE_ID NOT LIKE '%:%')
FROM FS_FILMATI ff JOIN FS_METADEVICE d ON d.ID_METADEVICE = ff.ID_METADEVICE
WHERE ff.FILE_ID LIKE '%:%' AND ISNULL(ff.PHYSICAL_SIZE, 0) = 0
ORDER BY ff.ID_METADEVICE, ff.COD_PROGRA
"""


def targets(conn) -> list[dict]:
    cur = conn.cursor()
    cur.execute(_TARGETS)
    seen, out = set(), []
    for mid, dev, devname, fid, code, file_id, fname, size, real in cur.fetchall():
        if mid in seen:
            continue  # the view repeats a metafile once per FS_FILE row
        seen.add(mid)
        out.append(
            {
                "id_metafile": int(mid),
                "device": int(dev),
                "device_name": devname,
                "id_filmati": int(fid),
                "code": code or "",
                "file_id": file_id,
                "filename": fname,
                "size": int(size),
                "real_copies": int(real),
            }
        )
    return [t for t in out if t["real_copies"] > 0]


def _counts(cur, mids: list[int]) -> dict[int, tuple[int, int, int]]:
    out = {}
    for m in mids:
        cur.execute(
            "SELECT (SELECT COUNT(*) FROM FS_METAFILE WHERE ID_METAFILE=%s),"
            " (SELECT COUNT(*) FROM FS_XFILEFILM WHERE ID_METAFILE=%s),"
            " (SELECT COUNT(*) FROM FS_FILE WHERE ID_METAFILE=%s)",
            (m, m, m),
        )
        out[m] = tuple(int(x) for x in cur.fetchone())
    return out


def _real_copy_counts(cur, fids: list[int]) -> dict[int, int]:
    out = {}
    for f in sorted(set(fids)):
        cur.execute(
            "SELECT COUNT(DISTINCT ID_METAFILE) FROM FS_FILMATI WHERE ID_FILMATI=%s AND FILE_ID NOT LIKE '%%:%%'",
            (f,),
        )
        out[f] = int(cur.fetchone()[0])
    return out


def _backup(conn, mids: list[int]) -> Path:
    cur = conn.cursor()
    dump = {"written": dt.datetime.now().isoformat(timespec="seconds")}
    ph = ",".join("%s" for _ in mids)
    for table in ("FS_METAFILE", "FS_XFILEFILM", "FS_FILE"):
        cur.execute(f"SELECT * FROM {table} WHERE ID_METAFILE IN ({ph})", tuple(mids))
        cols = [d[0] for d in cur.description]
        dump[table] = [
            {c: (v.isoformat() if hasattr(v, "isoformat") else v) for c, v in zip(cols, row)}
            for row in cur.fetchall()
        ]
    path = ROOT / "logs" / f"phantom-file-records-backup-{dt.date.today():%Y%m%d}.json"
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(dump, indent=1, default=str), encoding="utf-8")
    return path


def main():
    mode = "apply" if "--apply" in sys.argv else "rehearse" if "--rehearse" in sys.argv else "list"
    with connect() as conn:
        rows = targets(conn)
        print(
            f"{len(rows)} phantom file record(s) (colon in the name, size 0, asset has a real copy):"
        )
        for t in rows:
            print(
                f"  metafile {t['id_metafile']:>8}  {t['device_name']:6s} {t['code']:28s} "
                f"{t['file_id']!r}  real copies: {t['real_copies']}"
            )
        if mode == "list" or not rows:
            return
        mids = [t["id_metafile"] for t in rows]
        fids = [t["id_filmati"] for t in rows]
        print(f"backup: {_backup(conn, mids)}")

        cur = conn.cursor()
        real_before = _real_copy_counts(cur, fids)
        cur.execute("SELECT COUNT(*) FROM FS_METAFILE")
        total_before = int(cur.fetchone()[0])
        for m in mids:
            cur.execute("EXEC fs_metafile_delete @id_metafile=%s, @opt_outputMetafileIds=0", (m,))
            try:
                while cur.nextset():
                    pass
            except Exception as exc:  # noqa: BLE001 — a late server error must surface
                raise RuntimeError(f"fs_metafile_delete {m}: {exc}") from exc
        after = _counts(cur, mids)
        real_after = _real_copy_counts(cur, fids)
        cur.execute("SELECT COUNT(*) FROM FS_METAFILE")
        total_after = int(cur.fetchone()[0])
        problems = [m for m, c in after.items() if c != (0, 0, 0)]
        if real_after != real_before:
            problems.append("real copies changed: %r -> %r" % (real_before, real_after))
        if total_before - total_after != len(mids):
            problems.append(
                f"FS_METAFILE shrank by {total_before - total_after}, expected {len(mids)}"
            )
        if problems:
            conn.rollback()
            print("ROLLED BACK — verification failed:", problems)
            sys.exit(2)
        print(
            f"in transaction: {len(mids)} metafiles gone with their links and file rows; "
            f"real copies unchanged on all {len(real_before)} assets; FS_METAFILE -{len(mids)} exactly"
        )
        if mode == "rehearse":
            conn.rollback()
            print("REHEARSAL — rolled back, nothing changed")
            return
        conn.commit()
        print("COMMITTED")

    with connect() as conn2:
        cur2 = conn2.cursor()
        left = targets(conn2)
        final = _counts(cur2, mids)
        gone = all(c == (0, 0, 0) for c in final.values())
        print(f"fresh-connection readback: all gone = {gone}; phantom records left: {len(left)}")
        sys.exit(0 if gone and not left else 3)


if __name__ == "__main__":
    main()
