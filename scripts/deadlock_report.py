"""Read SQL Server's own deadlock graphs (system_health ring, kept for months) and say
who deadlocked with whom. Needs `GRANT VIEW SERVER STATE TO [etere_app]` (granted 2026-10-13).

    uv run python3 scripts/deadlock_report.py                 # per-day counts, top statements, last 10 graphs
    uv run python3 scripts/deadlock_report.py --since 2026-10-10 --grep "COD_USER=5"   # graphs matching text
    uv run python3 scripts/deadlock_report.py --since 2026-10-10 --hours               # per-hour counts (Pacific)

Timestamps in the ring are UTC; printed as Pacific. Read this BEFORE wrapping a retry
around a 1205 — the graph names the partner statement, host and program (lesson 10/13)."""

import argparse
import html
import re
import sys
from collections import Counter
from datetime import timedelta

sys.path.insert(0, ".")
from browser_automation.etere_direct_client import connect  # noqa: E402

PACIFIC = timedelta(hours=-7)  # PDT; the DB clock itself is Pacific, the xevent ring is UTC
SQL = """
SELECT CAST(event_data AS xml).value('(event/@timestamp)[1]','datetime2(0)'),
       CAST(event_data AS nvarchar(max))
FROM sys.fn_xe_file_target_read_file('system_health*.xel', NULL, NULL, NULL)
WHERE object_name = 'xml_deadlock_report'
"""


def _attr(attrs, k):
    m = re.search(k + r'="([^"]*)"', attrs)
    return m.group(1) if m else None


def load(since=None):
    with connect() as conn:
        cur = conn.cursor()
        cur.execute(SQL)
        rows = cur.fetchall()
    out = []
    for ts, s in rows:
        pac = ts + PACIFIC
        if since and pac.strftime("%Y-%m-%d") < since:
            continue
        victim = re.search(r'victimProcess id="(\w+)"', s)
        procs = []
        for pid, attrs, body in re.findall(r'<process id="(\w+)"([^>]*)>(.*?)</process>', s, re.S):
            inp = re.search(r"<inputbuf>(.*?)</inputbuf>", body, re.S)
            procs.append(
                {
                    "victim": bool(victim) and victim.group(1) == pid,
                    "host": _attr(attrs, "hostname") or "-",
                    "app": _attr(attrs, "clientapp") or "-",
                    "wait_ms": int(_attr(attrs, "waittime") or 0),
                    "input": re.sub(
                        r"\s+", " ", html.unescape(inp.group(1) if inp else "")
                    ).strip(),
                }
            )
        res = sorted(
            {
                f"{m.group(1)} {_attr(m.group(2), 'objectname')} {_attr(m.group(2), 'indexname')}"
                for m in re.finditer(r"<(keylock|pagelock|objectlock|ridlock)([^>]*)>", s)
            }
        )
        out.append({"pac": pac, "procs": procs, "res": res})
    out.sort(key=lambda r: r["pac"])
    return out


def shape(stmt):
    stmt = re.sub(r"'[^']*'", "'…'", stmt)
    return re.sub(r"\d+", "N", stmt)[:110]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", help="YYYY-MM-DD (Pacific)")
    ap.add_argument("--grep", help="only graphs whose statements contain this text")
    ap.add_argument("--hours", action="store_true", help="per-hour counts instead of per-day")
    ap.add_argument("--last", type=int, default=10, help="graphs to print in full")
    a = ap.parse_args()
    reps = load(a.since)
    if a.grep:
        reps = [r for r in reps if any(a.grep.lower() in p["input"].lower() for p in r["procs"])]
    print(
        f"{len(reps)} deadlock graphs"
        + (f" since {a.since}" if a.since else "")
        + (f" matching {a.grep!r}" if a.grep else "")
    )
    if not reps:
        return 0
    width = 13 if a.hours else 10
    for k, n in sorted(Counter(r["pac"].strftime("%Y-%m-%d %H")[:width] for r in reps).items()):
        print(f"  {k}  {n}")
    print("\nvictim statements:")
    for k, n in Counter(
        shape(p["input"]) for r in reps for p in r["procs"] if p["victim"]
    ).most_common(8):
        print(f"  {n:6}  {k}")
    print("\nparticipants by host / program:")
    for (h, ap_), n in Counter((p["host"], p["app"]) for r in reps for p in r["procs"]).most_common(
        8
    ):
        print(f"  {n:6}  {h}  {ap_}")
    print(f"\nlast {a.last} graphs:")
    for r in reps[-a.last :]:
        print(f"--- {r['pac']:%m-%d %H:%M:%S} PT  {'; '.join(r['res'])[:120]}")
        for p in r["procs"]:
            print(
                f"   {'*' if p['victim'] else ' '} {p['host'][-8:]:8} {p['app'][:14]:14} w{p['wait_ms']:5}ms  {p['input'][:150]}"
            )
    return 0


if __name__ == "__main__":
    sys.exit(main())
