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
import re
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
    """Key -> size for the whole bucket (67k keys ≈ 35 s). ListObjectsV2 is a metadata call:
    it does not count as access for Intelligent-Tiering."""
    out: dict[str, int] = {}
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket):
        for o in page.get("Contents", []):
            out[o["Key"]] = o["Size"]
    return out


LISTING_THRESHOLD = 4000  # HEADs cost ~7 ms each in parallel; a full listing of 67k keys ~35 s


def object_sizes(s3, bucket: str, keys: Iterable[str], workers: int = 32) -> dict[str, int]:
    """Key -> size for ``keys``: HeadObject each when there are few, one bucket listing
    when there are many. Neither counts as an access for Intelligent-Tiering."""
    wanted = [k for k in dict.fromkeys(keys) if k]
    if len(wanted) <= LISTING_THRESHOLD:
        return probe_sizes(s3, bucket, wanted, workers=workers)
    listing = list_bucket(s3, bucket)
    return {k: listing[k] for k in wanted if k in listing}


def probe_sizes(s3, bucket: str, keys: Iterable[str], workers: int = 32) -> dict[str, int]:
    """Key -> size for just ``keys`` via HeadObject (a few seconds for thousands of keys;
    HEAD is a metadata call, not an access). Missing keys are left out."""

    def _one(key: str) -> tuple[str, int | None]:
        try:
            return key, int(s3.head_object(Bucket=bucket, Key=key)["ContentLength"])
        except Exception as exc:  # noqa: BLE001 - 404 means "not there", anything else re-raises
            code = getattr(exc, "response", {}).get("Error", {}).get("Code", "")
            if code in ("404", "NoSuchKey", "NotFound"):
                return key, None
            raise

    keys = [k for k in dict.fromkeys(keys) if k]
    with ThreadPoolExecutor(max_workers=workers) as ex:
        return {k: n for k, n in ex.map(_one, keys) if n is not None}


def check_rows(rows: Iterable[dict], listing: dict[str, int]) -> tuple[list[dict], list[dict]]:
    """CLI view of ``categorize``: (deletable, everything else with ``problem`` set)."""
    cats = categorize(rows, listing)
    bad = [r for k, v in cats.items() if k != "delete" for r in v]
    for r in bad:
        if "playlist" not in r["problem"] and "missing" in r["problem"]:
            r["problem"] = "object missing in S3 (dangling record)"
        elif "vs S3" in r["problem"]:
            r["problem"] = "size differs: " + r["problem"]
    return cats["delete"], bad


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


def restore_sql(
    conn, metafile_ids: list[int], header: str = "", asset_ids: list[int] | None = None
) -> str:
    """INSERT statements that put the FS rows of ``metafile_ids`` back (identity ids kept),
    plus UPDATEs that put the assets' expiry dates back to what they were."""
    cur = conn.cursor()
    parts = [f"-- {header}".rstrip(), f"-- generated {dt.datetime.now():%Y-%m-%d %H:%M:%S}"]
    for table, fk in FS_TABLES:
        if not metafile_ids:
            break
        parts.append(f"SET IDENTITY_INSERT {table} ON;")
        for chunk in _chunks(metafile_ids, 500):
            cur.execute(f"SELECT * FROM {table} WHERE {fk} IN ({','.join(map(str, chunk))})")
            names = [d[0] for d in cur.description]
            for r in cur.fetchall():
                vals = ", ".join(_lit(v) for v in r)
                parts.append(f"INSERT INTO {table} ({', '.join(names)}) VALUES ({vals});")
        parts.append(f"SET IDENTITY_INSERT {table} OFF;")
    if asset_ids:
        parts.append("-- expiry dates before the run")
        for chunk in _chunks(sorted(set(asset_ids)), 500):
            cur.execute(
                f"SELECT ID_FILMATI, DATA_SCAD FROM FILMATI WHERE ID_FILMATI IN ({','.join(map(str, chunk))})"
            )
            for aid, scad in cur.fetchall():
                parts.append(
                    f"UPDATE FILMATI SET DATA_SCAD = {_lit(scad)} WHERE ID_FILMATI = {aid};"
                )
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


def ensure_expired(cur, asset_ids: Iterable[int], today: dt.date) -> int:
    """The invariant (Lee 9/29): nothing in S3 == expired in Etere. Stamp DATA_SCAD = today
    on any asset that is not yet expired. Returns rows changed."""
    ids = sorted({int(a) for a in asset_ids})
    n = 0
    for chunk in _chunks(ids, 500):
        cur.execute(
            f"UPDATE FILMATI SET DATA_SCAD = '{today:%Y-%m-%d}' WHERE ID_FILMATI IN ({','.join(map(str, chunk))})"
            f" AND (DATA_SCAD IS NULL OR DATA_SCAD > '{today:%Y-%m-%d}')"
        )
        n += cur.rowcount
    return n


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
    today: dt.date | None = None,
) -> dict:
    """Delete ``rows`` (already checked) from S3 and from Etere, batch by batch.

    Per batch: one DeleteObjects call; the FS rows of the keys S3 reports Deleted are
    removed and their assets stamped expired in one transaction and committed; keys S3
    reports as errors keep their rows. A DB failure rolls that batch's rows back and stops
    the run; the S3 objects of that batch are already gone, so the leftover records show
    up as "dangling" on the next scan. Dry run: nothing is called on S3, nothing written.
    """
    today = today or dt.date.today()
    todo = rows[:limit] if limit else list(rows)
    result = {
        "planned": len(todo),
        "deleted_s3": 0,
        "deleted_db": 0,
        "expired_stamped": 0,
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
            asset_ids=[r["id_filmati"] for r in todo],
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
        done_rows = [by_key[k] for k in gone if k in by_key]
        if not done_rows:
            continue
        ids = [r["id_metafile"] for r in done_rows]
        cur = conn.cursor()
        try:
            counts = delete_fs_rows(cur, ids)
            if counts.get("FS_METAFILE") != len(ids):
                raise RuntimeError(
                    f"batch {n}: FS_METAFILE rows removed {counts.get('FS_METAFILE')} != {len(ids)}"
                )
            stamped = ensure_expired(cur, (r["id_filmati"] for r in done_rows), today)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        result["deleted_db"] += len(ids)
        result["expired_stamped"] += stamped
        for t, c in counts.items():
            result["db_rows"][t] = result["db_rows"].get(t, 0) + c
        log(
            f"batch {n}: {len(gone)} object(s) deleted, {len(ids)} record(s) removed, "
            f"{stamped} asset(s) newly stamped expired"
            + (f", {len(errs)} S3 error(s)" if errs else "")
        )
    return result


def verify(
    conn, s3, bucket: str, rows: list[dict], workers: int = 16, today: dt.date | None = None
) -> dict:
    """From a FRESH connection: no FS rows left for the metafiles, HeadObject 404s for every
    key (HEAD is a metadata call, not an access), and every asset expired as of today."""
    today = today or dt.date.today()
    ids = [int(r["id_metafile"]) for r in rows if r.get("id_metafile")]
    left: dict[str, int] = {}
    cur = conn.cursor()
    for table, fk in FS_TABLES:
        n = 0
        for chunk in _chunks(ids, 500):
            cur.execute(f"SELECT COUNT(*) FROM {table} WHERE {fk} IN ({','.join(map(str, chunk))})")
            n += cur.fetchone()[0]
        left[table] = n
    not_expired = 0
    for chunk in _chunks(sorted({int(r["id_filmati"]) for r in rows}), 500):
        cur.execute(
            f"SELECT COUNT(*) FROM FILMATI WHERE ID_FILMATI IN ({','.join(map(str, chunk))})"
            f" AND (DATA_SCAD IS NULL OR DATA_SCAD > '{today:%Y-%m-%d}')"
        )
        not_expired += cur.fetchone()[0]

    def _exists(key: str) -> str | None:
        try:
            s3.head_object(Bucket=bucket, Key=key)
            return key
        except Exception as exc:  # noqa: BLE001 - 404 is the expected outcome
            code = getattr(exc, "response", {}).get("Error", {}).get("Code", "")
            if code in ("404", "NoSuchKey", "NotFound"):
                return None
            return f"{key} ({code or exc})"

    keys = [r["file_name"] for r in rows if r.get("file_name")]
    with ThreadPoolExecutor(max_workers=workers) as ex:
        still = [k for k in ex.map(_exists, keys) if k]
    return {
        "fs_rows_left": left,
        "objects_left": still,
        "not_expired": not_expired,
        "ok": not still and not any(left.values()) and not_expired == 0,
    }


def summarize(rows: list[dict], top: int = 15) -> list[tuple[str, int, int]]:
    fam: dict[str, list[int]] = {}
    for r in rows:
        f = (r.get("cod_progra") or "")[:14]
        fam.setdefault(f, [0, 0])
        fam[f][0] += 1
        fam[f][1] += int(r.get("size") or 0)
    return sorted(((f, n, b) for f, (n, b) in fam.items()), key=lambda x: -x[2])[:top]


# --------------------------------------------------------------------------- categories
# What the Control Room page shows (Lee 9/29: "categories, such as how you presented them
# to me"). Each key names ONE action; the page offers a button only for the first two.
CATEGORIES: dict[str, tuple[str, str]] = {
    "delete": (
        "Expired in Etere, file still in S3",
        "Delete the S3 file and remove the reference from the Media Library",
    ),
    "aged": (
        "Old programming: not expired, file in S3, last aired in the chosen date range",
        "Delete the S3 file, remove the reference and mark the asset expired as of today",
    ),
    "dangling": (
        "Expired in Etere, no file in S3",
        "Remove the dead reference from the Media Library (nothing to delete in S3)",
    ),
    "unexpired": (
        "No file in S3, but the asset is not expired",
        'Mark the asset expired as of today (Etere\'s "mark as expired", one field: DATA_SCAD)',
    ),
    "size_mismatch": (
        "Expired, but the S3 file's size differs from Etere's record",
        "Needs a look first; nothing is touched",
    ),
    "booked": (
        "Expired, but still on a playlist today or later",
        "Nothing is touched while a playlist row points at it",
    ),
}
ACTIONABLE = ("delete", "aged", "dangling", "unexpired")


_MARKET_SUFFIX = re.compile(r"[-_](NYC|LAX|SFO|SEA|HOU|CMP|CVC|WDC|MMT|DAL)$", re.I)


def show_family(code: str | None) -> str:
    """Group key for a program code: strip market suffix, piece letter, date tail and
    episode number. ``DTV-YOUTHBOOKS01-0123A`` -> ``DTV-YOUTHBOOKS``,
    ``NEWSTODAY073126`` -> ``NEWSTODAY``, ``FCI_010223_LAX`` -> ``FCI``."""
    c = re.sub(r"\.mp4$", "", (code or "").strip(), flags=re.I)
    c = _MARKET_SUFFIX.sub("", c)
    c = re.sub(r"(?<=\d)[A-Za-z]\d?$", "", c)  # piece letter (+ retake digit)
    c = re.sub(r"[-_]?\d{4,8}$", "", c)  # date
    c = re.sub(r"\d{1,3}$", "", c)  # episode / season number
    c = c.rstrip("-_ ")
    return c or "(no code)"


def categorize(rows: Iterable[dict], sizes: dict[str, int]) -> dict[str, list[dict]]:
    """Put every candidate metafile into exactly one CATEGORIES bucket."""
    out: dict[str, list[dict]] = {k: [] for k in CATEGORIES}
    for r in rows:
        key = r.get("file_name") or ""
        size = sizes.get(key)
        if int(r.get("future_rows") or 0) > 0:
            r["problem"] = f"{r['future_rows']} playlist row(s) today or later"
            out["booked"].append(r)
        elif not key or size is None:
            r["problem"] = "no FS_FILE row" if not key else "object missing in S3"
            out["dangling"].append(r)
        elif int(r.get("size") or -1) != size:
            r["problem"] = f"Etere {r.get('size')} vs S3 {size}"
            out["size_mismatch"].append(r)
        else:
            out["delete"].append(r)
    return out


def _iso(v) -> str | None:
    if v is None:
        return None
    return v.date().isoformat() if isinstance(v, dt.datetime) else str(v)[:10]


def group_rows(rows: Iterable[dict]) -> list[dict]:
    """Families (shows) for the page: count, bytes, expiry span, and the file list."""
    fam: dict[str, dict] = {}
    for r in rows:
        g = fam.setdefault(
            show_family(r.get("cod_progra")),
            {
                "family": show_family(r.get("cod_progra")),
                "count": 0,
                "bytes": 0,
                "expired_from": None,
                "expired_to": None,
                "aired_from": None,
                "aired_to": None,
                "ids": [],
                "files": [],
            },
        )
        g["count"] += 1
        g["bytes"] += int(r.get("size") or 0)
        d = _iso(r.get("data_scad"))
        if d:
            g["expired_from"] = min(g["expired_from"] or d, d)
            g["expired_to"] = max(g["expired_to"] or d, d)
        la = _iso(r.get("last_aired"))
        if la:
            g["aired_from"] = min(g["aired_from"] or la, la)
            g["aired_to"] = max(g["aired_to"] or la, la)
        g["ids"].append(int(r["id_metafile"] or r["id_filmati"]))
        g["files"].append(
            {
                "id_metafile": r.get("id_metafile"),
                "id_filmati": int(r["id_filmati"]),
                "cod_progra": r.get("cod_progra"),
                "file_name": r.get("file_name"),
                "size": int(r.get("size") or 0),
                "expired": d,
                "last_aired": _iso(r.get("last_aired")),
                "problem": r.get("problem", ""),
            }
        )
    return sorted(fam.values(), key=lambda g: (-g["bytes"], g["family"]))


def short_form_expired(conn, today: dt.date) -> dict:
    """Expired short-form assets (commercials, PSAs, PIs, IDs, ...) that still hold an S3
    file. Kept on purpose (Lee 9/29): Intelligent-Tiering carries them. Info only."""
    types = ", ".join(f"'{t}'" for t in ELIGIBLE_TYPES)
    cur = conn.cursor()
    cur.execute(
        f"""SELECT COUNT(*), ISNULL(SUM(CAST(fl.SIZE AS decimal(20,0))), 0)
            FROM FS_METAFILE m WITH (NOLOCK)
            JOIN FS_XFILEFILM x WITH (NOLOCK) ON x.ID_METAFILE = m.ID_METAFILE
            JOIN FILMATI f WITH (NOLOCK) ON f.ID_FILMATI = x.ID_FILMATI
            LEFT JOIN FS_FILE fl WITH (NOLOCK) ON fl.ID_METAFILE = m.ID_METAFILE
            WHERE m.ID_METADEVICE = {S3_DEVICE} AND f.NEWTYPE NOT IN ({types})
              AND f.DATA_SCAD IS NOT NULL
              AND f.DATA_SCAD < '{today + dt.timedelta(days=1):%Y-%m-%d}'"""
    )
    n, b = cur.fetchone()
    return {"count": int(n), "bytes": int(b)}


def scan(
    conn,
    s3,
    bucket: str,
    today: dt.date | None = None,
    log: Callable[[str], None] = lambda s: None,
    aged_from: dt.date | None = None,
    aged_to: dt.date | None = None,
) -> dict:
    """Everything the page needs: candidates by category, grouped by show family.
    The old-programming sweep runs only when ``aged_to`` is given."""
    today = today or dt.date.today()
    rows = fetch_expired(conn, today)
    log(f"{len(rows)} S3 metafile(s) on expired program assets")
    aged_rows: list[dict] = []
    if aged_to:
        aged_rows = fetch_aged(conn, today, aged_from, aged_to)
        log(
            f"{len(aged_rows)} S3 metafile(s) on not-expired program assets last aired "
            f"{aged_from or 'ever'} .. {aged_to}"
        )
    sizes = object_sizes(s3, bucket, (r.get("file_name") for r in rows + aged_rows))
    log(f"{len(sizes)} object(s) found in S3")
    cats = categorize(rows, sizes)
    unexp = categorize_unexpired(fetch_unexpired(conn, today))
    log(f"{len(unexp['unexpired'])} program asset(s) with no S3 file are not expired")
    cats["unexpired"] = unexp["unexpired"]
    cats["booked"].extend(unexp["booked"])
    cats["aged"] = []
    if aged_rows:
        aged = categorize_aged(aged_rows, sizes)
        cats["aged"] = aged.pop("aged")
        for k, v in aged.items():
            cats[k].extend(v)
    for r in cats["dangling"]:
        r["size"] = 0  # no object exists; Etere's recorded size is not storage
    return {
        "as_of": today.isoformat(),
        "aged_range": [
            aged_from.isoformat() if aged_from else None,
            aged_to.isoformat() if aged_to else None,
        ],
        "total": len(rows),
        "categories": [
            {
                "key": k,
                "label": CATEGORIES[k][0],
                "action": CATEGORIES[k][1],
                "actionable": k in ACTIONABLE,
                "count": len(v),
                "bytes": sum(int(r.get("size") or 0) for r in v),
                "groups": group_rows(v),
            }
            for k in CATEGORIES
            for v in [cats.get(k, [])]
        ],
        "short_form": short_form_expired(conn, today),
    }


def remove_references(
    conn,
    rows: list[dict],
    *,
    apply: bool,
    restore_dir: Path = Path("logs/s3-purge"),
    log: Callable[[str], None] = print,
    today: dt.date | None = None,
) -> dict:
    """The "dangling" action: Etere still lists an S3 copy whose object is gone. Remove
    the FS rows and make sure the asset is expired (restore .sql first); no S3 call."""
    today = today or dt.date.today()
    result = {
        "planned": len(rows),
        "deleted_db": 0,
        "expired_stamped": 0,
        "restore": None,
        "db_rows": {},
    }
    if not rows or not apply:
        return result
    ids = [int(r["id_metafile"]) for r in rows]
    by_id = {int(r["id_metafile"]): r for r in rows}
    restore_dir.mkdir(parents=True, exist_ok=True)
    rpath = restore_dir / f"s3-purge-restore-dangling-{dt.datetime.now():%Y%m%d-%H%M%S}.sql"
    rpath.write_text(
        restore_sql(
            conn,
            ids,
            header=f"restore of {len(ids)} dangling S3 metafile record(s)",
            asset_ids=[r["id_filmati"] for r in rows],
        )
    )
    result["restore"] = str(rpath)
    log(f"restore written: {rpath}")
    for n, chunk in enumerate(_chunks(ids, 500), 1):
        cur = conn.cursor()
        try:
            counts = delete_fs_rows(cur, chunk)
            if counts.get("FS_METAFILE") != len(chunk):
                raise RuntimeError(
                    f"batch {n}: FS_METAFILE rows removed {counts.get('FS_METAFILE')} != {len(chunk)}"
                )
            stamped = ensure_expired(cur, (by_id[i]["id_filmati"] for i in chunk), today)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        result["deleted_db"] += len(chunk)
        result["expired_stamped"] += stamped
        for t, c in counts.items():
            result["db_rows"][t] = result["db_rows"].get(t, 0) + c
        log(
            f"batch {n}: {len(chunk)} dead reference(s) removed, {stamped} asset(s) newly stamped expired"
        )
    return result


def mark_expired(
    conn,
    rows: list[dict],
    *,
    apply: bool,
    restore_dir: Path = Path("logs/s3-purge"),
    log: Callable[[str], None] = print,
    today: dt.date | None = None,
) -> dict:
    """The "unexpired" action: a program asset with no file in S3 that is not expired.
    Stamp DATA_SCAD = today (what Etere's wizard writes); restore .sql of the old dates first."""
    today = today or dt.date.today()
    result = {"planned": len(rows), "expired_stamped": 0, "restore": None}
    if not rows or not apply:
        return result
    ids = sorted({int(r["id_filmati"]) for r in rows})
    restore_dir.mkdir(parents=True, exist_ok=True)
    rpath = restore_dir / f"s3-purge-restore-expiry-{dt.datetime.now():%Y%m%d-%H%M%S}.sql"
    rpath.write_text(
        restore_sql(
            conn,
            [],
            header=f"expiry dates of {len(ids)} asset(s) before mark-expired",
            asset_ids=ids,
        )
    )
    result["restore"] = str(rpath)
    log(f"restore written: {rpath}")
    cur = conn.cursor()
    try:
        stamped = ensure_expired(cur, ids, today)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    result["expired_stamped"] = stamped
    log(f"{stamped} asset(s) stamped expired as of {today:%Y-%m-%d}")
    return result


def unexpired_candidates_sql(today: dt.date) -> str:
    """Program assets with NO S3 copy that are not expired: the invariant breakers. Assets
    created in the last 7 days are left alone (ingest in progress)."""
    types = ", ".join(f"'{t}'" for t in ELIGIBLE_TYPES)
    return f"""
WITH air AS (
    SELECT ID_FILMATI,
           MAX(CASE WHEN STATUS IN ('Q', 'D') THEN DATA END) AS last_aired,
           SUM(CASE WHEN DATA >= '{today:%Y-%m-%d}' THEN 1 ELSE 0 END) AS future_rows
    FROM TPALINSE WITH (NOLOCK) WHERE LIVELLO = 0 GROUP BY ID_FILMATI
),
copies AS (
    SELECT x.ID_FILMATI,
           SUM(CASE WHEN m.ID_METADEVICE = {S3_DEVICE} THEN 1 ELSE 0 END) AS s3_copies,
           COUNT(*) AS all_copies
    FROM FS_XFILEFILM x WITH (NOLOCK) JOIN FS_METAFILE m WITH (NOLOCK) ON m.ID_METAFILE = x.ID_METAFILE
    GROUP BY x.ID_FILMATI
)
SELECT NULL AS id_metafile, f.ID_FILMATI AS id_filmati, f.COD_PROGRA AS cod_progra,
       f.DESCRIZIO AS descrizio, f.NEWTYPE AS newtype, NULL AS file_id, NULL AS file_name,
       0 AS size, f.DATA_SCAD AS data_scad, f.CREATIONDATE AS created,
       air.last_aired, ISNULL(air.future_rows, 0) AS future_rows,
       ISNULL(c.all_copies, 0) AS other_copies
FROM FILMATI f WITH (NOLOCK)
LEFT JOIN copies c ON c.ID_FILMATI = f.ID_FILMATI
LEFT JOIN air ON air.ID_FILMATI = f.ID_FILMATI
WHERE f.NEWTYPE IN ({types})
  AND ISNULL(c.s3_copies, 0) = 0
  AND (f.DATA_SCAD IS NULL OR f.DATA_SCAD >= '{today + dt.timedelta(days=1):%Y-%m-%d}')
  AND f.CREATIONDATE < '{today - dt.timedelta(days=7):%Y-%m-%d}'
  AND {_name_guard_sql("f.COD_PROGRA")}
  AND {_name_guard_sql("f.DESCRIZIO")}
ORDER BY f.CREATIONDATE, f.COD_PROGRA
"""


def fetch_unexpired(conn, today: dt.date | None = None) -> list[dict]:
    today = today or dt.date.today()
    cur = conn.cursor()
    cur.execute(unexpired_candidates_sql(today))
    return _rows(cur)


def categorize_unexpired(rows: Iterable[dict]) -> dict[str, list[dict]]:
    """Booked-ahead assets are reported, the rest are the "unexpired" action."""
    out: dict[str, list[dict]] = {"unexpired": [], "booked": []}
    for r in rows:
        if int(r.get("future_rows") or 0) > 0:
            r["problem"] = f"{r['future_rows']} playlist row(s) today or later; no file in S3"
            out["booked"].append(r)
        else:
            r["problem"] = (
                f"copy on CIB/WIP only ({r['other_copies']})"
                if int(r.get("other_copies") or 0)
                else "no copy anywhere"
            )
            out["unexpired"].append(r)
    return out


# --------------------------------------------------------------------------- aged sweep
def aged_candidates_sql(today: dt.date, d_from: dt.date | None, d_to: dt.date) -> str:
    """S3 metafiles of program assets that are NOT expired, whose last air date (or, never
    aired, upload date) falls in [d_from, d_to]. This is how Lee picked AVS Jan-Jun and
    NEWSTODAY Jan-Jul by hand on 9/27: a show and a stretch of air dates."""
    types = ", ".join(f"'{t}'" for t in ELIGIBLE_TYPES)
    lo = f"AND COALESCE(air.last_aired, f.CREATIONDATE) >= '{d_from:%Y-%m-%d}'" if d_from else ""
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
       f.CREATIONDATE AS created, air.last_aired, ISNULL(air.future_rows, 0) AS future_rows
FROM FS_METAFILE m WITH (NOLOCK)
JOIN FS_XFILEFILM x WITH (NOLOCK) ON x.ID_METAFILE = m.ID_METAFILE
JOIN FILMATI f WITH (NOLOCK) ON f.ID_FILMATI = x.ID_FILMATI
LEFT JOIN FS_FILE fl WITH (NOLOCK) ON fl.ID_METAFILE = m.ID_METAFILE
LEFT JOIN air ON air.ID_FILMATI = f.ID_FILMATI
WHERE m.ID_METADEVICE = {S3_DEVICE}
  AND f.NEWTYPE IN ({types})
  AND (f.DATA_SCAD IS NULL OR f.DATA_SCAD >= '{today + dt.timedelta(days=1):%Y-%m-%d}')
  AND COALESCE(air.last_aired, f.CREATIONDATE) < '{d_to + dt.timedelta(days=1):%Y-%m-%d}'
  {lo}
  AND {_name_guard_sql("f.COD_PROGRA")}
  AND {_name_guard_sql("f.DESCRIZIO")}
ORDER BY COALESCE(air.last_aired, f.CREATIONDATE), f.COD_PROGRA, m.ID_METAFILE
"""


def fetch_aged(conn, today: dt.date, d_from: dt.date | None, d_to: dt.date) -> list[dict]:
    cur = conn.cursor()
    cur.execute(aged_candidates_sql(today, d_from, d_to))
    return _rows(cur)


def categorize_aged(rows: Iterable[dict], sizes: dict[str, int]) -> dict[str, list[dict]]:
    """Same S3 checks as the expired sweep; the clean ones are the "aged" action, the rest
    join the shared report/dangling buckets with a "not expired" note."""
    cats = categorize(rows, sizes)
    out = {"aged": cats.pop("delete")}
    for k in ("dangling", "size_mismatch", "booked"):
        for r in cats[k]:
            r["problem"] = (
                r.get("problem") + "; " if r.get("problem") else ""
            ) + "asset not expired"
        out[k] = cats[k]
    return out


def scan_aged(
    conn,
    s3,
    bucket: str,
    today: dt.date,
    d_from: dt.date | None,
    d_to: dt.date,
    log: Callable[[str], None] = lambda s: None,
) -> dict[str, list[dict]]:
    rows = fetch_aged(conn, today, d_from, d_to)
    log(
        f"{len(rows)} S3 metafile(s) on not-expired program assets last aired {d_from or 'ever'} .. {d_to}"
    )
    sizes = object_sizes(s3, bucket, (r.get("file_name") for r in rows))
    return categorize_aged(rows, sizes)
