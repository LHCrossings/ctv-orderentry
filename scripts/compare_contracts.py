"""
Compare two Etere contracts field-by-field.

Usage:
    uv run python scripts/compare_contracts.py <REF_ID> <TEST_ID>

REF_ID  — contract entered via Etere Web / Selenium (the "known good" baseline)
TEST_ID — contract entered via EtereDirectClient (what we're validating)

Output shows every field in CONTRATTITESTATA, CONTRATTIRIGHE, and CONTRATTIFASCE,
grouped as:
  [OK]        — values match
  [DIFF]      — unexpected mismatch (needs investigation)
  [ID/TS]     — PK, code, description, or timestamp — always differs, skip
  [NULL-TEST] — reference has a value, direct entry left it NULL/0

Run from Windows:  py scripts/compare_contracts.py 2381 2782
"""
import sys
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from browser_automation.etere_direct_client import connect  # noqa: E402

# ── Fields we expect to always differ (PK, identity, free-text, timestamps) ──
# These are skipped from the DIFF count so they don't pollute the signal.
HEADER_SKIP = {
    "ID_CONTRATTITESTATA",
    "COD_CONTRATTO",
    "DESCRIZIONE",
    "DATA_PROPOSTA",
    "DATA_SCADENZA_PROPOSTA",
    "DATA_INSERIMENTO",
    "DATA_AGGIORNAMENTO",
    "DATACREAZIONE",
    "DATAORAMODIFICA",
    "NOTE",                    # free-text note
    "RIFERIMENTO_ORDINE",      # customer order ref (free-text)
    "CUSTOMERCOLOR",           # cosmetic color, not functionally meaningful
}

LINE_SKIP = {
    "ID_CONTRATTIRIGHE",
    "ID_CONTRATTITESTATA",
    "DESCRIZIONERIGHE",
    "DATA_CREAZIONE",
    "DATACREAZIONE",
    "DATAORAMODIFICA",
    "DATA_INSERIMENTO",
    "DATA_AGGIORNAMENTO",
    "NOTERIGHE",               # free-text note on line
}

# ── Numeric zero is often the same as NULL for Etere unused fields ────────────
def _norm(v):
    """Normalise for comparison: None and 0 and '' and b'' are treated as empty."""
    if v is None:
        return None
    if isinstance(v, (bytes, bytearray)) and not v:
        return None
    if isinstance(v, str) and v.strip() == "":
        return None
    return v


def _fmt(v):
    if v is None:
        return "NULL"
    if isinstance(v, (bytes, bytearray)):
        return f"<bytes {len(v)}>"
    return repr(v)


def fetch_header(cur, contract_id):
    cur.execute(
        "SELECT * FROM CONTRATTITESTATA WHERE ID_CONTRATTITESTATA = %s", (contract_id,)
    )
    cols = [d[0] for d in cur.description]
    row = cur.fetchone()
    if not row:
        raise ValueError(f"Contract ID {contract_id} not found in CONTRATTITESTATA")
    return dict(zip(cols, row))


def fetch_lines(cur, contract_id):
    cur.execute(
        "SELECT * FROM CONTRATTIRIGHE WHERE ID_CONTRATTITESTATA = %s ORDER BY ID_CONTRATTIRIGHE",
        (contract_id,),
    )
    cols = [d[0] for d in cur.description]
    rows = cur.fetchall()
    return [dict(zip(cols, r)) for r in rows]


def fetch_blocks(cur, line_id):
    cur.execute(
        "SELECT * FROM CONTRATTIFASCE WHERE ID_CONTRATTIRIGHE = %s ORDER BY ID_FASCE",
        (line_id,),
    )
    cols = [d[0] for d in cur.description]
    rows = cur.fetchall()
    return [dict(zip(cols, r)) for r in rows]


def compare_dicts(ref, test, skip_keys, label):
    """
    Print a comparison table between ref and test dicts.
    Returns (diff_count, null_count) — both are flags that need investigation.
    """
    diffs = 0
    nulls = 0
    all_keys = list(ref.keys())

    ok_lines = []
    diff_lines = []
    null_lines = []
    id_lines = []

    for k in all_keys:
        rv = _norm(ref.get(k))
        tv = _norm(test.get(k))

        if k in skip_keys:
            id_lines.append(f"  [ID/TS]  {k:40s}  ref={_fmt(ref.get(k))}")
            continue

        if rv == tv:
            ok_lines.append(f"  [OK]     {k:40s}  {_fmt(rv)}")
        elif tv is None and rv is not None:
            nulls += 1
            null_lines.append(
                f"  [NULL!]  {k:40s}  ref={_fmt(rv)}  →  test=NULL"
            )
        else:
            diffs += 1
            diff_lines.append(
                f"  [DIFF!]  {k:40s}  ref={_fmt(rv)}  →  test={_fmt(tv)}"
            )

    # Print in order: problems first, then OK, then expected-diff
    print(f"\n{'─'*70}")
    print(f"  {label}")
    print(f"{'─'*70}")

    if diff_lines or null_lines:
        print(f"\n  *** {len(diff_lines)} MISMATCH(ES), {len(nulls if isinstance(nulls, list) else [])} ***")

    for line in diff_lines:
        print(line)
    for line in null_lines:
        print(line)

    if diff_lines or null_lines:
        print()  # blank separator

    for line in ok_lines:
        print(line)

    if id_lines:
        print(f"\n  — Expected-differ fields ({len(id_lines)}) —")
        for line in id_lines:
            print(line)

    return diffs, nulls


def run(ref_id, test_id, last: int = 0):
    print("=" * 70)
    print("  CONTRACT COMPARISON")
    print(f"  REF  (Selenium baseline) : #{ref_id}")
    print(f"  TEST (EtereDirect entry) : #{test_id}")
    if last:
        print(f"  Comparing last {last} line(s) from each contract")
    print("=" * 70)

    conn = connect()
    cur = conn.cursor()

    # ── 1. Header ────────────────────────────────────────────────────────────
    ref_h  = fetch_header(cur, ref_id)
    test_h = fetch_header(cur, test_id)

    ref_code  = ref_h.get("COD_CONTRATTO", "?")
    test_code = test_h.get("COD_CONTRATTO", "?")
    print(f"\n  Ref  code : {ref_code}")
    print(f"  Test code : {test_code}")

    h_diffs, h_nulls = compare_dicts(ref_h, test_h, HEADER_SKIP, "CONTRACT HEADER (CONTRATTITESTATA)")

    # ── 2. Lines ─────────────────────────────────────────────────────────────
    ref_lines  = fetch_lines(cur, ref_id)
    test_lines = fetch_lines(cur, test_id)

    if last:
        ref_lines  = ref_lines[-last:]
        test_lines = test_lines[-last:]

    print(f"\n{'='*70}")
    print("  CONTRACT LINES (CONTRATTIRIGHE)")
    print(f"  Ref has {len(ref_lines)} line(s), Test has {len(test_lines)} line(s)")
    print(f"{'='*70}")

    if len(ref_lines) != len(test_lines):
        print(f"\n  *** LINE COUNT MISMATCH — only comparing the first "
              f"{min(len(ref_lines), len(test_lines))} lines ***\n")

    total_line_diffs = 0
    total_line_nulls = 0

    for i, (rl, tl) in enumerate(zip(ref_lines, test_lines), 1):
        ld, ln = compare_dicts(
            rl, tl, LINE_SKIP,
            f"LINE {i}/{max(len(ref_lines), len(test_lines))}  "
            f"ref_line_id={rl['ID_CONTRATTIRIGHE']}  test_line_id={tl['ID_CONTRATTIRIGHE']}"
        )
        total_line_diffs += ld
        total_line_nulls += ln

        # Block assignments
        ref_blocks  = fetch_blocks(cur, rl["ID_CONTRATTIRIGHE"])
        test_blocks = fetch_blocks(cur, tl["ID_CONTRATTIRIGHE"])
        print(f"\n  BLOCKS:  ref={len(ref_blocks)}  test={len(test_blocks)}", end="")
        if len(ref_blocks) != len(test_blocks):
            print("  *** BLOCK COUNT MISMATCH ***")
        else:
            print("  [OK]")

        # Show block IDs for manual spot-check
        if ref_blocks or test_blocks:
            ref_ids  = [b["ID_FASCE"] for b in ref_blocks]
            test_ids = [b["ID_FASCE"] for b in test_blocks]
            print(f"  Block IDs ref  : {ref_ids}")
            print(f"  Block IDs test : {test_ids}")

    # ── 3. Summary ───────────────────────────────────────────────────────────
    print(f"\n{'='*70}")
    print("  SUMMARY")
    print(f"{'='*70}")
    print(f"  Header mismatches  : {h_diffs}")
    print(f"  Header nulls       : {h_nulls}")
    print(f"  Line mismatches    : {total_line_diffs}")
    print(f"  Line nulls         : {total_line_nulls}")
    total = h_diffs + h_nulls + total_line_diffs + total_line_nulls
    if total == 0:
        print("\n  ✓ All compared fields match — direct entry looks correct!")
    else:
        print(f"\n  ✗ {total} issue(s) need review before rolling out direct entry.")
    print()

    conn.close()


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("ref_id", type=int)
    parser.add_argument("test_id", type=int)
    parser.add_argument("--last", type=int, default=0,
                        help="Compare only the last N lines from each contract (by ID order)")
    args = parser.parse_args()
    run(args.ref_id, args.test_id, last=args.last)
