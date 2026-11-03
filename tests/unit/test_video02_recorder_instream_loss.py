"""An in-stream loss must never stay INSIDE a recording (11/1 07:00, Jenna: black flashes in EDIUS).

The 10/30 fix split the recording at every Phoenix reconnect. The 11/1 07:00 hour had no
reconnect: the relay logged zero SRT drops, yet the recorder's demuxer dropped 16 torn packets
over 6 s at 07:02:54-07:03:00 (the keyframe cadence has holes from 174.6 s to 181.1 s of the
file), the seconds the previous hour's remux finished on the same disk. record.ps1 now reads
the recorder's own ffmpeg log every loop (Read-LossEvents: each "Packet corrupt" line with its
source-clock dts, logged with the wall time and the last remux window), and Remux-Closed cuts a
segment that carried a loss into pieces around each burst (Loss-Bursts / Segment-Pieces): the
head ends on the last keyframe before the loss, the next piece opens on the first keyframe well
after it, named for its own start minute like a reconnect segment. The recorder's loopback
socket also gets a 64 MB receive buffer, so a stalled reader thread cannot lose datagrams there.
"""

import re
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[2] / "video02_recorder" / "record.ps1"


def _body() -> str:
    return re.sub(r"(?m)#.*$", "", _SCRIPT.read_text())


def _block(text: str, start_marker: str, end_marker: str) -> str:
    i = text.index(start_marker)
    j = text.index(end_marker, i)
    return text[i:j]


def test_recorder_socket_has_a_large_receive_buffer():
    body = _body()
    rec = _block(body, "function Start-Recorder", "function Stop-Proc")
    m = re.search(r"udp://127\.0\.0\.1:\$\{LoopPort\}\?([^\"]+)", rec)
    assert m, "the recorder reads the relay's loopback port"
    opts = dict(kv.split("=") for kv in m.group(1).split("&"))
    assert int(opts["buffer_size"]) >= 32 * 1024 * 1024, "OS socket receive buffer, not only ffmpeg's fifo"
    assert "fifo_size" in opts and opts.get("overrun_nonfatal") == "1"


def test_the_loop_reads_the_recorder_log_before_each_remux():
    body = _body()
    loop = _block(body, "while ($true)", "} catch {")
    assert "Read-LossEvents $script:RecLog" in loop
    assert loop.index("Read-LossEvents") < loop.index("Remux-Closed $recProc")
    rec = _block(body, "function Start-Recorder", "function Stop-Proc")
    assert "$script:RecLog = $ffLog" in rec, "the loop must know which ffmpeg log is live"


def test_loss_events_keep_the_source_dts_and_log_the_remux_window():
    body = _body()
    fn = _block(body, "function Read-LossEvents", "function Loss-Bursts")
    assert r"Packet corrupt \(stream = (\d+), dts = (\d+)\)" in fn
    assert "[IO.FileShare]::ReadWrite" in fn, "the log is open for writing by ffmpeg"
    assert "$script:LogPos[$ffLog]" in fn, "read only what the log gained since the last loop"
    assert "LastWriteTime" in fn and "$script:LastRemux" in fn, "wall time + last remux: the correlation data"


def test_bursts_are_matched_to_a_segment_by_source_clock_with_the_pcr_wrap():
    body = _body()
    fn = _block(body, "function Loss-Bursts", "function Segment-Pieces")
    assert "[Math]::Pow(2, 33) / 90000.0" in fn
    # the PowerShell comma operator binds tighter than + and -: each candidate is parenthesised
    assert "@($sec, ($sec + $wrap), ($sec - $wrap))" in fn
    assert "-lt 10" in fn, "packets less than 10 s apart are one burst"


def test_pieces_are_cut_at_keyframes_clear_of_the_loss():
    body = _body()
    fn = _block(body, "function Segment-Pieces", "function Piece-Name")
    assert "($b.from - 0.5) 'before'" in fn, "head ends before the first dropped packet"
    assert "($b.to + 2.0) 'after'" in fn, "next piece opens well after the last one (keyframes were missing for 6.5 s on 11/1)"
    assert "-ge 2" in fn, "pieces under 2 s are dropped"
    kf = _block(body, "function Keyframe-Near", "function Read-LossEvents")
    assert "dts_time" in kf and "- $range.start" in kf, "-to compares dts in output time (input minus start_time)"


def test_remux_writes_every_piece_and_deletes_the_ts_only_after_all_succeed():
    body = _body()
    remux = _block(body, "function Remux-Closed", "function Sweep")
    assert "Loss-Bursts $range" in remux and "Segment-Pieces $ts.FullName $range $bursts" in remux
    assert "foreach ($pc in $pieces)" in remux
    assert '"-noaccurate_seek", "-ss"' in remux, "a later piece starts with an input seek 0.3 s ahead of its keyframe"
    assert "$pc.from - 0.3" in remux
    assert '("{0:F3}" -f ($end - $seek))' in remux, "-to is relative to the input -ss on a seeked piece"
    cmd = re.search(r"\$ffArgs = (.+)", remux).group(1)
    assert cmd.index("$pre") < cmd.index('"-i"') < cmd.index("$trim") < cmd.index("$tmp")
    assert "Piece-Name $pStart" in remux, "later pieces are named for their own start minute"
    assert remux.index("if ($allOk)") < remux.index("Remove-Item $ts.FullName -Force -ErrorAction Stop")
    assert "$script:CutTail.Remove($ts.Name)" in remux
    # the reconnect tail trim still applies, to the LAST piece
    assert "if ($end -eq $null) { $end = $tailTo }" in remux


def test_piece_names_match_the_archive_pattern():
    body = _body()
    fn = _block(body, "function Piece-Name", "function Remux-Closed")
    assert 'ToString("yyyyMMdd_HHmm")' in fn
    archive = (_SCRIPT.parent / "archive.ps1").read_text()
    assert r"_(\d{4})(\d{2})(\d{2})_\d{4}\.mp4$" in archive, "archive.ps1 Day-Of accepts exactly Phoenix_YYYYMMDD_HHMM.mp4"
