# start-app.ps1 - bring the whole app up with one command, and open it.
#
# Why this exists: starting this app by hand is two terminals, two commands and
# two ports, and getting any of it slightly wrong looks exactly like the app
# being broken. The specific failure it was written for was a browser sitting on
# http://localhost:5174 - a port Vite had drifted onto once, earlier - answering
# ERR_CONNECTION_REFUSED while a perfectly healthy dev server was listening on
# 5173. (`strictPort` in client/vite.config.js now stops the drift; this stops
# the guessing.)
#
#     .\start-app.ps1              # the app
#     .\start-app.ps1 -Admin       # the app, straight into the admin panel
#     .\start-app.ps1 -NoBrowser   # start the servers, open nothing
#     .\start-app.ps1 -Restart     # kill whatever holds the ports, start fresh
#
# Or just double-click start-app.bat, which calls this.
#
# !! THIS FILE IS DELIBERATELY PURE ASCII. Windows PowerShell 5.1 reads a .ps1
# with no byte-order mark as cp1252, so a UTF-8 em-dash (E2 80 94) arrives as
# three characters - and the middle one, 0x94, is a SMART CLOSING QUOTE, which
# PowerShell honours as a real string delimiter. One em-dash inside one
# Write-Host string ended that string early and threw four cascading parse
# errors thirty lines further down. Plain hyphens cannot do that. Same reason
# there are no arrows or warning glyphs in here.
#
# !! IT IS IDEMPOTENT ON PURPOSE. Running it when the app is already up must not
# start a second copy - a second uvicorn cannot have the port anyway, and a
# second Vite is exactly how the 5174 drift happened in the first place. So each
# half is started ONLY if its port is free, and an already-running app just gets
# the browser opened at the right address.
#
# !! IT NEVER KILLS ANYTHING UNLESS ASKED. `-Restart` is opt-in because the
# thing holding port 8000 might be something else entirely, and `Stop-Process`
# on a guess is not a recovery.

param(
    [switch]$Admin,
    [switch]$NoBrowser,
    [switch]$Restart
)

$ErrorActionPreference = "Stop"

$Root = $PSScriptRoot
$Client = Join-Path $Root "client"
$ApiPort = 8000
$WebPort = 5173
$WebUrl = "http://localhost:$WebPort"
$ApiUrl = "http://127.0.0.1:$ApiPort"

function Get-PortOwner($port) {
    $conn = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
    if (-not $conn) { return $null }
    return Get-Process -Id $conn[0].OwningProcess -ErrorAction SilentlyContinue
}

function Test-Port($port) {
    if (Get-PortOwner $port) { return $true }
    return $false
}

# NOT `pkill -f uvicorn` - see the Windows gotchas in AGENTS.md. A Windows
# python process is not matched by name, so the stale server keeps the port and
# you end up testing old code. Go through the port to its owning PID instead.
function Stop-Port($port, $label) {
    $proc = Get-PortOwner $port
    if (-not $proc) { return }
    Write-Host ("  stopping " + $label + " on " + $port + " (pid " + $proc.Id + ", " + $proc.ProcessName + ")") -ForegroundColor DarkYellow
    try { Stop-Process -Id $proc.Id -Force -Confirm:$false } catch { Write-Host "  could not stop it" -ForegroundColor Red }
    Start-Sleep -Milliseconds 800
}

# Each half gets its own window, kept open, so a crash leaves its traceback on
# screen instead of vanishing with the process.
function Start-InWindow($title, $workDir, $command) {
    $inner = "`$Host.UI.RawUI.WindowTitle = '" + $title + "'; " + $command
    Start-Process -FilePath "powershell.exe" -WorkingDirectory $workDir -ArgumentList @(
        "-NoExit", "-NoProfile", "-Command", $inner
    ) | Out-Null
}

function Wait-For($url, $label, $seconds) {
    $deadline = (Get-Date).AddSeconds($seconds)
    while ((Get-Date) -lt $deadline) {
        try {
            Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec 3 | Out-Null
            Write-Host ("  " + $label + " is up") -ForegroundColor Green
            return $true
        } catch {
            Start-Sleep -Milliseconds 700
        }
    }
    Write-Host ("  " + $label + " did not answer in time - check its window") -ForegroundColor Red
    return $false
}

Write-Host ""
Write-Host "Character Studio - starting" -ForegroundColor Cyan

if ($Restart) {
    Write-Host ""
    Write-Host "restart requested" -ForegroundColor Cyan
    Stop-Port $WebPort "the frontend"
    Stop-Port $ApiPort "the backend"
}

# ---- backend ---------------------------------------------------------------
Write-Host ""
Write-Host ("backend  - API, port " + $ApiPort) -ForegroundColor Cyan
if (Test-Port $ApiPort) {
    Write-Host "  already running, left alone" -ForegroundColor DarkGray
} else {
    Start-InWindow ("API " + $ApiPort) $Root "python -m uvicorn server.main:app --reload"
    Wait-For ($ApiUrl + "/openapi.json") "the API" 60 | Out-Null
}

# ---- frontend --------------------------------------------------------------
Write-Host ""
Write-Host ("frontend - app, port " + $WebPort) -ForegroundColor Cyan
if (Test-Port $WebPort) {
    Write-Host "  already running, left alone" -ForegroundColor DarkGray
} else {
    if (-not (Test-Path (Join-Path $Client "node_modules"))) {
        Write-Host "  node_modules is missing, running npm install first (one time)" -ForegroundColor DarkYellow
        Push-Location $Client
        try { npm install } finally { Pop-Location }
    }
    Start-InWindow ("app " + $WebPort) $Client "npm run dev"
    Wait-For $WebUrl "the app" 90 | Out-Null
}

# ---- the address, said out loud --------------------------------------------
$target = $WebUrl
if ($Admin) { $target = $WebUrl + "/?admin=1" }

Write-Host ""
Write-Host ("  the app        " + $WebUrl) -ForegroundColor White
Write-Host ("  admin panel    " + $WebUrl + "/?admin=1") -ForegroundColor White
Write-Host ("  API docs       " + $ApiUrl + "/docs") -ForegroundColor DarkGray
Write-Host ""
Write-Host ("  Port " + $WebPort + " only, never 5174 - vite.config.js pins it now.") -ForegroundColor DarkGray
Write-Host "  Stop everything: close the two windows, or run with -Restart" -ForegroundColor DarkGray
Write-Host ""

if (-not $NoBrowser) {
    Start-Process $target
}
