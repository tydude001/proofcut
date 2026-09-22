# proofcut on Windows, in one file - docs/plans/PORTABILITY.md step 5b, scripts/mac_trial.sh's twin.
#
#   powershell -ExecutionPolicy Bypass -File proofcut\scripts\windows_trial.ps1              run the test
#   powershell -ExecutionPolicy Bypass -File proofcut\scripts\windows_trial.ps1 -Uninstall   remove what it added
#
# Run from a clone of the public repo. It installs the tools, runs docs/DEMO.md in that clone's
# checkout, and zips a report for a GitHub issue, with the home folder replaced by `~` in every
# text file in it.
#
# It installs with `proofcut setup`, never with code of its own (HISTORY.md, "The kits call
# setup"). This script downloads one thing, uv's standalone zip, pinned by URL and SHA-256, into
# %LOCALAPPDATA%\proofcut-windows-trial, then runs scripts\setup_trial.py: `proofcut setup --yes`
# (which installs only what doctor marks missing, from setup's own pins in
# src\proofcut\install.py), `proofcut doctor`, and DEMO.md's commands into the test folder,
# stopping at the first one that fails, since that is the finding. Until 2026-09-21 this kit
# downloaded ffmpeg, auto-editor, espeak-ng and Shotcut itself; setup's Windows route grew out of
# that, and a person's run now exercises the installer everyone gets.
#
# uv's caches, Pythons and tools and whisper's speech model are pointed into the test folder, so
# they go when it goes. What setup installs goes where setup puts it - %LOCALAPPDATA%\proofcut\deps,
# and ffmpeg.exe/ffprobe.exe moved into ~\.local\bin, since a symlink needs Developer Mode - and
# setup records it, so -Uninstall runs `proofcut setup --uninstall` and then deletes the folder.
# No admin rights, no registry keys, no winget.
#
# Written for Windows PowerShell 5.1, which is what a stock Windows has, and kept to ASCII: 5.1
# reads a script with no byte-order mark as the ANSI code page.

param(
    [switch]$Uninstall,
    # No questions: the CI job, which has nobody to press Enter.
    [switch]$Unattended
)

$ErrorActionPreference = 'Continue'   # a native command writing to stderr is not a failure; exit codes are checked
$ProgressPreference = 'SilentlyContinue'   # 5.1's download progress bar costs more than the download
[Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12

$ISSUE_URL = 'https://github.com/tydude001/proofcut/issues/new?template=windows-test.yml'
# The PROOFCUT_TRIAL_* overrides exist for the CI job, which uploads the report rather than leaving it on a Desktop.
$W = if ($env:PROOFCUT_TRIAL_DIR) { $env:PROOFCUT_TRIAL_DIR } else { Join-Path $env:LOCALAPPDATA 'proofcut-windows-trial' }
$MARKER = Join-Path $W '.proofcut-windows-trial'
$TOOLS = Join-Path $W 'tools'
$BIN = Join-Path $TOOLS 'bin'
$UV = Join-Path $BIN 'uv.exe'
$DEMO = Join-Path $W 'demo'
$REPORT = if ($env:PROOFCUT_TRIAL_REPORT) { $env:PROOFCUT_TRIAL_REPORT } else { Join-Path ([Environment]::GetFolderPath('Desktop')) 'proofcut-windows-report.zip' }
$RULE = [string][char]0x2500 + [char]0x2500   # the step header mark scripts/trial_check.py looks for
$REPO = Split-Path -Parent $PSScriptRoot

# uv, the one download this script makes.
$PIN_UV = @{ What = 'uv 0.12.13 (Python project manager)';
             Url = 'https://github.com/astral-sh/uv/releases/download/0.12.13/uv-x86_64-pc-windows-msvc.zip';
             Sha256 = 'a86c9dc7bad9b03f388583b7187c05fe9951c2e0d392217e8fd43d97787f6ec2' }

function Ask([string]$Question) {
    if ($Unattended) { return $true }
    $answer = Read-Host "$Question [y/N]"
    return ($answer -eq 'y' -or $answer -eq 'Y')
}

# Everything uv and whisper would otherwise put under the user profile goes in the folder.
function Set-UvEnv {
    $env:UV_CACHE_DIR = Join-Path $W 'uv\cache'
    $env:UV_PYTHON_INSTALL_DIR = Join-Path $W 'uv\python'
    $env:UV_TOOL_DIR = Join-Path $W 'uv\tools'
    $env:UV_TOOL_BIN_DIR = Join-Path $W 'uv\bin'
    # Without this, `uv sync` takes a Python 3.13 the PC already has (a person's laptop did, from
    # AppData\Local\Programs) and the .venv then depends on something outside the folder.
    $env:UV_PYTHON_PREFERENCE = 'only-managed'
    $env:XDG_CACHE_HOME = Join-Path $W 'cache'   # whisper keeps its speech model under here
    $env:PYTHONUTF8 = '1'
    $env:PYTHONIOENCODING = 'utf-8'
    # setup's ffmpeg goes in ~\.local\bin, as it does for anyone who runs it.
    $localBin = Join-Path $env:USERPROFILE '.local\bin'
    $env:PATH = (@($localBin, $env:UV_TOOL_BIN_DIR, $BIN) -join ';') + ';' + $env:PATH
}

# ---- -Uninstall ---------------------------------------------------------------------------
if ($Uninstall) {
    if (-not (Test-Path -LiteralPath $MARKER)) {
        Write-Host "No record of a proofcut test on this PC ($MARKER is missing), so nothing to remove."
        exit 0
    }
    Write-Host ''
    Write-Host '  This removes what the proofcut test added to this PC, and nothing else:'
    Write-Host "    - what 'proofcut setup' installed, from setup's own record"
    Write-Host "    - the folder everything else is in: $W"
    Write-Host ''
    if (-not (Ask '  Go ahead?')) { exit 0 }
    $setupTrial = Join-Path $REPO 'scripts\setup_trial.py'
    if ((Test-Path -LiteralPath $UV) -and (Test-Path -LiteralPath (Join-Path $W 'before-setup.json')) -and (Test-Path -LiteralPath $setupTrial)) {
        Set-UvEnv
        Push-Location -LiteralPath $REPO
        & $UV run python $setupTrial $W --uninstall
        if ($LASTEXITCODE -ne 0) { Write-Host "  setup's uninstall reported a difference (above); the folder is removed regardless." }
        Pop-Location
    }
    Remove-Item -LiteralPath $W -Recurse -Force
    if (Test-Path -LiteralPath $W) {
        Write-Host "  Some files could not be removed (is a program still using them?): $W"
        exit 1
    }
    Write-Host ''
    Write-Host '  Done. The report on your Desktop (proofcut-windows-report.zip) is yours to delete once sent.'
    Write-Host "  The proofcut folder you cloned is yours too: delete it when you are finished with it."
    Write-Host "  Its .venv folder uses a Python that was in the removed folder, so run 'uv sync' again if you keep using it."
    exit 0
}

# ---- the test -----------------------------------------------------------------------------
$pyproject = Join-Path $REPO 'pyproject.toml'
if (-not (Test-Path -LiteralPath (Join-Path $REPO 'scripts\setup_trial.py')) -or
    -not (Test-Path -LiteralPath $pyproject) -or
    -not (Select-String -LiteralPath $pyproject -Pattern '^name = "proofcut"' -Quiet)) {
    Write-Host 'This copy is not inside a proofcut checkout.'
    Write-Host 'Clone the repo and run it from there:  git clone https://github.com/tydude001/proofcut'
    exit 1
}
if (($REPO + '\').StartsWith($W + '\', [StringComparison]::OrdinalIgnoreCase)) {
    Write-Host "The checkout is inside $W, which every run clears. Clone it somewhere else."
    exit 1
}
$REV = 'no-git'
if (Get-Command git -ErrorAction SilentlyContinue) {
    $rev = & git -C $REPO rev-parse --short HEAD 2>$null
    if ($LASTEXITCODE -eq 0 -and $rev) { $REV = "$rev".Trim() }
}

Write-Host @"

  proofcut - Windows test
  -----------------------
  This will:
    1. download uv into one folder, $W, and use it to run 'proofcut setup',
       proofcut's own installer, which fetches what this PC is missing of ffmpeg, auto-editor,
       Shotcut's renderer and whisper. It needs no administrator rights.
    2. make a short test video and let proofcut edit it
    3. put proofcut-windows-report.zip on your Desktop, with your home folder's name taken out

  It takes 15-30 minutes, mostly downloading (about 2 GB, most of it the speech model and
  whisper's PyTorch). You can leave it running.
  To remove everything it added afterwards, run this same file with -Uninstall.

"@
if (-not $Unattended) {
    Read-Host '  Press Enter to start, or Ctrl-C to stop' | Out-Null
}

# A re-run starts clean. What setup installed last time stays in setup's own record, and setup
# leaves it alone this time (doctor reads it as present), so -Uninstall still finds it.
if (Test-Path -LiteralPath $MARKER) {
    Remove-Item -LiteralPath $W -Recurse -Force
}
elseif (Test-Path -LiteralPath $W) {
    Write-Host "$W exists and was not made by this test, so it is left alone. Move it, or set PROOFCUT_TRIAL_DIR."
    exit 1
}
New-Item -ItemType Directory -Force -Path $W, $BIN | Out-Null
New-Item -ItemType File -Force -Path $MARKER | Out-Null

# The console, whole. report.txt is setup_trial.py's, and is what trial_check.py reads.
$LOG = Join-Path $W 'kit.txt'
# UTF-8 without a byte-order mark, written through one handle: 5.1's Tee-Object and Out-File write
# UTF-16, and proofcut's own output carries non-ASCII.
$utf8 = New-Object System.Text.UTF8Encoding $false
$script:logw = New-Object System.IO.StreamWriter($LOG, $true, $utf8)
$script:logw.AutoFlush = $true
try { [Console]::OutputEncoding = $utf8 } catch { }   # no console at all under some hosts

function Say([string]$Line) {
    Write-Host $Line
    $script:logw.WriteLine($Line)
}

$script:STEPS = New-Object System.Collections.Generic.List[string]
$script:FAIL = ''

function Note-Step([string]$What, [bool]$Ok, [double]$Seconds, [string]$Why) {
    $took = '{0}s' -f [int]$Seconds
    if ($Ok) {
        $script:STEPS.Add("ok    $took  $What")
    }
    else {
        $script:STEPS.Add("FAIL  $took  $What  ($Why)")
        $script:FAIL = $What
        Say "!! failed ($Why) after $took"
    }
}

# A native command, its output streamed to the screen and the log. True when it exited 0.
function Step([string]$What, [string]$Exe, [string[]]$ArgList) {
    Say ''
    Say "$RULE $What"
    Say ('$ ' + ((@($Exe) + $ArgList) -join ' '))
    $clock = [Diagnostics.Stopwatch]::StartNew()
    $code = $null
    try {
        & $Exe @ArgList 2>&1 | ForEach-Object {
            if ($_ -is [System.Management.Automation.ErrorRecord]) { Say $_.Exception.Message } else { Say "$_" }
        }
        $code = $LASTEXITCODE
    }
    catch {
        Say "$_"
    }
    $ok = ($code -eq 0)
    $why = if ($null -eq $code) { 'could not run' } else { "exit $code" }
    Note-Step $What $ok $clock.Elapsed.TotalSeconds $why
    return $ok
}

# PowerShell work (a download, an unpack). True when the block threw nothing.
function Task([string]$What, [scriptblock]$Block) {
    Say ''
    Say "$RULE $What"
    $clock = [Diagnostics.Stopwatch]::StartNew()
    try {
        & $Block | ForEach-Object { Say "$_" }
        Note-Step $What $true $clock.Elapsed.TotalSeconds ''
        return $true
    }
    catch {
        Say "$_"
        Note-Step $What $false $clock.Elapsed.TotalSeconds 'error'
        return $false
    }
}

function Get-Pinned($Piece) {
    $downloads = Join-Path $W 'downloads'
    New-Item -ItemType Directory -Force -Path $downloads | Out-Null
    $file = Join-Path $downloads ([IO.Path]::GetFileName($Piece.Url))
    Say "downloading $($Piece.Url)"
    # Three tries: GitHub's release downloads answer a lone 504 now and then, and one stopped a
    # person's run at the first step (HISTORY.md, "The editor on Windows, looked at"). 5.1's
    # Invoke-WebRequest has no -MaximumRetryCount. A SHA-256 mismatch below is never retried.
    for ($try = 1; ; $try++) {
        try {
            Invoke-WebRequest -UseBasicParsing -Uri $Piece.Url -OutFile $file
            break
        }
        catch {
            if (Test-Path -LiteralPath $file) { Remove-Item -LiteralPath $file -Force }
            if ($try -ge 3) { throw }
            Say "try $try failed ($($_.Exception.Message.Trim())), trying again in $(10 * $try)s"
            Start-Sleep -Seconds (10 * $try)
        }
    }
    $got = (Get-FileHash -LiteralPath $file -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($got -ne $Piece.Sha256) {
        Remove-Item -LiteralPath $file -Force
        throw "$([IO.Path]::GetFileName($file)): SHA-256 is $got, expected $($Piece.Sha256). Not using it."
    }
    Say "sha256 ok: $got"
    return $file
}

function Hide-Home([string]$Text) {
    # A path appears as written, JSON-escaped, and with forward slashes; the username is the only
    # personal thing in any of them.
    $homeDir = $env:USERPROFILE
    foreach ($form in @($homeDir.Replace('\', '\\'), $homeDir, $homeDir.Replace('\', '/'))) {
        $Text = [regex]::Replace($Text, [regex]::Escape($form), '~', 'IgnoreCase')
    }
    return $Text
}

$script:FINISHED = $false
function Finish {
    if ($script:FINISHED) { return }
    $script:FINISHED = $true
    $os = Get-CimInstance Win32_OperatingSystem -ErrorAction SilentlyContinue
    $arch = $env:PROCESSOR_ARCHITECTURE
    if ($env:PROCESSOR_ARCHITEW6432) { $arch = "$env:PROCESSOR_ARCHITEW6432 (this shell: $arch)" }
    Say ''
    Say "$([char]0x2550)$([char]0x2550)$([char]0x2550)$([char]0x2550) summary"
    Say "proofcut $REV $([char]0x00B7) $($os.Caption) $($os.Version) $([char]0x00B7) $arch"
    foreach ($s in $script:STEPS) { Say "  $s" }
    if ($script:FAIL) { Say "STOPPED AT: $($script:FAIL)" } else { Say 'ALL STEPS RAN' }
    $script:logw.Close()

    $out = Join-Path $W 'report'
    if (Test-Path -LiteralPath $out) { Remove-Item -LiteralPath $out -Recurse -Force }
    if (Test-Path -LiteralPath $REPORT) { Remove-Item -LiteralPath $REPORT -Force }
    New-Item -ItemType Directory -Force -Path $out | Out-Null
    foreach ($f in @($LOG, (Join-Path $W 'report.txt'), (Join-Path $DEMO 'proj\proofcut.json'), (Join-Path $DEMO 'proj\project.otio'))) {
        if (Test-Path -LiteralPath $f) {
            $text = [IO.File]::ReadAllText($f, $utf8)
            [IO.File]::WriteAllText((Join-Path $out ([IO.Path]::GetFileName($f))), (Hide-Home $text), $utf8)
        }
    }
    foreach ($f in @((Join-Path $W 'frame-3s.png'), (Join-Path $W 'frame-10s.png'), (Join-Path $DEMO 'demo.mp4'))) {
        if (Test-Path -LiteralPath $f) { Copy-Item -LiteralPath $f -Destination $out }
    }
    Compress-Archive -Path (Join-Path $out '*') -DestinationPath $REPORT -Force
    Write-Host ''
    Write-Host "  Done. The report is on your Desktop:  $([IO.Path]::GetFileName($REPORT))"
    Write-Host "  Please attach it to a Windows test report: $ISSUE_URL"
    Write-Host "  To remove everything the test installed:  powershell -ExecutionPolicy Bypass -File `"$PSCommandPath`" -Uninstall"
    Write-Host ''
}

# The whole run sits in try/finally, so Ctrl-C mid-download still writes the summary and the report.
try {
    $winver = Get-CimInstance Win32_OperatingSystem -ErrorAction SilentlyContinue
    $cs = Get-CimInstance Win32_ComputerSystem -ErrorAction SilentlyContinue
    $cpu = Get-CimInstance Win32_Processor -ErrorAction SilentlyContinue | Select-Object -First 1
    $drive = Get-PSDrive -Name ($W.Substring(0, 1)) -ErrorAction SilentlyContinue
    Say "proofcut Windows test $([char]0x00B7) proofcut $REV $([char]0x00B7) $(Get-Date -Format 'yyyy-MM-dd HH:mm K')"
    Say "$($winver.Caption) $($winver.Version) $([char]0x00B7) $env:PROCESSOR_ARCHITECTURE $([char]0x00B7) $($cpu.Name)"
    Say ("memory {0} GB $([char]0x00B7) free disk {1} GB $([char]0x00B7) PowerShell {2}" -f [int]($cs.TotalPhysicalMemory / 1GB), [int]($drive.Free / 1GB), $PSVersionTable.PSVersion)
    Say "proofcut checkout: $(Hide-Home $REPO)"

    Set-UvEnv
    $ok = Task 'download uv (pinned, checked by SHA-256)' {
        $file = Get-Pinned $PIN_UV
        # ZipFile, not 5.1's Expand-Archive, which is slow and chatty.
        Add-Type -AssemblyName System.IO.Compression.FileSystem
        [System.IO.Compression.ZipFile]::ExtractToDirectory($file, $BIN)
        Remove-Item -LiteralPath $file -Force
        & $UV --version
    }
    if (-not $ok) { return }

    Set-Location -LiteralPath $REPO
    if (-not (Step 'uv sync' $UV @('sync'))) { return }
    # setup, doctor and DEMO.md, logged step by step into report.txt, then the frames at 3 s and 10 s.
    if (-not (Step 'proofcut setup, doctor and the demo (scripts/setup_trial.py)' $UV @('run', 'python', 'scripts\setup_trial.py', $W))) {
        # Name the step that stopped, as the issue form asks.
        $trialLog = Join-Path $W 'report.txt'
        if (Test-Path -LiteralPath $trialLog) {
            $inner = Select-String -LiteralPath $trialLog -Pattern '^STOPPED AT: (.+)$' | Select-Object -Last 1
            if ($inner) { $script:FAIL = $inner.Matches[0].Groups[1].Value }
        }
    }
}
finally {
    if (-not $script:FINISHED) {
        if (-not $script:FAIL -and $script:STEPS.Count -eq 0) { $script:FAIL = 'stopped before the first step' }
        Finish
    }
}

if (-not $script:FAIL -and -not $Unattended) {
    if (Ask "  Want to see proofcut's editor window with the result?") {
        & $UV run proofcut -C (Join-Path $DEMO 'proj') open
    }
}
exit 0
