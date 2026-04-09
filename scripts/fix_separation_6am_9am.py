"""
Set separation to (5, 0, 5) on all 06:00-09:00 lines for a given contract.

Usage:
    uv run python scripts/fix_separation_6am_9am.py 2611
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from browser_automation.etere_direct_client import connect

FPS           = 29.97
ORA_INIZIO_6  = round(6 * 3600 * FPS)   # 647352
ORA_FINE_9    = round(9 * 3600 * FPS)   # 971028
SEP_5_MIN     = round(5 * 60 * FPS)     # 8991


def main():
    contract_id = int(sys.argv[1]) if len(sys.argv) > 1 else None
    if not contract_id:
        print("Usage: uv run python scripts/fix_separation_6am_9am.py <contract_id>")
        sys.exit(1)

    with connect() as conn:
        cursor = conn.cursor()

        cursor.execute("""
            SELECT ID_CONTRATTIRIGHE, Interv_Committente, INTERVALLO, INTERV_CONTRATTO
            FROM   CONTRATTIRIGHE
            WHERE  ID_CONTRATTITESTATA = ?
              AND  ORA_INIZIO = ?
              AND  ORA_FINE   = ?
        """, [contract_id, ORA_INIZIO_6, ORA_FINE_9])
        rows = cursor.fetchall()

        if not rows:
            print(f"[INFO] No 06:00-09:00 lines found on contract {contract_id}.")
            return

        print(f"[INFO] Found {len(rows)} line(s) to update:")
        for row in rows:
            print(f"  line {row[0]}: sep=({row[1]}, {row[2]}, {row[3]})")

        line_ids = [row[0] for row in rows]
        placeholders = ",".join("?" * len(line_ids))
        # Field mapping (current Etere UI):
        #   Interv_Committente = Customer
        #   INTERVALLO         = Order   (old Etere web had this swapped with INTERV_CONTRATTO)
        #   INTERV_CONTRATTO   = Event
        cursor.execute(f"""
            UPDATE CONTRATTIRIGHE
            SET    Interv_Committente = ?,
                   INTERVALLO         = ?,
                   INTERV_CONTRATTO   = ?
            WHERE  ID_CONTRATTIRIGHE IN ({placeholders})
        """, [SEP_5_MIN, 0, SEP_5_MIN, *line_ids])

        conn.commit()
        print(f"\n[DONE] Updated {cursor.rowcount} line(s) → separation (5, 0, 5).")


if __name__ == "__main__":
    main()
