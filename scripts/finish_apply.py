"""Fill & Finish CLI — thin wrapper around finish_service.apply_window().

uv run python3 scripts/finish_apply.py --market 6 --date 2026-08-28 --hour 8          # dry run (rollback)
uv run python3 scripts/finish_apply.py --market 6 --date 2026-08-28 --hour 8 --apply
"""

import argparse
import sys

sys.path.insert(0, ".")
from browser_automation.etere_direct_client import connect  # noqa: E402
from src.business_logic.services.finish_service import apply_window  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--market", type=int, required=True)
    ap.add_argument("--date", required=True)
    ap.add_argument("--hour", type=int, required=True)
    ap.add_argument("--minutes", type=int, default=60)
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    lo = a.hour * 3600.0
    r = apply_window(connect(), a.market, a.date, lo, lo + a.minutes * 60, a.apply)
    print(f"\n{r['status'].upper()}" + (f": {r['message']}" if r.get("message") else ""))
    return 0 if r["status"] in ("applied", "dry-run", "finished") else 1


if __name__ == "__main__":
    sys.exit(main())
