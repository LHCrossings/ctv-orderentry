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
# The stream carries two audio tracks (AAC LC + AC-3); both are kept.
# Status: $Root\status.json rewritten every loop. Logs: $Root\logs\

$Root       = "C:\PhoenixSRT"
$Src        = "srt://47.181.223.221:6005?mode=caller&latency=2000000"   # latency in microseconds (2 s)
$RelayTarget = "udp://10.1.50.98:6005?pkt_size=1316"  # Datavideo (Comcast subnet). "" = no relay
$LoopPort   = 6010                                     # loopback hand-off relay -> recorder
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
    $p = Start-Process -FilePath $FFmpeg -ArgumentList $args -NoNewWindow -PassThru -RedirectStandardError $ffLog
    Log "relay started pid=$($p.Id) log=$ffLog"
    return $p
}

function Start-Recorder {
    $ffLog = "$LogDir\ffmpeg_{0:yyyyMMdd_HHmmss}.log" -f (Get-Date)
    $pattern = "$RecDir\${Prefix}_%Y%m%d_%H%M.ts"
    $args = @(
        "-hide_banner", "-loglevel", "warning", "-nostdin",
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
    Get-ChildItem $RecDir -Filter "${Prefix}_*.ts" | Sort-Object LastWriteTime -Descending | Select-Object -First 1
}

function Remux-Closed {
    # Any .ts not written to for 90 s is closed (ffmpeg writes every second).
    $cut = (Get-Date).AddSeconds(-90)
    foreach ($ts in Get-ChildItem $RecDir -Filter "${Prefix}_*.ts" | Where-Object { $_.LastWriteTime -lt $cut }) {
        $mp4 = [IO.Path]::ChangeExtension($ts.FullName, ".mp4")
        $tmp = "$mp4.part"
        if ($ts.Length -lt 1MB) { Log "discard tiny segment $($ts.Name) ($($ts.Length) B)"; Remove-Item $ts.FullName -Force; continue }
        & $FFmpeg -hide_banner -loglevel error -nostdin -i $ts.FullName -map 0 -c copy -movflags +faststart -f mp4 -y $tmp 2>> "$LogDir\remux_errors.log"
        if ($LASTEXITCODE -eq 0 -and (Test-Path $tmp)) {
            $dur = & $FFprobe -v error -show_entries format=duration -of csv=p=0 $tmp
            Move-Item $tmp $mp4 -Force
            Remove-Item $ts.FullName -Force
            Log ("remuxed {0} -> {1} ({2:N0} MB, {3} s)" -f $ts.Name, (Split-Path $mp4 -Leaf), ((Get-Item $mp4).Length/1MB), $dur)
        } else {
            Log "remux FAILED for $($ts.Name) (exit $LASTEXITCODE); .ts kept"
            if (Test-Path $tmp) { Remove-Item $tmp -Force }
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
        if ($relayProc.HasExited) { Log "relay exited code=$($relayProc.ExitCode); restarting in 5 s"; Start-Sleep -Seconds 5; $relayProc = Start-Relay; $lastSize = -1; $stall = 0 }
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
        Remux-Closed
        Sweep
        Write-Status $relayProc $recProc
    } catch {
        Log "loop error: $($_.Exception.Message)"
    }
}
