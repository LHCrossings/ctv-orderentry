"""
Find which database contains the real FASCE/blocks data.
Run from Windows: py scripts/discover_block_refresh4.py
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
from browser_automation.etere_direct_client import connect

KNOWN_IDS = [11202, 11208, 11958, 11959, 9898, 13530, 11205, 11206]
KNOWN_IDS_STR = ",".join(str(i) for i in KNOWN_IDS)

conn = connect()
cursor = conn.cursor()

# ── 1. List all databases on the server ──────────────────────────────────────
print("=" * 60)
print("All databases on server")
print("=" * 60)
cursor.execute("SELECT name FROM sys.databases ORDER BY name")
databases = [r[0] for r in cursor.fetchall()]
for db in databases:
    print(f"  {db}")

# ── 2. Search each accessible DB for a FASCE table with our known IDs ─────────
print("\n" + "=" * 60)
print("Searching for FASCE table with known ID_FASCE values")
print("=" * 60)
for db in databases:
    if db in ('master', 'tempdb', 'model', 'msdb'):
        continue
    try:
        cursor.execute(f"""
            SELECT COUNT(*) FROM [{db}].INFORMATION_SCHEMA.TABLES
            WHERE TABLE_NAME = 'FASCE'
        """)
        if cursor.fetchone()[0] == 0:
            continue
        # Found FASCE in this DB — check if it has our IDs
        cursor.execute(f"SELECT COUNT(*) FROM [{db}].dbo.FASCE WHERE ID_FASCE IN ({KNOWN_IDS_STR})")
        matches = cursor.fetchone()[0]
        cursor.execute(f"SELECT COUNT(*) FROM [{db}].dbo.FASCE")
        total = cursor.fetchone()[0]
        print(f"  [{db}] FASCE rows={total}  matches={matches}")
        if matches > 0:
            cursor.execute(f"SELECT TOP 3 * FROM [{db}].dbo.FASCE WHERE ID_FASCE IN ({KNOWN_IDS_STR})")
            cols = [d[0] for d in cursor.description]
            print(f"    Cols: {cols}")
            for row in cursor.fetchall():
                print(f"    {dict(zip(cols, row))}")
    except Exception as e:
        print(f"  [{db}] skipped: {e}")

cursor.close()
conn.close()
