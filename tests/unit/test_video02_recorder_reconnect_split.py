"""A Phoenix reconnect must never land INSIDE a recording (10/30 0800, Maija: black flashes in EDIUS).

The relay dies ~10x a day. Until 10/30 the recorder kept writing the same .ts across the join,
so the file carried a field cut off mid-slice, two P-fields of the new session referencing
pictures not in the file, and only then the first IDR; ffmpeg flagged every third frame
"corrupt" for the rest of the hour and EDIUS showed black flashes from there to the end.
record.ps1 now closes the segment on every relay exit (a fresh recorder opens the next file on a
clean keyframe) and marks the cut segment so Remux-Closed trims its tail to the last keyframe.
"""

import re
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[2] / "video02_recorder" / "record.ps1"


def _body() -> str:
    text = _SCRIPT.read_text()
    return re.sub(r"(?m)#.*$", "", text)


def _block(text: str, start_marker: str, end_marker: str) -> str:
    i = text.index(start_marker)
    j = text.index(end_marker, i)
    return text[i:j]


def test_a_relay_exit_restarts_the_recorder_before_reconnecting():
    body = _body()
    branch = _block(body, 'Log "relay exited code=', "for ($try = 1")
    assert "Mark-CutTail" in branch, "the live segment must be marked for tail trimming"
    assert "Stop-Proc $recProc" in branch and "$recProc = Start-Recorder" in branch, (
        "a reconnect must open a NEW segment, never continue the old .ts across the join"
    )


def test_every_abnormal_segment_end_is_marked():
    body = _body()
    assert body.count("Mark-CutTail") >= 4  # startup, recorder exit, relay exit, stall restart
    stall = _block(body, 'Log "no bytes written for', "Remux-Closed $recProc")
    assert "Mark-CutTail" in stall
    recorder_exit = _block(body, "if ($recProc.HasExited)", "if ($relayProc.HasExited)")
    assert "Mark-CutTail" in recorder_exit


def test_remux_trims_a_marked_segment_to_its_last_keyframe():
    body = _body()
    remux = _block(body, "function Remux-Closed", "function Sweep")
    assert "$script:CutTail.ContainsKey($ts.Name)" in remux
    assert "Last-KeyframeSeconds" in remux
    assert '"-to"' in remux, "the trim is an ffmpeg -to on the remux"
    # the trim option must sit on the remux command line, before the output file
    cmd = re.search(r"\$ffArgs = (.+)", remux).group(1)
    assert "$trim" in cmd and cmd.index("$trim") < cmd.index("$tmp")
    assert "$script:CutTail.Remove($ts.Name)" in remux


def test_keyframe_position_is_in_output_time():
    body = _body()
    fn = _block(body, "function Last-KeyframeSeconds", "function Remux-Closed")
    assert "start_time" in fn and "- $start" in fn, (
        "ffprobe reports input time; ffmpeg -to compares output time (input minus start_time)"
    )
