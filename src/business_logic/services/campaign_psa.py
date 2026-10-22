"""Campaign PSAs — a message that must get steady exposure through the FILL path.

Lee 2026-10-21: one message in three languages (English, Chinese in SIMPLIFIED
text, Vietnamese) runs on every network incl. DAL from 10/30/2026 to 10/30/2027,
5-10 times a day, in any programming. Maija ingested nine cuts on 10/21 (Lee
10/22): three messages per language, 1:00 to 2:10 long (ICE60E01 ... ICE130M03),
so a show takes the least recently aired cut of its language that FITS its open
time; the planner walks the candidates in that order. They are typed PSA in the library (purple in EE, deletable)
but must not be left to the ordinary PI/PSA rotation, which only draws :15/:10
PSAs for the end game and gives no language or frequency guarantee. They are
NOT entered as an order either: Etere places a line's whole flight at entry and
never bumps it, so a year-long order would take break slots a paid client needs.

Fill is placed after paid scheduling into open time only, and Finish strips
fill when an hour overruns, so a campaign PSA can never displace a paid spot.
Finish's planner seats ONE campaign PSA per show, ahead of the PI fill:

- language from the SHOW: Chinese creative in Mandarin AND Cantonese programming
  (Lee 10/22: both get the simplified "M" cut), Vietnamese in Vietnamese
  programming, English everywhere else (Korean, Filipino, South Asian, Hmong,
  Japanese). The grid's language word decides; when a window carries none, the
  day-aware language windows do (`classify_language`, DAL has its own table).
- cap per market-day (`cap_per_day`, 8 — "5-10x per day, any more is overkill"),
  counting every creative of the campaign; prefer a gap of `min_gap_s` (2 h)
  from the previous / next one (Lee: "no specific separation REQUIRED, but you
  can prefer it"); least recently aired creative first when a language has more
  than one, the planner taking the first of them that fits the show's open time.
- active only between `start` and `end`; outside the flight the rule is inert.

Creatives are named by COD_PROGRA (or `#<ID_FILMATI>`) in `CAMPAIGNS` and resolved
against FILMATI at plan time, so an asset that is expired or marked DO NOT USE drops
out by itself. Until Maija's three files are ingested the creative tuples are empty
and the planner says so in its notes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime

from src.business_logic.services.finish_plan import FPS, Ev, Filler


@dataclass(frozen=True)
class Campaign:
    name: str
    start: date
    end: date
    creatives: dict[str, tuple[str, ...]] = field(default_factory=dict)  # language → codes
    cap_per_day: int = 8
    min_gap_s: float = 2 * 3600.0


# Lee 10/22: Maija's nine cuts (FILMATI 152006-152014, S3 copies all ~38.4 kB/frame; the
# two Family Prep files on disk are named ICE30M01 / ICE30V01 but are full :60s — 69.0 MB).
CAMPAIGNS: tuple[Campaign, ...] = (
    Campaign(
        name="PSA 2026-27",
        start=date(2026, 10, 30),
        end=date(2027, 10, 30),
        creatives={
            "english": ("ICE60E01", "ICE102E02", "ICE81E03"),
            "chinese": ("ICE60M01", "ICE125M02", "ICE130M03"),
            "vietnamese": ("ICE60V01", "ICE105V02", "ICE81V03"),
        },
    ),
)

_LANG_WORDS = {
    "chinese": ("mandarin", "cantonese", "chinese", "simplified"),
    "vietnamese": ("vietnamese", "viet"),
}


def creative_language(language: str, market: int, date_s: str, lo: float) -> str:
    """Which creative a show takes: 'chinese' | 'vietnamese' | 'english'.

    `language` is the grid's word for the show ("Mandarin", "Cantonese", "Korean",
    ""); an empty word falls back to the language windows at the window's start.
    Mandarin and Cantonese both take the simplified-text Chinese cut (Lee 10/22)."""
    word = (language or "").strip().lower()
    if not word:
        from browser_automation.language_windows import classify_language

        weekday = datetime.strptime(date_s[:10], "%Y-%m-%d").strftime("%A")
        langs = classify_language(weekday, int(lo // 60), market)
        word = langs[0].lower() if len(langs) == 1 else ""
    for lang, words in _LANG_WORDS.items():
        if any(w in word for w in words):
            return lang
    return "english"


def active_campaign(date_s: str) -> Campaign | None:
    d = datetime.strptime(date_s[:10], "%Y-%m-%d").date()
    return next((c for c in CAMPAIGNS if c.start <= d <= c.end), None)


def resolve_creatives(cur, campaign: Campaign) -> dict[str, list[dict]]:
    """{language: [{fid, code, desc, frames}]} for the campaign's usable assets."""
    out: dict[str, list[dict]] = {}
    for lang, codes in campaign.creatives.items():
        out[lang] = []
        for code in codes:
            if code.startswith("#"):
                cur.execute(
                    "SELECT ID_FILMATI, RTRIM(COD_PROGRA), DESCRIZIO, DURATA FROM FILMATI WITH (NOLOCK)"
                    " WHERE ID_FILMATI=%s AND DESCRIZIO NOT LIKE 'DO NOT%%'"
                    " AND (DATA_SCAD IS NULL OR DATA_SCAD > GETDATE())",
                    (int(code[1:]),),
                )
            else:
                cur.execute(
                    "SELECT ID_FILMATI, RTRIM(COD_PROGRA), DESCRIZIO, DURATA FROM FILMATI WITH (NOLOCK)"
                    " WHERE RTRIM(COD_PROGRA)=%s AND DESCRIZIO NOT LIKE 'DO NOT%%'"
                    " AND (DATA_SCAD IS NULL OR DATA_SCAD > GETDATE())",
                    (code,),
                )
            for r in cur.fetchall():
                out[lang].append(
                    {
                        "fid": int(r[0]),
                        "code": r[1] or "",
                        "desc": r[2] or "",
                        "frames": int(r[3] or 0),
                    }
                )
    return out


def campaign_fids(cur, date_s: str) -> set[int]:
    """Every creative id of the campaign active on `date_s` (for the planner's
    'remove other fill first' preference)."""
    c = active_campaign(date_s)
    if c is None:
        return set()
    return {cr["fid"] for lst in resolve_creatives(cur, c).values() for cr in lst}


def _placed_today(cur, market: int, date_s: str, fids: set[int]) -> list[tuple[int, float]]:
    """(fid, ora_seconds) of every live row of the campaign in this market-day."""
    if not fids:
        return []
    ids_csv = ",".join(str(int(f)) for f in fids)
    cur.execute(
        f"SELECT ID_FILMATI, ORA FROM TPALINSE WITH (NOLOCK)"
        f" WHERE COD_USER=%s AND DATA=%s AND LIVELLO=0 AND ID_FILMATI IN ({ids_csv})",
        (market, date_s),
    )
    return [(int(r[0]), float(r[1]) / FPS) for r in cur.fetchall()]


def _last_aired(cur, market: int, date_s: str, fids: list[int]) -> dict[int, float]:
    if not fids:
        return {}
    ids_csv = ",".join(str(int(f)) for f in fids)
    cur.execute(
        f"SELECT ID_FILMATI, MAX(CAST(DATA AS float) * 86400 + ORA / 29.97) FROM TPALINSE WITH (NOLOCK)"
        f" WHERE ID_FILMATI IN ({ids_csv}) AND COD_USER=%s AND LIVELLO=0"
        f" AND DATA BETWEEN DATEADD(day,-14,%s) AND %s GROUP BY ID_FILMATI",
        (market, date_s, date_s),
    )
    return {int(r[0]): float(r[1] or 0) for r in cur.fetchall()}


def campaign_psa_for(
    cur, market: int, date_s: str, lo: float, hi: float, language: str, evs: list[Ev]
) -> tuple[list[Filler], str]:
    """The campaign PSA candidates Finish should seat in this window, least recently
    aired first (the planner takes the first that fits the open time), or [] with
    the reason.

    One per show; the market-day cap counts every creative of the campaign outside
    this window; a creative within `min_gap_s` before or after the window skips
    this show (spacing is preferred, not required)."""
    camp = active_campaign(date_s)
    if camp is None:
        return [], ""
    creatives = resolve_creatives(cur, camp)
    all_fids = {cr["fid"] for lst in creatives.values() for cr in lst}
    if not all_fids:
        return [], f"campaign PSA ({camp.name}): no creatives ingested yet"
    lang = creative_language(language, market, date_s, lo)
    cands = creatives.get(lang) or []
    if not cands:
        return [], f"campaign PSA ({camp.name}): no {lang} creative"
    if any(e.filmati in all_fids for e in evs):
        return [], "campaign PSA already in this show"
    placed = _placed_today(cur, market, date_s, all_fids)
    outside = [(f, o) for f, o in placed if not (lo - 0.5 <= o < hi - 0.5)]
    if len(outside) >= camp.cap_per_day:
        return [], f"campaign PSA: market-day cap {camp.cap_per_day} reached"
    near = [
        o for _f, o in outside if lo - camp.min_gap_s <= o < lo or hi <= o < hi + camp.min_gap_s
    ]
    if near:
        return (
            [],
            f"campaign PSA: one within {int(camp.min_gap_s // 60)} min of this show, skipped for spacing",
        )
    last = _last_aired(cur, market, date_s, [c["fid"] for c in cands])
    order = sorted(cands, key=lambda c: (last.get(c["fid"], 0.0), c["code"]))
    return (
        [
            Filler(
                c["fid"],
                c["desc"] or c["code"],
                c["frames"] / FPS,
                "PSA",
                f"CAMPAIGN {camp.name}",
                last.get(c["fid"], 0.0),
            )
            for c in order
        ],
        f"campaign PSA: {lang} {' › '.join(c['code'] for c in order)} by last aired"
        f" ({len(outside) + 1} of {camp.cap_per_day} today)",
    )
