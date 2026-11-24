"""WorldLink / American Heart Association, week of 11/23-11/29/2026 (3191 CTV, 3199 TAC):
add AHAHH2CCFH (Hold Me Closer Holiday) from 11/25.

The Rev 11-02 sheet gives five creatives 20% each for 11/23-11/29. HH was not
delivered until 11/23 evening, so 11/23 aired on four creatives. This script moves
enough of the unaired 11/25-11/29 spots onto HH that every creative ends the WEEK
at 20% (as near as whole spots allow), per contract and spread across markets and days.
Only the spots that change are written; the rest keep their creative.

    uv run python3 scripts/rebalance_aha_2611_hh.py            # plan, no write
    uv run python3 scripts/rebalance_aha_2611_hh.py --apply    # POST to the Control Room
                                                               # server (assign-spots), read back

--apply goes through the server's own /assign-spots route (pool registration,
full-row TPALINSE assignment, CONTRATTIFILMATI), so run it where the server listens.
Lee 2026-11-24.
"""

import argparse
import json
import sys
import urllib.request
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "browser_automation"))

from browser_automation.etere_direct_client import connect  # noqa: E402

SERVER = "http://localhost:8000"
CONTRACTS = (3191, 3199)
WEEK = ("2026-11-23", "2026-11-29")
FROM = "2026-11-25"  # first day HH may air (Lee 11/24: start 11/25, not 11/24)
HH = 154863
ROTATION = {47240: "JO", 89413: "GH", 103550: "SJ", 103067: "SD", HH: "HH"}


def load(cur, contract):
    cur.execute(
        """
        SELECT tp.ID_TPALINSE AS tp_id, tp.COD_USER AS market,
               CONVERT(varchar(10), tp.DATA, 120) AS day, tp.ORA AS ora,
               tp.ID_FILMATI AS fid, RTRIM(tp.STATUS) AS status
        FROM trafficPalinse tr
        JOIN TPALINSE tp ON tp.ID_TPALINSE = tr.id_tpalinse
        JOIN CONTRATTIRIGHE cr ON cr.ID_CONTRATTIRIGHE = tr.ID_ContrattiRighe
        WHERE cr.ID_CONTRATTITESTATA = %s AND tp.LIVELLO = 0
          AND tp.DATA BETWEEN %s AND %s
        ORDER BY tp.COD_USER, tp.DATA, tp.ORA
        """,
        (contract, *WEEK),
    )
    return cur.fetchall()


def plan_contract(rows):
    """[(tp_id, old_fid)] to move onto HH, plus the per-market rows for the report."""
    strange = [r for r in rows if r["fid"] not in ROTATION]
    if strange:
        raise SystemExit(f"REFUSING: {len(strange)} spot(s) carry a creative outside the sheet")
    by_mkt = defaultdict(list)
    for r in rows:
        by_mkt[r["market"]].append(r)

    def movable(r):
        return r["status"] == "I" and r["day"] >= FROM and r["fid"] != HH

    # HH share: 20% of the contract's week, dealt to markets by largest remainder.
    total = len(rows)
    already = sum(1 for r in rows if r["fid"] == HH)
    want = round(total / 5) - already
    quota = {m: len(v) / 5 for m, v in by_mkt.items()}
    give = {m: int(q) for m, q in quota.items()}
    for m in sorted(quota, key=lambda m: quota[m] - give[m], reverse=True):
        if sum(give.values()) >= want + already:
            break
        give[m] += 1
    moves = []
    for m, mrows in by_mkt.items():
        n = min(
            give[m] - sum(1 for r in mrows if r["fid"] == HH), sum(1 for r in mrows if movable(r))
        )
        if n <= 0:
            continue
        # Take each HH spot from the creative that is highest for the week.
        week = Counter(r["fid"] for r in mrows)
        left = Counter(r["fid"] for r in mrows if movable(r))
        take = Counter()
        for _ in range(n):
            src = max((f for f in left if left[f] > 0), key=lambda f: (week[f], left[f]))
            take[src] += 1
            week[src] -= 1
            left[src] -= 1
        # Spread the HH spots evenly through the market's remaining spots in time.
        cand = [r for r in mrows if r["status"] == "I" and r["day"] >= FROM]
        used = set()
        for i in range(n):
            ideal = (i + 0.5) * len(cand) / n
            best = min(
                (j for j, r in enumerate(cand) if j not in used and take[r["fid"]] > 0),
                key=lambda j: abs(j + 0.5 - ideal),
            )
            used.add(best)
            take[cand[best]["fid"]] -= 1
            moves.append((cand[best]["tp_id"], cand[best]["fid"], m, cand[best]["day"]))
    return moves


def report(contract, rows, moves):
    moved = {tp for tp, *_ in moves}
    after = Counter(HH if r["tp_id"] in moved else r["fid"] for r in rows)
    before = Counter(r["fid"] for r in rows)
    aired = Counter(r["fid"] for r in rows if r["status"] != "I" or r["day"] < FROM)
    print(f"\nContract {contract}: {len(rows)} spots in the week, {len(moves)} move to HH")
    print("  creative   aired/fixed   week now   week after   share after")
    for fid, tag in ROTATION.items():
        print(
            f"  {tag:<9} {aired[fid]:>8} {before[fid]:>12} {after[fid]:>11} "
            f"{after[fid] / len(rows):>12.1%}"
        )
    days = Counter(d for *_, d in moves)
    print("  HH by day:   ", "  ".join(f"{d[5:]}: {days[d]}" for d in sorted(days)))
    mk = Counter(m for _, _, m, _ in moves)
    print("  HH by market:", "  ".join(f"{m}: {mk[m]}" for m in sorted(mk)))
    src = Counter(ROTATION[f] for _, f, _, _ in moves)
    print("  taken from:  ", "  ".join(f"{t}: {n}" for t, n in sorted(src.items())))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--server", default=SERVER)
    args = ap.parse_args()

    conn = connect()
    cur = conn.cursor(as_dict=True)
    cur.execute(
        "SELECT COUNT(*) AS n FROM FS_FILMATI WHERE ID_FILMATI = %s AND ISNULL(DUR, 0) > 0", (HH,)
    )
    if not cur.fetchone()["n"]:
        raise SystemExit("REFUSING: AHAHH2CCFH has no sized file copy to bind")
    plans = {}
    for c in CONTRACTS:
        rows = load(cur, c)
        plans[c] = (rows, plan_contract(rows))
        report(c, *plans[c])
    conn.close()

    if not args.apply:
        print("\nDry run, nothing written. Re-run with --apply where the server listens.")
        return 0

    for c, (_rows, moves) in plans.items():
        body = json.dumps(
            {"assignments": [{"tp_id": tp, "filmati_id": HH} for tp, *_ in moves]}
        ).encode()
        req = urllib.request.Request(
            f"{args.server}/api/traffic/contract/{c}/assign-spots",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=300) as resp:
            print(f"  {c}: {resp.read()[:300]!r}")

    print("\nReadback (fresh connection):")
    conn = connect()
    cur = conn.cursor(as_dict=True)
    bad = 0
    for c, (_rows, moves) in plans.items():
        rows = load(cur, c)
        got = {r["tp_id"]: r["fid"] for r in rows}
        miss = [tp for tp, *_ in moves if got.get(tp) != HH]
        bad += len(miss)
        wk = Counter(r["fid"] for r in rows)
        print(
            f"  {c}: "
            + "  ".join(f"{t} {wk[f]}" for f, t in ROTATION.items())
            + (f"   <-- {len(miss)} spot(s) NOT on HH" if miss else "   OK")
        )
    conn.close()
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
