"""Find (and optionally fix) placed rows whose playout binding names a file the asset
no longer has — the NYC 9/2/2026 black screen.

TPALINSE.SUPPORTO must equal <prefix> + FS_FILMATI.FILE_ID for the CIB to find the media.
Etere's sch_UpdateSupportAndProperties builds it from COD_PROGRA instead, so any asset
renamed to its schedule code (scripts/rename-programming) and then placed carries a
binding to a non-existent file until the nightly aligner happens to rewrite it. Live
assets (LIVE_ID set) are excluded; only unaired rows (STATUS I/E) are touched.

    uv run python3 scripts/check_bindings.py            # report next 7 days
    uv run python3 scripts/check_bindings.py --days 3
    uv run python3 scripts/check_bindings.py --fix      # rebind, verified, restore SQL written
"""

import argparse
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "browser_automation"))

from browser_automation.etere_direct_client import connect  # noqa: E402

FPS = 29.97
PREFIX = "0ETX      "


def _hms(fr: int) -> str:
    s = fr / FPS
    return "%02d:%02d:%02d" % (s // 3600, (s % 3600) // 60, s % 60)


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--days", type=int, default=7, help="look-ahead from today (default 7)")
    ap.add_argument("--fix", action="store_true", help="rebind mismatched unaired rows")
    a = ap.parse_args()
    conn = connect()
    cur = conn.cursor()
    where = f"""
        FROM TPALINSE t JOIN FILMATI f ON f.ID_FILMATI = t.ID_FILMATI
        CROSS APPLY (SELECT TOP 1 FILE_ID FROM FS_FILMATI x
                     WHERE x.ID_FILMATI = t.ID_FILMATI AND x.ID_METADEVICE <> 6
                     ORDER BY x.LASTUPDATE DESC) fs
        WHERE t.DATA BETWEEN CAST(GETDATE() AS date) AND DATEADD(day, {int(a.days)}, CAST(GETDATE() AS date))
          AND t.LIVELLO = 0 AND t.COD_USER BETWEEN 1 AND 10 AND f.LIVE_ID IS NULL AND t.ID_FILMATI > 0
          AND RTRIM(t.SUPPORTO) <> '{PREFIX}' + RTRIM(fs.FILE_ID)
          AND t.STATUS IN ('I', 'E')"""
    cur.execute(
        "SELECT t.ID_TPALINSE, t.COD_USER, t.DATA, t.ORA, t.STATUS, RTRIM(t.COD_PROGRA), RTRIM(t.SUPPORTO), RTRIM(fs.FILE_ID) "
        + where
        + " ORDER BY t.DATA, t.COD_USER, t.ORA"
    )
    rows = cur.fetchall()
    print(
        f"{len(rows)} unaired row(s) bound to a file the asset no longer has (next {a.days} days)"
    )
    for r in rows:
        print(
            f"  m{r[1]:<2} {r[2]:%m/%d} {_hms(r[3])} {r[4]} {r[5][:26]:<26} bound={r[6][len(PREFIX) :]!r:<28} file={r[7]!r}"
        )
    if not rows or not a.fix:
        if rows:
            print("DRY RUN — add --fix to rebind")
        return
    rpath = Path("logs/finish-restore") / f"supporto-rebind-{datetime.now():%Y%m%d-%H%M%S}.sql"
    rpath.parent.mkdir(parents=True, exist_ok=True)
    with open(rpath, "w") as fh:
        for r in rows:
            fh.write(
                f"UPDATE TPALINSE SET SUPPORTO='{r[6]}', STATUS='{r[4]}' WHERE ID_TPALINSE={r[0]};\n"
            )
    try:
        cur.execute(
            f"UPDATE t SET t.SUPPORTO = '{PREFIX}' + RTRIM(fs.FILE_ID),"
            " t.STATUS = CASE WHEN t.STATUS = 'E' THEN 'I' ELSE t.STATUS END " + where
        )
        n = cur.rowcount
        cur.execute("SELECT COUNT(*) " + where)
        left = cur.fetchone()[0]
        if left:
            raise RuntimeError(f"{left} row(s) still mismatched after update")
        conn.commit()
        print(f"REBOUND {n} row(s); restore: {rpath}")
    except Exception as exc:  # noqa: BLE001
        conn.rollback()
        sys.exit(f"ROLLED BACK: {exc}")


if __name__ == "__main__":
    main()
