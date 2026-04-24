"""
Remove trailing asterisks from contract line descriptions.

Etere appends '*' characters to line descriptions after block operations.
This script strips them in bulk for a given contract.

Usage:
    uv run python scripts/clean_line_asterisks.py WL-25-001
    uv run python scripts/clean_line_asterisks.py 2630
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from browser_automation.etere_direct_client import connect

if len(sys.argv) < 2:
    print("Usage: uv run python scripts/clean_line_asterisks.py <contract_code_or_id>")
    sys.exit(1)

arg = sys.argv[1]
conn = connect()
cursor = conn.cursor()

# Resolve to ID_CONTRATTITESTATA — accept numeric ID or COD_CONTRATTO string
if arg.isdigit():
    contract_id = int(arg)
    cursor.execute("SELECT COD_CONTRATTO FROM CONTRATTITESTATA WHERE ID_CONTRATTITESTATA = ?", [contract_id])
    row = cursor.fetchone()
    if not row:
        print(f"No contract found with ID {contract_id}")
        sys.exit(1)
    contract_code = row[0]
else:
    contract_code = arg
    cursor.execute("SELECT ID_CONTRATTITESTATA FROM CONTRATTITESTATA WHERE COD_CONTRATTO = ?", [contract_code])
    row = cursor.fetchone()
    if not row:
        print(f"No contract found with code '{contract_code}'")
        sys.exit(1)
    contract_id = row[0]

# Preview affected lines
cursor.execute("""
    SELECT ID_CONTRATTIRIGHE, DESCRIZIONE
    FROM   CONTRATTIRIGHE
    WHERE  ID_CONTRATTITESTATA = ?
      AND  DESCRIZIONE LIKE '%*%'
""", [contract_id])
rows = cursor.fetchall()

if not rows:
    print(f"Contract {contract_code} (#{contract_id}): no lines with asterisks found.")
    sys.exit(0)

print(f"Contract {contract_code} (#{contract_id}): {len(rows)} line(s) to clean\n")
for line_id, desc in rows:
    cleaned = desc.replace('*', '').strip()
    print(f"  Line {line_id}: '{desc}' -> '{cleaned}'")

print()
confirm = input("Apply changes? [y/N] ").strip().lower()
if confirm != 'y':
    print("Aborted.")
    sys.exit(0)

cursor.execute("""
    UPDATE CONTRATTIRIGHE
    SET    DESCRIZIONE = RTRIM(LTRIM(REPLACE(DESCRIZIONE, '*', '')))
    WHERE  ID_CONTRATTITESTATA = ?
      AND  DESCRIZIONE LIKE '%*%'
""", [contract_id])
conn.commit()
print(f"Done — cleaned {len(rows)} line(s).")
