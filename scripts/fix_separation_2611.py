"""
Set separation to (0, 5, 0) on all 06:00-09:00 and 22:00-23:59 lines for contract 2611.

Usage:
    uv run python scripts/fix_separation_2611.py
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from browser_automation.etere_direct_client import connect

FPS            = 29.97
ORA_INIZIO_6   = round(6  * 3600 * FPS)          # 647352
ORA_FINE_9     = round(9  * 3600 * FPS)          # 971028
ORA_INIZIO_22  = round(22 * 3600 * FPS)          # 2373624
ORA_FINE_2359  = round((23 * 3600 + 59 * 60) * FPS)  # 2588402
SEP_10_MIN     = round(10 * 60 * FPS)            # 17982
CONTRACT_ID    = 2611


def main():
    with connect() as conn:
        cursor = conn.cursor()

        cursor.execute("""
            SELECT ID_CONTRATTIRIGHE, ORA_INIZIO, ORA_FINE,
                   Interv_Committente, INTERVALLO, INTERV_CONTRATTO
            FROM   CONTRATTIRIGHE
            WHERE  ID_CONTRATTITESTATA = ?
              AND (
                (ORA_INIZIO = ? AND ORA_FINE = ?)   -- 06:00-09:00
                OR
                (ORA_INIZIO = ? AND ORA_FINE = ?)   -- 22:00-23:59
              )
        """, [CONTRACT_ID,
              ORA_INIZIO_6, ORA_FINE_9,
              ORA_INIZIO_22, ORA_FINE_2359])
        rows = cursor.fetchall()

        if not rows:
            print(f"[INFO] No matching lines found on contract {CONTRACT_ID}.")
            return

        print(f"[INFO] Found {len(rows)} line(s) to update:")
        for row in rows:
            from browser_automation.etere_direct_client import _to_frames
            h_start = row[1] / FPS / 3600
            h_end   = row[2] / FPS / 3600
            print(f"  line {row[0]}: {row[1]}({h_start:.1f}h) – {row[2]}({h_end:.1f}h)  "
                  f"sep=({row[3]}, {row[4]}, {row[5]})")

        line_ids = [row[0] for row in rows]
        placeholders = ",".join("?" * len(line_ids))
        cursor.execute(f"""
            UPDATE CONTRATTIRIGHE
            SET    Interv_Committente = ?,
                   INTERVALLO         = ?,
                   INTERV_CONTRATTO   = ?
            WHERE  ID_CONTRATTIRIGHE IN ({placeholders})
        """, [0, SEP_10_MIN, 0, *line_ids])

        conn.commit()
        print(f"\n[DONE] Updated {cursor.rowcount} line(s) → separation (0, 10, 0).")


if __name__ == "__main__":
    main()
