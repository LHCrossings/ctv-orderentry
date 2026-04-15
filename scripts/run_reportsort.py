"""
Download Worldlink placement confirmation report from Etere and run ReportSort.

Usage:
    uv run python scripts/run_reportsort.py post 04/13/2026 04/19/2026
    uv run python scripts/run_reportsort.py pre  04/20/2026 04/26/2026

Arguments:
    log_type   : post or pre
    date_from  : MM/DD/YYYY
    date_to    : MM/DD/YYYY
"""
import sys
import os
import subprocess
from pathlib import Path
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from browser_automation.etere_direct_client import etere_web_login, etere_web_logout, ETERE_WEB_URL

AGENCY_ID      = 133
REPORTSORT_DIR = Path(__file__).parent.parent.parent / "ReportSort"
INPUT_CSV      = REPORTSORT_DIR / "input" / "placement-confirmation.csv"
MAIN_PY        = REPORTSORT_DIR / "main.py"

POST_LOG_BASE = Path(r"K:\!Archives\Post Logs")
PRE_LOG_BASE  = Path(r"K:\!Archives\Pre Logs")


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
        dt_to   = parse_date(date_to)
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


def download_report(session, date_from: str, date_to: str) -> None:
    """Download placement confirmation CSV from Etere."""
    url = (
        f"{ETERE_WEB_URL}/reportsetere/report"
        f"?reportCode=R100018_C0000_placement_confirmation"
        f"&isSystem=True"
        f"&reportType=DOWNLOADCSV"
        f"&customerid=0"
        f"&agencyid={AGENCY_ID}"
        f"&filters[0]="
        f"&filters[1]={AGENCY_ID}"
        f"&filters[2]=false"
        f"&filters[3]=true"
        f"&filters[4]={date_from}"
        f"&filters[5]={date_to}"
    )
    print(f"[INFO] Downloading report ({date_from} to {date_to}) ...")
    resp = session.get(url, timeout=120)
    resp.raise_for_status()

    content_type = resp.headers.get("Content-Type", "")
    if "text/html" in content_type:
        raise RuntimeError("Got HTML instead of CSV - session may have expired or report returned an error page.")

    INPUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    INPUT_CSV.write_bytes(resp.content)
    size_kb = len(resp.content) / 1024
    print(f"[INFO] Saved {size_kb:.1f} KB to {INPUT_CSV}")


def run_sort(log_type: str, date_from: str, date_to: str) -> int:
    """Run ReportSort main.py non-interactively."""
    python_exe = Path(sys.executable)
    output_folder = build_output_folder(log_type, date_from, date_to)
    print(f"[INFO] Running ReportSort ({log_type}logs) ...")
    print(f"[INFO] Output folder: {output_folder}")
    result = subprocess.run(
        [str(python_exe), str(MAIN_PY), "--log-type", log_type, "--output-folder", str(output_folder)],
        cwd=str(REPORTSORT_DIR),
    )
    return result.returncode


def main():
    if len(sys.argv) < 4:
        print("Usage: run_reportsort.py <post|pre> <from_date> <to_date>")
        print("Example: run_reportsort.py post 04/13/2026 04/19/2026")
        sys.exit(1)

    log_type  = sys.argv[1].lower()
    date_from = sys.argv[2]
    date_to   = sys.argv[3]

    if log_type not in ("post", "pre"):
        print("[ERROR] log_type must be 'post' or 'pre'")
        sys.exit(1)

    if not MAIN_PY.exists():
        print(f"[ERROR] ReportSort not found at {REPORTSORT_DIR}")
        sys.exit(1)

    print(f"[INFO] Logging into Etere ...")
    session = etere_web_login()
    set_master_market(session, coduser=10)  # DAL (Dallas) — required for TAC spots

    try:
        download_report(session, date_from, date_to)
    finally:
        etere_web_logout(session)

    rc = run_sort(log_type, date_from, date_to)
    if rc != 0:
        print(f"[ERROR] ReportSort exited with code {rc}")
        sys.exit(rc)

    output_folder = build_output_folder(log_type, date_from, date_to)
    print(f"\n[DONE] {log_type.capitalize()}logs complete. Output files in {output_folder}")


if __name__ == "__main__":
    main()
