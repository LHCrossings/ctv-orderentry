"""
Fix TPALINSE.NEWTYPE for all portal-assigned spots where the stored NEWTYPE
does not match the booking code on the contract line.

Covers any mismatch — not just BART (see fix_bart_newtypes.py for history).
Safe to run multiple times.

Run with:
    uv run python scripts/fix_wrong_newtypes.py
"""
import sys

sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent.parent))

from browser_automation.etere_direct_client import connect


def main():
    with connect() as conn:
        cur = conn.cursor(as_dict=True)

        cur.execute("""
            SELECT ct.ID_CONTRATTITESTATA,
                   ct.COD_CONTRATTO,
                   bc.code          AS correct_type,
                   tp.NEWTYPE       AS current_type,
                   COUNT(*)         AS spots
            FROM TPALINSE tp
            JOIN trafficPalinse tpa ON tpa.id_tpalinse      = tp.ID_TPALINSE
            JOIN CONTRATTIRIGHE cr  ON cr.ID_CONTRATTIRIGHE = tpa.id_contrattirighe
            JOIN CONTRATTITESTATA ct ON ct.ID_CONTRATTITESTATA = cr.ID_CONTRATTITESTATA
            JOIN trf_bookingcode bc  ON bc.id_bookingcode   = cr.ID_BOOKINGCODE
            WHERE tp.NEWTYPE != bc.code
              AND tp.ID_FILMATI IS NOT NULL
            GROUP BY ct.ID_CONTRATTITESTATA, ct.COD_CONTRATTO, bc.code, tp.NEWTYPE
            ORDER BY ct.ID_CONTRATTITESTATA
        """)
        rows = cur.fetchall()
        if not rows:
            print("No mismatched NEWTYPE spots found.")
            return

        print("Contracts with wrong NEWTYPE (portal-assigned spots only):")
        total = 0
        for r in rows:
            print(f"  [{r['ID_CONTRATTITESTATA']}] {r['COD_CONTRATTO']}"
                  f"  {r['current_type']} → {r['correct_type']}"
                  f"  ({r['spots']} spots)")
            total += r["spots"]
        print(f"\nTotal: {total} spots to correct")

        confirm = input("\nApply fix? [y/N] ").strip().lower()
        if confirm != "y":
            print("Aborted.")
            return

        cur2 = conn.cursor()
        cur2.execute("""
            UPDATE tp
            SET    tp.NEWTYPE = bc.code
            FROM   TPALINSE tp
            JOIN   trafficPalinse tpa ON tpa.id_tpalinse      = tp.ID_TPALINSE
            JOIN   CONTRATTIRIGHE cr  ON cr.ID_CONTRATTIRIGHE = tpa.id_contrattirighe
            JOIN   trf_bookingcode bc  ON bc.id_bookingcode   = cr.ID_BOOKINGCODE
            WHERE  tp.NEWTYPE != bc.code
              AND  tp.ID_FILMATI IS NOT NULL
        """)
        conn.commit()
        print(f"Fixed {cur2.rowcount} spots.")


if __name__ == "__main__":
    main()
