"""
Reconcile a Master Billing Sheet: every affidavit tab's Station Net rows must appear in the
CLEANED tab, row for row, and nothing on CLEANED flagged Affidavit=Y may be without a tab.

    uv run python3 scripts/check_billing_book.py "/mnt/c/Work Temp/Billing/Master Billing Sheet 2609.xlsm" \
        --exclude "Imprenta PGE- TSQ3 MAIL THIS"

An affidavit tab is any sheet whose D1 holds an invoice number (dddd-ddd); SCRATCH, MASTER,
CLEANED, the SAMPLE tabs and the NKB templates have none. Rows are matched as a multiset on
(Bill Code, air date, aired time, media, net) so duplicates (Tatari rows) count exactly.

Two rounding checks run per tab as well (Lee, 2610: "make sure there are no rounding issues"):
a Station Net that is not a whole number of cents (Etere's rounded gross x 0.85 = 100.0025)
is listed with the cents the tab's raw sum drifts from its 2-decimal sum, and the footer's
"Net Amount Due" (computed from the gross column) is compared with the sum of the Station Net
rows, which is what CLEANED carries. Neither is a tab-vs-CLEANED mismatch; both are printed
for the operator to decide.
"""

from __future__ import annotations

import argparse
import datetime as dt
import re
from collections import Counter
from decimal import ROUND_HALF_UP, Decimal

import openpyxl

INVOICE_RE = re.compile(r"^\d{4}-\d{3}$")
AFF_NET_HEADERS = ("Station Net", "Net")


def _money(v) -> Decimal | None:
    if v is None or v == "":
        return None
    try:
        return Decimal(str(v)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except Exception:
        return None


def _day(v):
    return v.date() if isinstance(v, dt.datetime) else v


def _key(row) -> tuple:
    # Bill Code, Air Date, Aired (actual time), Media, Net
    return (
        row[0],
        _day(row[1]),
        str(row[8]) if row[8] is not None else None,
        row[7],
        _money(row[21]),
    )


def _raw(v) -> Decimal | None:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    return Decimal(repr(v))


def _is_subcent(v) -> bool:
    d = _raw(v)
    if d is None:
        return False
    # beyond float noise (60.00000000000001 is a whole number of cents)
    return abs(d - d.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)) > Decimal("0.000001")


def _contract_no(rows) -> str | None:
    """The tab header's Contract Number (col L of that row), as text."""
    for r in rows[:16]:
        if r and len(r) > 11 and r[8] == "Contract Number" and r[11] not in (None, ""):
            return str(r[11]).strip()
    return None


def _footer_net(rows):
    """The tab's printed "Net Amount Due" (col P of that row), or None."""
    for r in rows:
        if r and len(r) > 15 and r[8] == "Net Amount Due":
            return _money(r[15])
    return None


def _header_row(rows, limit=30):
    for i, r in enumerate(rows[:limit]):
        if r and len(r) > 21 and r[21] in AFF_NET_HEADERS and r[0] == "Bill Code":
            return i
    return None


def _data_rows(rows, hdr_i):
    return [r for r in rows[hdr_i + 1 :] if r and r[0] not in (None, "") and len(r) > 21]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("book")
    ap.add_argument(
        "--exclude", action="append", default=[], help="affidavit tab left out on purpose"
    )
    ap.add_argument("--cleaned", default="CLEANED")
    args = ap.parse_args()

    wb = openpyxl.load_workbook(args.book, read_only=True, data_only=True)
    sheets = {ws.title: list(ws.iter_rows(values_only=True)) for ws in wb.worksheets}

    cleaned_rows = sheets[args.cleaned]
    c_hdr = _header_row(cleaned_rows)
    cleaned = _data_rows(cleaned_rows, c_hdr)
    cleaned_pool = Counter(_key(r) for r in cleaned)
    cleaned_net = sum((_money(r[21]) or Decimal(0)) for r in cleaned)

    tabs = []
    for name, rows in sheets.items():
        if name in (args.cleaned, "MASTER", "SCRATCH"):
            continue
        inv = rows[0][3] if rows and rows[0] and len(rows[0]) > 3 else None
        if not (isinstance(inv, str) and INVOICE_RE.match(inv.strip())):
            continue
        hdr_i = _header_row(rows)
        if hdr_i is None:
            print(f"!! {name}: invoice {inv} but no Bill Code / Station Net header found")
            continue
        tabs.append(
            (name, inv.strip(), _data_rows(rows, hdr_i), _footer_net(rows), _contract_no(rows))
        )

    month = Counter(inv[:4] for _, inv, _, _, _ in tabs).most_common(1)[0][0]
    excluded = set(args.exclude)
    problems = 0
    aff_total = Decimal(0)
    print(f"book: {args.book}")
    print(f"CLEANED: {len(cleaned):,} rows, net {cleaned_net:,.2f}")
    print(f"{len(tabs)} affidavit tabs; excluded on purpose: {sorted(excluded) or 'none'}\n")
    print(f"{'tab':34} {'invoice':9} {'rows':>5} {'tab net':>11} {'in CLEANED':>11}  note")
    rounding = []  # (tab, invoice, sub-cent rows, raw - rounded)
    footer_off = []  # (tab, invoice, rows net, footer net)
    respread = []  # (tab, invoice, contract, net): rows re-spread by market on CLEANED
    for name, inv, rows, footer, contract in tabs:
        net = sum((_money(r[21]) or Decimal(0)) for r in rows)
        subcent = [r for r in rows if _is_subcent(r[21])]
        raw_net = sum((_raw(r[21]) or Decimal(0)) for r in rows)
        drift = (raw_net - net).quantize(Decimal("0.0001"))
        if subcent and drift != 0:  # thirds that cancel (155.83 x 4 + 311.67 x 4) are fine
            rounding.append((name, inv, len(subcent), drift))
        if footer is not None and footer != net:
            footer_off.append((name, inv, net, footer))
        if name in excluded:
            print(f"{name:34} {inv:9} {len(rows):5} {net:11,.2f} {'(excluded)':>11}")
            continue
        aff_total += net
        matched_net = Decimal(0)
        missing = []
        for r in rows:
            k = _key(r)
            if cleaned_pool[k] > 0:
                cleaned_pool[k] -= 1
                matched_net += k[4] or Decimal(0)
            else:
                missing.append(k)
        note = ""
        if inv[:4] != month:
            note += f"invoice is not a {month} number; "
        if missing and len(missing) == len(rows) and contract:
            # Lee (11/2): a production/services tab lists what the client ordered; CLEANED
            # carries the same money as one line per market. Same contract, same net = match.
            twins = [
                r for r in cleaned if str(r[27]).strip() == contract and cleaned_pool[_key(r)] > 0
            ]
            twin_net = sum((_money(r[21]) or Decimal(0)) for r in twins)
            if twins and twin_net == net:
                for r in twins:
                    cleaned_pool[_key(r)] -= 1
                matched_net = net
                respread.append((name, inv, contract, net, len(twins)))
                note += f"re-spread on CLEANED as {len(twins)} row(s) by market, same net"
                missing = []
        if missing:
            problems += 1
            note += f"{len(missing)} row(s) NOT in CLEANED (net {sum((m[4] or Decimal(0)) for m in missing):,.2f})"
        print(f"{name:34} {inv:9} {len(rows):5} {net:11,.2f} {matched_net:11,.2f}  {note}")
        for m in missing[:5]:
            print(f"      missing: {m}")
        if len(missing) > 5:
            print(f"      ... {len(missing) - 5} more")

    # CLEANED rows no affidavit tab accounted for
    leftover = [(k, n) for k, n in cleaned_pool.items() if n > 0]
    left_rows = {}
    for r in cleaned:
        k = _key(r)
        if cleaned_pool.get(k, 0) > 0 and len(left_rows.get(k, [])) < cleaned_pool[k]:
            left_rows.setdefault(k, []).append(r)
    flagged_y = [r for rs in left_rows.values() for r in rs if str(r[26]).upper() == "Y"]
    flagged_n = [r for rs in left_rows.values() for r in rs if str(r[26]).upper() != "Y"]
    left_net_y = sum((_money(r[21]) or Decimal(0)) for r in flagged_y)
    left_net_n = sum((_money(r[21]) or Decimal(0)) for r in flagged_n)

    print(f"\naffidavit tabs total (excluding {len(excluded)}): {aff_total:,.2f}")
    print(f"CLEANED total:                              {cleaned_net:,.2f}")
    print(f"difference (CLEANED - tabs):                {cleaned_net - aff_total:,.2f}")
    print(
        f"CLEANED rows matched by no tab: {sum(n for _, n in leftover)} "
        f"(Affidavit=Y: {len(flagged_y)} rows, net {left_net_y:,.2f}; "
        f"Affidavit=N: {len(flagged_n)} rows, net {left_net_n:,.2f})"
    )
    if flagged_n:
        print("  Affidavit=N rows (expected house / infomercial / broker lines):")
        for r in flagged_n[:20]:
            print(f"    {r[0]!s:45} {r[7]!s:45} net {_money(r[21])}")
    if flagged_y:
        problems += 1
        print("  !! Affidavit=Y rows with NO affidavit tab:")
        by_code = Counter((r[0], r[27]) for r in flagged_y)
        for (code, contract), n in by_code.most_common(30):
            print(f"    {code!s:45} contract {contract!s:8} {n} row(s)")
    if rounding:
        print("\nSub-cent Station Nets (raw sum vs the 2-decimal sum the rows display):")
        for name, inv, n, drift in rounding:
            print(f"  {name:34} {inv:9} {n:4} row(s)  raw - rounded = {drift:+.4f}")
    if footer_off:
        print("\nTabs whose printed Net Amount Due differs from the sum of their Station Net rows:")
        for name, inv, net, footer in footer_off:
            print(
                f"  {name:34} {inv:9} rows {net:11,.2f}  footer {footer:11,.2f}  ({footer - net:+,.2f})"
            )
    print(
        f"\nRESULT: {'OK — every affidavit row is on CLEANED exactly once' if not problems else f'{problems} problem group(s) above'}"
    )
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
