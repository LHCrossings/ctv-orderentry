# Registers the "CTV OrderEntry Server" scheduled task on the Jumpbox (run once, as SYSTEM via SSM
# or as an admin). The task runs web_main.py in usrjp's INTERACTIVE session so the server keeps
# the K: drive mapping (program grid, traffic logs) exactly like the hand-started console did.
# Interactive logon type needs no stored password; the task runs whenever usrjp is logged on
# (an RDP session that is merely disconnected still counts). No execution time limit — the
# default 3-day limit would kill a long-running server.
$repo = 'C:\Users\usrjp\windev\ctv-orderentry'
$name = 'CTV OrderEntry Server'
$action = New-ScheduledTaskAction -Execute "$repo\.venv\Scripts\python.exe" -Argument 'web_main.py' -WorkingDirectory $repo
$trigger = New-ScheduledTaskTrigger -AtLogOn -User 'CTVETERE\usrjp'
$principal = New-ScheduledTaskPrincipal -UserId 'CTVETERE\usrjp' -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
Register-ScheduledTask -TaskName $name -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Force | Out-Null
Get-ScheduledTask -TaskName $name | Select-Object TaskName, State | Format-Table -AutoSize | Out-String -Width 80
(Get-ScheduledTask -TaskName $name).Principal | Select-Object UserId, LogonType | Format-Table -AutoSize | Out-String -Width 80
