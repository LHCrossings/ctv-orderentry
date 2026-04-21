"""
Fetch the Etere ReportsEtere/IndexTree page and list all available reports,
grouped by IsSystem (True = built-in, False = custom/user-defined).

Usage:
    uv run python scripts/list_etere_reports.py
"""

import re
import sys
from html import unescape
from pathlib import Path

root = Path(__file__).parent.parent
for p in [str(root), str(root / "browser_automation")]:
    if p not in sys.path:
        sys.path.insert(0, p)

from browser_automation.etere_direct_client import (
    ETERE_WEB_URL,
    etere_web_login,
    etere_web_logout,
)

_LI_RE = re.compile(
    r'<li[^>]+id="([^"]+)"[^>]+data-obj-rsystem="([^"]+)"[^>]*>(.*?)</li>',
    re.DOTALL | re.IGNORECASE,
)
_TAG_RE = re.compile(r"<[^>]+>")


def list_reports():
    session = etere_web_login()
    try:
        url = f"{ETERE_WEB_URL}/ReportsEtere/IndexTree"
        resp = session.get(url, timeout=30)
        resp.raise_for_status()
        html = resp.text
    finally:
        etere_web_logout(session)

    system_reports = []
    custom_reports = []

    for m in _LI_RE.finditer(html):
        rdl_file = m.group(1).strip()
        is_system = m.group(2).strip()
        title = unescape(_TAG_RE.sub("", m.group(3))).strip()
        entry = (rdl_file, title)
        if is_system.lower() == "true":
            system_reports.append(entry)
        else:
            custom_reports.append(entry)

    print(f"\n{'='*60}")
    print(f"CUSTOM REPORTS ({len(custom_reports)} found)")
    print(f"{'='*60}")
    if custom_reports:
        for rdl, title in sorted(custom_reports, key=lambda x: x[1].lower()):
            print(f"  {rdl:<50}  {title}")
    else:
        print("  (none)")

    print(f"\n{'='*60}")
    print(f"SYSTEM REPORTS ({len(system_reports)} found)")
    print(f"{'='*60}")
    for rdl, title in sorted(system_reports, key=lambda x: x[1].lower()):
        print(f"  {rdl:<50}  {title}")

    print()


if __name__ == "__main__":
    list_reports()
