"""
Download a placement confirmation report from Etere and run ReportSort.

Three modes:

  Worldlink batch (default) — every Worldlink (agency 133) booking in the date
  range, split into one file per booking code, written to K:\\!Archives:

      uv run python scripts/run_reportsort.py post 04/13/2026 04/19/2026
      uv run python scripts/run_reportsort.py pre  04/20/2026 04/26/2026

  Single contract — one contract for any agency/advertiser, written to an
  explicit output folder (the web UI hands it a temp dir and serves the file):

      uv run python scripts/run_reportsort.py post 08/01/2026 08/31/2026 \\
          --contract-id 2999 --contract-code "Admerasia McD 11SE 2608" \\
          --output-folder /tmp/pull

  Agency — every contract of one agency (any ANAGRAF agency id) in the range,
  one workbook per contract, written to an explicit output folder. The report
  is pulled per calendar month (Etere's fixed ~70 s per call makes a year
  ~12 calls) and the chunks are concatenated; the agency's own contract codes
  from CONTRATTITESTATA are the exact allow-list handed to ReportSort:

      uv run python scripts/run_reportsort.py post 01/01/2026 06/30/2026 \\
          --agency-id 203 --output-folder /tmp/pull

All modes use the same CTV/TAC Pre/Post templates in ReportSort/.
"""

import argparse
import calendar
import csv
import io
import os
import subprocess
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from browser_automation.etere_direct_client import ETERE_WEB_URL, etere_web_login, etere_web_logout

AGENCY_ID = 133
REPORTSORT_DIR = Path(__file__).parent.parent.parent / "ReportSort"
INPUT_CSV = REPORTSORT_DIR / "input" / "placement-confirmation.csv"
MAIN_PY = REPORTSORT_DIR / "main.py"

# K: on Windows, the SMB mount elsewhere; override via K_ARCHIVES_ROOT.
_ARCHIVES_ROOT = Path(
    os.environ.get(
        "K_ARCHIVES_ROOT",
        r"K:\!Archives" if sys.platform == "win32" else "/mnt/k/!Archives",
    )
)
POST_LOG_BASE = _ARCHIVES_ROOT / "Post Logs"
PRE_LOG_BASE = _ARCHIVES_ROOT / "Pre Logs"


def parse_date(date_str: str) -> datetime:
    """Parse MM/DD/YYYY, M/DD/YYYY, MM/DD, or M/DD — fills current year if missing."""
    date_str = date_str.strip()
    for fmt in ("%m/%d/%Y", "%m/%d"):
        try:
            dt = datetime.strptime(date_str, fmt)
            if fmt == "%m/%d":
                dt = dt.replace(year=datetime.now().year)
            return dt
        except ValueError:
            continue
    raise ValueError(f"Unrecognized date format: {date_str!r}")


def build_output_folder(log_type: str, date_from: str, date_to: str) -> Path:
    """Return the network output folder.
    Post logs: K:\\!Archives\\Post Logs\\yymmdd  (start date)
    Pre logs:  K:\\!Archives\\Pre Logs\\mmdd-mmdd (from-to)
    """
    if log_type == "post":
        dt = parse_date(date_from)
        folder_name = dt.strftime("%y%m%d")
        return POST_LOG_BASE / folder_name
    else:
        dt_from = parse_date(date_from)
        dt_to = parse_date(date_to)
        folder_name = f"{dt_from.strftime('%m%d')}-{dt_to.strftime('%m%d')}"
        return PRE_LOG_BASE / folder_name


def set_master_market(session, coduser: int) -> None:
    """Set the Etere master market (station) for this session.

    Must be called before downloading any report that filters by market.
    For Worldlink/TAC reports, coduser=10 (Dallas) is required or TAC spots
    will be excluded from results.
    """
    url = f"{ETERE_WEB_URL}/StationS/Save"
    resp = session.post(url, data={"coduser": coduser}, timeout=15)
    resp.raise_for_status()
    print(f"[MARKET] Master market set to coduser={coduser}")


def month_chunks(start: date, end: date) -> list[tuple[date, date]]:
    """Split [start, end] into calendar-month pieces (first and last clipped)."""
    out = []
    cur = start
    while cur <= end:
        last = date(cur.year, cur.month, calendar.monthrange(cur.year, cur.month)[1])
        out.append((cur, min(last, end)))
        cur = last + timedelta(days=1)
    return out


HEADER_ROWS = 4  # ReportSort: rows 0-2 report banner, row 3 column names, data from row 4


def concat_report_csvs(parts: list[bytes]) -> bytes:
    """Join per-chunk placement-confirmation CSVs into one: the 4 header rows of
    the first chunk, then every chunk's data rows (its own header rows dropped).
    Parsed as CSV, not split on newlines — a quoted field may span lines. Report
    footer rows ride along; the caller's exact code list filters them out."""
    rows: list[list[str]] = []
    for blob in parts:
        chunk = list(csv.reader(io.StringIO(blob.decode("utf-8-sig"), newline="")))
        if len(chunk) <= HEADER_ROWS:
            continue  # nothing in that month
        if not rows:
            rows.extend(chunk[:HEADER_ROWS])
        rows.extend(chunk[HEADER_ROWS:])
    if not rows:
        return b""
    buf = io.StringIO(newline="")
    csv.writer(buf, lineterminator="\r\n").writerows(rows)
    return buf.getvalue().encode("utf-8")


def agency_contract_codes(agency_id: int, start: date, end: date) -> list[str]:
    """COD_CONTRATTO of every contract of the agency whose flight overlaps the range."""
    from browser_automation.etere_direct_client import connect

    with connect() as conn:
        cur = conn.cursor()
        cur.execute(
            """SELECT RTRIM(COD_CONTRATTO) FROM CONTRATTITESTATA
               WHERE AGENZIA = %s AND COD_CONTRATTO IS NOT NULL
                 AND DATA_INIZIO <= %s AND DATA_TERMINE >= %s
               ORDER BY COD_CONTRATTO""",
            (agency_id, end, start),
        )
        return [r[0] for r in cur.fetchall() if r[0]]


def download_report(
    session,
    date_from: str,
    date_to: str,
    csv_path: Path,
    contract_id: int | None = None,
    agency_id: int = AGENCY_ID,
) -> None:
    """Download placement confirmation CSV from Etere.

    filters[0] is the contract filter and filters[1] the agency filter; the
    Worldlink batch fills the agency and leaves the contract blank, a single
    contract pull does the reverse.
    """
    if contract_id:
        agency_param, filter0, filter1 = 0, str(contract_id), ""
        scope = f"contract {contract_id}"
    else:
        agency_param, filter0, filter1 = agency_id, "", str(agency_id)
        scope = f"agency {agency_id}"

    url = (
        f"{ETERE_WEB_URL}/reportsetere/report"
        f"?reportCode=R100018_C0000_placement_confirmation"
        f"&isSystem=True"
        f"&reportType=DOWNLOADCSV"
        f"&customerid=0"
        f"&agencyid={agency_param}"
        f"&filters[0]={filter0}"
        f"&filters[1]={filter1}"
        f"&filters[2]=false"
        f"&filters[3]=true"
        f"&filters[4]={date_from}"
        f"&filters[5]={date_to}"
    )
    print(f"[INFO] Downloading report for {scope} ({date_from} to {date_to}) ...")
    # Etere generates this report on demand with a ~70s FIXED cost before data
    # size even matters (measured 2026-08-24: 1 day = 72s, a full week = 82s),
    # so the old 120s timeout died on any Etere load spike. Allow 10 minutes,
    # and retry once — the GET is read-only, so a retry is always safe.
    try:
        resp = session.get(url, timeout=600)
    except requests.exceptions.Timeout:
        print("[WARN] Report request timed out after 600s — retrying once ...")
        resp = session.get(url, timeout=600)
    resp.raise_for_status()

    content_type = resp.headers.get("Content-Type", "")
    if "text/html" in content_type:
        raise RuntimeError(
            "Got HTML instead of CSV - session may have expired or report returned an error page."
        )

    csv_path.parent.mkdir(parents=True, exist_ok=True)
    csv_path.write_bytes(resp.content)
    size_kb = len(resp.content) / 1024
    print(f"[INFO] Saved {size_kb:.1f} KB to {csv_path}")


def download_agency_report(session, agency_id: int, start: date, end: date, csv_path: Path) -> None:
    """Agency pull in calendar-month chunks, concatenated into csv_path."""
    chunks = month_chunks(start, end)
    parts: list[bytes] = []
    for k, (lo, hi) in enumerate(chunks, start=1):
        print(f"[INFO] chunk {k}/{len(chunks)}: {lo:%m/%d/%Y} - {hi:%m/%d/%Y}")
        part = csv_path.with_name(f"chunk-{k:02d}.csv")
        download_report(session, f"{lo:%m/%d/%Y}", f"{hi:%m/%d/%Y}", part, agency_id=agency_id)
        parts.append(part.read_bytes())
        part.unlink()
    csv_path.write_bytes(concat_report_csvs(parts))
    print(f"[INFO] {len(chunks)} chunk(s) joined into {csv_path.name}")


def run_sort(
    log_type: str,
    output_folder: Path,
    csv_path: Path,
    only_booking: str | list[str] | None = None,
) -> int:
    """Run ReportSort main.py non-interactively."""
    python_exe = Path(sys.executable)
    print(f"[INFO] Running ReportSort ({log_type}logs) ...")
    print(f"[INFO] Output folder: {output_folder}")
    args = [
        str(python_exe),
        str(MAIN_PY),
        "--log-type",
        log_type,
        "--output-folder",
        str(output_folder),
        "--input-file",
        str(csv_path),
    ]
    codes = [only_booking] if isinstance(only_booking, str) else (only_booking or [])
    for code in codes:
        args += ["--only-booking", code]
    result = subprocess.run(args, cwd=str(REPORTSORT_DIR))
    return result.returncode


def main():
    parser = argparse.ArgumentParser(
        description="Download an Etere placement confirmation report and run ReportSort"
    )
    parser.add_argument("log_type", choices=["post", "pre"])
    parser.add_argument("date_from", help="MM/DD/YYYY")
    parser.add_argument("date_to", help="MM/DD/YYYY")
    parser.add_argument(
        "--contract-id", type=int, help="Single-contract pull: Etere ID_CONTRATTITESTATA"
    )
    parser.add_argument(
        "--contract-code",
        help="Single-contract pull: COD_CONTRATTO, used to select the report rows",
    )
    parser.add_argument(
        "--agency-id", type=int, help="Agency pull: every contract of this ANAGRAF agency id"
    )
    parser.add_argument("--output-folder", help="Override the K:\\!Archives destination")
    args = parser.parse_args()

    log_type = args.log_type
    date_from = args.date_from
    date_to = args.date_to

    if args.contract_id and not args.contract_code:
        print("[ERROR] --contract-id requires --contract-code")
        sys.exit(1)
    if args.agency_id and args.contract_id:
        print("[ERROR] --agency-id and --contract-id are exclusive")
        sys.exit(1)
    if args.agency_id and not args.output_folder:
        print("[ERROR] --agency-id requires --output-folder (never the Worldlink archive)")
        sys.exit(1)

    if not MAIN_PY.exists():
        print(f"[ERROR] ReportSort not found at {REPORTSORT_DIR}")
        sys.exit(1)

    if args.output_folder:
        output_folder = Path(args.output_folder)
    else:
        output_folder = build_output_folder(log_type, date_from, date_to)

    # A single-contract or agency pull keeps its CSV beside its own output so
    # concurrent AE runs can't overwrite each other's shared input file.
    own_csv = bool(args.contract_id or args.agency_id)
    csv_path = (output_folder / "placement-confirmation.csv") if own_csv else INPUT_CSV

    only_booking: str | list[str] | None = args.contract_code
    if args.agency_id:
        start, end = parse_date(date_from).date(), parse_date(date_to).date()
        only_booking = agency_contract_codes(args.agency_id, start, end)
        if not only_booking:
            print(
                f"[ERROR] agency {args.agency_id} has no contract overlapping {date_from} - {date_to}"
            )
            sys.exit(1)
        print(f"[INFO] {len(only_booking)} contract(s) for agency {args.agency_id} in range:")
        for code in only_booking:
            print(f"       {code}")

    print("[INFO] Logging into Etere ...")
    session = etere_web_login()
    set_master_market(session, coduser=10)  # DAL (Dallas) — required for TAC spots

    try:
        if args.agency_id:
            csv_path.parent.mkdir(parents=True, exist_ok=True)
            download_agency_report(session, args.agency_id, start, end, csv_path)
        else:
            download_report(session, date_from, date_to, csv_path, contract_id=args.contract_id)
    finally:
        etere_web_logout(session)

    rc = run_sort(log_type, output_folder, csv_path, only_booking=only_booking)
    if rc != 0:
        print(f"[ERROR] ReportSort exited with code {rc}")
        sys.exit(rc)

    print(f"\n[DONE] {log_type.capitalize()}logs complete. Output files in {output_folder}")


if __name__ == "__main__":
    main()
