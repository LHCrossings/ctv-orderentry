"""Campaign PSA exposure report — placed and aired rows per market-day and creative.

    uv run python3 scripts/campaign_psa_report.py [--since YYYY-MM-DD] [--until YYYY-MM-DD]

Counts every live TPALINSE row of the active campaign's creatives (campaign_psa.CAMPAIGNS),
aired = STATUS 'Q' (or 'D' intraday). Lee 10/21: 5-10 per market-day is the target, the
cap is 8.
"""

from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

_root = Path(__file__).resolve().parents[1]
for p in (str(_root), str(_root / "src")):
    if p not in sys.path:
        sys.path.insert(0, p)

from browser_automation.etere_direct_client import connect  # noqa: E402
from src.business_logic.services.campaign_psa import CAMPAIGNS, resolve_creatives  # noqa: E402

MARKETS = {
    1: "NYC",
    2: "CMP",
    3: "HOU",
    4: "SFO",
    5: "SEA",
    6: "LAX",
    7: "CVC",
    8: "WDC",
    9: "MMT",
    10: "DAL",
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default=(date.today() - timedelta(days=7)).isoformat())
    ap.add_argument("--until", default=date.today().isoformat())
    a = ap.parse_args()
    conn = connect()
    cur = conn.cursor()
    for camp in CAMPAIGNS:
        creatives = resolve_creatives(cur, camp)
        by_fid = {cr["fid"]: (lang, cr["code"]) for lang, lst in creatives.items() for cr in lst}
        print(f"{camp.name}: {camp.start} - {camp.end}, cap {camp.cap_per_day}/market-day")
        for lang, lst in creatives.items():
            print(
                f"  {lang:<10} "
                + (", ".join(f"{c['code']} (#{c['fid']})" for c in lst) or "— no creative ingested")
            )
        if not by_fid:
            continue
        ids_csv = ",".join(str(f) for f in by_fid)
        cur.execute(
            f"SELECT COD_USER, CAST(DATA AS date), ID_FILMATI, STATUS FROM TPALINSE WITH (NOLOCK)"
            f" WHERE LIVELLO=0 AND ID_FILMATI IN ({ids_csv}) AND DATA BETWEEN %s AND %s",
            (a.since, a.until),
        )
        placed: dict = defaultdict(lambda: defaultdict(int))
        aired: dict = defaultdict(lambda: defaultdict(int))
        for mkt, d, fid, status in cur.fetchall():
            lang = by_fid[int(fid)][0]
            placed[(d, int(mkt))][lang] += 1
            if (status or "").strip() in ("Q", "D"):
                aired[(d, int(mkt))][lang] += 1
        print(f"\n  {'day':<11}{'mkt':<5}{'placed':>7}{'aired':>7}   by language")
        for key in sorted(placed):
            d, mkt = key
            tot = sum(placed[key].values())
            flag = "  ⚠ over cap" if tot > camp.cap_per_day else ("  ⚠ under 5" if tot < 5 else "")
            langs = ", ".join(f"{k} {v}" for k, v in sorted(placed[key].items()))
            print(
                f"  {str(d):<11}{MARKETS.get(mkt, mkt):<5}{tot:>7}{sum(aired[key].values()):>7}   {langs}{flag}"
            )
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
