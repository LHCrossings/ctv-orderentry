"""
Domain Entities - Core business objects with identity.

Entities are identified by their ID rather than their attributes.
They are immutable value objects that represent real business concepts.
"""

from dataclasses import dataclass, replace
from pathlib import Path

from .enums import OrderStatus, OrderType
from .value_objects import OrderInput


@dataclass(frozen=True)
class Order:
    """
    Core domain entity representing an advertising order.
    
    An order is a PDF file from an agency that needs to be processed
    into one or more contracts in the Etere system.
    
    For multi-order PDFs (e.g., TCAA), estimate_number identifies
    which specific order within the PDF this represents.
    """
    pdf_path: Path
    order_type: OrderType
    customer_name: str
    status: OrderStatus = OrderStatus.PENDING
    order_input: OrderInput | None = None
    estimate_number: str | None = None  # For multi-order PDFs (e.g., TCAA)
    
    def is_processable(self) -> bool:
        """
        Check if order can be processed.
        
        An order is processable if:
        - Status is PENDING
        - Order type is recognized (not UNKNOWN)
        - Required inputs are collected (if applicable)
        """
        if self.status != OrderStatus.PENDING:
            return False
        if self.order_type == OrderType.UNKNOWN:
            return False
        return True
    
    def requires_upfront_input(self) -> bool:
        """
        Check if this order type needs user input before processing.
        
        Some agencies (Daviselen, Admerasia) require additional context
        like customer IDs or time overrides that must be gathered upfront.
        """
        return self.order_type in {
            OrderType.DAVISELEN,
            OrderType.ADMERASIA,
            OrderType.MISFIT,
            OrderType.IMPACT
        }
    
    def with_status(self, status: OrderStatus) -> "Order":
        """Create new Order with updated status (immutability pattern)."""
        return replace(self, status=status)
    
    def with_input(self, order_input: OrderInput) -> "Order":
        """Create new Order with collected input data."""
        return replace(self, order_input=order_input)
    
    def get_display_name(self) -> str:
        """Get human-readable name for display."""
        if self.estimate_number:
            return f"{self.pdf_path.name} (Estimate: {self.estimate_number})"
        return self.pdf_path.name


@dataclass(frozen=True)
class Contract:
    """
    Represents a contract created in the Etere broadcast management system.
    
    A single order may result in multiple contracts depending on
    market configuration and agency requirements.
    """
    contract_number: str
    order_type: OrderType
    highest_line: int | None = None
    market: str | None = None
    
    def requires_block_refresh(self) -> bool:
        """
        Determine if this contract needs block refresh in Etere.
        
        Only WorldLink orders with multiple markets need block refresh.
        This is because WorldLink creates NYC lines with actual rates,
        then CMP lines at $0 that get replicated to other markets.
        """
        return self.order_type.requires_block_refresh()
    
    def has_partial_lines(self) -> bool:
        """
        Check if only lines above a certain number need refresh.
        
        For revisions, we only refresh newly added lines, not existing ones.
        """
        return self.highest_line is not None
    
    def get_refresh_range(self) -> tuple[int | None, int | None]:
        """
        Get the range of lines that need refreshing.
        
        Returns:
            Tuple of (start_line, end_line) or (None, None) for all lines
        """
        if self.has_partial_lines():
            return (self.highest_line, None)  # From highest_line to end
        return (None, None)  # All lines


@dataclass(frozen=True)
class ProcessingResult:
    """
    Result of processing a single order.
    
    Encapsulates success/failure status and any contracts created.
    """
    success: bool
    contracts: list[Contract]
    order_type: OrderType
    error_message: str | None = None
    
    def has_contracts(self) -> bool:
        """Check if any contracts were created."""
        return len(self.contracts) > 0
    
    def needs_block_refresh(self) -> bool:
        """Check if any contracts need block refresh."""
        return any(c.requires_block_refresh() for c in self.contracts)
    
    def get_refresh_contracts(self) -> list[Contract]:
        """Get list of contracts that need block refresh."""
        return [c for c in self.contracts if c.requires_block_refresh()]


@dataclass(frozen=True)
class Customer:
    """
    Represents a customer in the system.
    
    Customers are advertisers whose spots are scheduled in Etere.
    """
    customer_id: str
    customer_name: str
    order_type: OrderType
    
    def matches_name(self, name: str, threshold: float = 0.8) -> bool:
        """
        Check if a name matches this customer with fuzzy matching.
        
        Args:
            name: Name to check against
            threshold: Similarity threshold (0.0 to 1.0)
            
        Returns:
            True if names match above threshold
        """
        # Normalize for comparison
        name_normalized = name.lower().strip()
        customer_normalized = self.customer_name.lower().strip()
        
        # Exact match
        if name_normalized == customer_normalized:
            return True
        
        # Simple fuzzy match: check if one contains the other
        if name_normalized in customer_normalized or customer_normalized in name_normalized:
            return True
        
        # For more sophisticated matching, would use fuzzywuzzy library
        return False
