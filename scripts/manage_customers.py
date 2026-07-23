"""
Customer database management tool — dbo.CTV_Customers on the Etere SQL Server.

List, edit, and delete customer records (shared across all environments).

Usage:
    uv run python scripts/manage_customers.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from browser_automation.etere_direct_client import connect

EDITABLE_FIELDS = [
    ("separation_customer",     "Customer separation (minutes)"),
    ("separation_event",        "Event separation (minutes)"),
    ("separation_order",        "Order separation (minutes)"),
    ("code_name",               "Contract code name (e.g. 'Muckleshoot', 'TVC')"),
    ("description_name",        "Contract description name (e.g. 'Muckleshoot Casino')"),
    ("include_market_in_code",  "Include market in code/description? (1=yes, 0=no)"),
    ("abbreviation",            "Abbreviation"),
    ("default_market",          "Default market (SEA / SFO / CVC)"),
    ("billing_type",            "Billing type (agency / direct)"),
    ("auto_aircheck",           "Auto-schedule airchecks after traffic assignment? (1=yes, 0=no)"),
]


def open_db():
    return connect()


def list_customers(conn, order_type_filter=None):
    cur = conn.cursor()
    query = ("SELECT customer_name, order_type, customer_id, separation_customer, "
             "separation_event, separation_order, code_name, description_name, "
             "include_market_in_code, auto_aircheck FROM dbo.CTV_Customers")
    params = ()
    if order_type_filter:
        query += " WHERE order_type = %s"
        params = (order_type_filter,)
    query += " ORDER BY order_type, customer_name"
    cur.execute(query, params)
    rows = cur.fetchall()
    if not rows:
        print("  (no customers found)")
        return rows

    print(f"\n  {'#':<4} {'Name':<35} {'Type':<10} {'ID':<8} {'Sep (C/E/O)':<14} {'Code Name':<15} {'Mkt?':<6} {'AC?'}")
    print("  " + "-" * 100)
    for i, r in enumerate(rows, 1):
        name, otype, cid, sc, se, so, cn, dn, imk, aac = r
        sep = f"{sc}/{se}/{so}"
        print(f"  [{i:<2}] {name:<35} {otype:<10} {str(cid):<8} {sep:<14} {(cn or ''):<15} {'yes' if imk else 'no':<6} {'yes' if aac else 'no'}")
    return rows


def edit_customer(conn, row):
    name, otype = row[0], row[1]
    print(f"\nEditing: {name}")
    print("─" * 50)

    cur = conn.cursor()
    cur.execute(
        "SELECT separation_customer, separation_event, separation_order, "
        "code_name, description_name, include_market_in_code, "
        "abbreviation, default_market, billing_type, auto_aircheck "
        "FROM dbo.CTV_Customers WHERE customer_name = %s AND order_type = %s",
        (name, otype),
    )
    current = cur.fetchone()
    values = dict(zip([f[0] for f in EDITABLE_FIELDS], current))

    for col, label in EDITABLE_FIELDS:
        cur_val = values[col]
        raw = input(f"  {label} [{cur_val}]: ").strip()
        if raw:
            if col in ("include_market_in_code", "auto_aircheck"):
                values[col] = 1 if raw in ("1", "y", "yes", "true") else 0
            elif col in ("separation_customer", "separation_event", "separation_order"):
                try:
                    values[col] = int(raw)
                except ValueError:
                    print(f"    ⚠ Invalid number, keeping {cur_val}")
            else:
                values[col] = raw

    cur.execute(
        "UPDATE dbo.CTV_Customers SET "
        "separation_customer=%s, separation_event=%s, separation_order=%s, "
        "code_name=%s, description_name=%s, include_market_in_code=%s, "
        "abbreviation=%s, default_market=%s, billing_type=%s, auto_aircheck=%s, "
        "updated_at=GETDATE() WHERE customer_name=%s AND order_type=%s",
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
            values["auto_aircheck"],
            name, otype,
        ),
    )
    conn.commit()
    print(f"\n  ✓ Saved changes to {name}")


def delete_customer(conn, row):
    name, otype = row[0], row[1]
    confirm = input(f"\n  Delete '{name}'? This cannot be undone. (yes/n): ").strip().lower()
    if confirm == "yes":
        cur = conn.cursor()
        cur.execute("DELETE FROM dbo.CTV_Customers WHERE customer_name = %s AND order_type = %s", (name, otype))
        conn.commit()
        print(f"  ✓ Deleted {name}")
    else:
        print("  Cancelled.")


def main():
    conn = open_db()

    print("\n" + "=" * 60)
    print("CUSTOMER DATABASE MANAGER  ·  dbo.CTV_Customers (shared)")
    print("=" * 60)

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
