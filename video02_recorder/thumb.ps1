# Phoenix TV confidence monitor - one JPEG a minute from the recorder's loopback tap
# (udp 6012, see record.ps1 $ThumbTarget), served over HTTP on :8081 so the Control Room
# Monitor Wall can show "what Phoenix is sending right now" (Maija 10/16: a picture that
# refreshes every minute is enough; it must never touch the recording). It reads its own
# UDP port and writes two files; the recorder is a separate process on a separate port.
#
# Scheduled Task "PhoenixSrtThumb" (SYSTEM, at startup, restart on failure).
# GET http://100.90.230.98:8081/live.jpg   the latest frame (640 px wide, deinterlaced)
# GET http://100.90.230.98:8081/live.json  {captured, ok, error, elapsed_s, size}
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

New-Item -ItemType Directory -Force -Path $LogDir, $Frames | Out-Null

function Log($msg) {
    $line = "{0:yyyy-MM-dd HH:mm:ss} {1}" -f (Get-Date), $msg
    Add-Content -Path ("$LogDir\thumb_{0:yyyyMMdd}.log" -f (Get-Date)) -Value $line
}

$script:meta = @{ captured = $null; ok = $false; error = "no frame yet"; elapsed_s = 0; size = 0 }

function Grab {
    $tmp = "$Jpg.part"
    $errFile = "$LogDir\thumb_ffmpeg_err.txt"
    $sw = [Diagnostics.Stopwatch]::StartNew()
    # Start-Process, never the call operator: PowerShell wraps a native command's stderr
    # (ffmpeg's harmless mid-GOP "no frame" warnings) into error records and the result
    # check fails (10/15 22:00, every grab "failed" while the tap was fine).
    $p = Start-Process -FilePath $FFmpeg -ArgumentList @(
        "-hide_banner", "-loglevel", "error", "-nostdin", "-timeout", "8000000", "-i", $Src,
        "-frames:v", "1", "-vf", "yadif=0:-1:0,scale=640:-1", "-q:v", "4",
        "-f", "image2", "-update", "1", "-y", $tmp   # explicit muxer: the .part name has no image extension
    ) -NoNewWindow -PassThru -RedirectStandardError $errFile -Wait
    $ok = ($p.ExitCode -eq 0) -and (Test-Path $tmp) -and ((Get-Item $tmp).Length -gt 1000)
    if ($ok) {
        Move-Item $tmp $Jpg -Force
        Copy-Item $Jpg (Join-Path $Frames ("Phoenix_{0:yyyyMMdd_HHmmss}.jpg" -f (Get-Date))) -Force
        Get-ChildItem $Frames -Filter "Phoenix_*.jpg" | Where-Object { $_.LastWriteTime -lt (Get-Date).AddHours(-$KeepH) } | Remove-Item -Force
        $script:meta = @{
            captured  = (Get-Date).ToString("o")   # with the UTC offset, so any browser computes the age
            ok        = $true
            error     = ""
            elapsed_s = [math]::Round($sw.Elapsed.TotalSeconds, 1)
            size      = (Get-Item $Jpg).Length
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
    ($script:meta | ConvertTo-Json -Compress) | Set-Content $Meta -Encoding UTF8
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
            $bytes = [Text.Encoding]::UTF8.GetBytes(($script:meta | ConvertTo-Json -Compress))
            $res.ContentType = "application/json"
        } elseif ($req.Url.AbsolutePath -eq "/frames/") {
            # the archive, oldest first: ["Phoenix_20261015_220801.jpg", ...]
            $names = @(Get-ChildItem $Frames -Filter "Phoenix_*.jpg" | Sort-Object Name | ForEach-Object { $_.Name })
            $bytes = [Text.Encoding]::UTF8.GetBytes((ConvertTo-Json @($names) -Compress))
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

Log "thumb loop starting (src $Src, every $Every s, http :$Port)"
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
