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
sys.path.insert(0, str(project_root / "browser_automation"))

from browser_automation.etere_direct_client import EtereDirectClient, connect
from browser_automation.parsers.worldlink_parser import parse_worldlink_pdf
from browser_automation.worldlink_automation import _format_24hr_short, lookup_customer, _lookup_contract_by_tracking

WL_DEFAULT_SEPARATION = (5, 15, 0)  # fallback when advertiser not in customers.db

# All markets that receive a $0 line for CROSSINGS (NYC gets real rate)
# Order matches Selenium entry: CMP=2, HOU=3, SFO=4, SEA=5, LAX=6, CVC=7, WDC=8, MMT=9
CROSSINGS_ZERO_MARKETS = ["CMP", "HOU", "SFO", "SEA", "LAX", "CVC", "WDC", "MMT"]


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


def _extend_end_date(cursor, contract_id: int, new_end) -> None:
    """Extend contract end date in CONTRATTITESTATA if new lines go beyond it."""
    cursor.execute(
        "SELECT DATA_TERMINE FROM CONTRATTITESTATA WHERE ID_CONTRATTITESTATA = %s",
        (contract_id,)
    )
    row = cursor.fetchone()
    if not row:
        raise ValueError(f"Contract ID {contract_id} not found in CONTRATTITESTATA")
    current_end = row[0]
    # Normalise to date for comparison
    current_date = current_end.date() if hasattr(current_end, 'date') else current_end
    if current_date is None or current_date < new_end:
        print(f"[DATES] Extending contract end: {current_date} → {new_end}")
        cursor.execute(
            "UPDATE CONTRATTITESTATA SET DATA_TERMINE = %s "
            "WHERE ID_CONTRATTITESTATA = %s",
            (new_end, contract_id)
        )
    else:
        print(f"[DATES] Contract end {current_date} already covers {new_end} — no update needed")


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


def _add_crossings_lines(client: EtereDirectClient, lines: list, separation: tuple) -> None:
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
        time_short = f"{_format_24hr_short(from_time)}-{_format_24hr_short(to_time)}"
        desc = f"(Line {line['line_number']}) {label}{days} {time_short}"

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
            separation_intervals=separation,
            scheduling_type=0,  # Priority — WL uses wide dayparts that would auto-trigger Rotation
        )
        print(f"    NYC line_id={nyc_id}  rate=${rate}")

        # All other markets — $0 each
        for mkt in CROSSINGS_ZERO_MARKETS:
            mkt_id = client.add_contract_line(
                market=mkt,
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
                separation_intervals=separation,
                scheduling_type=0,
            )
            print(f"    {mkt} line_id={mkt_id}  rate=$0.00")


def _add_asian_lines(client: EtereDirectClient, lines: list, separation: tuple) -> None:
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
        time_short = f"{_format_24hr_short(from_time)}-{_format_24hr_short(to_time)}"
        desc = f"(Line {line['line_number']}) {label}{days} {time_short}"

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
            separation_intervals=separation,
            scheduling_type=0,
        )
        print(f"    DAL line_id={dal_id}  rate=${rate}")


def run(pdf_path: Path) -> None:
    print("=" * 65)
    print("WORLDLINK DIRECT DB TEST")
    print("=" * 65)

    # ── Parse ─────────────────────────────────────────────────────────
    print(f"\n[PARSE] {pdf_path.name}")
    order_data = parse_worldlink_pdf(str(pdf_path))

    network    = order_data.get('network', 'CROSSINGS')
    order_type = order_data.get('order_type', 'new')
    advertiser = order_data.get('advertiser', '')
    lines      = order_data.get('lines', [])
    order_code = order_data.get('order_code', 'WL UNKNOWN')
    description = order_data.get('description', order_code)
    tracking   = order_data.get('tracking_number', '')
    notes      = _build_notes(order_data)
    is_revision = order_type != 'new'

    print(f"  Network     : {network}")
    print(f"  Order type  : {order_type}")
    print(f"  Order code  : {order_code}")
    print(f"  Description : {description}")
    print(f"  Tracking #  : {tracking}")
    print(f"  Lines parsed: {len(lines)}")

    if not lines:
        print("\n[ERROR] No lines parsed — aborting")
        sys.exit(1)

    # ── Revision: look up existing contract ID by tracking number ────────
    existing_contract_id = None
    if is_revision:
        print(f"\n[REVISION] Order type: {order_type.upper()}")
        found_id, found_code = _lookup_contract_by_tracking(tracking)
        if found_id:
            print(f"[REVISION] ✓ Found contract {found_code} (ID {found_id}) for tracking '{tracking}'")
            confirm = input(f"  Use {found_code} (ID {found_id})? (y/n): ").strip().lower()
            existing_contract_id = int(found_id) if confirm == 'y' else int(input("  Existing contract DB ID: ").strip())
        else:
            if tracking:
                print(f"[REVISION] ✗ No contract found for tracking '{tracking}'")
            existing_contract_id = int(input("  Existing contract DB ID (ID_CONTRATTITESTATA): ").strip())

    # ── Connect ──────────────────────────────────────────────────────────
    print("\n[DB] Connecting...")
    conn = connect()
    cursor = conn.cursor()

    # Customer: look up in customers.db first; agency/media-center auto-populated
    # from ANAGRAF by create_contract_header when agency_id=None.
    cust_rec = lookup_customer(advertiser)
    if cust_rec:
        customer_id = int(cust_rec['customer_id'])
        agency_id = None          # triggers ANAGRAF auto-lookup
        media_center_id = None
        print(f"[DB] customer_id={customer_id} (from customers.db: {advertiser})")
    else:
        # Fallback: grab IDs from most recent matching WL contract
        customer_id, agency_id, media_center_id = _lookup_wl_ids(cursor)
        print(f"[DB] customer_id={customer_id}  agency_id={agency_id}  "
              f"media_center_id={media_center_id}  (fallback from DB)")

    # ── Flight range ───────────────────────────────────────────────────
    flight_start = min(_parse_date(l['start_date']) for l in lines)
    flight_end   = max(_parse_date(l['end_date'])   for l in lines)

    try:
        master_market = "DAL" if network == "ASIAN" else "NYC"
        client = EtereDirectClient(conn, owner="Charmaine Lane", autocommit=False)
        client.set_master_market(master_market)

        if is_revision:
            # ── Revision: extend end date + attach to existing contract ───
            _extend_end_date(cursor, existing_contract_id, flight_end)
            client._contract_id = existing_contract_id
            contract_id = existing_contract_id
            # Read the actual customer from the existing contract so Nielsen lookup is correct
            cursor.execute(
                "SELECT COMMITTENTE FROM CONTRATTITESTATA WHERE ID_CONTRATTITESTATA = %s",
                (existing_contract_id,)
            )
            _row = cursor.fetchone()
            if _row:
                customer_id = int(_row[0])
            print(f"\n[REVISION] Adding lines to existing contract #{contract_id}")
        else:
            # ── New order: create contract header ──────────────────────────
            test_code = f"TEST {order_code}"
            test_desc = f"TEST — {description}"
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
                master_market=master_market,
                note=notes,
                customer_order_ref=tracking,
            )
            print(f"  → contract_id = {contract_id}")

        # Per-advertiser separation from customers.db (falls back to WL default)
        cust_rec = lookup_customer(advertiser)
        separation = cust_rec['separation'] if cust_rec else WL_DEFAULT_SEPARATION
        print(f"[SEP] Customer={separation[0]}, Order={separation[1]}, Event={separation[2]}"
              + (f"  (from customers.db: {advertiser})" if cust_rec else "  (default fallback)"))

        # Pull Nielsen from the contract's actual customer record
        wl_defaults = client.get_client_defaults(customer_id)
        if wl_defaults.get("nielsen_id"):
            client._nielsen_id   = wl_defaults["nielsen_id"]
            client._nielsen_code = wl_defaults["nielsen_code"]
            print(f"[DB] Nielsen → id={client._nielsen_id}  code={client._nielsen_code}")

        # ── Lines ──────────────────────────────────────────────────────
        print(f"\n[LINES] Entering {len(lines)} PDF line(s) as {network}...")
        if network == "ASIAN":
            _add_asian_lines(client, lines, separation)
        else:
            _add_crossings_lines(client, lines, separation)

        conn.commit()
        print(f"\n{'='*65}")
        print(f"✓ Contract #{contract_id} committed.")
        if not is_revision:
            print(f"  Code : TEST {order_code}")
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

