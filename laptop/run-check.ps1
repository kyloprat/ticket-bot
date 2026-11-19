# One BookMyShow check from this laptop (home network, which BookMyShow doesn't block).
# Task Scheduler runs it every 10 minutes while you are logged on (see install-task.ps1).
# It works in its own copy of the repo, so it never touches your project folder:
#   %LOCALAPPDATA%\ticket-bot\repo          the copy (reset to GitHub's main each run)
#   %LOCALAPPDATA%\ticket-bot\telegram.env  TELEGRAM_BOT_TOKEN=... and TELEGRAM_CHAT_ID=... (you create it)
#   %LOCALAPPDATA%\ticket-bot\check.log     what each run did
$ErrorActionPreference = 'Continue'
$base = Join-Path $env:LOCALAPPDATA 'ticket-bot'
$repo = Join-Path $base 'repo'
$log = Join-Path $base 'check.log'
$envFile = Join-Path $base 'telegram.env'
New-Item -ItemType Directory -Force $base | Out-Null
if ((Test-Path $log) -and (Get-Item $log).Length -gt 1MB) { Move-Item -Force $log "$log.old" }

function Write-Log([string]$text) { "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') $text" | Add-Content -Encoding utf8 $log }
function Invoke-Logged([string]$exe, [string[]]$params) {
    $out = & $exe @params 2>&1 | Out-String
    if ($out.Trim()) { $out.TrimEnd() | Add-Content -Encoding utf8 $log }
    return $LASTEXITCODE
}

if (-not (Test-Path $envFile)) { Write-Log "telegram.env is missing: $envFile"; exit 1 }
$uv = (Get-Command uv -ErrorAction SilentlyContinue).Source
if (-not $uv) { $uv = Join-Path $env:USERPROFILE '.local\bin\uv.exe' }

if (-not (Test-Path (Join-Path $repo '.git'))) {
    if ((Invoke-Logged git @('clone', '-q', 'https://github.com/kyloprat/ticket-bot.git', $repo)) -ne 0) { Write-Log 'clone failed'; exit 1 }
    Invoke-Logged git @('-C', $repo, 'config', 'user.name', 'ticket-bot laptop') | Out-Null
    Invoke-Logged git @('-C', $repo, 'config', 'user.email', 'pratyush.txt@gmail.com') | Out-Null
}
try { Set-Location $repo -ErrorAction Stop } catch { Write-Log "cannot open $repo"; exit 1 }

# start from what GitHub has now (trackers from chat, state from the last check)
if ((Invoke-Logged git @('fetch', '-q', 'origin', 'main')) -ne 0) { Write-Log 'offline? fetch failed'; exit 1 }
Invoke-Logged git @('reset', '-q', '--hard', 'origin/main') | Out-Null

foreach ($line in Get-Content $envFile) {
    if ($line -match '^\s*(TELEGRAM_BOT_TOKEN|TELEGRAM_CHAT_ID)\s*=\s*(.+?)\s*$') { Set-Item "env:$($Matches[1])" $Matches[2] }
}
$env:CHECKER = 'laptop'
$env:PYTHONIOENCODING = 'utf-8'
Invoke-Logged $uv @('sync', '--frozen', '-q') | Out-Null
Invoke-Logged $uv @('run', 'main.py') | Out-Null

Invoke-Logged git @('add', 'bms_state.json', 'meta.json') | Out-Null
& git diff --cached --quiet
if ($LASTEXITCODE -eq 0) { exit 0 }
Invoke-Logged git @('commit', '-q', '-m', 'chore: update tracker state (laptop) [skip ci]') | Out-Null
# GitHub's run or the chat relay may have pushed meanwhile: then put this run's two state files, whole,
# on top of the newest main (no merge of JSON lines; the chat's newest trackers stay as they are).
$keep = Join-Path $base 'pending'
New-Item -ItemType Directory -Force $keep | Out-Null
foreach ($i in 1..5) {
    if ((Invoke-Logged git @('push', '-q')) -eq 0) { exit 0 }
    Copy-Item -Force bms_state.json, meta.json $keep
    if ((Invoke-Logged git @('fetch', '-q', 'origin', 'main')) -ne 0) { break }
    Invoke-Logged git @('reset', '-q', '--hard', 'origin/main') | Out-Null
    Copy-Item -Force (Join-Path $keep 'bms_state.json'), (Join-Path $keep 'meta.json') .
    Invoke-Logged git @('add', 'bms_state.json', 'meta.json') | Out-Null
    & git diff --cached --quiet
    if ($LASTEXITCODE -eq 0) { exit 0 }
    Invoke-Logged git @('commit', '-q', '-m', 'chore: update tracker state (laptop) [skip ci]') | Out-Null
    Start-Sleep -Seconds ($i * 3)
}
Write-Log 'push failed 5 times; the next run checks again'
exit 1
