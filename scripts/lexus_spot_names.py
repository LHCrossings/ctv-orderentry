#!/usr/bin/env python3
"""Propose Etere names for a batch of Lexus (IW Group) spot deliveries.

    uv run python3 scripts/lexus_spot_names.py <.mov files | a folder of them | traffic sheet PDFs>...
        [--next-serial N] [--csv out.csv]

Reads the next free LEXUS serial and the ISCIs already in FILMATI from Etere, then prints
one row per ISCI: code (the ISCI), proposed description, duration, language, tag. Nothing
is written; Lee edits the list and names the assets at ingest. Rule and worked examples:
browser_automation/parsers/lexus_spot_names.py.
"""
import argparse
import csv
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from browser_automation.parsers.lexus_spot_names import (  # noqa: E402
    DeliveredSpot, next_lexus_serial, parse_delivery_name, propose_names,
)


def _spots_from_inputs(paths):
    spots, skipped = [], []
    for raw in paths:
        p = Path(raw)
        files = sorted(p.iterdir()) if p.is_dir() else [p]
        for f in files:
            if f.suffix.lower() == ".pdf":
                from browser_automation.parsers.lexus_traffic_parser import parse_lexus_traffic_pdf
                instr = parse_lexus_traffic_pdf(f.read_bytes())
                for period in instr.periods:
                    for s in period.spots:
                        d = parse_delivery_name(
                            f"{s.isci}_{s.title}", sheet_language=s.language, market=instr.market_code
                        )
                        if d is None:
                            skipped.append(f"{f.name}: {s.isci} {s.title}")
                            continue
                        d.source = f.name
                        spots.append(d)
            elif f.suffix.lower() in (".mov", ".mp4", ".mxf"):
                d = parse_delivery_name(f.name)
                if d is None:
                    skipped.append(f.name)
                else:
                    spots.append(d)
    return spots, skipped


def _library(iscis):
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    from browser_automation.etere_direct_client import connect
    with connect() as conn:
        cur = conn.cursor()
        cur.execute("SELECT DESCRIZIO FROM FILMATI WHERE DESCRIZIO LIKE 'LEXUS%'")
        nxt = next_lexus_serial(r[0] for r in cur.fetchall())
        existing = {}
        if iscis:
            cur.execute(
                "SELECT COD_PROGRA, DESCRIZIO FROM FILMATI WHERE COD_PROGRA IN (%s)"
                % ",".join("'%s'" % i for i in sorted(iscis))
            )
            existing = {r[0].strip(): (r[1] or "").strip() for r in cur.fetchall()}
    return nxt, existing


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("paths", nargs="+")
    ap.add_argument("--next-serial", type=int, help="override the serial read from FILMATI")
    ap.add_argument("--csv", help="also write the proposal here")
    ap.add_argument("--no-db", action="store_true", help="skip Etere (requires --next-serial)")
    ap.add_argument("--month", help="keep only this month's deliveries (e.g. Nov) — December's spots arrive later")
    args = ap.parse_args()

    spots, skipped = _spots_from_inputs(args.paths)
    if args.month:
        spots = [s for s in spots if s.month.lower().startswith(args.month.lower()[:3])]
    if not spots:
        print("no IW Group deliveries recognised"); sys.exit(1)
    iscis = {s.isci for s in spots if s.isci}
    if args.no_db:
        if not args.next_serial:
            print("--no-db needs --next-serial"); sys.exit(2)
        nxt, existing = args.next_serial, {}
    else:
        nxt, existing = _library(iscis)
        if args.next_serial:
            nxt = args.next_serial
    rows = propose_names(spots, existing, nxt)

    print(f"next serial {nxt}; {len(rows)} ISCIs ({sum(r.existing for r in rows)} already in the library)")
    print(f"{'serial':>6}  {'code':<14} {'dur':>4}  description")
    for r in rows:
        flag = "  [exists]" if r.existing else (f"  [{r.note}]" if r.note else "")
        print(f"{(r.serial or ''):>6}  {r.code:<14} :{r.duration_sec:<3}  {r.description}{flag}")
    for s in skipped:
        print(f"  skipped (not an IW delivery name): {s}")
    if args.csv:
        with open(args.csv, "w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["serial", "code", "duration", "description", "tag", "existing", "note"])
            for r in rows:
                w.writerow([r.serial, r.code, r.duration_sec, r.description, r.tag, r.existing, r.note])
        print(f"wrote {args.csv}")


if __name__ == "__main__":
    main()
