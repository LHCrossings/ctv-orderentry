"""
Verify the Admerasia traffic color-match against the 7 clean July McValue IOs.

Runs the LIVE vision ISCI-legend read (needs ANTHROPIC_API_KEY in env) and checks it
against the deterministic pixel palette: every grid colour should map one-to-one to a
distinct ISCI, and each grid program-row should be single-duration. Prints the
colour -> ISCI legend per file so you can eyeball it.

Run from the repo root:
    uv run python scripts/verify_admerasia_legend.py
"""

import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from browser_automation.parsers.admerasia_traffic import _assign_clusters, _dist  # noqa: E402
from browser_automation.parsers.admerasia_traffic_color import read_color_grid  # noqa: E402
from browser_automation.parsers.admerasia_vision import extract_isci_legend  # noqa: E402

_ORDERS = "/mnt/c/Work Temp/!New/!Orders"
FILES = [
    ("VN Houston", f"{_ORDERS}/TV-MD26-Vietnamese IO-McValue July_Crossing TV_Houston.pdf"),
    ("VN SF",      f"{_ORDERS}/TV-MD26-Vietnamese IO-McValue July_Crossing TV_SF.pdf"),
    ("VN NewYork", f"{_ORDERS}/TV-MD26-Vietnamese IO-McValue July_CrossingTV_NewYork.pdf"),
    ("VN Seattle", f"{_ORDERS}/TV-MD26-Vietnamese IO-McValue July_CrossingTV_Seattle.pdf"),
    ("CN Seattle", f"{_ORDERS}/TV-MD26-Chinese IO-McValue July_Crossing TV - Seattle.pdf"),
    ("FIL LA",     f"{_ORDERS}/TV-MD26-Filipino IO-McValue July_Crossing TV_LA.pdf"),
    ("FIL NewYork", f"{_ORDERS}/TV-MD26-Filipino IO-McValue July_CrossingTV_NewYork.pdf"),
]


def main():
    for name, path in FILES:
        print(f"\n===== {name} =====")
        try:
            cg = read_color_grid(path)
            legend = extract_isci_legend(path).rows
        except Exception as exc:  # noqa: BLE001
            print(f"  ERROR: {type(exc).__name__}: {exc}")
            continue

        print("  vision legend (top->bottom):")
        for r in legend:
            print(f"    {r.isci_code} :{r.duration_sec:<2} {tuple(r.color_rgb)!s:<18} {r.color_name}")

        cluster_isci = _assign_clusters(cg.palette, legend)
        if cluster_isci is None:
            print(f"  !! grid has {len(cg.palette)} colours but legend has {len(legend)} creatives")
            continue

        dur_of = {r.isci_code: r.duration_sec for r in legend}
        per = Counter()
        row_durs = defaultdict(set)
        for c in cg.cells:
            isci = cluster_isci[c.cluster]
            per[isci] += c.count
            row_durs[c.row].add(dur_of.get(isci))
        # one-to-one check
        distinct = len(set(cluster_isci.values())) == len(cluster_isci)
        mixed = [r for r, ds in row_durs.items() if len(ds) > 1]

        print("  palette -> ISCI:")
        for i, cen in enumerate(cg.palette):
            leg = next((r for r in legend if r.isci_code == cluster_isci[i]), None)
            d = _dist(cen, tuple(leg.color_rgb)) if leg else -1
            print(f"    {cen!s:<18} -> {cluster_isci[i]}  (Δcolor {d:.0f})   spots={per[cluster_isci[i]]}")
        print(f"  distinct colour->ISCI: {'OK' if distinct else 'FAIL (collision)'}"
              f"  | duration-coherent rows: {'OK' if not mixed else f'FAIL {mixed}'}"
              f"  | total spots colour-read: {sum(c.count for c in cg.cells)}")


if __name__ == "__main__":
    main()
