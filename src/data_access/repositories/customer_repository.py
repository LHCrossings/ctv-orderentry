"""
Customer Repository - Data access layer for customer information.

This repository handles all database operations related to customers,
including the self-learning customer database that maps customer names
to their Etere customer IDs.

Extended fields support storing client defaults:
    - abbreviation: Short code for contract codes (e.g., "SRCF")
    - default_market: Default market code (e.g., "CVC"), None for any
    - billing_type: "agency" or "client"
    - separation_customer/event/order: Default separation intervals
"""

import json
import sys
from pathlib import Path

# Add src to path for imports
_src_path = Path(__file__).parent.parent.parent
if str(_src_path) not in sys.path:
    sys.path.insert(0, str(_src_path))

from domain.entities import Customer
from domain.enums import OrderType

# The 14 columns _row_to_customer expects, in order.
_COLS = ("customer_id, customer_name, order_type, abbreviation, default_market, "
         "billing_type, separation_customer, separation_event, separation_order, "
         "code_name, description_name, include_market_in_code, auto_aircheck, owner")
_TABLE = "dbo.CTV_Customers"


def _connect():
    """Short-lived connection to the shared Etere SQL Server."""
    from browser_automation.etere_direct_client import connect
    return connect()


class CustomerRepository:
    """
    Repository for customer data storage and retrieval.

    Backed by the shared dbo.CTV_Customers table on the Etere SQL Server (was a
    per-checkout SQLite `data/customers.db`, which drifted between machines). The
    public API is unchanged, so callers are unaffected. Self-learning: new
    customers are upserted via save() as they're encountered. The table itself is
    created/migrated by scripts/setup_ctv_customers_table.py.
    """

    def __init__(self, db_path: Path | str = None):
        """`db_path` is accepted for backward compatibility but ignored — the
        store is the shared SQL table, not a local file."""
        self._db_path = db_path  # kept for compat; unused

    def find_by_name(
        self,
        customer_name: str,
        order_type: OrderType
    ) -> Customer | None:
        """Find customer by exact name (case-insensitive) for a specific order type."""
        normalized_name = customer_name.strip().lower()
        with _connect() as conn:
            cur = conn.cursor()
            cur.execute(
                f"SELECT {_COLS} FROM {_TABLE} WHERE LOWER(customer_name) = %s AND order_type = %s",
                (normalized_name, order_type.value),
            )
            row = cur.fetchone()
        return self._row_to_customer(row) if row else None

    def find_by_name_any_type(
        self,
        customer_name: str
    ) -> Customer | None:
        """
        Find customer by name across ALL order types (exact first, then partial
        containment). Used for Charmaine-style orders where the same client may
        have been entered under a different order type previously.
        """
        normalized_name = customer_name.strip().lower()
        with _connect() as conn:
            cur = conn.cursor()
            cur.execute(
                f"SELECT TOP 1 {_COLS} FROM {_TABLE} WHERE LOWER(customer_name) = %s",
                (normalized_name,),
            )
            row = cur.fetchone()
            if row:
                return self._row_to_customer(row)

            cur.execute(f"SELECT {_COLS} FROM {_TABLE}")
            all_rows = cur.fetchall()
        for row in all_rows:
            row_name = (row[1] or "").lower()
            if normalized_name in row_name or row_name in normalized_name:
                return self._row_to_customer(row)
        return None

    def find_by_name_fuzzy(
        self,
        customer_name: str,
        order_type: OrderType
    ) -> Customer | None:
        """Alias for find_by_fuzzy_match (used by automation modules)."""
        return self.find_by_fuzzy_match(customer_name, order_type)

    def find_by_fuzzy_match(
        self,
        customer_name: str,
        order_type: OrderType
    ) -> Customer | None:
        """
        Find customer using fuzzy matching for specific order type.

        Tries various matching strategies:
        1. Exact match (case-insensitive)
        2. Contains match
        3. Contained by match

        Args:
            customer_name: Customer name to search for
            order_type: Order type context

        Returns:
            Best matching customer, or None if no match found
        """
        # Try exact match first
        exact_match = self.find_by_name(customer_name, order_type)
        if exact_match:
            return exact_match

        # Get all customers for this order type
        all_customers = self.list_by_order_type(order_type)

        if not all_customers:
            return None

        # Try fuzzy matching
        for customer in all_customers:
            if customer.matches_name(customer_name):
                return customer

        return None

    def list_by_order_type(self, order_type: OrderType) -> list[Customer]:
        """
        Get all customers for a specific order type.

        Args:
            order_type: Order type to filter by

        Returns:
            List of customers for this order type
        """
        with _connect() as conn:
            cur = conn.cursor()
            cur.execute(
                f"SELECT {_COLS} FROM {_TABLE} WHERE order_type = %s ORDER BY customer_name",
                (order_type.value,),
            )
            return [self._row_to_customer(row) for row in cur.fetchall()]

    def save(self, customer: Customer) -> None:
        """Upsert a customer (keyed on customer_name + order_type)."""
        vals = (
            customer.customer_id,
            customer.abbreviation,
            customer.default_market,
            customer.billing_type,
            customer.separation_customer,
            customer.separation_event,
            customer.separation_order,
            customer.code_name,
            customer.description_name,
            1 if customer.include_market_in_code else 0,
            1 if customer.auto_aircheck else 0,
            customer.owner,
        )
        with _connect() as conn:
            cur = conn.cursor()
            cur.execute(
                f"UPDATE {_TABLE} SET customer_id=%s, abbreviation=%s, default_market=%s, "
                "billing_type=%s, separation_customer=%s, separation_event=%s, separation_order=%s, "
                "code_name=%s, description_name=%s, include_market_in_code=%s, auto_aircheck=%s, "
                "owner=%s, updated_at=GETDATE() WHERE customer_name=%s AND order_type=%s",
                (*vals, customer.customer_name, customer.order_type.value),
            )
            if cur.rowcount == 0:
                cur.execute(
                    f"INSERT INTO {_TABLE} (customer_id, abbreviation, default_market, billing_type, "
                    "separation_customer, separation_event, separation_order, code_name, "
                    "description_name, include_market_in_code, auto_aircheck, owner, "
                    "customer_name, order_type) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (*vals, customer.customer_name, customer.order_type.value),
                )
            conn.commit()

    def delete(self, customer_name: str, order_type: OrderType) -> bool:
        """Delete a customer; True if a row was removed."""
        with _connect() as conn:
            cur = conn.cursor()
            cur.execute(
                f"DELETE FROM {_TABLE} WHERE LOWER(customer_name) = %s AND order_type = %s",
                (customer_name.strip().lower(), order_type.value),
            )
            deleted = cur.rowcount > 0
            conn.commit()
            return deleted

    def count(self) -> int:
        """Total number of customers."""
        with _connect() as conn:
            cur = conn.cursor()
            cur.execute(f"SELECT COUNT(*) FROM {_TABLE}")
            return cur.fetchone()[0]

    def list_all(self) -> list[Customer]:
        """All customers, ordered by name."""
        with _connect() as conn:
            cur = conn.cursor()
            cur.execute(f"SELECT {_COLS} FROM {_TABLE} ORDER BY customer_name")
            return [self._row_to_customer(row) for row in cur.fetchall()]

    @staticmethod
    def _row_to_customer(row: tuple) -> Customer:
        """
        Map a database row to a Customer entity.

        Handles both old-format rows (3 columns) and new-format rows (9 columns)
        for backward compatibility during migration.

        Args:
            row: Database row tuple

        Returns:
            Customer entity
        """
        if len(row) >= 14:
            return Customer(
                customer_id=row[0],
                customer_name=row[1],
                order_type=OrderType(row[2]),
                abbreviation=row[3] or "",
                default_market=row[4],
                billing_type=row[5] or "agency",
                separation_customer=row[6] if row[6] is not None else 15,
                separation_event=row[7] if row[7] is not None else 0,
                separation_order=row[8] if row[8] is not None else 0,
                code_name=row[9] or "",
                description_name=row[10] or "",
                include_market_in_code=bool(row[11]),
                auto_aircheck=bool(row[12]),
                owner=row[13] or "",
            )
        if len(row) >= 13:
            return Customer(
                customer_id=row[0],
                customer_name=row[1],
                order_type=OrderType(row[2]),
                abbreviation=row[3] or "",
                default_market=row[4],
                billing_type=row[5] or "agency",
                separation_customer=row[6] if row[6] is not None else 15,
                separation_event=row[7] if row[7] is not None else 0,
                separation_order=row[8] if row[8] is not None else 0,
                code_name=row[9] or "",
                description_name=row[10] or "",
                include_market_in_code=bool(row[11]),
                auto_aircheck=bool(row[12]),
            )
        if len(row) >= 12:
            return Customer(
                customer_id=row[0],
                customer_name=row[1],
                order_type=OrderType(row[2]),
                abbreviation=row[3] or "",
                default_market=row[4],
                billing_type=row[5] or "agency",
                separation_customer=row[6] if row[6] is not None else 15,
                separation_event=row[7] if row[7] is not None else 0,
                separation_order=row[8] if row[8] is not None else 0,
                code_name=row[9] or "",
                description_name=row[10] or "",
                include_market_in_code=bool(row[11]),
            )
        elif len(row) >= 9:
            return Customer(
                customer_id=row[0],
                customer_name=row[1],
                order_type=OrderType(row[2]),
                abbreviation=row[3] or "",
                default_market=row[4],
                billing_type=row[5] or "agency",
                separation_customer=row[6] if row[6] is not None else 15,
                separation_event=row[7] if row[7] is not None else 0,
                separation_order=row[8] if row[8] is not None else 0,
            )
        else:
            # Old-format row (backward compatibility)
            return Customer(
                customer_id=row[0],
                customer_name=row[1],
                order_type=OrderType(row[2]),
            )


class LegacyJSONCustomerRepository(CustomerRepository):
    """
    Backward-compatible repository that reads from JSON files.

    This exists for migration from the old JSON-based customer database
    to the new SQLite-based one. It can import JSON data into SQLite.
    """

    def __init__(self, db_path: Path | str, json_path: Path | str | None = None):
        """
        Initialize with optional JSON file for migration.

        Args:
            db_path: Path to SQLite database
            json_path: Optional path to legacy JSON file
        """
        super().__init__(db_path)
        self._json_path = Path(json_path) if json_path else None

        # Auto-migrate if JSON exists and SQLite is empty
        if self._json_path and self._json_path.exists() and self.count() == 0:
            self._migrate_from_json()

    def _migrate_from_json(self) -> None:
        """Migrate customer data from JSON to SQLite."""
        if not self._json_path or not self._json_path.exists():
            return

        try:
            with open(self._json_path, 'r') as f:
                data = json.load(f)

            # JSON format: {"order_type": {"customer_name": "customer_id"}}
            migrated_count = 0

            for order_type_str, customers_dict in data.items():
                try:
                    order_type = OrderType(order_type_str)
                except ValueError:
                    continue  # Skip unknown order types

                for customer_name, customer_id in customers_dict.items():
                    customer = Customer(
                        customer_id=customer_id,
                        customer_name=customer_name,
                        order_type=order_type
                    )
                    self.save(customer)
                    migrated_count += 1

            print(f"[MIGRATION] Migrated {migrated_count} customers from JSON to SQLite")

        except Exception as e:
            print(f"[MIGRATION] Error migrating from JSON: {e}")


def create_customer_repository(db_path):
    """
    Factory function to create a fully configured CustomerRepository.

    Args:
        db_path: Path to SQLite database file

    Returns:
        Configured CustomerRepository instance
    """
    return CustomerRepository(db_path)
