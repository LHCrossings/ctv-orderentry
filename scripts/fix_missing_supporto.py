"""
Fix TPALINSE.SUPPORTO for portal-assigned spots that were left blank.

The warning "The selected event is not recorded" fires whenever SUPPORTO is
empty and a filmati is assigned.  Etere's native app sets SUPPORTO from
FS_FILMATI + FS_METADEVICE; our portal never did.

Format: LEGACY_BASESUPP + FILE_ID  (e.g. "0ETX      TOY30M1206")
If LEGACY_BASESUPP is NULL (AWS S3, LEGACY_MEDIAID=0), derives it as
"{LEGACY_MEDIAID}ETX      ".

Run with:
    uv run python scripts/fix_missing_supporto.py
"""
import sys
sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent.parent))

from browser_automation.etere_direct_client import connect


def main():
    with connect() as conn:
        cur = conn.cursor(as_dict=True)

        # Preview affected spots
        cur.execute("""
            SELECT ct.COD_CONTRATTO, COUNT(*) AS spots
            FROM TPALINSE tp
            JOIN trafficPalinse tpa ON tpa.id_tpalinse       = tp.ID_TPALINSE
            JOIN CONTRATTIRIGHE cr  ON cr.ID_CONTRATTIRIGHE  = tpa.id_contrattirighe
            JOIN CONTRATTITESTATA ct ON ct.ID_CONTRATTITESTATA = cr.ID_CONTRATTITESTATA
            JOIN FS_FILMATI ff      ON ff.ID_FILMATI         = tp.ID_FILMATI
            JOIN FS_METADEVICE d    ON d.ID_METADEVICE       = ff.ID_METADEVICE
            WHERE tp.SUPPORTO = ''
              AND tp.ID_FILMATI > 0
              AND d.LEGACY_MEDIAID IS NOT NULL
            GROUP BY ct.COD_CONTRATTO
            ORDER BY ct.COD_CONTRATTO
        """)
        rows = cur.fetchall()
        if not rows:
            print("No spots with missing SUPPORTO found.")
            return

        total = sum(r["spots"] for r in rows)
        print(f"Contracts with blank SUPPORTO ({total} spots total):")
        for r in rows:
            print(f"  {r['COD_CONTRATTO']}: {r['spots']} spots")

        confirm = input("\nApply fix? [y/N] ").strip().lower()
        if confirm != "y":
            print("Aborted.")
            return

        cur2 = conn.cursor()
        cur2.execute("""
            UPDATE tp
            SET tp.SUPPORTO =
                ISNULL(d.LEGACY_BASESUPP,
                       CAST(d.LEGACY_MEDIAID AS VARCHAR) + 'ETX      ')
                + ff.FILE_ID
            FROM TPALINSE tp
            JOIN trafficPalinse tpa ON tpa.id_tpalinse      = tp.ID_TPALINSE
            JOIN FS_FILMATI ff      ON ff.ID_FILMATI        = tp.ID_FILMATI
            JOIN FS_METADEVICE d    ON d.ID_METADEVICE      = ff.ID_METADEVICE
            WHERE tp.SUPPORTO = ''
              AND tp.ID_FILMATI > 0
              AND d.LEGACY_MEDIAID IS NOT NULL
        """)
        conn.commit()
        print(f"Fixed SUPPORTO on {cur2.rowcount} spots.")


if __name__ == "__main__":
    main()
