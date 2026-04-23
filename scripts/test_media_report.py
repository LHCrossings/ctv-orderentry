"""
Interactive test: fetch R100177_C0000_MediaData from Etere and show raw output.
Run on Windows: python scripts/test_media_report.py
"""
import sys
from pathlib import Path

root = Path(__file__).parent.parent
sys.path.insert(0, str(root))
sys.path.insert(0, str(root / "browser_automation"))

from browser_automation.etere_direct_client import ETERE_WEB_URL, etere_web_login, etere_web_logout

CODE_PREFIX = "lexus"

params = {
    "reportCode": "R100177_C0000_MediaData",
    "isSystem":   "True",
    "reportType": "DOWNLOADCSV",
    "customerid": 0,
    "agencyid":   0,
    "filters[0]": CODE_PREFIX,
    "filters[1]": "1",
    "filters[2]": "",
    "filters[3]": "",
    "filters[4]": "",
    "filters[5]": "",
}

print(f"Connecting to {ETERE_WEB_URL} ...")
session = etere_web_login()
try:
    url = f"{ETERE_WEB_URL}/reportsetere/report"
    resp = session.get(url, params=params, timeout=60)
    print(f"HTTP {resp.status_code}  Content-Type: {resp.headers.get('Content-Type')}")
    raw = resp.content
finally:
    etere_web_logout(session)

text = raw.decode("utf-8-sig", errors="replace")

print("\n--- FIRST 800 CHARS OF RESPONSE ---")
print(repr(text[:800]))
print("\n--- FIRST 5 LINES (readable) ---")
for ln in text.splitlines()[:5]:
    print(ln)
