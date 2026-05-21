"""
Inspect all columns of a TPALINSE row to find the deletion flag.
Looks for spots matching a TITLE fragment on a given date/market.

Usage:
    uv run python scripts/inspect_ghost_spot.py "Horsepower Duck" 2026-05-21 3
    uv run python scripts/inspect_ghost_spot.py "Knightline" 2026-05-21 3
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from browser_automation.etere_direct_client import connect

title_frag = sys.argv[1] if len(sys.argv) > 1 else "Horsepower Duck"
date       = sys.argv[2] if len(sys.argv) > 2 else "2026-05-21"
cod_user   = int(sys.argv[3]) if len(sys.argv) > 3 else 3

with connect() as conn:
    cur = conn.cursor(as_dict=True)

    # Get all column names for TPALINSE
    cur.execute("""
        SELECT COLUMN_NAME
        FROM INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_NAME = 'TPALINSE'
        ORDER BY ORDINAL_POSITION
    """)
    all_cols = [r["COLUMN_NAME"] for r in cur.fetchall()]
    print(f"TPALINSE columns ({len(all_cols)}):")
    print("  " + ", ".join(all_cols))
    print()

    # Fetch matching rows
    cur.execute(
        "SELECT * FROM TPALINSE WHERE DATA = %s AND COD_USER = %d AND TITLE LIKE %s",
        (date, cod_user, f"%{title_frag}%")
    )
    rows = cur.fetchall()
    print(f"Found {len(rows)} row(s) matching '{title_frag}' on {date} COD_USER={cod_user}:")
    for row in rows:
        print()
        for col in all_cols:
            val = row.get(col)
            if val is not None and val != "" and val != 0:
                print(f"  {col:30s} = {val!r}")
        print("  --- zero/null/empty fields omitted ---")

    # Also check trafficTPalinse for these IDs
    if rows:
        ids = [r["ID_TPALINSE"] for r in rows]
        id_str = ",".join(str(i) for i in ids)

        cur2 = conn.cursor(as_dict=True)
        cur2.execute("""
            SELECT COLUMN_NAME
            FROM INFORMATION_SCHEMA.COLUMNS
            WHERE TABLE_NAME = 'trafficTPalinse'
            ORDER BY ORDINAL_POSITION
        """)
        tp_cols = [r["COLUMN_NAME"] for r in cur2.fetchall()]
        print(f"\ntrafficTPalinse columns: {', '.join(tp_cols)}")

        cur2.execute(f"SELECT * FROM trafficTPalinse WHERE ID_TPalinse IN ({id_str})")
        tp_rows = cur2.fetchall()
        print(f"\ntrafficTPalinse rows for these IDs ({len(tp_rows)}):")
        for row in tp_rows:
            print()
            for col in tp_cols:
                val = row.get(col)
                if val is not None and val != "" and val != 0:
                    print(f"  {col:30s} = {val!r}")
            print("  --- zero/null/empty fields omitted ---")
