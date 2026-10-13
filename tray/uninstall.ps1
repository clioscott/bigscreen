# Removes BigScreen Tray: stops it, takes it out of startup, deletes %LOCALAPPDATA%\BigScreenTray
# (asks before deleting your settings and hotkeys).

$ErrorActionPreference = "Continue"
$Dest = Join-Path $env:LOCALAPPDATA "BigScreenTray"

Write-Host ""
Write-Host "  Removing BigScreen Tray" -ForegroundColor White

Get-CimInstance Win32_Process -Filter "Name like 'python%'" | Where-Object { $_.CommandLine -match 'bigscreen_tray' } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Remove-ItemProperty -Path "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run" -Name "BigScreenTray" -ErrorAction SilentlyContinue
Write-Host "      Stopped, and removed from startup." -ForegroundColor Green
Start-Sleep -Seconds 1

if (Test-Path $Dest) {
    $keep = Read-Host "      Keep your settings and hotkeys, for a later reinstall? [Y/n]"
    if ([string]::IsNullOrWhiteSpace($keep) -or $keep.Trim().ToLower().StartsWith("y")) {
        Remove-Item -Recurse -Force (Join-Path $Dest "venv"), (Join-Path $Dest "src") -ErrorAction SilentlyContinue
        Write-Host "      Removed the program; kept $Dest\settings.json." -ForegroundColor Green
    } else {
        Remove-Item -Recurse -Force $Dest -ErrorAction SilentlyContinue
        Write-Host "      Removed $Dest." -ForegroundColor Green
    }
}
Write-Host ""
Read-Host "Press Enter to close" | Out-Null
