"""
Test HTTP block refresh on an existing contract without re-entering the order.

Usage (from project root, Windows):
    python scripts/test_block_refresh.py 2646
"""

import sys
from pathlib import Path

_root = Path(__file__).parent.parent
sys.path.insert(0, str(_root))
sys.path.insert(0, str(_root / "browser_automation"))

CONTRACT_ID = int(sys.argv[1]) if len(sys.argv) > 1 else 2646

from browser_automation.etere_session import EtereSession
from browser_automation.etere_client import EtereClient
from browser_automation.etere_direct_client import EtereDirectClient, connect as db_connect

with EtereSession() as session:
    etere = EtereClient(session.driver)

    # Extract authenticated session cookies from the live browser
    session_cookies = {c['name']: c['value'] for c in session.driver.get_cookies()}
    print(f"[TEST] Captured {len(session_cookies)} session cookies")

    with db_connect() as conn:
        direct = EtereDirectClient(conn)
        direct.set_session_cookies(session_cookies)

        all_ids = direct.get_all_line_ids(CONTRACT_ID)
        print(f"[TEST] Contract {CONTRACT_ID}: {len(all_ids)} lines — {all_ids}")

        ok = 0
        for idx, line_id in enumerate(all_ids, 1):
            print(f"\n[TEST] {idx}/{len(all_ids)}: line {line_id}")
            count = direct.assign_blocks_for_existing_line(line_id)
            if count >= 0:
                ok += 1

    print(f"\n[TEST] Done — {ok}/{len(all_ids)} lines succeeded")
