"""
Charmaine Client Automation
Browser automation for entering Charmaine's client orders into Etere.

═══════════════════════════════════════════════════════════════════════════════
OVERVIEW
═══════════════════════════════════════════════════════════════════════════════

This is a GENERIC template for Charmaine's Excel-based insertion orders.
Unlike agency-specific automation files (TCAA, Misfit, Sagent), this template:

    1. Does NOT hardcode a customer ID — looks up or prompts for each client
    2. Detects AGENCY vs CLIENT orders — no agency name = likely client
    3. Stores client defaults in customers.db for future orders
    4. Works with ANY market (detected from PDF)

BILLING RULES (Universal):
    - Agency order:  Charge To = "Customer share indicating agency %"
                     Invoice Header = "Agency"
    - Client order:  Charge To = "Customer"
                     Invoice Header = "Customer"

═══════════════════════════════════════════════════════════════════════════════
IMPORTS - Universal utilities, no duplication
═══════════════════════════════════════════════════════════════════════════════
"""

import sqlite3
import os
import math
from datetime import datetime, timedelta
from typing import Optional

from browser_automation.etere_client import EtereClient
from selenium.webdriver.common.by import By
from browser_automation.ros_definitions import ROS_SCHEDULES
from browser_automation.language_utils import (
    get_language_block_prefixes,
    extract_language_from_program,
)
from src.domain.enums import BillingType, OrderType, OrderBillingType, detect_order_billing_type

from browser_automation.parsers.charmaine_parser import (
    parse_charmaine_pdf,
    CharmaineOrder,
    CharmaineLine,
)


# ═══════════════════════════════════════════════════════════════════════════════
# CONSTANTS
# ═══════════════════════════════════════════════════════════════════════════════

# Default customer DB path (relative to project root)
CUSTOMER_DB_PATH = os.path.join("data", "customers.db")

# Known agency keywords - if detected, order type = AGENCY
# (Also defined in enums.py — this is for quick reference)
KNOWN_AGENCIES = [
    "worldlink", "tatari", "tcaa", "daviselen", "misfit",
    "igraphix", "admerasia", "opad", "rpm", "h&l partners",
    "impact marketing", "sagent", "galeforce", "galeforcemedia",
    "ntooitive",
]


# ═══════════════════════════════════════════════════════════════════════════════
# CUSTOMER DATABASE HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

def lookup_customer(
    advertiser: str,
    db_path: str = CUSTOMER_DB_PATH
) -> Optional[dict]:
    """
    Look up customer in the database by name (case-insensitive fuzzy match).
    
    Args:
        advertiser: Advertiser name from the PDF
        db_path: Path to customers.db
        
    Returns:
        Dict with customer info or None if not found
    """
    if not os.path.exists(db_path):
        return None
    
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        # Exact match first (case-insensitive)
        cursor.execute(
            "SELECT * FROM customers WHERE LOWER(customer_name) = LOWER(?)",
            (advertiser,)
        )
        row = cursor.fetchone()
        
        if row:
            result = dict(row)
            conn.close()
            return result
        
        # Partial match: check if advertiser contains or is contained by any name
        cursor.execute("SELECT * FROM customers")
        all_rows = cursor.fetchall()
        conn.close()
        
        adv_lower = advertiser.lower()
        for row in all_rows:
            name_lower = row['customer_name'].lower()
            if name_lower in adv_lower or adv_lower in name_lower:
                return dict(row)
        
        return None
        
    except Exception as e:
        print(f"[CUSTOMER DB] ⚠ Lookup error: {e}")
        return None


def save_new_customer(
    customer_id: str,
    customer_name: str,
    order_type: str,
    abbreviation: str = "",
    default_market: Optional[str] = None,
    billing_type: str = "client",
    separation_customer: int = 15,
    separation_event: int = 0,
    separation_order: int = 0,
    db_path: str = CUSTOMER_DB_PATH,
) -> None:
    """
    Save a new customer to the database.
    
    Args:
        customer_id: Etere customer ID
        customer_name: Full customer name
        order_type: "charmaine" or specific agency
        abbreviation: Short code (e.g., "SRCF")
        default_market: Default market code or None
        billing_type: "agency" or "client"
        separation_customer: Customer separation minutes
        separation_event: Event separation minutes
        separation_order: Order separation minutes
        db_path: Path to customers.db
    """
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        cursor.execute(
            """INSERT OR REPLACE INTO customers 
               (customer_id, customer_name, order_type, abbreviation, 
                default_market, billing_type, separation_customer, 
                separation_event, separation_order)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (customer_id, customer_name, order_type, abbreviation,
             default_market, billing_type, separation_customer,
             separation_event, separation_order)
        )
        
        conn.commit()
        conn.close()
        print(f"[CUSTOMER DB] ✓ Saved: {customer_name} → ID {customer_id}")
        
    except Exception as e:
        print(f"[CUSTOMER DB] ✗ Save failed: {e}")


def _update_customer_id(
    customer_name: str,
    new_id: str,
    db_path: str = CUSTOMER_DB_PATH,
) -> None:
    """
    Update the customer ID in the database after browser selection.
    
    Called when a customer was saved with ID="SEARCH" and the real
    Etere ID was determined during contract creation.
    
    Args:
        customer_name: Customer name to match
        new_id: The actual Etere customer ID
        db_path: Path to customers.db
    """
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        # Update by exact name match
        cursor.execute(
            "UPDATE customers SET customer_id = ? WHERE customer_name = ?",
            (new_id, customer_name)
        )
        
        # Also try partial match if exact didn't update
        if cursor.rowcount == 0:
            cursor.execute(
                "UPDATE customers SET customer_id = ? WHERE customer_name LIKE ?",
                (new_id, f"%{customer_name}%")
            )
        
        conn.commit()
        updated = cursor.rowcount
        conn.close()
        
        if updated > 0:
            print(f"[CUSTOMER DB] ✓ Updated {customer_name} → ID {new_id}")
        else:
            print(f"[CUSTOMER DB] ⚠ No matching customer found to update")
            
    except Exception as e:
        print(f"[CUSTOMER DB] ✗ Update failed: {e}")


# ═══════════════════════════════════════════════════════════════════════════════
# DAY PATTERN HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

def daypart_to_days(daypart: str) -> str:
    """
    Extract day pattern from a Charmaine daypart string.
    
    Handles semicolon-separated patterns by taking the broadest range.
    
    Examples:
        "M-F 7p-11p; Sat-Sun 7p-12a"  → "M-Su"  (M-F + Sa-Su = M-Su)
        "M-F 4p-7p; Sat-Sun 4p-6p"    → "M-Su"
        "Sat-Sun 6p-8p"               → "Sa-Su"
        "M-Sun 11a-1p"                → "M-Su"
        "M-F 10a-11a"                 → "M-F"
    
    Args:
        daypart: Daypart string from PDF
        
    Returns:
        Day pattern string for Etere
    """
    dp_lower = daypart.lower().strip()
    
    # Check for combined weekday + weekend patterns (semicolon)
    has_weekday = any(kw in dp_lower for kw in ['m-f', 'mon-fri', 'm-sa'])
    has_weekend = any(kw in dp_lower for kw in ['sat-sun', 'sa-su', 'sat-su'])
    has_full_week = any(kw in dp_lower for kw in ['m-su', 'm-sun', 'mon-sun'])
    
    if has_full_week:
        return "M-Su"
    elif has_weekday and has_weekend:
        return "M-Su"
    elif has_weekday:
        if 'm-sa' in dp_lower:
            return "M-Sa"
        return "M-F"
    elif has_weekend:
        return "Sa-Su"
    else:
        return "M-Su"  # Default fallback


def daypart_to_time_range(daypart: str) -> str:
    """
    Extract time range from a Charmaine daypart string.
    
    For multi-range dayparts (semicolons, commas, "and"), finds ALL time ranges
    and returns earliest start to latest end.
    
    Examples:
        "M-F 7p-11p; Sat-Sun 7p-12a"           → "7p-12a"
        "M-F 6a-7a,7p-8p and Sat-Sun 8p-9p"    → "6a-9p"
        "Sat-Sun 6p-8p"                          → "6p-8p"
        "M-Sun 11a-1p"                           → "11a-1p"
    
    Args:
        daypart: Daypart string from PDF (may contain newlines cleaned to spaces)
        
    Returns:
        Time range string
    """
    import re
    
    # Clean up the daypart
    dp = ' '.join(daypart.split())  # Normalize whitespace
    
    # Find ALL time range patterns in the string
    # Match: digits[optional :minutes][optional a/p] - digits[optional :minutes][a/p]
    all_ranges = re.findall(
        r'(\d{1,2}(?::\d{2})?[ap]?)\s*-\s*(\d{1,2}(?::\d{2})?[ap])',
        dp, re.IGNORECASE
    )
    
    if not all_ranges:
        return "6a-11:59p"  # Fallback to full ROS
    
    if len(all_ranges) == 1:
        return f"{all_ranges[0][0]}-{all_ranges[0][1]}"
    
    # Multiple ranges found — return earliest start to latest end
    # Just return them semicolon-separated so etere_client handles the logic
    range_strs = [f"{r[0]}-{r[1]}" for r in all_ranges]
    return '; '.join(range_strs)


# ═══════════════════════════════════════════════════════════════════════════════
# LANGUAGE NORMALIZATION
# ═══════════════════════════════════════════════════════════════════════════════

def normalize_language(language: str) -> str:
    """
    Normalize language name from PDF to standard system name.
    
    Handles variations like "Chinese ( Mandarin)" → "Chinese"
    
    Args:
        language: Raw language string from PDF
        
    Returns:
        Normalized language name
    """
    lang = language.strip()
    
    # "Chinese ( Mandarin)" → "Chinese"
    if 'chinese' in lang.lower() or 'mandarin' in lang.lower():
        return "Chinese"
    elif 'cantonese' in lang.lower():
        return "Cantonese"
    elif 'filipino' in lang.lower() or 'tagalog' in lang.lower():
        return "Filipino"
    elif 'vietnamese' in lang.lower():
        return "Vietnamese"
    elif 'korean' in lang.lower():
        return "Korean"
    elif 'hmong' in lang.lower():
        return "Hmong"
    elif 'south asian' in lang.lower() or 'hindi' in lang.lower() or 'punjabi' in lang.lower():
        return "South Asian"
    elif 'japanese' in lang.lower():
        return "Japanese"
    
    return lang


# ═══════════════════════════════════════════════════════════════════════════════
# WEEK DATE CALCULATION
# ═══════════════════════════════════════════════════════════════════════════════

# ═══════════════════════════════════════════════════════════════════════════════
# USER INPUT COLLECTION
# ═══════════════════════════════════════════════════════════════════════════════

def collect_user_input(order: CharmaineOrder) -> dict:
    """
    Collect all user input BEFORE starting browser automation.
    
    This ensures unattended processing once Etere entry begins.
    
    Args:
        order: Parsed CharmaineOrder
        
    Returns:
        Dict with all user-confirmed settings
    """
    print("\n" + "=" * 70)
    print("CHARMAINE CLIENT ORDER")
    print("=" * 70)
    print(f"  Advertiser: {order.advertiser}")
    print(f"  Campaign:   {order.campaign}")
    print(f"  Market:     {order.market}")
    print(f"  Duration:   :{order.duration_seconds}s")
    print(f"  Flight:     {order.flight_start} - {order.flight_end}")
    print(f"  Lines:      {len(order.lines)} ({sum(1 for l in order.lines if not l.is_bonus)} paid + {sum(1 for l in order.lines if l.is_bonus)} bonus)")
    print("=" * 70)
    
    # ═══════════════════════════════════════════════════════════════
    # DETECT AGENCY VS CLIENT
    # ═══════════════════════════════════════════════════════════════
    
    # Build full PDF text for detection
    full_text = f"{order.advertiser} {order.campaign} {order.contact} {order.email}"
    detected_type, matched_keyword = detect_order_billing_type(full_text)
    
    if detected_type == OrderBillingType.AGENCY:
        print(f"\n[BILLING] Agency detected: '{matched_keyword}' → Agency billing")
        order_type = OrderBillingType.AGENCY
    else:
        print(f"\n[BILLING] No agency detected — this appears to be a CLIENT order.")
        confirm = input("  Is this a client (direct) order? (Y/n): ").strip().lower()
        if confirm in ('', 'y', 'yes'):
            order_type = OrderBillingType.CLIENT
        else:
            order_type = OrderBillingType.AGENCY
    
    billing = order_type.get_billing_type()
    print(f"  Charge To:      {billing.get_charge_to()}")
    print(f"  Invoice Header: {billing.get_invoice_header()}")
    
    # ═══════════════════════════════════════════════════════════════
    # CUSTOMER LOOKUP
    # ═══════════════════════════════════════════════════════════════
    
    customer_info = lookup_customer(order.advertiser)
    
    customer_id = None
    abbreviation = ""
    separation = (15, 0, 0)
    
    if customer_info:
        print(f"\n[CUSTOMER] Found in database:")
        print(f"  Name:         {customer_info['customer_name']}")
        print(f"  ID:           {customer_info['customer_id']}")
        print(f"  Abbreviation: {customer_info.get('abbreviation', 'N/A')}")
        
        confirm = input("  Use this customer? (Y/n): ").strip().lower()
        if confirm in ('', 'y', 'yes'):
            customer_id = customer_info['customer_id']
            abbreviation = customer_info.get('abbreviation', '')
            sep_c = customer_info.get('separation_customer', 15)
            sep_e = customer_info.get('separation_event', 0)
            sep_o = customer_info.get('separation_order', 0)
            separation = (sep_c, sep_e, sep_o)
    
    if customer_id is None:
        print(f"\n[CUSTOMER] New client: '{order.advertiser}'")
        print("  Options:")
        print("    1. Enter Etere customer ID directly")
        print("    2. Search in Etere (manual selection in browser)")
        
        choice = input("  Choice (1/2): ").strip()
        
        if choice == "1":
            customer_id = input("  Enter Etere customer ID: ").strip()
        else:
            customer_id = None  # Will trigger browser search
        
        # Get abbreviation for contract code
        abbreviation = input(f"  Abbreviation for contract codes (e.g., SRCF): ").strip()
        
        # Confirm separation intervals
        print(f"\n  Separation intervals (default: 15, 0, 0)")
        sep_input = input("  Customer,Event,Order (or Enter for defaults): ").strip()
        if sep_input:
            parts = sep_input.split(',')
            separation = (
                int(parts[0].strip()) if len(parts) > 0 else 15,
                int(parts[1].strip()) if len(parts) > 1 else 0,
                int(parts[2].strip()) if len(parts) > 2 else 0,
            )
        
        # Save to database
        save = input("  Save this client for future orders? (Y/n): ").strip().lower()
        if save in ('', 'y', 'yes'):
            save_new_customer(
                customer_id=customer_id or "SEARCH",
                customer_name=order.advertiser,
                order_type="charmaine",
                abbreviation=abbreviation,
                default_market=order.market if order.market != "UNKNOWN" else None,
                billing_type=order_type.value,
                separation_customer=separation[0],
                separation_event=separation[1],
                separation_order=separation[2],
            )
    
    # ═══════════════════════════════════════════════════════════════
    # CONTRACT CODE & DESCRIPTION
    # ═══════════════════════════════════════════════════════════════
    
    # Build suggested code and description
    if abbreviation:
        suggested_code = f"{abbreviation} {order.campaign} {order.year}"
    else:
        suggested_code = f"{order.advertiser} {order.campaign} {order.year}"
    
    suggested_description = f"{order.advertiser} {order.campaign}"
    
    print(f"\n[CONTRACT] Suggested code: {suggested_code}")
    code_input = input(f"  Contract code (or Enter for suggested): ").strip()
    contract_code = code_input if code_input else suggested_code
    
    print(f"[CONTRACT] Suggested description: {suggested_description}")
    desc_input = input(f"  Description (or Enter for suggested): ").strip()
    contract_description = desc_input if desc_input else suggested_description
    
    # ═══════════════════════════════════════════════════════════════
    # CONTRACT NOTES
    # ═══════════════════════════════════════════════════════════════
    
    # Build default notes from PDF data
    default_notes = f"{order.advertiser} - {order.campaign}"
    if order.contact:
        default_notes += f"\nContact: {order.contact}"
    
    print(f"\n[NOTES] Default notes:")
    for line in default_notes.split('\n'):
        print(f"  {line}")
    notes_input = input("  Edit notes (or Enter to keep): ").strip()
    notes = notes_input if notes_input else default_notes
    
    # ═══════════════════════════════════════════════════════════════
    # SOUTH ASIAN DISAMBIGUATION
    # ═══════════════════════════════════════════════════════════════
    
    hindi_punjabi = None
    has_south_asian = any(
        normalize_language(line.language) == "South Asian"
        for line in order.lines
    )
    
    if has_south_asian:
        print("\n[LANGUAGE] South Asian programming detected.")
        print("  Block options: 1=Hindi (SA), 2=Punjabi (P), 3=Both (SA+P)")
        sa_choice = input("  Choice (1/2/3, default=3): ").strip()
        if sa_choice == "1":
            hindi_punjabi = "Hindi"
        elif sa_choice == "2":
            hindi_punjabi = "Punjabi"
        else:
            hindi_punjabi = "Both"
    
    # ═══════════════════════════════════════════════════════════════
    # DAYPART CORRECTIONS (garbled PDF text)
    # ═══════════════════════════════════════════════════════════════
    # Scan paid lines for unparseable dayparts and prompt upfront
    # so line entry can proceed unattended.
    
    daypart_corrections = {}  # keyed by line index
    
    for idx, line in enumerate(order.lines):
        if line.is_bonus:
            continue
        
        daypart_clean = ' '.join(line.daypart.split())
        time_range = daypart_to_time_range(daypart_clean)
        days = daypart_to_days(daypart_clean)
        
        # If time parsing fell back to default, the daypart is likely garbled
        if time_range == "6a-11:59p" and daypart_clean:
            program_name = ' '.join(line.language.split())
            print(f"\n[DAYPART] ⚠ Could not parse daypart for {program_name}:")
            print(f"  Raw text: \"{daypart_clean}\"")
            print(f"  Fallback: {days} {time_range}")
            
            user_time = input(f"  Enter correct time range (e.g., 7p-8p): ").strip()
            if user_time:
                time_range = user_time
            
            user_days = input(f"  Enter correct days (e.g., M-F) or Enter to keep [{days}]: ").strip()
            if user_days:
                days = user_days
            
            daypart_corrections[idx] = {
                'days': days,
                'time_range': time_range,
            }
            print(f"  → Corrected to: {days} {time_range}")
    
    # ═══════════════════════════════════════════════════════════════
    # BONUS LINE OVERRIDES
    # ═══════════════════════════════════════════════════════════════
    # For bonus lines with specific time ranges on the PDF (not just "ROS"),
    # ask the user whether to use standard ROS defaults or the listed times.
    
    bonus_overrides = {}  # keyed by line index
    
    for idx, line in enumerate(order.lines):
        if not line.is_bonus:
            continue
        
        language = normalize_language(line.language)
        daypart_raw = line.daypart.strip()
        
        # Clean up newlines/extra whitespace from PDF rendering
        daypart_clean = ' '.join(daypart_raw.split())
        
        # Check if the daypart has specific time info beyond just "ROS"
        # Generic ROS labels: "Chinese ROS Bonus", "ROS Bonus", "BONUS", etc.
        daypart_lower = daypart_clean.lower()
        is_generic_ros = (
            not daypart_clean
            or 'ros bonus' in daypart_lower
            or daypart_lower in ('bonus', 'ros', 'bns')
            or all(word in daypart_lower for word in ['ros'])
            and not any(c.isdigit() for c in daypart_clean)
        )
        
        # If there are actual time digits in the daypart, it has specific times
        has_specific_times = any(c.isdigit() for c in daypart_clean) and not is_generic_ros
        
        if has_specific_times:
            # Extract what the PDF says for display
            pdf_days = daypart_to_days(daypart_clean)
            pdf_time = daypart_to_time_range(daypart_clean)
            
            # Get standard ROS for this language
            ros_schedule = ROS_SCHEDULES.get(language, {})
            ros_days = ros_schedule.get('days', 'M-Su')
            ros_time = ros_schedule.get('time', '6a-11:59p')
            
            print(f"\n[BONUS] {language} bonus line has specific times on PDF:")
            print(f"  PDF says:     {daypart_clean}")
            print(f"  → Parsed as:  {pdf_days} {pdf_time}")
            print(f"  Standard ROS: {ros_days} {ros_time}")
            print(f"  Options:")
            print(f"    1 = Use standard ROS defaults ({ros_days} {ros_time})")
            print(f"    2 = Use PDF time range ({pdf_days} {pdf_time})")
            
            choice = input(f"  Choice (1/2, default=2): ").strip()
            
            if choice == "1":
                # Standard ROS — no override needed
                print(f"  → Using standard {language} ROS")
            else:
                # Use PDF times — ask for custom description
                default_desc = f"BNS {language} ROS"
                print(f"  Default description: {default_desc}")
                custom_desc = input(f"  Line description (or Enter for default): ").strip()
                
                bonus_overrides[idx] = {
                    'days': pdf_days,
                    'time_range': pdf_time,
                    'description': custom_desc if custom_desc else default_desc,
                }
                print(f"  → Using PDF times with description: {bonus_overrides[idx]['description']}")
    
    # ═══════════════════════════════════════════════════════════════
    # CONFIRM AND RETURN
    # ═══════════════════════════════════════════════════════════════
    
    print("\n" + "=" * 70)
    print("READY TO PROCESS")
    print("=" * 70)
    print(f"  Code:        {contract_code}")
    print(f"  Description: {contract_description}")
    print(f"  Customer ID: {customer_id or 'SEARCH IN BROWSER'}")
    print(f"  Market:      {order.market}")
    print(f"  Billing:     {order_type.value} → {billing.get_charge_to()}")
    print(f"  Separation:  {separation}")
    if notes:
        print(f"  Notes:       {notes.split(chr(10))[0]}{'...' if chr(10) in notes else ''}")
    print("=" * 70)
    
    confirm = input("\nProceed? (Y/n): ").strip().lower()
    if confirm not in ('', 'y', 'yes'):
        print("Cancelled.")
        return {}
    
    return {
        'customer_id': customer_id,
        'contract_code': contract_code,
        'contract_description': contract_description,
        'notes': notes,
        'order_type': order_type,
        'billing_type': billing,
        'separation': separation,
        'abbreviation': abbreviation,
        'hindi_punjabi': hindi_punjabi,
        'bonus_overrides': bonus_overrides,
        'daypart_corrections': daypart_corrections,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN AUTOMATION
# ═══════════════════════════════════════════════════════════════════════════════

def process_charmaine_order(
    pdf_path: str,
    shared_session: Optional[EtereClient] = None,
) -> bool:
    """
    Process a Charmaine client order end-to-end.
    
    1. Parse PDF
    2. Collect user input (all upfront)
    3. Create contract header
    4. Add all lines (paid + bonus)
    
    Args:
        pdf_path: Path to the PDF file
        shared_session: Optional shared EtereClient (for batch processing)
        
    Returns:
        True if successful
    """
    # ═══════════════════════════════════════════════════════════════
    # STEP 1: PARSE
    # ═══════════════════════════════════════════════════════════════
    
    print(f"\n[PARSER] Reading Charmaine PDF: {pdf_path}")
    orders = parse_charmaine_pdf(pdf_path)
    
    if not orders:
        print("[PARSER] ✗ No orders found in PDF")
        return False
    
    print(f"[PARSER] Found {len(orders)} order(s)")
    
    all_success = True
    
    for order_idx, order in enumerate(orders):
        if len(orders) > 1:
            print(f"\n{'#'*70}")
            print(f"# ORDER {order_idx + 1} of {len(orders)}")
            print(f"{'#'*70}")
        
        # ═══════════════════════════════════════════════════════════
        # STEP 2: COLLECT INPUT
        # ═══════════════════════════════════════════════════════════
        
        user_input = collect_user_input(order)
        if not user_input:
            continue
        
        # ═══════════════════════════════════════════════════════════
        # STEP 3: BROWSER AUTOMATION
        # ═══════════════════════════════════════════════════════════
        
        if shared_session:
            etere = shared_session
        else:
            from selenium import webdriver
            from selenium.webdriver.chrome.options import Options
            
            chrome_options = Options()
            chrome_options.add_argument("--start-maximized")
            driver = webdriver.Chrome(options=chrome_options)
            etere = EtereClient(driver)
            etere.login()
        
        try:
            # Set master market to NYC (Crossings TV standard)
            if order.station and 'asian channel' in order.station.lower():
                etere.set_master_market("DAL")
            else:
                etere.set_master_market("NYC")
            
            # ═══════════════════════════════════════════════════════
            # CREATE CONTRACT HEADER
            # ═══════════════════════════════════════════════════════
            
            billing = user_input['billing_type']
            
            # Use notes from user input (already confirmed/edited)
            notes = user_input.get('notes', '')
            
            contract_number = etere.create_contract_header(
                customer_id=int(user_input['customer_id']) if user_input['customer_id'] and user_input['customer_id'].isdigit() else None,
                code=user_input['contract_code'],
                description=user_input['contract_description'],
                contract_start=order.flight_start,
                contract_end=order.flight_end,
                notes=notes,
                charge_to=billing.get_charge_to(),
                invoice_header=billing.get_invoice_header(),
            )
            
            if not contract_number:
                print("[CONTRACT] ✗ Failed to create contract")
                all_success = False
                continue
            
            print(f"[CONTRACT] ✓ Created: {contract_number}")
            
            # ═══════════════════════════════════════════════════════
            # UPDATE CUSTOMER ID IN DATABASE
            # ═══════════════════════════════════════════════════════
            # If customer was selected via browser search (ID was "SEARCH"),
            # read the actual ID from the contract page and update the DB.
            
            stored_id = user_input.get('customer_id', '')
            if not stored_id or stored_id == 'SEARCH' or not str(stored_id).isdigit():
                try:
                    # Read the customer ID from the contract page
                    cid_field = etere.driver.find_element(By.ID, "customerId")
                    actual_id = cid_field.get_attribute("value")
                    if actual_id and actual_id.strip().isdigit():
                        actual_id = actual_id.strip()
                        print(f"[CUSTOMER DB] Updating ID: SEARCH → {actual_id}")
                        _update_customer_id(order.advertiser, actual_id)
                        user_input['customer_id'] = actual_id
                except Exception as e:
                    print(f"[CUSTOMER DB] ⚠ Could not update customer ID: {e}")
            
            # ═══════════════════════════════════════════════════════
            # ADD CONTRACT LINES
            # ═══════════════════════════════════════════════════════
            
            separation = user_input['separation']
            hindi_punjabi = user_input['hindi_punjabi']
            bonus_overrides = user_input.get('bonus_overrides', {})
            daypart_corrections = user_input.get('daypart_corrections', {})
            
            line_num = 0
            for line_idx, line in enumerate(order.lines):
                line_num += 1
                language = normalize_language(line.language)
                
                print(f"\n[LINE {line_num}] {'BNS' if line.is_bonus else 'PAID'} {language}")
                
                # Determine days and time
                if line.is_bonus:
                    # Check if user overrode this bonus line
                    override = bonus_overrides.get(line_idx)
                    
                    if override:
                        # User chose to use PDF-specific times
                        days = override['days']
                        time_range = override['time_range']
                        description = override['description']
                        print(f"  [OVERRIDE] Using PDF times: {days} {time_range}")
                        print(f"  [OVERRIDE] Description: {description}")
                    else:
                        # Standard ROS defaults
                        ros_schedule = ROS_SCHEDULES.get(language, {})
                        days = ros_schedule.get('days', 'M-Su')
                        time_range = ros_schedule.get('time', '6a-11:59p')
                        description = f"BNS {language} ROS"
                    
                    spot_code = 10  # BNS / Bonus Spot
                else:
                    # Check if daypart was corrected upfront
                    correction = daypart_corrections.get(line_idx)
                    if correction:
                        days = correction['days']
                        time_range = correction['time_range']
                    else:
                        daypart_clean = ' '.join(line.daypart.split())
                        days = daypart_to_days(daypart_clean)
                        time_range = daypart_to_time_range(daypart_clean)
                    
                    spot_code = 2  # Paid Commercial
                    
                    # Build description using program name from PDF
                    program_name = ' '.join(line.language.split())
                    description = f"{days} {time_range} {program_name}"
                
                # Apply Sunday 6-7a rule
                days, _ = EtereClient.check_sunday_6_7a_rule(days, time_range)
                
                # Parse time range through etere_client (handles semicolons)
                time_from, time_to = EtereClient.parse_time_range(time_range)
                
                # Block prefixes
                block_prefixes = get_language_block_prefixes(
                    language,
                    hindi_punjabi_both=hindi_punjabi if language == "South Asian" else None
                )
                
                # ═══════════════════════════════════════════════════
                # CONSOLIDATED LINE ENTRY
                # ═══════════════════════════════════════════════════
                # Group consecutive weeks with the same spot count
                # into a single Etere line to minimize entries.
                # Example: 9 weeks all with 3 spots → 1 line (not 9)
                
                week_groups = EtereClient.consolidate_weeks(
                    line.weekly_spots, order.week_columns, order.flight_end
                )
                
                # Rate (only for paid lines)
                rate = line.rate if not line.is_bonus else 0.0
                
                for group in week_groups:
                    group_start = group['start_date']
                    group_end = group['end_date']
                    group_spots_per_week = group['spots_per_week']
                    group_total = group['total_spots']
                    group_weeks = group['num_weeks']
                    
                    print(f"  {group_start} - {group_end} ({group_weeks} wk): {group_spots_per_week}/wk, {group_total} total")
                    
                    success = etere.add_contract_line(
                        contract_number=contract_number,
                        market=order.market,
                        start_date=group_start,
                        end_date=group_end,
                        days=days,
                        time_from=time_from,
                        time_to=time_to,
                        description=description,
                        spot_code=spot_code,
                        duration_seconds=order.duration_seconds,
                        total_spots=group_total,
                        spots_per_week=group_spots_per_week,
                        rate=rate,
                        block_prefixes=block_prefixes,
                        separation_intervals=separation,
                    )
                    
                    if not success:
                        print(f"  [LINE {line_num}] ✗ Failed for {group_start} - {group_end}")
                        all_success = False
            
            print(f"\n[COMPLETE] Contract {contract_number} — {line_num} lines processed")
            
        finally:
            # Only close browser if we created it (not shared session)
            if not shared_session:
                try:
                    etere.logout()
                except Exception:
                    pass
                try:
                    driver.quit()
                except Exception:
                    pass
    
    return all_success


# ═══════════════════════════════════════════════════════════════════════════════
# STANDALONE ENTRY POINT
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import sys
    
    if len(sys.argv) < 2:
        print("Usage: python charmaine_automation.py <pdf_path>")
        sys.exit(1)
    
    pdf_path = sys.argv[1]
    success = process_charmaine_order(pdf_path)
    
    if success:
        print("\n✓ Order processing complete!")
    else:
        print("\n✗ Order processing had errors — check output above")
