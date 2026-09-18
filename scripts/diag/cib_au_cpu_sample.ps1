# Sample the hot EtereAu64 threads on a CIB for Etere support.
#
# Run in an ADMIN PowerShell on the CIB while the AUs are still spinning
# (before restarting them):
#
#   powershell -ExecutionPolicy Bypass -File cib_au_cpu_sample.ps1
#
# What it does (nothing is suspended or restarted, playout is untouched):
#   1. Records the thread id + CPU of the hottest thread in each EtereAu64.
#   2. Takes a 10 s kernel sampled-CPU trace with WPR (1 kHz per core, stacks on).
#   3. Decodes the trace with tracerpt and histograms the instruction pointers
#      and call stacks of the hot threads, mapped to module+offset.
#   4. Writes everything to C:\Windows\Temp\au-cpu-<host>-<stamp>\ and zips it.
#
# Takes 3 to 6 minutes, most of it tracerpt + XML decode.
# Send the zip to Etere together with tasks/emails/etere-au-cpu-spin-20260918.md.

# Decode-only rerun on a folder that already holds au-cpu.etl (and optionally au-cpu.xml):
#   powershell -ExecutionPolicy Bypass -File cib_au_cpu_sample.ps1 -Reuse C:\Windows\Temp\au-cpu-<host>-<stamp>
param([string]$Reuse = '')

$ErrorActionPreference = 'Stop'
$KERNEL_MIN = [uint64]'9223372036854775808'   # 0x8000000000000000; a hex literal that size is a NEGATIVE int64 in PowerShell
if ($Reuse) { $dir = $Reuse } else { $dir = "C:\Windows\Temp\au-cpu-$env:COMPUTERNAME-$(Get-Date -Format 'yyyyMMdd-HHmm')" }
New-Item -ItemType Directory -Force -Path $dir | Out-Null
$report = Join-Path $dir 'report.txt'
Remove-Item (Join-Path $dir 'report.txt'), (Join-Path $dir 'au-modules.tsv') -ErrorAction SilentlyContinue
function Say($s) { Write-Host $s; Add-Content -Path $report -Value $s }

Say "HOST $env:COMPUTERNAME  $(Get-Date)  TZ $((Get-TimeZone).Id)"
Say "OS $((Get-CimInstance Win32_OperatingSystem).Caption) $((Get-CimInstance Win32_OperatingSystem).Version)  cores $((Get-CimInstance Win32_Processor | Select-Object -First 1).NumberOfLogicalProcessors)"
$exe = Get-Item 'C:\Program Files (x86)\Etere\EtereAu64.exe'
Say "EtereAu64.exe $($exe.VersionInfo.FileVersion)  size $($exe.Length)  mtime $($exe.LastWriteTime)"

# --- 1. hot threads + module maps (needed to symbolize sampled addresses) ---
$aus = @(Get-Process EtereAu64)
if (-not $aus) { throw 'no EtereAu64 running' }
$hot  = @{}   # tid -> process id
$mods = @{}   # process id -> module list
foreach ($p in $aus) {
    $is32 = [bool]($p.Modules | Where-Object { $_.ModuleName -match '^wow64' })
    $cmd = (Get-CimInstance Win32_Process -Filter "ProcessId=$($p.Id)").CommandLine
    Say "AU pid $($p.Id)  started $($p.StartTime)  cpu $([math]::Round($p.TotalProcessorTime.TotalMinutes)) min  threads $($p.Threads.Count)  $(if($is32){'32-bit (WOW64)'}else{'64-bit'})"
    Say "    cmd: $cmd"
    $top = @($p.Threads | Sort-Object { $_.TotalProcessorTime } -Descending | Select-Object -First 3)
    foreach ($th in $top) { Say "    tid $($th.Id)  cpu $([math]::Round($th.TotalProcessorTime.TotalMinutes)) min  pri $($th.PriorityLevel)  state $($th.ThreadState)/$($th.WaitReason)" }
    $hot[[int]$top[0].Id] = [int]$p.Id
    $mods[[int]$p.Id] = @($p.Modules | ForEach-Object {
        [pscustomobject]@{ name = $_.ModuleName; base = [uint64][int64]$_.BaseAddress; size = [uint64]$_.ModuleMemorySize; ver = $_.FileVersionInfo.FileVersion; path = $_.FileName } })
    $mods[[int]$p.Id] | ForEach-Object { "$($p.Id)`t$($_.name)`t0x$($_.base.ToString('x'))`t$($_.size)`t$($_.ver)`t$($_.path)" } | Add-Content (Join-Path $dir 'au-modules.tsv')
}
function ToAddr([string]$s) {
    $s = $s.Trim(); if ($s.StartsWith('0x')) { $s = $s.Substring(2) }
    try { return [System.Convert]::ToUInt64($s, 16) } catch { try { return [uint64]$s } catch { return [uint64]0 } }
}
function ModOf([int]$apid, [uint64]$addr) {
    if ($addr -ge $KERNEL_MIN) { return 'kernel' }
    foreach ($m in $mods[$apid]) { if ($addr -ge $m.base -and $addr -lt ($m.base + $m.size)) { return "$($m.name)+0x$(($addr - $m.base).ToString('x'))" } }
    return "0x$($addr.ToString('x'))"
}

# --- 2. 10 s sampled CPU trace ---
$wprp = @'
<?xml version="1.0" encoding="utf-8"?>
<WindowsPerformanceRecorder Version="1.0" Author="ctv-diag">
  <Profiles>
    <SystemCollector Id="SC" Name="NT Kernel Logger"><BufferSize Value="1024"/><Buffers Value="64"/></SystemCollector>
    <SystemProvider Id="SP">
      <Keywords><Keyword Value="ProcessThread"/><Keyword Value="Loader"/><Keyword Value="SampledProfile"/></Keywords>
      <Stacks><Stack Value="SampledProfile"/></Stacks>
    </SystemProvider>
    <Profile Id="CPUSample.Verbose.File" Name="CPUSample" Description="10s CPU sampling" LoggingMode="File" DetailLevel="Verbose">
      <Collectors><SystemCollectorId Value="SC"><SystemProviderId Value="SP"/></SystemCollectorId></Collectors>
    </Profile>
  </Profiles>
</WindowsPerformanceRecorder>
'@
$wprpPath = Join-Path $dir 'cpu.wprp'
[IO.File]::WriteAllText($wprpPath, $wprp)
$etl = Join-Path $dir 'au-cpu.etl'
if (Test-Path $etl) {
    Say "reusing trace: $etl  $([math]::Round((Get-Item $etl).Length/1MB,1)) MB"
} else {
    Say "--- WPR start $(Get-Date -Format 'HH:mm:ss') ---"
    try {
        & wpr -start "$wprpPath!CPUSample" -filemode
        Start-Sleep -Seconds 10
        & wpr -stop $etl
    } catch {
        & wpr -cancel 2>$null
        throw
    }
    Say "trace: $etl  $([math]::Round((Get-Item $etl).Length/1MB,1)) MB"
}

# --- 3. decode: tracerpt -> XML, stream it, histogram the hot threads ---
$xml = Join-Path $dir 'au-cpu.xml'
if (-not (Test-Path $xml)) {
    Say "--- tracerpt $(Get-Date -Format 'HH:mm:ss') (a few minutes) ---"
    & tracerpt $etl -o $xml -of XML -y | Out-Null
}
Say "xml: $([math]::Round((Get-Item $xml).Length/1MB,1)) MB  decode start $(Get-Date -Format 'HH:mm:ss')"

$ipHist = @{}      # "pid|module+off" -> count
$stackHist = @{}   # "pid|frame ; frame ; ..." -> count
$samples = @{}     # tid -> sample count
$settings = New-Object System.Xml.XmlReaderSettings
$settings.IgnoreWhitespace = $true
$reader = [System.Xml.XmlReader]::Create($xml, $settings)
$n = 0; $used = 0
$ok = $reader.Read()
while ($ok) {
    if ($reader.NodeType -eq 'Element' -and $reader.LocalName -eq 'Event') {
        $raw = $reader.ReadOuterXml()      # positions the reader on the next node; do not Read() again
        $n++
        if ($raw.IndexOf('InstructionPointer') -lt 0 -and $raw.IndexOf('StackThread') -lt 0) { continue }
        $doc = New-Object System.Xml.XmlDocument
        $doc.LoadXml($raw)
        $data = @{}
        foreach ($d in $doc.GetElementsByTagName('Data')) { $data[$d.GetAttribute('Name')] = $d.InnerText }
        if ($data.ContainsKey('InstructionPointer') -and $data.ContainsKey('ThreadId')) {      # PerfInfo SampledProfile
            $tid = [int]$data['ThreadId']
            if ($hot.ContainsKey($tid)) {
                $apid = $hot[$tid]; $used++
                $samples[$tid] = 1 + [int]$samples[$tid]
                $k = "$apid|" + (ModOf $apid (ToAddr $data['InstructionPointer']))
                $ipHist[$k] = 1 + [int]$ipHist[$k]
            }
        } elseif ($data.ContainsKey('StackThread')) {                                            # StackWalk
            $tid = [int]$data['StackThread']
            if ($hot.ContainsKey($tid)) {
                $apid = $hot[$tid]
                $frames = @()
                for ($i = 1; $i -le 96; $i++) { if (-not $data.ContainsKey("Stack$i")) { break }; $frames += (ModOf $apid (ToAddr $data["Stack$i"])) }
                $k = "$apid|" + ($frames -join ' ; ')
                $stackHist[$k] = 1 + [int]$stackHist[$k]
            }
        }
        continue
    }
    $ok = $reader.Read()
}
$reader.Close()
Say "events: $n  samples on hot threads: $used  decode end $(Get-Date -Format 'HH:mm:ss')"
Say ''
Say '=== samples per hot thread (10 s at 1 kHz: ~10,000 = one full core) ==='
foreach ($tid in $samples.Keys) { Say "  pid $($hot[$tid]) tid $tid : $($samples[$tid]) samples" }
Say ''
Say '=== top instruction pointers (where the spinning thread actually executes) ==='
$ipHist.GetEnumerator() | Sort-Object Value -Descending | Select-Object -First 25 | ForEach-Object { Say ("  {0,6}  {1}" -f $_.Value, $_.Key) }
Say ''
Say '=== top call stacks (leaf first) ==='
$stackHist.GetEnumerator() | Sort-Object Value -Descending | Select-Object -First 8 | ForEach-Object { Say ("  {0,6}  {1}" -f $_.Value, $_.Key) }
Say ''
Say '=== module summary for hot threads ==='
$byMod = @{}
foreach ($kv in $ipHist.GetEnumerator()) { $m = ($kv.Key -split '\|')[1] -replace '\+0x.*$', ''; $byMod[$m] = $kv.Value + [int]$byMod[$m] }
$byMod.GetEnumerator() | Sort-Object Value -Descending | Select-Object -First 12 | ForEach-Object { Say ("  {0,6}  {1}" -f $_.Value, $_.Key) }

Remove-Item $xml -ErrorAction SilentlyContinue   # the ETL is what Etere needs; the XML is huge
$zip = "$dir.zip"
Compress-Archive -Path "$dir\*" -DestinationPath $zip -Force
Say ''
Say "DONE. Send to Etere: $zip"
Write-Host "`nReport: $report`nZip:    $zip"
