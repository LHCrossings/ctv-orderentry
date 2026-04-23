"""
Interactive test: probe R100177_C0000_MediaData variants.
Run: python scripts/test_media_report.py
"""
import sys
from pathlib import Path

root = Path(__file__).parent.parent
sys.path.insert(0, str(root))
sys.path.insert(0, str(root / "browser_automation"))

from browser_automation.etere_direct_client import ETERE_WEB_URL, etere_web_login, etere_web_logout

CODE_PREFIX = "lexus"

VARIANTS = [
    {"isSystem": "False", "reportCode": "R100177_C0000_MediaData"},
    {"isSystem": "True",  "reportCode": "R100177_C0000_MediaData"},
    {"isSystem": "false", "reportCode": "R100177_C0000_MediaData"},
    {"isSystem": "true",  "reportCode": "R100177_C0000_MediaData"},
    {"isSystem": "False", "reportCode": "R100177_C0000_mediadata"},
]

session = etere_web_login()
try:
    for v in VARIANTS:
        params = {
            "reportCode": v["reportCode"],
            "isSystem":   v["isSystem"],
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
        url = f"{ETERE_WEB_URL}/reportsetere/report"
        resp = session.get(url, params=params, timeout=30)
        text = resp.content.decode("utf-8-sig", errors="replace")
        is_html = text.lstrip().startswith("<")
        snippet = text[:80].replace("\r\n", " ").replace("\n", " ")
        print(f"isSystem={v['isSystem']:6s} code={v['reportCode']:30s} -> {resp.status_code} html={is_html} | {snippet!r}")
finally:
    etere_web_logout(session)
