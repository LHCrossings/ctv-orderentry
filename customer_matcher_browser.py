"""
Customer Matcher for Browser Automation

Integrates with the refactored customer repository (SQLite).
Provides fuzzy matching and user confirmation workflow.
"""

from pathlib import Path
import sys
from typing import Optional
from dataclasses import dataclass

# Add src to path
_src_path = Path(__file__).parent.parent
if str(_src_path) not in sys.path:
    sys.path.insert(0, str(_src_path))

try:
    from fuzzywuzzy import fuzz
except ImportError:
    print("[WARNING] fuzzywuzzy not installed - customer matching will be limited")
    print("          Install with: pip install fuzzywuzzy python-Levenshtein")
    fuzz = None


@dataclass
class CustomerMatch:
    """Customer match result."""
    customer_id: int
    name: str
    confidence: int  # 0-100


class BrowserCustomerMatcher:
    """
    Customer matching for browser automation.
    
    Integrates with the refactored CustomerRepository (SQLite)
    and provides fuzzy matching with user confirmation.
    """
    
    def __init__(self, customer_repository):
        """
        Initialize matcher.
        
        Args:
            customer_repository: CustomerRepository instance from data_access layer
        """
        self._repo = customer_repository
    
    def find_customer(
        self,
        client_name: str,
        order_type: str,
        threshold: int = 60
    ) -> Optional[int]:
        """
        Find customer ID with fuzzy matching and user confirmation.
        
        Args:
            client_name: Client name from PDF
            order_type: Order type (for context)
            threshold: Minimum match confidence (0-100)
        
        Returns:
            Customer ID if found/confirmed, None if user cancelled
        """
        print("\n" + "=" * 70)
        print(f"CUSTOMER DETECTION: {client_name}")
        print("=" * 70)
        
        # Get all customers from repository
        all_customers = self._repo.get_all_customers()
        
        if not all_customers:
            print("\n[CUSTOMER] No customers in database")
            return self._prompt_manual_entry(client_name, order_type)
        
        # Find fuzzy matches
        matches = self._find_fuzzy_matches(
            client_name,
            all_customers,
            threshold
        )
        
        if not matches:
            print("\n[CUSTOMER] No close matches found")
            return self._prompt_manual_entry(client_name, order_type)
        
        # Show matches and get user selection
        return self._prompt_user_selection(matches, client_name, order_type)
    
    def _find_fuzzy_matches(
        self,
        search_name: str,
        customers: list,
        threshold: int
    ) -> list[CustomerMatch]:
        """
        Find fuzzy matches using fuzzywuzzy.
        
        Args:
            search_name: Name to search for
            customers: List of customer dicts from repository
            threshold: Minimum confidence score
        
        Returns:
            List of CustomerMatch objects, sorted by confidence
        """
        if not fuzz:
            # Fallback to exact matching if fuzzywuzzy not available
            matches = []
            for customer in customers:
                if search_name.lower() in customer['name'].lower():
                    matches.append(CustomerMatch(
                        customer_id=customer['id'],
                        name=customer['name'],
                        confidence=100
                    ))
            return matches
        
        matches = []
        
        for customer in customers:
            # Calculate fuzzy match score
            score = fuzz.token_sort_ratio(
                search_name.lower(),
                customer['name'].lower()
            )
            
            if score >= threshold:
                matches.append(CustomerMatch(
                    customer_id=customer['id'],
                    name=customer['name'],
                    confidence=score
                ))
        
        # Sort by confidence (highest first)
        matches.sort(key=lambda x: x.confidence, reverse=True)
        
        return matches
    
    def _prompt_user_selection(
        self,
        matches: list[CustomerMatch],
        client_name: str,
        order_type: str
    ) -> Optional[int]:
        """
        Display matches and prompt user to select.
        
        Args:
            matches: List of potential matches
            client_name: Original detected name
            order_type: Order type for context
        
        Returns:
            Selected customer ID or None
        """
        print(f"\n[CUSTOMER] Found {len(matches)} potential match(es):\n")
        
        for idx, match in enumerate(matches, 1):
            # Visual confidence bar
            bar_length = match.confidence // 10
            confidence_bar = "█" * bar_length
            
            print(f"{idx}. {match.name}")
            print(f"   ID: {match.customer_id}")
            print(f"   Confidence: [{confidence_bar:10}] {match.confidence}%\n")
        
        print(f"{len(matches) + 1}. None of these - enter Customer ID manually")
        
        while True:
            try:
                choice = input(f"\nSelect option (1-{len(matches) + 1}): ").strip()
                
                if not choice:
                    print("[CUSTOMER] Selection cancelled")
                    return None
                
                choice_num = int(choice)
                
                if 1 <= choice_num <= len(matches):
                    # Valid match selection
                    selected = matches[choice_num - 1]
                    print(f"\n✓ Selected: {selected.name} (ID: {selected.customer_id})")
                    return selected.customer_id
                
                elif choice_num == len(matches) + 1:
                    # Manual entry
                    return self._prompt_manual_entry(client_name, order_type)
                
                else:
                    print(f"Please enter a number between 1 and {len(matches) + 1}")
            
            except ValueError:
                print("Please enter a valid number")
            except KeyboardInterrupt:
                print("\n\n[CUSTOMER] User cancelled")
                return None
    
    def _prompt_manual_entry(
        self,
        client_name: str,
        order_type: str
    ) -> Optional[int]:
        """
        Prompt user to manually enter customer ID.
        
        Args:
            client_name: Client name for reference
            order_type: Order type for context
        
        Returns:
            Customer ID if entered, None if cancelled
        """
        print("\n" + "-" * 70)
        print("MANUAL CUSTOMER ID ENTRY")
        print("-" * 70)
        print(f"Client: {client_name}")
        print(f"Order Type: {order_type}")
        print()
        
        customer_id_input = input("Enter Customer ID (or press Enter to skip): ").strip()
        
        if not customer_id_input:
            print("[CUSTOMER] No customer ID provided - order will be skipped")
            return None
        
        try:
            customer_id = int(customer_id_input)
            
            # Ask if user wants to save for future use
            save = input(f"\nSave '{client_name}' as Customer ID {customer_id}? (Y/n): ").strip().lower()
            
            if save in ['', 'y', 'yes']:
                # Add to repository
                self._repo.add_customer(customer_id, client_name)
                print(f"✓ Saved to database")
            
            return customer_id
        
        except ValueError:
            print("[ERROR] Invalid Customer ID format")
            return None


# Convenience function for legacy compatibility
def detect_customer(
    client_name: str,
    agency: str,
    market: str,
    customer_repository
) -> Optional[int]:
    """
    Detect customer ID with fuzzy matching and user confirmation.
    
    Legacy-compatible wrapper function.
    
    Args:
        client_name: Client name from PDF
        agency: Agency name (for context)
        market: Market code (for context)
        customer_repository: CustomerRepository instance
    
    Returns:
        Customer ID if found/confirmed, None if cancelled
    """
    matcher = BrowserCustomerMatcher(customer_repository)
    return matcher.find_customer(client_name, agency)
