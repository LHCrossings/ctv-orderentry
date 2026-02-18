"""
Daviselen Order Automation
Browser automation for entering Daviselen agency orders into Etere.

═══════════════════════════════════════════════════════════════════════════════
DAVISELEN BUSINESS RULES
═══════════════════════════════════════════════════════════════════════════════

Known Customers:
    1. So Cal Toyota (ID: 362) → LAX market ONLY
       - Separation: 25, 0, 0
       - Products: Toyota campaigns, general media
    
    2. Seattle McDonald's (ID: 122) → SEA market ONLY
       - Client: "WESTERN WASHINGTON OP. ASSOC."
       - Separation: 15, 0, 0
    
    3. WDC McDonald's (ID: 416) → WDC market ONLY
       - Client: "CAPITAL BUSINESS UNIT"
       - Separation: 15, 0, 0
    
    4. SoCal McDonald's (ID: 368) → LAX market ONLY
       - Client: "MCD'S OP. ASSOC. OF SO. CAL."
       - Separation: 15, 0, 0

Billing (Universal for ALL Daviselen):
    - Charge To: "Agency with Credit Note"
    - Invoice Header: "Customer"

Contract Format:
    - Code: "Daviselen {client_abbrev} {estimate}"
    - Description: "{market} {client_abbrev} Est {estimate}"
    - Notes: Auto-populated from order details

═══════════════════════════════════════════════════════════════════════════════
IMPORTS - Universal utilities, no duplication
═══════════════════════════════════════════════════════════════════════════════
"""

import os
import sys
import math
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional

# Add project root to path
project_root = Path(__file__).parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from etere_client import EtereClient
from selenium.webdriver.common.by import By
from ros_definitions import ROS_SCHEDULES
from language_utils import (
    extract_language_from_program,
)
from src.domain.enums import BillingType, OrderType, SeparationInterval

from browser_automation.parsers.daviselen_parser import (
    parse_daviselen_pdf,
    DaviselenOrder,
    DaviselenLine,
    format_time_for_description,
    analyze_weekly_distribution,
)


# ═══════════════════════════════════════════════════════════════════════════════
# CUSTOMER DATABASE ACCESS
# ═══════════════════════════════════════════════════════════════════════════════

# Default database path
CUSTOMER_DB_PATH = os.path.join("data", "customers.db")


def lookup_customer(
    client_name: str,
    db_path: str = CUSTOMER_DB_PATH
) -> Optional[dict]:
    """
    Look up customer in the database.
    
    Uses CustomerRepository for database access with fuzzy matching support.
    Falls back to hardcoded defaults for known customers if database unavailable.
    
    Args:
        client_name: Full client name from PDF
        db_path: Path to customers.db
        
    Returns:
        Dict with customer info or None if not found
    """
    # Try database first
    if os.path.exists(db_path):
        try:
            from src.data_access.repositories.customer_repository import CustomerRepository
            from src.domain.entities import Customer
            
            repo = CustomerRepository(db_path)
            
            # Try exact match
            customer = repo.find_by_name(client_name, OrderType.DAVISELEN)
            
            if customer:
                return {
                    'customer_id': customer.customer_id,
                    'abbreviation': customer.abbreviation,
                    'market': customer.default_market,
                    'separation': (
                        customer.separation_customer,
                        customer.separation_event,
                        customer.separation_order
                    ),
                    'billing_type': customer.billing_type,
                }
            
            # Try fuzzy match
            customer = repo.find_by_name_fuzzy(client_name, OrderType.DAVISELEN)
            
            if customer:
                return {
                    'customer_id': customer.customer_id,
                    'abbreviation': customer.abbreviation,
                    'market': customer.default_market,
                    'separation': (
                        customer.separation_customer,
                        customer.separation_event,
                        customer.separation_order
                    ),
                    'billing_type': customer.billing_type,
                }
        
        except Exception as e:
            print(f"[CUSTOMER DB] ⚠ Database lookup failed: {e}")
    
    # Fallback: Hardcoded defaults for known customers
    # This ensures the system works even without database
    KNOWN_CUSTOMERS = {
        "SO. CAL. TDA": ('362', 'SoCal', 'LAX', (25, 0, 0)),
        "SCTDA": ('362', 'SoCal', 'LAX', (25, 0, 0)),
        "WESTERN WASHINGTON OP. ASSOC.": ('122', 'McD', 'SEA', (15, 0, 0)),
        "DMWW": ('122', 'McD', 'SEA', (15, 0, 0)),
        "CAPITAL BUSINESS UNIT": ('416', 'McD', 'WDC', (15, 0, 0)),
        "DCBU": ('416', 'McD', 'WDC', (15, 0, 0)),
        "MCD'S OP. ASSOC. OF SO. CAL.": ('368', 'McD', 'LAX', (15, 0, 0)),
        "DMLA": ('368', 'McD', 'LAX', (15, 0, 0)),
    }
    
    # Try exact match
    client_upper = client_name.upper()
    if client_upper in KNOWN_CUSTOMERS:
        cust_id, abbrev, market, sep = KNOWN_CUSTOMERS[client_upper]
        return {
            'customer_id': cust_id,
            'abbreviation': abbrev,
            'market': market,
            'separation': sep,
            'billing_type': 'agency',
        }
    
    # Try fuzzy match
    for known_name, (cust_id, abbrev, market, sep) in KNOWN_CUSTOMERS.items():
        if known_name in client_upper or client_upper in known_name:
            return {
                'customer_id': cust_id,
                'abbreviation': abbrev,
                'market': market,
                'separation': sep,
                'billing_type': 'agency',
            }
    
    return None


def save_new_customer(
    customer_id: str,
    customer_name: str,
    abbreviation: str,
    market: str,
    separation: tuple,
    db_path: str = CUSTOMER_DB_PATH,
) -> None:
    """
    Save a new customer to the database.
    
    Args:
        customer_id: Etere customer ID
        customer_name: Full customer name
        abbreviation: Short code (e.g., "SoCal", "McD")
        market: Market code (e.g., "LAX", "SEA")
        separation: Tuple of (customer, event, order) separation minutes
        db_path: Path to customers.db
    """
    try:
        from src.data_access.repositories.customer_repository import CustomerRepository
        from src.domain.entities import Customer
        
        repo = CustomerRepository(db_path)
        
        customer = Customer(
            customer_id=customer_id,
            customer_name=customer_name,
            order_type=OrderType.DAVISELEN,
            abbreviation=abbreviation,
            default_market=market,
            billing_type='agency',  # All Daviselen = agency
            separation_customer=separation[0],
            separation_event=separation[1],
            separation_order=separation[2],
        )
        
        repo.save(customer)
        print(f"[CUSTOMER DB] ✓ Saved: {customer_name} → ID {customer_id}")
        
    except Exception as e:
        print(f"[CUSTOMER DB] ✗ Save failed: {e}")


# ═══════════════════════════════════════════════════════════════════════════════
# MARKET MAPPING
# ═══════════════════════════════════════════════════════════════════════════════

def map_market_to_code(market_name: str) -> str:
    """
    Map market name or code to standard market code.
    
    Args:
        market_name: Market name from PDF (e.g., "LOS ANGELES, CA") or code
        
    Returns:
        Market code (e.g., "LAX", "SEA", "WDC")
    """
    market_upper = market_name.upper().strip()
    
    # Already a valid market code
    if market_upper in ["LAX", "SEA", "SFO", "HOU", "NYC", "CVC", "DAL", "WDC", "MMT", "CMP"]:
        return market_upper
    
    # Market code aliases
    aliases = {
        "WAS": "WDC",
        "LA": "LAX",
    }
    if market_upper in aliases:
        return aliases[market_upper]
    
    # Map from market name
    if "LOS ANGELES" in market_upper:
        return "LAX"
    elif "SEATTLE" in market_upper or "TACOMA" in market_upper:
        return "SEA"
    elif "SAN FRANCISCO" in market_upper:
        return "SFO"
    elif "HOUSTON" in market_upper:
        return "HOU"
    elif "NEW YORK" in market_upper:
        return "NYC"
    elif "SACRAMENTO" in market_upper or "CENTRAL VALLEY" in market_upper:
        return "CVC"
    elif "DALLAS" in market_upper:
        return "DAL"
    elif "WASHINGTON" in market_upper and "DC" in market_upper:
        return "WDC"
    elif "HAGRSTWN" in market_upper:  # Hagerstown = WDC market
        return "WDC"
    else:
        return "UNKNOWN"


# ═══════════════════════════════════════════════════════════════════════════════
# UPFRONT INPUT COLLECTION
# ═══════════════════════════════════════════════════════════════════════════════

def gather_daviselen_inputs(pdf_path: str) -> dict:
    """
    Gather ALL user inputs BEFORE browser automation starts.
    
    This function:
    1. Parses the PDF to extract order details
    2. Auto-detects customer from database
    3. Prompts for any missing information
    4. Prepares all data needed for unattended automation
    
    Args:
        pdf_path: Path to Daviselen PDF
        
    Returns:
        Dictionary with all inputs needed for automation
    """
    print("\n" + "="*70)
    print("DAVISELEN ORDER - UPFRONT INPUT COLLECTION")
    print("="*70)
    
    # Parse PDF
    print("\n[PARSE] Reading PDF...")
    try:
        order = parse_daviselen_pdf(pdf_path)
    except Exception as e:
        print(f"[PARSE] ✗ Failed: {e}")
        return None
    
    print(f"[PARSE] ✓ Order: {order.order_number}")
    print(f"[PARSE] ✓ Client: {order.client}")
    print(f"[PARSE] ✓ Estimate: {order.estimate_number}")
    print(f"[PARSE] ✓ Flight: {order.flight_start} - {order.flight_end}")
    print(f"[PARSE] ✓ Lines: {len(order.lines)}")
    
    # Detect market
    market = map_market_to_code(order.market)
    print(f"[MARKET] ✓ Detected: {market}")
    
    # Lookup customer
    customer = lookup_customer(order.client, order.client_code)
    
    if customer:
        print(f"\n[CUSTOMER] ✓ Found in database:")
        print(f"  ID: {customer['customer_id']}")
        print(f"  Abbreviation: {customer['abbreviation']}")
        print(f"  Market: {customer['market']}")
        print(f"  Separation: {customer['separation']}")
        
        customer_id = customer['customer_id']
        abbreviation = customer['abbreviation']
        separation = customer['separation']
        
        # Verify market matches
        if customer['market'] != market:
            print(f"\n[WARNING] Market mismatch!")
            print(f"  Customer default: {customer['market']}")
            print(f"  PDF market: {market}")
            print(f"  Using PDF market: {market}")
    
    else:
        print(f"\n[CUSTOMER] ✗ Not found in database: {order.client}")
        print("Please enter customer details:")
        
        customer_id = input("  Customer ID: ").strip()
        abbreviation = input("  Abbreviation (e.g., SoCal, McD): ").strip()
        
        print("\nSeparation intervals (minutes):")
        cust_sep = input("  Customer separation [15]: ").strip() or "15"
        event_sep = input("  Event separation [0]: ").strip() or "0"
        order_sep = input("  Order separation [0]: ").strip() or "0"
        
        separation = (int(cust_sep), int(event_sep), int(order_sep))
        
        # Save to database for future orders
        save_new_customer(
            customer_id=customer_id,
            customer_name=order.client,
            abbreviation=abbreviation,
            market=market,
            separation=separation,
        )
    
    # Contract code and description with smart defaults
    client_upper = order.client.upper()
    estimate = order.estimate_number
    
    # Customer Order Ref format: "Order 23924, Est 46"
    customer_order_ref = f"Order {order.order_number}, Est {estimate}"
    
    # Generate smart defaults based on client
    if customer:
        # Use abbreviation from database
        abbrev = customer['abbreviation']
        if abbrev == 'McD':
            # McDonald's - use market-specific label
            if market == "SEA":
                market_label = "WA"
            elif market == "LAX":
                market_label = "LAX"
            elif market == "WDC":
                market_label = "DC"
            else:
                market_label = market
            suggested_code = f"Daviselen McD {estimate}"
            suggested_desc = f"McDonald's {market_label} Est {estimate}"
        elif abbrev == 'SoCal':
            suggested_code = f"Daviselen Toyota {estimate}"
            suggested_desc = f"So Cal Toyota Est {estimate}"
        else:
            suggested_code = f"Daviselen {abbrev} {estimate}"
            suggested_desc = f"{abbrev} Est {estimate}"
    else:
        # No customer found - check client name for patterns
        if ("MCDONALD" in client_upper or "MCD" in client_upper or 
            "WESTERN WASHINGTON" in client_upper or "CAPITAL BUSINESS UNIT" in client_upper):
            # McDonald's
            if market == "SEA":
                market_label = "WA"
            elif market == "LAX":
                market_label = "LAX"
            elif market == "WDC":
                market_label = "DC"
            else:
                market_label = market
            suggested_code = f"Daviselen McD {estimate}"
            suggested_desc = f"McDonald's {market_label} Est {estimate}"
        elif "TOYOTA" in client_upper or "SCTDA" in client_upper:
            # Toyota
            suggested_code = f"Daviselen Toyota {estimate}"
            suggested_desc = f"So Cal Toyota Est {estimate}"
        else:
            # Generic fallback
            suggested_code = f"Daviselen {estimate}"
            suggested_desc = f"{order.client} Est {estimate}"
    
    print(f"\n[CONTRACT]")
    contract_code = input(f"  Code [{suggested_code}]: ").strip() or suggested_code
    description = input(f"  Description [{suggested_desc}]: ").strip() or suggested_desc
    
    # Notes (auto-populate from order - Etere standard format)
    # Format:
    # CLIENT <code> <name>
    # PRODUCT <code> <name>
    # ESTIMATE <number> <detail>
    
    client_line = "CLIENT "
    if order.client_code:
        client_line += f"{order.client_code} "
    client_line += order.client
    
    product_line = "PRODUCT "
    if order.product_code:
        product_line += f"{order.product_code} "
    product_line += order.product
    
    estimate_line = f"ESTIMATE {order.estimate_number}"
    if order.estimate_detail:
        estimate_line += f" {order.estimate_detail}"
    
    notes = f"{client_line}\n{product_line}\n{estimate_line}"
    
    print(f"  Notes:")
    for line in notes.split('\n'):
        print(f"    {line}")
    
    # Billing (UNIVERSAL for ALL agency orders)
    billing = BillingType.CUSTOMER_SHARE_AGENCY
    print(f"\n[BILLING] ✓ Customer share indicating agency % / Agency")
    
    print("\n" + "="*70)
    print("INPUT COLLECTION COMPLETE - Ready for automation")
    print("="*70)
    
    return {
        'order': order,
        'customer_id': customer_id,
        'market': market,
        'contract_code': contract_code,
        'contract_description': description,
        'customer_order_ref': customer_order_ref,
        'notes': notes,
        'billing': billing,
        'separation': separation,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# BROWSER AUTOMATION
# ═══════════════════════════════════════════════════════════════════════════════

def process_daviselen_order(
    driver,
    pdf_path: str,
    user_input: dict = None
) -> bool:
    """
    Process Daviselen order with completely unattended automation.
    
    Matches TCAA pattern for consistency across all automations.
    
    Workflow:
    1. Use pre-collected inputs (from orchestrator) OR gather them now
    2. Start browser automation (no interruptions)
    3. Create contract header
    4. Add all contract lines with week consolidation
    5. Return success status
    
    Args:
        driver: Selenium WebDriver (raw driver, not session)
        pdf_path: Path to Daviselen PDF
        user_input: Pre-collected inputs from orchestrator (optional)
        
    Returns:
        True if successful, False otherwise
    """
    # ═══════════════════════════════════════════════════════════════
    # GET INPUTS (pre-collected OR gather now)
    # ═══════════════════════════════════════════════════════════════
    
    if user_input is None:
        # Not called from orchestrator - gather inputs now
        user_input = gather_daviselen_inputs(pdf_path)
        if not user_input:
            return False
    
    order = user_input['order']
    
    # ═══════════════════════════════════════════════════════════════
    # BROWSER AUTOMATION (COMPLETELY UNATTENDED)
    # ═══════════════════════════════════════════════════════════════
    
    print("\n" + "="*70)
    print("STARTING BROWSER AUTOMATION")
    print("="*70)
    
    all_success = True
    
    # Create Etere client (just like TCAA does)
    etere = EtereClient(driver)
    
    try:
        # Master market already set by session (NYC for Crossings TV)
        # Individual lines will use their specific market (SEA, LAX, WDC, etc.)
        
        # ═══════════════════════════════════════════════════════════
        # CREATE CONTRACT HEADER
        # ═══════════════════════════════════════════════════════════
        
        billing = user_input['billing']
        
        contract_number = etere.create_contract_header(
            customer_id=int(user_input['customer_id']),
            code=user_input['contract_code'],
            description=user_input['contract_description'],
            contract_start=order.flight_start,
            contract_end=order.flight_end,
            customer_order_ref=user_input['customer_order_ref'],
            notes=user_input['notes'],
            charge_to=billing.get_charge_to(),
            invoice_header=billing.get_invoice_header(),
        )
        
        if not contract_number:
            print("[CONTRACT] ✗ Failed to create contract")
            return False
        
        print(f"[CONTRACT] ✓ Created: {contract_number}")
        
        # ═══════════════════════════════════════════════════════════
        # ADD CONTRACT LINES
        # ═══════════════════════════════════════════════════════════
        
        separation = user_input['separation']
        market = user_input['market']
        
        line_num = 0
        for line in order.lines:
            line_num += 1
            
            # Extract language from program name
            language = extract_language_from_program(line.program)
            
            # Determine if bonus
            is_bonus = line.is_bonus()
            spot_code = 10 if is_bonus else 2
            
            print(f"\n[LINE {line_num}] {'BNS' if is_bonus else 'PAID'} {language}")
            print(f"  Days: {line.days}, Time: {line.time}")
            print(f"  Duration: :{line.duration}s")
            
            # Apply Sunday 6-7a rule
            days, _ = EtereClient.check_sunday_6_7a_rule(line.days, line.time)
            
            # Parse time range (handles semicolons, compressed formats)
            time_from, time_to = EtereClient.parse_time_range(line.time)
            
            # Format time for description (6:00a-7:00a → 6-7a)
            time_formatted = format_time_for_description(line.time)
            
            # Build description: (Line XXX) DAYS TIME [BNS] PROGRAM
            # Remove leading zeros from line number (001 → 1)
            line_num_clean = str(int(line.line_number))
            desc_parts = [f"(Line {line_num_clean})", days, time_formatted]
            
            # Add BNS prefix for bonus spots
            if is_bonus:
                desc_parts.append("BNS")
            
            # Add program name
            desc_parts.append(line.program)
            
            description = " ".join(desc_parts)
            
            # ═══════════════════════════════════════════════════════
            # CONSOLIDATED LINE ENTRY
            # ═══════════════════════════════════════════════════════
            # Group consecutive weeks with same spot count into single lines
            
            week_groups = analyze_weekly_distribution(
                line.weekly_spots, order.week_start_dates, order.flight_end
            )
            
            for group in week_groups:
                group_start = group['start_date']
                group_end = group['end_date']
                group_spots_per_week = group['spots_per_week']
                group_total = group['spots']
                group_weeks = group['num_weeks']
                
                print(f"  {group_start} - {group_end} ({group_weeks} wk): {group_spots_per_week}/wk, {group_total} total")
                
                success = etere.add_contract_line(
                    contract_number=contract_number,
                    market=market,
                    start_date=group_start,
                    end_date=group_end,
                    days=days,
                    time_from=time_from,
                    time_to=time_to,
                    description=description,
                    spot_code=spot_code,
                    duration_seconds=line.duration,
                    total_spots=group_total,
                    spots_per_week=group_spots_per_week,
                    rate=line.rate,                    separation_intervals=separation,
                )
                
                if not success:
                    print(f"  [LINE {line_num}] ✗ Failed for {group_start} - {group_end}")
                    all_success = False
        
        print(f"\n[COMPLETE] Contract {contract_number} — {line_num} lines processed")
    
    except Exception as e:
        print(f"\n[ERROR] Browser automation failed: {e}")
        import traceback
        traceback.print_exc()
        return False
    
    return all_success


# ═══════════════════════════════════════════════════════════════════════════════
# STANDALONE ENTRY POINT
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import sys
    
    print("="*70)
    print("DAVISELEN AUTOMATION - STANDALONE MODE NOT SUPPORTED")
    print("="*70)
    print()
    print("This automation must be run through the orchestrator (main.py)")
    print("which provides the browser session.")
    print()
    print("To process Daviselen orders:")
    print("  1. Place PDF in incoming\\ folder")
    print("  2. Run: python main.py")
    print("  3. Select the Daviselen order from the menu")
    print()
    print("For testing/development, you can call process_daviselen_order()")
    print("directly with a browser driver session.")
    print("="*70)
    sys.exit(1)
