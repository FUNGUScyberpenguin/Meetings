# Installs Meeting Recorder into a local virtual environment and adds a desktop shortcut.
# Run from PowerShell ISE (no admin needed):  open this file, then press F5.

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$venv = Join-Path $root ".venv"

# Find Python 3.10-3.12 through the py launcher, falling back to python on PATH.
$python = $null
foreach ($v in "3.12", "3.11", "3.10") {
    try {
        & py "-$v" -c "import sys" 2>$null
        if ($LASTEXITCODE -eq 0) { $python = @("py", "-$v"); break }
    } catch { }
}
if (-not $python) {
    try {
        $ver = & python -c "import sys; print(f'{sys.version_info[0]}.{sys.version_info[1]}')"
        if ($ver -in "3.10", "3.11", "3.12") { $python = @("python") }
    } catch { }
}
if (-not $python) {
    Write-Host "Python 3.10, 3.11 or 3.12 is required. Install it from https://www.python.org/downloads/windows/" -ForegroundColor Red
    Write-Host "Tick 'Add python.exe to PATH' in the installer, then run this script again."
    return
}

Write-Host "Creating virtual environment in $venv"
if ($python.Count -gt 1) { & $python[0] $python[1] -m venv $venv } else { & $python[0] -m venv $venv }

$py = Join-Path $venv "Scripts\python.exe"
& $py -m pip install --upgrade pip
& $py -m pip install -e $root
if ($LASTEXITCODE -ne 0) { throw "pip install failed" }

# Desktop shortcut that starts the app without a console window.
$pythonw = Join-Path $venv "Scripts\pythonw.exe"
$desktop = [Environment]::GetFolderPath("Desktop")
$shell = New-Object -ComObject WScript.Shell
$lnk = $shell.CreateShortcut((Join-Path $desktop "Meeting Recorder.lnk"))
$lnk.TargetPath = $pythonw
$lnk.Arguments = "-m meeting_recorder"
$lnk.WorkingDirectory = $root
$lnk.IconLocation = "$env:SystemRoot\System32\SndVol.exe,0"
$lnk.Save()

Write-Host ""
Write-Host "Done. Start it from the 'Meeting Recorder' shortcut on your desktop." -ForegroundColor Green
Write-Host "The first transcription downloads the Whisper model (about 500 MB for 'small')."
