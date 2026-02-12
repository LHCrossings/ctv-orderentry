"""
Output Formatters - Presentation layer for displaying results.

This module handles all output formatting, keeping display logic
separate from business logic.
"""

from pathlib import Path
import sys
from typing import Any

# Add src to path for imports
_src_path = Path(__file__).parent.parent.parent
if str(_src_path) not in sys.path:
    sys.path.insert(0, str(_src_path))

from domain.entities import Order, Contract, ProcessingResult
from domain.enums import OrderType


class ConsoleFormatter:
    """
    Formats output for console display.
    
    This class handles all console output formatting, making it easy
    to change output style or add new formats (HTML, JSON, etc.).
    """
    
    def __init__(self, width: int = 70):
        """
        Initialize formatter.
        
        Args:
            width: Width of output lines
        """
        self._width = width
    
    def header(self, text: str, char: str = "=") -> str:
        """
        Format a header line.
        
        Args:
            text: Header text
            char: Character to use for border
            
        Returns:
            Formatted header string
        """
        lines = [
            char * self._width,
            text,
            char * self._width
        ]
        return "\n".join(lines)
    
    def subheader(self, text: str) -> str:
        """Format a subheader line."""
        return f"\n{text}\n{'-' * self._width}"
    
    def section(self, title: str, content: str) -> str:
        """
        Format a section with title and content.
        
        Args:
            title: Section title
            content: Section content
            
        Returns:
            Formatted section string
        """
        return f"\n{title}\n{'-' * self._width}\n{content}"
    
    def list_items(self, items: list[str], bullet: str = "  -") -> str:
        """
        Format a list of items.
        
        Args:
            items: List of items to display
            bullet: Bullet character/string
            
        Returns:
            Formatted list string
        """
        return "\n".join(f"{bullet} {item}" for item in items)
    
    def key_value(self, key: str, value: Any, indent: int = 0) -> str:
        """
        Format a key-value pair.
        
        Args:
            key: Key name
            value: Value to display
            indent: Number of spaces to indent
            
        Returns:
            Formatted key-value string
        """
        spaces = " " * indent
        return f"{spaces}{key}: {value}"
    
    def success(self, message: str) -> str:
        """Format a success message."""
        return f"✓ {message}"
    
    def error(self, message: str) -> str:
        """Format an error message."""
        return f"✗ {message}"
    
    def warning(self, message: str) -> str:
        """Format a warning message."""
        return f"⚠  {message}"
    
    def info(self, message: str) -> str:
        """Format an info message."""
        return f"ℹ  {message}"


class OrderFormatter(ConsoleFormatter):
    """Formatter specifically for order-related output."""
    
    def format_order_list(self, orders: list[Order]) -> str:
        """
        Format a list of orders for display.
        
        Args:
            orders: Orders to format
            
        Returns:
            Formatted string
        """
        if not orders:
            return self.warning("No orders found")
        
        lines = [self.header("AVAILABLE ORDERS")]
        
        for i, order in enumerate(orders, 1):
            lines.append(f"\n[{i}] {order.get_display_name()}")
            lines.append(f"    Type: {order.order_type.name}")
            lines.append(f"    Customer: {order.customer_name}")
            lines.append(f"    Status: {order.status.name}")
        
        lines.append(f"\nTotal: {len(orders)} order(s)")
        lines.append("=" * self._width)
        
        return "\n".join(lines)
    
    def format_order_summary(self, order: Order) -> str:
        """
        Format a single order summary.
        
        Args:
            order: Order to format
            
        Returns:
            Formatted string
        """
        lines = [
            self.subheader(f"Order: {order.get_display_name()}"),
            self.key_value("Type", order.order_type.name, 2),
            self.key_value("Customer", order.customer_name, 2),
            self.key_value("Status", order.status.name, 2),
        ]
        return "\n".join(lines)


class ProcessingResultFormatter(ConsoleFormatter):
    """Formatter for processing results and summaries."""
    
    def format_processing_result(self, result: ProcessingResult) -> str:
        """
        Format a single processing result.
        
        Args:
            result: Processing result to format
            
        Returns:
            Formatted string
        """
        if result.success:
            lines = [
                self.success(f"Processing completed"),
                self.key_value("Order Type", result.order_type.name, 2),
                self.key_value("Contracts Created", len(result.contracts), 2),
            ]
            
            if result.contracts:
                lines.append("\n  Contracts:")
                for contract in result.contracts:
                    lines.append(f"    - {contract.contract_number}")
        else:
            lines = [
                self.error(f"Processing failed"),
                self.key_value("Order Type", result.order_type.name, 2),
            ]
            if result.error_message:
                lines.append(self.key_value("Error", result.error_message, 2))
        
        return "\n".join(lines)
    
    def format_batch_summary(
        self,
        results: list[ProcessingResult]
    ) -> str:
        """
        Format a summary of batch processing results.
        
        Args:
            results: List of processing results
            
        Returns:
            Formatted summary string
        """
        lines = [self.header("PROCESSING COMPLETE")]
        
        # Count successes and failures
        successful = [r for r in results if r.success]
        failed = [r for r in results if not r.success]
        
        # Overall stats
        total_contracts = sum(len(r.contracts) for r in successful)
        
        lines.append(f"\n{self.success(f'Successfully processed: {len(successful)}/{len(results)} order(s)')}")
        lines.append(self.key_value("Total contracts created", total_contracts, 2))
        
        if failed:
            lines.append(f"\n{self.error(f'Failed: {len(failed)} order(s)')}")
        
        # Group by order type
        by_type: dict[OrderType, list[ProcessingResult]] = {}
        for result in successful:
            if result.order_type not in by_type:
                by_type[result.order_type] = []
            by_type[result.order_type].append(result)
        
        # Display by type
        for order_type, type_results in sorted(by_type.items(), key=lambda x: x[0].name):
            contracts = []
            for result in type_results:
                contracts.extend(result.contracts)
            
            lines.append(self.subheader(f"{order_type.name} ({len(contracts)} contract(s))"))
            for contract in contracts:
                refresh_indicator = "🔄" if contract.requires_block_refresh() else ""
                lines.append(f"  - Contract {contract.contract_number} {refresh_indicator}")
        
        # Failed orders
        if failed:
            lines.append(self.subheader("Failed Orders"))
            for result in failed:
                lines.append(f"  - {result.order_type.name}: {result.error_message or 'Unknown error'}")
        
        lines.append("\n" + "=" * self._width)
        
        return "\n".join(lines)
    
    def format_contracts_by_type(
        self,
        contracts_by_type: dict[OrderType, list[Contract]]
    ) -> str:
        """
        Format contracts grouped by order type.
        
        Args:
            contracts_by_type: Dictionary mapping order type to contracts
            
        Returns:
            Formatted string
        """
        lines = [self.header("CONTRACTS SUMMARY")]
        
        total = sum(len(contracts) for contracts in contracts_by_type.values())
        lines.append(f"\n{self.success(f'Total contracts: {total}')}")
        
        for order_type, contracts in sorted(contracts_by_type.items(), key=lambda x: x[0].name):
            if not contracts:
                continue
            
            lines.append(self.subheader(f"{order_type.name} ({len(contracts)} contract(s))"))
            
            for contract in contracts:
                refresh = " (needs refresh)" if contract.requires_block_refresh() else ""
                lines.append(f"  - {contract.contract_number}{refresh}")
        
        lines.append("\n" + "=" * self._width)
        
        return "\n".join(lines)


class ProgressFormatter(ConsoleFormatter):
    """Formatter for progress indicators."""
    
    def format_progress(
        self,
        current: int,
        total: int,
        description: str = ""
    ) -> str:
        """
        Format a progress indicator.
        
        Args:
            current: Current item number
            total: Total items
            description: Optional description
            
        Returns:
            Formatted progress string
        """
        percentage = (current / total * 100) if total > 0 else 0
        
        if description:
            return f"[{current}/{total}] ({percentage:.0f}%) {description}"
        else:
            return f"[{current}/{total}] ({percentage:.0f}%)"
    
    def format_spinner(self, message: str, frame: int = 0) -> str:
        """
        Format a spinner animation frame.
        
        Args:
            message: Message to display
            frame: Animation frame number
            
        Returns:
            Formatted spinner string
        """
        spinners = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
        spinner = spinners[frame % len(spinners)]
        return f"{spinner} {message}"


# Convenience instances for easy import
console_formatter = ConsoleFormatter()
order_formatter = OrderFormatter()
result_formatter = ProcessingResultFormatter()
progress_formatter = ProgressFormatter()
