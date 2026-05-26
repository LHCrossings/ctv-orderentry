"""
Test EtereDirectClient with a real WorldLink order.

Parses the PDF, creates a TEST contract (code prefixed with "TEST "),
enters all lines, and COMMITS — the contract remains in Etere for comparison
against its Selenium-entered counterpart via compare_contracts.py.

Usage:
    uv run python scripts/test_worldlink_direct.py <path/to/worldlink.pdf>

After running, note the printed contract ID, enter the Selenium contract as
usual, then compare:
    uv run python scripts/compare_contracts.py <selenium_id> <direct_id>
"""
import re
import sys
from datetime import datetime
from math import ceil
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from browser_automation.etere_direct_client import EtereDirectClient, connect
from browser_automation.parsers.worldlink_parser import parse_worldlink_pdf

SEPARATION = (5, 0, 15)  # WorldLink: customer=5, event=0, order=15


def _secs_to_duration(secs: int) -> str:
    """Convert integer seconds to HH:MM:SS:FF timecode string."""
    m, s = divmod(int(secs), 60)
    h, m = divmod(m, 60)
    return f"{h:02d}:{m:02d}:{s:02d}:00"


def _parse_date(date_str: str):
    """Convert 'MM/DD/YYYY' string to date object."""
    return datetime.strptime(date_str.strip(), '%m/%d/%Y').date()


def _build_notes(order_data: dict) -> str:
    comment = order_data.get('order_comment', '') or ''
    return re.sub(r'[-]', '', comment).strip()


def _lookup_wl_ids(cursor) -> tuple[int, int, int]:
    """
    Return (customer_id, agency_id, media_center_id) from the most recent
    WorldLink contract already in CONTRATTITESTATA.
    """
    cursor.execute("""
        SELECT TOP 1 COMMITTENTE, AGENZIA, CENTROMEDIA
        FROM CONTRATTITESTATA
        WHERE COD_CONTRATTO LIKE 'WL %'
        ORDER BY ID_CONTRATTITESTATA DESC
    """)
    row = cursor.fetchone()
    if row:
        return int(row[0] or 0), int(row[1] or 0), int(row[2] or 0)
    raise RuntimeError("No existing WorldLink contracts found — cannot derive customer/agency IDs")


def _add_crossings_lines(client: EtereDirectClient, lines: list) -> None:
    """
    CROSSINGS network: NYC line at real rate + CMP line at $0 per PDF line.
    """
    for line in lines:
        days = line['days_of_week']
        rate = float(line['rate'])
        is_bonus = rate == 0.0
        from_time = line['from_time']
        to_time = line['to_time']
        time_range = f"{from_time}-{to_time}"
        duration = _secs_to_duration(int(line['duration']))
        spots_pw = int(line['spots'])
        total = int(line['total_spots'])
        date_from = _parse_date(line['start_date'])
        date_to = _parse_date(line['end_date'])
        label = "BNS " if is_bonus else ""
        desc = f"(Line {line['line_number']}) {label}{days} {from_time}-{to_time}"

        print(f"\n  [LINE {line['line_number']}] {days} {time_range} | "
              f"{line['duration']}s | {spots_pw}/wk | ${rate}"
              + (" [BNS]" if is_bonus else ""))

        # NYC — real rate
        nyc_id = client.add_contract_line(
            market="NYC",
            days=days,
            time_range=time_range,
            description=desc,
            rate=rate,
            total_spots=total,
            spots_per_week=spots_pw,
            date_from=date_from,
            date_to=date_to,
            duration=duration,
            is_bonus=is_bonus,
            separation_intervals=SEPARATION,
        )
        print(f"    NYC line_id={nyc_id}  rate=${rate}")

        # CMP — $0 (block refresh replicates to other markets)
        cmp_id = client.add_contract_line(
            market="CMP",
            days=days,
            time_range=time_range,
            description=desc,
            rate=0.0,
            total_spots=total,
            spots_per_week=spots_pw,
            date_from=date_from,
            date_to=date_to,
            duration=duration,
            is_bonus=is_bonus,
            separation_intervals=SEPARATION,
        )
        print(f"    CMP line_id={cmp_id}  rate=$0.00")


def _add_asian_lines(client: EtereDirectClient, lines: list) -> None:
    """
    ASIAN network: single DAL market line per PDF line.
    """
    for line in lines:
        days = line['days_of_week']
        rate = float(line['rate'])
        is_bonus = rate == 0.0
        from_time = line['from_time']
        to_time = line['to_time']
        time_range = f"{from_time}-{to_time}"
        duration = _secs_to_duration(int(line['duration']))
        spots_pw = int(line['spots'])
        total = int(line['total_spots'])
        date_from = _parse_date(line['start_date'])
        date_to = _parse_date(line['end_date'])
        label = "BNS " if is_bonus else ""
        desc = f"(Line {line['line_number']}) {label}{days} {from_time}-{to_time}"

        print(f"\n  [LINE {line['line_number']}] {days} {time_range} | "
              f"{line['duration']}s | {spots_pw}/wk | ${rate}"
              + (" [BNS]" if is_bonus else ""))

        dal_id = client.add_contract_line(
            market="DAL",
            days=days,
            time_range=time_range,
            description=desc,
            rate=rate,
            total_spots=total,
            spots_per_week=spots_pw,
            date_from=date_from,
            date_to=date_to,
            duration=duration,
            is_bonus=is_bonus,
            separation_intervals=SEPARATION,
        )
        print(f"    DAL line_id={dal_id}  rate=${rate}")


def run(pdf_path: Path) -> None:
    print("=" * 65)
    print("WORLDLINK DIRECT DB TEST")
    print("=" * 65)

    # ── Parse ─────────────────────────────────────────────────────────
    print(f"\n[PARSE] {pdf_path.name}")
    order_data = parse_worldlink_pdf(str(pdf_path))

    network = order_data.get('network', 'CROSSINGS')
    lines = order_data.get('lines', [])
    order_code = order_data.get('order_code', 'WL UNKNOWN')
    description = order_data.get('description', order_code)
    tracking = order_data.get('tracking_number', '')
    notes = _build_notes(order_data)

    print(f"  Network     : {network}")
    print(f"  Order code  : {order_code}")
    print(f"  Description : {description}")
    print(f"  Tracking #  : {tracking}")
    print(f"  Lines parsed: {len(lines)}")

    if not lines:
        print("\n[ERROR] No lines parsed — aborting")
        sys.exit(1)

    # ── Connect + look up IDs ─────────────────────────────────────────
    print("\n[DB] Connecting...")
    conn = connect()
    conn.autocommit = False
    cursor = conn.cursor()

    customer_id, agency_id, media_center_id = _lookup_wl_ids(cursor)
    print(f"[DB] customer_id={customer_id}  agency_id={agency_id}  "
          f"media_center_id={media_center_id}")

    # ── Flight range ───────────────────────────────────────────────────
    flight_start = min(_parse_date(l['start_date']) for l in lines)
    flight_end   = max(_parse_date(l['end_date'])   for l in lines)

    test_code = f"TEST {order_code}"
    test_desc = f"TEST — {description}"

    try:
        master_market = "DAL" if network == "ASIAN" else "NYC"
        client = EtereDirectClient(conn, owner="Charmaine Lane", autocommit=False)
        client.set_master_market(master_market)

        # ── Contract header ────────────────────────────────────────────
        print(f"\n[HEADER] Creating: {test_code}")
        contract_id = client.create_contract_header(
            code=test_code,
            description=test_desc,
            customer_id=customer_id,
            agency_id=agency_id,
            media_center_id=media_center_id,
            contract_date=flight_start,
            contract_end_date=flight_end,
            contract_type=1,          # Proposal — forces manual review before scheduling
            billing_type="agency",
            note=notes,
            customer_order_ref=tracking,
        )
        print(f"  → contract_id = {contract_id}")

        # ── Lines ──────────────────────────────────────────────────────
        print(f"\n[LINES] Entering {len(lines)} PDF line(s) as {network}...")
        if network == "ASIAN":
            _add_asian_lines(client, lines)
        else:
            _add_crossings_lines(client, lines)

        conn.commit()
        print(f"\n{'='*65}")
        print(f"✓ Contract #{contract_id} committed.")
        print(f"  Code : {test_code}")
        print(f"  Note : Approve in Etere, then compare with:")
        print(f"         uv run python scripts/compare_contracts.py <REF_ID> {contract_id}")
        print(f"{'='*65}")

    except Exception as exc:
        print(f"\n✗ Error: {exc}")
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: uv run python scripts/test_worldlink_direct.py <worldlink.pdf>")
        sys.exit(1)
    run(Path(sys.argv[1]))
