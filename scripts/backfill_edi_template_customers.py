"""
Backfill `etere_customer_ids` (and `etere_agency_id` where needed) into
data/edi_templates/*.json — Phase 2 of tasks/edi-billing-redesign.md.

For each template, finds candidate ANAGRAF customers by name (LIKE on the
template's advertiser_match / advertiser_name) and shows contract-count
evidence for each candidate. READ-ONLY against the Etere DB; only writes
local JSON files, and only after an explicit confirm.

Usage:
    uv run python3 scripts/backfill_edi_template_customers.py            # propose + y/N
    uv run python3 scripts/backfill_edi_template_customers.py --dry-run  # propose only
    uv run python3 scripts/backfill_edi_template_customers.py --yes      # apply without prompt
    uv run python3 scripts/backfill_edi_template_customers.py --force    # re-propose even if already set
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
TMPL_DIR = REPO / "data" / "edi_templates"

STOPWORDS = {"the", "and", "of", "for", "dept", "department", "assoc", "association",
             "inc", "llc", "dba", "c/o"}


def _search_terms(t: dict) -> list[str]:
    terms = []
    for key in ("advertiser_match", "advertiser_name"):
        v = (t.get(key) or "").strip()
        if v and v not in terms:
            terms.append(v)
    return terms


def _fallback_terms(terms: list[str]) -> list[str]:
    """Significant leading words, for when the full string finds nothing."""
    out = []
    for term in terms:
        words = [w for w in re.findall(r"[A-Za-z]{4,}", term) if w.lower() not in STOPWORDS]
        if words:
            out.append(words[0] if len(words) == 1 else " ".join(words[:2]))
            if words[0] not in out:
                out.append(words[0])
    return out


def _find_candidates(cur, terms: list[str]) -> list[tuple[int, str]]:
    seen: dict[int, str] = {}
    for term in terms:
        like = "%" + term.replace("%", "[%]").replace("_", "[_]") + "%"
        cur.execute("SELECT ID_ANAGRAF, RAG_SOCIAL FROM ANAGRAF WHERE RAG_SOCIAL LIKE %s", (like,))
        for aid, name in cur.fetchall():
            seen.setdefault(int(aid), (name or "").strip())
        if seen:
            break
    return sorted(seen.items())


def _contract_evidence(cur, customer_id: int) -> tuple[int, int | None, str]:
    """(contract count, latest contract id, dominant agency 'id name')."""
    cur.execute("""
        SELECT COUNT(*), MAX(ct.ID_CONTRATTITESTATA)
        FROM CONTRATTITESTATA ct WHERE ct.COMMITTENTE = %s
    """, (customer_id,))
    count, latest = cur.fetchone()
    agency = ""
    if count:
        cur.execute("""
            SELECT TOP 1 ct.AGENZIA, ag.RAG_SOCIAL, COUNT(*) AS n
            FROM CONTRATTITESTATA ct
            LEFT JOIN ANAGRAF ag ON ag.ID_ANAGRAF = ct.AGENZIA
            WHERE ct.COMMITTENTE = %s AND ct.AGENZIA IS NOT NULL
            GROUP BY ct.AGENZIA, ag.RAG_SOCIAL ORDER BY n DESC
        """, (customer_id,))
        row = cur.fetchone()
        if row:
            agency = f"{row[0]} {(row[1] or '').strip()}"
    return int(count), latest, agency


def main() -> None:
    dry_run = "--dry-run" in sys.argv
    auto_yes = "--yes" in sys.argv
    force = "--force" in sys.argv

    from browser_automation.etere_direct_client import connect
    conn = connect()
    cur = conn.cursor()

    proposals = []   # (path, template, customer_ids, note, evidence_lines)
    for path in sorted(TMPL_DIR.glob("*.json")):
        t = json.loads(path.read_text())
        name = t.get("name", path.stem)
        if t.get("etere_customer_ids") and not force:
            print(f"  = {name}: already set {t['etere_customer_ids']} (use --force to redo)")
            continue

        terms = _search_terms(t)
        candidates = _find_candidates(cur, terms)
        if not candidates:
            candidates = _find_candidates(cur, _fallback_terms(terms))

        evidence = []
        scored = []
        for aid, aname in candidates:
            count, latest, agency = _contract_evidence(cur, aid)
            evidence.append(f"      {aid:>5}  {aname[:45]:45s} contracts={count:<4} latest={latest} agency={agency}")
            scored.append((count, aid))

        # Propose only candidates with contract history; single survivor = clean.
        with_contracts = [aid for count, aid in scored if count > 0]
        if len(with_contracts) == 1:
            ids, note = with_contracts, "ok"
        elif len(with_contracts) > 1:
            ids, note = with_contracts, "MULTIPLE — review"
        elif candidates:
            ids, note = [], "candidates have no contracts — review"
        else:
            ids, note = [], "NO CANDIDATES — set manually"
        proposals.append((path, t, ids, note, evidence))

    print("\n================ PROPOSED MAPPING ================")
    for path, t, ids, note, evidence in proposals:
        print(f"\n  {t.get('name', path.stem)}   [{note}]")
        print(f"      proposed etere_customer_ids = {ids}")
        for line in evidence:
            print(line)

    # Where two templates share a customer id and are not market-disambiguated,
    # the agency is the tie-breaker — propose etere_agency_id from the
    # template's own agency_name.
    by_cust: dict[int, list] = {}
    for prop in proposals:
        for cid in prop[2]:
            by_cust.setdefault(cid, []).append(prop)
    agency_props = []
    for cid, props in by_cust.items():
        markets = {p[1].get("market_match", "").strip().upper() for p in props}
        if len(props) > 1 and (len(markets) != len(props) or "" in markets):
            for path, t, ids, note, _ in props:
                a_cands = _find_candidates(cur, [t.get("agency_name", "")])
                a_cands = [(aid, an) for aid, an in a_cands] or []
                if len(a_cands) == 1:
                    agency_props.append((path, t, a_cands[0]))
                    print(f"\n  TIE-BREAK {t['name']}: customer {cid} shared — "
                          f"propose etere_agency_id={a_cands[0][0]} ({a_cands[0][1]})")
                else:
                    print(f"\n  ⚠ TIE-BREAK NEEDED for {t['name']} (customer {cid} shared) "
                          f"but agency lookup ambiguous: {a_cands} — set manually")

    conn.close()

    if dry_run:
        print("\n--dry-run: nothing written.")
        return

    to_write = [(p, t, ids) for p, t, ids, note, _ in proposals if ids]
    if not to_write and not agency_props:
        print("\nNothing to write.")
        return
    if not auto_yes:
        raw = input(f"\nWrite etere_customer_ids to {len(to_write)} template JSON(s) "
                    f"(+{len(agency_props)} agency tie-break(s))? [y/N] ").strip().lower()
        if raw not in ("y", "yes"):
            print("Aborted — nothing written.")
            return

    agency_by_path = {p: aid for p, _, (aid, _) in agency_props}
    for path, t, ids in to_write:
        t["etere_customer_ids"] = ids
        if path in agency_by_path:
            t["etere_agency_id"] = agency_by_path[path]
        path.write_text(json.dumps(t, indent=2))
        print(f"  wrote {path.name}: etere_customer_ids={ids}"
              + (f" etere_agency_id={t['etere_agency_id']}" if path in agency_by_path else ""))


if __name__ == "__main__":
    main()
