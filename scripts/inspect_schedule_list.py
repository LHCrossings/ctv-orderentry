"""
Inspect Traffic_ScheduleList rows for a contract, optionally filtered by date range.
Read-only — no changes are made.

Usage:
    uv run python scripts/inspect_schedule_list.py <contract_id> [from_date] [to_date]

Examples:
    uv run python scripts/inspect_schedule_list.py 2551
    uv run python scripts/inspect_schedule_list.py 2551 04/01/2025 04/30/2025
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from browser_automation.etere_direct_client import connect


def main():
    contract_id = int(sys.argv[1]) if len(sys.argv) > 1 else None
    if not contract_id:
        print("Usage: uv run python scripts/inspect_schedule_list.py <contract_id> [from_date] [to_date]")
        sys.exit(1)

    sys.argv[2] if len(sys.argv) > 2 else None
    sys.argv[3] if len(sys.argv) > 3 else None

    with connect() as conn:
        cursor = conn.cursor()

        def count_rows(table, date_col, line_col):
            cursor.execute(f"""
                SELECT COUNT(*)
                FROM   {table} t
                JOIN   CONTRATTIRIGHE cr ON cr.ID_CONTRATTIRIGHE = t.{line_col}
                WHERE  cr.ID_CONTRATTITESTATA = ?
            """, [contract_id])
            return cursor.fetchone()[0]

        tp_count  = count_rows("trafficPalinse",    "Date",   "ID_ContrattiRighe")
        ms_count  = count_rows("Traffic_Manualsched", "DATA_P", "ID_CONTRATTIRIGHE")
        tsl_count = count_rows("Traffic_ScheduleList", "Date",  "ID_ContrattiRighe")

        print(f"[INFO] Contract {contract_id} row counts:")
        print(f"  trafficPalinse    : {tp_count}")
        print(f"  Traffic_Manualsched: {ms_count}")
        print(f"  Traffic_ScheduleList: {tsl_count}")
        print()

        # Show sample rows from whichever table has data
        if tp_count > 0:
            cursor.execute("""
                SELECT TOP 10 t.id_trafficPalinse, t.ID_ContrattiRighe, cr.DESCRIZIONE, t.Date, t.scadenza
                FROM   trafficPalinse t
                JOIN   CONTRATTIRIGHE cr ON cr.ID_CONTRATTIRIGHE = t.ID_ContrattiRighe
                WHERE  cr.ID_CONTRATTITESTATA = ?
                ORDER  BY t.Date, t.ID_ContrattiRighe
            """, [contract_id])
            rows = cursor.fetchall()
            print("[INFO] trafficPalinse sample (first 10):")
            print(f"{'ID':>10}  {'LineID':>8}  {'Date':>12}  {'Scadenza':>12}  Description")
            print("-" * 70)
            for row in rows:
                rid, lid, desc, date, scad = row
                print(f"{rid:>10}  {lid:>8}  "
                      f"{date.strftime('%m/%d/%Y') if date else '—':>12}  "
                      f"{scad.strftime('%m/%d/%Y') if scad else '—':>12}  {desc or ''}")
            print()

        if ms_count > 0:
            cursor.execute("""
                SELECT TOP 10 t.ID_MANUALSCHED, t.ID_CONTRATTIRIGHE, cr.DESCRIZIONE, t.DATA_P
                FROM   Traffic_Manualsched t
                JOIN   CONTRATTIRIGHE cr ON cr.ID_CONTRATTIRIGHE = t.ID_CONTRATTIRIGHE
                WHERE  cr.ID_CONTRATTITESTATA = ?
                ORDER  BY t.DATA_P, t.ID_CONTRATTIRIGHE
            """, [contract_id])
            rows = cursor.fetchall()
            print("[INFO] Traffic_Manualsched sample (first 10):")
            print(f"{'ID':>10}  {'LineID':>8}  {'Date':>12}  Description")
            print("-" * 60)
            for row in rows:
                rid, lid, desc, date = row
                print(f"{rid:>10}  {lid:>8}  "
                      f"{date.strftime('%m/%d/%Y') if date else '—':>12}  {desc or ''}")


if __name__ == "__main__":
    main()
