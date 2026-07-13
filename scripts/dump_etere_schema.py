"""Dump the Etere SQL Server schema to deterministic text files for git diffing.

Etere updates sometimes alter the database (new columns, new SP parameters,
etc.). This script snapshots everything schema-shaped into schema/etere/ so
that after any Etere update you can rerun it and `git diff schema/etere/`
shows exactly what changed.

Usage (after every Etere update, or whenever in doubt):

    uv run python scripts/dump_etere_schema.py
    git diff schema/etere/

Output files (all sorted, no timestamps — identical DB state produces
byte-identical files):

    tables.txt          every user table: columns with type/null/default/identity
    keys_indexes.txt    primary keys, unique constraints, foreign keys, indexes
    routines.txt        SP + function signatures (parameter name/type/output).
                        Etere encrypts nearly all module BODIES (definition is
                        NULL in sys.sql_modules), but parameter signatures stay
                        visible — enough to catch e.g. web_sales_InsertContractLine
                        gaining a 60th parameter.
    modules.txt         full T-SQL of the non-encrypted modules (views, triggers,
                        a handful of functions); encrypted ones listed by name only
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from browser_automation.etere_direct_client import connect  # noqa: E402

OUT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "schema", "etere")


def _fmt_type(type_name, max_length, precision, scale):
    """Render a sys.columns/sys.parameters type the way DDL would."""
    t = type_name.lower()
    if t in ("varchar", "char", "varbinary", "binary"):
        return f"{t}({'max' if max_length == -1 else max_length})"
    if t in ("nvarchar", "nchar"):
        return f"{t}({'max' if max_length == -1 else max_length // 2})"
    if t in ("decimal", "numeric"):
        return f"{t}({precision},{scale})"
    if t in ("datetime2", "datetimeoffset", "time") and scale != 7:
        return f"{t}({scale})"
    return t


def dump_tables(cur):
    cur.execute("""
        SELECT s.name, t.name, c.column_id, c.name,
               TYPE_NAME(c.user_type_id), c.max_length, c.precision, c.scale,
               c.is_nullable, c.is_identity, c.is_computed,
               OBJECT_DEFINITION(c.default_object_id)
        FROM sys.tables t
        JOIN sys.schemas s ON s.schema_id = t.schema_id
        JOIN sys.columns c ON c.object_id = t.object_id
        ORDER BY s.name, t.name, c.column_id""")
    lines, current = [], None
    for sch, tbl, _cid, col, typ, mlen, prec, scale, nullable, ident, comp, default in cur.fetchall():
        key = f"{sch}.{tbl}"
        if key != current:
            if current is not None:
                lines.append("")
            lines.append(f"TABLE {key}")
            current = key
        bits = [_fmt_type(typ, mlen, prec, scale),
                "NULL" if nullable else "NOT NULL"]
        if ident:
            bits.append("IDENTITY")
        if comp:
            bits.append("COMPUTED")
        if default:
            bits.append(f"DEFAULT {default}")
        lines.append(f"  {col:40s} {' '.join(bits)}")
    return "\n".join(lines) + "\n"


def dump_keys_indexes(cur):
    lines = []
    # Indexes (covers PKs and unique constraints too — is_primary_key/is_unique flag them)
    cur.execute("""
        SELECT s.name, t.name, i.name, i.type_desc, i.is_unique, i.is_primary_key,
               i.is_unique_constraint, c.name, ic.key_ordinal, ic.is_included_column
        FROM sys.indexes i
        JOIN sys.tables t ON t.object_id = i.object_id
        JOIN sys.schemas s ON s.schema_id = t.schema_id
        JOIN sys.index_columns ic ON ic.object_id = i.object_id AND ic.index_id = i.index_id
        JOIN sys.columns c ON c.object_id = ic.object_id AND c.column_id = ic.column_id
        WHERE i.name IS NOT NULL
        ORDER BY s.name, t.name, i.name, ic.is_included_column, ic.key_ordinal, c.name""")
    idx = {}
    for sch, tbl, ix, tdesc, uniq, pk, uc, col, _ord, incl in cur.fetchall():
        k = (f"{sch}.{tbl}", ix, tdesc, uniq, pk, uc)
        idx.setdefault(k, ([], []))[1 if incl else 0].append(col)
    for (tbl, ix, tdesc, uniq, pk, uc), (keys, incl) in sorted(idx.items()):
        kind = "PK" if pk else "UNIQUE CONSTRAINT" if uc else "UNIQUE INDEX" if uniq else "INDEX"
        line = f"{tbl:45s} {kind} {ix} ({tdesc}) ON ({', '.join(keys)})"
        if incl:
            line += f" INCLUDE ({', '.join(incl)})"
        lines.append(line)
    lines.append("")
    # Foreign keys
    cur.execute("""
        SELECT fk.name, s1.name, t1.name, c1.name, s2.name, t2.name, c2.name
        FROM sys.foreign_keys fk
        JOIN sys.foreign_key_columns fkc ON fkc.constraint_object_id = fk.object_id
        JOIN sys.tables t1 ON t1.object_id = fkc.parent_object_id
        JOIN sys.schemas s1 ON s1.schema_id = t1.schema_id
        JOIN sys.columns c1 ON c1.object_id = fkc.parent_object_id AND c1.column_id = fkc.parent_column_id
        JOIN sys.tables t2 ON t2.object_id = fkc.referenced_object_id
        JOIN sys.schemas s2 ON s2.schema_id = t2.schema_id
        JOIN sys.columns c2 ON c2.object_id = fkc.referenced_object_id AND c2.column_id = fkc.referenced_column_id
        ORDER BY fk.name, fkc.constraint_column_id""")
    for name, s1, t1, c1, s2, t2, c2 in cur.fetchall():
        lines.append(f"FK {name}: {s1}.{t1}.{c1} -> {s2}.{t2}.{c2}")
    return "\n".join(lines) + "\n"


def dump_routines(cur):
    cur.execute("""
        SELECT s.name, o.name, o.type_desc,
               CASE WHEN m.object_id IS NOT NULL AND m.definition IS NULL THEN 1 ELSE 0 END,
               p.parameter_id, p.name, TYPE_NAME(p.user_type_id),
               p.max_length, p.precision, p.scale, p.is_output
        FROM sys.objects o
        JOIN sys.schemas s ON s.schema_id = o.schema_id
        LEFT JOIN sys.sql_modules m ON m.object_id = o.object_id
        LEFT JOIN sys.parameters p ON p.object_id = o.object_id
        WHERE o.type IN ('P', 'FN', 'TF', 'IF')
        ORDER BY s.name, o.name, p.parameter_id""")
    lines, current = [], None
    for sch, name, tdesc, enc, pid, pname, typ, mlen, prec, scale, out in cur.fetchall():
        key = f"{sch}.{name}"
        if key != current:
            if current is not None:
                lines.append("")
            enc_tag = " [ENCRYPTED]" if enc else ""
            lines.append(f"{tdesc} {key}{enc_tag}")
            current = key
        if pid is None:
            continue
        if pid == 0:  # a function's return value
            lines.append(f"  RETURNS {_fmt_type(typ, mlen, prec, scale)}")
            continue
        out_tag = " OUTPUT" if out else ""
        lines.append(f"  @{pid:<3d} {pname or '':35s} {_fmt_type(typ, mlen, prec, scale)}{out_tag}")
    return "\n".join(lines) + "\n"


def dump_modules(cur):
    cur.execute("""
        SELECT s.name, o.name, o.type_desc, m.definition
        FROM sys.sql_modules m
        JOIN sys.objects o ON o.object_id = m.object_id
        JOIN sys.schemas s ON s.schema_id = o.schema_id
        ORDER BY s.name, o.name""")
    lines = []
    encrypted = []
    for sch, name, tdesc, definition in cur.fetchall():
        if definition is None:
            encrypted.append(f"{tdesc} {sch}.{name}")
            continue
        lines.append(f"===== {tdesc} {sch}.{name} =====")
        # Normalize line endings so reruns diff cleanly
        lines.append(definition.replace("\r\n", "\n").rstrip() + "\n")
    lines.append(f"===== ENCRYPTED MODULES (definition unavailable): {len(encrypted)} =====")
    lines.extend(encrypted)
    return "\n".join(lines) + "\n"


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    with connect() as conn:
        cur = conn.cursor()
        for fname, fn in (("tables.txt", dump_tables),
                          ("keys_indexes.txt", dump_keys_indexes),
                          ("routines.txt", dump_routines),
                          ("modules.txt", dump_modules)):
            content = fn(cur)
            path = os.path.join(OUT_DIR, fname)
            with open(path, "w", encoding="utf-8", newline="\n") as f:
                f.write(content)
            print(f"wrote {path} ({len(content):,} chars)")
    print("\nNow run: git diff schema/etere/   (or git add + commit as the new baseline)")


if __name__ == "__main__":
    main()
