"""Edius marker-CSV → Etere EDL importer for the Set up Daily Programming tool.

Some program files (e.g. Korean News, NEWSTODAY<mmddyy>) are a single whole file
with NO physical a/b/c pieces and NO EDL marks — they must be split into the day's
program breaks via an EDL.  Instead of the operator hand-entering the marks in
Etere's Media Library, they export an "EDIUS Marker list" CSV and drop it in here.

CSV convention (confirmed with master control 2026-06-19):
  * Drop-frame timecodes (HH:MM:SS;FF) on NTSC 29.97.
  * The LAST marker = EOM (tail trim / out-point of the conformed program).
  * All earlier markers = EDL split points.
  * SOM = 0 (no head trim).  A head-trim variant will get its own annotated
    format later — until then assume SOM = 0.
So N markers → (N-1) splits → N exploded parts.

Writing mirrors exactly what Etere itself stores (verified against a known-good
marked file): the splits go to FINTERRUZIONI as pure split points
(MARKIN=MARKOUT, INSERTION_POINT=1, BULK_VIDEO=0, FLAG='P', TO_EXPLODE=1,
VALID=1, MARKORDER=MARKIN), replicated across every video-standard VERSION the
file already has in FEDLDESCRIPTION (scaled by that version's frame ratio); the
EOM is written to FEDLDESCRIPTION (EOM + DURATION=EOM+1, SOM=0) per version.

The whole write is one transaction that runs dbo.ExplodeEdl as a self-check and
COMMITS only if the marks explode into exactly the expected parts — otherwise it
ROLLS BACK, leaving the file untouched.
"""
from __future__ import annotations

import csv
import re

_TC = re.compile(r"(\d+):(\d+):(\d+)([;:])(\d+)")


def _tc_to_frames(pos: str):
    """EDIUS timecode → NTSC frame number. ';' = drop-frame (29.97), ':' = non-drop."""
    m = _TC.match(pos.strip())
    if not m:
        return None
    hh, mm, ss, sep, ff = int(m.group(1)), int(m.group(2)), int(m.group(3)), m.group(4), int(m.group(5))
    base = ((hh * 3600 + mm * 60 + ss) * 30) + ff
    if sep == ";":  # drop-frame: 2 dropped per minute except every 10th
        total_min = hh * 60 + mm
        base -= 2 * (total_min - total_min // 10)
    return base


def parse_edius_csv(text: str):
    """Parse an EDIUS marker-list CSV → (splits, eom) in NTSC frames.

    Returns (list_of_split_frames, eom_frame).  Raises ValueError on a CSV that
    yields fewer than two usable markers.
    """
    frames = []
    for line in (text or "").splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        try:
            row = next(csv.reader([s]))
        except Exception:
            continue
        if len(row) < 3:
            continue
        f = _tc_to_frames(row[2])
        if f is not None:
            frames.append(f)
    frames = sorted(set(frames))
    if len(frames) < 2:
        raise ValueError("CSV must contain at least 2 markers (≥1 split + the EOM out-point)")
    return frames[:-1], frames[-1]


def expected_parts(splits, eom):
    """The N parts the splits+EOM should explode into: part1=(0,split1) … partN=(splitN-1+1,EOM)."""
    bounds = [0] + list(splits) + [eom]
    out = []
    for k in range(len(bounds) - 1):
        lo = bounds[k] if k == 0 else bounds[k] + 1
        out.append((lo, bounds[k + 1]))
    return out


def apply_edl_from_csv(conn, filmati: int, splits, eom: int, cod_user: int | None = None):
    """Write the EDL (FINTERRUZIONI splits + FEDLDESCRIPTION EOM) across all of the
    file's video-standard VERSIONs.

    If ``cod_user`` is given, self-validate with dbo.ExplodeEdl and COMMIT only if
    the explode plan matches the expected parts (else ROLLBACK) — used by Daily
    Programming, which needs the marks to explode for that channel. If ``cod_user``
    is None, just write the marks to the asset and COMMIT — market-irrelevant EDL
    markup (the marks are stored on the asset; explode happens later at
    scheduling). Returns {ok, parts, expected, message}.
    """
    cur = conn.cursor()
    # Per-version frame ratio = that version's DURATION / VERSION-0 DURATION.
    # Stable across trims (both ends scale to the same wall-clock), so it works
    # on a first import or a re-import.
    cur.execute("SELECT VERSION, DURATION FROM FEDLDESCRIPTION WHERE ID_FILMATI=%s", (filmati,))
    durs = {int(v): int(dn) for v, dn in cur.fetchall()}
    if not durs:
        return {"ok": False, "parts": [], "message": f"filmati {filmati} has no FEDLDESCRIPTION (not ingested)"}
    if 0 not in durs or not durs[0]:
        return {"ok": False, "parts": [], "message": "filmati has no VERSION-0 EDL header"}
    d0 = durs[0]

    try:
        # The tail trim (EOM out-point) is what dbo.ExplodeEdl actually reads from
        # FILMATI.POS_FIN — NOT FEDLDESCRIPTION.EOM. DURATA = usable length =
        # POS_FIN - POS_INI + 1 (POS_INI stays 0; no head trim yet). The physical
        # file length (DUR_FISICA / DURATA_PUB) is left untouched.
        cur.execute("UPDATE FILMATI SET POS_FIN=%s, DURATA=%s WHERE ID_FILMATI=%s", (eom, eom + 1, filmati))
        for v, dn in durs.items():
            r = dn / d0
            new_eom = round(eom * r)
            cur.execute(
                "UPDATE FEDLDESCRIPTION SET SOM=0, EOM=%s, DURATION=%s WHERE ID_FILMATI=%s AND VERSION=%s",
                (new_eom, new_eom + 1, filmati, v),
            )
        cur.execute("DELETE FROM FINTERRUZIONI WHERE ID_FILMATI=%s", (filmati,))
        for v, dn in durs.items():
            r = dn / d0
            for s in splits:
                f = round(s * r)
                cur.execute(
                    """INSERT INTO FINTERRUZIONI
                         (ID_FILMATI, ID_FILMATI_LNK, ID_TIPOLOGIE, TESTO, NEWTYPE,
                          MARKIN, MARKOUT, PARTE, BULK_VIDEO, TO_EXPLODE, INSERTION_POINT,
                          VALID, FLAG, VERSION, NOTE, COMPLEX, MARKORDER)
                       VALUES (%s,-1,0,'','',%s,%s,0,0,1,1,1,'P',%s,'',0,%s)""",
                    (filmati, f, f, v, f),
                )

        exp = expected_parts(splits, eom)

        # Market-irrelevant markup: just persist the marks on the asset.
        if cod_user is None:
            conn.commit()
            return {"ok": True, "parts": exp,
                    "message": f"EDL written to asset: {len(splits)} split(s) → {len(exp)} parts"}

        # Self-check: explode against VERSION 0 (what CTV airs) inside the txn.
        cur.execute(
            "SELECT MARKIN, MARKOUT FROM dbo.ExplodeEdl(%s,0,N'eeAutomatic',%s,dbo.sch_GetInfDigit(%s,%s))",
            (filmati, cod_user, filmati, cod_user),
        )
        plan = [(int(a), int(b)) for a, b in cur.fetchall()]
        if plan != exp:
            conn.rollback()
            return {"ok": False, "parts": plan, "expected": exp,
                    "message": f"explode produced {len(plan)} part(s); expected {len(exp)} — not committed"}
        conn.commit()
        return {"ok": True, "parts": plan,
                "message": f"EDL written and validated: {len(splits)} split(s) → {len(plan)} parts"}
    except Exception as exc:  # noqa: BLE001 - leave the file untouched on any failure
        conn.rollback()
        return {"ok": False, "parts": [], "message": f"error: {exc}"}
