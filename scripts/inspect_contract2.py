"""
Follow-up inspection: bonus lines, users, SP definition, ORA_FINE mystery.
Run from Windows: py scripts/inspect_contract2.py
"""
import pyodbc

conn = pyodbc.connect(
    "DRIVER={SQL Server};"
    "SERVER=etere-sql-server.tail98be.ts.net;"
    "DATABASE=Etere_crossing;"
    "Trusted_Connection=yes;"
)
cursor = conn.cursor()

# ── 1. Find a bonus line and see NEWTYPE / OMAGGIO ────────────────────────────
print("=" * 60)
print("BONUS LINES (OMAGGIO=1 or NEWTYPE like BNS)")
print("=" * 60)
cursor.execute("""
    SELECT TOP 3
        ID_CONTRATTIRIGHE, DESCRIZIONE, NEWTYPE, OMAGGIO,
        IMPORTO, ORA_INIZIO, ORA_INIZIOF, ORA_FINE, ORA_FINEF
    FROM CONTRATTIRIGHE
    WHERE OMAGGIO = 1 OR NEWTYPE IN ('BNS', 'BNS;COMS', 'BNS;COM;COMS')
""")
cols = [d[0] for d in cursor.description]
for row in cursor.fetchall():
    for col, val in zip(cols, row):
        print(f"  {col}: {val!r}")
    print()

# ── 2. Users table ────────────────────────────────────────────────────────────
print("=" * 60)
print("Users table")
print("=" * 60)
cursor.execute("SELECT TOP 20 * FROM Users ORDER BY 1")
cols = [d[0] for d in cursor.description]
print("  Columns:", cols)
for row in cursor.fetchall():
    print(" ", dict(zip(cols, row)))

# ── 3. ORA_FINE mystery — find lines where ORA_FINE != ORA_INIZIO ─────────────
print("\n" + "=" * 60)
print("Lines where ORA_FINE != ORA_INIZIO (top 5)")
print("=" * 60)
cursor.execute("""
    SELECT TOP 5
        ID_CONTRATTIRIGHE, DESCRIZIONE,
        ORA_INIZIO, ORA_FINE, ORA_INIZIOF, ORA_FINEF
    FROM CONTRATTIRIGHE
    WHERE ORA_FINE != ORA_INIZIO AND ORA_FINE IS NOT NULL
    ORDER BY ID_CONTRATTIRIGHE DESC
""")
cols = [d[0] for d in cursor.description]
for row in cursor.fetchall():
    for col, val in zip(cols, row):
        print(f"  {col}: {val!r}")
    print()

# ── 4. InsertContractLine SP definition (first 200 lines) ─────────────────────
print("=" * 60)
print("web_sales_InsertContractLine SP source (first 200 lines)")
print("=" * 60)
cursor.execute("""
    SELECT ROUTINE_DEFINITION
    FROM INFORMATION_SCHEMA.ROUTINES
    WHERE ROUTINE_NAME = 'web_sales_InsertContractLine'
""")
row = cursor.fetchone()
if row and row[0]:
    lines = row[0].split('\n')
    for ln in lines[:200]:
        print(ln)
else:
    print("  (definition not accessible or truncated)")

# ── 5. savecontractgeneral SP source (first 200 lines) ───────────────────────
print("\n" + "=" * 60)
print("web_sales_savecontractgeneral SP source (first 200 lines)")
print("=" * 60)
cursor.execute("""
    SELECT ROUTINE_DEFINITION
    FROM INFORMATION_SCHEMA.ROUTINES
    WHERE ROUTINE_NAME = 'web_sales_savecontractgeneral'
""")
row = cursor.fetchone()
if row and row[0]:
    lines = row[0].split('\n')
    for ln in lines[:200]:
        print(ln)
else:
    print("  (definition not accessible or truncated)")

cursor.close()
conn.close()
