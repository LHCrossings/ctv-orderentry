"""
Tests for CustomerRepository - Data access layer testing.

These are integration tests that use a real SQLite database (in memory or temp file).
"""

import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).parent.parent.parent))  # repo root → browser_automation

from data_access.repositories.customer_repository import CustomerRepository
from domain.entities import Customer
from domain.enums import OrderType

# CustomerRepository is now backed by SQL Server (dbo.CTV_Customers), not SQLite.
# These integration tests run against an ISOLATED test table so they never touch
# production, and skip cleanly when no SQL Server is reachable (e.g. CI).
_TEST_TABLE = "dbo.CTV_Customers__pytest"
_TEST_TABLE_DDL = f"""CREATE TABLE {_TEST_TABLE} (
    customer_name NVARCHAR(200) NOT NULL, order_type NVARCHAR(50) NOT NULL,
    customer_id NVARCHAR(50) NOT NULL, abbreviation NVARCHAR(50) NOT NULL DEFAULT '',
    default_market NVARCHAR(10) NULL, billing_type NVARCHAR(20) NOT NULL DEFAULT 'agency',
    separation_customer INT NOT NULL DEFAULT 15, separation_event INT NOT NULL DEFAULT 0,
    separation_order INT NOT NULL DEFAULT 0, code_name NVARCHAR(100) NOT NULL DEFAULT '',
    description_name NVARCHAR(200) NOT NULL DEFAULT '', include_market_in_code INT NOT NULL DEFAULT 0,
    auto_aircheck INT NOT NULL DEFAULT 0, owner NVARCHAR(100) NOT NULL DEFAULT '',
    default_code_template NVARCHAR(200) NULL, default_desc_template NVARCHAR(200) NULL,
    created_at DATETIME NOT NULL DEFAULT GETDATE(), updated_at DATETIME NOT NULL DEFAULT GETDATE(),
    CONSTRAINT PK_CTV_Customers_pytest PRIMARY KEY (customer_name, order_type))"""


def _reset_test_table():
    """Create a fresh empty isolated test table; skip the test if no SQL Server."""
    try:
        from dotenv import load_dotenv
        load_dotenv(Path(__file__).resolve().parents[2] / ".env")
    except Exception:  # noqa: BLE001 - dotenv optional
        pass
    try:
        from browser_automation.etere_direct_client import connect
        conn = connect()
    except Exception as exc:  # noqa: BLE001 - no DB (e.g. CI) → skip, don't fail
        pytest.skip(f"SQL Server not available: {exc}")
    cur = conn.cursor()
    cur.execute(f"IF OBJECT_ID('{_TEST_TABLE}','U') IS NOT NULL DROP TABLE {_TEST_TABLE}")
    cur.execute(_TEST_TABLE_DDL)
    conn.commit()
    conn.close()


def _drop_test_table():
    try:
        from browser_automation.etere_direct_client import connect
        conn = connect()
        cur = conn.cursor()
        cur.execute(f"IF OBJECT_ID('{_TEST_TABLE}','U') IS NOT NULL DROP TABLE {_TEST_TABLE}")
        conn.commit()
        conn.close()
    except Exception:  # noqa: BLE001
        pass


class TestCustomerRepository:
    """Test customer repository with real database."""

    @pytest.fixture
    def repository(self):
        """Repository against a fresh, isolated SQL test table (skips if no DB)."""
        _reset_test_table()
        yield CustomerRepository(table=_TEST_TABLE)
        _drop_test_table()

    def test_database_creation(self, repository):
        """A fresh repository/table starts empty."""
        assert repository.count() == 0

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
    def json_file(self):
        """A sample legacy JSON file + a fresh isolated test table."""
        with tempfile.NamedTemporaryFile(mode='w', suffix=".json", delete=False) as json_f:
            json_path = Path(json_f.name)
            json_f.write("""{
                "worldlink": {
                    "McDonald's": "MCDS",
                    "Wendy's": "WNDY"
                },
                "tcaa": {
                    "Toyota": "TOYO"
                }
            }""")
        _reset_test_table()
        yield json_path
        _drop_test_table()
        try:
            json_path.unlink()
        except OSError:
            pass

    def test_auto_migration(self, json_file):
        """Should automatically migrate JSON into the (empty) table on first use."""
        from data_access.repositories.customer_repository import LegacyJSONCustomerRepository

        repo = LegacyJSONCustomerRepository(json_path=json_file, table=_TEST_TABLE)

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
