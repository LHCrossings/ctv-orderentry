# Phoenix TV SRT recorder - runs 24/7 on VIDEO-02 (Sacramento office) as the
# Scheduled Task "PhoenixSrtRecorder" (SYSTEM, at startup, restart on failure).
#
# Phoenix's SRT listener accepts ONE caller (verified 2026-10-14), so one ffmpeg ("relay")
# holds that connection and forwards the SRT payload UNTOUCHED - opaque 1316-byte blocks,
# seven TS packets each, no demux, no remux - to (a) the Datavideo decoder on the office LAN
# (its input is udp://@:6005; it feeds SDI -> EDIT-02 Etere Memory, to be retired later) and
# (b) a loopback port. A second ffmpeg ("recorder") reads the loopback port and writes hourly
# .ts segments aligned to the wall clock; a .ts is readable up to the last byte written, so a
# crash or power loss costs nothing already on disk. Once a segment is closed it is remuxed
# (-c copy) to a standard .mp4 and the .ts is deleted.
#
# Why not one ffmpeg with two outputs: ffmpeg's remux splits each interlaced frame into two
# field packets and invents the second field's PTS (Phoenix sends it without one); desktop
# decoders cope, the Datavideo's Pi hardware decoder froze / went black (10/15). The relay
# therefore never parses the stream; only the recording path does, offline-safe.
#
# Retention: keep KEEP_HOURS of recordings, and delete oldest-first whenever C: has less than
# MIN_FREE_GB free. Never touches the segment being written.
# The stream carries two audio tracks: AAC LC (the programme audio) and an AC-3 track that is
# DIGITAL SILENCE (-91 dB over whole hours, measured 10/17). The .ts keeps both; the .mp4 keeps
# ONE AAC track: Phoenix's AAC copied, or re-encoded with decoder errors ignored when a frame
# is damaged (see Remux-Closed). The AC-3 is never used: 15 hours of 10/16-10/17 were written
# with it as the "fallback" and every one of them is silent (Maija 10/17).
# Status: $Root\status.json rewritten every loop. Logs: $Root\logs\

$Root       = "C:\PhoenixSRT"
$Src        = "srt://47.181.223.221:6005?mode=caller&latency=8000000"   # latency in microseconds (8 s): a packet SRT cannot recover inside this window is DROPPED (relay log "RCV-DROPPED"); 2 s lost 269 packets in three episodes on 10/15 03:53-04:55 and every hour's AAC broke. Delay is irrelevant for a recording.
$RelayTarget = ""   # was udp://10.1.50.98:6005?pkt_size=1316 (Datavideo); off since 21:30 PT 10/15 (Lee 10/16: no longer needed). "" = no relay
$LoopPort   = 6010                                     # loopback hand-off relay -> recorder
$MonitorTarget = "udp://127.0.0.1:6011?pkt_size=1316" # live-view tap: VLC on VIDEO-02 opens udp://@127.0.0.1:6011 ("Phoenix Live View" desktop shortcut). The relay fires it whether or not anyone listens; the recorder never sees this leg. "" = none
$ThumbTarget = "udp://127.0.0.1:6012?pkt_size=1316"   # confidence-monitor tap: thumb.ps1 grabs one frame a minute from it and serves it on :8081 for the Control Room (Maija 10/16). "" = none
$RecDir     = "$Root\rec"
$LogDir     = "$Root\logs"
$FFmpeg     = "$Root\bin\ffmpeg.exe"
$FFprobe    = "$Root\bin\ffprobe.exe"
$SegSeconds = 3600
$KeepHours  = 72    # three days (Lee 10/15), ~180 GB at 5.6 Mbps; fits C: with the MinFreeGB floor
$MinFreeGB  = 80
$LoopSleep  = 60
$Prefix     = "Phoenix"

New-Item -ItemType Directory -Force -Path $RecDir, $LogDir | Out-Null
$script:RemuxRetryAt = @{}   # segment name -> next remux attempt after an all-modes failure

function Log($msg) {
    $line = "{0:yyyy-MM-dd HH:mm:ss} {1}" -f (Get-Date), $msg
    Add-Content -Path ("$LogDir\recorder_{0:yyyyMMdd}.log" -f (Get-Date)) -Value $line
}

function Start-Relay {
    # Opaque pass-through: the SRT payload is read as 1316-byte "frames" and written verbatim,
    # one UDP datagram per block, to every target. Nothing is parsed.
    $ffLog = "$LogDir\relay_{0:yyyyMMdd_HHmmss}.log" -f (Get-Date)
    $args = @(
        "-hide_banner", "-loglevel", "warning", "-nostdin",
        "-f", "rawvideo", "-pixel_format", "gray", "-video_size", "1316x1",
        "-i", $Src,
        "-map", "0", "-c", "copy", "-f", "rawvideo", "-flush_packets", "1", "udp://127.0.0.1:${LoopPort}?pkt_size=1316"
    )
    if ($RelayTarget) { $args += @("-map", "0", "-c", "copy", "-f", "rawvideo", "-flush_packets", "1", $RelayTarget) }
    if ($MonitorTarget) { $args += @("-map", "0", "-c", "copy", "-f", "rawvideo", "-flush_packets", "1", $MonitorTarget) }
    if ($ThumbTarget) { $args += @("-map", "0", "-c", "copy", "-f", "rawvideo", "-flush_packets", "1", $ThumbTarget) }
    $p = Start-Process -FilePath $FFmpeg -ArgumentList $args -NoNewWindow -PassThru -RedirectStandardError $ffLog
    Log "relay started pid=$($p.Id) log=$ffLog"
    return $p
}

function Start-Recorder {
    $ffLog = "$LogDir\ffmpeg_{0:yyyyMMdd_HHmmss}.log" -f (Get-Date)
    $pattern = "$RecDir\${Prefix}_%Y%m%d_%H%M.ts"
    $args = @(
        "-hide_banner", "-loglevel", "warning", "-nostdin",
        "-fflags", "+discardcorrupt",   # a Phoenix reconnect joins the stream mid-frame; the continuity jump flags that torn PES corrupt and this drops it here instead of writing a broken AAC frame into the .ts (10/17)
        "-i", "udp://127.0.0.1:${LoopPort}?fifo_size=1000000&overrun_nonfatal=1",
        "-map", "0", "-c", "copy",
        "-f", "segment", "-segment_time", "$SegSeconds", "-segment_atclocktime", "1",
        "-reset_timestamps", "1", "-strftime", "1", "-segment_format", "mpegts",
        $pattern
    )
    $p = Start-Process -FilePath $FFmpeg -ArgumentList $args -NoNewWindow -PassThru -RedirectStandardError $ffLog
    Log "recorder started pid=$($p.Id) log=$ffLog"
    return $p
}

function Stop-Proc($p) { if ($p -and -not $p.HasExited) { Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue } }

function Current-Segment {
    # Segment names are the clock (Phoenix_YYYYMMDD_HHMM), so the newest NAME is the live file.
    Get-ChildItem $RecDir -Filter "${Prefix}_*.ts" -File | Sort-Object Name -Descending | Select-Object -First 1
}

function Remux-Closed($recProc) {
    # A segment is closed when the muxer has opened a NEWER one, or the recorder is not
    # running. Never by elapsed time: on 10/15 every relay reconnect (16:36, 17:35, 20:34)
    # paused writes for over 90 s and the old "not written for 90 s" rule remuxed the LIVE
    # hour at 38 and 40 minutes, each copy replacing the last on the NAS, so an editor
    # pulling in between got a partial hour named like a full one.
    $all = @(Get-ChildItem $RecDir -Filter "${Prefix}_*.ts" -File | Sort-Object Name)
    if (-not $all) { return }
    $live = if ($recProc -and -not $recProc.HasExited) { $all[-1].FullName } else { $null }
    foreach ($ts in $all | Where-Object { $_.FullName -ne $live }) {
        $mp4 = [IO.Path]::ChangeExtension($ts.FullName, ".mp4")
        $tmp = "$mp4.part"
        if ($ts.Length -lt 1MB) { Log "discard tiny segment $($ts.Name) ($($ts.Length) B)"; Remove-Item $ts.FullName -Force; continue }
        # A segment whose remux failed in every mode is retried once an hour, not every loop.
        if ($script:RemuxRetryAt.ContainsKey($ts.Name) -and (Get-Date) -lt $script:RemuxRetryAt[$ts.Name]) { continue }
        # Like EDIT-02 Memory's export (Lee 10/15): video + ONE audio track and a tmcd timecode
        # track from the segment's start time (the .ts creation time, drop-frame).
        # Audio: the AAC track copied. When one ADTS frame is damaged the mp4 muxer refuses the
        # whole track (every Phoenix reconnect tears the frame at the cut: 14 of 24 hours on
        # 10/16, with ZERO SRT packet loss since latency went to 8 s). Fallback: decode that AAC
        # with errors ignored (one torn frame = one lost 21 ms frame) and re-encode it to AAC
        # 256k (~4 min per hour on this Xeon, mostly the 2.3 GB copy). NEVER the AC-3 track:
        # it is digital silence end to end (-91 dB measured 10/17 on three hours and the live
        # stream), which is why the 10/15 "ac3 copy" fallback produced Maija's silent hours.
        $tc = $ts.CreationTime.ToString("HH:mm:ss") + ";00"
        $streams = (& $FFprobe -v error -select_streams a -show_entries stream=index,codec_name -of csv=p=0 $ts.FullName) -split "`r?`n"
        $aac = $streams | Where-Object { $_ -match ',aac' } | Select-Object -First 1
        $enc = @("-c:a", "aac", "-b:a", "256k", "-ar", "48000", "-ac", "2")
        $modes = @()
        # pre = input options (before -i); args = stream selection + audio codec (after -map 0:v:0)
        if ($aac) {
            $modes += @{ label = "aac copy"; pre = @(); args = @("-map", ("0:" + ($aac -split ',')[0]), "-c:a", "copy") }
            $modes += @{ label = "aac re-encoded, damaged frames dropped"; pre = @("-err_detect", "ignore_err"); args = @("-map", ("0:" + ($aac -split ',')[0])) + $enc }
        } else {
            $modes += @{ label = "first audio -> aac (no AAC track found)"; pre = @("-err_detect", "ignore_err"); args = @("-map", "0:a:0?") + $enc }
        }
        $ok = $false
        foreach ($m in $modes) {
            $ffArgs = @("-hide_banner", "-loglevel", "error", "-nostdin") + $m.pre + @("-i", $ts.FullName, "-map", "0:v:0") + $m.args + @("-c:v", "copy", "-timecode", $tc, "-movflags", "+faststart", "-f", "mp4", "-y", $tmp)
            # Start-Process, never `&` + 2>&1: ffmpeg's stderr warnings would become PowerShell errors (thumb.ps1 lesson 10/16).
            $errFile = "$LogDir\remux_last_stderr.txt"
            $proc = Start-Process -FilePath $FFmpeg -ArgumentList $ffArgs -NoNewWindow -PassThru -Wait -RedirectStandardError $errFile
            $code = $proc.ExitCode
            if (Test-Path $errFile) { Add-Content "$LogDir\remux_errors.log" ("--- {0} {1} ---" -f $ts.Name, $m.label); Get-Content $errFile | Add-Content "$LogDir\remux_errors.log" }
            if ($code -eq 0 -and (Test-Path $tmp)) {
                $dur = & $FFprobe -v error -show_entries format=duration -of csv=p=0 $tmp
                Move-Item $tmp $mp4 -Force
                try { Remove-Item $ts.FullName -Force -ErrorAction Stop }
                catch { Log "could not delete $($ts.Name) after remux: $($_.Exception.Message)" }
                $script:RemuxRetryAt.Remove($ts.Name)
                Log ("remuxed {0} -> {1} ({2:N0} MB, {3} s, audio: {4})" -f $ts.Name, (Split-Path $mp4 -Leaf), ((Get-Item $mp4).Length/1MB), $dur, $m.label)
                $ok = $true; break
            }
            Add-Content "$LogDir\remux_errors.log" ("--- {0} {1}: exit {2} ---" -f $ts.Name, $m.label, $code)
            if (Test-Path $tmp) { Remove-Item $tmp -Force }
        }
        if (-not $ok) {
            $script:RemuxRetryAt[$ts.Name] = (Get-Date).AddHours(1)
            Log "remux FAILED for $($ts.Name) in every audio mode; .ts kept, retry in 1 h (see remux_errors.log)"
        }
    }
}

function Sweep {
    $cur = Current-Segment
    $files = @(Get-ChildItem $RecDir -Include "${Prefix}_*.mp4", "${Prefix}_*.ts" -Recurse |
             Where-Object { -not $cur -or $_.FullName -ne $cur.FullName } | Sort-Object LastWriteTime)
    $old = (Get-Date).AddHours(-$KeepHours)
    foreach ($f in $files | Where-Object { $_.LastWriteTime -lt $old }) {
        Remove-Item $f.FullName -Force; Log "retention: deleted $($f.Name) (older than $KeepHours h)"
    }
    $files = @($files | Where-Object { Test-Path $_.FullName })
    while (((Get-Volume -DriveLetter C).SizeRemaining / 1GB) -lt $MinFreeGB -and $files.Count -gt 0) {
        $f = $files[0]; $files = @($files | Select-Object -Skip 1)
        Remove-Item $f.FullName -Force; Log "low disk: deleted $($f.Name)"
    }
    Get-ChildItem $LogDir -Filter "*.log" | Where-Object { $_.LastWriteTime -lt (Get-Date).AddDays(-30) } | Remove-Item -Force
}

function Write-Status($relayProc, $recProc) {
    $cur = Current-Segment
    $st = [ordered]@{
        updated       = (Get-Date).ToString("s")
        relay_pid     = if ($relayProc -and -not $relayProc.HasExited) { $relayProc.Id } else { $null }
        recorder_pid  = if ($recProc -and -not $recProc.HasExited) { $recProc.Id } else { $null }
        recording     = [bool]($relayProc -and -not $relayProc.HasExited -and $recProc -and -not $recProc.HasExited)
        current_file  = if ($cur) { $cur.Name } else { $null }
        current_mb    = if ($cur) { [math]::Round($cur.Length / 1MB, 1) } else { 0 }
        current_age_s = if ($cur) { [int]((Get-Date) - $cur.LastWriteTime).TotalSeconds } else { $null }
        free_gb       = [math]::Round((Get-Volume -DriveLetter C).SizeRemaining / 1GB, 1)
        files         = (Get-ChildItem $RecDir -Filter "${Prefix}_*" | Measure-Object).Count
        source        = $Src
        relay_target  = $RelayTarget
        monitor_target = $MonitorTarget
        thumb_target  = $ThumbTarget
    }
    ($st | ConvertTo-Json) | Set-Content "$Root\status.json" -Encoding UTF8
}

Log "loop starting (keep ${KeepHours}h, min free ${MinFreeGB} GB, relay=$RelayTarget)"
$recProc = Start-Recorder      # listener first, so the relay's first datagrams land
Start-Sleep -Seconds 2
$relayProc = Start-Relay
$lastSize = -1; $stall = 0
while ($true) {
    Start-Sleep -Seconds $LoopSleep
    try {
        if ($recProc.HasExited)   { Log "recorder exited code=$($recProc.ExitCode); restarting"; $recProc = Start-Recorder; $lastSize = -1; $stall = 0 }
        if ($relayProc.HasExited) {
            # Phoenix's SRT session breaks a few times a day (10/15: 11:54, 11:59, 16:35, 16:37,
            # 17:34, 20:34; the stream's own clock jumps forward minutes at each, i.e. a restart
            # or source switch on their side, never packet loss here). Their listener takes ONE
            # caller and can still hold our dead session for a minute, so the first reconnect
            # may be refused: retry every 5 s instead of once per loop.
            Log "relay exited code=$($relayProc.ExitCode); reconnecting"
            for ($try = 1; $try -le 6; $try++) {
                Start-Sleep -Seconds 5
                $relayProc = Start-Relay
                Start-Sleep -Seconds 5
                if (-not $relayProc.HasExited) { break }
                Log "relay exited again code=$($relayProc.ExitCode) (attempt $try of 6; Phoenix's single caller slot may still hold our dead session)"
            }
            $lastSize = -1; $stall = 0
        }
        # Stall guard: an SRT that stops delivering leaves both processes alive with a frozen file.
        $cur = Current-Segment
        $size = if ($cur) { $cur.Length } else { 0 }
        if ($size -eq $lastSize) { $stall++ } else { $stall = 0 }
        $lastSize = $size
        if ($stall -ge 3) {
            Log "no bytes written for $($stall * $LoopSleep) s; restarting relay pid=$($relayProc.Id) and recorder pid=$($recProc.Id)"
            Stop-Proc $relayProc; Stop-Proc $recProc; Start-Sleep -Seconds 5
            $recProc = Start-Recorder; Start-Sleep -Seconds 2; $relayProc = Start-Relay; $lastSize = -1; $stall = 0
        }
        Remux-Closed $recProc
        Sweep
        Write-Status $relayProc $recProc
    } catch {
        Log "loop error: $($_.Exception.Message)"
    }
}
