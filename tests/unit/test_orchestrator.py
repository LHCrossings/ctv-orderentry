"""
Tests for Application Orchestrator.
"""

import pytest
from pathlib import Path
from unittest.mock import Mock, MagicMock, patch
import sys

# Add src to path
_src_path = Path(__file__).parent.parent.parent / "src"
if str(_src_path) not in sys.path:
    sys.path.insert(0, str(_src_path))

from orchestration.orchestrator import ApplicationOrchestrator, create_orchestrator
from orchestration.config import ApplicationConfig
from domain.entities import Order, ProcessingResult, Contract
from domain.enums import OrderType, OrderStatus


@pytest.fixture
def test_config(tmp_path):
    """Create test configuration."""
    return ApplicationConfig(
        incoming_dir=tmp_path / "incoming",
        processed_dir=tmp_path / "processed",
        error_dir=tmp_path / "error",
        customer_db_path=tmp_path / "customers.db",
        batch_size=5,
        auto_process=True,
        require_confirmation=False
    )


@pytest.fixture
def mock_services():
    """Create mock services."""
    detection_service = Mock()
    customer_repository = Mock()
    processing_service = Mock()
    
    return detection_service, customer_repository, processing_service


@pytest.fixture
def sample_order():
    """Create a sample order."""
    return Order(
        pdf_path=Path("/test/order.pdf"),
        order_type=OrderType.WORLDLINK,
        customer_name="Test Customer",
        status=OrderStatus.PENDING
    )


@pytest.fixture
def sample_result():
    """Create a sample processing result."""
    return ProcessingResult(
        success=True,
        order_type=OrderType.WORLDLINK,
        contracts=[
            Contract(
                contract_number="WL-001",
                order_type=OrderType.WORLDLINK
            )
        ],
        error_message=None
    )


class TestApplicationOrchestrator:
    """Tests for ApplicationOrchestrator."""
    
    def test_create_orchestrator(self, test_config, mock_services):
        """Should create orchestrator with all dependencies."""
        detection_service, customer_repository, processing_service = mock_services
        
        orchestrator = ApplicationOrchestrator(
            config=test_config,
            detection_service=detection_service,
            customer_repository=customer_repository,
            processing_service=processing_service
        )
        
        assert orchestrator._config == test_config
        assert orchestrator._detection_service == detection_service
        assert orchestrator._customer_repository == customer_repository
        assert orchestrator._processing_service == processing_service
    
    def test_creates_default_presentation_components(
        self,
        test_config,
        mock_services
    ):
        """Should create default presentation components if not provided."""
        detection_service, customer_repository, processing_service = mock_services
        
        orchestrator = ApplicationOrchestrator(
            config=test_config,
            detection_service=detection_service,
            customer_repository=customer_repository,
            processing_service=processing_service
        )
        
        # Should have created presentation components
        assert orchestrator._input_collector is not None
        assert orchestrator._batch_input_collector is not None
        assert orchestrator._order_formatter is not None
        assert orchestrator._result_formatter is not None
        assert orchestrator._progress_formatter is not None
    
    def test_accepts_custom_presentation_components(
        self,
        test_config,
        mock_services
    ):
        """Should accept custom presentation components."""
        detection_service, customer_repository, processing_service = mock_services
        
        # Create custom components
        custom_input = Mock()
        custom_batch = Mock()
        custom_order_formatter = Mock()
        custom_result_formatter = Mock()
        custom_progress_formatter = Mock()
        
        orchestrator = ApplicationOrchestrator(
            config=test_config,
            detection_service=detection_service,
            customer_repository=customer_repository,
            processing_service=processing_service,
            input_collector=custom_input,
            batch_input_collector=custom_batch,
            order_formatter=custom_order_formatter,
            result_formatter=custom_result_formatter,
            progress_formatter=custom_progress_formatter
        )
        
        assert orchestrator._input_collector == custom_input
        assert orchestrator._batch_input_collector == custom_batch
        assert orchestrator._order_formatter == custom_order_formatter
        assert orchestrator._result_formatter == custom_result_formatter
        assert orchestrator._progress_formatter == custom_progress_formatter


class TestCreateOrchestrator:
    """Tests for create_orchestrator factory function."""
    
    def test_creates_with_default_config(self):
        """Should create orchestrator with default configuration."""
        orchestrator = create_orchestrator()
        
        assert orchestrator is not None
        assert orchestrator._config is not None
        assert orchestrator._detection_service is not None
        assert orchestrator._customer_repository is not None
        assert orchestrator._processing_service is not None
    
    def test_creates_with_custom_config(self, test_config):
        """Should create orchestrator with provided configuration."""
        # Ensure directories exist
        test_config.ensure_directories()
        
        orchestrator = create_orchestrator(test_config)
        
        assert orchestrator._config == test_config
    
    def test_ensures_directories_exist(self, tmp_path):
        """Should create directories if they don't exist."""
        config = ApplicationConfig(
            incoming_dir=tmp_path / "incoming",
            processed_dir=tmp_path / "processed",
            error_dir=tmp_path / "error",
            customer_db_path=tmp_path / "data" / "customers.db"
        )
        
        # Directories shouldn't exist yet
        assert not config.incoming_dir.exists()
        
        # Create orchestrator
        orchestrator = create_orchestrator(config)
        
        # Directories should now exist
        assert config.incoming_dir.exists()
        assert config.processed_dir.exists()
        assert config.error_dir.exists()


class TestOrchestratorModes:
    """Tests for different orchestrator execution modes."""
    
    @patch('orchestration.orchestrator.OrderScanner')
    @patch('builtins.print')
    def test_run_interactive_with_no_orders(
        self,
        mock_print,
        mock_scanner_class,
        test_config,
        mock_services
    ):
        """Should handle case when no orders are found."""
        detection_service, customer_repository, processing_service = mock_services
        
        # Mock scanner to return no orders
        mock_scanner = Mock()
        mock_scanner.scan_for_orders.return_value = []
        mock_scanner_class.return_value = mock_scanner
        
        orchestrator = ApplicationOrchestrator(
            config=test_config,
            detection_service=detection_service,
            customer_repository=customer_repository,
            processing_service=processing_service
        )
        
        orchestrator.run_interactive()
        
        # Should have scanned
        mock_scanner.scan_for_orders.assert_called_once()
        
        # Should print info message (checking the call happened)
        assert any(
            "[INFO]" in str(call) and "No orders" in str(call)
            for call in mock_print.call_args_list
        )
    
    @patch('orchestration.orchestrator.OrderScanner')
    @patch('builtins.print')
    def test_run_batch_with_no_orders(
        self,
        mock_print,
        mock_scanner_class,
        test_config,
        mock_services
    ):
        """Should handle batch mode with no orders."""
        detection_service, customer_repository, processing_service = mock_services
        
        # Mock scanner to return no orders
        mock_scanner = Mock()
        mock_scanner.scan_for_orders.return_value = []
        mock_scanner_class.return_value = mock_scanner
        
        orchestrator = ApplicationOrchestrator(
            config=test_config,
            detection_service=detection_service,
            customer_repository=customer_repository,
            processing_service=processing_service
        )
        
        orchestrator.run_batch()
        
        # Should have scanned
        mock_scanner.scan_for_orders.assert_called_once()
    
    @patch('orchestration.orchestrator.OrderScanner')
    @patch('builtins.print')
    def test_run_auto_with_no_orders(
        self,
        mock_print,
        mock_scanner_class,
        test_config,
        mock_services
    ):
        """Should handle auto mode with no orders."""
        detection_service, customer_repository, processing_service = mock_services
        
        # Mock scanner to return no orders
        mock_scanner = Mock()
        mock_scanner.scan_for_orders.return_value = []
        mock_scanner_class.return_value = mock_scanner
        
        orchestrator = ApplicationOrchestrator(
            config=test_config,
            detection_service=detection_service,
            customer_repository=customer_repository,
            processing_service=processing_service
        )
        
        orchestrator.run_auto()
        
        # Should have scanned
        mock_scanner.scan_for_orders.assert_called_once()


# Run tests
if __name__ == "__main__":
    pytest.main([__file__, "-v"])
