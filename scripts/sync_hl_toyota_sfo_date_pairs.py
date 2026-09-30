"""Finish the 9/30 trim of H/L Toyota SFO 4Q26 (3137-3139): CONTRATTIRIGHE carries TWO date
pairs. DATA_INIZIO/DATA_FINE were trimmed; DATESTART/DATEEND (what the line form shows and the
scheduler honours) were not. Set them equal, and set the header DATA_ACQUISIZIONE to its
DATA_INIZIO (181 of 202 recent headers keep them equal). Dry run by default; --apply commits.
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from browser_automation.etere_direct_client import connect  # noqa: E402

CONTRACTS = "3137,3138,3139"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    restore = (
        Path(__file__).resolve().parents[1]
        / "logs"
        / "hl-toyota-sfo-date-pairs-restore-20260930.sql"
    )
    lines = ["-- restores DATESTART/DATEEND and DATA_ACQUISIZIONE before the 9/30 sync"]
    with connect() as conn:
        cur = conn.cursor(as_dict=True)
        cur.execute(f"""SELECT ID_CONTRATTIRIGHE lid, CONVERT(VARCHAR(10),DATA_INIZIO,120) di, CONVERT(VARCHAR(10),DATESTART,120) dst,
            CONVERT(VARCHAR(10),DATA_FINE,120) df, CONVERT(VARCHAR(10),DATEEND,120) den
            FROM CONTRATTIRIGHE WHERE ID_CONTRATTITESTATA IN ({CONTRACTS}) AND (DATESTART<>DATA_INIZIO OR DATEEND<>DATA_FINE) ORDER BY 1""")
        rows = cur.fetchall()
        for r in rows:
            print(
                f"line {r['lid']}: DATESTART {r['dst']} -> {r['di']}, DATEEND {r['den']} -> {r['df']}"
            )
            lines.append(
                f"UPDATE CONTRATTIRIGHE SET DATESTART='{r['dst']}', DATEEND='{r['den']}' WHERE ID_CONTRATTIRIGHE={r['lid']};"
            )
        cur.execute(
            f"UPDATE CONTRATTIRIGHE SET DATESTART=DATA_INIZIO, DATEEND=DATA_FINE WHERE ID_CONTRATTITESTATA IN ({CONTRACTS}) AND (DATESTART<>DATA_INIZIO OR DATEEND<>DATA_FINE)"
        )
        n_lines = cur.rowcount
        cur.execute(
            f"SELECT ID_CONTRATTITESTATA id, CONVERT(VARCHAR(10),DATA_ACQUISIZIONE,120) acq, CONVERT(VARCHAR(10),DATA_INIZIO,120) ds FROM CONTRATTITESTATA WHERE ID_CONTRATTITESTATA IN ({CONTRACTS}) AND DATA_ACQUISIZIONE<>DATA_INIZIO"
        )
        hdrs = cur.fetchall()
        for h in hdrs:
            print(f"header {h['id']}: DATA_ACQUISIZIONE {h['acq']} -> {h['ds']}")
            lines.append(
                f"UPDATE CONTRATTITESTATA SET DATA_ACQUISIZIONE='{h['acq']}' WHERE ID_CONTRATTITESTATA={h['id']};"
            )
        cur.execute(
            f"UPDATE CONTRATTITESTATA SET DATA_ACQUISIZIONE=DATA_INIZIO WHERE ID_CONTRATTITESTATA IN ({CONTRACTS}) AND DATA_ACQUISIZIONE<>DATA_INIZIO"
        )
        n_hdr = cur.rowcount
        ok = n_lines == len(rows) == 17 and n_hdr == len(hdrs) == 3
        print(f"in txn: {n_lines} lines, {n_hdr} headers")
        if not (ok and args.apply):
            conn.rollback()
            print(
                "ROLLED BACK"
                + ("" if ok else " (unexpected counts)")
                + ("" if args.apply else " (dry run)")
            )
            return 0 if ok else 1
        restore.write_text("\n".join(lines) + "\n")
        conn.commit()
        print("COMMITTED; restore:", restore)
    with connect() as c2:
        cur = c2.cursor(as_dict=True)
        cur.execute(
            f"SELECT COUNT(*) n FROM CONTRATTIRIGHE WHERE ID_CONTRATTITESTATA IN ({CONTRACTS}) AND (DATESTART<>DATA_INIZIO OR DATEEND<>DATA_FINE)"
        )
        a = cur.fetchone()["n"]
        cur.execute(
            f"SELECT COUNT(*) n FROM CONTRATTITESTATA WHERE ID_CONTRATTITESTATA IN ({CONTRACTS}) AND DATA_ACQUISIZIONE<>DATA_INIZIO"
        )
        b = cur.fetchone()["n"]
        cur.execute(
            "SELECT ID_CONTRATTIRIGHE lid, CONVERT(VARCHAR(10),DATESTART,120) s, CONVERT(VARCHAR(10),DATEEND,120) e FROM CONTRATTIRIGHE WHERE ID_CONTRATTITESTATA=3137 ORDER BY 1"
        )
        print("fresh: lines out of step =", a, "; headers out of step =", b)
        print(
            "fresh 3137 form dates:", [(r["lid"], r["s"][5:], r["e"][5:]) for r in cur.fetchall()]
        )
        return 0 if a == b == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
