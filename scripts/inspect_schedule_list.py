"""
Inspect Traffic_ScheduleList rows for a contract, optionally filtered by date range.
Read-only — no changes are made.

Usage:
    uv run python scripts/inspect_schedule_list.py <contract_id> [from_date] [to_date]

Examples:
    uv run python scripts/inspect_schedule_list.py 2551
    uv run python scripts/inspect_schedule_list.py 2551 04/01/2025 04/30/2025
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from browser_automation.etere_direct_client import connect


def main():
    contract_id = int(sys.argv[1]) if len(sys.argv) > 1 else None
    if not contract_id:
        print("Usage: uv run python scripts/inspect_schedule_list.py <contract_id> [from_date] [to_date]")
        sys.exit(1)

    date_from = sys.argv[2] if len(sys.argv) > 2 else None
    date_to   = sys.argv[3] if len(sys.argv) > 3 else None

    with connect() as conn:
        cursor = conn.cursor()

        # Build query — join to CONTRATTIRIGHE to filter by contract header
        params = [contract_id]
        date_filter = ""
        if date_from:
            date_filter += " AND sl.Date >= ?"
            params.append(date_from)
        if date_to:
            date_filter += " AND sl.Date <= ?"
            params.append(date_to)

        cursor.execute(f"""
            SELECT sl.ID_TrafficScheduleList,
                   sl.ID_ContrattiRighe,
                   cr.DESCRIZIONE,
                   sl.Date,
                   sl.ExitDate,
                   sl.ToDate,
                   sl.PassageMiss,
                   sl.Notes
            FROM   Traffic_ScheduleList sl
            JOIN   CONTRATTIRIGHE cr ON cr.ID_CONTRATTIRIGHE = sl.ID_ContrattiRighe
            WHERE  cr.ID_CONTRATTITESTATA = ?
            {date_filter}
            ORDER  BY sl.Date, sl.ID_ContrattiRighe
        """, params)

        rows = cursor.fetchall()

    if not rows:
        print(f"[INFO] No scheduled entries found for contract {contract_id}"
              + (f" between {date_from} and {date_to}" if date_from else "") + ".")
        return

    print(f"[INFO] {len(rows)} scheduled entry(ies) for contract {contract_id}"
          + (f" between {date_from} and {date_to}" if date_from else "") + ":\n")
    print(f"{'ScheduleID':>12}  {'LineID':>8}  {'Date':>12}  {'ExitDate':>12}  Description")
    print("-" * 80)
    for row in rows:
        sl_id, line_id, desc, date, exit_date, to_date, miss, notes = row
        date_str      = date.strftime("%m/%d/%Y")      if date      else "—"
        exit_date_str = exit_date.strftime("%m/%d/%Y") if exit_date else "—"
        print(f"{sl_id:>12}  {line_id:>8}  {date_str:>12}  {exit_date_str:>12}  {desc or ''}")


if __name__ == "__main__":
    main()
