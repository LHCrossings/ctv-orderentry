"""Parser sidecars must travel with their IO and never look like an order.

A `<io>.adm.json` (Admerasia vision cache) left behind in incoming when its IO
moved to Entered/ is a file the queue cannot classify, so it rendered as a
pending "Unrecognized file" row that outlived the order it belonged to — the
stray-row filter named only .manifest.json and .ai.json.
"""

import sys
from pathlib import Path

_root = Path(__file__).parent.parent.parent
for _p in (_root, _root / "src"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from business_logic.services.backwrite_manifest import (  # noqa: E402
    ENTERED_DIRNAME,
    SIDECAR_SUFFIXES,
    _move_io_to_entered,
    move_sidecars,
)


def _incoming(tmp_path: Path) -> Path:
    inc = tmp_path / "incoming"
    (inc / ENTERED_DIRNAME).mkdir(parents=True)
    return inc


def test_entered_move_takes_the_sidecars_along(tmp_path):
    inc = _incoming(tmp_path)
    io = inc / "order.pdf"
    io.write_text("pdf")
    for suffix in (".adm.json", ".adm-legend.json"):
        (inc / (io.name + suffix)).write_text("{}")
    other = inc / "unrelated.pdf"
    other.write_text("pdf")

    assert _move_io_to_entered(io) is True

    assert [f.name for f in inc.iterdir() if f.is_file()] == ["unrelated.pdf"]
    assert sorted(f.name for f in (inc / ENTERED_DIRNAME).iterdir()) == [
        "order.pdf", "order.pdf.adm-legend.json", "order.pdf.adm.json",
    ]


def test_sweep_rescues_a_sidecar_whose_io_already_moved(tmp_path):
    """Once the IO has moved, nothing else keys on its name — so an orphaned
    sidecar would sit in incoming forever without an unconditional sweep."""
    inc = _incoming(tmp_path)
    entered = inc / ENTERED_DIRNAME
    (entered / "order.pdf").write_text("pdf")
    (inc / "order.pdf.adm.json").write_text("{}")

    move_sidecars(inc / "order.pdf", entered)

    assert not [f for f in inc.iterdir() if f.is_file()]
    assert (entered / "order.pdf.adm.json").exists()


def test_missing_sidecars_are_a_no_op(tmp_path):
    inc = _incoming(tmp_path)
    io = inc / "order.pdf"
    io.write_text("pdf")
    assert _move_io_to_entered(io) is True
    assert (inc / ENTERED_DIRNAME / "order.pdf").exists()


def test_every_known_sidecar_suffix_is_filtered():
    """The queue's stray-row filter tests `endswith(SIDECAR_SUFFIXES)`, so a new
    sidecar suffix is covered the moment it is added to that one tuple."""
    assert ".adm.json" in SIDECAR_SUFFIXES, "the suffix that caused the bug"
    for suffix in SIDECAR_SUFFIXES:
        assert ("some order.pdf" + suffix).endswith(SIDECAR_SUFFIXES)
    assert not "some order.pdf".endswith(SIDECAR_SUFFIXES)
    assert not "notes.json".endswith(SIDECAR_SUFFIXES)
