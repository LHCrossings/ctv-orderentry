"""
SAGENT Order Browser Automation (Refactored)

Uses etere_client.py for ALL Etere interactions.
This file contains ONLY:
- PDF parsing orchestration
- Business logic (multi-market handling, rate grossing)
- Data transformation
- Calls to etere_client methods

NO Etere browser code lives here - it's all in etere_client.py.

Key SAGENT Business Rules:
- Multi-market orders (CVC, LAX, SFO, etc.)
- Master market always NYC (like Misfit)
- Individual lines set their own market
- Customer: Looked up from customers.db by advertiser name; prompts if not found
- Rate Grossing: Net rates divided by 0.85
- Order # goes in Customer Order Ref field
- Contract naming: "Sagent <Client> <Est#>"
- Billing: "Customer share indicating agency %" / "Agency"
- Separation: Customer=15, Order=0, Event=0
"""

from dataclasses import dataclass
from typing import Optional, Tuple
from pathlib import Path
import sys

# Add paths for imports
_src_path = Path(__file__).parent.parent
if str(_src_path) not in sys.path:
    sys.path.insert(0, str(_src_path))

from browser_automation.etere_client import EtereClient
from browser_automation.ros_definitions import get_ros_schedule
from parsers.sagent_parser import (
    parse_sagent_pdf,
    SagentOrder,
    SagentLine,
)
from src.domain.enums import OrderType, BillingType


# ============================================================================
# SAGENT CONSTANTS
# ============================================================================

# Separation intervals
SAGENT_SEPARATION = (10, 0, 0)  # Customer=10, Order=0, Event=0


# ============================================================================
# DATE / DURATION HELPERS (direct DB)
# ============================================================================

def _parse_date(s):
    """Parse MM/DD/YYYY, MM/DD/YY, or date objects to datetime.date."""
    from datetime import datetime, date
    if isinstance(s, date):
        return s
    s = str(s).strip()
    for fmt in ('%m/%d/%Y', '%m/%d/%y', '%b %d, %Y', '%Y-%m-%d'):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"Cannot parse date: {s!r}")


def _secs_to_duration(secs: int) -> str:
    """Convert seconds to HH:MM:SS:FF duration string for EtereDirectClient."""
    m, s = divmod(int(secs), 60)
    h, m = divmod(m, 60)
    return f"{h:02d}:{m:02d}:{s:02d}:00"


# ============================================================================
# DIRECT DB ENTRY
# ============================================================================

def _create_sagent_contract_direct(order: SagentOrder, inputs: dict) -> Optional[int]:
    """
    Enter SAGENT order directly via DB stored procedures (no browser).
    Returns contract_id on success, None on failure (rolls back fully).
    """
    from browser_automation.etere_direct_client import EtereDirectClient, connect

    customer_id = inputs.get('customer_id')
    if customer_id is None:
        print("[SAGENT DIRECT] ✗ No customer_id — cannot enter without a known ID")
        return None

    conn = None
    try:
        conn = connect()
        client = EtereDirectClient(conn, owner="Charmaine Lane", autocommit=False)
        client.set_master_market("NYC")

        contract_id = client.create_contract_header(
            code=inputs['contract_code'],
            description=inputs['description'],
            customer_id=int(customer_id),
            contract_date=_parse_date(order.flight_start),
            contract_end_date=_parse_date(order.flight_end),
            contract_type=1,
            billing_type="agency",
            note=inputs['notes'],
            customer_order_ref=order.order_number,
        )
        print(f"[SAGENT DIRECT] ✓ Contract header ID={contract_id}")

        line_count = 0
        for line in sorted(order.lines, key=lambda l: l.line_number):
            is_bonus     = line.is_bonus()
            booking_code = 10 if is_bonus else 2
            duration_str = _secs_to_duration(line.get_duration_seconds())
            days         = line.get_etere_days()
            time_str     = line.get_etere_time()
            desc         = line.get_description()

            time_from, time_to   = EtereClient.parse_time_range(time_str)
            time_range           = f"{time_from}-{time_to}"
            adjusted_days, _     = EtereClient.check_sunday_6_7a_rule(days, time_str)

            ranges = EtereClient.consolidate_weeks(
                line.weekly_spots,
                order.week_start_dates,
                flight_end=order.flight_end,
            )

            for rng in ranges:
                line_count  += 1
                total_spots  = rng['spots_per_week'] * rng['weeks']
                print(f"  [LINE {line_count}] {line.market} {desc}: "
                      f"{rng['start_date']}–{rng['end_date']} "
                      f"({rng['spots_per_week']}/wk×{rng['weeks']}w={total_spots})")
                client.add_contract_line(
                    market=line.market,
                    days=adjusted_days,
                    time_range=time_range,
                    description=desc,
                    rate=float(line.gross_rate),
                    total_spots=total_spots,
                    spots_per_week=rng['spots_per_week'],
                    date_from=_parse_date(rng['start_date']),
                    date_to=_parse_date(rng['end_date']),
                    duration=duration_str,
                    is_bonus=is_bonus,
                    booking_code=booking_code,
                    separation_intervals=SAGENT_SEPARATION,
                )

        conn.commit()
        conn.close()
        print(f"[SAGENT DIRECT] ✓ {line_count} lines committed.")
        return contract_id

    except Exception as exc:
        print(f"[SAGENT DIRECT] ✗ {exc}")
        import traceback
        traceback.print_exc()
        if conn:
            try:
                conn.rollback()
                conn.close()
            except Exception:
                pass
        return None


def process_sagent_order_direct(pdf_path: str, user_input: dict) -> Optional[int]:
    """Direct DB entry point for the order processing service (no browser needed)."""
    order = parse_sagent_pdf(pdf_path)
    return _create_sagent_contract_direct(order, user_input)


# ============================================================================
# PRESENTATION LAYER - User Input Gathering
# ============================================================================

def gather_sagent_inputs_from_pdf(pdf_path: str) -> Optional[dict]:
    """
    Parse PDF and gather user inputs WITHOUT needing browser/driver.
    
    This can be called BEFORE browser session creation to enable
    fully unattended processing after login.
    
    Args:
        pdf_path: Path to SAGENT PDF
        
    Returns:
        Dictionary with contract_code, description, notes or None if cancelled
    """
    # Parse PDF first
    order = parse_sagent_pdf(pdf_path)
    
    # Gather inputs using the order
    return gather_upfront_inputs(order)


def gather_upfront_inputs(order: SagentOrder) -> dict:
    """
    Gather ALL user inputs upfront before any browser automation.
    
    This enables fully unattended processing after initial setup.
    
    Args:
        order: Parsed SagentOrder object
        
    Returns:
        Dictionary with:
        - contract_code: Contract code
        - description: Contract description
        - notes: Contract notes
    """
    print(f"\n{'='*70}")
    print("UPFRONT INPUT GATHERING")
    print(f"{'='*70}\n")
    
    # Show parsed order details
    print(f"Advertiser: {order.advertiser}")
    print(f"Campaign: {order.campaign}")
    print(f"Flight: {order.flight_start} - {order.flight_end}")
    print(f"Order #: {order.order_number}")
    print(f"Estimate: {order.estimate_number} (stripped: {order.estimate_number_stripped})")
    print(f"Markets: {', '.join(order.markets)}")
    print(f"Lines: {len(order.lines)}")
    print()
    
    # 0. Customer
    print("[0/3] Customer")
    print("-" * 70)
    resolved_customer_id = None
    resolved_customer_name = order.advertiser
    try:
        import sys as _sys
        from pathlib import Path as _Path
        _src = _Path(__file__).parent.parent / "src"
        if str(_src) not in _sys.path:
            _sys.path.insert(0, str(_src))
        from browser_automation.customer_defaults import DEFAULT_DB_PATH as _DB_PATH
        from data_access.repositories.customer_repository import CustomerRepository as _CR
        import sqlite3 as _sqlite3
        _conn = _sqlite3.connect(str(_DB_PATH))
        _conn.row_factory = _sqlite3.Row
        _rows = _conn.execute(
            "SELECT customer_id, customer_name FROM customers"
            " WHERE order_type='sagent' ORDER BY customer_name"
        ).fetchall()
        _conn.close()
        if _rows:
            print("Known SAGENT customers:")
            for r in _rows:
                print(f"  {r['customer_id']:>6}  {r['customer_name']}")
    except Exception:
        pass
    cid_input = input(f"Enter Etere customer ID for '{order.advertiser}': ").strip()
    if cid_input:
        resolved_customer_id = int(cid_input)
        name_input = input(f"Customer name (Enter to use '{order.advertiser}'): ").strip()
        resolved_customer_name = name_input if name_input else order.advertiser
    print(f"✓ Customer ID: {resolved_customer_id}  ({resolved_customer_name})\n")

    # 1. Contract Code
    print("[1/3] Contract Code")
    print("-" * 70)
    default_code = order.get_default_contract_code()
    print(f"Default: {default_code}")
    
    use_default = input(f"Use default? (y/n): ").strip().lower()
    if use_default == 'y':
        contract_code = default_code
    else:
        contract_code = input("Enter contract code: ").strip()
    
    print(f"✓ Contract Code: {contract_code}")
    print()
    
    # 2. Description
    print("[2/3] Contract Description")
    print("-" * 70)
    default_desc = order.get_default_description()
    print(f"Default: {default_desc}")
    
    use_default = input(f"Use default? (y/n): ").strip().lower()
    if use_default == 'y':
        description = default_desc
    else:
        description = input("Enter description: ").strip()
    
    print(f"✓ Description: {description}")
    print()
    
    # 3. Notes
    print("[3/3] Contract Notes")
    print("-" * 70)
    default_notes = order.get_default_notes()
    print(f"Default: {default_notes}")
    
    use_default = input(f"Use default? (y/n): ").strip().lower()
    if use_default == 'y':
        notes = default_notes
    else:
        notes = input("Enter notes: ").strip()
    
    print(f"✓ Notes: {notes}")
    print()
    
    print(f"{'='*70}")
    print("✓ All inputs gathered - ready for automation")
    print(f"{'='*70}\n")

    # Upsert customer to DB if we have an ID
    if resolved_customer_id is not None:
        try:
            from browser_automation.customer_defaults import DEFAULT_DB_PATH as CUSTOMER_DB_PATH
            from data_access.repositories.customer_repository import CustomerRepository as _CR
            from domain.entities import Customer as _Cust
            from domain.enums import OrderType as _OT
            _CR(CUSTOMER_DB_PATH).save(_Cust(
                customer_id=str(resolved_customer_id),
                customer_name=resolved_customer_name,
                order_type=_OT.SAGENT,
                billing_type="agency",
            ))
        except Exception:
            pass

    return {
        'contract_code': contract_code,
        'description': description,
        'notes': notes,
        'customer_id': resolved_customer_id,
        'customer_name': resolved_customer_name,
    }


# ============================================================================
# MAIN PROCESSING FUNCTION
# ============================================================================

def process_sagent_order(
    driver,
    pdf_path: str,
    shared_session: Optional[any] = None,
    pre_gathered_inputs: Optional[dict] = None
) -> bool:
    """
    Process SAGENT order PDF and create contract in Etere.
    
    Complete workflow:
    1. Parse PDF
    2. Gather upfront inputs (contract code, description, notes) - OR use pre-gathered
    3. Create EtereClient
    4. Create contract
    
    Args:
        driver: Selenium WebDriver
        pdf_path: Path to SAGENT PDF
        shared_session: Optional shared session for batch processing
        
    Returns:
        True if successful
    """
    try:
        # Parse PDF
        order = parse_sagent_pdf(pdf_path)
        
        print(f"\n{'='*70}")
        print("SAGENT ORDER PROCESSING")
        print(f"{'='*70}")
        print(f"Advertiser: {order.advertiser}")
        print(f"Campaign: {order.campaign}")
        print(f"Order #: {order.order_number}")
        print(f"Estimate: {order.estimate_number_stripped}")
        print(f"Markets: {', '.join(order.markets)}")
        print(f"Lines: {len(order.lines)}")
        print(f"{'='*70}\n")
        
        # Gather inputs (use pre-gathered if provided)
        if pre_gathered_inputs:
            inputs = pre_gathered_inputs
            print("[INFO] Using pre-gathered inputs\n")
        else:
            inputs = gather_upfront_inputs(order)
        
        if not inputs:
            print("\n✗ Input gathering cancelled")
            return False
        
        # Try direct DB first (no browser needed)
        contract_id = _create_sagent_contract_direct(order, inputs)
        if contract_id is not None:
            success = True
        elif driver is not None:
            print("[FALLBACK] Direct DB failed — retrying via browser automation...")
            etere = EtereClient(driver)
            success = create_sagent_contract(
                etere=etere,
                order=order,
                order_code=inputs['contract_code'],
                description=inputs['description'],
                notes=inputs['notes'],
                customer_id=inputs.get('customer_id'),
                separation_intervals=SAGENT_SEPARATION
            )
        else:
            print("[SAGENT] ✗ Direct DB failed and no browser driver available")
            success = False
        
        if success:
            print(f"\n{'='*70}")
            print("SAGENT ORDER PROCESSING COMPLETE")
            print(f"{'='*70}")
            print("✓ Contract created successfully")
        else:
            print(f"\n{'='*70}")
            print("SAGENT ORDER PROCESSING FAILED")
            print(f"{'='*70}")
            print("✗ Contract creation failed - review errors above")
        
        return success
        
    except Exception as e:
        print(f"\n✗ Error processing SAGENT order: {e}")
        import traceback
        traceback.print_exc()
        return False


def create_sagent_contract(
    etere: EtereClient,
    order: SagentOrder,
    order_code: str,
    description: str,
    notes: str,
    separation_intervals: Tuple[int, int, int],
    customer_id: Optional[int] = None,
) -> bool:
    """
    Create a single SAGENT contract in Etere.
    
    Multi-market workflow:
    1. Create contract header (master market = NYC)
    2. For each market in order:
       - Get lines for that market
       - Add lines with market-specific setting
    
    ALL Etere interactions happen through etere.method_name() calls.
    NO direct Selenium/driver code here.
    
    Args:
        etere: EtereClient instance
        order: SagentOrder object
        order_code: Contract code
        description: Contract description
        notes: Contract notes
        separation_intervals: (customer, order, event) separation
    
    Returns:
        True if successful
    """
    try:
        print(f"\n[SAGENT] Creating contract for {order.advertiser}")
        
        # ═══════════════════════════════════════════════════════════════
        # CREATE CONTRACT HEADER
        # ═══════════════════════════════════════════════════════════════
        
        # Master market is already set to NYC by session
        # Individual lines will use their own market (CVC, LAX, SFO, etc.)
        
        contract_number = etere.create_contract_header(
            customer_id=customer_id,
            code=order_code,
            description=description,
            contract_start=order.flight_start,
            contract_end=order.flight_end,
            customer_order_ref=order.order_number,  # Full order number
            notes=notes,
            charge_to=BillingType.CUSTOMER_SHARE_AGENCY.get_charge_to(),
            invoice_header=BillingType.CUSTOMER_SHARE_AGENCY.get_invoice_header()
        )
        
        if not contract_number:
            print(f"[SAGENT] ✗ Failed to create contract header")
            return False
        
        print(f"[SAGENT] ✓ Contract created: {contract_number}")
        
        # ═══════════════════════════════════════════════════════════════
        # ADD LINES - Process in PDF line-number order
        # ═══════════════════════════════════════════════════════════════

        line_count = 0

        for line_idx, line in enumerate(sorted(order.lines, key=lambda l: l.line_number)):
            market = line.market
            print(f"\n[LINE {line.line_number}] Market: {market}")

            # Get line description (handles paid vs bonus formatting)
            desc = line.get_description()
            # Get language
            language = line.get_language()

            # Spot code
            spot_code = 10 if line.is_bonus() else 2  # BNS=10, Paid=2

            # Get Etere-formatted days and time
            days = line.get_etere_days()
            time = line.get_etere_time()

            # Consolidate weekly distribution (groups identical consecutive weeks)
            ranges = EtereClient.consolidate_weeks(
                line.weekly_spots,
                order.week_start_dates,
                flight_end=order.flight_end
            )

            print(f"\n  Line {line.line_number}: {desc}")
            print(f"    Rate: ${line.net_rate} net → ${line.gross_rate} gross")
            print(f"    Splits into {len(ranges)} Etere line(s)")

            # Parse time range using etere_client utility
            time_from, time_to = EtereClient.parse_time_range(time)

            # Apply Sunday 6-7a rule using etere_client utility
            adjusted_days, adjusted_day_count = EtereClient.check_sunday_6_7a_rule(days, time)

            # Get duration in seconds
            duration_seconds = line.get_duration_seconds()

            # Create Etere line for each range
            for range_idx, range_data in enumerate(ranges, 1):
                line_count += 1

                # Calculate total spots for this range
                total_spots = range_data['spots_per_week'] * range_data['weeks']

                print(f"    Creating line {line_count}: {range_data['start_date']} - {range_data['end_date']}")

                # Get spots per week
                spots_per_week = range_data['spots_per_week']

                success = etere.add_contract_line(
                    contract_number=contract_number,
                    market=market,
                    start_date=range_data['start_date'],
                    end_date=range_data['end_date'],
                    days=adjusted_days,
                    time_from=time_from,
                    time_to=time_to,
                    description=desc,
                    spot_code=spot_code,
                    duration_seconds=duration_seconds,
                    total_spots=total_spots,
                    spots_per_week=spots_per_week,
                    rate=float(line.gross_rate),
                    separation_intervals=separation_intervals,
                    is_bookend=False
                )

                if not success:
                    print(f"    ✗ Failed to add line {line_count}")
                    return False
        
        print(f"\n[SAGENT] ✓ All {line_count} lines added successfully")
        return True
        
    except Exception as e:
        print(f"\n[SAGENT] ✗ Error creating contract: {e}")
        import traceback
        traceback.print_exc()
        return False


# ============================================================================
# TESTING FUNCTION
# ============================================================================

def test_sagent_automation():
    """Test SAGENT automation with a sample PDF."""
    from browser_automation.etere_session import EtereSession
    
    # Prompt for PDF path
    pdf_path = input("Enter path to SAGENT PDF: ").strip()
    
    with EtereSession() as session:
        # Set master market to NYC for SAGENT multi-market orders
        session.set_market("NYC")
        
        # Process the order
        success = process_sagent_order(session.driver, pdf_path)
        
        if success:
            print("\n✓ Contract created successfully!")
        else:
            print("\n✗ Contract creation failed - review errors above")


if __name__ == "__main__":
    test_sagent_automation()
