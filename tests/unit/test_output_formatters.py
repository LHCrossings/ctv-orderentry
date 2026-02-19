"""
Tests for Output Formatters.

Tests the output formatting layer, verifying that output is
formatted correctly for console display.
"""

import sys
from pathlib import Path

import pytest

# Add src to path
_src_path = Path(__file__).parent.parent.parent / "src"
if str(_src_path) not in sys.path:
    sys.path.insert(0, str(_src_path))

from domain.entities import Contract, Order, ProcessingResult
from domain.enums import OrderStatus, OrderType
from presentation.formatters.output_formatters import (
    ConsoleFormatter,
    OrderFormatter,
    ProcessingResultFormatter,
    ProgressFormatter,
)

# Fixtures

@pytest.fixture
def console_formatter():
    """Create a console formatter for testing."""
    return ConsoleFormatter(width=70)


@pytest.fixture
def order_formatter():
    """Create an order formatter for testing."""
    return OrderFormatter(width=70)


@pytest.fixture
def result_formatter():
    """Create a processing result formatter for testing."""
    return ProcessingResultFormatter(width=70)


@pytest.fixture
def progress_formatter():
    """Create a progress formatter for testing."""
    return ProgressFormatter(width=70)


@pytest.fixture
def sample_order():
    """Create a sample order for testing."""
    return Order(
        pdf_path=Path("/test/orders/order_001.pdf"),
        order_type=OrderType.WORLDLINK,
        customer_name="Test Customer",
        status=OrderStatus.PENDING
    )


@pytest.fixture
def sample_orders():
    """Create multiple sample orders for testing."""
    return [
        Order(
            pdf_path=Path("/test/orders/order_001.pdf"),
            order_type=OrderType.WORLDLINK,
            customer_name="Customer A",
            status=OrderStatus.PENDING
        ),
        Order(
            pdf_path=Path("/test/orders/order_002.pdf"),
            order_type=OrderType.TCAA,
            customer_name="Customer B",
            status=OrderStatus.PROCESSING
        ),
        Order(
            pdf_path=Path("/test/orders/order_003.pdf"),
            order_type=OrderType.OPAD,
            customer_name="Customer C",
            status=OrderStatus.COMPLETED
        ),
    ]


@pytest.fixture
def sample_contract():
    """Create a sample contract for testing."""
    return Contract(
        contract_number="CON-001",
        order_type=OrderType.WORLDLINK
    )


@pytest.fixture
def sample_contract_with_block():
    """Create a contract that requires block refresh."""
    return Contract(
        contract_number="CON-002",
        order_type=OrderType.WORLDLINK,  # WorldLink requires block refresh
        highest_line=5,
        market="NYC"
    )


# Tests for ConsoleFormatter

class TestConsoleFormatter:
    """Tests for base ConsoleFormatter class."""

    def test_header(self, console_formatter):
        """Should format header with border."""
        result = console_formatter.header("Test Header")

        assert "Test Header" in result
        assert "=" * 70 in result
        assert result.count("\n") == 2

    def test_header_custom_char(self, console_formatter):
        """Should use custom border character."""
        result = console_formatter.header("Test Header", char="-")

        assert "Test Header" in result
        assert "-" * 70 in result

    def test_subheader(self, console_formatter):
        """Should format subheader with separator."""
        result = console_formatter.subheader("Test Subheader")

        assert "Test Subheader" in result
        assert "-" * 70 in result

    def test_section(self, console_formatter):
        """Should format section with title and content."""
        result = console_formatter.section("Section Title", "Section content here")

        assert "Section Title" in result
        assert "Section content here" in result
        assert "-" * 70 in result

    def test_list_items(self, console_formatter):
        """Should format list with bullets."""
        items = ["Item 1", "Item 2", "Item 3"]
        result = console_formatter.list_items(items)

        assert "  - Item 1" in result
        assert "  - Item 2" in result
        assert "  - Item 3" in result

    def test_list_items_custom_bullet(self, console_formatter):
        """Should use custom bullet character."""
        items = ["Item 1", "Item 2"]
        result = console_formatter.list_items(items, bullet="  *")

        assert "  * Item 1" in result
        assert "  * Item 2" in result

    def test_key_value(self, console_formatter):
        """Should format key-value pair."""
        result = console_formatter.key_value("Name", "Value")
        assert result == "Name: Value"

    def test_key_value_with_indent(self, console_formatter):
        """Should indent key-value pair."""
        result = console_formatter.key_value("Name", "Value", indent=4)
        assert result == "    Name: Value"

    def test_success_message(self, console_formatter):
        """Should format success message with checkmark."""
        result = console_formatter.success("Operation completed")
        assert "✓" in result
        assert "Operation completed" in result

    def test_error_message(self, console_formatter):
        """Should format error message with X."""
        result = console_formatter.error("Operation failed")
        assert "✗" in result
        assert "Operation failed" in result

    def test_warning_message(self, console_formatter):
        """Should format warning message with warning symbol."""
        result = console_formatter.warning("Watch out")
        assert "⚠" in result
        assert "Watch out" in result

    def test_info_message(self, console_formatter):
        """Should format info message with info symbol."""
        result = console_formatter.info("FYI")
        assert "ℹ" in result
        assert "FYI" in result


# Tests for OrderFormatter

class TestOrderFormatter:
    """Tests for OrderFormatter class."""

    def test_format_order_list_empty(self, order_formatter):
        """Should handle empty order list."""
        result = order_formatter.format_order_list([])
        assert "No orders found" in result

    def test_format_order_list(self, order_formatter, sample_orders):
        """Should format list of orders."""
        result = order_formatter.format_order_list(sample_orders)

        assert "AVAILABLE ORDERS" in result
        assert "order_001.pdf" in result
        assert "order_002.pdf" in result
        assert "order_003.pdf" in result
        assert "Customer A" in result
        assert "Customer B" in result
        assert "Customer C" in result
        assert "Total: 3 order(s)" in result

    def test_format_order_list_shows_types(self, order_formatter, sample_orders):
        """Should show order types."""
        result = order_formatter.format_order_list(sample_orders)

        assert "WORLDLINK" in result
        assert "TCAA" in result
        assert "OPAD" in result

    def test_format_order_list_shows_status(self, order_formatter, sample_orders):
        """Should show order status."""
        result = order_formatter.format_order_list(sample_orders)

        assert "PENDING" in result
        assert "PROCESSING" in result
        assert "COMPLETED" in result

    def test_format_order_summary(self, order_formatter, sample_order):
        """Should format single order summary."""
        result = order_formatter.format_order_summary(sample_order)

        assert "order_001.pdf" in result
        assert "Test Customer" in result
        assert "WORLDLINK" in result
        assert "PENDING" in result


# Tests for ProcessingResultFormatter

class TestProcessingResultFormatter:
    """Tests for ProcessingResultFormatter class."""

    def test_format_successful_result(self, result_formatter, sample_contract):
        """Should format successful processing result."""
        result = ProcessingResult(
            success=True,
            order_type=OrderType.WORLDLINK,
            contracts=[sample_contract],
            error_message=None
        )

        output = result_formatter.format_processing_result(result)

        assert "✓" in output
        assert "Processing completed" in output
        assert "WORLDLINK" in output
        assert "CON-001" in output

    def test_format_failed_result(self, result_formatter):
        """Should format failed processing result."""
        result = ProcessingResult(
            success=False,
            order_type=OrderType.WORLDLINK,
            contracts=[],
            error_message="Something went wrong"
        )

        output = result_formatter.format_processing_result(result)

        assert "✗" in output
        assert "Processing failed" in output
        assert "Something went wrong" in output

    def test_format_batch_summary_all_success(
        self,
        result_formatter,
        sample_contract,
        sample_contract_with_block
    ):
        """Should format summary of all successful results."""
        results = [
            ProcessingResult(
                success=True,
                order_type=OrderType.WORLDLINK,
                contracts=[sample_contract],
                error_message=None
            ),
            ProcessingResult(
                success=True,
                order_type=OrderType.TCAA,
                contracts=[sample_contract_with_block],
                error_message=None
            ),
        ]

        output = result_formatter.format_batch_summary(results)

        assert "PROCESSING COMPLETE" in output
        assert "Successfully processed: 2/2" in output
        assert "Total contracts created: 2" in output
        assert "CON-001" in output
        assert "CON-002" in output

    def test_format_batch_summary_with_failures(self, result_formatter, sample_contract):
        """Should show failed orders in summary."""
        results = [
            ProcessingResult(
                success=True,
                order_type=OrderType.WORLDLINK,
                contracts=[sample_contract],
                error_message=None
            ),
            ProcessingResult(
                success=False,
                order_type=OrderType.TCAA,
                contracts=[],
                error_message="Missing required field"
            ),
        ]

        output = result_formatter.format_batch_summary(results)

        assert "Successfully processed: 1/2" in output
        assert "Failed: 1 order(s)" in output
        assert "Missing required field" in output

    def test_format_batch_summary_groups_by_type(
        self,
        result_formatter,
        sample_contract
    ):
        """Should group contracts by order type."""
        contract2 = Contract(
            contract_number="CON-002",
            order_type=OrderType.WORLDLINK
        )

        results = [
            ProcessingResult(
                success=True,
                order_type=OrderType.WORLDLINK,
                contracts=[sample_contract, contract2],
                error_message=None
            ),
        ]

        output = result_formatter.format_batch_summary(results)

        assert "WORLDLINK (2 contract(s))" in output

    def test_format_contracts_by_type(self, result_formatter, sample_contract, sample_contract_with_block):
        """Should format contracts grouped by type."""
        contracts_by_type = {
            OrderType.WORLDLINK: [sample_contract],
            OrderType.TCAA: [sample_contract_with_block],
        }

        output = result_formatter.format_contracts_by_type(contracts_by_type)

        assert "CONTRACTS SUMMARY" in output
        assert "Total contracts: 2" in output
        assert "WORLDLINK" in output
        assert "TCAA" in output
        assert "CON-001" in output
        assert "CON-002" in output
        assert "needs refresh" in output  # For contract with block


# Tests for ProgressFormatter

class TestProgressFormatter:
    """Tests for ProgressFormatter class."""

    def test_format_progress(self, progress_formatter):
        """Should format progress indicator."""
        result = progress_formatter.format_progress(5, 10, "Processing orders")

        assert "[5/10]" in result
        assert "50%" in result
        assert "Processing orders" in result

    def test_format_progress_no_description(self, progress_formatter):
        """Should format progress without description."""
        result = progress_formatter.format_progress(3, 10)

        assert "[3/10]" in result
        assert "30%" in result

    def test_format_progress_zero_total(self, progress_formatter):
        """Should handle zero total gracefully."""
        result = progress_formatter.format_progress(0, 0)

        assert "[0/0]" in result
        assert "0%" in result

    def test_format_progress_100_percent(self, progress_formatter):
        """Should show 100% when complete."""
        result = progress_formatter.format_progress(10, 10)

        assert "[10/10]" in result
        assert "100%" in result

    def test_format_spinner(self, progress_formatter):
        """Should format spinner animation."""
        result = progress_formatter.format_spinner("Loading", frame=0)

        assert "Loading" in result
        # Should have a spinner character
        assert len(result) > len("Loading")

    def test_format_spinner_cycles_frames(self, progress_formatter):
        """Should cycle through spinner frames."""
        frames = []
        for i in range(15):  # Test more frames than spinner has
            frame = progress_formatter.format_spinner("Loading", frame=i)
            frames.append(frame)

        # Should have some variation (cycling through frames)
        assert len(set(frames)) > 1


# Run tests
if __name__ == "__main__":
    pytest.main([__file__, "-v"])
