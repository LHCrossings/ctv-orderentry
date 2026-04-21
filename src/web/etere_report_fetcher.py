"""
Fetch reports from Etere web headlessly using an authenticated requests.Session.
Reuses the same login mechanism as block refresh.
"""

import sys
from datetime import date
from pathlib import Path


def _ensure_path():
    root = Path(__file__).parent.parent.parent
    for p in [str(root), str(root / "browser_automation")]:
        if p not in sys.path:
            sys.path.insert(0, p)


def fetch_etere_report(
    contract_number,
    report_code: str = "R100018_C18236_new_pc_with_contract_no",
    is_system: str = "False",
    print_times: bool = True,
    use_date_range: bool = False,
    start_date: str = None,
    end_date: str = None,
    customer_id: int = 0,
    agency_id: int = 0,
) -> bytes:
    """
    Fetch an Etere report by contract number and return raw response bytes.

    Logs in headlessly, GETs the report endpoint, logs out.
    The response is typically CSV (reportType=DOWNLOADCSV).

    Args:
        contract_number: Etere contract ID (shown in web UI URL)
        report_code:     Report filename key, e.g. "R100018_C0000_placement_confirmation"
        print_times:     filters[2] — "Print scheduled times?" checkbox
        use_date_range:  filters[3] — restrict report to a date range
        start_date:      filters[4] — M/D/YYYY, defaults to today
        end_date:        filters[5] — M/D/YYYY, defaults to today
        customer_id:     filter by customer (0 = all)
        agency_id:       filter by agency (0 = all)
    """
    _ensure_path()
    from browser_automation.etere_direct_client import (  # noqa: E402
        ETERE_WEB_URL,
        etere_web_login,
        etere_web_logout,
    )

    _d = date.today()
    today = f"{_d.month}/{_d.day}/{_d.year}"
    params = {
        "reportCode":  report_code,
        "isSystem":    is_system,
        "reportType":  "DOWNLOADCSV",
        "customerid":  customer_id,
        "agencyid":    agency_id,
        "filters[0]":  str(contract_number),
        "filters[1]":  "",
        "filters[2]":  "true" if print_times else "false",
        "filters[3]":  "true" if use_date_range else "false",
        "filters[4]":  start_date or today,
        "filters[5]":  end_date or today,
    }

    session = etere_web_login()
    try:
        url = f"{ETERE_WEB_URL}/reportsetere/report"
        resp = session.get(url, params=params, timeout=60)
        resp.raise_for_status()
        return resp.content
    finally:
        etere_web_logout(session)
