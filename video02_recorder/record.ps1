# Phoenix TV SRT recorder - runs 24/7 on VIDEO-02 (Sacramento office) as the
# Scheduled Task "PhoenixSrtRecorder" (SYSTEM, at startup, restart on failure).
#
# ffmpeg dials Phoenix's SRT listener (single-caller; VIDEO-02 is the only client)
# and copies the transport stream to hourly .ts segments aligned to the wall clock. A .ts is readable up to the last byte written, so a crash or power
# loss costs nothing already on disk. Once a segment is closed it is remuxed
# (-c copy) to a standard .mp4 and the .ts is deleted.
#
# Retention: keep KEEP_HOURS of recordings, and delete oldest-first whenever
# C: has less than MIN_FREE_GB free. Never touches the segment being written.
#
# The stream carries two audio tracks (AAC LC + AC-3); both are kept.
# Status: $Root\status.json rewritten every loop (pid, current file, free GB).
# Logs:   $Root\logs\recorder_YYYYMMDD.log and ffmpeg_YYYYMMDD_HHMMSS.log

$Root       = "C:\PhoenixSRT"
$Src        = "srt://47.181.223.221:6005?mode=caller&latency=2000000"   # latency in microseconds (2 s)
# Phoenix's listener accepts ONE caller (verified 2026-10-14: refused while the Datavideo was
# connected, accepted on the first try once it was moved off). VIDEO-02 is that one caller;
# for now (Lee, 10/15) the untouched TS is also relayed over the Comcast subnet to the Datavideo
# decoder (10.1.50.98, input set to udp://@:6005) so EDIT-02 Memory keeps recording in parallel;
# the Datavideo is to be retired later. Empty string = no relay.
$Relay      = "udp://10.1.50.98:6005?pkt_size=1316"
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

function Start-Recorder {
    $ffLog = "$LogDir\ffmpeg_{0:yyyyMMdd_HHmmss}.log" -f (Get-Date)
    $pattern = "$RecDir\${Prefix}_%Y%m%d_%H%M.ts"
    $args = @(
        "-hide_banner", "-loglevel", "warning", "-nostdin",
        "-i", $Src,
        "-map", "0", "-c", "copy",
        "-f", "segment", "-segment_time", "$SegSeconds", "-segment_atclocktime", "1",
        "-reset_timestamps", "1", "-strftime", "1", "-segment_format", "mpegts",
        $pattern
    )
    if ($Relay) { $args += @("-map", "0", "-c", "copy", "-f", "mpegts", $Relay) }
    $p = Start-Process -FilePath $FFmpeg -ArgumentList $args -NoNewWindow -PassThru `
         -RedirectStandardError $ffLog
    Log "ffmpeg started pid=$($p.Id) log=$ffLog"
    return $p
}

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

function Write-Status($proc) {
    $cur = Current-Segment
    $st = [ordered]@{
        updated      = (Get-Date).ToString("s")
        ffmpeg_pid   = if ($proc -and -not $proc.HasExited) { $proc.Id } else { $null }
        recording    = [bool]($proc -and -not $proc.HasExited)
        current_file = if ($cur) { $cur.Name } else { $null }
        current_mb   = if ($cur) { [math]::Round($cur.Length / 1MB, 1) } else { 0 }
        current_age_s = if ($cur) { [int]((Get-Date) - $cur.LastWriteTime).TotalSeconds } else { $null }
        free_gb      = [math]::Round((Get-Volume -DriveLetter C).SizeRemaining / 1GB, 1)
        files        = (Get-ChildItem $RecDir -Filter "${Prefix}_*" | Measure-Object).Count
        source       = $Src
    }
    ($st | ConvertTo-Json) | Set-Content "$Root\status.json" -Encoding UTF8
}

Log "recorder loop starting (keep ${KeepHours}h, min free ${MinFreeGB} GB)"
$proc = Start-Recorder
$lastSize = -1; $stall = 0
while ($true) {
    Start-Sleep -Seconds $LoopSleep
    try {
        if ($proc.HasExited) {
            Log "ffmpeg exited code=$($proc.ExitCode); restarting in 5 s"
            Start-Sleep -Seconds 5
            $proc = Start-Recorder; $lastSize = -1; $stall = 0
        } else {
            # Stall guard: a live SRT that stops delivering leaves ffmpeg running with a frozen file.
            $cur = Current-Segment
            $size = if ($cur) { $cur.Length } else { 0 }
            if ($size -eq $lastSize) { $stall++ } else { $stall = 0 }
            $lastSize = $size
            if ($stall -ge 3) {
                Log "no bytes written for $($stall * $LoopSleep) s; killing ffmpeg pid=$($proc.Id)"
                Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
                Start-Sleep -Seconds 5
                $proc = Start-Recorder; $lastSize = -1; $stall = 0
            }
        }
        Remux-Closed
        Sweep
        Write-Status $proc
    } catch {
        Log "loop error: $($_.Exception.Message)"
    }
}
