"""
Etere Client - Single Source of Truth for ALL Etere Web Interactions

███████╗████████╗███████╗██████╗ ███████╗     ██████╗██╗     ██╗███████╗███╗   ██╗████████╗
██╔════╝╚══██╔══╝██╔════╝██╔══██╗██╔════╝    ██╔════╝██║     ██║██╔════╝████╗  ██║╚══██╔══╝
█████╗     ██║   █████╗  ██████╔╝█████╗      ██║     ██║     ██║█████╗  ██╔██╗ ██║   ██║   
██╔══╝     ██║   ██╔══╝  ██╔══██╗██╔══╝      ██║     ██║     ██║██╔══╝  ██║╚██╗██║   ██║   
███████╗   ██║   ███████╗██║  ██║███████╗    ╚██████╗███████╗██║███████╗██║ ╚████║   ██║   
╚══════╝   ╚═╝   ╚══════╝╚═╝  ╚═╝╚══════╝     ╚═════╝╚══════╝╚═╝╚══════╝╚═╝  ╚═══╝   ╚═╝   

═══════════════════════════════════════════════════════════════════════════════
CRITICAL RULES
═══════════════════════════════════════════════════════════════════════════════

1. ✅ Field IDs, button IDs, URLs live HERE and ONLY here
2. ✅ If an Etere field changes, fix it HERE once - all agencies benefit  
3. ✅ Agency files pass DATA to these functions - they don't touch Etere directly
4. ✅ Never duplicate Etere interaction code in agency files

This file extracts ALL Etere operations from ALL agencies:
- Daviselen: Contract creation, line addition, billing fields
- WorldLink: Block refresh, contract extensions, revision handling
- Impact: Bookend scheduling (Top and Bottom placement)
- Misfit: Multi-market handling
- All others: Various field patterns

═══════════════════════════════════════════════════════════════════════════════
"""

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait, Select
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.keys import Keys
from selenium.common.exceptions import TimeoutException, NoSuchElementException
from datetime import date, datetime
from decimal import Decimal
from typing import Optional, List, Tuple
import time


class EtereClient:
    """Single client for ALL Etere web interactions."""
    
    # Etere Configuration
    BASE_URL = "http://100.102.206.113"
    
    MARKET_CODES = {
        "NYC": 1, "CMP": 2, "HOU": 3, "SFO": 4, "SEA": 5,
        "LAX": 6, "CVC": 7, "WDC": 8, "MMT": 9, "DAL": 10
    }
    
    SPOT_CODES = {
        "Paid Commercial": 2,
        "BNS": 10,
        "Bonus Spot": 10
    }
    
    def __init__(self, driver: webdriver.Chrome):
        """Initialize with existing Selenium WebDriver."""
        self.driver = driver
        self.wait = WebDriverWait(driver, 15)
    
    # ═══════════════════════════════════════════════════════════════════════
    # SESSION MANAGEMENT
    # ═══════════════════════════════════════════════════════════════════════
    
    def login(self) -> None:
        """Navigate to login and wait for user to log in."""
        print("[LOGIN] Navigating to Etere login page...")
        self.driver.get(f"{self.BASE_URL}/etere/etere.html")
        print("[LOGIN] Please log in to Etere in the browser window...")
        self.wait.until(EC.presence_of_element_located((By.ID, "menu")))
        print("[LOGIN] ✓ Login successful!")
        time.sleep(2)
    
    # ═══════════════════════════════════════════════════════════════════════
    # MASTER MARKET SELECTION
    # ═══════════════════════════════════════════════════════════════════════
    
    def set_master_market(self, market: str = "NYC") -> bool:
        """
        Set master market (ALWAYS NYC except Dallas WorldLink).
        
        Args:
            market: Market code. Defaults to NYC.
            
        Returns:
            True if successful
        """
        try:
            self.driver.get(f"{self.BASE_URL}/sales")
            time.sleep(2)
            
            # Click user menu
            user_menu = self.wait.until(EC.element_to_be_clickable(
                (By.CSS_SELECTOR, "a.user-profile.dropdown-toggle")
            ))
            user_menu.click()
            time.sleep(1)
            
            # Click "Stations"
            stations_link = self.wait.until(EC.element_to_be_clickable(
                (By.XPATH, "//a[@onclick='OpenSelectStation();']")
            ))
            stations_link.click()
            time.sleep(2)
            
            # Wait for modal
            self.wait.until(EC.presence_of_element_located((By.ID, "GalleryStations")))
            
            # Get market ID
            market_id = self.MARKET_CODES.get(market.upper())
            if not market_id:
                print(f"[MARKET] ✗ Unknown market: {market}")
                return False
            
            # Click market icon
            station = self.wait.until(EC.element_to_be_clickable(
                (By.CSS_SELECTOR, f"img[data-coduser='{market_id}'][onclick*='SelectThisUser']")
            ))
            station.click()
            time.sleep(2)
            
            print(f"[MARKET] ✓ Set to {market.upper()}")
            return True
            
        except Exception as e:
            print(f"[MARKET] ✗ Failed: {e}")
            return False
    
    # ═══════════════════════════════════════════════════════════════════════
    # SESSION MANAGEMENT
    # ═══════════════════════════════════════════════════════════════════════
    
    def logout(self) -> bool:
        """
        Logout from Etere system.
        
        Important: Always logout before closing browser to prevent
        multiple login sessions from locking out the account.
        
        Returns:
            True if logout successful
        """
        try:
            print("[LOGOUT] Logging out of Etere...")
            
            # Click user menu dropdown
            user_menu = self.wait.until(
                EC.element_to_be_clickable(
                    (By.CSS_SELECTOR, "a.user-profile.dropdown-toggle")
                )
            )
            user_menu.click()
            time.sleep(1)
            
            # Click logout option (using working XPath from old code)
            logout_link = self.wait.until(
                EC.element_to_be_clickable(
                    (By.XPATH, "//a[contains(text(), 'Logout') or contains(text(), 'Log out')]")
                )
            )
            logout_link.click()
            time.sleep(2)
            
            print("[LOGOUT] ✓ Successfully logged out")
            return True
            
        except Exception as e:
            print(f"[LOGOUT] ⚠ Could not logout automatically: {e}")
            print("[LOGOUT] Please logout manually before closing browser")
            return False
    
    # ═══════════════════════════════════════════════════════════════════════
    # CUSTOMER SEARCH
    # ═══════════════════════════════════════════════════════════════════════
    
    def search_customer(self, client_name: str) -> None:
        """Open customer search modal. User must click Insert."""
        try:
            search_icon = self.driver.find_element(
                By.CSS_SELECTOR, "a[onclick*='openCustomerSearchModal']"
            )
            search_icon.click()
            time.sleep(1)
            
            self.wait.until(EC.presence_of_element_located((By.ID, "customerSearchModal")))
            
            search_field = self.driver.find_element(By.ID, "customerSearchInput")
            search_field.clear()
            search_field.send_keys(client_name)
            
            search_button = self.driver.find_element(By.ID, "customerSearchButton")
            search_button.click()
            time.sleep(2)
            
            print(f"[CUSTOMER] Search results for: {client_name}")
            print(f"[CUSTOMER] Please click 'Insert' to select customer")
            
        except Exception as e:
            print(f"[CUSTOMER] ✗ Search failed: {e}")
    
    # ═══════════════════════════════════════════════════════════════════════
    # CONTRACT HEADER CREATION
    # ═══════════════════════════════════════════════════════════════════════
    
    def create_contract_header(
        self,
        customer_id: Optional[int] = None,
        code: str = "",
        description: str = "",
        contract_start: Optional[str] = None,
        contract_end: Optional[str] = None,
        customer_order_ref: Optional[str] = None,
        notes: Optional[str] = None,
        charge_to: Optional[str] = None,
        invoice_header: Optional[str] = None,
        search_required: bool = False,
        client_name: Optional[str] = None
    ) -> Optional[str]:
        """
        Create contract header in Etere.
        
        NOTE: Master market must be set BEFORE calling this (via session.set_market()).
        This function does NOT set master market - that's the session's job.
        
        Complete workflow:
        1. Navigate to new contract page
        2. Fill contract code
        3. Handle customer (search or direct ID)
        4. Save contract
        5. Fill additional fields (dates, billing, notes)
        
        Returns:
            Contract number if successful, None otherwise
        """
        try:
            # Navigate to new contract
            print("[CONTRACT] Creating new contract...")
            self.driver.get(f"{self.BASE_URL}/sales/new")
            self.wait.until(EC.presence_of_element_located((By.ID, "code")))
            time.sleep(2)
            
            # Fill contract code
            code_field = self.driver.find_element(By.ID, "code")
            code_field.clear()
            code_field.send_keys(code)
            print(f"[CONTRACT] Code: {code}")
            
            # Try to fill description if field exists
            try:
                desc_field = self.driver.find_element(By.ID, "description")
                desc_field.clear()
                desc_field.send_keys(description)
            except:
                pass
            
            # Handle customer
            if customer_id is None:
                # No customer ID provided - user will select manually in browser
                print("\n" + "="*70)
                print("CUSTOMER SELECTION REQUIRED")
                print("="*70)
                print("Please select a customer in the browser:")
                print("  1. Click the search icon next to the Customer field")
                print("  2. Search for your customer")
                print("  3. Click on the customer to select")
                print("  4. Click 'Insert' button")
                print("  5. Return here and press Enter to continue")
                print("="*70)
                
                input("\nPress Enter after you've selected the customer...")
                
                # Verify customer was selected
                customer_id_field = self.driver.find_element(By.ID, "customerId")
                populated_id = customer_id_field.get_attribute("value")
                
                if not populated_id or populated_id.strip() == "":
                    print("[CONTRACT] ✗ No customer selected")
                    print("[CONTRACT] ✗ Please use the search icon and click 'Insert'")
                    return None
                
                print(f"[CONTRACT] ✓ Customer ID: {populated_id}")
                
            elif search_required and client_name:
                # Legacy: Auto-trigger search with client name (kept for backward compatibility)
                self.search_customer(client_name)
                
                # Verify customer was selected
                customer_id_field = self.driver.find_element(By.ID, "customerId")
                populated_id = customer_id_field.get_attribute("value")
                
                if not populated_id or populated_id.strip() == "":
                    print("[CONTRACT] ✗ Customer ID empty after search")
                    print("[CONTRACT] ✗ Please click 'Insert' in modal")
                    return None
                
                print(f"[CONTRACT] ✓ Customer ID: {populated_id}")
            else:
                # Direct customer ID entry
                customer_id_field = self.driver.find_element(By.ID, "customerId")
                customer_id_field.clear()
                customer_id_field.send_keys(str(customer_id))
                customer_id_field.send_keys(Keys.TAB)
                time.sleep(2)
                print(f"[CONTRACT] ✓ Customer ID: {customer_id}")
            
            # Save contract
            save_button = self.driver.find_element(By.ID, "formNewContractSubmit")
            save_button.click()
            
            # Wait for redirect
            print("[CONTRACT] Waiting for redirect...")
            for attempt in range(10):
                time.sleep(1)
                current_url = self.driver.current_url
                if "/sales/contract/" in current_url:
                    break
            else:
                print(f"[CONTRACT] ✗ No redirect to contract page")
                return None
            
            # Extract contract number
            contract_number = current_url.split("/sales/contract/")[1].split("/")[0]
            print(f"[CONTRACT] ✓ Created: {contract_number}")
            
            # Fill additional fields
            self._fill_contract_details(
                contract_start=contract_start,
                contract_end=contract_end,
                customer_order_ref=customer_order_ref,
                notes=notes,
                charge_to=charge_to,
                invoice_header=invoice_header
            )
            
            return contract_number
            
        except Exception as e:
            print(f"[CONTRACT] ✗ Failed: {e}")
            import traceback
            traceback.print_exc()
            return None
    
    def _fill_contract_details(
        self,
        contract_start: Optional[str],
        contract_end: Optional[str],
        customer_order_ref: Optional[str],
        notes: Optional[str],
        charge_to: Optional[str],
        invoice_header: Optional[str]
    ) -> None:
        """Fill additional contract details on General tab."""
        try:
            print("[HEADER] Filling contract details...")
            self.wait.until(EC.presence_of_element_located((By.ID, "date")))
            time.sleep(2)
            
            # Start Date
            if contract_start:
                try:
                    field = self.driver.find_element(By.ID, "date")
                    field.clear()
                    field.send_keys(contract_start)
                    print(f"[HEADER] ✓ Start: {contract_start}")
                except Exception as e:
                    print(f"[HEADER] ⚠ Start date: {e}")
            
            # End Date
            if contract_end:
                try:
                    field = self.driver.find_element(By.ID, "expirydate")
                    field.clear()
                    field.send_keys(contract_end)
                    print(f"[HEADER] ✓ End: {contract_end}")
                except Exception as e:
                    print(f"[HEADER] ⚠ End date: {e}")
            
            # Customer Order Ref
            if customer_order_ref:
                try:
                    field = self.driver.find_element(By.ID, "customerOrderRef")
                    field.clear()
                    field.send_keys(customer_order_ref)
                    print(f"[HEADER] ✓ Ref: {customer_order_ref}")
                except Exception as e:
                    print(f"[HEADER] ⚠ Ref: {e}")
            
            # Notes
            if notes:
                try:
                    field = self.driver.find_element(By.ID, "notes")
                    field.clear()
                    field.send_keys(notes)
                    print(f"[HEADER] ✓ Notes populated")
                except Exception as e:
                    print(f"[HEADER] ⚠ Notes: {e}")
            
            # Charge To (Select2)
            if charge_to:
                try:
                    print(f"[HEADER] Setting Charge To: {charge_to}")
                    container = self.driver.find_element(
                        By.CSS_SELECTOR,
                        "span[aria-labelledby='select2-selectedChargeTo-container']"
                    )
                    container.click()
                    time.sleep(1)
                    
                    option = None
                    patterns = [
                        f"//li[contains(@class, 'select2-results__option') and text()='{charge_to}']",
                        f"//li[contains(@class, 'select2-results__option') and contains(text(), '{charge_to}')]",
                        f"//li[contains(@class, 'select2-results__option') and normalize-space(text())='{charge_to}']"
                    ]
                    
                    for pattern in patterns:
                        try:
                            option = self.wait.until(EC.element_to_be_clickable((By.XPATH, pattern)))
                            break
                        except:
                            continue
                    
                    if option:
                        option.click()
                        time.sleep(0.5)
                        print(f"[HEADER] ✓ Charge To set")
                    else:
                        print(f"[HEADER] ⚠ Option not found")
                        
                except Exception as e:
                    print(f"[HEADER] ⚠ Charge To: {e}")
                    try:
                        self.driver.find_element(By.TAG_NAME, "body").click()
                    except:
                        pass
            
            # Invoice Header (Select2)
            if invoice_header:
                try:
                    print(f"[HEADER] Setting Invoice Header: {invoice_header}")
                    container = self.driver.find_element(
                        By.CSS_SELECTOR,
                        "span[aria-labelledby='select2-selectedInvoiceHeader-container']"
                    )
                    container.click()
                    time.sleep(1)
                    
                    option = None
                    patterns = [
                        f"//li[contains(@class, 'select2-results__option') and text()='{invoice_header}']",
                        f"//li[contains(@class, 'select2-results__option') and normalize-space(text())='{invoice_header}']",
                        f"//li[contains(@class, 'select2-results__option') and contains(text(), '{invoice_header}')]"
                    ]
                    
                    for pattern in patterns:
                        try:
                            option = self.wait.until(EC.element_to_be_clickable((By.XPATH, pattern)))
                            break
                        except:
                            continue
                    
                    if option:
                        option.click()
                        time.sleep(0.5)
                        print(f"[HEADER] ✓ Invoice Header set")
                    else:
                        print(f"[HEADER] ⚠ Option not found")
                        
                except Exception as e:
                    print(f"[HEADER] ⚠ Invoice Header: {e}")
                    try:
                        self.driver.find_element(By.TAG_NAME, "body").click()
                    except:
                        pass
            
            # Save details
            print("[HEADER] Saving...")
            try:
                save_button = self.driver.find_element(By.ID, "formContractGeneralSubmit")
                save_button.click()
                time.sleep(2)
                print("[HEADER] ✓ Saved")
            except Exception as e:
                print(f"[HEADER] ⚠ Save: {e}")
                
        except Exception as e:
            print(f"[HEADER] ⚠ Error: {e}")
    
    # ═══════════════════════════════════════════════════════════════════════
    # CONTRACT LINE CREATION
    # ═══════════════════════════════════════════════════════════════════════
    
    def add_contract_line(
        self,
        contract_number: str,
        market: str,
        start_date: str,
        end_date: str,
        days: str,
        time_from: str,
        time_to: str,
        description: str,
        spot_code: int,
        duration_seconds: int,
        total_spots: Optional[int] = None,  # Total spots for entire date range
        spots_per_week: int = 0,
        max_daily_run: Optional[int] = None,  # Auto-calculated if None
        rate: float = 0.0,
        block_prefixes: Optional[List[str]] = None,
        separation_intervals: Tuple[int, int, int] = (15, 0, 0),  # DEFAULT: Customer=15, Event=0, Order=0
        is_bookend: bool = False
    ) -> bool:
        """
        Add contract line to existing contract.
        
        Complete workflow:
        1. Navigate to Add Line page
        2. Fill all GENERAL tab fields
        3. Select days of week
        4. Select programming blocks
        5. Set separation intervals in OPTIONS tab
        6. Set bookend scheduling type if requested
        7. Save line
        
        Args:
            max_daily_run: Maximum spots per day. If None, calculated automatically
                from spots_per_week and day count using ceiling division.
            separation_intervals: (Customer, Event, Order) intervals in minutes.
                DEFAULT: (15, 0, 0) - Industry standard is 15 minutes customer separation.
                Override when PDF specifies different separation (e.g., TCAA uses 10,0,0).
                Format: (customer_minutes, event_minutes, order_minutes)
            total_spots: Total spots for entire date range (if None, calculated from spots_per_week)
        
        Returns:
            True if successful
        """
        try:
            # Universal calculation: If max_daily_run not provided, calculate it
            if max_daily_run is None:
                # Calculate actual days in date range that match the day pattern
                from datetime import datetime, timedelta
                
                try:
                    start = datetime.strptime(start_date, '%m/%d/%Y')
                    end = datetime.strptime(end_date, '%m/%d/%Y')
                    
                    # Map day pattern to actual days
                    day_pattern_map = {
                        "M-Su": [0, 1, 2, 3, 4, 5, 6],  # All days
                        "M-F": [0, 1, 2, 3, 4],          # Weekdays
                        "M-Sa": [0, 1, 2, 3, 4, 5],      # Mon-Sat
                        "Sa-Su": [5, 6],                  # Weekend
                        "SAT": [5], "Sa": [5],           # Saturday only
                        "SU": [6], "Su": [6], "Sun": [6], "SUN": [6]  # Sunday only
                    }
                    
                    # Get active day indices (0=Monday, 6=Sunday)
                    active_days = day_pattern_map.get(days, [0, 1, 2, 3, 4, 5, 6])
                    
                    # Calculate max_daily_run based on days PER WEEK, not total days
                    # Example: 2 spots/week on M-F = 2 spots ÷ 5 days = 0.4 → 1/day
                    # NOT: 2 spots/week ÷ 20 total M-F days across 4 weeks!
                    days_per_week = len(active_days)
                    
                    # Calculate using ceiling division to ensure all spots can fit
                    # Example: 10 spots/week on M-F = 10 ÷ 5 = 2/day
                    if days_per_week > 0 and spots_per_week > 0:
                        import math
                        max_daily_run = math.ceil(spots_per_week / days_per_week)
                    else:
                        max_daily_run = spots_per_week  # Fallback
                    
                    print(f"[LINE] ℹ Auto-calculated max_daily_run: {spots_per_week} spots/week ÷ {days_per_week} days/week ({days}) = {max_daily_run} spots/day")
                    
                except Exception as e:
                    # Fallback to old method if date parsing fails
                    print(f"[LINE] ⚠ Date parsing failed, using day pattern: {e}")
                    day_count = self._count_active_days(days)
                    if day_count > 0 and spots_per_week > 0:
                        import math
                        max_daily_run = math.ceil(spots_per_week / day_count)
                    else:
                        max_daily_run = spots_per_week
                    print(f"[LINE] ℹ Fallback max_daily_run: {spots_per_week} spots ÷ {day_count} days = {max_daily_run} spots/day")
            
            print(f"[LINE] Adding line to contract {contract_number}...")
            
            # Navigate to line creation modal (correct Etere URL!)
            add_line_url = f"{self.BASE_URL}/sales/modalcreatecontractline?idContract={contract_number}&selectedPriceColor=16711680"
            self.driver.get(add_line_url)
            self.wait.until(EC.presence_of_element_located(
                (By.ID, "contractLineGeneralFromDate")
            ))
            time.sleep(2)
            
            # ═══════════════════════════════════════════════════════════════
            # GENERAL TAB
            # ═══════════════════════════════════════════════════════════════
            
            # Scheduled Station (Market) - CRITICAL for setting line market
            market_id = self.MARKET_CODES.get(market.upper())
            if market_id:
                market_select = Select(self.driver.find_element(
                    By.ID, "selectedschedStation"
                ))
                market_select.select_by_value(str(market_id))
                print(f"[LINE] ✓ Market: {market}")
            
            # Start Date
            start_field = self.driver.find_element(By.ID, "contractLineGeneralFromDate")
            start_field.clear()
            start_field.send_keys(start_date)
            
            # End Date
            end_field = self.driver.find_element(By.ID, "contractLineGeneralToDate")
            end_field.clear()
            end_field.send_keys(end_date)
            
            print(f"[LINE] ✓ Dates: {start_date} - {end_date}")
            
            # Time From
            time_from_field = self.driver.find_element(By.ID, "contractLineGeneralStartTime")
            time_from_field.clear()
            time_from_field.send_keys(time_from)
            
            # Time To
            time_to_field = self.driver.find_element(By.ID, "contractLineGeneralEndTime")
            time_to_field.clear()
            time_to_field.send_keys(time_to)
            
            print(f"[LINE] ✓ Time: {time_from} - {time_to}")
            
            # Description
            desc_field = self.driver.find_element(By.ID, "contractLineGeneralDescription")
            desc_field.clear()
            desc_field.send_keys(description)
            print(f"[LINE] ✓ Description: {description}")
            
            # Spot Code
            spot_code_select = Select(self.driver.find_element(
                By.ID, "contractLineGeneralBookingCode"
            ))
            spot_code_select.select_by_value(str(spot_code))
            
            # Duration
            duration_formatted = self._format_duration(duration_seconds)
            duration_field = self.driver.find_element(By.ID, "contractLineGeneralDuration")
            duration_field.clear()
            duration_field.send_keys(duration_formatted)
            print(f"[LINE] ✓ Duration: {duration_formatted}")
            
            # Total to Schedule (if not provided, use spots_per_week as estimate)
            if total_spots is None:
                total_spots = spots_per_week  # Simple default
            
            total_spots_field = self.driver.find_element(By.ID, "contractLineGeneralTotToSchedule")
            total_spots_field.clear()
            total_spots_field.send_keys(str(total_spots))
            
            # Spots Per Week
            spots_field = self.driver.find_element(By.ID, "contractLineGeneralMaxWeekSchedule")
            spots_field.clear()
            spots_field.send_keys(str(spots_per_week))
            
            # Max Daily Run
            max_daily_field = self.driver.find_element(By.ID, "contractLineGeneralMaxDailyRun")
            max_daily_field.clear()
            max_daily_field.send_keys(str(max_daily_run))
            
            print(f"[LINE] ✓ Spots: {spots_per_week}/week, {max_daily_run}/day max")
            
            # Price Mode: Manual (required before entering rate!)
            self._click_iradio_by_value("selectedPriceMode", "2")
            time.sleep(0.5)
            
            # Rate (Unit Price)
            rate_field = self.wait.until(EC.visibility_of_element_located(
                (By.ID, "contractLineGeneralUnitPrice")
            ))
            rate_field.clear()
            rate_field.send_keys(str(rate))
            print(f"[LINE] ✓ Rate: ${rate}")
            
            # Days Selection
            self._select_days(days)
            print(f"[LINE] ✓ Days: {days}")
            
            # ═══════════════════════════════════════════════════════════════
            # BLOCKS TAB
            # ═══════════════════════════════════════════════════════════════
            
            if block_prefixes:
                blocks_tab = self.driver.find_element(
                    By.CSS_SELECTOR, "a[href='#tabLineBlocks']"
                )
                blocks_tab.click()
                time.sleep(1)
                
                # Click "Add Blocks Automatically" button
                add_blocks_btn = self.wait.until(EC.element_to_be_clickable(
                    (By.ID, "contractLineBlocksAddBlockAutomatically")
                ))
                add_blocks_btn.click()
                time.sleep(5)  # Wait for blocks to populate
                
                # Filter by prefix
                self._filter_blocks_by_prefix(block_prefixes)
                print(f"[LINE] ✓ Blocks: {block_prefixes}")
            
            # ═══════════════════════════════════════════════════════════════
            # OPTIONS TAB
            # ═══════════════════════════════════════════════════════════════
            
            options_tab = self.driver.find_element(
                By.CSS_SELECTOR, "a[href='#tabLineOptions']"
            )
            options_tab.click()
            time.sleep(1)
            
            # Separation Intervals
            customer_int, event_int, order_int = separation_intervals
            
            customer_field = self.driver.find_element(By.ID, "contractLineGeneralicomm")
            customer_field.clear()
            customer_field.send_keys(str(customer_int))
            
            event_field = self.driver.find_element(By.ID, "contractLineGeneralievent")
            event_field.clear()
            event_field.send_keys(str(event_int))
            
            order_field = self.driver.find_element(By.ID, "contractLineGeneralisster")
            order_field.clear()
            order_field.send_keys(str(order_int))
            
            print(f"[LINE] ✓ Intervals: Cust={customer_int}, Event={event_int}, Order={order_int}")
            
            # SCHEDULING TYPE - Bookend (Top and Bottom)
            if is_bookend:
                print(f"[LINE] Setting bookend scheduling...")
                try:
                    self._click_iradio_by_value("selectedSchedulingType", "6")
                    time.sleep(0.5)
                    print(f"[LINE] ✓ Bookend set")
                except Exception as e:
                    print(f"[LINE] ⚠ Bookend: {e}")
            
            # ═══════════════════════════════════════════════════════════════
            # SAVE LINE
            # ═══════════════════════════════════════════════════════════════
            
            save_btn = self.wait.until(EC.element_to_be_clickable(
                (By.ID, "btnsaveexitcl")
            ))
            save_btn.click()
            time.sleep(3)
            
            print(f"[LINE] ✓ Line added successfully")
            return True
            
        except Exception as e:
            print(f"[LINE] ✗ Failed: {e}")
            import traceback
            traceback.print_exc()
            return False
    
    # ═══════════════════════════════════════════════════════════════════════
    # HELPER FUNCTIONS
    # ═══════════════════════════════════════════════════════════════════════
    
    def _format_duration(self, seconds: int) -> str:
        """Format duration as timecode."""
        if seconds == 15:
            return "00:00:15:00"
        elif seconds == 30:
            return "00:00:30:00"
        elif seconds == 60:
            return "00:01:00:00"
        else:
            minutes = seconds // 60
            remaining_seconds = seconds % 60
            return f"00:{minutes:02d}:{remaining_seconds:02d}:00"
    
    def _select_days(self, days: str) -> None:
        """Select days of week based on pattern."""
        day_ids = [
            'contractLineBlocksSunday',
            'contractLineBlocksMonday',
            'contractLineBlocksTuesday',
            'contractLineBlocksWednesday',
            'contractLineBlocksThursday',
            'contractLineBlocksFriday',
            'contractLineBlocksSaturday'
        ]
        
        patterns = {
            "M-Su": [0, 1, 2, 3, 4, 5, 6],
            "M-F": [1, 2, 3, 4, 5],
            "M-Sa": [1, 2, 3, 4, 5, 6],
            "Sa-Su": [6, 0],
            "SAT": [6],
            "Sa": [6],
            "SU": [0],
            "Su": [0],
            "Sun": [0],
            "SUN": [0]
        }
        
        active_days = patterns.get(days, [0, 1, 2, 3, 4, 5, 6])
        
        # Uncheck all first
        for checkbox_id in day_ids:
            checkbox = self.driver.find_element(By.ID, checkbox_id)
            if self._is_icheck_checked(checkbox):
                self._click_icheck(checkbox)
        
        # Check active days
        for day_index in active_days:
            checkbox = self.driver.find_element(By.ID, day_ids[day_index])
            if not self._is_icheck_checked(checkbox):
                self._click_icheck(checkbox)
    
    def _filter_blocks_by_prefix(self, prefixes: List[str]) -> None:
        """Filter programming blocks by language prefixes."""
        try:
            block_checkboxes = self.driver.find_elements(
                By.CLASS_NAME, "block-checkbox"
            )
            
            selected_count = 0
            for checkbox in block_checkboxes:
                block_id = checkbox.get_attribute("id")
                block_label = self.driver.find_element(
                    By.CSS_SELECTOR, f"label[for='{block_id}']"
                )
                block_name = block_label.text.strip()
                
                should_select = False
                for prefix in prefixes:
                    if block_name.startswith(f"{prefix} - "):
                        should_select = True
                        break
                
                if should_select:
                    if not checkbox.is_selected():
                        checkbox.click()
                        selected_count += 1
                else:
                    if checkbox.is_selected():
                        checkbox.click()
            
            print(f"[BLOCKS] Selected {selected_count} blocks")
            
        except Exception as e:
            print(f"[BLOCKS] ⚠ Error: {e}")
    
    def _is_icheck_checked(self, checkbox) -> bool:
        """Check if iCheck checkbox is checked."""
        parent = checkbox.find_element(By.XPATH, "..")
        return 'checked' in parent.get_attribute('class')
    
    def _click_icheck(self, checkbox) -> None:
        """Click iCheck checkbox via parent."""
        parent = checkbox.find_element(By.XPATH, "..")
        parent.click()
    
    def _click_iradio_by_value(self, name: str, value: str) -> None:
        """Click iRadio button by name and value."""
        radio = self.driver.find_element(
            By.CSS_SELECTOR, f'input[name="{name}"][value="{value}"]'
        )
        parent = radio.find_element(By.XPATH, "..")
        parent.click()
    
    # ═══════════════════════════════════════════════════════════════════════
    # TIME PARSING UTILITIES
    # ═══════════════════════════════════════════════════════════════════════
    
    @staticmethod
    def parse_time_range(time_str: str) -> Tuple[str, str]:
        """
        Parse ANY time range format to simple 24-hour HH:MM format for Etere.
        
        Handles ALL these variations:
        - "6:00a-7:00a"    → ("06:00", "07:00")
        - "6a-11:59p"      → ("06:00", "23:59")
        - "7:00-7:30p"     → ("19:00", "19:30")  # PM only on end
        - "730p-800p"      → ("19:30", "20:00")  # No colons
        - "11-130p"        → ("11:00", "13:30")  # Smart AM/PM inference
        - "4p-5p; 6p-7p"   → ("16:00", "19:00")  # Semicolon: earliest to latest
        
        SPECIAL HANDLING:
        - Semicolon-separated ranges (e.g., "4p-5p; 6p-7p"):
          Takes EARLIEST start time and LATEST end time
          This is for cases where users manually remove inapplicable programming
          
        CRITICAL RULES:
        - Start time FLOOR: 06:00 (6am) - nothing earlier allowed
        - End time CEILING: 23:59 (11:59pm) - midnight = end of day
        - 12:00a or 12a ALWAYS becomes 23:59
        
        Etere needs: Simple "HHMM" or "HH:MM" format (both work).
        
        Args:
            time_str: Time range in any common format
            
        Returns:
            Tuple of (from_time, to_time) in "HH:MM" format
        """
        import re
        
        # ═══════════════════════════════════════════════════════════════
        # HANDLE SEMICOLON-SEPARATED TIME RANGES (e.g., "4p-5p; 6p-7p")
        # ═══════════════════════════════════════════════════════════════
        
        if ';' in time_str:
            # Split on semicolon to get multiple ranges
            ranges = [r.strip() for r in time_str.split(';') if r.strip()]
            
            # Parse each range recursively
            parsed_ranges = []
            for range_str in ranges:
                try:
                    start, end = EtereClient.parse_time_range(range_str)
                    parsed_ranges.append((start, end))
                except Exception as e:
                    print(f"[TIME] ⚠ Failed to parse range '{range_str}': {e}")
                    continue
            
            if parsed_ranges:
                # Take EARLIEST start and LATEST end
                earliest_start = min(r[0] for r in parsed_ranges)
                latest_end = max(r[1] for r in parsed_ranges)
                print(f"[TIME] ℹ Semicolon range detected: '{time_str}' → {earliest_start} to {latest_end}")
                return (earliest_start, latest_end)
            else:
                # All ranges failed to parse - fallback
                print(f"[TIME] ⚠ Could not parse any ranges in '{time_str}' - using fallback")
                return ("06:00", "23:59")
        
        # ═══════════════════════════════════════════════════════════════
        # SINGLE TIME RANGE PARSING (original logic)
        # ═══════════════════════════════════════════════════════════════
        
        # Clean up input
        time_str = time_str.replace(' ', '').lower()
        time_str = time_str.replace('am', 'a').replace('pm', 'p')  # Normalize am/pm
        
        # Split on dash to get start and end
        parts = time_str.split('-')
        if len(parts) != 2:
            # Fallback if format is unexpected
            return ("06:00", "23:59")
        
        start_str, end_str = parts
        
        # ═══════════════════════════════════════════════════════════════
        # PARSE START TIME
        # ═══════════════════════════════════════════════════════════════
        
        # Try: "6:00a", "730p", "6a", "600a", etc.
        # Match: optional hours, optional colon, optional minutes, required a/p
        start_match = re.match(r'(\d{1,2}):?(\d{2})?([ap])?', start_str)
        
        if start_match:
            hour = int(start_match.group(1))
            minute = start_match.group(2) if start_match.group(2) else "00"
            period = start_match.group(3) if start_match.group(3) else None
            
            # If no period on start, we need to infer it
            if not period:
                # Check what period the end has
                end_period_match = re.search(r'([ap])$', end_str)
                if end_period_match:
                    end_period = end_period_match.group(1)
                    
                    # Parse end hour to determine if start should be AM or PM
                    end_hour_match = re.match(r'(\d{1,2})', end_str)
                    if end_hour_match:
                        end_hour = int(end_hour_match.group(1))
                        
                        # If end is PM and start hour > end hour (e.g., 11-130p)
                        # Then start is AM (11am-1:30pm)
                        if end_period == 'p' and hour > end_hour and hour != 12:
                            period = 'a'
                        else:
                            period = end_period
            
            # Convert to 24-hour
            if period == 'a':
                if hour == 12:
                    hour = 0  # 12am = 00:00
            elif period == 'p':
                if hour != 12:
                    hour += 12
            
            from_time = f"{hour:02d}:{minute}"
            
            # ENFORCE FLOOR: Nothing before 06:00
            if from_time < "06:00":
                from_time = "06:00"
        else:
            from_time = "06:00"  # Default to floor
        
        # ═══════════════════════════════════════════════════════════════
        # PARSE END TIME
        # ═══════════════════════════════════════════════════════════════
        
        # Try: "7:00a", "800p", "10a", "1159p", etc.
        end_match = re.match(r'(\d{1,2}):?(\d{2})?([ap])?', end_str)
        
        if end_match:
            hour = int(end_match.group(1))
            minute = end_match.group(2) if end_match.group(2) else "00"
            period = end_match.group(3) if end_match.group(3) else None
            
            # CRITICAL: 12:00a or 12a = midnight = 23:59
            if hour == 12 and period == 'a':
                to_time = "23:59"
            # Also catch times like "1a", "2a" (past midnight) → cap to 23:59
            elif period == 'a' and hour < 6:  # 1am-5am = past midnight
                to_time = "23:59"
            else:
                # Convert to 24-hour
                if period == 'a':
                    if hour == 12:
                        hour = 0
                elif period == 'p':
                    if hour != 12:
                        hour += 12
                
                to_time = f"{hour:02d}:{minute}"
                
                # ENFORCE CEILING: Nothing past 23:59
                if to_time > "23:59":
                    to_time = "23:59"
        else:
            to_time = "23:59"
        
        return from_time, to_time
    
    @staticmethod
    def check_sunday_6_7a_rule(days: str, time_str: str) -> Tuple[str, int]:
        """
        Apply Sunday 6-7a paid programming rule.
        
        UNIVERSAL RULE: Sunday 6:00am-7:00am has paid programming.
        If days include Sunday and time is exactly 6:00a-7:00a, remove Sunday.
        """
        time_normalized = time_str.replace(' ', '').lower()
        
        is_6_7a = time_normalized in ["6:00a-7:00a", "6a-7a", "6:00am-7:00am"]
        
        if not is_6_7a:
            return days, EtereClient._count_active_days(days)
        
        has_sunday = "Su" in days or days == "M-Su"
        
        if not has_sunday:
            return days, EtereClient._count_active_days(days)
        
        print(f"[SUNDAY 6-7a RULE] Removing Sunday from '{days}'")
        
        if days == "M-Su":
            return "M-Sa", 6
        elif days == "Sa-Su":
            return "Sa", 1
        else:
            return days, EtereClient._count_active_days(days) - 1
    
    # ═══════════════════════════════════════════════════════════════════════
    # WEEK CONSOLIDATION UTILITIES
    # ═══════════════════════════════════════════════════════════════════════
    
    @staticmethod
    def calculate_week_end_date(week_start: str, flight_end: str) -> str:
        """
        Calculate the end date for a week, capped by flight end date.
        
        A "week" runs 6 days from start (Mon-Sun). The last week
        may end earlier if the flight ends mid-week.
        
        Args:
            week_start: Week start date in MM/DD/YYYY format
            flight_end: Overall flight end date in MM/DD/YYYY format
            
        Returns:
            Week end date in MM/DD/YYYY format
        """
        from datetime import datetime, timedelta
        start = datetime.strptime(week_start, "%m/%d/%Y")
        end = datetime.strptime(flight_end, "%m/%d/%Y")
        week_natural_end = start + timedelta(days=6)
        actual_end = min(week_natural_end, end)
        return actual_end.strftime("%m/%d/%Y")
    
    @staticmethod
    def consolidate_weeks(
        weekly_spots: list,
        week_start_dates: list,
        flight_end: str,
    ) -> list:
        """
        Group consecutive weeks with the same spot count into single Etere lines.
        
        UNIVERSAL RULE: When spot counts are identical across consecutive weeks,
        combine them into one contract line spanning the full date range.
        This dramatically reduces the number of lines entered.
        
        When spot counts differ, split at the boundary into separate groups.
        
        Examples:
            [3,3,3,3,3,3,3,3,3] → 1 group  (3/wk × 9wk = 27 total)
            [3,3,3,5,5,5,3,3,3] → 3 groups (9, 15, 9)
            [0,0,3,3,3,0,0,0,0] → 1 group  (3/wk × 3wk, zeros skipped)
        
        Args:
            weekly_spots: List of spot counts per week (ints)
            week_start_dates: List of week start dates (str MM/DD/YYYY) or
                objects with .start_date attribute (e.g., CharmaineWeekColumn)
            flight_end: Overall flight end date (MM/DD/YYYY)
            
        Returns:
            List of dicts with keys:
                start_date:     First day of group (MM/DD/YYYY)
                end_date:       Last day of group (MM/DD/YYYY)
                spots_per_week: Spots per week in this group (int)
                spots:          Total spots in this group (int)
                weeks:          Number of weeks in this group (int)
                total_spots:    Same as spots (alias for compatibility)
                num_weeks:      Same as weeks (alias for compatibility)
        """
        groups: list = []
        
        # Normalize week_start_dates to strings
        def _get_date(item) -> str:
            if isinstance(item, str):
                return item
            return getattr(item, 'start_date', str(item))
        
        # Filter to non-zero weeks with valid date data
        active_weeks = []
        for idx, spots in enumerate(weekly_spots):
            if spots > 0 and idx < len(week_start_dates):
                active_weeks.append((idx, spots))
        
        if not active_weeks:
            return groups
        
        # Group consecutive weeks with same spot count
        current_start_idx = active_weeks[0][0]
        current_spots = active_weeks[0][1]
        current_count = 1
        
        for i in range(1, len(active_weeks)):
            idx, spots = active_weeks[i]
            prev_idx = active_weeks[i - 1][0]
            
            if spots == current_spots and idx == prev_idx + 1:
                current_count += 1
            else:
                # Close current group
                group_start = _get_date(week_start_dates[current_start_idx])
                last_idx = active_weeks[i - 1][0]
                group_end = EtereClient.calculate_week_end_date(
                    _get_date(week_start_dates[last_idx]), flight_end
                )
                total = current_spots * current_count
                groups.append({
                    'start_date': group_start,
                    'end_date': group_end,
                    'spots_per_week': current_spots,
                    'spots': total,
                    'weeks': current_count,
                    'total_spots': total,
                    'num_weeks': current_count,
                })
                
                # Start new group
                current_start_idx = idx
                current_spots = spots
                current_count = 1
        
        # Close final group
        group_start = _get_date(week_start_dates[current_start_idx])
        last_idx = active_weeks[-1][0]
        group_end = EtereClient.calculate_week_end_date(
            _get_date(week_start_dates[last_idx]), flight_end
        )
        total = current_spots * current_count
        groups.append({
            'start_date': group_start,
            'end_date': group_end,
            'spots_per_week': current_spots,
            'spots': total,
            'weeks': current_count,
            'total_spots': total,
            'num_weeks': current_count,
        })
        
        return groups
    
    @staticmethod
    def consolidate_weeks_from_flight(
        weekly_spots: list,
        flight_start: str,
        flight_end: str,
    ) -> list:
        """
        Convenience wrapper for consolidate_weeks when only flight dates are available.
        
        Generates week start dates from flight_start (one per week, 7 days apart)
        then delegates to consolidate_weeks.
        
        Used by agencies like TCAA whose parsers provide flight dates
        instead of explicit week start date lists.
        
        Args:
            weekly_spots: List of spot counts per week
            flight_start: Flight start date (MM/DD/YYYY)
            flight_end: Flight end date (MM/DD/YYYY)
            
        Returns:
            Same as consolidate_weeks
        """
        from datetime import datetime, timedelta
        start = datetime.strptime(flight_start, "%m/%d/%Y")
        week_dates = []
        for i in range(len(weekly_spots)):
            week_date = start + timedelta(weeks=i)
            week_dates.append(week_date.strftime("%m/%d/%Y"))
        
        return EtereClient.consolidate_weeks(weekly_spots, week_dates, flight_end)
    
    @staticmethod
    def _count_active_days(days: str) -> int:
        """Count number of active days."""
        if days == "M-Su":
            return 7
        elif days == "M-F":
            return 5
        elif days == "M-Sa":
            return 6
        elif days == "Sa-Su":
            return 2
        else:
            return 7


# ═══════════════════════════════════════════════════════════════════════════
# CONVENIENCE FUNCTION
# ═══════════════════════════════════════════════════════════════════════════

def create_etere_client(driver: webdriver.Chrome) -> EtereClient:
    """Create and return EtereClient instance."""
    return EtereClient(driver)
