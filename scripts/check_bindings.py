"""Find (and optionally fix) placed rows whose playout binding names a file the asset
does not have — the NYC 9/2/2026 black screen. The rule and the SQL live in
`src/business_logic/services/playout_binding.py`, shared with the rename-programming
tool and the Broadcast Health nightly scan (header dot goes amber, report-only).

    uv run python3 scripts/check_bindings.py            # report next 7 days
    uv run python3 scripts/check_bindings.py --days 3
    uv run python3 scripts/check_bindings.py --fix      # rebind, verified, restore SQL written
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "browser_automation"))

from browser_automation.etere_direct_client import connect  # noqa: E402
from src.business_logic.services import playout_binding as pb  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--days", type=int, default=7, help="look-ahead from today (default 7)")
    ap.add_argument("--fix", action="store_true", help="rebind mismatched unaired rows")
    a = ap.parse_args()
    conn = connect()
    result = pb.scan(conn, days=a.days)
    print(pb.format_report(result, a.days))
    if not result["count"] or not a.fix:
        if result["count"]:
            print("DRY RUN — add --fix to rebind")
        return
    try:
        n, rpath, _ = pb.rebind(conn, days=a.days)
    except Exception as exc:  # noqa: BLE001
        sys.exit(f"ROLLED BACK: {exc}")
    print(f"REBOUND {n} row(s); restore: {rpath}")


if __name__ == "__main__":
    main()
