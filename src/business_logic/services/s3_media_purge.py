"""Remove S3 files Etere will never use again, the way Etere's own "Delete on -> AWS S3"
workflow does it, but in batches instead of one three-second Datamover job per file.

Oracle: Lee's 9/27/2026 run (215 files, wf_log action ``mm-s3-69ead476…``). Per file the
Datamover ran awsexec ``jobtype == delete`` on the key, then the metafile's rows vanished
from FS_METAFILE, FS_XFILEFILM, FS_FILE and smptemetadata. FILMATI (the asset) is untouched;
it simply has no copy on the S3 device any more.

Scope (Lee, 2026-09-29):

* Program assets only (NEWTYPE PGM / PGMX). Commercials, PSAs, PIs, station IDs, bumpers,
  religious opens and every other short-form type stay forever; Intelligent-Tiering carries
  their cost. Bumpers and opens are typed PGM in Etere, so a NAME guard covers them.
* Anything with HIATUS in the code or description is coming back. Never touched.
* Expired sweep: the asset is marked expired (FILMATI.DATA_SCAD on or before today).
  "Once we expire an asset in Etere, it won't be used again."
* A file is only removed when the bucket object exists with the size Etere recorded and
  the asset has no playlist row today or later. Anything else is reported, not touched.

The bucket is unversioned: an S3 delete is final. The FS rows are written to a restore
.sql before the first delete so the Media Library side can be put back.
"""

from __future__ import annotations

import csv
import datetime as dt
import decimal
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

S3_DEVICE = 6
ELIGIBLE_TYPES = ("PGM", "PGMX")
NAME_GUARD = ("BUMP", "OPEN", "CLOSE", "RELIG", "HIATUS")
BATCH_SIZE = 1000  # DeleteObjects maximum

# Rows Etere removes when a metafile leaves a device (verified on the 9/27 metafiles:
# zero rows left in each). Order matters for the restore file (identity parents first).
FS_TABLES: tuple[tuple[str, str], ...] = (
    ("FS_METAFILE", "ID_METAFILE"),
    ("FS_XFILEFILM", "ID_METAFILE"),
    ("FS_FILE", "ID_METAFILE"),
    ("smptemetadata", "id_metafile"),
)


def _name_guard_sql(col: str) -> str:
    return " AND ".join(f"{col} NOT LIKE '%{w}%'" for w in NAME_GUARD)


def expired_candidates_sql(today: dt.date) -> str:
    """S3 metafiles of program assets Etere has marked expired, with the facts the
    guards need. One row per metafile (an asset can hold two S3 metafile records)."""
    types = ", ".join(f"'{t}'" for t in ELIGIBLE_TYPES)
    return f"""
WITH air AS (
    SELECT ID_FILMATI,
           MAX(CASE WHEN STATUS IN ('Q', 'D') THEN DATA END) AS last_aired,
           SUM(CASE WHEN DATA >= '{today:%Y-%m-%d}' THEN 1 ELSE 0 END) AS future_rows
    FROM TPALINSE WITH (NOLOCK)
    WHERE LIVELLO = 0
    GROUP BY ID_FILMATI
)
SELECT m.ID_METAFILE AS id_metafile, f.ID_FILMATI AS id_filmati, f.COD_PROGRA AS cod_progra,
       f.DESCRIZIO AS descrizio, f.NEWTYPE AS newtype, m.FILE_ID AS file_id,
       fl.FILE_NAME AS file_name, fl.SIZE AS size, f.DATA_SCAD AS data_scad,
       air.last_aired, ISNULL(air.future_rows, 0) AS future_rows
FROM FS_METAFILE m WITH (NOLOCK)
JOIN FS_XFILEFILM x WITH (NOLOCK) ON x.ID_METAFILE = m.ID_METAFILE
JOIN FILMATI f WITH (NOLOCK) ON f.ID_FILMATI = x.ID_FILMATI
LEFT JOIN FS_FILE fl WITH (NOLOCK) ON fl.ID_METAFILE = m.ID_METAFILE
LEFT JOIN air ON air.ID_FILMATI = f.ID_FILMATI
WHERE m.ID_METADEVICE = {S3_DEVICE}
  AND f.NEWTYPE IN ({types})
  AND f.DATA_SCAD IS NOT NULL AND f.DATA_SCAD < '{today + dt.timedelta(days=1):%Y-%m-%d}'
  AND {_name_guard_sql("f.COD_PROGRA")}
  AND {_name_guard_sql("f.DESCRIZIO")}
ORDER BY f.DATA_SCAD, f.COD_PROGRA, m.ID_METAFILE
"""


def _rows(cur) -> list[dict]:
    names = [d[0] for d in cur.description]
    return [dict(zip(names, r, strict=True)) for r in cur.fetchall()]


def fetch_expired(conn, today: dt.date | None = None) -> list[dict]:
    today = today or dt.date.today()
    cur = conn.cursor()
    cur.execute(expired_candidates_sql(today))
    return _rows(cur)


def list_bucket(s3, bucket: str) -> dict[str, int]:
    """Key -> size for the whole bucket. ListObjectsV2 is a metadata call: it does not
    count as access for Intelligent-Tiering."""
    out: dict[str, int] = {}
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket):
        for o in page.get("Contents", []):
            out[o["Key"]] = o["Size"]
    return out


def check_rows(rows: Iterable[dict], listing: dict[str, int]) -> tuple[list[dict], list[dict]]:
    """Split candidates into (deletable, problems). A problem row carries ``problem``."""
    ok: list[dict] = []
    bad: list[dict] = []
    for r in rows:
        key = r.get("file_name") or ""
        size = listing.get(key)
        if int(r.get("future_rows") or 0) > 0:
            r["problem"] = f"{r['future_rows']} playlist row(s) today or later"
        elif not key:
            r["problem"] = "no FS_FILE row (no file name)"
        elif size is None:
            r["problem"] = "object missing in S3 (dangling record)"
        elif int(r.get("size") or -1) != size:
            r["problem"] = f"size differs: Etere {r.get('size')} vs S3 {size}"
        else:
            ok.append(r)
            continue
        bad.append(r)
    return ok, bad


def _lit(v) -> str:
    if v is None:
        return "NULL"
    if isinstance(v, bool):
        return "1" if v else "0"
    if isinstance(v, (int, decimal.Decimal, float)):
        return str(v)
    if isinstance(v, dt.datetime):
        return f"'{v:%Y-%m-%d %H:%M:%S}.{v.microsecond // 1000:03d}'"
    if isinstance(v, dt.date):
        return f"'{v:%Y-%m-%d}'"
    if isinstance(v, (bytes, bytearray)):
        return "0x" + bytes(v).hex() if v else "0x"
    return "N'" + str(v).replace("'", "''") + "'"


def _chunks(items: list, n: int) -> Iterable[list]:
    for i in range(0, len(items), n):
        yield items[i : i + n]


def restore_sql(conn, metafile_ids: list[int], header: str = "") -> str:
    """INSERT statements that put the FS rows of ``metafile_ids`` back, identity ids kept."""
    cur = conn.cursor()
    parts = [f"-- {header}".rstrip(), f"-- generated {dt.datetime.now():%Y-%m-%d %H:%M:%S}"]
    for table, fk in FS_TABLES:
        parts.append(f"SET IDENTITY_INSERT {table} ON;")
        for chunk in _chunks(metafile_ids, 500):
            cur.execute(f"SELECT * FROM {table} WHERE {fk} IN ({','.join(map(str, chunk))})")
            names = [d[0] for d in cur.description]
            for r in cur.fetchall():
                vals = ", ".join(_lit(v) for v in r)
                parts.append(f"INSERT INTO {table} ({', '.join(names)}) VALUES ({vals});")
        parts.append(f"SET IDENTITY_INSERT {table} OFF;")
    return "\n".join(parts) + "\n"


def write_plan_csv(path: Path, ok: list[dict], bad: list[dict]) -> None:
    cols = [
        "action",
        "file_name",
        "size",
        "id_metafile",
        "id_filmati",
        "cod_progra",
        "newtype",
        "data_scad",
        "last_aired",
        "future_rows",
        "problem",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for r in ok:
            w.writerow({**r, "action": "delete", "problem": ""})
        for r in bad:
            w.writerow({**r, "action": "skip"})


def delete_fs_rows(cur, metafile_ids: list[int]) -> dict[str, int]:
    """Etere's DB side of a device delete, for the metafiles S3 has confirmed gone."""
    counts: dict[str, int] = {}
    ids = ",".join(map(str, metafile_ids))
    for table, fk in reversed(FS_TABLES):  # children first
        cur.execute(f"DELETE FROM {table} WHERE {fk} IN ({ids})")
        counts[table] = cur.rowcount
    return counts


def purge(
    conn,
    s3,
    bucket: str,
    rows: list[dict],
    *,
    apply: bool,
    limit: int | None = None,
    batch_size: int = BATCH_SIZE,
    restore_dir: Path = Path("logs/s3-purge"),
    log: Callable[[str], None] = print,
) -> dict:
    """Delete ``rows`` (already checked) from S3 and from Etere, batch by batch.

    Per batch: one DeleteObjects call; the FS rows of the keys S3 reports Deleted are
    removed in one transaction and committed; keys S3 reports as errors keep their rows.
    A DB failure rolls that batch's rows back and stops the run — the S3 objects of that
    batch are already gone, so the leftover records show up as "dangling" on the next
    dry run. Dry run: nothing is called on S3 and nothing is written.
    """
    todo = rows[:limit] if limit else list(rows)
    result = {
        "planned": len(todo),
        "deleted_s3": 0,
        "deleted_db": 0,
        "s3_errors": [],
        "restore": None,
        "bytes": sum(int(r["size"]) for r in todo),
        "db_rows": {},
    }
    if not todo or not apply:
        return result
    restore_dir.mkdir(parents=True, exist_ok=True)
    rpath = restore_dir / f"s3-purge-restore-{dt.datetime.now():%Y%m%d-%H%M%S}.sql"
    rpath.write_text(
        restore_sql(
            conn,
            [r["id_metafile"] for r in todo],
            header=f"restore of {len(todo)} S3 metafile record(s) removed by purge_s3_media",
        )
    )
    result["restore"] = str(rpath)
    log(f"restore written: {rpath}")
    by_key = {r["file_name"]: r for r in todo}
    for n, chunk in enumerate(_chunks(todo, batch_size), 1):
        resp = s3.delete_objects(
            Bucket=bucket,
            Delete={"Objects": [{"Key": r["file_name"]} for r in chunk], "Quiet": False},
        )
        gone = [d["Key"] for d in resp.get("Deleted", [])]
        errs = resp.get("Errors", [])
        result["deleted_s3"] += len(gone)
        result["s3_errors"].extend(
            f"{e.get('Key')}: {e.get('Code')} {e.get('Message')}" for e in errs
        )
        ids = [by_key[k]["id_metafile"] for k in gone if k in by_key]
        if not ids:
            continue
        cur = conn.cursor()
        try:
            counts = delete_fs_rows(cur, ids)
            if counts.get("FS_METAFILE") != len(ids):
                raise RuntimeError(
                    f"batch {n}: FS_METAFILE rows removed {counts.get('FS_METAFILE')} != {len(ids)}"
                )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        result["deleted_db"] += len(ids)
        for t, c in counts.items():
            result["db_rows"][t] = result["db_rows"].get(t, 0) + c
        log(
            f"batch {n}: {len(gone)} object(s) deleted, {len(ids)} record(s) removed"
            + (f", {len(errs)} S3 error(s)" if errs else "")
        )
    return result


def verify(conn, s3, bucket: str, rows: list[dict], workers: int = 16) -> dict:
    """From a FRESH connection: no FS rows left for the metafiles, and HeadObject 404s
    for every key (HEAD is a metadata call, not an access)."""
    ids = [r["id_metafile"] for r in rows]
    left: dict[str, int] = {}
    cur = conn.cursor()
    for table, fk in FS_TABLES:
        n = 0
        for chunk in _chunks(ids, 500):
            cur.execute(f"SELECT COUNT(*) FROM {table} WHERE {fk} IN ({','.join(map(str, chunk))})")
            n += cur.fetchone()[0]
        left[table] = n

    def _exists(key: str) -> str | None:
        try:
            s3.head_object(Bucket=bucket, Key=key)
            return key
        except Exception as exc:  # noqa: BLE001 - 404 is the expected outcome
            code = getattr(exc, "response", {}).get("Error", {}).get("Code", "")
            if code in ("404", "NoSuchKey", "NotFound"):
                return None
            return f"{key} ({code or exc})"

    with ThreadPoolExecutor(max_workers=workers) as ex:
        still = [k for k in ex.map(_exists, [r["file_name"] for r in rows]) if k]
    return {"fs_rows_left": left, "objects_left": still, "ok": not still and not any(left.values())}


def summarize(rows: list[dict], top: int = 15) -> list[tuple[str, int, int]]:
    fam: dict[str, list[int]] = {}
    for r in rows:
        f = (r.get("cod_progra") or "")[:14]
        fam.setdefault(f, [0, 0])
        fam[f][0] += 1
        fam[f][1] += int(r.get("size") or 0)
    return sorted(((f, n, b) for f, (n, b) in fam.items()), key=lambda x: -x[2])[:top]
