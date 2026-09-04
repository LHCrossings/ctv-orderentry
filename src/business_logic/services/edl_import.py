"""Edius marker-CSV → Etere EDL importer for the Set up Daily Programming tool.

Some program files (e.g. Korean News, NEWSTODAY<mmddyy>) are a single whole file
with NO physical a/b/c pieces and NO EDL marks — they must be split into the day's
program breaks via an EDL.  Instead of the operator hand-entering the marks in
Etere's Media Library, they export an "EDIUS Marker list" CSV and drop it in here.

CSV convention (confirmed with master control 2026-06-19, GAP pairs 2026-09-04):
  * Drop-frame timecodes (HH:MM:SS;FF) on NTSC 29.97.
  * The LAST marker = EOM (tail trim / out-point of the conformed program).
  * A marker whose Comment is ``GAP`` is one end of an internal section to OMIT.
    GAP markers come in ADJACENT pairs in position order: the first is the last
    frame kept, the second is the last frame dropped (program resumes at +1).
    Etere shows this as a "black" mark; the show still splits into one extra part.
  * Every other earlier marker = EDL split point.
  * SOM = 0 (no head trim).  A head-trim variant will get its own annotated
    format later — until then assume SOM = 0.
So N splits + G gaps → N+G+1 exploded parts.

Writing mirrors exactly what Etere itself stores (verified against known-good
hand-marked files — NEWSTODAY040926 for the omit row): splits go to FINTERRUZIONI
as pure split points (MARKIN=MARKOUT, INSERTION_POINT=1, BULK_VIDEO=0, FLAG='P');
omits as a range (MARKIN<MARKOUT, INSERTION_POINT=0, BULK_VIDEO=1, FLAG=''); both
TO_EXPLODE=1, VALID=1, MARKORDER=MARKIN, replicated across every video-standard
VERSION the file already has in FEDLDESCRIPTION (scaled by that version's frame
ratio). The EOM is written to FEDLDESCRIPTION per version and FILMATI.POS_FIN;
DURATION / DURATA = usable length = EOM+1 minus the omitted frames, where Etere
counts an omit as the inclusive range MARKIN..MARKOUT (so EOM − Σ(MARKOUT−MARKIN)).

The whole write is one transaction that runs dbo.ExplodeEdl as a self-check and
COMMITS only if the marks explode into exactly the expected parts — otherwise it
ROLLS BACK, leaving the file untouched.
"""

from __future__ import annotations

import csv
import re

_TC = re.compile(r"(\d+):(\d+):(\d+)([;:])(\d+)")
GAP_COMMENT = "GAP"


def _tc_to_frames(pos: str):
    """EDIUS timecode → NTSC frame number. ';' = drop-frame (29.97), ':' = non-drop."""
    m = _TC.match(pos.strip())
    if not m:
        return None
    hh, mm, ss, sep, ff = (
        int(m.group(1)),
        int(m.group(2)),
        int(m.group(3)),
        m.group(4),
        int(m.group(5)),
    )
    base = ((hh * 3600 + mm * 60 + ss) * 30) + ff
    if sep == ";":  # drop-frame: 2 dropped per minute except every 10th
        total_min = hh * 60 + mm
        base -= 2 * (total_min - total_min // 10)
    return base


def parse_edius_csv(text: str):
    """Parse an EDIUS marker-list CSV → (splits, eom, omits) in NTSC frames.

    ``splits`` is a sorted list of split frames, ``eom`` the out-point, ``omits`` a
    sorted list of (markin, markout) ranges built from adjacent GAP-commented
    marker pairs.  Raises ValueError on a CSV that yields fewer than two usable
    markers, or whose GAP markers do not pair up cleanly.
    """
    marks: dict[int, bool] = {}  # frame → is_gap
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
        if f is None:
            continue
        is_gap = len(row) > 4 and row[4].strip().upper() == GAP_COMMENT
        marks[f] = marks.get(f, False) or is_gap
    frames = sorted(marks)
    if len(frames) < 2:
        raise ValueError("CSV must contain at least 2 markers (≥1 split + the EOM out-point)")
    eom = frames[-1]
    if marks[eom]:
        raise ValueError(
            f"the last marker (EOM, frame {eom}) is commented GAP — a gap cannot end the program"
        )

    splits: list[int] = []
    omits: list[tuple[int, int]] = []
    i = 0
    body = frames[:-1]
    while i < len(body):
        f = body[i]
        if not marks[f]:
            splits.append(f)
            i += 1
            continue
        if i + 1 >= len(body) or not marks[body[i + 1]]:
            raise ValueError(
                f"GAP marker at frame {f} has no adjacent GAP partner — gaps must be marked as an in/out pair"
            )
        omits.append((f, body[i + 1]))
        i += 2
    return splits, eom, omits


def expected_parts(splits, eom, omits=()):
    """The parts the marks should explode into (mirrors dbo.ExplodeEdl): a split at
    ``s`` ends a part at ``s`` and starts the next at ``s+1``; an omit ``(a, b)``
    ends a part at ``a`` and starts the next at ``b+1``; the last part ends at EOM."""
    cuts = sorted([(s, s) for s in splits] + [(a, b) for a, b in omits])
    out = []
    start = 0
    for a, b in cuts:
        out.append((start, a))
        start = b + 1
    out.append((start, eom))
    return out


def _omitted_frames(omits) -> int:
    """Etere's DURATION bookkeeping counts an omit as the inclusive MARKIN..MARKOUT range."""
    return sum(b - a + 1 for a, b in omits)


def apply_edl_from_csv(conn, filmati: int, splits, eom: int, cod_user: int | None = None, omits=()):
    """Write the EDL (FINTERRUZIONI splits/omits + FEDLDESCRIPTION EOM) across all of
    the file's video-standard VERSIONs.

    If ``cod_user`` is given, self-validate with dbo.ExplodeEdl and COMMIT only if
    the explode plan matches the expected parts (else ROLLBACK) — used by Daily
    Programming, which needs the marks to explode for that channel. If ``cod_user``
    is None, just write the marks to the asset and COMMIT — market-irrelevant EDL
    markup (the marks are stored on the asset; explode happens later at
    scheduling). Returns {ok, parts, expected, message}.
    """
    omits = [(int(a), int(b)) for a, b in (omits or ())]
    for a, b in omits:
        if not 0 < a < b < eom:
            return {
                "ok": False,
                "parts": [],
                "message": f"gap {a}-{b} is not inside (0, EOM={eom})",
            }
    cur = conn.cursor()
    # Per-version frame ratio = that version's DURATION / VERSION-0 DURATION.
    # Stable across trims (both ends scale to the same wall-clock), so it works
    # on a first import or a re-import.
    cur.execute("SELECT VERSION, DURATION FROM FEDLDESCRIPTION WHERE ID_FILMATI=%s", (filmati,))
    durs = {int(v): int(dn) for v, dn in cur.fetchall()}
    if not durs:
        return {
            "ok": False,
            "parts": [],
            "message": f"filmati {filmati} has no FEDLDESCRIPTION (not ingested)",
        }
    if 0 not in durs or not durs[0]:
        return {"ok": False, "parts": [], "message": "filmati has no VERSION-0 EDL header"}
    d0 = durs[0]

    def _label(n_parts: int) -> str:
        gaps = f" + {len(omits)} gap(s)" if omits else ""
        return f"{len(splits)} split(s){gaps} → {n_parts} parts"

    try:
        # The tail trim (EOM out-point) is what dbo.ExplodeEdl actually reads from
        # FILMATI.POS_FIN — NOT FEDLDESCRIPTION.EOM. DURATA = usable length =
        # POS_FIN - POS_INI + 1 minus omitted frames (POS_INI stays 0; no head
        # trim yet). The physical file length (DUR_FISICA / DURATA_PUB) is left
        # untouched.
        cur.execute(
            "UPDATE FILMATI SET POS_FIN=%s, DURATA=%s WHERE ID_FILMATI=%s",
            (eom, eom + 1 - _omitted_frames(omits), filmati),
        )
        for v, dn in durs.items():
            r = dn / d0
            new_eom = round(eom * r)
            v_omits = [(round(a * r), round(b * r)) for a, b in omits]
            cur.execute(
                "UPDATE FEDLDESCRIPTION SET SOM=0, EOM=%s, DURATION=%s WHERE ID_FILMATI=%s AND VERSION=%s",
                (new_eom, new_eom + 1 - _omitted_frames(v_omits), filmati, v),
            )
        cur.execute("DELETE FROM FINTERRUZIONI WHERE ID_FILMATI=%s", (filmati,))
        ins = """INSERT INTO FINTERRUZIONI
                   (ID_FILMATI, ID_FILMATI_LNK, ID_TIPOLOGIE, TESTO, NEWTYPE,
                    MARKIN, MARKOUT, PARTE, BULK_VIDEO, TO_EXPLODE, INSERTION_POINT,
                    VALID, FLAG, VERSION, NOTE, COMPLEX, MARKORDER)
                 VALUES (%s,-1,0,'','',%s,%s,0,%s,1,%s,1,%s,%s,'',0,%s)"""
        for v, dn in durs.items():
            r = dn / d0
            for s in splits:
                f = round(s * r)
                cur.execute(ins, (filmati, f, f, 0, 1, "P", v, f))
            for a, b in omits:
                fa, fb = round(a * r), round(b * r)
                cur.execute(ins, (filmati, fa, fb, 1, 0, "", v, fa))

        exp = expected_parts(splits, eom, omits)

        # Market-irrelevant markup: just persist the marks on the asset.
        if cod_user is None:
            conn.commit()
            return {
                "ok": True,
                "parts": exp,
                "message": f"EDL written to asset: {_label(len(exp))}",
            }

        # Self-check: explode against VERSION 0 (what CTV airs) inside the txn.
        cur.execute(
            "SELECT MARKIN, MARKOUT FROM dbo.ExplodeEdl(%s,0,N'eeAutomatic',%s,dbo.sch_GetInfDigit(%s,%s))",
            (filmati, cod_user, filmati, cod_user),
        )
        plan = [(int(a), int(b)) for a, b in cur.fetchall()]
        if plan != exp:
            conn.rollback()
            return {
                "ok": False,
                "parts": plan,
                "expected": exp,
                "message": f"explode produced {len(plan)} part(s); expected {len(exp)} — not committed",
            }
        conn.commit()
        return {
            "ok": True,
            "parts": plan,
            "message": f"EDL written and validated: {_label(len(plan))}",
        }
    except Exception as exc:  # noqa: BLE001 - leave the file untouched on any failure
        conn.rollback()
        return {"ok": False, "parts": [], "message": f"error: {exc}"}
