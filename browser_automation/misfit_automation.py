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
- Separation: Customer=15, Event=0, Order=0
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
from parsers.misfit_parser import (
    parse_misfit_pdf,
    MisfitOrder,
    MisfitLine,
    prompt_for_spot_duration,
    _parse_week_date,
    analyze_weekly_distribution,
)

# Customer detection
from src.domain.enums import OrderType, BillingType
from src.data_access.repositories.customer_repository import CustomerRepository
from src.business_logic.services.customer_matching_service import CustomerMatchingService


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
    db_path = Path(__file__).parent.parent / "data" / "customers.db"
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
        
        print(f"\n[INFO] Customer search will open automatically during contract creation")
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
# MAIN PROCESSING FUNCTIONS
# ============================================================================

def process_misfit_order(
    driver,
    pdf_path: str,
    order_code: Optional[str] = None,
    description: Optional[str] = None,
    customer_id: Optional[int] = None
) -> bool:
    """
    Process Misfit order PDF - create contract in Etere.
    
    Misfit orders are multi-market (LAX, SFO, CVC) with:
    - Master market always NYC
    - Individual lines set their own market
    - No customer on PDF - uses universal detection
    - ALL ROS lines are bonus
    
    Args:
        driver: Selenium WebDriver (already logged in, master market set to NYC)
        pdf_path: Path to the Misfit PDF
        order_code: Optional pre-gathered order code
        description: Optional pre-gathered description
        customer_id: Optional pre-gathered customer ID
    
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
    
    print(f"✓ Parsed order")
    print(f"  Agency: {order.agency}")
    print(f"  Contact: {order.contact}")
    print(f"  Markets: {', '.join(order.markets)}")
    flight_start, flight_end = order.get_flight_dates()
    print(f"  Flight: {flight_start} - {flight_end}")
    print(f"  Total lines: {len(order.lines)}")
    print()
    
    # Create Etere client
    etere = EtereClient(driver)
    
    # If inputs not provided, prompt for them (simple version)
    if not customer_id:
        print("\n[CUSTOMER] No customer info on Misfit PDFs")
        print("Options:")
        print("  1. Enter customer ID directly")
        print("  2. Search in Etere browser")
        choice = input("Select (1-2): ").strip()
        
        if choice == "1":
            customer_id = input("Customer ID: ").strip()
        elif choice == "2":
            # Will trigger browser search - don't need ID now
            customer_id = None
        else:
            print("✗ Invalid choice")
            return False
    
    # Use provided order_code or prompt
    if not order_code:
        normalized_markets = [normalize_market(m) for m in order.markets]
        markets_str = '-'.join(normalized_markets)
        default_code = f"Misfit {order.contact.split()[0]} {markets_str}"
        order_code = input(f"Contract code (default: {default_code}): ").strip() or default_code
    
    # Use provided description or prompt
    if not description:
        default_desc = f"Misfit {', '.join(order.markets)}"
        description = input(f"Description (default: {default_desc}): ").strip() or default_desc
    
    # Spot duration
    spot_duration = prompt_for_spot_duration()
    
    # Separation intervals for Misfit
    separation_intervals = (15, 0, 0)  # Customer=15, Event=0, Order=0
    
    # Create contract
    success = create_misfit_contract(
        etere=etere,
        order=order,
        customer_id=customer_id,
        order_code=order_code,
        description=description,
        spot_duration=spot_duration,
        separation_intervals=separation_intervals
    )
    
    if success:
        print(f"\n{'='*70}")
        print("MISFIT ORDER PROCESSING COMPLETE")
        print(f"{'='*70}")
        print("✓ Contract created successfully!")
    else:
        print(f"\n{'='*70}")
        print("MISFIT ORDER PROCESSING FAILED")
        print(f"{'='*70}")
        print("✗ Contract creation failed - review errors above")
    
    return success


def create_misfit_contract(
    etere: EtereClient,
    order: MisfitOrder,
    customer_id: Optional[int],
    order_code: str,
    description: str,
    spot_duration: int,
    separation_intervals: Tuple[int, int, int]
) -> bool:
    """
    Create a single Misfit contract in Etere.
    
    Multi-market workflow:
    1. Create contract header (master market = NYC)
    2. For each market (LAX, SFO, CVC):
       - Get lines for that market
       - Add lines with market-specific setting
    
    ALL Etere interactions happen through etere.method_name() calls.
    NO direct Selenium/driver code here.
    
    Args:
        etere: EtereClient instance
        order: MisfitOrder object
        customer_id: Customer ID from detection
        contract_code: Contract code
        description: Contract description
        spot_duration: Spot duration in seconds
        separation_intervals: (customer, event, order) separation
    
    Returns:
        True if successful
    """
    try:
        print(f"\n[MISFIT] Creating contract for {order.agency}")
        
        # ═══════════════════════════════════════════════════════════════
        # CREATE CONTRACT HEADER
        # ═══════════════════════════════════════════════════════════════
        
        # Get flight dates
        flight_start, flight_end = order.get_flight_dates()
        
        # Handle customer ID
        if customer_id is None:
            # User will select in browser - etere_client will show instructions
            contract_number = etere.create_contract_header(
                customer_id=None,  # Triggers manual selection
                code=order_code,
                description=description,
                contract_start=flight_start,
                contract_end=flight_end,
                customer_order_ref=f"Misfit {order.date}",
                notes=None,  # Leave notes blank for Misfit
                charge_to=BillingType.CUSTOMER_SHARE_AGENCY.get_charge_to(),
                invoice_header=BillingType.CUSTOMER_SHARE_AGENCY.get_invoice_header()
            )
        else:
            # Direct customer ID provided
            contract_number = etere.create_contract_header(
                customer_id=int(customer_id),
                code=order_code,
                description=description,
                contract_start=flight_start,
                contract_end=flight_end,
                customer_order_ref=f"Misfit {order.date}",
                notes=None,  # Leave notes blank for Misfit
                charge_to=BillingType.CUSTOMER_SHARE_AGENCY.get_charge_to(),
                invoice_header=BillingType.CUSTOMER_SHARE_AGENCY.get_invoice_header()
            )
        
        if not contract_number:
            print(f"[MISFIT] ✗ Failed to create contract header")
            return False
        
        print(f"[MISFIT] ✓ Contract created: {contract_number}")
        
        # ═══════════════════════════════════════════════════════════════
        # ADD LINES - Process each market separately
        # ═══════════════════════════════════════════════════════════════
        
        line_count = 0
        
        # Process each market
        for market in order.markets:
            market_code = normalize_market(market)
            market_lines = order.get_lines_by_market(market_code)
            
            print(f"\n[MARKET] Processing {market_code} ({len(market_lines)} lines)")
            
            for line_idx, line in enumerate(market_lines):
                
                # Get line description (handles paid vs bonus formatting)
                desc = line.get_description()
                
                # Determine days and time
                if line.is_bonus and line.time == "ROS":
                    # Bonus ROS line - get actual schedule
                    ros_days, ros_time = line.get_ros_schedule()
                    days = ros_days
                    time = ros_time
                else:
                    # Paid line or custom bonus
                    days = line.days
                    time = line.time
                
                # Spot code
                spot_code = 10 if line.is_bonus else 2  # BNS=10, Paid=2
                
                # Analyze weekly distribution
                # Convert week dates from '26-Jan' format to 'MM/DD/YYYY'
                converted_week_dates = [
                    _parse_week_date(week, order.date) 
                    for week in order.week_start_dates
                ]
                
                # Consolidate weekly distribution (groups identical consecutive weeks)
                ranges = analyze_weekly_distribution(
                    line.weekly_spots,
                    converted_week_dates,
                    contract_end_date=flight_end
                )
                
                print(f"\n  Line {line_idx + 1}: {desc}")
                print(f"    Splits into {len(ranges)} Etere line(s)")
                
                # Parse time range using etere_client utility
                time_from, time_to = EtereClient.parse_time_range(time)
                
                # Apply Sunday 6-7a rule using etere_client utility
                adjusted_days, adjusted_day_count = EtereClient.check_sunday_6_7a_rule(days, time)
                
                # Create Etere line for each range
                for range_idx, range_data in enumerate(ranges, 1):
                    line_count += 1
                    
                    # Calculate total spots for this range
                    total_spots = range_data['spots_per_week'] * range_data['weeks']
                    
                    print(f"    Creating line {line_count}: {range_data['start_date']} - {range_data['end_date']}")
                    
                    # Get spots per week
                    spots_per_week = range_data['spots_per_week']
                    
                    # Add the line using etere_client!
                    # max_daily_run is auto-calculated by etere_client from spots_per_week and days
                    success = etere.add_contract_line(
                        contract_number=contract_number,
                        market=market_code,  # Set market per line (LAX, SFO, CVC)
                        start_date=range_data['start_date'],
                        end_date=range_data['end_date'],
                        days=adjusted_days,
                        time_from=time_from,
                        time_to=time_to,
                        description=desc,
                        spot_code=spot_code,
                        duration_seconds=spot_duration,  # User-specified duration
                        total_spots=total_spots,  # Calculated from spots_per_week * weeks
                        spots_per_week=spots_per_week,
                        # max_daily_run is auto-calculated - no need to pass it!
                        rate=line.rate,                        separation_intervals=separation_intervals,
                        is_bookend=False
                    )
                    
                    if not success:
                        print(f"    ✗ Failed to add line {line_count}")
                        return False
        
        print(f"\n[MISFIT] ✓ All {line_count} lines added successfully")
        return True
        
    except Exception as e:
        print(f"\n[MISFIT] ✗ Error creating contract: {e}")
        import traceback
        traceback.print_exc()
        return False


# ============================================================================
# TESTING FUNCTION
# ============================================================================

def test_misfit_automation():
    """Test Misfit automation with a sample PDF."""
    from browser_automation.etere_session import EtereSession
    
    # Prompt for PDF path
    pdf_path = input("Enter path to Misfit PDF: ").strip()
    
    with EtereSession() as session:
        # Set master market to NYC for Misfit multi-market orders
        session.set_market("NYC")
        
        # Process the order
        success = process_misfit_order(session.driver, pdf_path)
        
        if success:
            print("\n✓ Contract created successfully!")
        else:
            print("\n✗ Contract creation failed - review errors above")


if __name__ == "__main__":
    test_misfit_automation()
