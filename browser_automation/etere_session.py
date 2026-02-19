"""
Etere Browser Session Manager

Manages Selenium browser lifecycle for Etere automation.
Handles login, market selection, and cleanup.
"""

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException
import time
from pathlib import Path
import sys

# Add src to path for imports
_src_path = Path(__file__).parent.parent
if str(_src_path) not in sys.path:
    sys.path.insert(0, str(_src_path))

from credential_loader import load_credentials
from src.domain.enums import Market


# Etere Configuration
ETERE_URL = "http://100.102.206.113"
LOGIN_URL = f"{ETERE_URL}/index/login"


class EtereSession:
    """
    Manages a single Etere browser session for automation tasks.
    
    This class handles:
    - Chrome driver initialization
    - User login workflow
    - Market selection
    - Session cleanup
    """
    
    def __init__(self):
        """Initialize session manager."""
        self.driver = None
        self.is_logged_in = False
        self.wait = None
    
    def start(self) -> webdriver.Chrome:
        """
        Initialize Chrome driver.
        
        Returns:
            Chrome WebDriver instance
        """
        print("\n[BROWSER] Initializing Chrome driver...")
        
        options = webdriver.ChromeOptions()
        options.add_argument('--start-maximized')
        
        # Suppress automation warnings
        options.add_experimental_option("excludeSwitches", ["enable-automation"])
        options.add_experimental_option('useAutomationExtension', False)
        
        self.driver = webdriver.Chrome(options=options)
        self.wait = WebDriverWait(self.driver, 10)
        
        print("[BROWSER] ✓ Browser started")
        return self.driver
    
    def login(self) -> None:
        """
        Navigate to Etere login and authenticate.
        
        Attempts auto-login using credentials from credentials.env.
        Falls back to manual login if credentials are unavailable.
        """
        if self.is_logged_in:
            print("[BROWSER] Already logged in")
            return
        
        if not self.driver:
            raise RuntimeError("Browser not started. Call start() first.")
        
        print("\n[LOGIN] Navigating to Etere login page...")
        self.driver.get(LOGIN_URL)
        time.sleep(2)
        
        try:
            username, password = load_credentials()
            
            # Fill username
            user_field = self.wait.until(
                EC.presence_of_element_located((By.ID, "LoginUserName"))
            )
            user_field.clear()
            user_field.send_keys(username)
            
            # Fill password
            from selenium.webdriver.common.keys import Keys
            pass_field = self.driver.find_element(By.ID, "LoginUserPassword")
            pass_field.clear()
            pass_field.send_keys(password)
            
            # Submit
            pass_field.send_keys(Keys.RETURN)
            
            time.sleep(2)
            self.is_logged_in = True
            print("[LOGIN] ✓ Auto-login successful!")
            
        except (FileNotFoundError, ValueError) as e:
            print(f"[LOGIN] ⚠ Auto-login unavailable: {e}")
            print("\n" + "=" * 70)
            print("PLEASE LOG IN TO ETERE MANUALLY")
            print("=" * 70)
            print("1. Enter your username")
            print("2. Enter your password")
            print("3. Click the login button")
            print("4. Wait for the main page to load")
            print("5. Return here and press Enter")
            print("=" * 70)
            
            input("\nPress Enter after you've logged in...")
            
            time.sleep(2)
            self.is_logged_in = True
            print("[LOGIN] ✓ Manual login completed")
    
    def set_market(self, market_code: str = "NYC") -> None:
        """
        Set the master market in Etere.
        
        Args:
            market_code: Market code (default: NYC)
        """
        if not self.driver or not self.is_logged_in:
            raise RuntimeError("Must be logged in to set market")
        
        print(f"\n[MARKET] Setting master market to {market_code}...")
        
        try:
            # Click user menu dropdown
            user_menu = self.wait.until(
                EC.element_to_be_clickable(
                    (By.CSS_SELECTOR, "a.user-profile.dropdown-toggle")
                )
            )
            user_menu.click()
            time.sleep(1)
            
            # Click "Stations" option
            stations_link = self.wait.until(
                EC.element_to_be_clickable(
                    (By.XPATH, "//a[@onclick='OpenSelectStation();']")
                )
            )
            stations_link.click()
            time.sleep(2)
            
            # Wait for station modal
            self.wait.until(
                EC.presence_of_element_located((By.ID, "GalleryStations"))
            )
            
            try:
                coduser = str(Market[market_code].etere_id)
            except KeyError:
                coduser = "1"
            
            # Click the market station
            market_station = self.wait.until(
                EC.element_to_be_clickable(
                    (By.CSS_SELECTOR, f"img[data-coduser='{coduser}'][onclick*='SelectThisUser']")
                )
            )
            market_station.click()
            
            time.sleep(2)
            print(f"[MARKET] ✓ Master market set to {market_code}")
            
        except Exception as e:
            print(f"[MARKET] ⚠ Could not automatically set market: {e}")
            print(f"[MARKET] Please manually set market to {market_code}, then press Enter...")
            input()
    
    def navigate_to_url(self, url: str) -> None:
        """
        Navigate to a specific URL.
        
        Args:
            url: URL to navigate to
        """
        if not self.driver:
            raise RuntimeError("Browser not started")
        
        self.driver.get(url)
        time.sleep(1)
    
    def close(self) -> None:
        """
        Close the browser session.
        
        Provides user a chance to review before closing.
        Logs out before closing to prevent multiple login sessions.
        """
        if not self.driver:
            return
        
        print("\n[BROWSER] Session complete")
        print("[BROWSER] Review the results in the browser if needed")
        
        choice = input("\nClose browser now? (Y/n): ").strip().lower()
        
        if choice in ['', 'y', 'yes']:
            # Logout before closing
            try:
                from browser_automation.etere_client import EtereClient
                etere = EtereClient(self.driver)
                etere.logout()
            except Exception as e:
                print(f"[LOGOUT] ⚠ Logout failed: {e}")
            
            print("[BROWSER] Closing browser...")
            self.driver.quit()
            self.driver = None
            self.is_logged_in = False
            print("[BROWSER] ✓ Browser closed")
        else:
            print("[BROWSER] Browser left open - close manually when done")
            print("[BROWSER] ⚠ Remember to logout manually to prevent session conflicts")
    
    def __enter__(self):
        """
        Context manager entry.
        
        CRITICAL MARKET RULES:
        - Master market persists between orders in Etere
        - MUST explicitly set master market for EVERY order
        - Default: Set to NYC (for all agencies)
        - ONLY exception: WorldLink Asian Channel orders use DAL (Dallas)
        - Individual contract lines set their own market (SEA, SFO, CVC, etc.)
        - Always call set_market("NYC") unless WorldLink Asian Channel
        """
        self.start()
        self.login()
        # Note: Master market is whatever it was from last order - must be set explicitly
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit."""
        self.close()
        return False


# Convenience function for quick testing
def test_session():
    """Test the browser session."""
    print("Testing Etere Session...")
    
    with EtereSession() as session:
        print("✓ Browser started and logged in")
        session.set_market("NYC")
        print("✓ Market set")
        input("Press Enter to close...")
    
    print("✓ Session closed")


if __name__ == "__main__":
    test_session()
