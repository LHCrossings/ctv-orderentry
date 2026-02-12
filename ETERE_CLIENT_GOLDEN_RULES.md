"""
═══════════════════════════════════════════════════════════════════════════════
ETERE_CLIENT.PY - GOLDEN RULES
═══════════════════════════════════════════════════════════════════════════════

This is the SINGLE SOURCE OF TRUTH for ALL Etere browser interactions.

█████████████████████████████████████████████████████████████████████████████
███ RULE #1: ALL ETERE INTERACTIONS GO IN ETERE_CLIENT.PY                 ███
█████████████████████████████████████████████████████████████████████████████

If you need to interact with Etere's web interface, the code goes in 
etere_client.py, NOT in an agency file.

EXAMPLES:

❌ WRONG - Adding Etere code to agency file:
    # worldlink_functions.py
    def _extend_contract_dates(driver, contract_num, new_end_date):
        driver.get(f"{ETERE_URL}/sales/contract/{contract_num}")
        end_field = driver.find_element(By.ID, "expirydate")
        end_field.clear()
        end_field.send_keys(new_end_date)
        # ... more Etere interaction code

✅ RIGHT - Add to etere_client.py, then call it:
    # etere_client.py (add this method to EtereClient class)
    def extend_contract_end_date(self, contract_number: str, new_end_date: str) -> bool:
        """Extend contract end date."""
        try:
            self.driver.get(f"{self.BASE_URL}/sales/contract/{contract_number}")
            end_field = self.driver.find_element(By.ID, "expirydate")
            end_field.clear()
            end_field.send_keys(new_end_date)
            # ... save, etc.
            return True
        except Exception as e:
            print(f"[CONTRACT] ✗ Failed to extend date: {e}")
            return False
    
    # worldlink_functions.py (just call it!)
    etere.extend_contract_end_date(contract_num, new_end_date)


█████████████████████████████████████████████████████████████████████████████
███ RULE #2: FIX BUGS IN ONE PLACE ONLY                                   ███
█████████████████████████████████████████████████████████████████████████████

When you find a bug in Etere interaction code:
1. Fix it in etere_client.py
2. ALL agencies immediately benefit

EXAMPLE:

You discover that the "expirydate" field ID changed to "contractEndDate":

❌ WRONG - Fix in each agency file:
    # Fix in daviselen_functions.py
    # Fix in worldlink_functions.py  
    # Fix in tcaa_functions.py
    # Fix in impact_functions.py
    # ... fix in 8 more files

✅ RIGHT - Fix once in etere_client.py:
    # etere_client.py - line 450
    # OLD:
    end_field = self.driver.find_element(By.ID, "expirydate")
    
    # NEW:
    end_field = self.driver.find_element(By.ID, "contractEndDate")
    
    # DONE! All agencies now use the correct field ID.


█████████████████████████████████████████████████████████████████████████████
███ RULE #3: NEW ETERE FEATURES GO IN ETERE_CLIENT.PY FIRST               ███
█████████████████████████████████████████████████████████████████████████████

When an agency needs a new Etere operation that doesn't exist yet:
1. Add it to etere_client.py as a method
2. Agency calls that method

EXAMPLE:

WorldLink needs block refresh functionality:

❌ WRONG - Build it directly in worldlink_functions.py:
    # worldlink_functions.py
    def _refresh_blocks_for_contract(driver, contract_num, market):
        # 200 lines of Etere browser automation
        driver.get(...)
        # ... complex block refresh logic

✅ RIGHT - Add to etere_client.py, then call it:
    # etere_client.py (add this method to EtereClient class)
    def refresh_contract_blocks(
        self,
        contract_number: str,
        market: str,
        line_numbers: Optional[List[int]] = None
    ) -> bool:
        """
        Refresh programming blocks for contract lines.
        
        Used by WorldLink multi-market orders where Etere duplicates lines
        with wrong market blocks.
        
        Args:
            contract_number: Contract number
            market: Market code (NYC, LAX, etc.)
            line_numbers: Specific lines to refresh, or None for all
            
        Returns:
            True if successful
        """
        try:
            # Navigate to contract
            self.driver.get(f"{self.BASE_URL}/sales/contract/{contract_number}")
            
            # ... block refresh logic ...
            
            return True
        except Exception as e:
            print(f"[BLOCKS] ✗ Refresh failed: {e}")
            return False
    
    # worldlink_functions.py (just call it!)
    etere.refresh_contract_blocks(contract_num, market="NYC")


█████████████████████████████████████████████████████████████████████████████
███ RULE #4: AGENCY FILES ONLY CONTAIN BUSINESS LOGIC                     ███
█████████████████████████████████████████████████████████████████████████████

Agency files should ONLY contain:
✅ PDF parsing
✅ Data transformation
✅ Business rules (what data to send)
✅ Calls to etere_client methods

Agency files should NEVER contain:
❌ driver.get() calls to Etere URLs
❌ driver.find_element() for Etere fields
❌ Field IDs like "contractLineGeneralFromDate"
❌ Button clicks on Etere interface
❌ Tab switching in Etere


█████████████████████████████████████████████████████████████████████████████
███ RULE #5: WHEN IN DOUBT, IT GOES IN ETERE_CLIENT.PY                    ███
█████████████████████████████████████████████████████████████████████████████

If you're not sure whether something belongs in etere_client.py or an agency
file, ask yourself:

"Does this code interact with Etere's web interface?"
    YES → Goes in etere_client.py
    NO → Can go in agency file

"Would other agencies benefit from this function?"
    YES → Goes in etere_client.py
    NO → Might still go in etere_client.py if it touches Etere


═══════════════════════════════════════════════════════════════════════════════
WORKFLOW: ADDING NEW ETERE FUNCTIONALITY
═══════════════════════════════════════════════════════════════════════════════

SCENARIO: You need to add revision handling for WorldLink.

STEP 1: Identify what Etere operations are needed
    - Find existing contract by number
    - Extend contract end date if needed
    - Add new lines to existing contract
    - Update existing lines (CHANGE operation)

STEP 2: Check if etere_client.py already has these
    - create_contract_header? ✓ Already exists
    - add_contract_line? ✓ Already exists  
    - extend_contract_end_date? ✗ Doesn't exist
    - update_existing_line? ✗ Doesn't exist

STEP 3: Add missing methods to etere_client.py
    class EtereClient:
        # ... existing methods ...
        
        def extend_contract_end_date(self, contract_number: str, new_end_date: str) -> bool:
            """Extend contract end date (for revisions)."""
            # Implementation here
            
        def update_contract_line(
            self,
            contract_number: str,
            line_id: str,
            new_end_date: Optional[str] = None,
            new_spots: Optional[int] = None,
            new_rate: Optional[float] = None
        ) -> bool:
            """Update existing contract line (for CHANGE revisions)."""
            # Implementation here

STEP 4: Use in agency file
    # worldlink_functions.py
    def process_worldlink_revision(driver, pdf_path):
        etere = EtereClient(driver)
        
        # Parse revision data
        revision = parse_worldlink_revision_pdf(pdf_path)
        
        # Extend contract if needed
        if revision.new_end_date > current_end_date:
            etere.extend_contract_end_date(contract_num, revision.new_end_date)
        
        # Add new lines
        if revision.operation == "ADD":
            for line in revision.new_lines:
                etere.add_contract_line(...)
        
        # Update existing lines
        elif revision.operation == "CHANGE":
            for line in revision.changes:
                etere.update_contract_line(contract_num, line.id, ...)


═══════════════════════════════════════════════════════════════════════════════
QUICK REFERENCE: CURRENT ETERE_CLIENT.PY METHODS
═══════════════════════════════════════════════════════════════════════════════

SESSION MANAGEMENT:
    etere.login()
    etere.logout()

MARKET MANAGEMENT:
    etere.set_master_market(market="NYC")

CUSTOMER MANAGEMENT:
    etere.search_customer(client_name="Toyota")

CONTRACT MANAGEMENT:
    etere.create_contract_header(
        customer_id=75,
        code="TCAA 9708",
        description="...",
        market="SEA",
        contract_start="01/01/2026",
        contract_end="03/31/2026",
        customer_order_ref="...",
        notes="...",
        charge_to="Customer share indicating agency %",
        invoice_header="Agency"
    )

LINE MANAGEMENT:
    etere.add_contract_line(
        contract_number="123456",
        market="SEA",
        start_date="01/01/2026",
        end_date="01/07/2026",
        days="M-F",
        time_from="08:00",
        time_to="09:00",
        description="...",
        spot_code=2,
        duration_seconds=30,
        spots_per_week=10,
        max_daily_run=2,
        rate=100.00,
        block_prefixes=["J"],
        separation_intervals=(0, 0, 0),
        is_bookend=False
    )

UTILITY FUNCTIONS:
    EtereClient.parse_time_range("6:00a-7:00a") → ("06:00", "07:00")
    EtereClient.check_sunday_6_7a_rule("M-Su", "6:00a-7:00a") → ("M-Sa", 6)


═══════════════════════════════════════════════════════════════════════════════
METHODS TO ADD IN FUTURE (When Agencies Need Them):
═══════════════════════════════════════════════════════════════════════════════

When WorldLink needs block refresh:
    etere.refresh_contract_blocks(contract_number, market, line_numbers=None)

When WorldLink needs revision handling:
    etere.extend_contract_end_date(contract_number, new_end_date)
    etere.update_contract_line(contract_number, line_id, new_end_date=..., new_spots=..., new_rate=...)
    etere.find_contract_by_code(contract_code) → contract_number

When any agency needs contract deletion:
    etere.delete_contract(contract_number)

When any agency needs line deletion:
    etere.delete_contract_line(contract_number, line_id)

When TCAA needs bonus line filtering:
    etere.get_contract_lines(contract_number, filter_by_language=None)

═══════════════════════════════════════════════════════════════════════════════


████████████████████████████████████████████████████████████████████████████
███                                                                        ███
███  REMEMBER: IF IT TOUCHES ETERE, IT LIVES IN ETERE_CLIENT.PY          ███
███                                                                        ███
███  FIX IT ONCE → EVERYONE BENEFITS                                     ███
███                                                                        ███
████████████████████████████████████████████████████████████████████████████

"""
