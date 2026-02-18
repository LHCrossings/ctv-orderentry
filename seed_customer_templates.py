"""
Seed Customer Default Templates

Run this once to populate default_code_template and default_desc_template
for all known customers in customers.db.

These templates were previously hardcoded in each agency's automation file.
Now they live in the database so any customer can have custom defaults.

Template placeholders:
    {est}  → Estimate/order number
    {mkt3} → 3-letter market code (CVC, SFO, NYC, etc.)
    {mkt2} → 2-letter market short (CV, SF, NY, etc.)

Usage:
    python seed_customer_templates.py
    python seed_customer_templates.py --db data/customers.db
    python seed_customer_templates.py --list
"""

import sqlite3
import sys
from pathlib import Path


# ═══════════════════════════════════════════════════════════════════════════════
# KNOWN CUSTOMER TEMPLATES
# ═══════════════════════════════════════════════════════════════════════════════
#
# Gathered from existing automation files' get_*_defaults() functions.
# Format: (customer_name, order_type, code_template, desc_template)
#

KNOWN_TEMPLATES = [
    # ── H&L Partners ──
    # Source: hl_automation.py get_hl_defaults()
    # Code: "HL Toyota 14080 CV" → "HL Toyota {est} {mkt2}"
    # Desc: "Toyota CVC Est 14080" → "Toyota {mkt3} Est {est}"
    (
        "Northern California Dealers Association",
        "hl",
        "HL Toyota {est} {mkt2}",
        "Toyota {mkt3} Est {est}",
    ),

    # ── TCAA ──
    # Source: tcaa_automation.py - "TCAA Toyota {estimate_number}"
    # TCAA has one known customer: Toyota (Seattle)
    # Code: "TCAA Toyota 9709" → "TCAA Toyota {est}"
    # Desc: "Toyota SEA Est 9709" → "Toyota {mkt3} Est {est}"
    (
        "Toyota Motor Sales",
        "tcaa",
        "TCAA Toyota {est}",
        "Toyota {mkt3} Est {est}",
    ),

    # ── opAD ──
    # Source: opad_automation.py get_opad_defaults()
    # Code: "opAD NYSDOH 2824" → "opAD NYSDOH {est}"
    # Desc: "NYSDOH NYC Est 2824" → "NYSDOH {mkt3} Est {est}"
    (
        "NYS Department of Health",
        "opad",
        "opAD NYSDOH {est}",
        "NYSDOH {mkt3} Est {est}",
    ),

    # ── Daviselen ──
    # Source: daviselen_parser.py get_defaults()
    # Toyota: Code "Daviselen Toyota 175" → Desc "Toyota LAX Est 175"
    (
        "So. Cal Toyota Dlrs Adv Assoc",
        "daviselen",
        "Daviselen Toyota {est} {mkt2}",
        "Toyota {mkt3} Est {est}",
    ),
    # McDonald's variants
    (
        "McDonald's SoCal",
        "daviselen",
        "Daviselen McD {est} {mkt2}",
        "McDonald's {mkt3} Est {est}",
    ),
    (
        "Western Washington McDonald's",
        "daviselen",
        "Daviselen McD {est} {mkt2}",
        "McDonald's {mkt3} Est {est}",
    ),
    (
        "CAPITAL BUSINESS UNIT",
        "daviselen",
        "Daviselen McD {est} {mkt2}",
        "McDonald's {mkt3} Est {est}",
    ),

    # ── Misfit ──
    # Source: misfit_automation.py - "Misfit CACC {YYMM}"
    # Code: "Misfit CACC 2602" → "Misfit CACC {est}"
    # Desc: "CA Community Colleges 2602-2606" (complex, may need manual)
    (
        "California Community Colleges",
        "misfit",
        "Misfit CACC {est}",
        "CA Community Colleges {est}",
    ),

    # ── Sagent ──
    # Source: sagent_automation.py
    # Code: "Sagent Cal Fire 202" → "Sagent Cal Fire {est}"
    # Desc: "Cal Fire 2026 Cal Fire Fourth of July Est 202"
    # Note: Desc includes campaign name which varies, so template is partial
    (
        "CAL FIRE",
        "sagent",
        "Sagent Cal Fire {est}",
        "Cal Fire Est {est}",
    ),

    # ── WorldLink / Tatari ──
    # Source: worldlink automation - "WL {agency_first} {tracking}"
    # WL has many clients - these are common ones
    # Templates use tracking number not estimate, but {est} works as placeholder
    # NOTE: WorldLink uses tracking numbers, not estimate numbers.
    # The {est} placeholder maps to tracking number for WL orders.
]


def seed_templates(db_path: Path) -> None:
    """
    Add default templates to all known customers.
    Only updates rows where templates are currently NULL.
    """
    if not db_path.exists():
        print(f"✗ Database not found: {db_path}")
        print(f"  Run init_customers.py first to create the database.")
        return

    # Ensure columns exist
    try:
        from browser_automation.customer_defaults import ensure_template_columns
        ensure_template_columns(db_path)
    except ImportError:
        # Fallback: add columns manually
        with sqlite3.connect(str(db_path)) as conn:
            try:
                conn.execute("ALTER TABLE customers ADD COLUMN default_code_template TEXT")
            except sqlite3.OperationalError:
                pass  # Column already exists
            try:
                conn.execute("ALTER TABLE customers ADD COLUMN default_desc_template TEXT")
            except sqlite3.OperationalError:
                pass

    updated = 0
    skipped = 0
    not_found = 0

    with sqlite3.connect(str(db_path)) as conn:
        for name, order_type, code_tmpl, desc_tmpl in KNOWN_TEMPLATES:
            # Check if customer exists
            cursor = conn.execute(
                """SELECT customer_id, default_code_template, default_desc_template
                   FROM customers
                   WHERE customer_name = ? AND order_type = ?""",
                (name, order_type),
            )
            row = cursor.fetchone()

            if not row:
                # Try fuzzy match
                cursor = conn.execute(
                    """SELECT customer_name, customer_id, default_code_template, default_desc_template
                       FROM customers WHERE order_type = ?""",
                    (order_type,),
                )
                found = False
                for db_name, db_id, existing_code, existing_desc in cursor.fetchall():
                    if (db_name.lower() in name.lower()
                            or name.lower() in db_name.lower()):
                        # Fuzzy match found
                        if existing_code is None and existing_desc is None:
                            conn.execute(
                                """UPDATE customers
                                   SET default_code_template = ?, default_desc_template = ?
                                   WHERE customer_name = ? AND order_type = ?""",
                                (code_tmpl, desc_tmpl, db_name, order_type),
                            )
                            print(f"  ✓ {order_type:10s} | {db_name} (fuzzy) → code='{code_tmpl}' desc='{desc_tmpl}'")
                            updated += 1
                        else:
                            print(f"  ⊘ {order_type:10s} | {db_name} (already has templates)")
                            skipped += 1
                        found = True
                        break

                if not found:
                    print(f"  ✗ {order_type:10s} | {name} (not in database)")
                    not_found += 1
                continue

            cust_id, existing_code, existing_desc = row

            if existing_code is not None or existing_desc is not None:
                print(f"  ⊘ {order_type:10s} | {name} (already has templates)")
                skipped += 1
                continue

            conn.execute(
                """UPDATE customers
                   SET default_code_template = ?, default_desc_template = ?
                   WHERE customer_name = ? AND order_type = ?""",
                (code_tmpl, desc_tmpl, name, order_type),
            )
            print(f"  ✓ {order_type:10s} | {name} → code='{code_tmpl}' desc='{desc_tmpl}'")
            updated += 1

    print(f"\nDone: {updated} updated, {skipped} skipped (already set), {not_found} not in DB")


def list_templates(db_path: Path) -> None:
    """Show all customers and their templates."""
    if not db_path.exists():
        print(f"✗ Database not found: {db_path}")
        return

    with sqlite3.connect(str(db_path)) as conn:
        cursor = conn.execute(
            """SELECT customer_id, customer_name, order_type,
                      default_code_template, default_desc_template
               FROM customers
               ORDER BY order_type, customer_name"""
        )
        rows = cursor.fetchall()

    if not rows:
        print("Database is empty")
        return

    print(f"\n{'='*100}")
    print(f"{'ID':<6} {'Agency':<12} {'Customer':<35} {'Code Template':<30} {'Desc Template'}")
    print(f"{'='*100}")

    for cust_id, name, order_type, code_tmpl, desc_tmpl in rows:
        code_display = code_tmpl or "(none)"
        desc_display = desc_tmpl or "(none)"
        print(f"{cust_id:<6} {order_type:<12} {name:<35} {code_display:<30} {desc_display}")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Seed customer default templates")
    parser.add_argument("--db", type=Path, default=Path("data/customers.db"),
                        help="Path to customers.db")
    parser.add_argument("--list", action="store_true", help="List all templates")
    args = parser.parse_args()

    if args.list:
        list_templates(args.db)
    else:
        print(f"Seeding templates in: {args.db}\n")
        seed_templates(args.db)
        print()
        list_templates(args.db)
