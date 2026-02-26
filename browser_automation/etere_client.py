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

from pathlib import Path
import sys

_project_root = Path(__file__).parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait, Select
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.keys import Keys
from selenium.common.exceptions import TimeoutException, NoSuchElementException
from datetime import date, datetime
from decimal import Decimal
from typing import Optional, List, Tuple
import re
import time

from src.domain.enums import Market


class EtereClient:
    """Single client for ALL Etere web interactions."""
    
    # Etere Configuration
    BASE_URL = "http://100.102.206.113"
    
    SPOT_CODES = {
        "Paid Commercial": 2,
        "BNS": 10,
        "Bonus Spot": 10
    }
    
    def __init__(self, driver: webdriver.Chrome):
        """Initialize with existing Selenium WebDriver."""
        self.driver = driver
        self.wait = WebDriverWait(driver, 15)
        self.last_customer_id: str | None = None  # Set after manual browser selection
    
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
            try:
                market_id = Market[market.upper()].etere_id
            except KeyError:
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
    
    # Standard billing for ALL agency orders. Client/direct orders override these explicitly.
    AGENCY_CHARGE_TO = "Customer share indicating agency %"
    AGENCY_INVOICE_HEADER = "Agency"

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
                self.last_customer_id = populated_id

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
        charge_to: Optional[str] = None,
        invoice_header: Optional[str] = None,
    ) -> None:
        """Fill additional contract details on General tab.

        charge_to and invoice_header default to the universal agency billing
        constants (AGENCY_CHARGE_TO / AGENCY_INVOICE_HEADER). Client/direct
        order automations pass different values explicitly to override.
        """
        if charge_to is None:
            charge_to = self.AGENCY_CHARGE_TO
        if invoice_header is None:
            invoice_header = self.AGENCY_INVOICE_HEADER
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
    # CONTRACT DATE EXTENSION
    # ═══════════════════════════════════════════════════════════════════════

    def extend_contract_end_date(self, contract_number: str, lines: list) -> bool:
        """
        Extend a contract's end date to cover revision lines that go beyond it.

        Also serves as the warm-up navigation for revision orders — navigating to
        the contract page establishes the Etere sales context before adding lines
        (new orders get this via create_contract_header; revisions need it explicitly).

        Args:
            contract_number: Contract to update
            lines: List of line dicts with 'end_date' in MM/DD/YYYY format

        Returns:
            True if successful or no update needed, False if update failed (stops processing)
        """
        try:
            print(f"\n[DATES] Checking contract {contract_number} end date...")

            contract_url = f"{self.BASE_URL}/sales/contract/{contract_number}"

            # Etere's SPA requires two navigations to a contract URL when coming
            # from market setup — the first attempt never lands on the page
            # (SPA isn't ready), the second always succeeds. This mirrors the old
            # code where get_highest_existing_line_number navigated first (silently
            # failing), then update_contract_dates_for_revision navigated again.
            self.driver.get(contract_url)
            time.sleep(3)
            try:
                WebDriverWait(self.driver, 8).until(
                    EC.presence_of_element_located((By.ID, "date"))
                )
            except TimeoutException:
                print(f"[DATES] First navigation didn't land — retrying...")
                self.driver.get(contract_url)
                time.sleep(3)
                self.wait.until(EC.presence_of_element_located((By.ID, "date")))

            expiry_field = self.driver.find_element(By.ID, "expirydate")
            current_to_str = expiry_field.get_attribute("value")
            print(f"[DATES] Current contract end: {current_to_str}")

            latest_end = max(datetime.strptime(line['end_date'], '%m/%d/%Y') for line in lines)
            contract_end = datetime.strptime(current_to_str, '%m/%d/%Y')

            if latest_end <= contract_end:
                print(f"[DATES] ✓ No extension needed")
                return True

            new_end_str = latest_end.strftime('%m/%d/%Y')
            print(f"[DATES] New lines end {new_end_str} — extending contract end date")

            # Update field (handle readonly/disabled via JavaScript)
            is_readonly = expiry_field.get_attribute("readonly")
            is_disabled = expiry_field.get_attribute("disabled")

            if is_readonly or is_disabled:
                self.driver.execute_script("arguments[0].value = arguments[1];", expiry_field, new_end_str)
                self.driver.execute_script(
                    "arguments[0].dispatchEvent(new Event('change', { bubbles: true }));",
                    expiry_field
                )
            else:
                expiry_field.clear()
                expiry_field.send_keys(new_end_str)

            # Save
            save_btn = self.driver.find_element(By.ID, "formContractGeneralSubmit")
            try:
                save_btn.click()
            except Exception:
                self.driver.execute_script("arguments[0].click();", save_btn)
            time.sleep(3)

            # Verify
            saved = self.driver.find_element(By.ID, "expirydate").get_attribute("value")
            if saved == new_end_str:
                print(f"[DATES] ✓ Contract end date extended to {saved}")
                return True
            else:
                print(f"[DATES] ✗ Date save failed — expected {new_end_str}, got {saved}")
                print(f"[DATES] *** STOPPING — fix contract end date before adding lines ***")
                return False

        except Exception as e:
            print(f"[DATES] ✗ Error: {e}")
            import traceback
            traceback.print_exc()
            return False

    # ═══════════════════════════════════════════════════════════════════════
    # CONTRACT LINE SCANNING
    # ═══════════════════════════════════════════════════════════════════════

    def get_all_line_ids_with_numbers(self, contract_number: str) -> list:
        """
        Scan contract Lines tab and return (line_id, line_number) tuples.

        line_id  — Etere's internal ID from onclick="openModalChangeContractLine(id)"
        line_number — Etere's SQL-assigned line number from the first table cell (onscreen value, grows over time)

        Used to determine which lines to refresh after revision adds.
        """
        try:
            print(f"\n[SCAN] Scanning lines for contract {contract_number}...")
            self.driver.get(f"{self.BASE_URL}/sales/contract/{contract_number}")
            time.sleep(3)

            # Click Lines tab
            try:
                tab = self.wait.until(EC.element_to_be_clickable(
                    (By.XPATH, "//a[contains(text(), 'Lines')]")
                ))
                tab.click()
                time.sleep(3)
            except Exception:
                try:
                    tab = self.driver.find_element(By.CSS_SELECTOR, 'a[href="#Lines"]')
                    tab.click()
                    time.sleep(3)
                except Exception:
                    pass

            time.sleep(2)
            lines_data = []

            for row in self.driver.find_elements(By.CSS_SELECTOR, "table tbody tr"):
                try:
                    link = row.find_element(
                        By.CSS_SELECTOR, "a[onclick*='openModalChangeContractLine']"
                    )
                    onclick = link.get_attribute('onclick')
                    if not onclick:
                        continue
                    line_id = onclick.split('(')[1].split(')')[0]

                    cells = row.find_elements(By.TAG_NAME, "td")
                    line_number = None
                    for cell in cells[:3]:
                        try:
                            line_number = int(cell.text.strip())
                            break
                        except ValueError:
                            continue

                    lines_data.append((line_id, line_number))
                except Exception:
                    continue

            print(f"[SCAN] ✓ Found {len(lines_data)} lines")

            # Return to General tab — leaves browser in clean state so subsequent
            # operations (e.g., add_contract_line navigating to modalcreatecontractline)
            # don't get caught by SPA state left on the Lines tab.
            self.driver.get(f"{self.BASE_URL}/sales/contract/{contract_number}")
            time.sleep(2)

            return lines_data

        except Exception as e:
            print(f"[SCAN] ✗ Error: {e}")
            return []

    # ═══════════════════════════════════════════════════════════════════════
    # BLOCK REFRESH (WorldLink Crossings TV only)
    # ═══════════════════════════════════════════════════════════════════════

    def refresh_line_blocks(self, line_id: str) -> bool:
        """
        Refresh blocks for a single contract line.

        Navigates to /sales/modalchangecontractline/{line_id}, cleans
        description asterisks, runs Add Blocks Automatically, checks all
        blocks, and saves.
        """
        try:
            self.driver.get(f"{self.BASE_URL}/sales/modalchangecontractline/{line_id}")
            self.wait.until(EC.presence_of_element_located(
                (By.ID, "contractLineGeneralFromDate")
            ))
            time.sleep(2)

            # Clean description asterisks (Etere appends these after block operations)
            try:
                field = self.driver.find_element(By.ID, "contractLineGeneralDescription")
                desc = field.get_attribute('value') or ''
                if '*' in desc:
                    cleaned = desc.rstrip('*').strip()
                    field.clear()
                    field.send_keys(cleaned)
            except Exception:
                pass

            # Blocks tab
            self.driver.find_element(By.CSS_SELECTOR, 'a[href="#tabLineBlocks"]').click()
            time.sleep(2)

            # Add blocks automatically
            try:
                btn = self.wait.until(EC.element_to_be_clickable(
                    (By.ID, "contractLineBlocksAddBlockAutomatically")
                ))
                btn.click()
                time.sleep(8)
            except Exception as e:
                print(f"[REFRESH] ⚠ Add blocks button: {e}")

            # Check all blocks
            try:
                cb = self.driver.find_element(By.CLASS_NAME, "checkAllTableRows")
                cb.find_element(
                    By.XPATH, "./following-sibling::ins[@class='iCheck-helper']"
                ).click()
                time.sleep(0.5)
            except Exception:
                try:
                    self._click_icheck(
                        self.driver.find_element(By.CLASS_NAME, "checkAllTableRows")
                    )
                    time.sleep(0.5)
                except Exception:
                    pass

            # Save
            self.wait.until(EC.element_to_be_clickable(
                (By.ID, "btnsaveexitcl")
            )).click()
            time.sleep(3)
            return True

        except Exception as e:
            print(f"[REFRESH] ✗ Line {line_id}: {e}")
            return False

    def perform_block_refresh(
        self, contract_number: str, only_lines_above: Optional[int] = None
    ) -> bool:
        """
        Refresh blocks for WorldLink Crossings TV lines.

        Args:
            contract_number: Contract to refresh
            only_lines_above: If set, only refresh lines with line_number > this value.
                              Pass highest_line from gather_worldlink_inputs for revisions
                              (e.g., 12 → refreshes lines 13, 14, 15...).
                              None (default) refreshes all lines (new contracts).

        Only called for Crossings TV — Asian Channel is single-market, no refresh needed.
        """
        print(f"\n{'='*60}")
        print(f"BLOCK REFRESH: Contract {contract_number}")
        if only_lines_above is not None:
            print(f"Filter: lines > {only_lines_above} only")
        print(f"{'='*60}")

        lines_data = self.get_all_line_ids_with_numbers(contract_number)
        if not lines_data:
            print("[REFRESH] ✗ No lines found")
            return False

        if only_lines_above is not None:
            lines_data = [
                (lid, lnum) for lid, lnum in lines_data
                if lnum is not None and lnum > only_lines_above
            ]

        if not lines_data:
            print("[REFRESH] ✓ No new lines to refresh")
            return True

        print(f"[REFRESH] Refreshing {len(lines_data)} lines...")
        ok_count = 0
        for idx, (line_id, line_num) in enumerate(lines_data, 1):
            label = f"Line {line_num}" if line_num else f"ID {line_id}"
            print(f"[REFRESH] {idx}/{len(lines_data)}: {label}")
            if self.refresh_line_blocks(line_id):
                ok_count += 1
                print(f"[REFRESH] ✓")
            else:
                print(f"[REFRESH] ✗")
            time.sleep(2)

        print(f"\n[REFRESH] ✓ Complete — {ok_count}/{len(lines_data)} succeeded")
        return ok_count == len(lines_data)

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
        separation_intervals: Tuple[int, int, int] = (15, 0, 0),  # DEFAULT: Customer=15, Event=0, Order=0
        is_bookend: bool = False,
        other_markets: Optional[List[str]] = None,  # WorldLink CMP multi-market replication
    ) -> bool:
        """
        Add contract line to existing contract.
        
        Complete workflow:
        1. Navigate to Add Line page
        2. Fill all GENERAL tab fields
        3. Select days of week
        4. Set separation intervals in OPTIONS tab
        5. Set bookend scheduling type if requested
        6. Save line
        
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
                # Count active days in the days pattern
                day_count = self._count_active_days(days)
                
                # Calculate using ceiling division to ensure all spots can fit
                # Example: 14 spots ÷ 6 days = 2.33 → 3/day (not 2/day)
                if day_count > 0 and spots_per_week > 0:
                    import math
                    max_daily_run = math.ceil(spots_per_week / day_count)
                else:
                    max_daily_run = spots_per_week  # Fallback
                
                print(f"[LINE] ℹ Auto-calculated max_daily_run: {spots_per_week} spots/week ÷ {day_count} days = {max_daily_run} spots/day")
            
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
            try:
                market_id = Market[market.upper()].etere_id
                market_select = Select(self.driver.find_element(
                    By.ID, "selectedschedStation"
                ))
                market_select.select_by_value(str(market_id))
                print(f"[LINE] ✓ Market: {market}")
            except KeyError:
                print(f"[LINE] ⚠ Unknown market: {market}, skipping station selection")
            
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
            
            # SCHEDULING TYPE - Bookend (Top and Bottom)
            # Must be set here on the GENERAL tab, before navigating to OPTIONS tab
            if is_bookend:
                print(f"[LINE] Setting bookend scheduling...")
                try:
                    radio = self.driver.find_element(
                        By.CSS_SELECTOR, 'input[name="selectedSchedulingType"][value="6"]'
                    )
                    parent = radio.find_element(By.XPATH, "..")
                    parent.click()
                    time.sleep(0.5)
                    print(f"[LINE] ✓ Bookend set")
                except Exception as e:
                    print(f"[LINE] ⚠ Bookend: {e}")
            
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
            
            # Spots Per Week  (contractLineGeneralMaxWeekSchedule)
            # RULE: If an order specifies exact per-day spot counts (e.g. Admerasia),
            # pass spots_per_week=0 — the weekly cap is irrelevant because per-day
            # placement is fully controlled by max_daily_run.  Only set spots_per_week
            # to a non-zero value for orders that specify a weekly quota and let Etere
            # distribute spots freely within the week.
            spots_field = self.driver.find_element(By.ID, "contractLineGeneralMaxWeekSchedule")
            spots_field.clear()
            spots_field.send_keys(str(spots_per_week))

            # Max Daily Run  (contractLineGeneralMaxDailyRun)
            # For per-day exact orders: set this to the actual spots-per-day from the order.
            # For weekly quota orders: auto-calculated above as ceil(spots_per_week / day_count).
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

            # Other markets (WorldLink CMP lines — ddpselectedStationOther multi-select)
            if other_markets:
                for market_code in other_markets:
                    try:
                        market_id = Market[market_code.upper()].etere_id
                        self.driver.execute_script(
                            "var s = document.getElementById('ddpselectedStationOther');"
                            f"var o = s ? s.querySelector('option[value=\"{market_id}\"]') : null;"
                            "if (o) { o.selected = true; }"
                        )
                    except Exception:
                        pass
                self.driver.execute_script(
                    "$('#ddpselectedStationOther').trigger('change');"
                )
                print(f"[LINE] ✓ Other markets: {', '.join(other_markets)}")

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
        """Select days of week based on pattern string.

        Supports ranges (M-F, M-R, M-Su), comma lists (M,W,R,F), and
        single days (M, S, U).  Unknown strings default to M-Su and log
        a warning rather than silently selecting all days.
        """
        day_ids = [
            'contractLineBlocksSunday',     # index 0
            'contractLineBlocksMonday',     # index 1
            'contractLineBlocksTuesday',    # index 2
            'contractLineBlocksWednesday',  # index 3
            'contractLineBlocksThursday',   # index 4
            'contractLineBlocksFriday',     # index 5
            'contractLineBlocksSaturday'    # index 6
        ]

        active_days = self._parse_day_codes(days)

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
        time_str = time_str.replace('12m', '12a').replace('12n', '12p')  # midnight/noon
        
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
                    
                    # Parse end hour to determine if start should be AM or PM.
                    # Handle compressed format ("130p"=1:30p, "730p"=7:30p) to avoid
                    # greedy \d{1,2} extracting 13 instead of 1 from "130p".
                    compressed = re.match(r'^(\d{3,4})[ap]?$', end_str)
                    if compressed:
                        d = compressed.group(1)
                        end_hour = int(d[0]) if len(d) == 3 else int(d[0:2])
                    else:
                        end_hour_match = re.match(r'(\d{1,2})', end_str)
                        end_hour = int(end_hour_match.group(1)) if end_hour_match else None
                    if end_hour is not None:
                        
                        # If end is PM and either end==12 (noon) or start>end,
                        # then start is AM (e.g., "1130-12p" → 11:30a, "11-130p" → 11a)
                        if end_period == 'p' and hour != 12:
                            if end_hour == 12 or hour > end_hour:
                                period = 'a'
                            else:
                                period = end_period
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
        # Handle compressed 3-4 digit format first ("730p"=7:30p, "130p"=1:30p) to
        # avoid greedy \d{1,2} misparsing "130p" as hour=13.
        compressed_end = re.match(r'^(\d{3,4})([ap]?)$', end_str)
        if compressed_end:
            digits = compressed_end.group(1)
            end_period = compressed_end.group(2) or None
            if len(digits) == 3:
                hour, minute = int(digits[0]), digits[1:3]
            else:
                hour, minute = int(digits[0:2]), digits[2:4]
            period = end_period
        else:
            end_match = re.match(r'(\d{1,2}):?(\d{2})?([ap])?', end_str)
            if end_match:
                hour = int(end_match.group(1))
                minute = end_match.group(2) if end_match.group(2) else "00"
                period = end_match.group(3) if end_match.group(3) else None
            else:
                hour, minute, period = None, None, None

        if hour is not None:
            
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
    
    @staticmethod
    def _parse_day_codes(days: str) -> List[int]:
        """Parse a day-pattern string into a sorted list of day_ids indices.

        day_ids index mapping (matches contractLineBlocks* element order):
            0=Sunday, 1=Monday, 2=Tuesday, 3=Wednesday,
            4=Thursday, 5=Friday, 6=Saturday

        Supported input formats:
            Ranges:      M-F, M-R, M-Su, M-Sa, Sa-Su
            Comma list:  M,W,R,F  or  M,R
            Single:      M  T  W  R  F  S  U
            Aliases:     Sa Su SAT SU Sun SUN
        """
        # Single-letter parser codes → day_ids index
        code_to_idx = {
            'M': 1, 'T': 2, 'W': 3, 'R': 4, 'F': 5, 'S': 6, 'U': 0,
        }
        # Multi-char aliases → normalise to single-letter before lookup
        aliases = {
            'Sa': 'S', 'SAT': 'S',
            'Su': 'U', 'SU': 'U', 'Sun': 'U', 'SUN': 'U',
        }
        # Week sequence used for range expansion (Mon → Sun)
        week_seq = ['M', 'T', 'W', 'R', 'F', 'S', 'U']

        def _resolve(code: str) -> int:
            code = aliases.get(code, code)
            return code_to_idx[code]

        days = days.strip()
        indices = set()

        # Range notation: two tokens separated by a single hyphen
        m = re.match(r'^([A-Za-z]+)-([A-Za-z]+)$', days)
        if m:
            start = aliases.get(m.group(1), m.group(1))
            end   = aliases.get(m.group(2), m.group(2))
            if start in week_seq and end in week_seq:
                si, ei = week_seq.index(start), week_seq.index(end)
                for code in week_seq[si:ei + 1]:
                    indices.add(code_to_idx[code])
            else:
                print(f"[DAYS] ⚠ Unknown range '{days}', defaulting to M-Su")
                return list(range(7))
        else:
            # Comma-separated or single token
            for part in days.split(','):
                part = part.strip()
                try:
                    indices.add(_resolve(part))
                except KeyError:
                    print(f"[DAYS] ⚠ Unknown day code '{part}' in '{days}', skipping")

        if not indices:
            print(f"[DAYS] ⚠ Could not parse '{days}', defaulting to M-Su")
            return list(range(7))

        return sorted(indices)

    @staticmethod
    def _count_active_days(days: str) -> int:
        """Count number of active days in a day-pattern string."""
        return len(EtereClient._parse_day_codes(days))

    # ═══════════════════════════════════════════════════════════════════════
    # WEEK CONSOLIDATION UTILITIES
    # ═══════════════════════════════════════════════════════════════════════

    @staticmethod
    def consolidate_weeks(
        weekly_spots: List[int],
        week_start_dates: List,
        flight_end: str,
    ) -> List[dict]:
        """
        Group consecutive weeks with identical non-zero spot counts.

        Universal helper used by SAGENT, Charmaine, and GaleForce.

        Args:
            weekly_spots: Spots per week, e.g. [3, 3, 0, 3]
            week_start_dates: Either List[str] ("Apr 27") or
                              List[CharmaineWeekColumn] (has .start_date MM/DD/YYYY)
            flight_end: Contract end date in MM/DD/YYYY format

        Returns:
            List of dicts with keys: start_date, end_date, spots_per_week, weeks
            Dates are MM/DD/YYYY strings.
        """
        from datetime import datetime, timedelta

        # Normalise week_start_dates → List[date]
        parsed_dates: List[date] = []
        year = int(flight_end.split('/')[-1])

        month_map = {
            'Jan': 1, 'Feb': 2, 'Mar': 3, 'Apr': 4,
            'May': 5, 'Jun': 6, 'Jul': 7, 'Aug': 8,
            'Sep': 9, 'Oct': 10, 'Nov': 11, 'Dec': 12,
        }

        for item in week_start_dates:
            # CharmaineWeekColumn or any object with start_date attribute
            if hasattr(item, 'start_date'):
                parsed_dates.append(datetime.strptime(item.start_date, '%m/%d/%Y').date())
            elif isinstance(item, str):
                # "Apr 27" format
                parts = item.strip().split()
                if len(parts) == 2 and parts[0] in month_map:
                    m = month_map[parts[0]]
                    d = int(parts[1])
                    # Year-crossing: if the date is before Jan 01 of flight_end year,
                    # use year+1 (unlikely but defensive).
                    parsed_dates.append(date(year, m, d))
                else:
                    # Try MM/DD/YYYY
                    try:
                        parsed_dates.append(datetime.strptime(item, '%m/%d/%Y').date())
                    except ValueError:
                        print(f"[CONSOLIDATE] ⚠ Cannot parse week date '{item}', skipping")
            else:
                print(f"[CONSOLIDATE] ⚠ Unknown week date type {type(item)}, skipping")

        flight_end_date = datetime.strptime(flight_end, '%m/%d/%Y').date()

        ranges = []
        n = min(len(weekly_spots), len(parsed_dates))
        i = 0
        while i < n:
            if weekly_spots[i] == 0:
                i += 1
                continue

            block_spots = weekly_spots[i]
            block_start_date = parsed_dates[i]

            # Extend while consecutive weeks have the same count AND are
            # exactly 7 days apart (handles non-contiguous week schedules,
            # e.g. BMO Apr-May gap before Aug).
            j = i + 1
            while j < n and weekly_spots[j] == block_spots:
                gap = (parsed_dates[j] - parsed_dates[j - 1]).days
                if gap != 7:
                    break  # Non-consecutive weeks — start a new range
                j += 1

            last_week_start = parsed_dates[j - 1]
            # End of last week = Saturday of that week, capped at flight_end
            block_end_date = min(last_week_start + timedelta(days=6), flight_end_date)

            ranges.append({
                'start_date': block_start_date.strftime('%m/%d/%Y'),
                'end_date': block_end_date.strftime('%m/%d/%Y'),
                'spots_per_week': block_spots,
                'weeks': j - i,
            })
            i = j

        return ranges

    @staticmethod
    def consolidate_weeks_from_flight(
        weekly_spots: List[int],
        flight_start: str,
        flight_end: str,
    ) -> List[dict]:
        """
        Generate week dates from flight_start + 7-day increments, then consolidate.

        Used by TCAA where week dates are not explicitly listed in the PDF.

        Args:
            weekly_spots: Spots per week
            flight_start: Contract start date MM/DD/YYYY
            flight_end: Contract end date MM/DD/YYYY

        Returns:
            List of dicts with keys: start_date, end_date, spots_per_week, weeks
        """
        from datetime import datetime, timedelta

        start = datetime.strptime(flight_start, '%m/%d/%Y').date()
        week_dates = [start + timedelta(weeks=i) for i in range(len(weekly_spots))]

        # Build string list in the "Apr 27" format that consolidate_weeks accepts
        month_abbr = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
                      'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
        week_start_dates = [f"{month_abbr[d.month - 1]} {d.day}" for d in week_dates]

        return EtereClient.consolidate_weeks(weekly_spots, week_start_dates, flight_end)


# ═══════════════════════════════════════════════════════════════════════════
# CONVENIENCE FUNCTION
# ═══════════════════════════════════════════════════════════════════════════

def create_etere_client(driver: webdriver.Chrome) -> EtereClient:
    """Create and return EtereClient instance."""
    return EtereClient(driver)
