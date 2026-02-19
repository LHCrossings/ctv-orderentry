"""
Tests for CustomerMatchingService - Business logic testing.

These tests use a mock repository to test the service logic in isolation.
"""

import sys
from pathlib import Path
from unittest.mock import Mock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from business_logic.services.customer_matching_service import CustomerMatchingService
from domain.entities import Customer
from domain.enums import OrderType


class TestCustomerMatchingService:
    """Test customer matching service logic."""

    @pytest.fixture
    def mock_repository(self):
        """Create mock repository for testing."""
        return Mock()

    @pytest.fixture
    def service(self, mock_repository):
        """Create service with mock repository."""
        return CustomerMatchingService(mock_repository)

    def test_find_customer_exact_match(self, service, mock_repository):
        """Should find customer with exact match."""
        # Setup mock
        customer = Customer("MCDS", "McDonald's", OrderType.WORLDLINK)
        mock_repository.find_by_fuzzy_match.return_value = customer

        # Call service
        result = service.find_customer("McDonald's", OrderType.WORLDLINK, prompt_if_not_found=False)

        # Verify
        assert result == "MCDS"
        mock_repository.find_by_fuzzy_match.assert_called_once_with("McDonald's", OrderType.WORLDLINK)

    def test_find_customer_not_found_no_prompt(self, service, mock_repository):
        """Should return None when customer not found and prompting disabled."""
        # Setup mock
        mock_repository.find_by_fuzzy_match.return_value = None

        # Call service
        result = service.find_customer("Unknown", OrderType.WORLDLINK, prompt_if_not_found=False)

        # Verify
        assert result is None

    def test_find_customer_empty_name(self, service, mock_repository):
        """Should return None for empty customer name."""
        result = service.find_customer("", OrderType.WORLDLINK, prompt_if_not_found=False)

        assert result is None
        mock_repository.find_by_fuzzy_match.assert_not_called()

    def test_find_customer_whitespace_name(self, service, mock_repository):
        """Should return None for whitespace-only customer name."""
        result = service.find_customer("   ", OrderType.WORLDLINK, prompt_if_not_found=False)

        assert result is None
        mock_repository.find_by_fuzzy_match.assert_not_called()

    def test_add_customer(self, service, mock_repository):
        """Should add customer to repository."""
        customer = service.add_customer("Test Company", "TEST", OrderType.WORLDLINK)

        assert customer.customer_id == "TEST"
        assert customer.customer_name == "Test Company"
        assert customer.order_type == OrderType.WORLDLINK

        mock_repository.save.assert_called_once()

    def test_remove_customer(self, service, mock_repository):
        """Should remove customer from repository."""
        mock_repository.delete.return_value = True

        result = service.remove_customer("Test Company", OrderType.WORLDLINK)

        assert result is True
        mock_repository.delete.assert_called_once_with("Test Company", OrderType.WORLDLINK)

    def test_list_customers_all(self, service, mock_repository):
        """Should list all customers when no filter specified."""
        customers = [
            Customer("MCDS", "McDonald's", OrderType.WORLDLINK),
            Customer("TOYO", "Toyota", OrderType.TCAA),
        ]
        mock_repository.list_all.return_value = customers

        result = service.list_customers()

        assert len(result) == 2
        mock_repository.list_all.assert_called_once()

    def test_list_customers_filtered(self, service, mock_repository):
        """Should list customers filtered by order type."""
        customers = [
            Customer("MCDS", "McDonald's", OrderType.WORLDLINK),
            Customer("WNDY", "Wendy's", OrderType.WORLDLINK),
        ]
        mock_repository.list_by_order_type.return_value = customers

        result = service.list_customers(OrderType.WORLDLINK)

        assert len(result) == 2
        assert all(c.order_type == OrderType.WORLDLINK for c in result)
        mock_repository.list_by_order_type.assert_called_once_with(OrderType.WORLDLINK)

    def test_get_statistics(self, service, mock_repository):
        """Should return statistics about customer database."""
        customers = [
            Customer("MCDS", "McDonald's", OrderType.WORLDLINK),
            Customer("WNDY", "Wendy's", OrderType.WORLDLINK),
            Customer("TOYO", "Toyota", OrderType.TCAA),
        ]
        mock_repository.list_all.return_value = customers

        stats = service.get_statistics()

        assert stats['total'] == 3
        assert stats['WORLDLINK'] == 2
        assert stats['TCAA'] == 1

    def test_get_statistics_empty(self, service, mock_repository):
        """Should return zero statistics for empty database."""
        mock_repository.list_all.return_value = []

        stats = service.get_statistics()

        assert stats['total'] == 0
        assert 'WORLDLINK' not in stats


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
