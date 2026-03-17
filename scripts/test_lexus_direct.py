"""
Test EtereDirectClient with a real Lexus order.

Parses the XLSX, builds Etere lines, creates a TEST contract (code prefixed
with "TEST "), enters all lines, then ROLLS BACK — nothing is permanently written.

Run from Windows:
    git pull && py scripts/test_lexus_direct.py
"""
import sys
import os
from pathlib import Path
from datetime import date

# ── Path setup ───────────────────────────────────────────────────────────────
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))
sys.path.insert(0, str(project_root / "browser_automation"))

from browser_automation.etere_direct_client import (
    EtereDirectClient, connect, AGENCY_IDS, MEDIA_CENTER_IDS,
)
from browser_automation.parsers.lexus_parser import parse_lexus_file
from browser_automation.lexus_automation import _build_etere_lines, LEXUS_CUSTOMER_ID

# ── File ─────────────────────────────────────────────────────────────────────
_FILENAME = "NEW ORDER Lexus CY26 LDA - AI - NY EST 202 -Crossings r1.xlsx"
_WSL_PATH = Path(r"\\wsl.localhost\Ubuntu\home\scrib\dev\ctv-orderentry\incoming") / _FILENAME
FILE_PATH = _WSL_PATH if _WSL_PATH.exists() else project_root / "incoming" / _FILENAME
MARKET = "NYC"
TEST_CODE = "TEST LEXUS 202 NYC"
TEST_DESC = "TEST — Lexus NYC Est 202 (delete me)"

print("=" * 60)
print("LEXUS DIRECT DB TEST")
print("=" * 60)

# ── Parse ─────────────────────────────────────────────────────────────────────
print(f"\n[PARSE] {FILE_PATH.name}")
result = parse_lexus_file(FILE_PATH)
print(f"  Broadcast month : {result.broadcast_month}")
print(f"  Language        : {result.language}")
print(f"  Lines parsed    : {len(result.lines)}")

# Build Etere lines (no cutoff — enter everything)
etere_lines = _build_etere_lines(parse_result=result, include_bns=True, cutoff_date=None)
print(f"  Etere lines     : {len(etere_lines)}")
for i, ln in enumerate(etere_lines, 1):
    bns = " [BNS]" if ln["is_bonus"] else ""
    print(f"  {i:2}. {ln['days']:6} {ln['time']:15} "
          f"{ln['start_date']} – {ln['end_date']}  "
          f"{ln['total_spots']}x @ ${ln['rate']:.2f}{bns}")

if not etere_lines:
    print("\n[ERROR] No lines parsed — aborting")
    sys.exit(1)

# ── Connect ───────────────────────────────────────────────────────────────────
print(f"\n[DB] Connecting...")
conn = connect()
conn.autocommit = False
print("[DB] Connected.")

# ── Discover IW Group agency/media-center IDs from existing contracts ─────────
cursor = conn.cursor()
cursor.execute("""
    SELECT TOP 1 AGENZIA, CENTROMEDIA
    FROM CONTRATTITESTATA
    WHERE COD_CONTRATTO LIKE 'IW Lexus%'
    ORDER BY ID_CONTRATTITESTATA DESC
""")
row = cursor.fetchone()
if row:
    agency_id      = row[0] or 0
    media_center_id = row[1] or 0
    print(f"[DB] IW Group agency_id={agency_id}  media_center_id={media_center_id}")
else:
    # Fallback — use same as HL (also IW Group billing) or 0
    agency_id      = AGENCY_IDS.get("HL", 0)
    media_center_id = MEDIA_CENTER_IDS.get("HL", 0)
    print(f"[DB] No existing Lexus contracts found — using HL defaults: agency={agency_id} mc={media_center_id}")

try:
    client = EtereDirectClient(conn, owner="Charmaine Lane", autocommit=False)
    client.set_master_market("NYC")

    # ── Contract header ───────────────────────────────────────────────────────
    print(f"\n[HEADER] Creating test contract...")
    contract_id = client.create_contract_header(
        code=TEST_CODE,
        description=TEST_DESC,
        customer_id=LEXUS_CUSTOMER_ID,
        agency_id=agency_id,
        media_center_id=media_center_id,
        contract_date=date.today(),
        contract_end_date=max(ln["end_date"] for ln in etere_lines),
        contract_type=2,
        customer_order_ref=f"{result.estimate} {MARKET} {result.language}",
    )
    print(f"  → contract_id = {contract_id}")

    # ── Lines ─────────────────────────────────────────────────────────────────
    print(f"\n[LINES] Entering {len(etere_lines)} line(s)...")
    for i, ln in enumerate(etere_lines, 1):
        from etere_client import EtereClient
        days, _ = EtereClient.check_sunday_6_7a_rule(ln["days"], ln["time"])
        line_id = client.add_contract_line(
            market=MARKET,
            days=days,
            time_range=ln["time"],
            description=ln["description"],
            rate=ln["rate"],
            total_spots=ln["total_spots"],
            spots_per_week=ln["spots_per_week"],
            max_daily_run=ln["max_daily_run"],
            date_from=ln["start_date"],
            date_to=ln["end_date"],
            duration=f"00:00:{ln['duration']:02d}:00",
            is_bonus=ln["is_bonus"],
            separation_intervals=ln["separation"],
        )
        bns = " [BNS]" if ln["is_bonus"] else ""
        print(f"  {i:2}. line_id={line_id}  {ln['days']} {ln['time']}{bns}")

    print(f"\n✓ All {len(etere_lines)} lines entered successfully.")
    print(f"Contract #{contract_id} (TEST LEXUS 202 NYC) committed to DB — delete when done reviewing.")
    conn.commit()

except Exception as e:
    print(f"\n✗ Error: {e}")
    conn.rollback()
    raise
finally:
    conn.close()
