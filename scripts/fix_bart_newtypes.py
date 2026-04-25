"""
Fix TPALINSE.NEWTYPE for portal-assigned spots that were incorrectly set to BART.

Covers all contracts — not just 2655. Any spot in trafficPalinse where the
contract line's booking code maps to a known type (COM, BNS, AV, etc.) but
TPALINSE.NEWTYPE is BART will be corrected.

Run with:
    uv run python scripts/fix_bart_newtypes.py
"""
import sys
sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent.parent))

from browser_automation.etere_direct_client import connect


def main():
    with connect() as conn:
        cur = conn.cursor(as_dict=True)

        # Preview: show affected contracts and counts
        cur.execute("""
            SELECT ct.ID_CONTRATTITESTATA,
                   ct.COD_CONTRATTO,
                   bc.code  AS correct_type,
                   COUNT(*) AS spots
            FROM TPALINSE tp
            JOIN trafficPalinse tpa ON tpa.id_tpalinse       = tp.ID_TPALINSE
            JOIN CONTRATTIRIGHE cr  ON cr.ID_CONTRATTIRIGHE  = tpa.id_contrattirighe
            JOIN CONTRATTITESTATA ct ON ct.ID_CONTRATTITESTATA = cr.ID_CONTRATTITESTATA
            JOIN trf_bookingcode bc  ON bc.id_bookingcode    = cr.ID_BOOKINGCODE
            WHERE tp.NEWTYPE = 'BART'
              AND bc.code != 'BART'
            GROUP BY ct.ID_CONTRATTITESTATA, ct.COD_CONTRATTO, bc.code
            ORDER BY ct.ID_CONTRATTITESTATA
        """)
        rows = cur.fetchall()
        if not rows:
            print("No BART spots need fixing.")
            return

        print("Contracts with BART spots to fix:")
        total = 0
        for r in rows:
            print(f"  [{r['ID_CONTRATTITESTATA']}] {r['COD_CONTRATTO']}"
                  f"  →  {r['spots']} spots  BART → {r['correct_type']}")
            total += r["spots"]
        print(f"\nTotal: {total} spots")

        confirm = input("\nApply fix? [y/N] ").strip().lower()
        if confirm != "y":
            print("Aborted.")
            return

        # Apply: set NEWTYPE from the booking code table
        cur2 = conn.cursor()
        cur2.execute("""
            UPDATE tp
            SET    tp.NEWTYPE = bc.code
            FROM   TPALINSE tp
            JOIN   trafficPalinse tpa ON tpa.id_tpalinse      = tp.ID_TPALINSE
            JOIN   CONTRATTIRIGHE cr  ON cr.ID_CONTRATTIRIGHE = tpa.id_contrattirighe
            JOIN   trf_bookingcode bc  ON bc.id_bookingcode   = cr.ID_BOOKINGCODE
            WHERE  tp.NEWTYPE = 'BART'
              AND  bc.code   != 'BART'
        """)
        conn.commit()
        print(f"Fixed {cur2.rowcount} spots.")


if __name__ == "__main__":
    main()
