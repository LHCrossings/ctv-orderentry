"""
Inspect a real Etere contract to resolve unknown field formats.
Run from Windows: py scripts/inspect_contract.py
"""
import pyodbc

CONTRACT_ID = 2381

conn = pyodbc.connect(
    "DRIVER={SQL Server};"
    "SERVER=etere-sql-server.tail98be.ts.net;"
    "DATABASE=Etere_crossing;"
    "Trusted_Connection=yes;"
)
cursor = conn.cursor()

# ── 1. Contract header ────────────────────────────────────────────────────────
print("=" * 60)
print("CONTRACT HEADER (CONTRATTITESTATA)")
print("=" * 60)
cursor.execute("SELECT * FROM CONTRATTITESTATA WHERE ID_CONTRATTITESTATA = ?", CONTRACT_ID)
cols = [d[0] for d in cursor.description]
row = cursor.fetchone()
if row:
    for col, val in zip(cols, row):
        if val is not None and val != '' and val != 0:
            print(f"  {col}: {val!r}")
else:
    print("  NOT FOUND")

# ── 2. Contract lines ─────────────────────────────────────────────────────────
print("\n" + "=" * 60)
print("CONTRACT LINES (CONTRATTIRIGHE)")
print("=" * 60)
cursor.execute("""
    SELECT * FROM CONTRATTIRIGHE
    WHERE ID_CONTRATTITESTATA = ?
    ORDER BY ID_CONTRATTIRIGHE
""", CONTRACT_ID)
cols = [d[0] for d in cursor.description]
rows = cursor.fetchall()
print(f"  ({len(rows)} lines)")
for row in rows[:3]:   # first 3 lines — enough to see patterns
    print()
    for col, val in zip(cols, row):
        if val is not None and val != '' and val != 0:
            print(f"  {col}: {val!r}")

# ── 3. Look up COD_USER values in use ─────────────────────────────────────────
print("\n" + "=" * 60)
print("COD_USER values in CONTRATTITESTATA")
print("=" * 60)
cursor.execute("""
    SELECT DISTINCT COD_USER FROM CONTRATTITESTATA
    WHERE COD_USER IS NOT NULL
    ORDER BY COD_USER
""")
for row in cursor.fetchall():
    print(f"  {row[0]!r}")

# ── 4. Try to find users table ────────────────────────────────────────────────
print("\n" + "=" * 60)
print("Tables with 'user' or 'utent' in name")
print("=" * 60)
cursor.execute("""
    SELECT TABLE_NAME FROM INFORMATION_SCHEMA.TABLES
    WHERE TABLE_TYPE = 'BASE TABLE'
      AND (TABLE_NAME LIKE '%user%' OR TABLE_NAME LIKE '%utent%'
           OR TABLE_NAME LIKE '%operato%')
    ORDER BY TABLE_NAME
""")
for row in cursor.fetchall():
    print(f"  {row[0]}")

# ── 5. ID_FatturaDescrizione lookup ───────────────────────────────────────────
print("\n" + "=" * 60)
print("ID_FatturaDescrizione values in CONTRATTIRIGHE")
print("=" * 60)
cursor.execute("""
    SELECT DISTINCT ID_FatturaDescrizione FROM CONTRATTIRIGHE
    WHERE ID_FatturaDescrizione IS NOT NULL
    ORDER BY ID_FatturaDescrizione
""")
for row in cursor.fetchall():
    print(f"  {row[0]!r}")

# ── 6. NEWTYPE values in use ──────────────────────────────────────────────────
print("\n" + "=" * 60)
print("NEWTYPE values in CONTRATTIRIGHE")
print("=" * 60)
cursor.execute("""
    SELECT DISTINCT NEWTYPE FROM CONTRATTIRIGHE
    WHERE NEWTYPE IS NOT NULL
    ORDER BY NEWTYPE
""")
for row in cursor.fetchall():
    print(f"  {row[0]!r}")

# ── 7. CONTRACTTYPE values in use ─────────────────────────────────────────────
print("\n" + "=" * 60)
print("CONTRACTTYPE values in CONTRATTITESTATA")
print("=" * 60)
cursor.execute("""
    SELECT DISTINCT CONTRACTTYPE FROM CONTRATTITESTATA
    WHERE CONTRACTTYPE IS NOT NULL
    ORDER BY CONTRACTTYPE
""")
for row in cursor.fetchall():
    print(f"  {row[0]!r}")

# ── 8. ORA_INIZIO / ORA_FINE sample values ────────────────────────────────────
print("\n" + "=" * 60)
print("ORA_INIZIO / ORA_FINE samples (first 20 distinct pairs)")
print("=" * 60)
cursor.execute("""
    SELECT DISTINCT TOP 20 ORA_INIZIO, ORA_FINE
    FROM CONTRATTIRIGHE
    WHERE ORA_INIZIO IS NOT NULL
    ORDER BY ORA_INIZIO
""")
for row in cursor.fetchall():
    print(f"  ORA_INIZIO={row[0]}  ORA_FINE={row[1]}")

cursor.close()
conn.close()
