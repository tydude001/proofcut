# proofcut on Windows, past the demo - docs/plans/PORTABILITY.md 5e. Run it after windows_trial.ps1.
#
#   powershell -ExecutionPolicy Bypass -File proofcut\scripts\windows_probe.ps1
#   powershell -ExecutionPolicy Bypass -File proofcut\scripts\windows_probe.ps1 -SecondDrive E:\ -Footage C:\Users\you\Videos\clip.mp4
#
# The demo proves proofcut runs in one short folder on C:. This re-runs the demo's edit in folders
# shaped to hit what only Windows gets wrong: a space, an accent, and characters the ANSI code page
# cannot hold in the path; a path past MAX_PATH; a project on another drive from its footage; two
# spellings of one folder against the MCP server's project check; and a caption burn. Every render
# is read back. With -Footage it also imports one of your own clips and renders a cut of it; the
# report carries that clip's numbers, never its name or a frame of it.
#
# The work is scripts/windows_probe.py; this file only borrows the test kit's settings and the
# tools `proofcut setup` installed for it (ffmpeg in ~\.local\bin, melt in setup's own folder,
# which proofcut finds by itself), so nothing is downloaded and nothing is transcribed. It takes about five minutes. Everything it
# writes goes in the kit's folder, so windows_trial.ps1 -Uninstall removes it too; a -SecondDrive
# folder is deleted when the probe finishes.
#
# Windows PowerShell 5.1 and ASCII only, for the reason windows_trial.ps1 gives.

param(
    [string]$SecondDrive,
    [string]$Footage
)

$ErrorActionPreference = 'Continue'
$W = if ($env:PROOFCUT_TRIAL_DIR) { $env:PROOFCUT_TRIAL_DIR } else { Join-Path $env:LOCALAPPDATA 'proofcut-windows-trial' }
$REPORT = if ($env:PROOFCUT_PROBE_REPORT) { $env:PROOFCUT_PROBE_REPORT } else { Join-Path ([Environment]::GetFolderPath('Desktop')) 'proofcut-windows-probe.zip' }
$ISSUE_URL = 'https://github.com/tydude001/proofcut/issues/new?template=windows-test.yml'
$REPO = Split-Path -Parent $PSScriptRoot
$TOOLS = Join-Path $W 'tools'
$bin = Join-Path $TOOLS 'bin'
$uv = Join-Path $bin 'uv.exe'

if (-not (Test-Path -LiteralPath (Join-Path $W '.proofcut-windows-trial')) -or -not (Test-Path -LiteralPath $uv) -or
    -not (Test-Path -LiteralPath (Join-Path $W 'demo\proj\cache\transcripts\vo.json'))) {
    Write-Host "No finished test kit run in $W."
    Write-Host 'Run windows_trial.ps1 first, to the end; this reuses its tools, footage and transcript.'
    exit 1
}

$args_ = @('run', 'python', 'scripts\windows_probe.py', $W, '--report', $REPORT)
if ($SecondDrive) {
    if (-not (Test-Path -LiteralPath $SecondDrive)) { Write-Host "-SecondDrive $SecondDrive does not exist."; exit 1 }
    $q = (Split-Path -Qualifier (Resolve-Path -LiteralPath $SecondDrive).Path).ToUpper()
    if ($q -eq (Split-Path -Qualifier $W).ToUpper()) { Write-Host "-SecondDrive $SecondDrive is on the same drive as the kit ($q). Give a folder on another drive."; exit 1 }
    $args_ += @('--second-drive', $SecondDrive)
}
else {
    $others = @(Get-PSDrive -PSProvider FileSystem | Where-Object { $_.Free -gt 1GB -and ($_.Name + ':') -ne (Split-Path -Qualifier $W) } | ForEach-Object { $_.Name + ':\' })
    if ($others.Count -gt 0) { Write-Host "  No -SecondDrive given, so that case is skipped. Drives this PC has: $($others -join ', ')" }
    else { Write-Host '  No -SecondDrive given, and no other drive is attached (a USB stick works), so that case is skipped.' }
}
if ($Footage) {
    if (-not (Test-Path -LiteralPath $Footage -PathType Leaf)) { Write-Host "-Footage $Footage is not a file."; exit 1 }
    $args_ += @('--footage', $Footage)
}

# The kit's own environment, restated rather than dot-sourced: the kit runs its test when loaded.
$env:UV_CACHE_DIR = Join-Path $W 'uv\cache'
$env:UV_PYTHON_INSTALL_DIR = Join-Path $W 'uv\python'
$env:UV_TOOL_DIR = Join-Path $W 'uv\tools'
$env:UV_TOOL_BIN_DIR = Join-Path $W 'uv\bin'
$env:UV_PYTHON_PREFERENCE = 'only-managed'
$env:XDG_CACHE_HOME = Join-Path $W 'cache'
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
try { [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding $false } catch { }
# setup's ffmpeg, where the kit's PATH put it first; melt is whatever proofcut itself resolves,
# setup's own copy included, so the probe measures the melt a person's proofcut would run.
$localBin = Join-Path $env:USERPROFILE '.local\bin'
$env:PATH = (@($localBin, $env:UV_TOOL_BIN_DIR, $bin) -join ';') + ';' + $env:PATH
if (-not (Get-Command ffmpeg -ErrorAction SilentlyContinue)) { Write-Host "No ffmpeg on PATH or in $localBin. Run windows_trial.ps1 again."; exit 1 }

Set-Location -LiteralPath $REPO
& $uv @args_
$code = $LASTEXITCODE
if ($code -eq 0) {
    Write-Host ''
    Write-Host "  The report is on your Desktop: $([IO.Path]::GetFileName($REPORT))"
    Write-Host "  Please attach it to a Windows test report: $ISSUE_URL"
}
exit $code
