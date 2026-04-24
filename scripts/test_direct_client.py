"""
Smoke test for EtereDirectClient.
Creates ONE contract with ONE line, prints the IDs, then rolls back.

Run from Windows: py scripts/test_direct_client.py
"""
import sys

sys.path.insert(0, '.')

from datetime import date

from browser_automation.etere_direct_client import (
    AGENCY_IDS,
    MEDIA_CENTER_IDS,
    EtereDirectClient,
    connect,
)

print("Connecting to Etere SQL Server...")
conn = connect()
print("Connected.\n")

# ── Use a transaction we can roll back so nothing is committed ─────────────────
conn.autocommit = False

try:
    client = EtereDirectClient(conn, owner="Charmaine Lane", autocommit=False)
    client.set_master_market("NYC")

    print("Creating test contract header...")
    contract_id = client.create_contract_header(
        code="TEST DIRECT 001",
        description="Direct DB test — delete me",
        customer_id=68,          # Thunder Valley Casino
        agency_id=AGENCY_IDS["RPM"],
        media_center_id=MEDIA_CENTER_IDS["RPM"],
        contract_date=date.today(),
        contract_type=2,
    )
    print(f"  → contract_id = {contract_id}")

    print("\nAdding test line...")
    line_id = client.add_contract_line(
        market="SFO",
        days="M-F",
        time_range="06:00-07:00",
        description="M-F Mandarin News 6a-7a",
        rate=120.0,
        total_spots=4,
        spots_per_week=2,
        max_daily_run=1,
        date_from=date(2026, 6, 1),
        date_to=date(2026, 6, 7),
        duration="00:00:30:00",
        is_bonus=False,
        separation_intervals=(25, 0, 0),
    )
    print(f"  → line_id = {line_id}")

    print("\n✓ Both calls succeeded.")
    print("Rolling back — no data was permanently written.")
    conn.rollback()

except Exception as e:
    print(f"\n✗ Error: {e}")
    conn.rollback()
    raise
finally:
    conn.close()
