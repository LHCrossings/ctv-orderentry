"""
One-time migration: set code_name, description_name, include_market_in_code
on existing RPM customer records.

Run once from the project root:
    python scripts/migrate_rpm_customers.py
"""

import sqlite3
from pathlib import Path

DB_PATH = Path("data") / "customers.db"

UPDATES = [
    {
        "customer_name": "Muckleshoot Casino Resort",
        "code_name": "Muckleshoot",
        "description_name": "Muckleshoot Casino",
        "include_market_in_code": 0,
    },
    {
        "customer_name": "Thunder Valley Casino Resort",
        "code_name": "TVC",
        "description_name": "Thunder Valley Casino",
        "include_market_in_code": 1,
    },
]


def main():
    if not DB_PATH.exists():
        print(f"✗ Database not found: {DB_PATH}")
        return

    conn = sqlite3.connect(DB_PATH)
    try:
        # Ensure columns exist (safe no-op if already migrated)
        for col, typedef in [
            ("code_name", "TEXT DEFAULT ''"),
            ("description_name", "TEXT DEFAULT ''"),
            ("include_market_in_code", "INTEGER DEFAULT 0"),
        ]:
            try:
                conn.execute(f"ALTER TABLE customers ADD COLUMN {col} {typedef}")
            except sqlite3.OperationalError:
                pass  # already exists

        for rec in UPDATES:
            cursor = conn.execute(
                "SELECT customer_name FROM customers WHERE LOWER(customer_name) = ?",
                (rec["customer_name"].lower(),)
            )
            row = cursor.fetchone()
            if not row:
                print(f"  ⚠  Not found: {rec['customer_name']} — skipping")
                continue

            conn.execute(
                """
                UPDATE customers
                SET code_name = ?,
                    description_name = ?,
                    include_market_in_code = ?
                WHERE LOWER(customer_name) = ?
                """,
                (
                    rec["code_name"],
                    rec["description_name"],
                    rec["include_market_in_code"],
                    rec["customer_name"].lower(),
                )
            )
            mkt_flag = "include market" if rec["include_market_in_code"] else "no market"
            print(f"  ✓  {rec['customer_name']}: "
                  f"code_name={rec['code_name']!r}, "
                  f"description_name={rec['description_name']!r}, "
                  f"{mkt_flag}")

        conn.commit()
        print("\nMigration complete.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
