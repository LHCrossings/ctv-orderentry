"""Playout binding — TPALINSE.SUPPORTO must name the FILE the CIB can open.

The binding is `<channel prefix> + FS_FILMATI.FILE_ID` (`0ETX      CD-TeresaTeng12-0130A`).
Etere's own `sch_UpdateSupportAndProperties` builds it from COD_PROGRA instead, so every
asset whose code differs from its file name (our rename-programming tool gives shows their
schedule code; WorldLink PIs carry an ISCI over a `WLPI-LF-nnnn` file) is one Etere
refresh away from a binding to a file that does not exist — the AU logs FINDCLIP forever
and the slot airs BLACK (NYC 9/2/2026 06:00 Phoenix Evening Express).

2026-09-14: Daily Programming had bound Teresa Teng 12/13 and Beauty Tycoon 23 correctly at
placement (9/11 09:18); the assets were renamed two hours later and Etere re-ran its support
update on exactly those rows (Explode signature on the renamed shows only, not on the
neighbour placed in the same batch) — 258 rows for 9/15-16 pointed at `CD-TERESATENG12-…`
while the file is `CD-TeresaTeng12-0130A.mp4`. Two guards existed and both were blind:
the rename tool's rebind and `check_bindings.py` took the FILE_ID from "the newest non-S3
copy", which (a) does not exist until the aligner restores the file the night before air,
so nothing more than a day out was ever rebound, and (b) once picked a size-0 stale CIB
record `PI-LF-0011: Ellipse Deluxe` and wrote a colon binding that can never load.

ONE rule, here, for every consumer (CLI, rename tool, nightly Broadcast Health scan):
the file name is a SIZED copy whose name carries no colon, the S3 master first (every CIB
copy is restored under the S3 key), any other sized copy as fallback.
Related: daily_programming_run._bind_supporto / finish_service._supporto /
orders._pi_filler_supporto bind a single new row the same way (S3 first) at write time.
"""

from __future__ import annotations

import datetime as dt
from collections import Counter
from pathlib import Path

FPS = 29.97
PREFIX = "0ETX      "
MARKETS = {
    1: "NYC",
    2: "CMP",
    3: "HOU",
    4: "SFO",
    5: "SEA",
    6: "LAX",
    7: "CVC",
    8: "WDC",
    9: "MMT",
    10: "DAL",
}
MAX_ROWS = 400
MAX_LEN = (
    42  # TPALINSE.SUPPORTO is varchar(42); a clipped binding cannot load (DAL ID, 31 chars, 9/1)
)

# The file an asset's binding must name — see the module docstring. Aliases: `t` = TPALINSE.
FILE_ID_APPLY = (
    "CROSS APPLY (SELECT TOP 1 x.FILE_ID FROM FS_FILMATI x"
    " JOIN FS_METADEVICE d ON d.ID_METADEVICE = x.ID_METADEVICE"
    " WHERE x.ID_FILMATI = t.ID_FILMATI AND x.PHYSICAL_SIZE > 0 AND x.FILE_ID NOT LIKE '%:%'"
    " ORDER BY CASE WHEN d.LEGACY_MEDIAID = '0' THEN 0 ELSE 1 END, x.LASTUPDATE DESC) fs"
)

# The same rule for ONE asset, as a parameterised statement (pymssql: literal % doubled).
FILE_ID_FOR_ASSET = (
    "SELECT TOP 1 x.FILE_ID FROM FS_FILMATI x"
    " JOIN FS_METADEVICE d ON d.ID_METADEVICE = x.ID_METADEVICE"
    " WHERE x.ID_FILMATI = %s AND x.PHYSICAL_SIZE > 0 AND x.FILE_ID NOT LIKE '%%:%%'"
    " ORDER BY CASE WHEN d.LEGACY_MEDIAID = '0' THEN 0 ELSE 1 END, x.LASTUPDATE DESC"
)


def binding(cur, asset_id: int) -> str | None:
    """The playout binding a row of `asset_id` must carry: PREFIX + the FILE_ID the rule
    picks (sized, colon-free, S3 master first, newest). None when the asset has no such
    copy yet — leave the row alone, the nightly scan re-checks it. Raises when the value
    would not fit varchar(42).

    Every writer takes the value from here — Daily Programming `_bind_supporto` and
    `_apply_filmati_sync`, Finish `_supporto` — so no placement path can build it from
    the code again. 2026-09-15: `_apply_filmati_sync` wrote prefix + COD_PROGRA on every
    row of an asset AFTER `_bind_supporto` had bound the new row right, so each Daily
    Programming placement of a renamed show broke its own binding; the nightly aligner
    repaired rows once the file reached a CIB, which is why only S3-only assets ever
    showed (142 rows for 9/17; the 9/14 batch; NYC 9/2 aired black)."""
    cur.execute(FILE_ID_FOR_ASSET, (int(asset_id),))
    r = cur.fetchone()
    if not r or not r[0]:
        return None
    sup = PREFIX + str(r[0]).strip()
    if len(sup) > MAX_LEN:
        raise RuntimeError(f"SUPPORTO overflows varchar({MAX_LEN}), cannot bind: {sup!r}")
    return sup


def hms(frames: int) -> str:
    s = frames / FPS
    return "%02d:%02d:%02d" % (s // 3600, (s % 3600) // 60, s % 60)


def mismatch_where(d_from: dt.date, d_to: dt.date, asset_id: int | None = None) -> str:
    """FROM/WHERE for unaired playlist rows whose binding is not prefix + FILE_ID.

    Live assets (LIVE_ID set, `0LIVE…` bindings) are excluded; only I/E rows are in scope —
    aired rows are the as-run record. Dates are literals so the SQL runs on pymssql and
    pyodbc alike.

    The FILE part is what the CIB resolves; the 10-char device prefix is not: `1ETX      X`
    (CIB1's LEGACY_BASESUPP, written by the traffic-assign builder when a CIB1 copy sorted
    first) aired Q on all ten markets (FEEDING2ME18 6/22, SHRINERS2ME25 9/3), and the
    2026-09-14 01:40 nightly scan flagged 49 such Lexus/BVFL rows as "would air black" —
    a false alarm (Lee). So: any `<digit>ETX      ` prefix is accepted, the FILE_ID must
    match (aired oracle 6/1-9/13: 440,929 rows, 0 mismatches); a rebind normalises to
    `0ETX      ` like every other writer."""
    sql = (
        f" FROM TPALINSE t JOIN FILMATI f ON f.ID_FILMATI = t.ID_FILMATI {FILE_ID_APPLY}"
        f" WHERE t.DATA BETWEEN '{d_from:%Y-%m-%d}' AND '{d_to:%Y-%m-%d}'"
        " AND t.LIVELLO = 0 AND t.COD_USER BETWEEN 1 AND 10 AND t.ID_FILMATI > 0"
        " AND f.LIVE_ID IS NULL AND t.STATUS IN ('I', 'E')"
        f" AND (t.SUPPORTO NOT LIKE '[0-9]ETX      %' OR RTRIM(SUBSTRING(t.SUPPORTO, {len(PREFIX) + 1}, 100)) <> RTRIM(fs.FILE_ID))"
    )
    if asset_id is not None:
        sql += f" AND t.ID_FILMATI = {int(asset_id)}"
    return sql


def _window(days: int, today: dt.date | None) -> tuple[dt.date, dt.date]:
    today = today or dt.date.today()
    return today, today + dt.timedelta(days=days)


def fetch_mismatches(conn, days: int = 2, today: dt.date | None = None) -> list[dict]:
    d0, d1 = _window(days, today)
    cur = conn.cursor()
    cur.execute(
        "SELECT t.ID_TPALINSE, t.COD_USER, t.DATA, t.ORA, t.STATUS, RTRIM(t.COD_PROGRA),"
        " RTRIM(t.SUPPORTO), RTRIM(fs.FILE_ID), t.ID_FILMATI"
        + mismatch_where(d0, d1)
        + " ORDER BY t.DATA, t.COD_USER, t.ORA"
    )
    out = []
    for tid, mk, data, ora, st, code, supp, fid, filmati in cur.fetchall():
        out.append(
            {
                "id_tpalinse": int(tid),
                "market": MARKETS.get(int(mk), str(mk)),
                "date": data.strftime("%Y-%m-%d") if hasattr(data, "strftime") else str(data)[:10],
                "time": hms(int(ora)),
                "status": st,
                "code": code,
                "bound": supp[len(PREFIX) :]
                if len(supp) > len(PREFIX) and supp[1:4] == "ETX"
                else supp,
                "file": fid,
                "id_filmati": int(filmati),
            }
        )
    return out


def summarize(rows: list[dict]) -> list[dict]:
    """One entry per (code, file), most rows first, with markets and the first airing."""
    groups: dict[tuple[str, str], list[dict]] = {}
    for r in rows:
        groups.setdefault((r["code"], r["file"]), []).append(r)
    out = []
    for (code, file), rs in groups.items():
        first = min(rs, key=lambda r: (r["date"], r["time"]))
        mk = Counter(r["market"] for r in rs)
        out.append(
            {
                "code": code,
                "file": file,
                "count": len(rs),
                "markets": ", ".join(f"{m} {n}" for m, n in sorted(mk.items())),
                "first": {"market": first["market"], "date": first["date"], "time": first["time"]},
            }
        )
    out.sort(key=lambda g: (-g["count"], g["first"]["date"], g["first"]["time"]))
    return out


def scan(conn, days: int = 2, today: dt.date | None = None) -> dict:
    """Report-only. JSON-able: state 'ok' | 'alert', count, by_code, rows (capped)."""
    rows = fetch_mismatches(conn, days, today)
    d0, d1 = _window(days, today)
    return {
        "state": "alert" if rows else "ok",
        "checked_at": dt.datetime.now().isoformat(timespec="seconds"),
        "from": d0.isoformat(),
        "to": d1.isoformat(),
        "count": len(rows),
        "by_code": summarize(rows),
        "rows": rows[:MAX_ROWS],
    }


def restore_sql(rows: list[dict]) -> str:
    return "".join(
        f"UPDATE TPALINSE SET SUPPORTO='{PREFIX}{r['bound']}', STATUS='{r['status']}'"
        f" WHERE ID_TPALINSE={r['id_tpalinse']};\n"
        for r in rows
    )


def rebind(
    conn,
    days: int = 2,
    today: dt.date | None = None,
    restore_dir: Path = Path("logs/finish-restore"),
) -> tuple[int, Path | None, list[dict]]:
    """Rebind every mismatched unaired row in the window to prefix + FILE_ID; an E row
    goes back to I so the AU retries. Verified by re-reading the same predicate inside the
    transaction; the previous values are written to a restore .sql first."""
    rows = fetch_mismatches(conn, days, today)
    if not rows:
        return 0, None, rows
    restore_dir.mkdir(parents=True, exist_ok=True)
    rpath = restore_dir / f"supporto-rebind-{dt.datetime.now():%Y%m%d-%H%M%S}.sql"
    rpath.write_text(restore_sql(rows))
    d0, d1 = _window(days, today)
    cur = conn.cursor()
    try:
        cur.execute(
            f"UPDATE t SET t.SUPPORTO = '{PREFIX}' + RTRIM(fs.FILE_ID),"
            " t.STATUS = CASE WHEN t.STATUS = 'E' THEN 'I' ELSE t.STATUS END"
            + mismatch_where(d0, d1)
        )
        n = cur.rowcount
        cur.execute("SELECT COUNT(*)" + mismatch_where(d0, d1))
        left = cur.fetchone()[0]
        if left:
            raise RuntimeError(f"{left} row(s) still mismatched after update")
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return n, rpath, rows


def rebind_asset(cur, asset_id: int, days: int = 400, today: dt.date | None = None) -> int:
    """Rebind one asset's unaired rows on the caller's cursor/transaction (the rename tool:
    the code just changed, the file did not). No CIB copy is required — the S3 master
    names the file every CIB will restore. Returns rows changed."""
    d0, d1 = _window(days, today)
    cur.execute(
        f"UPDATE t SET t.SUPPORTO = '{PREFIX}' + RTRIM(fs.FILE_ID),"
        " t.STATUS = CASE WHEN t.STATUS = 'E' THEN 'I' ELSE t.STATUS END"
        + mismatch_where(d0, d1, asset_id=asset_id)
    )
    return cur.rowcount


def format_report(result: dict, days: int) -> str:
    lines = [
        f"{result['count']} unaired row(s) bound to a file the asset does not have (next {days} days)"
    ]
    for r in result["rows"]:
        lines.append(
            f"  {r['market']:<3} {r['date'][5:]} {r['time']} {r['status']} {r['code'][:26]:<26}"
            f" bound={r['bound']!r:<28} file={r['file']!r}"
        )
    if result["count"] > len(result["rows"]):
        lines.append(f"  … {result['count'] - len(result['rows'])} more")
    return "\n".join(lines)
