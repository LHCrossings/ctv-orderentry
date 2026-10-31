"""Write a creative onto a placed playlist row the way a traffic assignment does.

A TPALINSE row that carries a creative is seven columns, not one: COD_PROGRA, TITLE,
ID_FILMATI, NEWTYPE (COM/BNS from the line's booking code), SUPPORTO (the playout
binding, prefix + FILE_ID — `playout_binding.binding`, the one rule), ASPECT and
DURATION_P. The /traffic/assign-assets route writes all seven.

2026-10-30 20:34: the WorldLink room's "reapply filmati" step wrote
`UPDATE TPALINSE SET ID_FILMATI = …` alone onto the 78 CVC rows Etere had just
re-placed after a restore, so each row said "creative 146733" while still reading
COMS, no title, no duration and an EMPTY binding — the Broadcast Health scan flagged
the first six (11/2) as "would air black" (Lee 10/31). Every writer that puts a
creative on a placed row goes through `assign_creative` so the row can never be
half written again; `tests/unit/test_tpalinse_assign_full_row.py` pins it.
"""

from __future__ import annotations

from business_logic.services.playout_binding import binding

# The columns a creative assignment writes, in the order ASSIGN_SQL names them.
ASSIGN_COLUMNS = (
    "COD_PROGRA",
    "TITLE",
    "ID_FILMATI",
    "NEWTYPE",
    "SUPPORTO",
    "ASPECT",
    "DURATION_P",
)

ASSIGN_SQL = (
    "UPDATE TPALINSE SET COD_PROGRA = %s, TITLE = %s, ID_FILMATI = %s, NEWTYPE = %s,"
    " SUPPORTO = %s, ASPECT = %s, DURATION_P = %s WHERE ID_TPALINSE = %s"
)

# VIDEOSTANDARD "D" (digital HD) and NULL both map to ASPECT "H" (same as the assign route).
_VS_TO_ASPECT = {"D": "H"}

# The copy the binding rule picks also supplies the aspect and duration (S3 master first,
# sized, colon-free — mirrors playout_binding.FILE_ID_FOR_ASSET).
_COPY_SQL = (
    "SELECT TOP 1 x.VIDEOSTANDARD, x.DUR FROM FS_FILMATI x"
    " JOIN FS_METADEVICE d ON d.ID_METADEVICE = x.ID_METADEVICE"
    " WHERE x.ID_FILMATI = %s AND x.PHYSICAL_SIZE > 0 AND x.FILE_ID NOT LIKE '%%:%%'"
    " ORDER BY CASE WHEN d.LEGACY_MEDIAID = '0' THEN 0 ELSE 1 END, x.LASTUPDATE DESC"
)

_NEWTYPE_SQL = (
    "SELECT b.code FROM trafficPalinse tp"
    " JOIN CONTRATTIRIGHE cr ON cr.ID_CONTRATTIRIGHE = tp.ID_ContrattiRighe"
    " JOIN trf_bookingcode b ON b.id_bookingcode = cr.ID_BOOKINGCODE"
    " WHERE tp.id_tpalinse = %s"
)


def creative_values(cur, filmati_id: int) -> dict | None:
    """COD_PROGRA, TITLE, SUPPORTO, ASPECT, DURATION_P for `filmati_id`, or None when the
    asset has no sized copy yet (then the row is left alone and the nightly scan re-checks)."""
    sup = binding(cur, filmati_id)
    if sup is None:
        return None
    cur.execute(
        "SELECT COD_PROGRA, DESCRIZIO FROM FILMATI WHERE ID_FILMATI = %s", (int(filmati_id),)
    )
    f = cur.fetchone()
    if not f:
        return None
    cur.execute(_COPY_SQL, (int(filmati_id),))
    c = cur.fetchone() or (None, None)
    return {
        "COD_PROGRA": (f[0] or "").strip(),
        "TITLE": (f[1] or "").strip(),
        "SUPPORTO": sup,
        "ASPECT": _VS_TO_ASPECT.get(c[0], "H"),
        "DURATION_P": int(c[1] or 0),
    }


def row_newtype(cur, tp_id: int) -> str:
    """NEWTYPE from the row's contract line booking code (COM paid, BNS bonus); COM when
    the row has no line."""
    cur.execute(_NEWTYPE_SQL, (int(tp_id),))
    r = cur.fetchone()
    return (r[0] or "COM").strip() if r else "COM"


def assign_creative(cur, tp_id: int, filmati_id: int) -> bool:
    """Write `filmati_id` onto playlist row `tp_id` as a full assignment (all of
    ASSIGN_COLUMNS). Returns False, writing nothing, when the asset has no copy to bind."""
    v = creative_values(cur, filmati_id)
    if v is None:
        return False
    cur.execute(
        ASSIGN_SQL,
        (
            v["COD_PROGRA"],
            v["TITLE"],
            int(filmati_id),
            row_newtype(cur, tp_id),
            v["SUPPORTO"],
            v["ASPECT"],
            v["DURATION_P"],
            int(tp_id),
        ),
    )
    return True
