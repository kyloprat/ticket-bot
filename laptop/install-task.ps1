# Adds (or replaces) the "ticket-bot check" task: run-check.ps1 every 10 minutes at :05, :15, ...
# while you are logged on, with no window. GitHub checks at the other times and whenever the laptop is off.
# Remove it:  Unregister-ScheduledTask -TaskName 'ticket-bot check' -Confirm:$false
# Pause it:   Disable-ScheduledTask -TaskName 'ticket-bot check'
$ErrorActionPreference = 'Stop'
$script = Join-Path $PSScriptRoot 'run-check.ps1'
$base = Join-Path $env:LOCALAPPDATA 'ticket-bot'
New-Item -ItemType Directory -Force $base | Out-Null
Copy-Item -Force $script (Join-Path $base 'run-check.ps1')     # runs from its own folder, not the project

$ps = "powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"$(Join-Path $base 'run-check.ps1')`""
$action = New-ScheduledTaskAction -Execute 'conhost.exe' -Argument "--headless $ps"
$start = (Get-Date).Date.AddMinutes(5)
$trigger = New-ScheduledTaskTrigger -Once -At $start -RepetitionInterval (New-TimeSpan -Minutes 10)
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 5) -MultipleInstances IgnoreNew -StartWhenAvailable
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive
Register-ScheduledTask -TaskName 'ticket-bot check' -Action $action -Trigger $trigger -Settings $settings `
    -Principal $principal -Description 'BookMyShow check from the home network (kyloprat/ticket-bot)' -Force | Out-Null

$envFile = Join-Path $base 'telegram.env'
if (-not (Test-Path $envFile)) {
    Write-Output "Task added. One step left: create $envFile with two lines:"
    Write-Output '  TELEGRAM_BOT_TOKEN=<the bot token>'
    Write-Output '  TELEGRAM_CHAT_ID=<your chat id>'
} else {
    Write-Output ('Task added. Log: ' + (Join-Path $base 'check.log'))
}
