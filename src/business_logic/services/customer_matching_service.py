"""
Customer Matching Service - Business logic for customer detection and matching.

This service handles the self-learning customer detection system, including
fuzzy matching, user interaction, and database updates.
"""

from pathlib import Path
import sys

# Add src to path for imports
_src_path = Path(__file__).parent.parent.parent
if str(_src_path) not in sys.path:
    sys.path.insert(0, str(_src_path))

from domain.entities import Customer
from domain.enums import OrderType
from data_access.repositories.customer_repository import CustomerRepository


class CustomerMatchingService:
    """
    Service for matching customer names to customer IDs.
    
    This service provides the self-learning customer detection system
    that was previously in customer_matcher.py. It uses the repository
    for data access and implements the business logic for matching.
    """
    
    def __init__(self, repository: CustomerRepository):
        """
        Initialize service with repository dependency.
        
        Args:
            repository: Customer repository for data access
        """
        self._repository = repository
    
    def find_customer(
        self,
        customer_name: str,
        order_type: OrderType,
        prompt_if_not_found: bool = True
    ) -> str | None:
        """
        Find customer ID for a given name, with optional user interaction.
        
        This is the main entry point that replaces the detect_customer function.
        It searches the database with fuzzy matching, and if not found,
        optionally prompts the user to enter the customer ID.
        
        Args:
            customer_name: Name of customer to find
            order_type: Type of order (for context-specific matching)
            prompt_if_not_found: If True, prompt user when customer not found
            
        Returns:
            Customer ID if found or entered, None if not found and not prompted
            
        Examples:
            >>> service = CustomerMatchingService(repo)
            >>> customer_id = service.find_customer("McDonald's", OrderType.WORLDLINK)
            >>> print(customer_id)
            "MCDS"
        """
        if not customer_name or not customer_name.strip():
            return None
        
        # Try to find in database
        customer = self._repository.find_by_fuzzy_match(customer_name, order_type)
        
        if customer:
            print(f"[CUSTOMER] ✓ Found: {customer.customer_name} → {customer.customer_id}")
            return customer.customer_id
        
        # Not found in database
        if not prompt_if_not_found:
            return None
        
        # Prompt user for customer ID
        return self._prompt_for_new_customer(customer_name, order_type)
    
    def _prompt_for_new_customer(
        self,
        customer_name: str,
        order_type: OrderType
    ) -> str | None:
        """
        Prompt user to enter customer ID for unknown customer.
        
        Args:
            customer_name: Name of new customer
            order_type: Order type context
            
        Returns:
            Customer ID entered by user, or None if cancelled
        """
        print(f"\n[CUSTOMER] Customer not found: '{customer_name}'")
        print(f"[CUSTOMER] Order type: {order_type.name}")
        
        # Show existing customers for reference
        existing = self._repository.list_by_order_type(order_type)
        if existing:
            print(f"\n[CUSTOMER] Existing customers for {order_type.name}:")
            for i, customer in enumerate(existing[:10], 1):  # Show first 10
                print(f"  {i}. {customer.customer_name} → {customer.customer_id}")
            if len(existing) > 10:
                print(f"  ... and {len(existing) - 10} more")
        
        print()
        customer_id = input(f"Enter customer ID for '{customer_name}' (or press Enter to skip): ").strip()
        
        if not customer_id:
            return None
        
        # Save to database for future use
        new_customer = Customer(
            customer_id=customer_id,
            customer_name=customer_name,
            order_type=order_type
        )
        self._repository.save(new_customer)
        
        print(f"[CUSTOMER] ✓ Saved: {customer_name} → {customer_id}")
        
        return customer_id
    
    def add_customer(
        self,
        customer_name: str,
        customer_id: str,
        order_type: OrderType
    ) -> Customer:
        """
        Manually add a customer to the database.
        
        Args:
            customer_name: Name of customer
            customer_id: Customer ID in Etere
            order_type: Order type context
            
        Returns:
            Created Customer entity
        """
        customer = Customer(
            customer_id=customer_id,
            customer_name=customer_name,
            order_type=order_type
        )
        self._repository.save(customer)
        return customer
    
    def remove_customer(
        self,
        customer_name: str,
        order_type: OrderType
    ) -> bool:
        """
        Remove a customer from the database.
        
        Args:
            customer_name: Name of customer to remove
            order_type: Order type context
            
        Returns:
            True if customer was removed, False if not found
        """
        return self._repository.delete(customer_name, order_type)
    
    def list_customers(
        self,
        order_type: OrderType | None = None
    ) -> list[Customer]:
        """
        List all customers, optionally filtered by order type.
        
        Args:
            order_type: Optional order type to filter by
            
        Returns:
            List of customers
        """
        if order_type:
            return self._repository.list_by_order_type(order_type)
        return self._repository.list_all()
    
    def get_statistics(self) -> dict[str, int]:
        """
        Get statistics about the customer database.
        
        Returns:
            Dictionary with customer counts by order type
        """
        all_customers = self._repository.list_all()
        
        stats = {
            'total': len(all_customers)
        }
        
        # Count by order type
        for order_type in OrderType:
            if order_type == OrderType.UNKNOWN:
                continue
            count = sum(1 for c in all_customers if c.order_type == order_type)
            if count > 0:
                stats[order_type.name] = count
        
        return stats


def detect_customer(
    customer_name: str,
    order_type: str | OrderType,
    market: str | None = None,
    repository_path: Path | str = "customer_database.db"
) -> str | None:
    """
    Legacy function for backward compatibility with existing code.
    
    This function maintains the same signature as the old detect_customer
    from customer_matcher.py, making migration easier.
    
    Args:
        customer_name: Name of customer to find
        order_type: Order type (string or enum)
        market: Market (not used, kept for compatibility)
        repository_path: Path to customer database
        
    Returns:
        Customer ID if found, None otherwise
    """
    # Convert string to enum if needed
    if isinstance(order_type, str):
        try:
            order_type = OrderType(order_type)
        except ValueError:
            order_type = OrderType.UNKNOWN
    
    # Create service and find customer
    repo = CustomerRepository(repository_path)
    service = CustomerMatchingService(repo)
    
    return service.find_customer(customer_name, order_type, prompt_if_not_found=True)
