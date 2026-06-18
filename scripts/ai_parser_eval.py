"""
AI parser evaluation harness (READ-ONLY — no Etere writes).

Runs the prototype AI extractor (browser_automation/parsers/ai_parser.py) on an
order PDF and, when a trusted deterministic parser exists for that order, diffs
the two so you can eyeball Claude's accuracy before trusting the AI path.

Usage:
    uv run python scripts/ai_parser_eval.py                 # default: LRCCD PDF, compare vs lrccd parser
    uv run python scripts/ai_parser_eval.py "<pdf path>"    # extract only, print the AI lines
    uv run python scripts/ai_parser_eval.py "<pdf>" --compare lrccd

Requires ANTHROPIC_API_KEY. Add it to the project's .env (it is loaded here and
stays out of the terminal/transcript):  ANTHROPIC_API_KEY=sk-ant-...
"""

from __future__ import annotations

import sys
from datetime import date, datetime
from pathlib import Path

# Ensure the repo root is importable when run as a script file (scripts/ is on
# sys.path by default, the repo root is not).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

DEFAULT_PDF = "/mnt/c/Work Temp/!New/!Orders/3FOLD_LRCCD Fall&Spring Enrollment 26-27_AIRTIME_Signed.pdf"


def _load_env() -> None:
    try:
        from dotenv import load_dotenv
        load_dotenv(".env")
        load_dotenv("credentials.env")
    except Exception:
        pass


def _norm_days(s: str) -> str:
    return (s or "").upper().replace(" ", "").replace("–", "-").strip()


def _to_date(v) -> date | None:
    if isinstance(v, date):
        return v
    for fmt in ("%m/%d/%Y", "%m/%d/%y", "%Y-%m-%d"):
        try:
            return datetime.strptime(str(v).strip(), fmt).date()
        except (ValueError, TypeError):
            continue
    return None


def _norm_time(raw: str) -> str:
    """Normalize an air-time window to HH:MM-HH:MM; pass ROS through."""
    if not raw or raw.strip().upper() == "ROS":
        return "ROS"
    try:
        from browser_automation.etere_client import EtereClient
        a, b = EtereClient.parse_time_range(raw)
        return f"{a}-{b}"
    except Exception:
        return raw.strip()


def _core_key(market, language, duration, rate, is_bonus, start, end):
    """Identity of a line independent of how days/time were expressed."""
    return (
        (market or "").upper().strip(),
        (language or "").lower().strip(),
        int(duration),
        round(float(rate), 2),
        bool(is_bonus),
        _to_date(start),
        _to_date(end),
    )


def _ai_lines(order):
    for ln in order.lines:
        yield {
            "key": _core_key(ln.market, ln.language, ln.duration, ln.rate, ln.is_bonus, ln.start_date, ln.end_date),
            "days": _norm_days(ln.days),
            "time": _norm_time(ln.time_range),
            "desc": ln.description,
            "spots": ln.total_spots,
            "rate": ln.rate,
            "is_bonus": ln.is_bonus,
            "week_dates": list(getattr(ln, "week_dates", []) or []),
            "week_spots": list(getattr(ln, "week_spots", []) or []),
            "end_date": ln.end_date,
        }


class _Wk:
    """Shim so AI week dates feed consolidate_weeks (expects .start_date MM/DD/YYYY)."""
    def __init__(self, d):
        self.start_date = d


def _show_consolidation(rows, flight_end):
    """For AI lines that carry a weekly grid, show how consolidate_weeks splits them
    into the separate contract lines that would actually be entered."""
    weekly = [r for r in rows if r["week_dates"] and r["week_spots"]]
    if not weekly:
        return
    from browser_automation.etere_client import EtereClient
    print("\nEntry preview (weekly grids → consolidate_weeks → contract lines):")
    for r in weekly:
        cols = ", ".join(f"{d}:{s}" for d, s in zip(r["week_dates"], r["week_spots"]))
        print(f"  {r['desc'][:46]:<46} weeks[{cols}]")
        try:
            runs = EtereClient.consolidate_weeks(r["week_spots"], [_Wk(d) for d in r["week_dates"]], flight_end=flight_end)
            for run in runs:
                total = run["spots_per_week"] * run["weeks"]
                print(f"      → line: {run['start_date']}–{run['end_date']}  {run['spots_per_week']}/wk × {run['weeks']}wk = {total} spots")
        except Exception as e:
            print(f"      (consolidate_weeks error: {e})")


def _lrccd_lines():
    from browser_automation.parsers.lrccd_parser import parse_lrccd_pdf

    def gen(path):
        doc = parse_lrccd_pdf(path)
        for ln in doc.lines:
            yield {
                "key": _core_key(ln.market, ln.language, ln.duration, ln.rate, ln.is_bonus, ln.start, ln.end),
                "days": _norm_days(ln.days),
                "time": _norm_time(ln.time),
                "desc": ln.description,
                "spots": ln.total_spots,
                "rate": ln.rate,
                "is_bonus": ln.is_bonus,
            }
    return gen


_COMPARATORS = {"lrccd": _lrccd_lines}


def _reconcile(rows):
    total = sum(r["spots"] for r in rows)
    paid = sum(r["spots"] for r in rows if not r["is_bonus"])
    cost = sum(r["rate"] * r["spots"] for r in rows if not r["is_bonus"])
    return total, paid, cost


def main() -> int:
    _load_env()
    import os
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("✗ ANTHROPIC_API_KEY not set.")
        print("  Add it to the project's .env (loaded automatically, stays out of the terminal):")
        print("      ANTHROPIC_API_KEY=sk-ant-...")
        print("  Then re-run this script.")
        return 1

    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    pdf = args[0] if args else DEFAULT_PDF
    compare = "lrccd" if (not args) else None
    if "--compare" in sys.argv:
        compare = sys.argv[sys.argv.index("--compare") + 1]
    if compare in ("none", "off"):
        compare = None

    print(f"\n{'='*72}\nAI extraction: {pdf}\n{'='*72}")
    from browser_automation.parsers.ai_parser import parse_ai_pdf
    order, usage = parse_ai_pdf(pdf)

    print(f"client={order.client!r}  agency={order.agency!r}  markets={order.markets}")
    print(f"flight={order.flight_start} -> {order.flight_end}  rates_are_net={order.rates_are_net}")
    print(f"lines={len(order.lines)}  | tokens in/out={usage['input_tokens']}/{usage['output_tokens']}  est_cost=${usage['est_cost_usd']}")
    if order.warnings:
        print("\nMODEL WARNINGS:")
        for w in order.warnings:
            print(f"  ⚠ {w}")

    ai_rows = list(_ai_lines(order))
    print("\nAI lines:")
    for r in sorted(ai_rows, key=lambda x: (str(x['key'][6]), x['key'][2], x['desc'])):
        tag = "BNS" if r["is_bonus"] else "   "
        print(f"  {tag} :{r['key'][2]:<2} {r['key'][0]:<4} {r['key'][1]:<11} {r['days']:<6} {r['time']:<12} sp={r['spots']:<3} ${r['rate']}")

    a_tot, a_paid, a_cost = _reconcile(ai_rows)
    print(f"\nAI totals: spots={a_tot} paid={a_paid} paid_cost=${a_cost:,.2f}")
    _show_consolidation(ai_rows, order.flight_end)

    if not compare:
        return 0
    if compare not in _COMPARATORS:
        print(f"\n(no comparator '{compare}' — extraction-only)")
        return 0

    # ── Diff against the trusted deterministic parser ─────────────────────
    print(f"\n{'='*72}\nDIFF vs trusted '{compare}' parser\n{'='*72}")
    det_rows = list(_COMPARATORS[compare]()(pdf))
    ai_by = {r["key"]: r for r in ai_rows}
    det_by = {r["key"]: r for r in det_rows}

    matched = sorted(set(ai_by) & set(det_by), key=lambda k: (str(k[6]), k[2], k[1]))
    ai_only = sorted(set(ai_by) - set(det_by), key=lambda k: (str(k[6]), k[2], k[1]))
    det_only = sorted(set(det_by) - set(ai_by), key=lambda k: (str(k[6]), k[2], k[1]))

    print(f"lines: AI={len(ai_rows)}  trusted={len(det_rows)}  matched(core)={len(matched)}")

    days_ok = time_ok = ros_expected = 0
    for k in matched:
        a, d = ai_by[k], det_by[k]
        dmark = "✓" if a["days"] == d["days"] else f"✗ AI={a['days']} det={d['days']}"
        if a["days"] == d["days"]:
            days_ok += 1
        if a["time"] == "ROS" and d["time"] != "ROS":
            tmark = f"~ AI=ROS det={d['time']} (ROS window applied downstream)"
            ros_expected += 1
        elif a["time"] == d["time"]:
            tmark = "✓"
            time_ok += 1
        else:
            tmark = f"✗ AI={a['time']} det={d['time']}"
        if dmark != "✓" or not tmark.startswith("✓"):
            print(f"  {d['desc'][:40]:<40} days:{dmark}  time:{tmark}")

    print(f"\nmatched lines: days agree {days_ok}/{len(matched)}, "
          f"time agree {time_ok}/{len(matched)} ({ros_expected} ROS handled downstream)")

    if ai_only:
        print(f"\nAI produced {len(ai_only)} line(s) with NO trusted match (possible hallucination / mis-read):")
        for k in ai_only:
            print(f"  + {ai_by[k]['desc'][:50]:<50} :{k[2]} {k[0]} ${k[3]} bonus={k[4]} {k[5]}->{k[6]}")
    if det_only:
        print(f"\nTrusted parser had {len(det_only)} line(s) the AI MISSED:")
        for k in det_only:
            print(f"  - {det_by[k]['desc'][:50]:<50} :{k[2]} {k[0]} ${k[3]} bonus={k[4]} {k[5]}->{k[6]}")

    d_tot, d_paid, d_cost = _reconcile(det_rows)
    print("\nReconciliation:")
    print(f"  total spots  AI={a_tot:<5} trusted={d_tot:<5} {'✓' if a_tot==d_tot else '✗'}")
    print(f"  paid spots   AI={a_paid:<5} trusted={d_paid:<5} {'✓' if a_paid==d_paid else '✗'}")
    print(f"  paid cost    AI=${a_cost:<10,.2f} trusted=${d_cost:<10,.2f} {'✓' if abs(a_cost-d_cost)<0.01 else '✗'}")

    perfect = (not ai_only and not det_only and a_tot == d_tot and abs(a_cost - d_cost) < 0.01)
    print(f"\n{'✓ AI extraction matches the trusted parser on all lines + totals.' if perfect else '✗ Differences found above — review before trusting the AI path here.'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
