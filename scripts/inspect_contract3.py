"""
Final lookup: SP parameters, agency/media center IDs, other constants.
Run from Windows: py scripts/inspect_contract3.py
"""
import pyodbc

conn = pyodbc.connect(
    "DRIVER={SQL Server};"
    "SERVER=etere-sql-server.tail98be.ts.net;"
    "DATABASE=Etere_crossing;"
    "Trusted_Connection=yes;"
)
cursor = conn.cursor()

# ── 1. SP parameter names ──────────────────────────────────────────────────────
print("=" * 60)
print("web_sales_InsertContractLine parameters")
print("=" * 60)
cursor.execute("""
    SELECT PARAMETER_NAME, DATA_TYPE, PARAMETER_MODE
    FROM INFORMATION_SCHEMA.PARAMETERS
    WHERE SPECIFIC_NAME = 'web_sales_InsertContractLine'
    ORDER BY ORDINAL_POSITION
""")
for row in cursor.fetchall():
    print(f"  {row[2]:6} {row[0]:40} {row[1]}")

print("\n" + "=" * 60)
print("web_sales_savecontractgeneral parameters")
print("=" * 60)
cursor.execute("""
    SELECT PARAMETER_NAME, DATA_TYPE, PARAMETER_MODE
    FROM INFORMATION_SCHEMA.PARAMETERS
    WHERE SPECIFIC_NAME = 'web_sales_savecontractgeneral'
    ORDER BY ORDINAL_POSITION
""")
for row in cursor.fetchall():
    print(f"  {row[2]:6} {row[0]:40} {row[1]}")

# ── 2. Agency table ────────────────────────────────────────────────────────────
print("\n" + "=" * 60)
print("Agency table (looking for RPM, Impact, Imprenta, etc.)")
print("=" * 60)
cursor.execute("""
    SELECT TABLE_NAME FROM INFORMATION_SCHEMA.TABLES
    WHERE TABLE_NAME LIKE '%agenz%' OR TABLE_NAME LIKE '%agency%'
      OR TABLE_NAME LIKE '%anagrafi%'
    ORDER BY TABLE_NAME
""")
tables = [r[0] for r in cursor.fetchall()]
print("  Tables:", tables)

if tables:
    for tbl in tables[:2]:
        print(f"\n  -- {tbl} (top 20, key cols) --")
        try:
            cursor.execute(f"SELECT TOP 20 * FROM {tbl}")
            cols = [d[0] for d in cursor.description]
            print("  Cols:", cols)
            for row in cursor.fetchall():
                d = dict(zip(cols, row))
                # Show only non-null text-ish fields
                out = {k: v for k, v in d.items()
                       if v is not None and isinstance(v, (str, int)) and v != 0}
                print(" ", out)
        except Exception as e:
            print(f"  Error: {e}")

# ── 3. AGENZIA and CENTROMEDIA distinct values per market from existing contracts ─
print("\n" + "=" * 60)
print("AGENZIA / CENTROMEDIA by contract (sample from real contracts)")
print("=" * 60)
cursor.execute("""
    SELECT TOP 30
        COD_CONTRATTO, AGENZIA, CENTROMEDIA, AGENTE1, P_AGENZIA,
        FATTURAZIONE_PRINCIPALE, INVOICEMODE, CONTRACTTYPE
    FROM CONTRATTITESTATA
    WHERE COD_CONTRATTO LIKE 'RPM%' OR COD_CONTRATTO LIKE 'IMP%'
       OR COD_CONTRATTO LIKE 'IMPACT%' OR COD_CONTRATTO LIKE 'SAGENT%'
       OR COD_CONTRATTO LIKE 'GF%' OR COD_CONTRATTO LIKE 'HL%'
    ORDER BY ID_CONTRATTITESTATA DESC
""")
cols = [d[0] for d in cursor.description]
for row in cursor.fetchall():
    print(" ", dict(zip(cols, row)))

# ── 4. NIELSEN table ──────────────────────────────────────────────────────────
print("\n" + "=" * 60)
print("NIELSEN / ID_NIELSEN lookup")
print("=" * 60)
cursor.execute("""
    SELECT TABLE_NAME FROM INFORMATION_SCHEMA.TABLES
    WHERE TABLE_NAME LIKE '%nielsen%' OR TABLE_NAME LIKE '%target%' OR TABLE_NAME LIKE '%demo%'
    ORDER BY TABLE_NAME
""")
for row in cursor.fetchall():
    print(" ", row[0])

cursor.close()
conn.close()
