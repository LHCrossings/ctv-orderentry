"""Report placed media files that are too small to be real or differ between CIBs.

The 2026-09-07 freeze (DAL, WDC, NYC on TheOne090726B: 37 MB for 30:41) is what this
catches. Rules and thresholds live in business_logic/services/media_integrity.py; the
Broadcast Health header runs the same scan nightly and shows findings site-wide.

    uv run python3 scripts/check_media_sizes.py            # today .. today+2
    uv run python3 scripts/check_media_sizes.py --days 7
    uv run python3 scripts/check_media_sizes.py --json

Exit status 1 when anything is flagged, so it can gate a Daily Programming publish.
"""

import argparse
import json
import sys
from pathlib import Path

_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_root))
sys.path.insert(0, str(_root / "browser_automation"))
sys.path.insert(0, str(_root / "src"))

from browser_automation.etere_direct_client import connect  # noqa: E402
from business_logic.services.media_integrity import format_report, scan  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--days", type=int, default=2, help="look-ahead from today (default 2)")
    ap.add_argument("--json", action="store_true", help="print the raw result as JSON")
    a = ap.parse_args()
    with connect() as conn:
        result = scan(conn, days=a.days)
    print(json.dumps(result, indent=2, default=str) if a.json else format_report(result))
    sys.exit(1 if result["findings"] else 0)


if __name__ == "__main__":
    main()
