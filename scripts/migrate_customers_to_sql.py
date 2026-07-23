"""Migrate a local SQLite customers.db into the shared dbo.CTV_Customers table.

Run it once per source, most-authoritative first (Jumpbox), then the others:
  1. Jumpbox   : uv run python scripts/migrate_customers_to_sql.py <jumpbox customers.db> --apply
  2. Windows   : uv run python scripts/migrate_customers_to_sql.py <windows customers.db> --apply
  3. WSL       : uv run python scripts/migrate_customers_to_sql.py data/customers.db --apply

Behavior (keyed on the composite PK: customer_name + order_type):
  * MISSING in CTV_Customers  → inserted (with --apply).
  * IDENTICAL                 → skipped.
  * CONFLICT (present, differs)→ REPORTED, never overwritten — you decide. Re-run
    that source with --overwrite to make the SOURCE win for the conflicting rows.

Default (no flags) is a DRY RUN: it only reports what it would do. Add --apply to
insert gaps; add --overwrite to also update conflicting rows from this source.
"""
import argparse
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from browser_automation.etere_direct_client import connect

# Fields compared/migrated (created_at/updated_at are table-managed).
_FIELDS = [
    "customer_id", "abbreviation", "default_market", "billing_type",
    "separation_customer", "separation_event", "separation_order",
    "code_name", "description_name", "include_market_in_code",
    "auto_aircheck", "owner",
]
_INT_FIELDS = {"separation_customer", "separation_event", "separation_order",
               "include_market_in_code", "auto_aircheck"}


def _norm(field, val):
    """Canonical value for comparison (tolerate NULL/'' and int/str drift)."""
    if field in _INT_FIELDS:
        try:
            return int(val or 0)
        except (TypeError, ValueError):
            return 0
    return (str(val).strip() if val is not None else "")


def _read_sqlite(path):
    con = sqlite3.connect(str(path))
    con.row_factory = sqlite3.Row
    cols = {r[1] for r in con.execute("PRAGMA table_info(customers)")}
    rows = []
    for r in con.execute("SELECT * FROM customers"):
        d = dict(r)
        rows.append({
            "customer_name": _norm("customer_name", d.get("customer_name")),
            "order_type": _norm("order_type", d.get("order_type")),
            **{f: _norm(f, d.get(f)) for f in _FIELDS},
        })
    con.close()
    return rows, cols


def _load_existing(cur):
    cur.execute("SELECT customer_name, order_type, " + ", ".join(_FIELDS) + " FROM dbo.CTV_Customers")
    out = {}
    for row in cur.fetchall():
        name, otype = _norm("customer_name", row[0]), _norm("order_type", row[1])
        out[(name, otype)] = {f: _norm(f, row[2 + i]) for i, f in enumerate(_FIELDS)}
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("sqlite_path")
    ap.add_argument("--apply", action="store_true", help="insert missing rows")
    ap.add_argument("--overwrite", action="store_true", help="also update conflicting rows from this source")
    args = ap.parse_args()

    rows, cols = _read_sqlite(args.sqlite_path)
    print(f"Source {args.sqlite_path}: {len(rows)} customer row(s); columns present: {len(cols)}")

    with connect() as conn:
        cur = conn.cursor()
        existing = _load_existing(cur)
        gaps, identical, conflicts = [], 0, []
        for r in rows:
            key = (r["customer_name"], r["order_type"])
            if key not in existing:
                gaps.append(r)
            else:
                diffs = {f: (existing[key][f], r[f]) for f in _FIELDS if existing[key][f] != r[f]}
                if diffs:
                    conflicts.append((key, diffs))
                else:
                    identical += 1

        print(f"  → {len(gaps)} to insert · {identical} identical · {len(conflicts)} conflict(s)")
        if conflicts:
            print("\nCONFLICTS (table value → source value) — resolve by choosing, or re-run with --overwrite:")
            for (name, otype), diffs in conflicts:
                print(f"  • {name} [{otype}]")
                for f, (tv, sv) in diffs.items():
                    print(f"      {f}: {tv!r} → {sv!r}")

        if args.apply:
            for r in gaps:
                cols_sql = ", ".join(["customer_name", "order_type"] + _FIELDS)
                ph = ", ".join(["%s"] * (2 + len(_FIELDS)))
                cur.execute(
                    f"INSERT INTO dbo.CTV_Customers ({cols_sql}) VALUES ({ph})",
                    [r["customer_name"], r["order_type"], *[r[f] for f in _FIELDS]],
                )
            n_over = 0
            if args.overwrite and conflicts:
                src = {(r["customer_name"], r["order_type"]): r for r in rows}
                for (name, otype), _ in conflicts:
                    r = src[(name, otype)]
                    setclause = ", ".join(f"{f}=%s" for f in _FIELDS) + ", updated_at=GETDATE()"
                    cur.execute(
                        f"UPDATE dbo.CTV_Customers SET {setclause} WHERE customer_name=%s AND order_type=%s",
                        [*[r[f] for f in _FIELDS], name, otype],
                    )
                    n_over += 1
            conn.commit()
            print(f"\nAPPLIED: inserted {len(gaps)}"
                  + (f", overwrote {n_over} conflict(s)" if args.overwrite else
                     (f", left {len(conflicts)} conflict(s) untouched" if conflicts else "")))
        else:
            print("\n(dry run — add --apply to insert the gaps"
                  + (", --overwrite to also update conflicts)" if conflicts else ")"))


if __name__ == "__main__":
    main()
