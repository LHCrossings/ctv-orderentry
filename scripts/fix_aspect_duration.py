"""
Fix TPALINSE.ASPECT and TPALINSE.DURATION_P for portal-assigned spots.

Native Etere sets both from the media library (FS_FILMATI):
  ASPECT     — derived from VIDEOSTANDARD ("D" → "H")
  DURATION_P — actual file duration in frames (FS_FILMATI.DUR)

Our portal left ASPECT="4" (wrong) and DURATION_P=NULL for all assignments.

Run with:
    uv run python scripts/fix_aspect_duration.py
"""
import sys
sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent.parent))

from browser_automation.etere_direct_client import connect


def main():
    with connect() as conn:
        cur = conn.cursor(as_dict=True)

        # Preview
        cur.execute("""
            SELECT ct.COD_CONTRATTO,
                   COUNT(*) AS spots
            FROM TPALINSE tp
            JOIN trafficPalinse tpa ON tpa.id_tpalinse      = tp.ID_TPALINSE
            JOIN CONTRATTIRIGHE cr  ON cr.ID_CONTRATTIRIGHE = tpa.id_contrattirighe
            JOIN CONTRATTITESTATA ct ON ct.ID_CONTRATTITESTATA = cr.ID_CONTRATTITESTATA
            JOIN FS_FILMATI ff      ON ff.ID_FILMATI        = tp.ID_FILMATI
            WHERE (tp.ASPECT != 'H' OR tp.DURATION_P IS NULL)
              AND tp.ID_FILMATI > 0
            GROUP BY ct.COD_CONTRATTO
            ORDER BY ct.COD_CONTRATTO
        """)
        rows = cur.fetchall()
        if not rows:
            print("Nothing to fix.")
            return

        total = sum(r["spots"] for r in rows)
        print(f"Contracts needing ASPECT/DURATION_P fix ({total} rows):")
        for r in rows:
            print(f"  {r['COD_CONTRATTO']}: {r['spots']}")

        confirm = input("\nApply fix? [y/N] ").strip().lower()
        if confirm != "y":
            print("Aborted.")
            return

        cur2 = conn.cursor()
        cur2.execute("""
            UPDATE tp
            SET tp.ASPECT     = CASE ISNULL(ff.VIDEOSTANDARD, 'D')
                                    WHEN 'D' THEN 'H'
                                    ELSE 'H'
                                END,
                tp.DURATION_P = ff.DUR
            FROM TPALINSE tp
            JOIN trafficPalinse tpa ON tpa.id_tpalinse = tp.ID_TPALINSE
            JOIN FS_FILMATI ff      ON ff.ID_FILMATI   = tp.ID_FILMATI
            WHERE (tp.ASPECT != 'H' OR tp.DURATION_P IS NULL)
              AND tp.ID_FILMATI > 0
        """)
        conn.commit()
        print(f"Fixed {cur2.rowcount} spots.")


if __name__ == "__main__":
    main()
