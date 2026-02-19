"""
Tests for CustomerRepository - Data access layer testing.

These are integration tests that use a real SQLite database (in memory or temp file).
"""

import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from data_access.repositories.customer_repository import CustomerRepository
from domain.entities import Customer
from domain.enums import OrderType


class TestCustomerRepository:
    """Test customer repository with real database."""

    @pytest.fixture
    def temp_db(self):
        """Create temporary database for testing."""
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = Path(f.name)

        yield db_path

        # Cleanup - be graceful about Windows file locking
        try:
            # Force garbage collection to close any lingering connections
            import gc
            gc.collect()

            if db_path.exists():
                db_path.unlink()
        except (PermissionError, OSError):
            # On Windows, temp files may still be locked
            # They'll be cleaned up by the OS eventually
            pass

    @pytest.fixture
    def repository(self, temp_db):
        """Create repository instance with temp database."""
        return CustomerRepository(temp_db)

    def test_database_creation(self, temp_db):
        """Repository should create database tables on initialization."""
        repo = CustomerRepository(temp_db)

        assert temp_db.exists()
        assert repo.count() == 0  # Empty but initialized

    def test_save_customer(self, repository):
        """Should save customer to database."""
        customer = Customer(
            customer_id="MCDS",
            customer_name="McDonald's",
            order_type=OrderType.WORLDLINK
        )

        repository.save(customer)

        assert repository.count() == 1

    def test_find_by_name_exact_match(self, repository):
        """Should find customer by exact name match."""
        customer = Customer(
            customer_id="MCDS",
            customer_name="McDonald's",
            order_type=OrderType.WORLDLINK
        )
        repository.save(customer)

        found = repository.find_by_name("McDonald's", OrderType.WORLDLINK)

        assert found is not None
        assert found.customer_id == "MCDS"
        assert found.customer_name == "McDonald's"

    def test_find_by_name_case_insensitive(self, repository):
        """Should find customer regardless of case."""
        customer = Customer(
            customer_id="MCDS",
            customer_name="McDonald's",
            order_type=OrderType.WORLDLINK
        )
        repository.save(customer)

        found = repository.find_by_name("mcdonald's", OrderType.WORLDLINK)

        assert found is not None
        assert found.customer_id == "MCDS"

    def test_find_by_name_different_order_type(self, repository):
        """Should not find customer with wrong order type."""
        customer = Customer(
            customer_id="MCDS",
            customer_name="McDonald's",
            order_type=OrderType.WORLDLINK
        )
        repository.save(customer)

        found = repository.find_by_name("McDonald's", OrderType.TCAA)

        assert found is None

    def test_find_by_fuzzy_match_exact(self, repository):
        """Fuzzy match should find exact matches."""
        customer = Customer(
            customer_id="TOYO",
            customer_name="Toyota",
            order_type=OrderType.TCAA
        )
        repository.save(customer)

        found = repository.find_by_fuzzy_match("Toyota", OrderType.TCAA)

        assert found is not None
        assert found.customer_id == "TOYO"

    def test_find_by_fuzzy_match_partial(self, repository):
        """Fuzzy match should find partial matches."""
        customer = Customer(
            customer_id="MCDS",
            customer_name="McDonald's Corporation",
            order_type=OrderType.WORLDLINK
        )
        repository.save(customer)

        # Search with partial name
        found = repository.find_by_fuzzy_match("McDonald's", OrderType.WORLDLINK)

        assert found is not None
        assert found.customer_id == "MCDS"

    def test_list_by_order_type(self, repository):
        """Should list all customers for specific order type."""
        customers = [
            Customer("MCDS", "McDonald's", OrderType.WORLDLINK),
            Customer("WNDY", "Wendy's", OrderType.WORLDLINK),
            Customer("TOYO", "Toyota", OrderType.TCAA),
        ]

        for customer in customers:
            repository.save(customer)

        worldlink_customers = repository.list_by_order_type(OrderType.WORLDLINK)

        assert len(worldlink_customers) == 2
        assert all(c.order_type == OrderType.WORLDLINK for c in worldlink_customers)

    def test_delete_customer(self, repository):
        """Should delete customer from database."""
        customer = Customer(
            customer_id="MCDS",
            customer_name="McDonald's",
            order_type=OrderType.WORLDLINK
        )
        repository.save(customer)

        assert repository.count() == 1

        deleted = repository.delete("McDonald's", OrderType.WORLDLINK)

        assert deleted is True
        assert repository.count() == 0

    def test_delete_nonexistent_customer(self, repository):
        """Should return False when deleting nonexistent customer."""
        deleted = repository.delete("Nonexistent", OrderType.WORLDLINK)

        assert deleted is False

    def test_update_customer(self, repository):
        """Saving existing customer should update it."""
        customer = Customer(
            customer_id="OLD_ID",
            customer_name="Test Company",
            order_type=OrderType.WORLDLINK
        )
        repository.save(customer)

        # Update with new ID
        updated = Customer(
            customer_id="NEW_ID",
            customer_name="Test Company",
            order_type=OrderType.WORLDLINK
        )
        repository.save(updated)

        # Should have same count (update, not insert)
        assert repository.count() == 1

        # Should have new ID
        found = repository.find_by_name("Test Company", OrderType.WORLDLINK)
        assert found.customer_id == "NEW_ID"

    def test_list_all(self, repository):
        """Should list all customers across all order types."""
        customers = [
            Customer("MCDS", "McDonald's", OrderType.WORLDLINK),
            Customer("TOYO", "Toyota", OrderType.TCAA),
            Customer("REST", "Restaurant", OrderType.OPAD),
        ]

        for customer in customers:
            repository.save(customer)

        all_customers = repository.list_all()

        assert len(all_customers) == 3
        assert set(c.customer_id for c in all_customers) == {"MCDS", "TOYO", "REST"}

    def test_same_name_different_order_types(self, repository):
        """Same customer name can have different IDs for different order types."""
        customer1 = Customer(
            customer_id="WL_MCDS",
            customer_name="McDonald's",
            order_type=OrderType.WORLDLINK
        )
        customer2 = Customer(
            customer_id="TCAA_MCDS",
            customer_name="McDonald's",
            order_type=OrderType.TCAA
        )

        repository.save(customer1)
        repository.save(customer2)

        assert repository.count() == 2

        found_wl = repository.find_by_name("McDonald's", OrderType.WORLDLINK)
        found_tcaa = repository.find_by_name("McDonald's", OrderType.TCAA)

        assert found_wl.customer_id == "WL_MCDS"
        assert found_tcaa.customer_id == "TCAA_MCDS"


class TestLegacyJSONMigration:
    """Test migration from JSON to SQLite."""

    @pytest.fixture
    def temp_files(self):
        """Create temporary JSON and DB files."""
        with tempfile.NamedTemporaryFile(mode='w', suffix=".json", delete=False) as json_f:
            json_path = Path(json_f.name)
            # Write sample JSON
            json_f.write("""{
                "worldlink": {
                    "McDonald's": "MCDS",
                    "Wendy's": "WNDY"
                },
                "tcaa": {
                    "Toyota": "TOYO"
                }
            }""")

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as db_f:
            db_path = Path(db_f.name)

        yield json_path, db_path

        # Cleanup - Windows needs time for connections to close
        import gc
        import time
        gc.collect()  # Force garbage collection
        time.sleep(0.1)  # Give Windows a moment

        try:
            if json_path.exists():
                json_path.unlink()
        except PermissionError:
            pass

        try:
            if db_path.exists():
                db_path.unlink()
        except PermissionError:
            pass

    def test_auto_migration(self, temp_files):
        """Should automatically migrate JSON to SQLite on first use."""
        json_path, db_path = temp_files

        from data_access.repositories.customer_repository import LegacyJSONCustomerRepository

        repo = LegacyJSONCustomerRepository(db_path, json_path)

        # Should have migrated 3 customers
        assert repo.count() == 3

        # Verify data migrated correctly
        mcds = repo.find_by_name("McDonald's", OrderType.WORLDLINK)
        assert mcds is not None
        assert mcds.customer_id == "MCDS"

        toyota = repo.find_by_name("Toyota", OrderType.TCAA)
        assert toyota is not None
        assert toyota.customer_id == "TOYO"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
