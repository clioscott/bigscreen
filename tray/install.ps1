# BigScreen Tray installer. Double-click "Install BigScreen Tray.cmd" (or run this script).
# Run it again any time to update: it replaces the installed copy and keeps your settings and hotkeys.
#
# Installs into %LOCALAPPDATA%\BigScreenTray (venv + a copy of the code), finds the hub, optionally
# starts with Windows (HKCU Run key), launches the tray icon and pins it next to the clock.
# Nothing here needs admin rights.

param([string]$Hub = "")

$ErrorActionPreference = "Continue"     # native tools write notices to stderr; exit codes are checked instead
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$env:PYTHONIOENCODING = "utf-8"

$Src  = $PSScriptRoot
$Dest = Join-Path $env:LOCALAPPDATA "BigScreenTray"
$Venv = Join-Path $Dest "venv"
$VPy  = Join-Path $Venv "Scripts\python.exe"
$VPyw = Join-Path $Venv "Scripts\pythonw.exe"

function Step($n, $text) { Write-Host ""; Write-Host "[$n/6] $text" -ForegroundColor Cyan }
function Say($text, $color = "Gray") { Write-Host "      $text" -ForegroundColor $color }
function Ask($question, $default = $true) {
    $hint = if ($default) { "[Y/n]" } else { "[y/N]" }
    $a = Read-Host "      $question $hint"
    if ([string]::IsNullOrWhiteSpace($a)) { return $default }
    return $a.Trim().ToLower().StartsWith("y")
}
function Finish($code) { Write-Host ""; Read-Host "Press Enter to close" | Out-Null; exit $code }
function Fail($text) { Say $text Red; Finish 1 }

function Find-Python {
    # every Python we can find; keep those >= 3.9 that have tkinter; prefer the newest up to 3.13
    $cands = New-Object System.Collections.Generic.List[string]
    if (Get-Command py -ErrorAction SilentlyContinue) {
        & py -0p 2>$null | ForEach-Object { if ($_ -match '([A-Za-z]:\\\S*python\.exe)') { $cands.Add($Matches[1]) } }
    }
    foreach ($dir in @("$env:USERPROFILE\.pyenv\pyenv-win\versions\*", "$env:LOCALAPPDATA\Programs\Python\Python3*",
                       "$env:ProgramFiles\Python3*")) {
        Get-ChildItem $dir -Directory -ErrorAction SilentlyContinue | ForEach-Object { $cands.Add((Join-Path $_.FullName "python.exe")) }
    }
    Get-Command python -All -ErrorAction SilentlyContinue | Where-Object { $_.Source -notlike "*WindowsApps*" } |
        ForEach-Object { $cands.Add($_.Source) }

    $found = @()
    foreach ($c in ($cands | Select-Object -Unique)) {
        if (-not (Test-Path $c)) { continue }
        $out = & $c -c "import sys, tkinter, venv; print('%d.%d|%s' % (sys.version_info[0], sys.version_info[1], sys.executable))" 2>$null
        if ($LASTEXITCODE -ne 0 -or -not $out) { continue }
        $ver, $exe = ([string]($out | Select-Object -Last 1)).Split("|", 2)
        $v = [version]$ver
        if ($v -ge [version]"3.9") { $found += [pscustomobject]@{ Version = $v; Exe = $exe } }
    }
    $pick = $found | Where-Object { $_.Version -le [version]"3.13" } | Sort-Object Version -Descending | Select-Object -First 1
    if (-not $pick) { $pick = $found | Sort-Object Version -Descending | Select-Object -First 1 }
    return $pick
}

Write-Host ""
Write-Host "  BigScreen Tray setup" -ForegroundColor White
Write-Host "  Puts a BigScreen icon in your system tray to control the wall, with optional hotkeys." -ForegroundColor DarkGray

# 1 ---------------------------------------------------------------------------------------------
Step 1 "Looking for Python"
$python = Find-Python
if (-not $python) {
    Say "No Python 3.9+ with tkinter found." Yellow
    if ((Get-Command winget -ErrorAction SilentlyContinue) -and (Ask "Install Python 3.12 now (winget, just for you)?")) {
        winget install --exact --id Python.Python.3.12 --scope user
        $python = Find-Python
    }
    if (-not $python) { Fail "Install Python from https://www.python.org/downloads/ (tick 'tcl/tk'), then run this again." }
}
Say "Python $($python.Version)  ($($python.Exe))" Green

# 2 ---------------------------------------------------------------------------------------------
Step 2 "Installing to $Dest"
$running = Get-CimInstance Win32_Process -Filter "Name like 'python%'" | Where-Object { $_.CommandLine -match 'bigscreen_tray' }
if ($running) {
    Say "Stopping the copy that's running..."
    $running | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    Start-Sleep -Seconds 1
}
New-Item -ItemType Directory -Force $Dest | Out-Null
& $python.Exe -m venv --clear $Venv
if ($LASTEXITCODE -ne 0) { Fail "Couldn't create the virtual environment in $Venv." }

# install from a copy, so the repo doesn't collect build/ and egg-info folders
$Stage = Join-Path $Dest "src"
Remove-Item -Recurse -Force $Stage -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force (Join-Path $Stage "bigscreen_tray") | Out-Null
Copy-Item (Join-Path $Src "pyproject.toml") $Stage
Copy-Item (Join-Path $Src "bigscreen_tray\*.py") (Join-Path $Stage "bigscreen_tray")
Say "Downloading pystray and Pillow..."
& $VPy -m pip install --disable-pip-version-check --quiet $Stage
if ($LASTEXITCODE -ne 0) { Fail "pip install failed (see above). Check your internet connection and run this again." }
Say "Installed." Green

# 3 ---------------------------------------------------------------------------------------------
Step 3 "Finding your BigScreen"
$hubUrl = $null
$hubArgs = @()
if ($Hub) { $hubArgs = @($Hub) }
while (-not $hubUrl) {
    $line = & $VPy -m bigscreen_tray.install find-hub @hubArgs 2>$null
    if ($LASTEXITCODE -eq 0 -and $line) {
        $hubUrl, $what = ([string]($line | Select-Object -Last 1)).Split("`t", 2)
        Say "Connected to $hubUrl" Green
        Say "($what)" DarkGray
        break
    }
    Say "Couldn't reach the hub." Yellow
    $answer = Read-Host "      Hub address (like 192.168.1.111), or Enter to skip for now"
    if ([string]::IsNullOrWhiteSpace($answer)) {
        $hubUrl = "http://192.168.1.111:8800"
        Say "Using $hubUrl; change it later under Hotkeys & settings." Yellow
        break
    }
    $hubArgs = @($answer)
}

# 4 ---------------------------------------------------------------------------------------------
Step 4 "Start with Windows"
$autostart = Ask "Start BigScreen Tray automatically when you sign in?"
$flag = if ($autostart) { "--autostart" } else { "--no-autostart" }
& $VPy -m bigscreen_tray.install configure --hub $hubUrl $flag
if ($LASTEXITCODE -ne 0) { Fail "Couldn't save the settings." }
if ($autostart) { Say "It will start when you sign in." Green } else { Say "Not starting automatically (turn it on later in settings)." }

# 5 ---------------------------------------------------------------------------------------------
Step 5 "Starting it"
$hotkeys = Ask "Open the hotkey settings now, to set up some hotkeys?"
$launch = @("-I", "-m", "bigscreen_tray")
if ($hotkeys) { $launch += "--settings" }
Start-Process -FilePath $VPyw -ArgumentList $launch -WorkingDirectory $Dest
Say "Running." Green

# 6 ---------------------------------------------------------------------------------------------
Step 6 "Putting the icon on the taskbar"
& $VPy -m bigscreen_tray.install show-icon
if ($LASTEXITCODE -eq 0) {
    Say "Done: it's next to the clock." Green
} else {
    Say "Windows may have tucked it under the ^ arrow next to the clock; drag it onto the taskbar to keep it there." Yellow
}

Write-Host ""
Write-Host "  All set." -ForegroundColor Green
Write-Host "  Left- or right-click the BigScreen icon for the menu. Logs: $Dest\tray.log" -ForegroundColor DarkGray
Write-Host "  To update, run this installer again. To remove it, run 'Uninstall BigScreen Tray.cmd'." -ForegroundColor DarkGray
Finish 0
