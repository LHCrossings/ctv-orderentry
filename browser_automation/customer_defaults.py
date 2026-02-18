"""
Customer Defaults - Template-Based Contract Code & Description Generation

Every customer has their own default code and description templates stored in
customers.db. Templates use placeholders that get filled at runtime:

    {est}  → Estimate/order number
    {mkt3} → 3-letter market code (CVC, SFO, NYC, etc.)
    {mkt2} → 2-letter market short (CV, SF, NY, etc.)

Examples:
    Customer: Northern California Dealers Association (H&L)
    Code template:  "HL Toyota {est} {mkt2}"
    Desc template:  "Toyota {mkt3} Est {est}"

    With est=14080, market=CVC:
    Code:  "HL Toyota 14080 CV"
    Desc:  "Toyota CVC Est 14080"

Schema additions to customers table:
    default_code_template TEXT
    default_desc_template TEXT
"""

import sqlite3
from pathlib import Path
from typing import Optional


# ═══════════════════════════════════════════════════════════════════════════════
# MARKET SHORT CODES (Universal)
# ═══════════════════════════════════════════════════════════════════════════════

MARKET_3_TO_2: dict[str, str] = {
    "CVC": "CV",
    "SFO": "SF",
    "NYC": "NY",
    "LAX": "LA",
    "SEA": "SE",
    "HOU": "HO",
    "CMP": "CM",
    "WDC": "DC",
    "MMT": "MM",
    "DAL": "DL",
}

# Default database path (relative to project root)
DEFAULT_DB_PATH = Path("data") / "customers.db"


# ═══════════════════════════════════════════════════════════════════════════════
# SCHEMA MIGRATION
# ═══════════════════════════════════════════════════════════════════════════════

def ensure_template_columns(db_path: Path = DEFAULT_DB_PATH) -> None:
    """
    Add template columns to customers table if they don't exist.
    Safe to call multiple times (idempotent).
    """
    if not db_path.exists():
        return

    try:
        with sqlite3.connect(str(db_path)) as conn:
            # Check if columns exist
            cursor = conn.execute("PRAGMA table_info(customers)")
            columns = {row[1] for row in cursor.fetchall()}

            if "default_code_template" not in columns:
                conn.execute(
                    "ALTER TABLE customers ADD COLUMN default_code_template TEXT"
                )
                print("[CUSTOMER DB] ✓ Added default_code_template column")

            if "default_desc_template" not in columns:
                conn.execute(
                    "ALTER TABLE customers ADD COLUMN default_desc_template TEXT"
                )
                print("[CUSTOMER DB] ✓ Added default_desc_template column")
    except Exception as e:
        print(f"[CUSTOMER DB] ⚠ Migration error (non-fatal): {e}")


# ═══════════════════════════════════════════════════════════════════════════════
# TEMPLATE RESOLUTION
# ═══════════════════════════════════════════════════════════════════════════════

def resolve_defaults(
    customer_name: str,
    order_type: str,
    estimate_number: str,
    market: str,
    db_path: Path = DEFAULT_DB_PATH,
) -> tuple[Optional[str], Optional[str]]:
    """
    Resolve default code and description from customer templates.

    Looks up the customer's stored templates, substitutes placeholders,
    and returns the populated values.

    Args:
        customer_name: Customer name as it appears in the DB
        order_type: Agency identifier (e.g., "hl", "tcaa", "opad")
        estimate_number: Estimate/order number from the PDF
        market: 3-letter market code (e.g., "CVC", "SFO", "NYC")
        db_path: Path to customers.db

    Returns:
        Tuple of (code, description). Either may be None if no template found.
    """
    templates = _get_templates(customer_name, order_type, db_path)
    if templates is None:
        return (None, None)

    code_template, desc_template = templates
    if code_template is None and desc_template is None:
        return (None, None)

    mkt3 = market.upper()
    mkt2 = MARKET_3_TO_2.get(mkt3, mkt3[:2])

    replacements = {
        "{est}": str(estimate_number),
        "{mkt3}": mkt3,
        "{mkt2}": mkt2,
    }

    code = _apply_template(code_template, replacements) if code_template else None
    desc = _apply_template(desc_template, replacements) if desc_template else None

    return (code, desc)


def _apply_template(template: str, replacements: dict[str, str]) -> str:
    """Apply replacements to a template string."""
    result = template
    for placeholder, value in replacements.items():
        result = result.replace(placeholder, value)
    return result


def _get_templates(
    customer_name: str,
    order_type: str,
    db_path: Path = DEFAULT_DB_PATH,
) -> Optional[tuple[Optional[str], Optional[str]]]:
    """
    Look up customer templates from database.

    Tries exact match first, then fuzzy containment match.

    Returns:
        (code_template, desc_template) or None if customer not found
    """
    if not db_path.exists():
        return None

    try:
        with sqlite3.connect(str(db_path)) as conn:
            # Exact match
            cursor = conn.execute(
                """SELECT default_code_template, default_desc_template
                   FROM customers
                   WHERE customer_name = ? AND order_type = ?""",
                (customer_name, order_type),
            )
            row = cursor.fetchone()
            if row:
                return (row[0], row[1])

            # Fuzzy: containment match
            cursor = conn.execute(
                """SELECT customer_name, default_code_template, default_desc_template
                   FROM customers
                   WHERE order_type = ?""",
                (order_type,),
            )
            for db_name, code_tmpl, desc_tmpl in cursor.fetchall():
                if (db_name.lower() in customer_name.lower()
                        or customer_name.lower() in db_name.lower()):
                    return (code_tmpl, desc_tmpl)

    except Exception as e:
        print(f"[CUSTOMER DB] ⚠ Template lookup error: {e}")

    return None


# ═══════════════════════════════════════════════════════════════════════════════
# TEMPLATE STORAGE
# ═══════════════════════════════════════════════════════════════════════════════

def save_templates(
    customer_name: str,
    order_type: str,
    code_template: str,
    desc_template: str,
    db_path: Path = DEFAULT_DB_PATH,
) -> bool:
    """
    Save or update default templates for a customer.

    Args:
        customer_name: Customer name
        order_type: Agency identifier
        code_template: Template with {est}, {mkt2}, {mkt3} placeholders
        desc_template: Template with {est}, {mkt2}, {mkt3} placeholders
        db_path: Path to customers.db

    Returns:
        True if saved successfully
    """
    if not db_path.exists():
        print(f"[CUSTOMER DB] ⚠ Database not found at {db_path}")
        return False

    try:
        with sqlite3.connect(str(db_path)) as conn:
            conn.execute(
                """UPDATE customers
                   SET default_code_template = ?, default_desc_template = ?
                   WHERE customer_name = ? AND order_type = ?""",
                (code_template, desc_template, customer_name, order_type),
            )
            if conn.total_changes > 0:
                print(f"[CUSTOMER DB] ✓ Saved templates for {customer_name}")
                return True
            else:
                print(f"[CUSTOMER DB] ⚠ Customer not found: {customer_name} ({order_type})")
                return False
    except Exception as e:
        print(f"[CUSTOMER DB] ⚠ Could not save templates: {e}")
        return False


# ═══════════════════════════════════════════════════════════════════════════════
# FIRST-TIME TEMPLATE CAPTURE
# ═══════════════════════════════════════════════════════════════════════════════

def prompt_and_save_templates(
    customer_name: str,
    order_type: str,
    sample_code: str,
    sample_desc: str,
    estimate_number: str,
    market: str,
    db_path: Path = DEFAULT_DB_PATH,
) -> tuple[str, str]:
    """
    For first-time customers without templates: show the user what they entered,
    infer the template, confirm, and save.

    The function reverse-engineers templates from the user's actual input by
    replacing the estimate number and market codes with placeholders.

    Args:
        customer_name: Customer name
        order_type: Agency identifier
        sample_code: The actual code the user typed (e.g., "HL Toyota 14080 CV")
        sample_desc: The actual description the user typed (e.g., "Toyota CVC Est 14080")
        estimate_number: The estimate number used
        market: 3-letter market code used
        db_path: Path to customers.db

    Returns:
        The (code_template, desc_template) that were saved
    """
    mkt3 = market.upper()
    mkt2 = MARKET_3_TO_2.get(mkt3, mkt3[:2])

    # Reverse-engineer: replace concrete values with placeholders
    code_template = sample_code.replace(estimate_number, "{est}")
    code_template = code_template.replace(mkt2, "{mkt2}")
    code_template = code_template.replace(mkt3, "{mkt3}")

    desc_template = sample_desc.replace(estimate_number, "{est}")
    desc_template = desc_template.replace(mkt3, "{mkt3}")
    desc_template = desc_template.replace(mkt2, "{mkt2}")

    print(f"\n{'='*60}")
    print(f"SAVE DEFAULT TEMPLATES FOR: {customer_name}")
    print(f"{'='*60}")
    print(f"  Code template:  {code_template}")
    print(f"  Desc template:  {desc_template}")
    print(f"{'='*60}")

    confirm = input("Save these as defaults for this customer? (Y/n): ").strip().lower()
    if confirm in ("", "y", "yes"):
        save_templates(customer_name, order_type, code_template, desc_template, db_path)
    else:
        # Let user type templates manually
        print("\nEnter templates using {est}, {mkt2}, {mkt3} as placeholders:")
        code_template = input(f"  Code template [{code_template}]: ").strip() or code_template
        desc_template = input(f"  Desc template [{desc_template}]: ").strip() or desc_template
        save_templates(customer_name, order_type, code_template, desc_template, db_path)

    return (code_template, desc_template)
