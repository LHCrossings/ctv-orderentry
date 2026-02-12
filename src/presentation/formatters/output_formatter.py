"""
Output Formatters - Handles formatting and display of processing results.

This module formats processing results for display, separating
presentation concerns from business logic.
"""

from pathlib import Path
import sys

# Add src to path for imports
_src_path = Path(__file__).parent.parent.parent
if str(_src_path) not in sys.path:
    sys.path.insert(0, str(_src_path))

from domain.entities import Contract, ProcessingResult
from domain.enums import OrderType


class ProcessingSummaryFormatter:
    """
    Formats processing results into human-readable summaries.
    
    Provides consistent formatting for processing results across
    different output channels.
    """
    
    def format_summary(
        self,
        results: list[ProcessingResult]
    ) -> str:
        """
        Format a summary of processing results.
        
        Args:
            results: List of processing results
            
        Returns:
            Formatted summary string
        """
        lines = []
        
        # Header
        lines.append("="*70)
        lines.append("PROCESSING COMPLETE")
        lines.append("="*70)
        
        # Count results by outcome
        successful = [r for r in results if r.success]
        failed = [r for r in results if not r.success]
        
        # Summary stats
        total_contracts = sum(len(r.contracts) for r in successful)
        lines.append(f"\n✓ Successfully processed {len(successful)}/{len(results)} order(s)")
        lines.append(f"✓ Created {total_contracts} contract(s)")
        
        if failed:
            lines.append(f"✗ Failed: {len(failed)} order(s)")
        
        # Group by order type
        contracts_by_type: dict[OrderType, list[Contract]] = {}
        for result in successful:
            if result.contracts:
                if result.order_type not in contracts_by_type:
                    contracts_by_type[result.order_type] = []
                contracts_by_type[result.order_type].extend(result.contracts)
        
        # Display contracts by type
        if contracts_by_type:
            lines.append("\nContracts created:")
            for order_type in sorted(contracts_by_type.keys(), key=lambda x: x.name):
                contracts = contracts_by_type[order_type]
                lines.append(f"\n  {order_type.name} ({len(contracts)}):")
                for contract in contracts:
                    refresh_note = " (needs refresh)" if contract.requires_block_refresh() else ""
                    lines.append(f"    - Contract {contract.contract_number}{refresh_note}")
        
        # Display failures
        if failed:
            lines.append("\nFailed orders:")
            for result in failed:
                error_msg = result.error_message or "Unknown error"
                lines.append(f"  ✗ {result.order_type.name}: {error_msg}")
        
        lines.append("\n" + "="*70)
        
        return "\n".join(lines)
    
    def format_contracts_for_refresh(
        self,
        contracts: list[Contract]
    ) -> str:
        """
        Format list of contracts needing block refresh.
        
        Args:
            contracts: Contracts that need refresh
            
        Returns:
            Formatted string
        """
        if not contracts:
            return "\n[INFO] No contracts need block refresh"
        
        lines = []
        lines.append("\n" + "="*70)
        lines.append("BLOCK REFRESH NEEDED")
        lines.append("="*70)
        lines.append(f"[REFRESH] Will refresh blocks for {len(contracts)} contract(s)")
        
        for contract in contracts:
            if contract.has_partial_lines():
                lines.append(f"\n[REFRESH] Contract {contract.contract_number}")
                lines.append(f"  → Only lines > {contract.highest_line}")
            else:
                lines.append(f"\n[REFRESH] Contract {contract.contract_number}")
                lines.append(f"  → All lines")
        
        return "\n".join(lines)
    
    def format_progress(
        self,
        current: int,
        total: int,
        message: str = ""
    ) -> str:
        """
        Format progress indicator.
        
        Args:
            current: Current item number
            total: Total items
            message: Optional message
            
        Returns:
            Formatted progress string
        """
        percentage = int((current / total) * 100) if total > 0 else 0
        bar_length = 30
        filled = int((bar_length * current) / total) if total > 0 else 0
        bar = "█" * filled + "░" * (bar_length - filled)
        
        progress_str = f"[{bar}] {percentage}% ({current}/{total})"
        
        if message:
            progress_str += f" - {message}"
        
        return progress_str


class ContractListFormatter:
    """Formats lists of contracts for display."""
    
    def format_contract_list(
        self,
        contracts: list[Contract],
        show_details: bool = False
    ) -> str:
        """
        Format a list of contracts.
        
        Args:
            contracts: Contracts to format
            show_details: Whether to show detailed info
            
        Returns:
            Formatted string
        """
        if not contracts:
            return "[No contracts]"
        
        lines = []
        
        for contract in contracts:
            line = f"Contract {contract.contract_number}"
            
            if show_details:
                details = []
                if contract.order_type:
                    details.append(f"Type: {contract.order_type.name}")
                if contract.market:
                    details.append(f"Market: {contract.market}")
                if contract.has_partial_lines():
                    details.append(f"Lines > {contract.highest_line}")
                if contract.requires_block_refresh():
                    details.append("Needs refresh")
                
                if details:
                    line += f" ({', '.join(details)})"
            
            lines.append(line)
        
        return "\n".join(lines)


class ErrorFormatter:
    """Formats error messages for display."""
    
    def format_error(
        self,
        error: Exception | str,
        context: str = ""
    ) -> str:
        """
        Format an error message.
        
        Args:
            error: Error to format
            context: Optional context about where error occurred
            
        Returns:
            Formatted error string
        """
        lines = []
        lines.append("\n" + "="*70)
        lines.append("ERROR")
        lines.append("="*70)
        
        if context:
            lines.append(f"Context: {context}")
        
        error_msg = str(error) if isinstance(error, Exception) else error
        lines.append(f"\nError: {error_msg}")
        
        lines.append("\n" + "="*70)
        
        return "\n".join(lines)
    
    def format_warning(
        self,
        message: str
    ) -> str:
        """
        Format a warning message.
        
        Args:
            message: Warning message
            
        Returns:
            Formatted warning string
        """
        return f"\n⚠️  WARNING: {message}\n"


class BannerFormatter:
    """Formats banners and headers."""
    
    def format_header(
        self,
        title: str,
        subtitle: str = ""
    ) -> str:
        """
        Format a header banner.
        
        Args:
            title: Main title
            subtitle: Optional subtitle
            
        Returns:
            Formatted header
        """
        lines = []
        lines.append("="*70)
        lines.append(title.center(70))
        if subtitle:
            lines.append(subtitle.center(70))
        lines.append("="*70)
        return "\n".join(lines)
    
    def format_section(
        self,
        title: str
    ) -> str:
        """
        Format a section header.
        
        Args:
            title: Section title
            
        Returns:
            Formatted section header
        """
        lines = []
        lines.append("\n" + "="*70)
        lines.append(title)
        lines.append("="*70)
        return "\n".join(lines)
    
    def format_subsection(
        self,
        title: str
    ) -> str:
        """
        Format a subsection header.
        
        Args:
            title: Subsection title
            
        Returns:
            Formatted subsection header
        """
        return f"\n{title}\n{'-'*70}"


class TableFormatter:
    """Formats data in table format."""
    
    def format_table(
        self,
        headers: list[str],
        rows: list[list[str]],
        column_widths: list[int] | None = None
    ) -> str:
        """
        Format data as a table.
        
        Args:
            headers: Column headers
            rows: Data rows
            column_widths: Optional fixed column widths
            
        Returns:
            Formatted table string
        """
        if not column_widths:
            # Calculate widths
            column_widths = [len(h) for h in headers]
            for row in rows:
                for i, cell in enumerate(row):
                    if i < len(column_widths):
                        column_widths[i] = max(column_widths[i], len(str(cell)))
        
        lines = []
        
        # Header
        header_line = " | ".join(
            headers[i].ljust(column_widths[i])
            for i in range(len(headers))
        )
        lines.append(header_line)
        lines.append("-" * len(header_line))
        
        # Rows
        for row in rows:
            row_line = " | ".join(
                str(row[i]).ljust(column_widths[i])
                for i in range(len(row))
            )
            lines.append(row_line)
        
        return "\n".join(lines)
