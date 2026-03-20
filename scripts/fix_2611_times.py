"""
One-off fix for contract 2611: lines entered as 22:00–02:00 should be 22:00–23:59,
and those same lines get separation (5, 0, 5).

Usage:
    uv run python scripts/fix_2611_times.py
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from browser_automation.etere_direct_client import connect

# Frame constants (29.97 fps)
FPS = 29.97
ORA_FINE_2AM   = round(26 * 3600 * FPS)  # 2805192 — Etere broadcast hour (2am = hour 26)
ORA_FINE_2359  = round((23 * 3600 + 59 * 60) * FPS)  # 2588402
SEP_5_MIN      = round(5 * 60 * FPS)     # 8991
CONTRACT_ID    = 2611


def frames_to_hhmm(f):
    secs = f / FPS
    h = int(secs // 3600)
    m = int((secs % 3600) // 60)
    return f"{h:02d}:{m:02d}"


def main():
    with connect() as conn:
        cursor = conn.cursor()

        # Diagnostic: show all ORA_FINE values on the contract
        cursor.execute("""
            SELECT ID_CONTRATTIRIGHE, ORA_INIZIO, ORA_FINE
            FROM   CONTRATTIRIGHE
            WHERE  ID_CONTRATTITESTATA = ?
            ORDER  BY ID_CONTRATTIRIGHE
        """, [CONTRACT_ID])
        all_rows = cursor.fetchall()
        print(f"[DIAG] All lines on contract {CONTRACT_ID}:")
        for r in all_rows:
            print(f"  line {r[0]}: {frames_to_hhmm(r[1])} – {frames_to_hhmm(r[2])}  "
                  f"(ORA_INIZIO={r[1]}, ORA_FINE={r[2]})")
        print()

        # Find affected lines
        cursor.execute("""
            SELECT ID_CONTRATTIRIGHE, ORA_INIZIO, ORA_FINE,
                   Interv_Committente, INTERVALLO, INTERV_CONTRATTO
            FROM   CONTRATTIRIGHE
            WHERE  ID_CONTRATTITESTATA = ?
              AND  ORA_FINE = ?
        """, [CONTRACT_ID, ORA_FINE_2AM])
        rows = cursor.fetchall()

        if not rows:
            print(f"[INFO] No lines found with ORA_FINE={ORA_FINE_2AM} on contract {CONTRACT_ID}.")
            return

        print(f"[INFO] Found {len(rows)} line(s) to fix:")
        for row in rows:
            print(f"  line {row[0]}: ORA_INIZIO={row[1]}, ORA_FINE={row[2]}, "
                  f"Interv_Committente={row[3]}, INTERVALLO={row[4]}, INTERV_CONTRATTO={row[5]}")

        # Apply fix
        line_ids = [row[0] for row in rows]
        placeholders = ",".join("?" * len(line_ids))
        cursor.execute(f"""
            UPDATE CONTRATTIRIGHE
            SET    ORA_FINE            = ?,
                   Interv_Committente  = ?,
                   INTERVALLO          = ?,
                   INTERV_CONTRATTO    = ?
            WHERE  ID_CONTRATTIRIGHE IN ({placeholders})
        """, [ORA_FINE_2359, SEP_5_MIN, 0, SEP_5_MIN, *line_ids])

        updated = cursor.rowcount
        conn.commit()
        print(f"\n[DONE] Updated {updated} line(s): ORA_FINE → {ORA_FINE_2359} (23:59), "
              f"separation → (5, 0, 5).")


if __name__ == "__main__":
    main()
