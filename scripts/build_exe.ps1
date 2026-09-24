# Builds MeetingRecorder.exe (and the installer, if Inno Setup is installed) on your own PC.
# Run install.ps1 once first, then open this file in PowerShell ISE and press F5.
#
# Output:
#   dist\MeetingRecorder\MeetingRecorder.exe     the app (keep the whole folder together)
#   dist\MeetingRecorder-Setup-<version>.exe     installer, only if Inno Setup 6 is present

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$py = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) {
    Write-Host "Run scripts\install.ps1 first to create the .venv folder." -ForegroundColor Red
    return
}

Push-Location $root
try {
    & $py -m pip install "pyinstaller>=6.10"
    & $py -m PyInstaller packaging\MeetingRecorder.spec --noconfirm --clean
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }

    # Quick check that the packaged app can load its audio and Whisper libraries.
    $report = Join-Path $root "dist\selftest.json"
    $p = Start-Process -FilePath "dist\MeetingRecorder\MeetingRecorder.exe" `
            -ArgumentList "--selftest", "`"$report`"" -Wait -PassThru
    Get-Content $report
    if ($p.ExitCode -ne 0) { throw "Self-test failed. See $report" }

    $iscc = "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe"
    if (Test-Path $iscc) {
        $version = & $py -c "import meeting_recorder; print(meeting_recorder.__version__)"
        & $iscc "/DAppVersion=$version" packaging\installer.iss
    } else {
        Write-Host "Inno Setup 6 not found, so no installer was built. Get it from https://jrsoftware.org/isdl.php"
    }

    Write-Host ""
    Write-Host "Built dist\MeetingRecorder\MeetingRecorder.exe" -ForegroundColor Green
} finally {
    Pop-Location
}
