"""
Tests for Order Scanner.
"""

import sys
from pathlib import Path
from unittest.mock import Mock

import pytest

# Add src to path
_src_path = Path(__file__).parent.parent.parent / "src"
if str(_src_path) not in sys.path:
    sys.path.insert(0, str(_src_path))

from domain.enums import OrderStatus, OrderType
from orchestration.order_scanner import OrderScanner


@pytest.fixture
def mock_detection_service():
    """Create a mock detection service."""
    service = Mock()
    service.detect_multi_order_pdf.return_value = (OrderType.WORLDLINK, 1)
    service.extract_customer_name.return_value = "Test Customer"
    return service


@pytest.fixture
def incoming_dir(tmp_path):
    """Create a temporary incoming directory."""
    incoming = tmp_path / "incoming"
    incoming.mkdir()
    return incoming


class TestOrderScanner:
    """Tests for OrderScanner."""

    def test_create_scanner(self, mock_detection_service, incoming_dir):
        """Should create scanner with dependencies."""
        scanner = OrderScanner(mock_detection_service, incoming_dir)

        assert scanner._detection_service == mock_detection_service
        assert scanner._incoming_dir == incoming_dir

    def test_scan_empty_directory(self, mock_detection_service, incoming_dir):
        """Should return empty list for empty directory."""
        scanner = OrderScanner(mock_detection_service, incoming_dir)

        orders = scanner.scan_for_orders()

        assert orders == []

    def test_scan_nonexistent_directory(self, mock_detection_service):
        """Should return empty list if directory doesn't exist."""
        scanner = OrderScanner(mock_detection_service, Path("/nonexistent"))

        orders = scanner.scan_for_orders()

        assert orders == []

    def test_scan_with_single_pdf(self, mock_detection_service, incoming_dir):
        """Should find and process single PDF."""
        # Create a test PDF
        pdf_file = incoming_dir / "test_order.pdf"
        pdf_file.touch()

        scanner = OrderScanner(mock_detection_service, incoming_dir)
        orders = scanner.scan_for_orders()

        assert len(orders) == 1
        assert orders[0].pdf_path == pdf_file
        assert orders[0].order_type == OrderType.WORLDLINK
        assert orders[0].customer_name == "Test Customer"
        assert orders[0].status == OrderStatus.PENDING

    def test_scan_with_multiple_pdfs(self, mock_detection_service, incoming_dir):
        """Should find and process multiple PDFs."""
        # Create test PDFs
        pdf1 = incoming_dir / "order1.pdf"
        pdf2 = incoming_dir / "order2.pdf"
        pdf3 = incoming_dir / "order3.pdf"
        pdf1.touch()
        pdf2.touch()
        pdf3.touch()

        scanner = OrderScanner(mock_detection_service, incoming_dir)
        orders = scanner.scan_for_orders()

        assert len(orders) == 3
        assert all(order.status == OrderStatus.PENDING for order in orders)

    def test_scan_ignores_non_pdf_files(self, mock_detection_service, incoming_dir):
        """Should ignore files that aren't PDFs."""
        # Create various files
        (incoming_dir / "order.pdf").touch()
        (incoming_dir / "document.txt").touch()
        (incoming_dir / "image.jpg").touch()
        (incoming_dir / "data.csv").touch()

        scanner = OrderScanner(mock_detection_service, incoming_dir)
        orders = scanner.scan_for_orders()

        # Should only find the PDF
        assert len(orders) == 1
        assert orders[0].pdf_path.suffix == ".pdf"

    def test_scan_with_different_order_types(self, mock_detection_service, incoming_dir):
        """Should detect different order types."""
        # Create PDFs
        pdf1 = incoming_dir / "worldlink.pdf"
        pdf2 = incoming_dir / "tcaa.pdf"
        pdf1.touch()
        pdf2.touch()

        # Mock different return values for each file
        def detect_order_type_side_effect(path):
            if "worldlink" in path.name:
                return (OrderType.WORLDLINK, 1)
            elif "tcaa" in path.name:
                return (OrderType.TCAA, 1)
            return (OrderType.UNKNOWN, 1)

        mock_detection_service.detect_multi_order_pdf.side_effect = detect_order_type_side_effect

        scanner = OrderScanner(mock_detection_service, incoming_dir)
        orders = scanner.scan_for_orders()

        assert len(orders) == 2
        order_types = {order.order_type for order in orders}
        assert OrderType.WORLDLINK in order_types
        assert OrderType.TCAA in order_types

    def test_scan_handles_customer_name_extraction_failure(
        self,
        mock_detection_service,
        incoming_dir
    ):
        """Should use default customer name if extraction fails."""
        pdf = incoming_dir / "order.pdf"
        pdf.touch()

        # Mock extraction to raise exception
        mock_detection_service.extract_customer_name.side_effect = Exception("Extraction failed")

        scanner = OrderScanner(mock_detection_service, incoming_dir)
        orders = scanner.scan_for_orders()

        assert len(orders) == 1
        assert orders[0].customer_name == "Unknown"

    def test_scan_continues_on_individual_file_error(
        self,
        mock_detection_service,
        incoming_dir
    ):
        """Should continue scanning if one file fails."""
        # Create PDFs
        pdf1 = incoming_dir / "good.pdf"
        pdf2 = incoming_dir / "bad.pdf"
        pdf3 = incoming_dir / "also_good.pdf"
        pdf1.touch()
        pdf2.touch()
        pdf3.touch()

        # Mock to fail on specific file
        def detect_side_effect(path):
            if "bad" in path.name:
                raise Exception("Detection failed")
            return (OrderType.WORLDLINK, 1)

        mock_detection_service.detect_multi_order_pdf.side_effect = detect_side_effect

        scanner = OrderScanner(mock_detection_service, incoming_dir)

        # Should capture stdout to check warning is printed
        import io
        from contextlib import redirect_stdout

        f = io.StringIO()
        with redirect_stdout(f):
            orders = scanner.scan_for_orders()

        # Should have processed 2 out of 3 files
        assert len(orders) == 2

    def test_get_pending_orders_alias(self, mock_detection_service, incoming_dir):
        """Should provide alias method for scan_for_orders."""
        pdf = incoming_dir / "order.pdf"
        pdf.touch()

        scanner = OrderScanner(mock_detection_service, incoming_dir)

        # Both methods should return same result
        orders1 = scanner.scan_for_orders()
        orders2 = scanner.get_pending_orders()

        assert len(orders1) == len(orders2) == 1

    def test_count_pending_orders(self, mock_detection_service, incoming_dir):
        """Should count PDFs without creating Order objects."""
        # Create test PDFs
        for i in range(5):
            (incoming_dir / f"order{i}.pdf").touch()

        scanner = OrderScanner(mock_detection_service, incoming_dir)
        count = scanner.count_pending_orders()

        assert count == 5
        # Detection service should not have been called
        mock_detection_service.detect_order_type.assert_not_called()

    def test_count_pending_orders_empty(self, mock_detection_service, incoming_dir):
        """Should return 0 for empty directory."""
        scanner = OrderScanner(mock_detection_service, incoming_dir)
        count = scanner.count_pending_orders()

        assert count == 0

    def test_count_pending_orders_nonexistent_dir(self, mock_detection_service):
        """Should return 0 if directory doesn't exist."""
        scanner = OrderScanner(mock_detection_service, Path("/nonexistent"))
        count = scanner.count_pending_orders()

        assert count == 0

    def test_scan_returns_sorted_results(self, mock_detection_service, incoming_dir):
        """Should return orders sorted by filename."""
        # Create PDFs in non-alphabetical order
        (incoming_dir / "zebra.pdf").touch()
        (incoming_dir / "alpha.pdf").touch()
        (incoming_dir / "delta.pdf").touch()

        scanner = OrderScanner(mock_detection_service, incoming_dir)
        orders = scanner.scan_for_orders()

        # Should be sorted alphabetically
        names = [order.pdf_path.name for order in orders]
        assert names == ["alpha.pdf", "delta.pdf", "zebra.pdf"]


# Run tests
if __name__ == "__main__":
    pytest.main([__file__, "-v"])
