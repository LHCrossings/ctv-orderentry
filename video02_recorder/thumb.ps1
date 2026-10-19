# Phoenix TV confidence monitor - one JPEG a minute from the recorder's loopback tap
# (udp 6012, see record.ps1 $ThumbTarget), served over HTTP on :8081 so the Control Room
# Monitor Wall can show "what Phoenix is sending right now" (Maija 10/16: a picture that
# refreshes every minute is enough; it must never touch the recording). It reads its own
# UDP port and writes two files; the recorder is a separate process on a separate port.
#
# Audio (Maija 10/19: "an audio meter or something to tell us if audio is okay"): the same
# ffmpeg call measures 3 s of the AAC track (0:a:0) with astats, per channel RMS and peak in
# dBFS, written into live.json next to the frame and kept for 24 h in levels.json. The tap
# sits BEFORE the recorder, so silence here = Phoenix or the SRT path sent no sound; sound
# here with a mute recording = our recorder. Never measure 0:a:1: the stream's AC-3 track is
# digital silence (-123 dBFS, 10/17), so a meter on it reads "no audio" while the AAC is fine.
#
# Scheduled Task "PhoenixSrtThumb" (SYSTEM, at startup, restart on failure).
# GET http://100.90.230.98:8081/live.jpg   the latest frame (640 px wide, deinterlaced)
# GET http://100.90.230.98:8081/live.json  {captured, ok, error, elapsed_s, size,
#                                            audio: {track, window_s, L:{rms,peak}, R:{rms,peak}, silent}}
# GET http://100.90.230.98:8081/levels.json  the last 24 h of readings, oldest first:
#                                            [{t, l, lp, r, rp}, ...]  (RMS / peak per channel, dBFS)
# GET http://100.90.230.98:8081/frames/     the last 24 h of grabs (JSON list, oldest first)
# GET http://100.90.230.98:8081/frames/Phoenix_YYYYMMDD_HHMMSS.jpg   one archived frame
# Firewall: TCP 8081 from the tailnet (100.64.0.0/10) and the office LANs only.
# Log: C:\PhoenixSRT\logs\thumb_YYYYMMDD.log

$Root   = "C:\PhoenixSRT"
$LogDir = "$Root\logs"
$FFmpeg = "$Root\bin\ffmpeg.exe"
$Src    = "udp://127.0.0.1:6012?timeout=8000000&fifo_size=1000000&overrun_nonfatal=1"
$Jpg    = "$Root\live.jpg"
$Meta   = "$Root\live.json"
$Port   = 8081
$Every  = 60
$Frames = "$Root\frames"      # 24 h archive of every grab (Lee 10/16: "we may need them"); ~65 MB
$KeepH  = 24
$AudioS = 3                   # seconds of audio measured per grab
$AStats = "thumb_astats.txt"  # astats output, relative to $LogDir (a drive colon in the filter value breaks ffmpeg's parser)
$LevelsFile = "$Root\levels.json"
$Floor  = -120.0              # dBFS written for -inf (digital silence); JSON cannot carry -Infinity
$Silent = -60.0               # RMS at or below this on BOTH channels = "no audio"

New-Item -ItemType Directory -Force -Path $LogDir, $Frames | Out-Null

function Log($msg) {
    $line = "{0:yyyy-MM-dd HH:mm:ss} {1}" -f (Get-Date), $msg
    Add-Content -Path ("$LogDir\thumb_{0:yyyyMMdd}.log" -f (Get-Date)) -Value $line
}

$script:meta = @{ captured = $null; ok = $false; error = "no frame yet"; elapsed_s = 0; size = 0; audio = $null }

# 24 h of readings, loaded from disk so a task restart keeps the history.
$script:levels = New-Object System.Collections.Generic.List[object]
if (Test-Path $LevelsFile) {
    try {
        foreach ($row in @(Get-Content $LevelsFile -Raw | ConvertFrom-Json)) {
            if ($row -and $row.t) { $script:levels.Add(@{ t = $row.t; l = $row.l; lp = $row.lp; r = $row.r; rp = $row.rp }) }
        }
    } catch { Log "levels.json unreadable, starting empty: $($_.Exception.Message)" }
}

function Db($text) {
    # "-26.626406" -> -26.6; "-inf" or garbage -> $Floor
    $d = 0.0
    if ([double]::TryParse($text, [Globalization.NumberStyles]::Float, [Globalization.CultureInfo]::InvariantCulture, [ref]$d)) {
        return [math]::Max($Floor, [math]::Round($d, 1))
    }
    return $Floor
}

function Read-Audio($statsPath) {
    # ametadata prints one block per audio frame; with reset=0 the LAST block holds the
    # cumulative values for the whole window. Channel 1 = L, 2 = R.
    if (-not (Test-Path $statsPath)) { return @{ track = "none"; window_s = $AudioS; error = "no audio track measured" } }
    $last = @{}
    foreach ($line in Get-Content $statsPath) {
        if ($line -match '^lavfi\.astats\.(\d)\.(RMS_level|Peak_level)=(\S+)') { $last["$($Matches[1]).$($Matches[2])"] = $Matches[3] }
    }
    if ($last.Count -eq 0) { return @{ track = "none"; window_s = $AudioS; error = "astats wrote no levels" } }
    $L = @{ rms = (Db $last["1.RMS_level"]); peak = (Db $last["1.Peak_level"]) }
    $R = @{ rms = (Db $last["2.RMS_level"]); peak = (Db $last["2.Peak_level"]) }
    return @{
        track    = "aac"
        window_s = $AudioS
        L        = $L
        R        = $R
        silent   = (($L.rms -le $Silent) -and ($R.rms -le $Silent))
    }
}

function Save-Level($when, $audio) {
    if ($audio.track -ne "aac") { return }
    $script:levels.Add(@{ t = $when; l = $audio.L.rms; lp = $audio.L.peak; r = $audio.R.rms; rp = $audio.R.peak })
    $cut = (Get-Date).AddHours(-$KeepH)
    while ($script:levels.Count -gt 0 -and ([datetime]::Parse($script:levels[0].t) -lt $cut)) { $script:levels.RemoveAt(0) }
    # -InputObject keeps a one-element list an array (ConvertTo-Json @($x) unwraps it)
    (ConvertTo-Json -InputObject @($script:levels) -Compress) | Set-Content $LevelsFile -Encoding UTF8
}

function Grab {
    $tmp = "$Jpg.part"
    $errFile = "$LogDir\thumb_ffmpeg_err.txt"
    $statsPath = "$LogDir\$AStats"
    Remove-Item $statsPath -Force -ErrorAction SilentlyContinue
    $sw = [Diagnostics.Stopwatch]::StartNew()
    # Start-Process, never the call operator: PowerShell wraps a native command's stderr
    # (ffmpeg's harmless mid-GOP "no frame" warnings) into error records and the result
    # check fails (10/15 22:00, every grab "failed" while the tap was fine).
    # One input, two outputs: the first frame to the JPEG, 3 s of the AAC track through astats.
    # "0:a:0?" = optional map: a stream with no audio track still gives a picture.
    $p = Start-Process -FilePath $FFmpeg -WorkingDirectory $LogDir -ArgumentList @(
        "-hide_banner", "-loglevel", "error", "-nostdin", "-fflags", "+discardcorrupt", "-timeout", "8000000", "-i", $Src,
        "-map", "0:v:0", "-frames:v", "1", "-vf", "yadif=0:-1:0,scale=640:-1", "-q:v", "4",
        "-f", "image2", "-update", "1", "-y", $tmp,   # explicit muxer: the .part name has no image extension
        "-map", "0:a:0?", "-t", "$AudioS", "-vn",
        "-af", "astats=metadata=1:reset=0:measure_perchannel=RMS_level+Peak_level:measure_overall=none,ametadata=mode=print:file=$AStats",
        "-f", "null", "NUL"
    ) -NoNewWindow -PassThru -RedirectStandardError $errFile -Wait
    $ok = ($p.ExitCode -eq 0) -and (Test-Path $tmp) -and ((Get-Item $tmp).Length -gt 1000)
    if ($ok) {
        Move-Item $tmp $Jpg -Force
        $now = Get-Date
        Copy-Item $Jpg (Join-Path $Frames ("Phoenix_{0:yyyyMMdd_HHmmss}.jpg" -f $now)) -Force
        Get-ChildItem $Frames -Filter "Phoenix_*.jpg" | Where-Object { $_.LastWriteTime -lt (Get-Date).AddHours(-$KeepH) } | Remove-Item -Force
        $audio = Read-Audio $statsPath
        $captured = $now.ToString("o")   # with the UTC offset, so any browser computes the age
        try { Save-Level $captured $audio } catch { Log "levels save failed: $($_.Exception.Message)" }
        if ($audio.track -eq "aac" -and $audio.silent -and -not $script:meta.audio.silent) { Log ("audio SILENT: L {0} R {1} dBFS RMS" -f $audio.L.rms, $audio.R.rms) }
        if ($audio.track -eq "aac" -and -not $audio.silent -and $script:meta.audio -and $script:meta.audio.silent) { Log ("audio back: L {0} R {1} dBFS RMS" -f $audio.L.rms, $audio.R.rms) }
        $script:meta = @{
            captured  = $captured
            ok        = $true
            error     = ""
            elapsed_s = [math]::Round($sw.Elapsed.TotalSeconds, 1)
            size      = (Get-Item $Jpg).Length
            audio     = $audio
        }
    } else {
        Remove-Item $tmp -Force -ErrorAction SilentlyContinue
        $reason = (Get-Content $errFile -ErrorAction SilentlyContinue | Where-Object { $_ -match "rror" } | Select-Object -Last 1)
        if (-not $reason) { $reason = "ffmpeg exit $($p.ExitCode)" }
        $script:meta.ok = $false
        $script:meta.error = ("{0} (no new frame since {1})" -f $reason.Trim(), $script:meta.captured)
        $script:meta.elapsed_s = [math]::Round($sw.Elapsed.TotalSeconds, 1)
        Log "grab failed: $($script:meta.error)"
    }
    ($script:meta | ConvertTo-Json -Compress -Depth 4) | Set-Content $Meta -Encoding UTF8
}

function Serve($ctx) {
    $req = $ctx.Request; $res = $ctx.Response
    $res.Headers["Cache-Control"] = "no-store"
    $res.Headers["Access-Control-Allow-Origin"] = "*"
    try {
        if ($req.Url.AbsolutePath -eq "/live.jpg" -and (Test-Path $Jpg)) {
            $bytes = [IO.File]::ReadAllBytes($Jpg)
            $res.ContentType = "image/jpeg"
            if ($script:meta.captured) { $res.Headers["X-Captured"] = $script:meta.captured }
        } elseif ($req.Url.AbsolutePath -eq "/live.json") {
            $bytes = [Text.Encoding]::UTF8.GetBytes(($script:meta | ConvertTo-Json -Compress -Depth 4))
            $res.ContentType = "application/json"
        } elseif ($req.Url.AbsolutePath -eq "/levels.json") {
            $bytes = [Text.Encoding]::UTF8.GetBytes((ConvertTo-Json -InputObject @($script:levels) -Compress))
            $res.ContentType = "application/json"
        } elseif ($req.Url.AbsolutePath -eq "/frames/") {
            # the archive, oldest first: ["Phoenix_20261015_220801.jpg", ...]
            $names = @(Get-ChildItem $Frames -Filter "Phoenix_*.jpg" | Sort-Object Name | ForEach-Object { $_.Name })
            $bytes = [Text.Encoding]::UTF8.GetBytes((ConvertTo-Json -InputObject @($names) -Compress))
            $res.ContentType = "application/json"
        } elseif ($req.Url.AbsolutePath -match '^/frames/(Phoenix_\d{8}_\d{6}\.jpg)$' -and (Test-Path (Join-Path $Frames $Matches[1]))) {
            $bytes = [IO.File]::ReadAllBytes((Join-Path $Frames $Matches[1]))
            $res.ContentType = "image/jpeg"
        } else {
            $res.StatusCode = 404
            $bytes = [Text.Encoding]::UTF8.GetBytes("not found")
            $res.ContentType = "text/plain"
        }
        $res.ContentLength64 = $bytes.Length
        $res.OutputStream.Write($bytes, 0, $bytes.Length)
    } finally {
        $res.OutputStream.Close()
    }
}

Log "thumb loop starting (src $Src, every $Every s, audio $AudioS s of 0:a:0, http :$Port)"
$listener = New-Object System.Net.HttpListener
$listener.Prefixes.Add("http://+:$Port/")
$listener.Start()
$pending = $listener.GetContextAsync()
$next = Get-Date
while ($true) {
    try {
        if ((Get-Date) -ge $next) { Grab; $next = (Get-Date).AddSeconds($Every) }
        if ($pending.Wait(500)) {
            $ctx = $pending.Result
            try { Serve $ctx } catch { Log "serve error: $($_.Exception.Message)" }
            $pending = $listener.GetContextAsync()
        }
    } catch {
        Log "loop error: $($_.Exception.Message)"
        Start-Sleep -Seconds 2
    }
}
