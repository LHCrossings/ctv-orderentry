"""
Customer database management tool.

List, edit, and delete customer records.

Usage:
    python scripts/manage_customers.py
"""

import sqlite3
import sys
from pathlib import Path

DB_PATH = Path("data") / "customers.db"

EDITABLE_FIELDS = [
    ("separation_customer",     "Customer separation (minutes)"),
    ("separation_event",        "Event separation (minutes)"),
    ("separation_order",        "Order separation (minutes)"),
    ("code_name",               "Contract code name (e.g. 'Muckleshoot', 'TVC')"),
    ("description_name",        "Contract description name (e.g. 'Muckleshoot Casino')"),
    ("include_market_in_code",  "Include market in code/description? (1=yes, 0=no)"),
    ("abbreviation",            "Abbreviation"),
    ("default_market",          "Default market (SEA / SFO / CVC)"),
    ("billing_type",            "Billing type (agency / client)"),
]


def open_db():
    if not DB_PATH.exists():
        print(f"✗ Database not found: {DB_PATH}")
        sys.exit(1)
    return sqlite3.connect(DB_PATH)


def list_customers(conn, order_type_filter=None):
    query = "SELECT rowid, customer_name, order_type, customer_id, separation_customer, separation_event, separation_order, code_name, description_name, include_market_in_code FROM customers"
    params = ()
    if order_type_filter:
        query += " WHERE order_type = ?"
        params = (order_type_filter,)
    query += " ORDER BY order_type, customer_name"
    rows = conn.execute(query, params).fetchall()
    if not rows:
        print("  (no customers found)")
        return rows

    print(f"\n  {'#':<4} {'Name':<35} {'Type':<10} {'ID':<8} {'Sep (C/E/O)':<14} {'Code Name':<15} {'Mkt?'}")
    print("  " + "-"*95)
    for i, r in enumerate(rows, 1):
        rowid, name, otype, cid, sc, se, so, cn, dn, imk = r
        sep = f"{sc}/{se}/{so}"
        print(f"  [{i:<2}] {name:<35} {otype:<10} {cid:<8} {sep:<14} {(cn or ''):<15} {'yes' if imk else 'no'}")
    return rows


def edit_customer(conn, row):
    rowid = row[0]
    name = row[1]
    print(f"\nEditing: {name}")
    print("─" * 50)

    # Fetch current full record
    cur = conn.execute(
        "SELECT separation_customer, separation_event, separation_order, "
        "code_name, description_name, include_market_in_code, "
        "abbreviation, default_market, billing_type "
        "FROM customers WHERE rowid = ?", (rowid,)
    ).fetchone()

    values = dict(zip([f[0] for f in EDITABLE_FIELDS], cur))

    for col, label in EDITABLE_FIELDS:
        current = values[col]
        raw = input(f"  {label} [{current}]: ").strip()
        if raw:
            if col == "include_market_in_code":
                values[col] = 1 if raw in ("1", "y", "yes", "true") else 0
            elif col in ("separation_customer", "separation_event", "separation_order"):
                try:
                    values[col] = int(raw)
                except ValueError:
                    print(f"    ⚠ Invalid number, keeping {current}")
            else:
                values[col] = raw

    conn.execute(
        """
        UPDATE customers SET
            separation_customer = ?,
            separation_event = ?,
            separation_order = ?,
            code_name = ?,
            description_name = ?,
            include_market_in_code = ?,
            abbreviation = ?,
            default_market = ?,
            billing_type = ?
        WHERE rowid = ?
        """,
        (
            values["separation_customer"],
            values["separation_event"],
            values["separation_order"],
            values["code_name"],
            values["description_name"],
            values["include_market_in_code"],
            values["abbreviation"],
            values["default_market"],
            values["billing_type"],
            rowid,
        )
    )
    conn.commit()
    print(f"\n  ✓ Saved changes to {name}")


def delete_customer(conn, row):
    name = row[1]
    confirm = input(f"\n  Delete '{name}'? This cannot be undone. (yes/n): ").strip().lower()
    if confirm == "yes":
        conn.execute("DELETE FROM customers WHERE rowid = ?", (row[0],))
        conn.commit()
        print(f"  ✓ Deleted {name}")
    else:
        print("  Cancelled.")


def main():
    conn = open_db()

    print("\n" + "="*60)
    print("CUSTOMER DATABASE MANAGER")
    print("="*60)

    while True:
        print("\nFilter by order type (RPM / SAGENT / all) [all]: ", end="")
        otype = input().strip().upper() or None
        if otype == "ALL":
            otype = None

        rows = list_customers(conn, otype)
        if not rows:
            break

        print("\nEnter number to edit, 'd<number>' to delete, or Enter to quit: ", end="")
        choice = input().strip()

        if not choice:
            break

        delete_mode = choice.lower().startswith('d')
        num_str = choice[1:] if delete_mode else choice

        try:
            idx = int(num_str) - 1
            if not 0 <= idx < len(rows):
                print("  Invalid selection.")
                continue
        except ValueError:
            print("  Invalid selection.")
            continue

        if delete_mode:
            delete_customer(conn, rows[idx])
        else:
            edit_customer(conn, rows[idx])

    conn.close()
    print("\nDone.")


if __name__ == "__main__":
    main()
