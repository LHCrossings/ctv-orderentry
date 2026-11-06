"""
Propose Etere spot names for a batch of IW Group (Lexus) deliveries.

Lee's naming rule (2026-10-13, refined 2026-11-06):

  description = LEXUS<dur><lang><serial>: MY<yy> <model> <Title> <Mon> <tag>
  code        = the ISCI from the delivered file name

* Serial = (model year, model, title, month, agency tag). Under one tag the :15, the :30
  and every language cut share the serial; the LEA-n and LWA-n versions of one title are
  different serials; a title repeated next month is a new serial (never reuse a serial
  for a different ISCI).
* Market suffix (-SE, -SF, -NY) ONLY when one serial has two ISCIs for the same cut, one
  per market — "if spot number 277 has exactly the same title but there is a different
  ISCI for SEA and one for SFO, we keep 277 and add SE or SF" (Lee 11/6). A name that is
  already unique by serial + tag gets none, so a batch where SEA and SFO share their
  files has no suffix at all.
* Existing assets are never renamed: an ISCI already in FILMATI keeps its description.

Inputs are the delivered .mov file names
(``TYIW26TL929H_MY26-RX_TV_OVER-THE-YEARS_ENG_CY26-NOV_23_07_LEA-4.mov``; ``23_07`` = :30,
``08_07`` = :15) and/or the traffic sheets' rows (file name without the ISCI prefix, plus
the ISCI and Language columns). The proposal is for Lee to edit, never auto-written.
"""
import re
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional

_DELIVERY_RE = re.compile(
    r'^(?:(?P<isci>[A-Z]{4}\d{2}[A-Z]{2}\d{3}[A-Z]?)_)?'
    r'MY(?P<my>\d{2})-(?P<model>[A-Z]{2,3})_TV_(?P<title>.+?)_'
    r'(?P<lang>ENG|VT|CH|KR|JP|SA)_CY\d{2}-(?P<month>[A-Z]{3,4})_'
    r'(?P<len>\d{2})[-_]07_(?P<tag>L[EW]A-\d+)(?:\.mov)?$',
    re.IGNORECASE,
)

_LEN_TO_DURATION = {"23": 30, "08": 15, "30": 30, "15": 15, "60": 60}

# Our language letter: the sheet's Language column first, else the file-name token.
_SHEET_LANGUAGE_CODE = {
    "hinglish": "E", "english": "E", "vietnamese": "V", "mandarin": "M",
    "cantonese": "C", "korean": "K", "japanese": "J", "south asian": "SA",
}
_FILE_LANGUAGE_CODE = {"ENG": "E", "VT": "V", "CH": "M", "KR": "K", "JP": "J", "SA": "SA"}

# Etere market code → the suffix Lee writes when one serial has a cut per market.
_MARKET_SUFFIX = {"SEA": "SE", "SFO": "SF", "NYC": "NY", "LAX": "LA", "CVC": "CV",
                  "HOU": "HO", "CMP": "CM", "WDC": "WD", "DAL": "DA"}

def title_words(raw: str) -> str:
    """'OVER-THE-YEARS' → 'Over the Years'; 'CHILDS-EYES' → 'Childs Eyes'; 'Game-Time' → 'Game Time'."""
    small = {"the", "of", "a", "an", "and", "in", "on", "to", "at", "for"}
    words = [w for w in re.split(r'[-_ ]+', raw) if w]
    out = []
    for i, w in enumerate(words):
        lw = w.lower()
        if i and lw in small:
            out.append(lw)
        elif w.isupper() and len(w) <= 4 and not w.isalpha():
            out.append(w)           # PS1, 4WD
        else:
            out.append(lw[:1].upper() + lw[1:])
    return " ".join(out)


@dataclass
class DeliveredSpot:
    isci: str
    model_year: str      # '26'
    model: str           # 'RX'
    title: str           # 'Over the Years'
    month: str           # 'Nov'
    duration_sec: int
    tag: str             # 'LEA-4'
    lang_code: str       # 'E', 'V', 'M', 'C'
    source: str = ""     # file name or sheet
    market: str = ""     # Etere market code of the sheet the row came from ('SEA'); '' from a .mov name


def parse_delivery_name(name: str, sheet_language: str = "", market: str = "") -> Optional[DeliveredSpot]:
    """Read one .mov file name or a sheet's 'File Name / TV Spot Title' (+ ISCI) cell.

    Returns None when the name is not IW Group's shape. ``sheet_language`` (the row's
    Language column) wins over the file-name token, which cannot tell Mandarin from
    Cantonese. ``market`` is the sheet's market code; a .mov name carries none.
    """
    base = name.rsplit("/", 1)[-1].rsplit("\\", 1)[-1].strip()
    m = _DELIVERY_RE.match(base)
    if not m:
        return None
    dur = _LEN_TO_DURATION.get(m.group("len"))
    if dur is None:
        return None
    lang_code = _SHEET_LANGUAGE_CODE.get(sheet_language.strip().lower()) or \
        _FILE_LANGUAGE_CODE.get(m.group("lang").upper(), "")
    month = m.group("month")
    month = month[:1].upper() + month[1:].lower()
    return DeliveredSpot(
        isci=(m.group("isci") or "").upper(),
        model_year=m.group("my"),
        model=m.group("model").upper(),
        title=title_words(m.group("title")),
        month=month,
        duration_sec=dur,
        tag=m.group("tag").upper(),
        lang_code=lang_code,
        source=base,
        market=(market or "").upper(),
    )


@dataclass
class NameProposal:
    isci: str
    serial: Optional[int]
    code: str                 # FILMATI.COD_PROGRA (the ISCI)
    description: str          # FILMATI.DESCRIZIO
    duration_sec: int
    lang_code: str
    tag: str
    existing: bool = False    # already in FILMATI — description is the library's, unchanged
    note: str = ""


_MONTH_ORDER = {m: i for i, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Sept", "Oct", "Nov", "Dec"])}


def _batch_order(s: DeliveredSpot) -> tuple:
    """Serials run month by month (Nov before Dec), east before west, then by ISCI, so the
    same batch numbers the same way whether it is read from .mov names or from sheets."""
    region, _, num = s.tag.partition("-")
    return (_MONTH_ORDER.get(s.month, 99), region, int(num or 0), s.isci)


def _group_key(s: DeliveredSpot) -> tuple:
    return (s.model_year, s.model, s.title, s.month, s.tag)


def propose_names(
    spots: Iterable[DeliveredSpot],
    existing_by_isci: Dict[str, str],
    next_serial: int,
) -> List[NameProposal]:
    """Assign serials in first-seen order and render each description.

    ``existing_by_isci`` maps COD_PROGRA → DESCRIZIO for ISCIs already in FILMATI;
    those rows are echoed unchanged (never renamed) and consume no serial. When one
    ISCI of a group already exists, the new cuts of that group reuse its serial.
    """
    spots = list(spots)
    seen: Dict[str, DeliveredSpot] = {}
    for s in spots:                       # dedupe on ISCI (a sheet repeats rows; SEA and SFO share files)
        if s.isci and s.isci not in seen:
            seen[s.isci] = s
        elif s.isci and s.market and seen[s.isci].market and s.market != seen[s.isci].market:
            seen[s.isci].market += "/" + s.market    # one file serving two markets
    spots = sorted(seen.values(), key=_batch_order)

    # One serial, two ISCIs for the same cut (lang + duration) = a cut per market → suffix.
    iscis_per_cut: Dict[tuple, set] = {}
    for s in spots:
        iscis_per_cut.setdefault(_group_key(s) + (s.lang_code, s.duration_sec), set()).add(s.isci)

    serial_of_group: Dict[tuple, int] = {}
    # Existing library rows pin their group's serial first.
    for s in spots:
        desc = existing_by_isci.get(s.isci)
        if desc:
            m = re.match(r'LEXUS\d+[A-Z]+(\d+)', desc)
            if m:
                serial_of_group.setdefault(_group_key(s), int(m.group(1)))

    out: List[NameProposal] = []
    serial = next_serial
    for s in spots:
        key = _group_key(s)
        if s.isci in existing_by_isci:
            out.append(NameProposal(
                isci=s.isci, serial=serial_of_group.get(key), code=s.isci,
                description=existing_by_isci[s.isci], duration_sec=s.duration_sec,
                lang_code=s.lang_code, tag=s.tag, existing=True,
                note="already in the library — not renamed",
            ))
            continue
        if key not in serial_of_group:
            serial_of_group[key] = serial
            serial += 1
        n = serial_of_group[key]
        suffix, note = "", ""
        if len(iscis_per_cut[key + (s.lang_code, s.duration_sec)]) > 1:
            mk = s.market if s.market and "/" not in s.market else ""
            suffix = "-" + _MARKET_SUFFIX.get(mk, mk[:2]) if mk else ""
            if not suffix:
                note = "two ISCIs for this cut — add the market suffix (SE/SF) by hand"
        desc = (
            f"LEXUS{s.duration_sec}{s.lang_code}{n}{suffix}: "
            f"MY{s.model_year} {s.model} {s.title} {s.month} {s.tag}"
        )
        if not s.lang_code:
            note = "language unknown — set the letter"
        out.append(NameProposal(
            isci=s.isci, serial=n, code=s.isci, description=desc,
            duration_sec=s.duration_sec, lang_code=s.lang_code, tag=s.tag, note=note,
        ))
    return out


def next_lexus_serial(descriptions: Iterable[str]) -> int:
    """Max serial over FILMATI descriptions that start with LEXUS, plus one."""
    best = 0
    for d in descriptions:
        m = re.match(r'LEXUS\d+[A-Z]+(\d+)', d or "")
        if m:
            best = max(best, int(m.group(1)))
    return best + 1
