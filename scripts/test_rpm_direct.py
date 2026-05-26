"""
Test EtereDirectClient with RPM Thunder Valley order.

Parses AEInboxOrder (8).pdf, enters via direct DB with contract code
"RPM TEST ONLY". Commits to DB — delete the contract when done reviewing.

Run:  uv run python scripts/test_rpm_direct.py
"""
import sys
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from datetime import timedelta

from browser_automation.etere_direct_client import EtereDirectClient, connect
from browser_automation.parsers.rpm_parser import parse_rpm_pdf

sys.path.insert(0, str(project_root / "browser_automation"))
from etere_client import EtereClient


def _parse_rpm_daypart(daypart):
    parts = daypart.split(" ", 2)
    days = parts[0] if len(parts) > 0 else "M-F"
    time_range = parts[1] if len(parts) > 1 else "6a-12m"
    language = parts[2] if len(parts) > 2 else ""
    return days, time_range, language


def _duration_to_seconds(duration_str):
    parts = duration_str.split(":")
    return int(parts[2]) if len(parts) >= 3 else 30


def _consolidate_weeks(weekly_spots, week_dates, flight_end):
    blocks = []
    n = len(weekly_spots)
    i = 0
    while i < n:
        if weekly_spots[i] == 0:
            i += 1
            continue
        block_spots = weekly_spots[i]
        block_start = week_dates[i]
        prev_date = week_dates[i]
        count = 1
        j = i + 1
        while j < n:
            if weekly_spots[j] != block_spots:
                break
            if week_dates[j] != prev_date + timedelta(days=7):
                break
            prev_date = week_dates[j]
            count += 1
            j += 1
        block_end = min(prev_date + timedelta(days=6), flight_end)
        blocks.append((block_start, block_end, block_spots, block_spots * count))
        i = j
    return blocks

PDF_PATH   = project_root / "incoming" / "AEInboxOrder (8).pdf"
MARKET     = "CVC"
CUSTOMER_ID = 68
AGENCY_ID   = 67
MEDIA_CENTER_ID = 316
AGENCY_PCT  = 15.0
SEPARATION  = (25, 0, 0)
TEST_CODE   = "RPM TEST ONLY"
TEST_DESC   = "TEST — Thunder Valley RPM 10965 CVC (delete me)"

print("=" * 60)
print("RPM DIRECT DB TEST")
print("=" * 60)

order, lines = parse_rpm_pdf(str(PDF_PATH))
print(f"\n[PARSE] {PDF_PATH.name}")
print(f"  Client  : {order.client}")
print(f"  Estimate: {order.estimate_number}")
print(f"  Flight  : {order.flight_start} – {order.flight_end}")
print(f"  Lines   : {len(lines)} ({sum(ln.total_spots for ln in lines)} spots)")

conn = connect()
print("\n[DB] Connected.")

try:
    client = EtereDirectClient(conn, owner="Charmaine Lane", autocommit=True)
    client.set_master_market(MARKET)

    print("\n[HEADER] Creating contract...")
    contract_id = client.create_contract_header(
        code=TEST_CODE,
        description=TEST_DESC,
        customer_id=CUSTOMER_ID,
        agency_id=AGENCY_ID,
        media_center_id=MEDIA_CENTER_ID,
        agency_pct=AGENCY_PCT,
        contract_date=order.flight_start,
        contract_end_date=order.flight_end,
        contract_type=1,
        customer_order_ref=order.estimate_number,
    )
    print(f"  → contract_id = {contract_id}")

    print(f"\n[LINES] Entering {len(lines)} line(s)...")
    for i, line in enumerate(lines, 1):
        days, time_range, language = _parse_rpm_daypart(line.daypart)
        days, _ = EtereClient.check_sunday_6_7a_rule(days, time_range)
        duration_seconds = _duration_to_seconds(line.duration)
        description = f"(Line {line.line_number}) {line.daypart}"
        spot_code = 10 if line.is_bonus else 2

        week_dates = order.week_dates or tuple(
            order.flight_start + __import__('datetime').timedelta(weeks=k)
            for k in range(len(line.weekly_spots))
        )
        blocks = _consolidate_weeks(line.weekly_spots, week_dates, order.flight_end)

        for block_start, block_end, spots_per_week, total_spots in blocks:
            print(f"  {i:2}. {'BNS' if line.is_bonus else 'PAID'} {language} | "
                  f"{days} {time_range} | {block_start}–{block_end} "
                  f"{spots_per_week}/wk {total_spots} total @ ${line.rate}")
            line_id = client.add_contract_line(
                market=MARKET,
                days=days,
                time_range=time_range,
                description=description,
                rate=float(line.rate),
                total_spots=total_spots,
                spots_per_week=spots_per_week,
                date_from=block_start,
                date_to=block_end,
                duration=f"00:00:{duration_seconds:02d}:00",
                is_bonus=line.is_bonus,
                booking_code=spot_code,
                separation_intervals=SEPARATION,
            )
            print(f"      → line_id = {line_id}")

    print(f"\n✓ All lines entered. Contract #{contract_id} — delete when done reviewing.")

except Exception as e:
    print(f"\n✗ Error: {e}")
    import traceback
    traceback.print_exc()
finally:
    conn.close()
