"""
Misfit Order Browser Automation (Refactored)

Uses etere_client.py for ALL Etere interactions.
This file contains ONLY:
- PDF parsing orchestration  
- Business logic (multi-market handling, ROS definitions, bonus line formats)
- Data transformation
- Calls to etere_client methods

NO Etere browser code lives here - it's all in etere_client.py.

Key Misfit Business Rules:
- Multi-market orders (LAX, SFO, CVC)
- Master market always NYC, individual lines set their own market
- No customer info on PDF - uses universal customer detection
- ALL ROS lines are BONUS (rate = $0)
- Billing: "Customer share indicating agency %" / "Agency"
- Separation: Customer=15, Order=0, Event=0
"""

import sys
from datetime import date, datetime
from pathlib import Path
from typing import Optional

# Add paths for imports
_src_path = Path(__file__).parent.parent
if str(_src_path) not in sys.path:
    sys.path.insert(0, str(_src_path))

from parsers.misfit_parser import (
    MisfitOrder,
    _parse_week_date,
    analyze_weekly_distribution,
    parse_misfit_pdf,
    prompt_for_spot_duration,
)

from browser_automation.etere_client import EtereClient
from src.business_logic.services.customer_matching_service import CustomerMatchingService
from src.data_access.repositories.customer_repository import CustomerRepository
from src.domain.entities import Customer

# Customer detection
from src.domain.enums import OrderType

# ============================================================================
# ROS (Run of Schedule) DEFINITIONS - Now using universal definitions
# ============================================================================

# Import universal ROS schedules (same for all agencies)
# Hmong is ALWAYS Sa-Su 6p-8p across all agencies


# ============================================================================
# MARKET MAPPING
# ============================================================================

MARKET_CODES = {
    "LA": "LAX",
    "LAX": "LAX",
    "LOS ANGELES": "LAX",
    "SF": "SFO", 
    "SFO": "SFO",
    "SAN FRANCISCO": "SFO",
    "CVC": "CVC",
    "CENTRAL VALLEY": "CVC",
    "SACRAMENTO": "CVC"
}


def normalize_market(market_name: str) -> str:
    """
    Normalize market name to standard code.
    
    Args:
        market_name: Market name from PDF (e.g., "LA", "SF", "Central Valley")
        
    Returns:
        Market code (e.g., "LAX", "SFO", "CVC")
    """
    market_upper = market_name.upper().strip()
    return MARKET_CODES.get(market_upper, market_upper)


# ============================================================================
# PRESENTATION LAYER - User Input Gathering
# ============================================================================

def gather_upfront_inputs(order: MisfitOrder, etere_client: EtereClient) -> dict:
    """
    Gather ALL user inputs upfront before any browser automation.
    
    This enables fully unattended processing after initial setup.
    
    Args:
        order: Parsed MisfitOrder object
        etere_client: EtereClient instance for browser-based customer search
        
    Returns:
        Dictionary with:
        - customer_id: Customer ID from detection
        - spot_duration: Spot duration in seconds
        - contract_code: Contract code
        - description: Contract description
    """
    print(f"\n{'='*70}")
    print("UPFRONT INPUT GATHERING")
    print(f"{'='*70}\n")
    
    # 1. Customer Detection (using universal system + Etere search)
    print("[1/4] Customer Detection")
    print("-" * 70)
    
    # Get client name from order (Misfit PDFs may have it in agency/contact fields)
    # Since there's no standard client field, we'll prompt the user
    print(f"\nAgency: {order.agency}")
    print(f"Contact: {order.contact}")
    print(f"Markets: {', '.join(order.markets)}")
    print()
    
    # Show existing Misfit customers for reference
    from browser_automation.customer_defaults import DEFAULT_DB_PATH as db_path
    repo = CustomerRepository(str(db_path))
    
    existing_customers = repo.list_by_order_type(OrderType.MISFIT)
    if existing_customers:
        print("Existing Misfit customers:")
        for i, customer in enumerate(existing_customers[:10], 1):
            print(f"  {i}. {customer.customer_name} → {customer.customer_id}")
        if len(existing_customers) > 10:
            print(f"  ... and {len(existing_customers) - 10} more")
        print()
    
    # Prompt for customer selection method
    print("How would you like to select the customer?")
    print("  1. Enter customer ID (if you know it)")
    print("  2. Enter customer name (fuzzy match from database)")
    print("  3. Search in Etere browser (visual search)")
    print()
    
    choice = input("Select option (1-3): ").strip()
    
    customer_id = None
    
    if choice == "1":
        # Direct ID entry
        customer_id = input("Enter customer ID: ").strip()
        if not customer_id:
            print("\n✗ Customer ID required")
            return None
        print(f"[CUSTOMER] ✓ Using Customer ID: {customer_id}")
    
    elif choice == "2":
        # Fuzzy match from database
        customer_input = input("Enter customer name to search: ").strip()
        if not customer_input:
            print("\n✗ Customer name required")
            return None
        
        matcher = CustomerMatchingService(repo)
        customer_id = matcher.find_customer(
            customer_name=customer_input,
            order_type=OrderType.MISFIT,
            prompt_if_not_found=True
        )
        
        if not customer_id:
            print("\n✗ Customer detection cancelled")
            return None
    
    elif choice == "3":
        # Search in Etere browser - need to navigate to contract page first
        print("\n[ETERE SEARCH] Will open customer search during contract creation")
        print("We'll navigate to the contract page first, then you can search")
        
        # Mark that we want to use Etere search
        customer_id = "SEARCH_IN_ETERE"
        
        # Save the search term for later
        search_term = input("Enter search term to use (or press Enter for blank): ").strip()
        
        print("\n[INFO] Customer search will open automatically during contract creation")
        print(f"      Search term: '{search_term}' (you can modify it there)")
    
    else:
        print("\n✗ Invalid choice")
        return None
    
    print()
    
    # Store the search term if needed
    search_info = {
        'search_term': search_term if choice == "3" else None,
        'use_search': choice == "3"
    }
    
    # 2. Spot Duration
    print("[2/4] Spot Duration")
    print("-" * 70)
    spot_duration = prompt_for_spot_duration()
    print(f"✓ Spot duration: {spot_duration} seconds")
    print()
    
    # 3. Contract Code
    print("[3/4] Contract Code")
    print("-" * 70)
    
    # Normalize markets for display
    normalized_markets = [normalize_market(m) for m in order.markets]
    markets_str = '-'.join(normalized_markets)
    
    default_code = f"Misfit {order.contact.split()[0]} {markets_str} {order.date.replace('/', '')}"
    contract_code = input(f"Enter contract code (default: {default_code}): ").strip()
    if not contract_code:
        contract_code = default_code
    print(f"✓ Contract code: {contract_code}")
    print()
    
    # 4. Description
    print("[4/4] Contract Description")
    print("-" * 70)
    default_desc = f"Misfit Order {order.date}"
    description = input(f"Enter description (default: {default_desc}): ").strip()
    if not description:
        description = default_desc
    print(f"✓ Description: {description}")
    print()
    
    return {
        'customer_id': customer_id,
        'spot_duration': spot_duration,
        'contract_code': contract_code,
        'description': description,
        'use_search': search_info.get('use_search', False),
        'search_term': search_info.get('search_term', '')
    }


# ============================================================================
# DIRECT DB HELPERS
# ============================================================================

def _parse_date(s):
    """Parse MM/DD/YYYY, YYYY-MM-DD, or MM/DD/YY to date. Accepts date objects too."""
    if isinstance(s, date):
        return s
    for fmt in ('%m/%d/%Y', '%Y-%m-%d', '%m/%d/%y'):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"Cannot parse date: {s!r}")


def _create_misfit_contract_direct(
    order: "MisfitOrder",
    customer_id,
    order_code: str,
    description: str,
    spot_duration: int,
    separation_intervals,
) -> Optional[int]:
    """Enter Misfit order directly via DB stored procedures (no browser).
    Returns contract_id on success, None on failure.
    """
    from browser_automation.etere_direct_client import EtereDirectClient, connect

    if not customer_id:
        print("[MISFIT DIRECT] ✗ No customer_id (browser search not available in direct mode)")
        return None

    conn = None
    try:
        conn = connect()
        client = EtereDirectClient(conn, owner="Charmaine Lane", autocommit=False)
        client.set_master_market("NYC")

        flight_start, flight_end = order.get_flight_dates()

        contract_id = client.create_contract_header(
            code=order_code,
            description=description,
            customer_id=int(customer_id),
            contract_date=_parse_date(flight_start),
            contract_end_date=_parse_date(flight_end),
            billing_type="agency",
            note="",
            allow_rename=True,
        )
        print(f"[MISFIT DIRECT] ✓ Contract header ID={contract_id}")

        line_count = 0
        for market in order.markets:
            market_code = normalize_market(market)
            market_lines = order.get_lines_by_market(market_code)

            for line in market_lines:
                if line.is_bonus and line.time == "ROS":
                    days, time = line.get_ros_schedule()
                else:
                    days = line.days
                    time = line.time

                booking_code = 10 if line.is_bonus else 2
                rate = 0.0 if line.is_bonus else float(line.rate)
                desc = line.get_description()

                adjusted_days, _ = EtereClient.check_sunday_6_7a_rule(days, time)
                time_from, time_to = EtereClient.parse_time_range(time)
                time_range = f"{time_from}-{time_to}"

                converted_week_dates = [
                    _parse_week_date(week, order.date)
                    for week in order.week_start_dates
                ]
                ranges = analyze_weekly_distribution(
                    line.weekly_spots,
                    converted_week_dates,
                    contract_end_date=flight_end,
                )

                for rng in ranges:
                    line_count += 1
                    total_spots = rng["spots_per_week"] * rng["weeks"]
                    client.add_contract_line(
                        market=market_code,
                        days=adjusted_days,
                        time_range=time_range,
                        description=desc,
                        rate=rate,
                        total_spots=total_spots,
                        spots_per_week=rng["spots_per_week"],
                        date_from=_parse_date(rng["start_date"]),
                        date_to=_parse_date(rng["end_date"]),
                        duration=str(spot_duration),
                        is_bonus=line.is_bonus,
                        booking_code=booking_code,
                        separation_intervals=separation_intervals,
                        contract_id=contract_id,
                    )

        conn.commit()
        conn.close()
        print(f"[MISFIT DIRECT] ✓ {line_count} lines committed")
        return contract_id

    except Exception as exc:
        print(f"[MISFIT DIRECT] ✗ {exc}")
        import traceback
        traceback.print_exc()
        if conn:
            try:
                conn.rollback()
                conn.close()
            except Exception:
                pass
        return None


# ============================================================================
# GATHER (pre-processing input collection)
# ============================================================================

def gather_misfit_inputs(pdf_path: str) -> Optional[dict]:
    """Collect all Misfit inputs before processing. Returns dict or None on cancel."""
    from browser_automation.customer_defaults import DEFAULT_DB_PATH as _db_path

    print(f"\n{'='*70}")
    print("MISFIT — UPFRONT INPUT COLLECTION")
    print(f"{'='*70}\n")

    try:
        order = parse_misfit_pdf(pdf_path)
    except Exception as e:
        print(f"[PARSE] ✗ Failed: {e}")
        return None

    flight_start, flight_end = order.get_flight_dates()
    print(f"[PARSE] ✓ Agency:   {order.agency}")
    print(f"[PARSE] ✓ Contact:  {order.contact}")
    print(f"[PARSE] ✓ Markets:  {', '.join(order.markets)}")
    print(f"[PARSE] ✓ Flight:   {flight_start} – {flight_end}")
    print(f"[PARSE] ✓ Lines:    {len(order.lines)}")

    # ── Customer ID with DB lookup ──────────────────────────────────────────
    _repo = CustomerRepository(_db_path)
    _found = _repo.find_by_name_any_type(order.agency)
    _saved_id = _found.customer_id if (_found and _found.order_type == OrderType.MISFIT) else None

    cid_prompt = (f"\n  Etere customer ID [{_saved_id}]: " if _saved_id
                  else "\n  Etere customer ID: ")
    cid_input = input(cid_prompt).strip() or _saved_id or ""
    try:
        customer_id = int(cid_input)
    except (ValueError, TypeError):
        print("  [ERROR] A customer ID is required for direct DB entry")
        return None

    try:
        _repo.save(Customer(
            customer_id=str(customer_id),
            customer_name=order.agency,
            order_type=OrderType.MISFIT,
            billing_type="agency",
            code_name=order.agency,
            description_name=order.agency,
        ))
    except Exception as e:
        print(f"  ⚠ Could not save customer to DB: {e}")

    # ── Contract code / description ─────────────────────────────────────────
    first_name = order.contact.split()[0] if order.contact else "Misfit"
    markets_str = '-'.join(normalize_market(m) for m in order.markets)
    default_code = f"Misfit {first_name} {markets_str}"
    default_desc = f"Misfit {', '.join(order.markets)}"

    order_code  = input(f"  Code [{default_code}]: ").strip() or default_code
    description = input(f"  Description [{default_desc}]: ").strip() or default_desc

    # ── Spot duration ───────────────────────────────────────────────────────
    spot_duration = prompt_for_spot_duration()

    print(f"\n{'='*70}")
    print("INPUT COLLECTION COMPLETE")
    print(f"{'='*70}")

    return {
        'customer_id':   customer_id,
        'order_code':    order_code,
        'description':   description,
        'spot_duration': spot_duration,
        'separation':    (15, 0, 0),
    }


# ============================================================================
# MAIN PROCESSING FUNCTIONS
# ============================================================================

def process_misfit_order(
    pdf_path: str,
    shared_session=None,
    pre_gathered_inputs: Optional[dict] = None,
) -> bool:
    """
    Process Misfit order PDF - create contract in Etere via direct DB.

    Misfit orders are multi-market (LAX, SFO, CVC) with:
    - Master market always NYC
    - Individual lines set their own market
    - No customer on PDF - uses universal detection
    - ALL ROS lines are bonus

    Args:
        pdf_path: Path to the Misfit PDF
        shared_session: Unused (kept for orchestrator compatibility)
        pre_gathered_inputs: Dict from gather_misfit_inputs()

    Returns:
        True if contract created successfully
    """
    print(f"\n{'='*70}")
    print(f"PROCESSING MISFIT ORDER: {pdf_path}")
    print(f"{'='*70}\n")

    # Parse PDF
    print("Parsing PDF...")
    try:
        order = parse_misfit_pdf(pdf_path)
    except Exception as e:
        print(f"✗ Failed to parse PDF: {e}")
        return False

    print("✓ Parsed order")
    print(f"  Agency: {order.agency}")
    print(f"  Contact: {order.contact}")
    print(f"  Markets: {', '.join(order.markets)}")
    flight_start, flight_end = order.get_flight_dates()
    print(f"  Flight: {flight_start} - {flight_end}")
    print(f"  Total lines: {len(order.lines)}")
    print()

    inputs = pre_gathered_inputs or {}
    customer_id   = inputs.get('customer_id')
    order_code    = inputs.get('order_code')
    description   = inputs.get('description')
    spot_duration = inputs.get('spot_duration')
    separation_intervals = inputs.get('separation', (15, 0, 0))

    contract_id = _create_misfit_contract_direct(
        order, customer_id, order_code, description, spot_duration, separation_intervals
    )
    return contract_id is not None


if __name__ == "__main__":
    import sys
    _pdf = sys.argv[1] if len(sys.argv) > 1 else input("Enter path to Misfit PDF: ").strip()
    inputs = gather_misfit_inputs(_pdf)
    if inputs:
        process_misfit_order(_pdf, pre_gathered_inputs=inputs)
