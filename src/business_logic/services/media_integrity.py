"""Media integrity — catch a truncated or partially copied playout file BEFORE it airs.

2026-09-07: DAL, WDC and NYC each froze for 30 minutes on "The One" piece B. The file
`TheOne090726B.mp4` was 36,962,304 bytes for a 30:41 program — 670 bytes per frame where
every conformed house MP4 sits at ~38,500 (about 9.2 Mbps at 29.97 fps). The AU holds the
slot for the clip's full DURATA showing a frozen/black picture, so one bad export takes a
market off air for the length of the piece, in every market that carries the show. All six
copies were the same size: the truncation happened at the source, not in the Datamover.

Two rules, both derived from the 2026-09-07 population (570 scheduled assets):
  * truncated    — the smallest sized playout copy is below MIN_BYTES_PER_FRAME. The lowest
                   legitimate file seen was 22,396 B/frame (a short news piece); the bad one
                   was 670. Proxies (device "Proxy", H264, ~9,700) are excluded by device.
  * inconsistent — copies of one asset with the same codec differ in size across the S3
                   master and the CIBs (a partial or interrupted copy on one box).

Playout devices = FS_METADEVICE rows whose LEGACY_MEDIAID is a digit: '0' = AWS S3 master,
'1'/'3'/'4'/'5'/'6' = CIB1/3/4/5/6. Excluded: Proxy (H264), WIP, training, CIB_TEST ('A').

Shared by `scripts/check_media_sizes.py` (CLI) and the Broadcast Health nightly task
(`src/web/routes/broadcast_health.py`), which shows findings in the site header.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import asdict, dataclass, field

FPS = 29.97
MIN_BYTES_PER_FRAME = 12_000
HOUSE_BYTES_PER_FRAME = 38_500
COPY_TOLERANCE = 0.02

MARKETS = {
    1: "NYC",
    2: "CMP",
    3: "HOU",
    4: "SFO",
    5: "SEA",
    6: "LAX",
    7: "CVC",
    8: "WDC",
    9: "MMT",
    10: "DAL",
}


@dataclass
class Finding:
    id_filmati: int
    code: str
    newtype: str
    durata: int  # frames
    kind: str  # 'truncated' | 'inconsistent'
    detail: str
    copies: list[dict] = field(default_factory=list)  # [{device, size, codec}]
    airings: list[dict] = field(default_factory=list)  # [{market, date, time, status}]


def hms(frames: int) -> str:
    s = frames / FPS
    return "%02d:%02d:%02d" % (s // 3600, (s % 3600) // 60, s % 60)


def classify(durata: int, copies: list[dict]) -> list[tuple[str, str]]:
    """Pure rule evaluation over one asset's playout copies.

    `copies` = [{"device": "CIB1", "size": 123, "codec": "MP4"}, ...]. Copies with no
    size (0/None — the CIB has not stamped the file yet) are ignored; they are not
    evidence either way. Returns [(kind, detail), ...], empty when the asset looks fine.
    """
    out: list[tuple[str, str]] = []
    if not durata or durata <= 0:
        return out
    sized = [c for c in copies if c.get("size")]
    if not sized:
        return out
    smallest = min(sized, key=lambda c: c["size"])
    bpf = smallest["size"] / durata
    if bpf < MIN_BYTES_PER_FRAME:
        devs = sorted({c["device"] for c in sized if c["size"] == smallest["size"]})
        out.append(
            (
                "truncated",
                f"{smallest['size']:,} bytes for {hms(durata)} = {bpf:,.0f} B/frame "
                f"(house ~{HOUSE_BYTES_PER_FRAME:,}) on {', '.join(devs)}",
            )
        )
    by_codec: dict[str, list[dict]] = {}
    for c in sized:
        by_codec.setdefault((c.get("codec") or "").strip().upper(), []).append(c)
    for codec, group in by_codec.items():
        if len(group) < 2:
            continue
        lo = min(group, key=lambda c: c["size"])
        hi = max(group, key=lambda c: c["size"])
        if hi["size"] / lo["size"] - 1 > COPY_TOLERANCE:
            out.append(
                (
                    "inconsistent",
                    f"{codec or 'copy'} sizes differ: {lo['device']} {lo['size']:,} vs "
                    f"{hi['device']} {hi['size']:,} bytes",
                )
            )
    return out


def scan(conn, days: int = 2, today: dt.date | None = None) -> dict:
    """Check every asset placed (STATUS I/C, LIVELLO 0) from `today` through today+days.

    Returns a JSON-able dict: state 'ok' | 'alert', checked_at, from/to, assets, findings.
    Read-only. Dates and ids are formatted as literals (all generated here), so the same
    SQL runs on pymssql and pyodbc.
    """
    today = today or dt.date.today()
    d_to = today + dt.timedelta(days=days)
    cur = conn.cursor()
    cur.execute(
        "SELECT t.ID_FILMATI, t.COD_USER, t.DATA, t.ORA, t.STATUS, RTRIM(f.COD_PROGRA),"
        " RTRIM(f.NEWTYPE), f.DURATA"
        " FROM TPALINSE t JOIN FILMATI f ON f.ID_FILMATI = t.ID_FILMATI"
        " WHERE t.LIVELLO = 0 AND t.COD_USER BETWEEN 1 AND 10 AND t.ID_FILMATI > 0"
        "   AND f.LIVE_ID IS NULL AND f.DURATA > 0 AND t.STATUS IN ('I', 'C')"
        f"   AND t.DATA BETWEEN '{today:%Y-%m-%d}' AND '{d_to:%Y-%m-%d}'"
        " ORDER BY t.DATA, t.ORA"
    )
    assets: dict[int, dict] = {}
    for fid, market, data, ora, status, code, newtype, durata in cur.fetchall():
        a = assets.setdefault(
            fid,
            {"code": code, "newtype": newtype, "durata": int(durata), "airings": []},
        )
        if len(a["airings"]) < 5:
            a["airings"].append(
                {
                    "market": MARKETS.get(market, str(market)),
                    "date": data.strftime("%Y-%m-%d")
                    if hasattr(data, "strftime")
                    else str(data)[:10],
                    "time": hms(int(ora))[:5],
                    "status": status,
                }
            )
    copies: dict[int, list[dict]] = {}
    ids = sorted(assets)
    for i in range(0, len(ids), 500):
        chunk = ",".join(str(int(x)) for x in ids[i : i + 500])
        cur.execute(
            "SELECT x.ID_FILMATI, RTRIM(d.DESCRIPTION), x.PHYSICAL_SIZE, RTRIM(x.CODEC_TYPE)"
            " FROM FS_FILMATI x JOIN FS_METADEVICE d ON d.ID_METADEVICE = x.ID_METADEVICE"
            f" WHERE d.LEGACY_MEDIAID LIKE '[0-9]' AND x.ID_FILMATI IN ({chunk})"
        )
        for fid, dev, size, codec in cur.fetchall():
            copies.setdefault(fid, []).append(
                {"device": dev or "?", "size": int(size or 0), "codec": codec or ""}
            )
    findings: list[Finding] = []
    for fid in ids:
        a = assets[fid]
        for kind, detail in classify(a["durata"], copies.get(fid, [])):
            findings.append(
                Finding(
                    id_filmati=fid,
                    code=a["code"],
                    newtype=a["newtype"],
                    durata=a["durata"],
                    kind=kind,
                    detail=detail,
                    copies=sorted(copies.get(fid, []), key=lambda c: c["device"]),
                    airings=a["airings"],
                )
            )
    findings.sort(
        key=lambda f: (f.airings[0]["date"], f.airings[0]["time"]) if f.airings else ("", "")
    )
    return {
        "state": "alert" if findings else "ok",
        "checked_at": dt.datetime.now().isoformat(timespec="seconds"),
        "from": today.isoformat(),
        "to": d_to.isoformat(),
        "assets": len(assets),
        "findings": [asdict(f) for f in findings],
    }


def format_report(result: dict) -> str:
    lines = [
        f"media integrity {result['from']}..{result['to']}: {result['assets']} scheduled asset(s),"
        f" {len(result['findings'])} finding(s)  [{result['checked_at']}]"
    ]
    for f in result["findings"]:
        lines.append(
            f"  {f['kind'].upper():<12} {f['code']} ({f['newtype']}, {hms(f['durata'])}, id {f['id_filmati']})"
        )
        lines.append(f"               {f['detail']}")
        for a in f["airings"]:
            lines.append(
                f"               airs {a['market']} {a['date']} {a['time']} ({a['status']})"
            )
    return "\n".join(lines)
