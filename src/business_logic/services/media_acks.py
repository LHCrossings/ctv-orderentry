"""Media Check dismissals — master control's "this file is fine" mark on a nightly finding.

2026-09-15: the McD Seattle billboards MD07BBV418 / MD06BBM418 (6 and 7 s, ~11,000 bytes per
frame) tripped the 12,000 B/frame floor. Lee viewed the files: complete, just a low-rate encode.
The finding was right to ask, but nothing could answer it, so the header dot stayed amber and the
"Bad media file" toast re-fired in every browser session for the whole flight. This store holds
the answer.

A dismissal is keyed by the asset id and PINNED to the size of the copy that was judged: if the
file is re-ingested (size changes) the finding is new and alerts again. The finding itself stays
on the Media Check page, muted, with an Undo — the scan never stops looking, only the alarm is
answered. `data/` is gitignored and per host, like the event log.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

PRUNE_DAYS = 90  # a dismissal older than this is dropped on the next write


def _now_iso() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def finding_size(finding: dict) -> int:
    """The size a finding is judged on: the largest sized playout copy (what `classify`
    calls `best`). 0 when no copy is sized yet."""
    return max((int(c.get("size") or 0) for c in finding.get("copies") or []), default=0)


class MediaAcks:
    """JSON file `{ "<id_filmati>": {code, size, kind, note, at} }`; read on every call so a
    second worker or a hand edit is seen at once, written whole (the file is tiny)."""

    def __init__(self, path: Path):
        self.path = Path(path)

    # -- storage -------------------------------------------------------------------
    def load(self) -> dict[str, dict]:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return raw if isinstance(raw, dict) else {}

    def _save(self, acks: dict[str, dict]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(acks, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(self.path)

    @staticmethod
    def _prune(acks: dict[str, dict], now: dt.datetime | None = None) -> dict[str, dict]:
        now = now or dt.datetime.now().astimezone()
        cutoff = now - dt.timedelta(days=PRUNE_DAYS)
        out = {}
        for k, a in acks.items():
            try:
                at = dt.datetime.fromisoformat(a["at"])
                if at.tzinfo is None:
                    at = at.astimezone()
            except (KeyError, TypeError, ValueError):
                continue
            if at >= cutoff:
                out[k] = a
        return out

    # -- operations ------------------------------------------------------------------
    def ack(self, finding: dict, note: str = "", at: str | None = None) -> dict:
        """Dismiss `finding` (a scan finding dict). Returns the stored record."""
        rec = {
            "code": finding.get("code", ""),
            "kind": finding.get("kind", ""),
            "size": finding_size(finding),
            "note": (note or "").strip()[:200],
            "at": at or _now_iso(),
        }
        acks = self._prune(self.load())
        acks[str(finding["id_filmati"])] = rec
        self._save(acks)
        return rec

    def unack(self, id_filmati: int) -> dict | None:
        acks = self._prune(self.load())
        rec = acks.pop(str(id_filmati), None)
        self._save(acks)
        return rec

    def get(self, finding: dict) -> dict | None:
        """The dismissal that applies to `finding`, or None. A record pinned to a different
        copy size does not apply: the file changed since master control looked at it."""
        rec = self.load().get(str(finding.get("id_filmati")))
        if not rec:
            return None
        if int(rec.get("size") or 0) != finding_size(finding):
            return None
        return rec

    def apply(self, findings: list[dict]) -> tuple[list[dict], list[dict]]:
        """Split scan findings into (active, dismissed). Every returned finding carries an
        `ack` key: None for active, the record for dismissed. Callers that alarm (header dot,
        toast, event log) use the first list; the Media Check page shows both."""
        acks = self.load()
        active, dismissed = [], []
        for f in findings:
            rec = acks.get(str(f.get("id_filmati")))
            if rec and int(rec.get("size") or 0) == finding_size(f):
                dismissed.append({**f, "ack": rec})
            else:
                active.append({**f, "ack": None})
        return active, dismissed
