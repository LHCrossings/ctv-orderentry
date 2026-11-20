"""Propose house names for newly delivered spots: `<CLIENT><DUR><LANG><serial>: <title> (<ISCI>)`.

Two pasted texts go in, a list of proposed lines comes out. Nothing is read from or written
to Etere: FILMATI holds only some of a client's names (5 of 21 for Woof on 11/20), so the
next serial comes from the client's own list (V:\\Commercials\\<client>), pasted by the
operator.

Rule (Lee 11/20): the cuts of ONE creative that differ only in length (WOOF15E20 +
WOOF30E20) or only in language (MD30C433 + MD30M433 + MD30V433) share one serial. The
language letter comes from the language word in the title (CAN / MAN / VIET), read against
the letters the client's list already uses. Creatives take serials in ISCI order (the
McDonald's list follows it; the portal's own row order is download time) or as pasted.
Titles are shortened on request: year to two digits, agency and format words out, CamelCase split,
language word last in the list's own spelling.
A new cut of a creative that is already on the prior list at another length takes that
creative's serial. An ISCI already on the prior list is reported, never renamed.
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass

# WOOF15E03: Woof Pupsicle R5 v1_1 15 (WFWPREV115H)
_PRIOR = re.compile(r"^\s*([A-Z][A-Z0-9]*?)(\d{2,3})([A-Z]{1,2})(\d{2,3})\s*:\s*(.+?)\s*\(([^()]+)\)\s*$")
_LENGTH_CELL = re.compile(r"^:?(\d{2,3})s?$")
_BADGES = {"hd", "sd", "bvs", "1080p", "1080i", "720p", "4k", "uhd"}
_DUR_TOKEN = re.compile(r"^:?0?(\d{2,3})s?$")
_VERSION_TOKEN = re.compile(r"^([vr]\d+|\d{1,2})$")
_KNOWN_LENGTHS = (5, 10, 15, 20, 30, 45, 60, 90, 120)


@dataclass
class PriorSpot:
    client: str
    duration: int
    lang: str
    serial: int
    serial_width: int
    title: str
    isci: str

    @property
    def code(self) -> str:
        return f"{self.client}{self.duration}{self.lang}{self.serial:0{self.serial_width}d}"


@dataclass
class Delivered:
    isci: str
    title: str
    duration: int | None


@dataclass
class Proposal:
    code: str
    title: str
    isci: str
    duration: int | None
    serial: int | None
    note: str = ""
    is_new: bool = True
    lang: str = ""

    @property
    def line(self) -> str:
        return f"{self.code}: {self.title} ({self.isci})"


class SpotNameError(ValueError):
    pass


def parse_prior(text: str) -> tuple[list[PriorSpot], list[str]]:
    """Lines of the client's own list. Returns (spots, lines that did not parse)."""
    spots, bad = [], []
    for raw in (text or "").splitlines():
        if not re.search(r"[A-Za-z0-9]", raw):  # blank lines and ---- separators
            continue
        m = _PRIOR.match(raw)
        if not m:
            bad.append(raw.strip())
            continue
        client, dur, lang, serial, title, isci = m.groups()
        spots.append(PriorSpot(client, int(dur), lang, int(serial), len(serial), title.strip(), isci.strip()))
    return spots, bad


def _is_isci(cell: str) -> bool:
    return bool(
        re.fullmatch(r"[A-Z0-9][A-Z0-9\-]{6,19}", cell) and re.search(r"\d", cell) and re.search(r"[A-Z]", cell)
    )


def _clean_title(title: str) -> str:
    return re.sub(r"\s+", " ", title.replace("×", "x")).strip(" -:\t")


def duration_from(title: str, isci: str) -> int | None:
    """A length named in the title (`_015_`, `30s`, ` 15`), else in the ISCI (`…V115H`)."""
    for tok in reversed(re.split(r"[_\s]+", title)):
        m = _DUR_TOKEN.match(tok)
        if m and int(m.group(1)) in _KNOWN_LENGTHS:
            return int(m.group(1))
    m = re.search(r"(\d{2})[A-Z]?$", isci)
    if m and int(m.group(1)) in _KNOWN_LENGTHS:
        return int(m.group(1))
    return None


def parse_delivery(text: str) -> tuple[list[Delivered], list[str]]:
    """The delivery portal's rows, copied from the page, or one spot per line.

    Accepted per spot: `<ISCI> <title> :15` in cells (tabs or line breaks, badge cells such
    as HD / 1080p ignored), `<title> (<ISCI>)`, or `<ISCI> <title>` on one line.
    Returns (spots, text that could not be read as a spot).
    """
    text = text or ""
    spots: list[Delivered] = []
    bad: list[str] = []

    paren = re.compile(r"^\s*(?:[A-Z0-9]+\s*:\s*)?(.+?)\s*\(([^()]+)\)\s*(?::?(\d{2,3})s?)?\s*$")
    # `<ISCI> <title> :15` typed on one line becomes the same three cells the portal gives
    lines = []
    for raw in text.splitlines():
        first, _, rest = raw.strip().partition(" ")
        if _is_isci(first) and rest.strip():
            raw = first + "\t" + re.sub(r"\s+(:\d{2,3}s?)$", r"\t\1", rest.strip())
        lines.append(raw)
    cells = [c.strip() for c in re.split(r"[\t\r\n]+| {2,}", "\n".join(lines)) if c.strip()]
    if any(_is_isci(c) for c in cells):
        cur: Delivered | None = None
        parts: list[str] = []

        def close(length: int | None = None):
            nonlocal cur, parts
            if cur is None:
                return
            # a title broken across cells: underscore names rejoin with nothing between
            cur.title = _clean_title(("" if all(" " not in p for p in parts) else " ").join(parts))
            cur.duration = length or duration_from(cur.title, cur.isci)
            if cur.title:
                spots.append(cur)
            else:
                bad.append(cur.isci)
            cur, parts = None, []

        for cell in cells:
            if _is_isci(cell):
                close()
                cur = Delivered(cell, "", None)
            elif cur is None or cell.lower() in _BADGES:
                continue
            elif _LENGTH_CELL.match(cell) and parts:
                close(int(_LENGTH_CELL.match(cell).group(1)))
            else:
                parts.append(cell)
        close()
        return spots, bad

    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        m = paren.match(line)
        if m:
            title, isci, length = m.groups()
            title = _clean_title(title)
            spots.append(Delivered(isci.strip(), title, int(length) if length else duration_from(title, isci)))
            continue
        bad.append(line)
    return spots, bad


_LANG_WORDS = {
    "M": ("MAN", "MANDARIN"),
    "C": ("CAN", "CANTONESE"),
    "V": ("VIE", "VIET", "VIETNAMESE"),
    "T": ("TAG", "TAGALOG", "FIL", "FILIPINO"),
    "K": ("KOR", "KOREAN"),
    "P": ("PUN", "PUNJABI"),
    "SA": ("HIN", "HINDI"),
    "J": ("JPN", "JAPANESE"),
    "HM": ("HMG", "HMONG"),
    "E": ("ENG", "ENGLISH"),
}
_NOISE = {"tvc", "olv", "tvc-olv", "acm", "hd", "sd", "16x9", "9x16", "4x3", "1x1", "ltv", "ctv"}


def language_words(prior: list[PriorSpot], extra_letters=()) -> tuple[dict[str, str], dict[str, str]]:
    """({WORD: letter}, {letter: the list's own spelling}) for the letters this client uses."""
    learned: dict[str, Counter] = {}
    for p in prior:
        last = p.title.split()[-1] if p.title.split() else ""
        if re.fullmatch(r"[A-Z]{2,5}", last):
            learned.setdefault(p.lang, Counter())[last] += 1
    letters = {p.lang for p in prior} | {x for x in extra_letters if x}
    words: dict[str, str] = {}
    spelling: dict[str, str] = {}
    for letter in letters:
        for w in _LANG_WORDS.get(letter, ()):
            words[w] = letter
        own = learned.get(letter)
        # a learned last word counts only when it is a known language word, or the list has
        # several languages (then the last word is what tells them apart)
        if own:
            top = own.most_common(1)[0][0]
            if top in words or len(letters) > 1:
                words[top] = letter
                spelling[letter] = top
        spelling.setdefault(letter, (_LANG_WORDS.get(letter) or ("",))[0])
    return words, spelling


def _tokens(title: str) -> list[str]:
    return [t for t in re.split(r"[_\s]+", title.replace("×", "x")) if t]


def _is_length(tok: str) -> bool:
    m = _DUR_TOKEN.match(tok)
    return bool(m and int(m.group(1)) in _KNOWN_LENGTHS)


def title_language(title: str, words: dict[str, str]) -> str:
    for tok in reversed(_tokens(title)):
        if tok.upper() in words:
            return words[tok.upper()]
    return ""


def creative_key(title: str, words: dict[str, str] | None = None) -> str:
    """The title without its length, language word and trailing version: one creative, one key."""
    words = words or {}
    toks = [t.lower() for t in _tokens(title) if not _is_length(t) and t.upper() not in words]
    while len(toks) > 1 and _VERSION_TOKEN.match(toks[-1]):
        toks.pop()
    return " ".join(toks)


def simplify_title(title: str, words: dict[str, str] | None = None, lang_word: str = "") -> str:
    """`2026_HomeAlone_USMcDsAlone_ACM_CAN_TVC-OLV_16x9_30` -> `26 Home Alone McDs Alone US CAN`."""
    words = words or {}
    out: list[str] = []
    for tok in _tokens(title):
        if _is_length(tok) or tok.lower() in _NOISE or tok.upper() in words:
            continue
        if re.fullmatch(r"20\d\d", tok):  # the year stays, as two digits (Lee 11/20)
            out.append(tok[2:])
            continue
        tail = ""
        if re.match(r"US(?=[A-Z][a-z])", tok):
            tok, tail = tok[2:], " US"
        # CamelCase apart, but McDs / McValue stay whole
        out.append(re.sub(r"(?<=[a-z])(?<!\bMc)(?=[A-Z])", " ", tok) + tail)
    text = " ".join(out)
    return f"{text} {lang_word}".strip() if lang_word else text


def propose(
    delivered: list[Delivered],
    prior: list[PriorSpot],
    client: str = "",
    lang: str = "",
    next_serial: int | None = None,
    order: str = "isci",
    simplify: bool = False,
) -> list[Proposal]:
    client = (client or "").strip().upper()
    lang = (lang or "").strip().upper()
    if not client and prior:
        client = Counter(p.client for p in prior).most_common(1)[0][0]
    default_lang = lang or (Counter(p.lang for p in prior).most_common(1)[0][0] if prior else "")
    if not client or not default_lang:
        raise SpotNameError("No prior list to read the client code and language from: fill in both fields.")
    mine = [p for p in prior if p.client == client]
    width = max([2] + [p.serial_width for p in mine])
    nxt = next_serial or (max((p.serial for p in mine), default=0) + 1)
    words, spelling = language_words(mine, extra_letters=(lang,))

    by_isci = {p.isci.upper(): p for p in prior}
    taken = {(p.serial, p.duration, p.lang) for p in mine}
    key_serial: dict[str, int] = {}
    prior_key: dict[str, PriorSpot] = {}
    for p in mine:
        prior_key.setdefault(creative_key(p.title, words), p)

    if order == "isci":
        delivered = sorted(delivered, key=lambda d: d.isci.upper())

    out: list[Proposal] = []
    for d in delivered:
        old = by_isci.get(d.isci.upper())
        if old is not None:
            out.append(
                Proposal(old.code, old.title, old.isci, old.duration, old.serial, "already on the list", False, old.lang)
            )
            continue
        if d.duration is None:
            out.append(Proposal("?", d.title, d.isci, None, None, "no length found: add :15 or :30", True))
            continue
        # a language typed in the field wins; else the title's own language word; else the list's
        found = title_language(d.title, words)
        d_lang = lang or found or default_lang
        key = creative_key(d.title, words)
        note = ""
        if not lang and not found and len({p.lang for p in mine}) > 1:
            note = f"no language word in the title: {default_lang} assumed"
        serial = key_serial.get(key)
        if serial is None:
            pk = prior_key.get(key)
            if pk is not None and (pk.serial, d.duration, d_lang) not in taken:
                serial, note = pk.serial, f"pairs with {pk.code}"
            else:
                serial, nxt = nxt, nxt + 1
            key_serial[key] = serial
        if (serial, d.duration, d_lang) in taken:
            serial, nxt = nxt, nxt + 1
            note = "same title, length and language as another spot: own serial"
        taken.add((serial, d.duration, d_lang))
        title = simplify_title(d.title, words, spelling.get(d_lang, "") if found else "") if simplify else d.title
        out.append(
            Proposal(f"{client}{d.duration}{d_lang}{serial:0{width}d}", title, d.isci, d.duration, serial, note, True, d_lang)
        )
    new = sorted(
        (p for p in out if p.is_new), key=lambda p: (p.lang, p.serial is None, p.serial or 0, p.duration or 0)
    )
    return new + [p for p in out if not p.is_new]
