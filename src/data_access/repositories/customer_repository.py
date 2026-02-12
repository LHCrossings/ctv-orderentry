"""
Customer Repository - Data access layer for customer information.

This repository handles all database operations related to customers,
including the self-learning customer database that maps customer names
to their Etere customer IDs.
"""

import json
import sqlite3
from pathlib import Path
from typing import Protocol
import sys

# Add src to path for imports
_src_path = Path(__file__).parent.parent.parent
if str(_src_path) not in sys.path:
    sys.path.insert(0, str(_src_path))

from domain.entities import Customer
from domain.enums import OrderType


class CustomerRepository:
    """
    Repository for customer data storage and retrieval.
    
    Uses SQLite for persistent storage of customer mappings.
    The database is self-learning - new customers are added as they're encountered.
    """
    
    def __init__(self, db_path: Path | str):
        """
        Initialize repository with database path.
        
        Args:
            db_path: Path to SQLite database file or ":memory:" for in-memory DB
        """
        if str(db_path) == ":memory:":
            self._db_path = ":memory:"
        else:
            self._db_path = Path(db_path)
        self._ensure_database_exists()
    
    def _ensure_database_exists(self) -> None:
        """Create database and tables if they don't exist."""
        # Only create parent directory for file-based databases
        if self._db_path != ":memory:":
            Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        
        conn = sqlite3.connect(self._db_path)
        try:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS customers (
                    customer_id TEXT NOT NULL,
                    customer_name TEXT NOT NULL,
                    order_type TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (customer_name, order_type)
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_customer_name 
                ON customers(customer_name)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_order_type 
                ON customers(order_type)
            """)
            conn.commit()
        finally:
            conn.close()
    
    def find_by_name(
        self,
        customer_name: str,
        order_type: OrderType
    ) -> Customer | None:
        """
        Find customer by exact name match for specific order type.
        
        Args:
            customer_name: Customer name to search for
            order_type: Order type context
            
        Returns:
            Customer if found, None otherwise
        """
        # Normalize name for comparison
        normalized_name = customer_name.strip().lower()
        
        conn = sqlite3.connect(self._db_path)
        try:
            cursor = conn.execute(
                """
                SELECT customer_id, customer_name, order_type
                FROM customers
                WHERE LOWER(customer_name) = ? AND order_type = ?
                """,
                (normalized_name, order_type.value)
            )
            row = cursor.fetchone()
            
            if row:
                return Customer(
                    customer_id=row[0],
                    customer_name=row[1],
                    order_type=OrderType(row[2])
                )
            
            return None
        finally:
            conn.close()
    
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
        normalized_search = customer_name.strip().lower()
        
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
        with sqlite3.connect(self._db_path) as conn:
            cursor = conn.execute(
                """
                SELECT customer_id, customer_name, order_type
                FROM customers
                WHERE order_type = ?
                ORDER BY customer_name
                """,
                (order_type.value,)
            )
            
            return [
                Customer(
                    customer_id=row[0],
                    customer_name=row[1],
                    order_type=OrderType(row[2])
                )
                for row in cursor.fetchall()
            ]
    
    def save(self, customer: Customer) -> None:
        """
        Save or update customer in database.
        
        Args:
            customer: Customer to save
        """
        with sqlite3.connect(self._db_path) as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO customers 
                (customer_id, customer_name, order_type)
                VALUES (?, ?, ?)
                """,
                (
                    customer.customer_id,
                    customer.customer_name,
                    customer.order_type.value
                )
            )
            conn.commit()
    
    def delete(self, customer_name: str, order_type: OrderType) -> bool:
        """
        Delete customer from database.
        
        Args:
            customer_name: Name of customer to delete
            order_type: Order type context
            
        Returns:
            True if customer was deleted, False if not found
        """
        with sqlite3.connect(self._db_path) as conn:
            cursor = conn.execute(
                """
                DELETE FROM customers
                WHERE LOWER(customer_name) = ? AND order_type = ?
                """,
                (customer_name.strip().lower(), order_type.value)
            )
            conn.commit()
            return cursor.rowcount > 0
    
    def count(self) -> int:
        """
        Get total number of customers in database.
        
        Returns:
            Total customer count
        """
        with sqlite3.connect(self._db_path) as conn:
            cursor = conn.execute("SELECT COUNT(*) FROM customers")
            return cursor.fetchone()[0]
    
    def list_all(self) -> list[Customer]:
        """
        Get all customers from database.
        
        Returns:
            List of all customers, ordered by name
        """
        with sqlite3.connect(self._db_path) as conn:
            cursor = conn.execute(
                """
                SELECT customer_id, customer_name, order_type
                FROM customers
                ORDER BY customer_name
                """
            )
            
            return [
                Customer(
                    customer_id=row[0],
                    customer_name=row[1],
                    order_type=OrderType(row[2])
                )
                for row in cursor.fetchall()
            ]


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
