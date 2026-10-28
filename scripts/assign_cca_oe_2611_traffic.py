"""Traffic for IW Group / Covered California OE Action 2611 (3219 Chinese, 3220
Vietnamese, 3221 Filipino): CTA2 creatives on spots dated 11/9-12/31, CTA3 on
1/1-1/31, per line duration. Assignment goes by SPOT DATE because several lines
straddle 12/31 (87136, 87182, 87198...).

Runs the server's own assign endpoint (/api/traffic/contract/{id}/assign — pool
registration via MaterialAddToAssetListC, TPALINSE copy + SUPPORTO binding,
CONTRATTIFILMATI rows) so nothing here re-implements a write path. Run it ON the
Jumpbox, where the Control Room server listens on :8000:

    uv run python3 scripts/assign_cca_oe_2611_traffic.py           # plan + expected counts
    uv run python3 scripts/assign_cca_oe_2611_traffic.py --apply   # fire the 12 calls, then read back

Lee 2026-10-28; creatives named that morning (COVER30T45/46, COVER15x50/51).
"""

import argparse
import json
import sys
import urllib.request
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

SERVER = "http://localhost:8000"

# (contract, filmati, our code, line DURATA frames, date_from, date_to)
PLAN = [
    (3219, 120954, "COVER30M45 Open Invitation rev CHI", 900, "2026-11-09", "2026-12-31"),
    (3219, 124763, "COVER30M46 Open Invitation rev2 CHI", 900, "2027-01-01", "2027-01-31"),
    (3219, 152564, "COVER15M50 Open Invitation Cutdown W2 CHI", 450, "2026-11-09", "2026-12-31"),
    (3219, 152565, "COVER15M51 Open Invitation Cutdown W3 CHI", 450, "2027-01-01", "2027-01-31"),
    (3220, 120955, "COVER30V45 Open Invitation rev VIE", 900, "2026-11-09", "2026-12-31"),
    (3220, 124764, "COVER30V46 Open Invitation rev2 VIE", 900, "2027-01-01", "2027-01-31"),
    (3220, 152568, "COVER15V50 Open Invitation Cutdown W2 VIE", 450, "2026-11-09", "2026-12-31"),
    (3220, 152569, "COVER15V51 Open Invitation Cutdown W3 VIE", 450, "2027-01-01", "2027-01-31"),
    (3221, 152570, "COVER30T45 Open Invitation rev TAG", 900, "2026-11-09", "2026-12-31"),
    (3221, 152571, "COVER30T46 Open Invitation rev2 TAG", 900, "2027-01-01", "2027-01-31"),
    (3221, 152566, "COVER15T50 Open Invitation Cutdown W2 TAG", 450, "2026-11-09", "2026-12-31"),
    (3221, 152567, "COVER15T51 Open Invitation Cutdown W3 TAG", 450, "2027-01-01", "2027-01-31"),
]
CONTRACTS = sorted({p[0] for p in PLAN})


def expected():
    """Spots per (contract, DURATA, window) straight from the placements, plus a
    check that every creative exists and has the duration its lines carry."""
    from browser_automation.etere_direct_client import connect

    conn = connect()
    cur = conn.cursor()
    cur.execute(
        """SELECT cr.ID_CONTRATTITESTATA, cr.DURATA, tp.DATA, COUNT(*),
                  SUM(CASE WHEN tp.ID_FILMATI > 0 THEN 1 ELSE 0 END)
           FROM CONTRATTIRIGHE cr
           JOIN trafficPalinse trp ON trp.ID_ContrattiRighe = cr.ID_CONTRATTIRIGHE
           JOIN TPALINSE tp ON tp.ID_TPALINSE = trp.id_tpalinse AND tp.LIVELLO = 0
           WHERE cr.ID_CONTRATTITESTATA IN (%s, %s, %s)
           GROUP BY cr.ID_CONTRATTITESTATA, cr.DURATA, tp.DATA""",
        tuple(CONTRACTS),
    )
    rows = cur.fetchall()
    counts, with_copy = defaultdict(int), defaultdict(int)
    for c, dur, day, n, copied in rows:
        d = str(day)[:10]
        for pc, _, _, pdur, lo, hi in PLAN:
            if pc == c and pdur == dur and lo <= d <= hi:
                counts[(c, dur, lo)] += n
                with_copy[(c, dur, lo)] += copied
    cur.execute(
        "SELECT ID_FILMATI, DESCRIZIO, DURATA FROM FILMATI WHERE ID_FILMATI IN (%s)"
        % ",".join(str(p[1]) for p in PLAN)
    )
    assets = {r[0]: (r[1], r[2]) for r in cur.fetchall()}
    conn.close()
    return counts, with_copy, assets


def readback():
    from browser_automation.etere_direct_client import connect

    conn = connect()
    cur = conn.cursor()
    cur.execute(
        """SELECT cr.ID_CONTRATTITESTATA, cr.DURATA,
                  CASE WHEN tp.DATA <= '2026-12-31' THEN 'CTA2' ELSE 'CTA3' END,
                  tp.ID_FILMATI, f.DESCRIZIO, COUNT(*)
           FROM CONTRATTIRIGHE cr
           JOIN trafficPalinse trp ON trp.ID_ContrattiRighe = cr.ID_CONTRATTIRIGHE
           JOIN TPALINSE tp ON tp.ID_TPALINSE = trp.id_tpalinse AND tp.LIVELLO = 0
           LEFT JOIN FILMATI f ON f.ID_FILMATI = tp.ID_FILMATI
           WHERE cr.ID_CONTRATTITESTATA IN (%s, %s, %s)
           GROUP BY cr.ID_CONTRATTITESTATA, cr.DURATA,
                    CASE WHEN tp.DATA <= '2026-12-31' THEN 'CTA2' ELSE 'CTA3' END,
                    tp.ID_FILMATI, f.DESCRIZIO
           ORDER BY 1, 2, 3""",
        tuple(CONTRACTS),
    )
    rows = cur.fetchall()
    conn.close()
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--apply", action="store_true", help="POST the 12 assignments to the local server"
    )
    args = ap.parse_args()

    counts, with_copy, assets = expected()
    print("Plan (contract / creative / line length / spot dates / spots now / already with copy):")
    bad = 0
    for c, fid, code, dur, lo, hi in PLAN:
        desc, adur = assets.get(fid, ("MISSING", None))
        flag = ""
        if fid not in assets:
            flag, bad = "  <-- asset not in Etere", bad + 1
        elif adur != dur:
            flag, bad = f"  <-- asset is {adur} frames, lines are {dur}", bad + 1
        print(
            f"  {c}  {fid} {code:<44} :{dur // 30:<3} {lo}..{hi}  "
            f"{counts[(c, dur, lo)]:>4} spots  ({with_copy[(c, dur, lo)]} with copy){flag}"
        )
    print(f"  total spots: {sum(counts.values())}")
    if bad:
        print(f"REFUSING: {bad} plan row(s) do not match Etere")
        return 2
    if not args.apply:
        print("\nDry run. Re-run with --apply on the Jumpbox to assign.")
        return 0

    for c, fid, code, dur, lo, hi in PLAN:
        body = json.dumps(
            {
                "spots": [{"id": fid, "weight": 1}],
                "filters": {"date_from": lo, "date_to": hi, "durations": [dur]},
            }
        ).encode()
        req = urllib.request.Request(
            f"{SERVER}/api/traffic/contract/{c}/assign",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=180) as resp:
                out = json.loads(resp.read())
        except urllib.error.HTTPError as e:
            print(f"  {c} {code}: FAILED {e.code} {e.read()[:300]!r}")
            return 1
        print(f"  {c} {code}: {json.dumps(out)[:200]}")

    print("\nReadback (fresh connection):")
    missing = 0
    for c, dur, per, fid, desc, n in readback():
        mark = ""
        if not fid or fid <= 0:
            mark, missing = "  <-- NO COPY", missing + n
        print(f"  {c} :{dur // 30:<3} {per}  {fid or '-':>7} {desc or '':<44} {n:>4}{mark}")
    print(
        "OK: every spot carries a creative"
        if not missing
        else f"WARNING: {missing} spot(s) still without copy"
    )
    return 0 if not missing else 1


if __name__ == "__main__":
    sys.exit(main())
