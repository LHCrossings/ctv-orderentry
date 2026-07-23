"""Long-form PI spot rotation for the Marketplace assigner.

Long-form PI (paid-programming / infomercial) spots air in the Dallas (TAC/DAL)
"Marketplace" hours. Two families share one pool:
  * Regular  — `PI-LF-NNNN`   (e.g. PI-LF-0002)
  * WorldLink — `WLPI-LF-NNNN` (e.g. WLPI-LF-0008)

The identifying token lives at the START of `FILMATI.DESCRIZIO`
(`PI-LF-0002: Audien Hearing Aides`); `COD_PROGRA` is an unrelated internal code.

Active vs. not: a spot is usable unless its name (either DESCRIZIO or COD_PROGRA)
carries a `DO NOT USE` or `HIATUS` marker, or it has expired (`DATA_SCAD` in the
past). `DO NOT USE` spots are usually archived out of Etere's UI, but direct DB
access still sees them — the name marker is the reliable signal (`ARCHIVIATO` is
empty on every row and can't be used). The pool is fluid: spots come and go.

Rotation = random WITHOUT replacement. Each drawn/assigned token is recorded in
`chat.pi_lf_rotation`; a spot is only drawn again once every active spot has been
used, at which point the table is cleared and a fresh cycle begins. Keying on the
token (not the FILMATI id) means a re-ingested file with the same PI number keeps
its place in the cycle. See scripts/setup_pi_lf_rotation_table.py for the DDL.
"""
from __future__ import annotations

import random
import re

# Token at the start of DESCRIZIO. WLPI first so the WorldLink family is labelled
# as such (re.match anchors at start, so PI-LF- can't match a WLPI-LF- string
# anyway, but the order documents intent).
_TOKEN_RE = re.compile(r"^(WLPI-LF-\d+|PI-LF-\d+)", re.IGNORECASE)

# Active-pool query: the identity token leads DESCRIZIO, no DO NOT USE / HIATUS
# marker in either name field, not expired.
_ACTIVE_SQL = """
    SELECT ID_FILMATI, COD_PROGRA, DESCRIZIO, DURATA
    FROM FILMATI WITH(NOLOCK)
    WHERE NEWTYPE = 'PGM'
      AND (DESCRIZIO LIKE 'PI-LF-[0-9]%' OR DESCRIZIO LIKE 'WLPI-LF-[0-9]%')
      AND COD_PROGRA NOT LIKE '%DO NOT USE%' AND DESCRIZIO NOT LIKE '%DO NOT USE%'
      AND COD_PROGRA NOT LIKE '%HIATUS%'     AND DESCRIZIO NOT LIKE '%HIATUS%'
      AND (DATA_SCAD IS NULL OR DATA_SCAD >= CAST(GETDATE() AS DATE))
    ORDER BY DESCRIZIO
"""


def token_of(descrizio: str) -> str | None:
    """The PI token (`PI-LF-0002` / `WLPI-LF-0008`) leading a DESCRIZIO, upper-cased."""
    m = _TOKEN_RE.match((descrizio or "").strip())
    return m.group(1).upper() if m else None


def active_pool(cur) -> list[dict]:
    """All currently-usable long-form PI spots (combined PI-LF + WLPI-LF pool).
    Returns dicts: {fid, token, family, code, desc, durata}."""
    cur.execute(_ACTIVE_SQL)
    out = []
    for fid, code, desc, durata in cur.fetchall():
        tok = token_of(desc)
        if not tok:
            continue
        out.append({
            "fid": int(fid),
            "token": tok,
            "family": "WLPI" if tok.startswith("WLPI") else "PI",
            "code": (code or "").strip(),
            "desc": (desc or "").strip(),
            "durata": int(durata or 0),
        })
    return out


def token_for_fid(cur, fid: int) -> str | None:
    """The PI token for a FILMATI id, from its DESCRIZIO (for marking a manual pick used)."""
    cur.execute("SELECT DESCRIZIO FROM FILMATI WITH(NOLOCK) WHERE ID_FILMATI=%s", (int(fid),))
    r = cur.fetchone()
    return token_of(r[0]) if r else None


def _used_tokens(cur) -> set[str]:
    cur.execute("SELECT pi_token FROM chat.pi_lf_rotation")
    return {(r[0] or "").upper() for r in cur.fetchall()}


def status(cur) -> dict:
    """Rotation status for the UI: how many active spots have been used this cycle."""
    pool = active_pool(cur)
    used = _used_tokens(cur)
    active_tokens = {p["token"] for p in pool}
    used_active = active_tokens & used
    return {
        "total": len(active_tokens),
        "used": len(used_active),
        "remaining": len(active_tokens - used),
    }


def pick(conn):
    """Choose the next spot to air: a random active spot not yet used this cycle.
    When every active spot has been used, clear the table (new cycle) and draw
    from the full pool. Does NOT record the pick — the caller records it with
    mark_used() only once the placement commits, so a failed placement never
    burns a spot. Returns ({fid, token, ...}, reset: bool), or (None, False) if
    the pool is empty."""
    cur = conn.cursor()
    pool = active_pool(cur)
    if not pool:
        return None, False
    used = _used_tokens(cur)
    unused = [p for p in pool if p["token"] not in used]
    reset = False
    if not unused:
        cur.execute("DELETE FROM chat.pi_lf_rotation")   # cycle complete → start over
        conn.commit()
        unused = pool
        reset = True
    return random.choice(unused), reset


def mark_used(conn, token: str, used_by: str | None = None) -> None:
    """Record a token as used in the current cycle (idempotent — PK on pi_token)."""
    if not token:
        return
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO chat.pi_lf_rotation (pi_token, used_by) "
        "SELECT %s, %s WHERE NOT EXISTS (SELECT 1 FROM chat.pi_lf_rotation WHERE pi_token=%s)",
        (token.upper(), used_by, token.upper()),
    )
    conn.commit()
