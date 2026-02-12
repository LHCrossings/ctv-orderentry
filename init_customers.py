"""
Customer Database Initialization Script

This script initializes the customer database with known customers
for each agency type. Run this once to set up the self-learning database.

Usage:
    python init_customers.py
"""

import sqlite3
from pathlib import Path

# Known customers by agency type
KNOWN_CUSTOMERS = {
    # SAGENT customers
    'sagent': [
        ('175', 'CAL FIRE'),
        ('175', 'Cal Fire'),  # Variation
    ],
    
    # TCAA customers (if you have them)
    'tcaa': [
        ('75', 'TCAA Toyota'),
        ('75', 'Toyota'),
    ],
    
    # Misfit customers (add your known customers)
    'misfit': [
        # Add as: ('customer_id', 'customer_name'),
    ],
    
    # WorldLink customers
    'worldlink': [
        # Add as: ('customer_id', 'customer_name'),
    ],
    
    # Daviselen customers
    'daviselen': [
        # Add as: ('customer_id', 'customer_name'),
    ],
    
    # opAD customers
    'opad': [
        # Add as: ('customer_id', 'customer_name'),
    ],
    
    # RPM customers
    'rpm': [
        # Add as: ('customer_id', 'customer_name'),
    ],
    
    # H&L Partners customers
    'hl': [
        # Add as: ('customer_id', 'customer_name'),
    ],
    
    # Impact customers
    'impact': [
        # Add as: ('customer_id', 'customer_name'),
    ],
    
    # iGraphix customers
    'igraphix': [
        # Add as: ('customer_id', 'customer_name'),
    ],
    
    # Admerasia customers
    'admerasia': [
        # Add as: ('customer_id', 'customer_name'),
    ],
}


def init_database(db_path: str | Path):
    """
    Initialize customer database with known customers.
    
    Args:
        db_path: Path to customers.db file
    """
    db_path = Path(db_path)
    
    # Ensure database directory exists
    db_path.parent.mkdir(parents=True, exist_ok=True)
    
    # Connect to database
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    try:
        # Ensure table exists
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS customers (
                customer_id TEXT NOT NULL,
                customer_name TEXT NOT NULL,
                order_type TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (customer_name, order_type)
            )
        """)
        
        # Create indexes
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_customer_name 
            ON customers(customer_name)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_order_type 
            ON customers(order_type)
        """)
        
        # Add all known customers
        added_count = 0
        updated_count = 0
        
        for order_type, customers in KNOWN_CUSTOMERS.items():
            for customer_id, customer_name in customers:
                try:
                    cursor.execute(
                        """
                        INSERT INTO customers (customer_id, customer_name, order_type)
                        VALUES (?, ?, ?)
                        """,
                        (customer_id, customer_name, order_type)
                    )
                    added_count += 1
                    print(f"✓ Added: {customer_name} ({customer_id}) for {order_type}")
                except sqlite3.IntegrityError:
                    # Already exists, update it
                    cursor.execute(
                        """
                        UPDATE customers 
                        SET customer_id = ?
                        WHERE customer_name = ? AND order_type = ?
                        """,
                        (customer_id, customer_name, order_type)
                    )
                    updated_count += 1
                    print(f"⟳ Updated: {customer_name} ({customer_id}) for {order_type}")
        
        # Commit changes
        conn.commit()
        
        print(f"\n{'='*60}")
        print(f"Database initialization complete!")
        print(f"{'='*60}")
        print(f"Added: {added_count} customers")
        print(f"Updated: {updated_count} customers")
        print(f"Total in database: {added_count + updated_count}")
        
        # Show summary
        print(f"\n{'='*60}")
        print("Database Summary:")
        print(f"{'='*60}")
        
        cursor.execute("""
            SELECT order_type, COUNT(*) 
            FROM customers 
            GROUP BY order_type
            ORDER BY order_type
        """)
        
        for row in cursor.fetchall():
            print(f"  {row[0]:15s}: {row[1]:3d} customer(s)")
        
    finally:
        conn.close()


def verify_database(db_path: str | Path):
    """
    Verify database contents.
    
    Args:
        db_path: Path to customers.db file
    """
    db_path = Path(db_path)
    
    if not db_path.exists():
        print(f"✗ Database not found: {db_path}")
        return False
    
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    try:
        # Check CAL FIRE specifically
        cursor.execute("""
            SELECT customer_id, customer_name, order_type 
            FROM customers 
            WHERE LOWER(customer_name) LIKE '%cal fire%' OR customer_id = '175'
        """)
        
        rows = cursor.fetchall()
        
        if rows:
            print("\n✓ CAL FIRE verification:")
            for row in rows:
                print(f"  ID: {row[0]}, Name: {row[1]}, Type: {row[2]}")
            return True
        else:
            print("\n✗ CAL FIRE not found in database")
            return False
            
    finally:
        conn.close()


def list_all_customers(db_path: str | Path):
    """
    List all customers in database.
    
    Args:
        db_path: Path to customers.db file
    """
    db_path = Path(db_path)
    
    if not db_path.exists():
        print(f"✗ Database not found: {db_path}")
        return
    
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    try:
        cursor.execute("""
            SELECT customer_id, customer_name, order_type, created_at
            FROM customers
            ORDER BY order_type, customer_name
        """)
        
        rows = cursor.fetchall()
        
        if rows:
            print(f"\n{'='*80}")
            print(f"All Customers in Database ({len(rows)} total)")
            print(f"{'='*80}")
            print(f"{'ID':<8} {'Name':<30} {'Type':<15} {'Created':<20}")
            print(f"{'-'*8} {'-'*30} {'-'*15} {'-'*20}")
            
            for row in rows:
                print(f"{row[0]:<8} {row[1]:<30} {row[2]:<15} {row[3] or 'N/A':<20}")
        else:
            print("\nDatabase is empty")
            
    finally:
        conn.close()


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(
        description="Initialize customer database with known customers"
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=Path("data/customers.db"),
        help="Path to customers.db file (default: data/customers.db)"
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="List all customers in database"
    )
    parser.add_argument(
        "--verify",
        action="store_true",
        help="Verify database contents"
    )
    
    args = parser.parse_args()
    
    if args.list:
        list_all_customers(args.db)
    elif args.verify:
        verify_database(args.db)
    else:
        # Initialize database
        print(f"Initializing database: {args.db}")
        print()
        init_database(args.db)
        print()
        verify_database(args.db)
