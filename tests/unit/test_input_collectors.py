"""
Tests for CLI Input Collectors.

Tests the user input collection layer, using mocking to avoid
requiring actual user input during tests.
"""

import pytest
from unittest.mock import Mock, patch, call
from pathlib import Path
import sys

# Add src to path
_src_path = Path(__file__).parent.parent.parent / "src"
if str(_src_path) not in sys.path:
    sys.path.insert(0, str(_src_path))

from presentation.cli.input_collectors import InputCollector, BatchInputCollector
from domain.entities import Order
from domain.enums import OrderType, OrderStatus
from domain.value_objects import OrderInput


# Fixtures

@pytest.fixture
def input_collector():
    """Create an input collector for testing."""
    return InputCollector()


@pytest.fixture
def batch_input_collector():
    """Create a batch input collector for testing."""
    return BatchInputCollector()


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
            status=OrderStatus.PENDING
        ),
        Order(
            pdf_path=Path("/test/orders/order_003.pdf"),
            order_type=OrderType.OPAD,
            customer_name="Customer C",
            status=OrderStatus.PENDING
        ),
    ]


# Tests for InputCollector

class TestGetYesNo:
    """Tests for get_yes_no method."""
    
    def test_accepts_yes(self, input_collector):
        """Should accept 'y' and 'yes' as True."""
        with patch('builtins.input', side_effect=['y']):
            assert input_collector.get_yes_no("Confirm?") is True
        
        with patch('builtins.input', side_effect=['yes']):
            assert input_collector.get_yes_no("Confirm?") is True
        
        with patch('builtins.input', side_effect=['YES']):
            assert input_collector.get_yes_no("Confirm?") is True
    
    def test_accepts_no(self, input_collector):
        """Should accept 'n' and 'no' as False."""
        with patch('builtins.input', side_effect=['n']):
            assert input_collector.get_yes_no("Confirm?") is False
        
        with patch('builtins.input', side_effect=['no']):
            assert input_collector.get_yes_no("Confirm?") is False
        
        with patch('builtins.input', side_effect=['NO']):
            assert input_collector.get_yes_no("Confirm?") is False
    
    def test_rejects_invalid_input(self, input_collector):
        """Should keep asking until valid input received."""
        with patch('builtins.input', side_effect=['maybe', 'sure', 'y']):
            result = input_collector.get_yes_no("Confirm?")
            assert result is True
    
    def test_strips_whitespace(self, input_collector):
        """Should strip whitespace from input."""
        with patch('builtins.input', side_effect=['  yes  ']):
            assert input_collector.get_yes_no("Confirm?") is True


class TestGetString:
    """Tests for get_string method."""
    
    def test_returns_user_input(self, input_collector):
        """Should return user's input."""
        with patch('builtins.input', return_value='test value'):
            result = input_collector.get_string("Enter value")
            assert result == "test value"
    
    def test_uses_default_when_empty(self, input_collector):
        """Should return default value when input is empty."""
        with patch('builtins.input', return_value=''):
            result = input_collector.get_string(
                "Enter value",
                default="default"
            )
            assert result == "default"
    
    def test_requires_input_when_required(self, input_collector):
        """Should keep prompting when required=True and no default."""
        with patch('builtins.input', side_effect=['', '', 'value']):
            result = input_collector.get_string("Enter value", required=True)
            assert result == "value"
    
    def test_allows_empty_when_not_required(self, input_collector):
        """Should accept empty string when required=False."""
        with patch('builtins.input', return_value=''):
            result = input_collector.get_string("Enter value", required=False)
            assert result == ""
    
    def test_strips_whitespace(self, input_collector):
        """Should strip leading/trailing whitespace."""
        with patch('builtins.input', return_value='  value  '):
            result = input_collector.get_string("Enter value")
            assert result == "value"


class TestGetInteger:
    """Tests for get_integer method."""
    
    def test_returns_integer(self, input_collector):
        """Should return integer value."""
        with patch('builtins.input', return_value='42'):
            result = input_collector.get_integer("Enter number")
            assert result == 42
    
    def test_uses_default_when_empty(self, input_collector):
        """Should return default when input is empty."""
        with patch('builtins.input', return_value=''):
            result = input_collector.get_integer("Enter number", default=10)
            assert result == 10
    
    def test_rejects_non_integer(self, input_collector):
        """Should keep prompting for invalid integers."""
        with patch('builtins.input', side_effect=['abc', '3.14', '42']):
            result = input_collector.get_integer("Enter number")
            assert result == 42
    
    def test_enforces_min_value(self, input_collector):
        """Should reject values below minimum."""
        with patch('builtins.input', side_effect=['5', '15']):
            result = input_collector.get_integer("Enter number", min_value=10)
            assert result == 15
    
    def test_enforces_max_value(self, input_collector):
        """Should reject values above maximum."""
        with patch('builtins.input', side_effect=['25', '15']):
            result = input_collector.get_integer("Enter number", max_value=20)
            assert result == 15
    
    def test_enforces_range(self, input_collector):
        """Should enforce both min and max."""
        with patch('builtins.input', side_effect=['5', '25', '15']):
            result = input_collector.get_integer(
                "Enter number",
                min_value=10,
                max_value=20
            )
            assert result == 15


class TestGetChoice:
    """Tests for get_choice method."""
    
    def test_accepts_number(self, input_collector):
        """Should accept choice by number."""
        choices = ['Option A', 'Option B', 'Option C']
        with patch('builtins.input', return_value='2'):
            result = input_collector.get_choice("Select", choices)
            assert result == 'Option B'
    
    def test_accepts_exact_string(self, input_collector):
        """Should accept exact choice string."""
        choices = ['Option A', 'Option B', 'Option C']
        with patch('builtins.input', return_value='Option B'):
            result = input_collector.get_choice("Select", choices)
            assert result == 'Option B'
    
    def test_case_insensitive(self, input_collector):
        """Should match choices case-insensitively."""
        choices = ['Option A', 'Option B', 'Option C']
        with patch('builtins.input', return_value='option b'):
            result = input_collector.get_choice("Select", choices)
            assert result == 'Option B'
    
    def test_rejects_invalid_number(self, input_collector):
        """Should reject numbers out of range."""
        choices = ['Option A', 'Option B', 'Option C']
        with patch('builtins.input', side_effect=['0', '5', '2']):
            result = input_collector.get_choice("Select", choices)
            assert result == 'Option B'
    
    def test_rejects_invalid_string(self, input_collector):
        """Should reject strings not in choices."""
        choices = ['Option A', 'Option B', 'Option C']
        with patch('builtins.input', side_effect=['Option D', 'Option B']):
            result = input_collector.get_choice("Select", choices)
            assert result == 'Option B'


class TestCollectOrderInput:
    """Tests for collect_order_input method."""
    
    def test_collects_order_input(self, input_collector, sample_order):
        """Should collect order code and description."""
        with patch('builtins.input', side_effect=['ORD-001', 'Test Order']):
            result = input_collector.collect_order_input(sample_order)
            
            assert isinstance(result, OrderInput)
            assert result.order_code == 'ORD-001'
            assert result.description == 'Test Order'
    
    def test_uses_defaults(self, input_collector, sample_order):
        """Should use provided defaults."""
        with patch('builtins.input', side_effect=['', '']):
            result = input_collector.collect_order_input(
                sample_order,
                default_code='DEF-001',
                default_description='Default Desc'
            )
            
            assert result.order_code == 'DEF-001'
            assert result.description == 'Default Desc'


class TestSelectOrders:
    """Tests for select_orders method."""
    
    def test_returns_empty_for_no_orders(self, input_collector):
        """Should return empty list when no orders provided."""
        result = input_collector.select_orders([])
        assert result == []
    
    def test_selects_single_order(self, input_collector, sample_orders):
        """Should select single order by number."""
        with patch('builtins.input', return_value='1'):
            result = input_collector.select_orders(sample_orders)
            assert len(result) == 1
            assert result[0] == sample_orders[0]
    
    def test_selects_multiple_orders(self, input_collector, sample_orders):
        """Should select multiple orders."""
        with patch('builtins.input', return_value='1 3'):
            result = input_collector.select_orders(sample_orders)
            assert len(result) == 2
            assert sample_orders[0] in result
            assert sample_orders[2] in result
    
    def test_accepts_comma_separated(self, input_collector, sample_orders):
        """Should accept comma-separated numbers."""
        with patch('builtins.input', return_value='1,2,3'):
            result = input_collector.select_orders(sample_orders)
            assert len(result) == 3
    
    def test_selects_all_orders(self, input_collector, sample_orders):
        """Should select all orders with 'all'."""
        with patch('builtins.input', return_value='all'):
            result = input_collector.select_orders(sample_orders)
            assert len(result) == 3
            assert result == sample_orders
    
    def test_returns_empty_on_cancel(self, input_collector, sample_orders):
        """Should return empty list when user presses Enter."""
        with patch('builtins.input', return_value=''):
            result = input_collector.select_orders(sample_orders)
            assert result == []
    
    def test_ignores_duplicates(self, input_collector, sample_orders):
        """Should not include duplicates in selection."""
        with patch('builtins.input', return_value='1 1 2'):
            result = input_collector.select_orders(sample_orders)
            assert len(result) == 2


class TestConfirmProcessing:
    """Tests for confirm_processing method."""
    
    def test_returns_true_on_yes(self, input_collector, sample_orders):
        """Should return True when user confirms."""
        with patch('builtins.input', return_value='y'):
            result = input_collector.confirm_processing(sample_orders)
            assert result is True
    
    def test_returns_false_on_no(self, input_collector, sample_orders):
        """Should return False when user declines."""
        with patch('builtins.input', return_value='n'):
            result = input_collector.confirm_processing(sample_orders)
            assert result is False


# Tests for BatchInputCollector

class TestBatchCollectAllOrderInputs:
    """Tests for collect_all_order_inputs method."""
    
    def test_collects_inputs_for_all_orders(self, batch_input_collector, sample_orders):
        """Should collect inputs for all orders."""
        inputs = [
            'ORD-001', 'Order 1',
            'ORD-002', 'Order 2',
            'ORD-003', 'Order 3'
        ]
        
        with patch('builtins.input', side_effect=inputs):
            result = batch_input_collector.collect_all_order_inputs(sample_orders)
            
            assert len(result) == 3
            assert all(isinstance(v, OrderInput) for v in result.values())
    
    def test_uses_defaults_provider(self, batch_input_collector, sample_orders):
        """Should use defaults provider when available."""
        def defaults_provider(order):
            return ('DEFAULT', 'Default Description')
        
        with patch('builtins.input', side_effect=['', '', '', '', '', '']):
            result = batch_input_collector.collect_all_order_inputs(
                sample_orders,
                defaults_provider=defaults_provider
            )
            
            assert all(inp.order_code == 'DEFAULT' for inp in result.values())
            assert all(inp.description == 'Default Description' for inp in result.values())
    
    def test_handles_defaults_provider_error(self, batch_input_collector, sample_orders):
        """Should handle errors from defaults provider gracefully."""
        def failing_defaults_provider(order):
            raise Exception("Provider error")
        
        inputs = ['ORD-001', 'Order 1', 'ORD-002', 'Order 2', 'ORD-003', 'Order 3']
        
        with patch('builtins.input', side_effect=inputs):
            # Should not raise exception
            result = batch_input_collector.collect_all_order_inputs(
                sample_orders,
                defaults_provider=failing_defaults_provider
            )
            
            assert len(result) == 3


# Run tests
if __name__ == "__main__":
    pytest.main([__file__, "-v"])
