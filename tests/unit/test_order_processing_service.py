"""
Tests for OrderProcessingService - Business logic testing.

These tests verify the order processing workflow using mocks.
"""

import pytest
from pathlib import Path
import sys
from unittest.mock import Mock, MagicMock, patch
import tempfile
import shutil

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from domain.entities import Order, Contract, ProcessingResult
from domain.enums import OrderType, OrderStatus
from domain.value_objects import OrderInput
from business_logic.services.order_processing_service import (
    OrderProcessingService,
    LegacyProcessorAdapter
)


class TestOrderProcessingService:
    """Test order processing service workflow."""
    
    @pytest.fixture
    def temp_orders_dir(self):
        """Create temporary orders directory structure."""
        temp_dir = Path(tempfile.mkdtemp())
        yield temp_dir
        # Cleanup
        if temp_dir.exists():
            shutil.rmtree(temp_dir)
    
    @pytest.fixture
    def mock_processor(self):
        """Create mock processor."""
        processor = Mock()
        processor.process.return_value = ProcessingResult(
            success=True,
            contracts=[Contract("12345", OrderType.WORLDLINK)],
            order_type=OrderType.WORLDLINK
        )
        return processor
    
    @pytest.fixture
    def service(self, mock_processor, temp_orders_dir):
        """Create service with mock processor."""
        processors = {OrderType.WORLDLINK: mock_processor}
        return OrderProcessingService(processors, temp_orders_dir)
    
    def test_directory_setup(self, temp_orders_dir):
        """Should create directory structure on initialization."""
        service = OrderProcessingService({}, temp_orders_dir)
        
        assert (temp_orders_dir / "incoming").exists()
        assert (temp_orders_dir / "processing").exists()
        assert (temp_orders_dir / "completed").exists()
        assert (temp_orders_dir / "failed").exists()
    
    def test_process_order_success(self, service, mock_processor, temp_orders_dir):
        """Should process order successfully."""
        # Create test PDF
        pdf_path = temp_orders_dir / "incoming" / "test_order.pdf"
        pdf_path.parent.mkdir(parents=True, exist_ok=True)
        pdf_path.write_text("test content")
        
        order = Order(
            pdf_path=pdf_path,
            order_type=OrderType.WORLDLINK,
            customer_name="Test Customer",
            status=OrderStatus.PENDING
        )
        
        browser = Mock()
        result = service.process_order(order, browser)
        
        assert result.success is True
        assert len(result.contracts) == 1
        assert result.contracts[0].contract_number == "12345"
        
        # Verify processor was called
        mock_processor.process.assert_called_once()
        
        # Verify file moved to completed
        assert (temp_orders_dir / "completed" / "test_order.pdf").exists()
        assert not (temp_orders_dir / "incoming" / "test_order.pdf").exists()
    
    def test_process_order_not_processable(self, service, temp_orders_dir):
        """Should reject non-processable orders."""
        pdf_path = temp_orders_dir / "incoming" / "test.pdf"
        pdf_path.parent.mkdir(parents=True, exist_ok=True)
        pdf_path.write_text("test")
        
        order = Order(
            pdf_path=pdf_path,
            order_type=OrderType.WORLDLINK,
            customer_name="Test",
            status=OrderStatus.COMPLETED  # Already completed!
        )
        
        result = service.process_order(order, Mock())
        
        assert result.success is False
        assert "not in processable state" in result.error_message
    
    def test_process_order_no_processor(self, service, temp_orders_dir):
        """Should handle missing processor gracefully."""
        pdf_path = temp_orders_dir / "incoming" / "test.pdf"
        pdf_path.parent.mkdir(parents=True, exist_ok=True)
        pdf_path.write_text("test")
        
        order = Order(
            pdf_path=pdf_path,
            order_type=OrderType.TCAA,  # No processor registered
            customer_name="Test",
            status=OrderStatus.PENDING
        )
        
        result = service.process_order(order, Mock())
        
        assert result.success is False
        assert "No processor registered" in result.error_message
        
        # File should be in failed folder
        assert (temp_orders_dir / "failed" / "test.pdf").exists()
    
    def test_process_order_processor_failure(self, service, mock_processor, temp_orders_dir):
        """Should handle processor failure."""
        # Setup processor to return failure
        mock_processor.process.return_value = ProcessingResult(
            success=False,
            contracts=[],
            order_type=OrderType.WORLDLINK,
            error_message="Processing failed"
        )
        
        pdf_path = temp_orders_dir / "incoming" / "test.pdf"
        pdf_path.parent.mkdir(parents=True, exist_ok=True)
        pdf_path.write_text("test")
        
        order = Order(
            pdf_path=pdf_path,
            order_type=OrderType.WORLDLINK,
            customer_name="Test",
            status=OrderStatus.PENDING
        )
        
        result = service.process_order(order, Mock())
        
        assert result.success is False
        
        # File should be in failed folder
        assert (temp_orders_dir / "failed" / "test.pdf").exists()
    
    def test_process_order_with_input(self, service, mock_processor, temp_orders_dir):
        """Should pass order input to processor."""
        pdf_path = temp_orders_dir / "incoming" / "test.pdf"
        pdf_path.parent.mkdir(parents=True, exist_ok=True)
        pdf_path.write_text("test")
        
        order = Order(
            pdf_path=pdf_path,
            order_type=OrderType.WORLDLINK,
            customer_name="Test",
            status=OrderStatus.PENDING
        )
        
        order_input = OrderInput(
            order_code="TEST123",
            description="Test Order"
        )
        
        result = service.process_order(order, Mock(), order_input)
        
        # Verify order_input was passed to processor
        call_args = mock_processor.process.call_args
        assert call_args[0][2] == order_input  # Third argument
    
    def test_register_processor(self, service):
        """Should allow dynamic processor registration."""
        new_processor = Mock()
        
        service.register_processor(OrderType.TCAA, new_processor)
        
        supported = service.get_supported_order_types()
        assert OrderType.TCAA in supported
        assert OrderType.WORLDLINK in supported
    
    def test_get_supported_order_types(self, service):
        """Should return list of supported types."""
        supported = service.get_supported_order_types()
        
        assert OrderType.WORLDLINK in supported
        assert len(supported) == 1


class TestLegacyProcessorAdapter:
    """Test legacy function adapter."""
    
    def test_adapter_success(self):
        """Should adapt legacy function successfully."""
        # Mock legacy function
        def legacy_func(browser, pdf_path, inputs=None):
            return (True, True, [("12345", 10)])
        
        adapter = LegacyProcessorAdapter(legacy_func)
        
        result = adapter.process(Mock(), Path("test.pdf"), None)
        
        assert result.success is True
        assert len(result.contracts) == 1
        assert result.contracts[0].contract_number == "12345"
    
    def test_adapter_with_inputs(self):
        """Should pass inputs to legacy function."""
        called_with = {}
        
        def legacy_func(browser, pdf_path, inputs=None):
            called_with['inputs'] = inputs
            return (True, False, [])
        
        adapter = LegacyProcessorAdapter(legacy_func)
        order_input = OrderInput(
            order_code="TEST",
            description="Test"
        )
        
        adapter.process(Mock(), Path("test.pdf"), order_input)
        
        assert called_with['inputs']['order_code'] == "TEST"
        assert called_with['inputs']['description'] == "Test"
    
    def test_adapter_failure(self):
        """Should handle legacy function exceptions."""
        def legacy_func(browser, pdf_path, inputs=None):
            raise ValueError("Test error")
        
        adapter = LegacyProcessorAdapter(legacy_func)
        
        result = adapter.process(Mock(), Path("test.pdf"), None)
        
        assert result.success is False
        assert "Test error" in result.error_message
    
    def test_adapter_contract_conversion(self):
        """Should convert various legacy contract formats."""
        def legacy_func(browser, pdf_path, inputs=None):
            # Legacy functions return different formats
            return (True, False, [
                ("12345", 10),  # Tuple with highest line
                ("67890", None),  # Tuple without highest line
            ])
        
        adapter = LegacyProcessorAdapter(legacy_func)
        result = adapter.process(Mock(), Path("test.pdf"), None)
        
        assert len(result.contracts) == 2
        assert result.contracts[0].contract_number == "12345"
        assert result.contracts[0].highest_line == 10
        assert result.contracts[1].contract_number == "67890"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
