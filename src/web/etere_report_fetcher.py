"""
Fetch reports from Etere web headlessly using an authenticated requests.Session.
Reuses the same login mechanism as block refresh.
"""

import csv
import io
import logging
import sys
from datetime import date
from pathlib import Path

log = logging.getLogger(__name__)


def _ensure_path():
    root = Path(__file__).parent.parent.parent
    for p in [str(root), str(root / "browser_automation")]:
        if p not in sys.path:
            sys.path.insert(0, p)


def _enrich_bookingcode(csv_bytes: bytes, contract_number) -> bytes:
    """
    Replace blank / 'NEED COPY' values in the bookingcode2 column with the
    actual spot code (FILMATI.COD_PROGRA) looked up via TPALINSE.

    The placement CSV has 3 header rows before the column-name row (row index 3).
    Match key: (id_contrattirighe, dateschedule, airtimep) — all three present
    per row, making each airing uniquely identifiable.

    Silently skips enrichment on any DB error so the raw CSV is still returned.
    """
    try:
        contract_id = int(str(contract_number).strip())
    except (ValueError, TypeError):
        return csv_bytes  # non-numeric contract ref — skip enrichment

    try:
        _ensure_path()
        from browser_automation.etere_direct_client import connect as _db_connect  # noqa: E402

        with _db_connect() as conn:
            cur = conn.cursor(as_dict=True)
            cur.execute("""
                SELECT
                    tpa.id_contrattirighe                                        AS line_id,
                    CONVERT(VARCHAR(10), tp.DATA, 101)                           AS air_date,
                    CONVERT(VARCHAR(8),  DATEADD(SECOND, tp.ORA/30, 0), 108)    AS air_time,
                    f.COD_PROGRA                                                 AS spot_code
                FROM TPALINSE tp
                JOIN trafficPalinse tpa ON tpa.id_tpalinse      = tp.ID_TPALINSE
                JOIN CONTRATTIRIGHE cr  ON cr.ID_CONTRATTIRIGHE = tpa.id_contrattirighe
                LEFT JOIN FILMATI f     ON f.ID_FILMATI = tp.ID_FILMATI
                WHERE cr.ID_CONTRATTITESTATA = %d
                  AND f.COD_PROGRA IS NOT NULL
                  AND f.COD_PROGRA != ''
            """ % contract_id)
            rows = cur.fetchall()

        # Build lookup: (str(line_id), air_date, air_time) → spot_code
        lookup: dict[tuple, str] = {}
        for r in rows:
            key = (str(r["line_id"]), r["air_date"], r["air_time"])
            lookup[key] = r["spot_code"]

        if not lookup:
            return csv_bytes

    except Exception as exc:
        log.warning("bookingcode enrichment skipped — DB error: %s", exc)
        return csv_bytes

    # Parse and re-write the CSV preserving the 3-row preamble
    text = csv_bytes.decode("utf-8-sig", errors="replace")
    lines = text.splitlines(keepends=True)

    # Find the data header row (contains 'id_contrattirighe')
    header_idx = next(
        (i for i, ln in enumerate(lines) if "id_contrattirighe" in ln.lower()),
        None,
    )
    if header_idx is None:
        return csv_bytes

    preamble = lines[: header_idx]
    data_block = "".join(lines[header_idx:])

    reader = csv.DictReader(io.StringIO(data_block))
    if "bookingcode2" not in (reader.fieldnames or []):
        return csv_bytes

    enriched_rows = []
    for row in reader:
        line_id  = str(row.get("id_contrattirighe", "")).strip()
        air_date = str(row.get("dateschedule", "")).strip()
        air_time = str(row.get("airtimep", "")).strip()
        current  = str(row.get("bookingcode2", "")).strip()

        if current in ("", "NEED COPY"):
            spot = lookup.get((line_id, air_date, air_time))
            if spot:
                row["bookingcode2"] = spot

        enriched_rows.append(row)

    if not enriched_rows:
        return csv_bytes

    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=reader.fieldnames, lineterminator="\r\n")
    writer.writeheader()
    writer.writerows(enriched_rows)

    result = "".join(preamble) + out.getvalue()
    return result.encode("utf-8-sig")


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

    After fetching, enriches the bookingcode2 column from TPALINSE so that
    column H (Media) in the Run Sheet tab is always populated.

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
        resp = session.get(url, params=params, timeout=180)
        resp.raise_for_status()
        csv_bytes = resp.content
    finally:
        etere_web_logout(session)

    # Enrich bookingcode2 (→ column H "Media" in Run Sheet) from DB
    return _enrich_bookingcode(csv_bytes, contract_number)
