"""
Example: How Agency Files Will Look Now

This shows how simple agency automation becomes when using etere_client.py.
All the Etere interaction code is in ONE place now.

═══════════════════════════════════════════════════════════════════════════════
BEFORE (Duplicated Code in Every Agency File):
═══════════════════════════════════════════════════════════════════════════════

# daviselen_functions.py - 1,002 lines
# worldlink_functions.py - 1,500 lines  
# impact_functions.py - 915 lines
# tcaa_functions.py - 850 lines
# ... and so on

Each file had its own:
- _create_contract_header() function
- _add_contract_line() function
- _select_days() function
- _filter_blocks_by_prefix() function
- _format_duration() function
- ... dozens of duplicated helpers

TOTAL: 7,856 lines of mostly duplicated code!

═══════════════════════════════════════════════════════════════════════════════
AFTER (Using etere_client.py):
═══════════════════════════════════════════════════════════════════════════════
"""

from selenium import webdriver
from etere_client import EtereClient
from parsers.tcaa_parser import parse_tcaa_pdf, TCAAEstimate


def process_tcaa_order(driver: webdriver.Chrome, pdf_path: str) -> str:
    """
    Process TCAA order - NOW JUST 30 LINES INSTEAD OF 600+!
    
    All Etere interactions are handled by EtereClient.
    This function just:
    1. Parses the PDF
    2. Formats the data
    3. Calls etere_client methods
    """
    
    # Create Etere client
    etere = EtereClient(driver)
    
    # Parse PDF to get order data
    order = parse_tcaa_pdf(pdf_path)
    
    # Create contract header
    contract_num = etere.create_contract_header(
        customer_id=75,  # TCAA Toyota
        code=f"TCAA Toyota {order.estimate_number}",
        description=f"Toyota Asian Channel {order.estimate_number}",
        market="SEA",
        contract_start=order.flight_start,
        contract_end=order.flight_end,
        customer_order_ref=f"Order {order.estimate_number}",
        notes=f"CLIENT: Toyota\\nPRODUCT: Asian Channel\\nESTIMATE: {order.estimate_number}",
        charge_to="Customer share indicating agency %",
        invoice_header="Agency"
    )
    
    if not contract_num:
        print("Failed to create contract")
        return None
    
    # Add each line
    for line in order.lines:
        # Parse time range
        time_from, time_to = EtereClient.parse_time_range(line.time)
        
        # Determine spot code (paid or bonus)
        spot_code = 10 if line.rate == 0 else 2
        
        # Get block prefixes for language
        block_prefixes = _get_language_blocks(line.language)
        
        # Add the line (ONE function call!)
        etere.add_contract_line(
            contract_number=contract_num,
            market="SEA",
            start_date=line.start_date,
            end_date=line.end_date,
            days=line.days,
            time_from=time_from,
            time_to=time_to,
            description=f"(Line {line.line_number}) {line.days} {line.time} {line.language}",
            spot_code=spot_code,
            duration_seconds=30,
            spots_per_week=line.spots,
            max_daily_run=line.spots // 7,
            rate=line.rate,
            block_prefixes=block_prefixes,
            separation_intervals=(0, 0, 0),
            is_bookend=False
        )
    
    print(f"✓ Order processed successfully: {contract_num}")
    return contract_num


def _get_language_blocks(language: str) -> list:
    """Map language to block prefixes."""
    mapping = {
        "Chinese": ["C", "M"],
        "Filipino": ["T"],
        "Korean": ["K"],
        "Vietnamese": ["V"],
        "Japanese": ["J"]
    }
    return mapping.get(language, [])


# ═══════════════════════════════════════════════════════════════════════════
# THAT'S IT! 30 LINES TOTAL!
# ═══════════════════════════════════════════════════════════════════════════

"""
═══════════════════════════════════════════════════════════════════════════════
KEY BENEFITS:
═══════════════════════════════════════════════════════════════════════════════

1. ✅ ONE PLACE to fix Etere bugs
   - Field ID changes? Fix once in etere_client.py
   - All agencies benefit immediately

2. ✅ MUCH SHORTER agency files
   - 600+ lines → 30 lines
   - Focus on business logic, not browser automation

3. ✅ CONSISTENT behavior
   - Every agency uses same code for same operations
   - No more "it works for Daviselen but not TCAA"

4. ✅ EASIER to add new agencies
   - Copy this example
   - Change customer ID, codes, descriptions
   - Done!

5. ✅ EASIER to test
   - Test etere_client.py once
   - Agency files just pass data

═══════════════════════════════════════════════════════════════════════════════
EXAMPLE: Adding Bookend Spots (Impact Marketing)
═══════════════════════════════════════════════════════════════════════════════
"""

def process_impact_order_bookends(driver: webdriver.Chrome, pdf_path: str):
    """Example showing bookend spots - just set is_bookend=True!"""
    
    etere = EtereClient(driver)
    
    # ... create contract header ...
    
    # Add bookend line (15-second spots, Top and Bottom placement)
    etere.add_contract_line(
        contract_number=contract_num,
        market="CVC",
        start_date="01/01/2026",
        end_date="03/31/2026",
        days="M-F",
        time_from="08:00",
        time_to="09:00",
        description="Big Valley Ford :15 Bookends",
        spot_code=2,
        duration_seconds=15,  # 15 seconds
        spots_per_week=10,
        max_daily_run=2,
        rate=50.00,
        block_prefixes=["C", "M"],
        separation_intervals=(0, 0, 0),
        is_bookend=True  # <-- THIS ONE FLAG sets "Top and Bottom" scheduling!
    )


"""
═══════════════════════════════════════════════════════════════════════════════
EXAMPLE: WorldLink Multi-Market (if we add block refresh later)
═══════════════════════════════════════════════════════════════════════════════
"""

def process_worldlink_order(driver: webdriver.Chrome, pdf_path: str):
    """
    Example showing how block refresh would work.
    
    When we add block refresh to etere_client.py, it's just:
        etere.refresh_blocks(contract_num, market="NYC")
    
    That's it. No duplicating the refresh logic in every agency file.
    """
    
    etere = EtereClient(driver)
    
    # ... create contract and add lines ...
    
    # If block refresh is needed:
    # etere.refresh_blocks(contract_num, market="NYC")
    
    pass
