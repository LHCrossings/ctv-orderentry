"""
Tests for OrderProcessingService - Business logic testing.

These tests verify the order processing workflow using mocks.
"""

import shutil
import sys
import tempfile
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from business_logic.services.order_processing_service import (
    OrderProcessingService,
)
from domain.entities import Contract, Order, ProcessingResult
from domain.enums import OrderStatus, OrderType
from domain.value_objects import OrderInput


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
        OrderProcessingService({}, temp_orders_dir)

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

        service.process_order(order, Mock(), order_input)

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


class TestProcessorDispatch:
    """Tests for _PROCESSOR_DISPATCH dict and _process_single_order routing."""

    @pytest.fixture
    def temp_orders_dir(self):
        temp_dir = Path(tempfile.mkdtemp())
        yield temp_dir
        if temp_dir.exists():
            shutil.rmtree(temp_dir)

    @pytest.fixture
    def service(self, temp_orders_dir):
        return OrderProcessingService({}, temp_orders_dir)

    def test_dispatch_dict_covers_all_automated_types(self, service):
        """_PROCESSOR_DISPATCH must contain exactly the 13 dedicated-handler types."""
        expected = {
            OrderType.TCAA, OrderType.MISFIT, OrderType.DAVISELEN,
            OrderType.SAGENT, OrderType.GALEFORCE, OrderType.CHARMAINE,
            OrderType.ADMERASIA, OrderType.HL, OrderType.OPAD,
            OrderType.IGRAPHIX, OrderType.IMPACT, OrderType.RPM,
            OrderType.WORLDLINK,
        }
        assert set(service._PROCESSOR_DISPATCH.keys()) == expected

    def test_dispatch_method_names_exist_on_class(self, service):
        """Every method name in _PROCESSOR_DISPATCH must exist on the service class."""
        for order_type, method_name in service._PROCESSOR_DISPATCH.items():
            assert hasattr(service, method_name), (
                f"Method {method_name!r} for {order_type} not found on class"
            )

    def test_process_single_order_routes_to_correct_method(self, service):
        """_process_single_order must call the mapped method for a registered type."""
        from unittest.mock import Mock
        fake_result = ProcessingResult(success=True, contracts=[], order_type=OrderType.TCAA)
        order = Order(
            pdf_path=Path("/t/o.pdf"), order_type=OrderType.TCAA,
            customer_name="Toyota", status=OrderStatus.PENDING,
        )
        shared_session = Mock()
        with patch.object(service, '_process_tcaa_order', return_value=fake_result) as m:
            result = service._process_single_order(order, shared_session)
        m.assert_called_once_with(order, shared_session)
        assert result is fake_result

    def test_process_single_order_fallback_for_unknown(self, service):
        """UNKNOWN is not in _PROCESSOR_DISPATCH — must fall through to process_order()."""
        fake_result = ProcessingResult(success=True, contracts=[], order_type=OrderType.UNKNOWN)
        order = Order(
            pdf_path=Path("/t/o.pdf"), order_type=OrderType.UNKNOWN,
            customer_name="Unknown", status=OrderStatus.PENDING,
        )
        with patch.object(service, 'process_order', return_value=fake_result) as m:
            result = service._process_single_order(order, None)
        m.assert_called_once_with(order, None)
        assert result is fake_result

    def test_create_stub_result_always_fails(self, service):
        """_create_stub_result must return success=False with order type and customer name."""
        order = Order(
            pdf_path=Path("/t/o.pdf"), order_type=OrderType.WORLDLINK,
            customer_name="WorldLink Co", status=OrderStatus.PENDING,
        )
        result = service._create_stub_result(order)
        assert result.success is False
        assert result.contracts == []
        assert result.order_type == OrderType.WORLDLINK
        assert "WORLDLINK" in result.error_message
        assert "WorldLink Co" in result.error_message

    def test_create_stub_result_includes_order_input(self, service):
        """_create_stub_result includes order_code/description when order_input is set."""
        order = Order(
            pdf_path=Path("/t/o.pdf"), order_type=OrderType.WORLDLINK,
            customer_name="WL", status=OrderStatus.PENDING,
            order_input=OrderInput(order_code="WL123", description="Q1 Campaign"),
        )
        result = service._create_stub_result(order)
        assert "WL123" in result.error_message
        assert "Q1 Campaign" in result.error_message


class TestOrderGroupingLogic:
    """Tests for TCAA-by-PDF grouping in _process_orders_with_session."""

    @pytest.fixture
    def temp_orders_dir(self):
        temp_dir = Path(tempfile.mkdtemp())
        yield temp_dir
        if temp_dir.exists():
            shutil.rmtree(temp_dir)

    @pytest.fixture
    def service(self, temp_orders_dir):
        return OrderProcessingService({}, temp_orders_dir)

    def _tcaa(self, pdf: str, est: str = "001") -> Order:
        return Order(
            pdf_path=Path(f"/t/{pdf}"), order_type=OrderType.TCAA,
            customer_name="Toyota", status=OrderStatus.PENDING,
            estimate_number=est,
        )

    def test_two_tcaa_same_pdf_calls_batch(self, service):
        """Two TCAA orders from the same PDF must be batched together."""
        o1, o2 = self._tcaa("a.pdf", "001"), self._tcaa("a.pdf", "002")
        r = ProcessingResult(success=True, contracts=[], order_type=OrderType.TCAA)
        with patch.object(service, '_process_tcaa_orders_batch', return_value=r) as mb, \
             patch.object(service, '_process_single_order') as ms:
            results = service._process_orders_with_session([o1, o2], None)
        mb.assert_called_once()
        ms.assert_not_called()
        assert results == [r]

    def test_single_tcaa_uses_single_order_path(self, service):
        """A TCAA order with no PDF siblings must go through _process_single_order."""
        o = self._tcaa("a.pdf", "001")
        r = ProcessingResult(success=True, contracts=[], order_type=OrderType.TCAA)
        with patch.object(service, '_process_single_order', return_value=r) as ms, \
             patch.object(service, '_process_tcaa_orders_batch') as mb:
            results = service._process_orders_with_session([o], None)
        ms.assert_called_once_with(o, None)
        mb.assert_not_called()
        assert results == [r]

    def test_non_tcaa_skips_batch_grouping(self, service):
        """Non-TCAA orders must bypass batch grouping entirely."""
        misfit = Order(
            pdf_path=Path("/t/m.pdf"), order_type=OrderType.MISFIT,
            customer_name="Misfit", status=OrderStatus.PENDING,
        )
        r = ProcessingResult(success=True, contracts=[], order_type=OrderType.MISFIT)
        with patch.object(service, '_process_single_order', return_value=r) as ms, \
             patch.object(service, '_process_tcaa_orders_batch') as mb:
            results = service._process_orders_with_session([misfit], None)
        ms.assert_called_once_with(misfit, None)
        mb.assert_not_called()
        assert results == [r]

    def test_mixed_batch_routes_correctly(self, service):
        """2x TCAA same PDF → batch; 1x TCAA diff PDF → single; 1x Misfit → single."""
        t1, t2 = self._tcaa("pdf_a.pdf", "001"), self._tcaa("pdf_a.pdf", "002")
        t3 = self._tcaa("pdf_b.pdf", "001")
        misfit = Order(
            pdf_path=Path("/t/m.pdf"), order_type=OrderType.MISFIT,
            customer_name="Misfit", status=OrderStatus.PENDING,
        )
        batch_r = ProcessingResult(success=True, contracts=[], order_type=OrderType.TCAA)
        r1 = ProcessingResult(success=True, contracts=[], order_type=OrderType.TCAA)
        r2 = ProcessingResult(success=True, contracts=[], order_type=OrderType.MISFIT)
        with patch.object(service, '_process_tcaa_orders_batch', return_value=batch_r) as mb, \
             patch.object(service, '_process_single_order', side_effect=[r1, r2]) as ms:
            results = service._process_orders_with_session([t1, t2, t3, misfit], None)
        mb.assert_called_once()   # pdf_a.pdf batch
        assert ms.call_count == 2  # pdf_b.pdf TCAA + misfit
        assert len(results) == 3   # 1 batch result + 2 single results


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
