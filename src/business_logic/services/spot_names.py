"""Propose house names for newly delivered spots: `<CLIENT><DUR><LANG><serial>: <title> (<ISCI>)`.

Two pasted texts go in, a list of proposed lines comes out. Nothing is read from or written
to Etere: FILMATI holds only some of a client's names (5 of 21 for Woof on 11/20), so the
next serial comes from the client's own list (V:\\Commercials\\<client>), pasted by the
operator.

Rule (Lee 11/20): the cuts of ONE creative that differ only in length share one serial
(WOOF15E20 + WOOF30E20). Creatives take serials in the order they appear in the delivery.
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

    @property
    def line(self) -> str:
        return f"{self.code}: {self.title} ({self.isci})"


class SpotNameError(ValueError):
    pass


def parse_prior(text: str) -> tuple[list[PriorSpot], list[str]]:
    """Lines of the client's own list. Returns (spots, lines that did not parse)."""
    spots, bad = [], []
    for raw in (text or "").splitlines():
        if not raw.strip():
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
    cells = [c.strip() for c in re.split(r"[\t\r\n]+", "\n".join(lines)) if c.strip()]
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


def creative_key(title: str) -> str:
    """The title without its length and trailing version, so two cuts of one creative match."""
    toks = [t for t in re.split(r"[_\s]+", title.lower().replace("×", "x")) if t]
    toks = [t for t in toks if not (_DUR_TOKEN.match(t) and int(_DUR_TOKEN.match(t).group(1)) in _KNOWN_LENGTHS)]
    while len(toks) > 1 and _VERSION_TOKEN.match(toks[-1]):
        toks.pop()
    return " ".join(toks)


def propose(
    delivered: list[Delivered],
    prior: list[PriorSpot],
    client: str = "",
    lang: str = "",
    next_serial: int | None = None,
) -> list[Proposal]:
    client = (client or "").strip().upper()
    lang = (lang or "").strip().upper()
    if not client and prior:
        client = Counter(p.client for p in prior).most_common(1)[0][0]
    if not lang and prior:
        lang = Counter(p.lang for p in prior).most_common(1)[0][0]
    if not client or not lang:
        raise SpotNameError("No prior list to read the client code and language from: fill in both fields.")
    mine = [p for p in prior if p.client == client]
    width = max([2] + [p.serial_width for p in mine])
    nxt = next_serial or (max((p.serial for p in mine), default=0) + 1)

    by_isci = {p.isci.upper(): p for p in prior}
    taken = {(p.serial, p.duration) for p in mine}
    key_serial: dict[str, int] = {}
    prior_key: dict[str, PriorSpot] = {}
    for p in mine:
        prior_key.setdefault(creative_key(p.title), p)

    out: list[Proposal] = []
    for d in delivered:
        old = by_isci.get(d.isci.upper())
        if old is not None:
            out.append(Proposal(old.code, old.title, old.isci, old.duration, old.serial, "already on the list", False))
            continue
        if d.duration is None:
            out.append(Proposal("?", d.title, d.isci, None, None, "no length found: add :15 or :30", True))
            continue
        key = creative_key(d.title)
        note = ""
        serial = key_serial.get(key)
        if serial is None:
            pk = prior_key.get(key)
            if pk is not None and (pk.serial, d.duration) not in taken:
                serial, note = pk.serial, f"pairs with {pk.code}"
            else:
                serial, nxt = nxt, nxt + 1
            key_serial[key] = serial
        if (serial, d.duration) in taken:
            serial, nxt = nxt, nxt + 1
            note = "same title and length as another spot: own serial"
        taken.add((serial, d.duration))
        out.append(Proposal(f"{client}{d.duration}{lang}{serial:0{width}d}", d.title, d.isci, d.duration, serial, note))
    new = sorted((p for p in out if p.is_new), key=lambda p: (p.serial is None, p.serial or 0, p.duration or 0))
    return new + [p for p in out if not p.is_new]
