# Iotift installer for Windows.
#
#   irm https://raw.githubusercontent.com/CoolNinja2009/Iotift2/main/install.ps1 | iex
#
# What it does, all inside your user account (no admin rights needed):
#   1. finds Python 3.8+ (installs Python 3.12 with winget if there is none),
#   2. installs Iotift into its own environment in %LOCALAPPDATA%\Iotift,
#   3. puts the `iotift` command on your PATH,
#   4. runs `iotift setup`, which installs PlatformIO and the ESP32 toolchain.
#
# Options, set as environment variables before running:
#   $env:IOTIFT_VERSION = '2.0.0'   install that release instead of the newest one
#   $env:IOTIFT_SKIP_SETUP = '1'    skip step 4 (it then happens on your first upload)
#   $env:IOTIFT_FROM = 'C:\path'    install from a local folder, wheel or URL instead
#
# Uninstall: delete %LOCALAPPDATA%\Iotift and remove %LOCALAPPDATA%\Iotift\bin from your PATH.

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12

$Repo = 'CoolNinja2009/Iotift2'
$Root = Join-Path $env:LOCALAPPDATA 'Iotift'
$Venv = Join-Path $Root 'venv'
$Bin  = Join-Path $Root 'bin'

function Step($text) { Write-Host "`n==> $text" -ForegroundColor Cyan }
function Ok($text)   { Write-Host "    $text" -ForegroundColor Green }
function Note($text) { Write-Host "    $text" }
function Die($text)  { Write-Host "`n$text" -ForegroundColor Red; throw 'Iotift was not installed.' }

function Test-Python($cmd, $argv) {
    # The Microsoft Store "python" stub prints nothing useful, so ask Python itself.
    try {
        $out = & $cmd @argv -c "import sys; print('OK' if sys.version_info >= (3, 8) else 'OLD')" 2>$null
        return ($LASTEXITCODE -eq 0 -and "$out".Trim() -eq 'OK')
    } catch { return $false }
}

function Find-Python {
    $candidates = @(
        @{ cmd = 'py';      argv = @('-3') },
        @{ cmd = 'python';  argv = @() },
        @{ cmd = 'python3'; argv = @() }
    )
    foreach ($c in $candidates) {
        if (Get-Command $c.cmd -ErrorAction SilentlyContinue) {
            if (Test-Python $c.cmd $c.argv) { return $c }
        }
    }
    # Fresh winget installs are not on this window's PATH yet.
    $dirs = Get-ChildItem (Join-Path $env:LOCALAPPDATA 'Programs\Python') -Directory -ErrorAction SilentlyContinue |
            Sort-Object Name -Descending
    foreach ($d in $dirs) {
        $exe = Join-Path $d.FullName 'python.exe'
        if ((Test-Path $exe) -and (Test-Python $exe @())) { return @{ cmd = $exe; argv = @() } }
    }
    return $null
}

Write-Host ''
Write-Host '  Iotift installer' -ForegroundColor White
Write-Host '  A programming language for the ESP32' -ForegroundColor DarkGray

# ---------------------------------------------------------------- 1. Python
Step 'Looking for Python 3.8 or newer'
$py = Find-Python
if (-not $py) {
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        Note 'Python was not found. Installing Python 3.12 with winget (this takes a minute)...'
        winget install --exact --id Python.Python.3.12 --scope user --silent `
            --accept-package-agreements --accept-source-agreements | Out-Host
        $py = Find-Python
    }
    if (-not $py) {
        Die ("Python 3.8 or newer is needed. Install it from https://www.python.org/downloads/ " +
             "(tick 'Add python.exe to PATH'), open a new PowerShell window and run this installer again.")
    }
}
$pyVersion = & $py.cmd @($py.argv) -c "import sys; print(sys.version.split()[0])"
Ok "Python $pyVersion"

# ---------------------------------------------------------------- 2. Iotift
Step "Installing Iotift into $Root"
New-Item -ItemType Directory -Force -Path $Root, $Bin | Out-Null
$venvPy = Join-Path $Venv 'Scripts\python.exe'
if (-not (Test-Path $venvPy)) {
    & $py.cmd @($py.argv) -m venv $Venv
    if ($LASTEXITCODE -ne 0) { Die 'Could not create a Python environment for Iotift.' }
}

$source = $env:IOTIFT_FROM
if (-not $source) {
    try {
        # /releases, not /releases/latest: the newest release may be a pre-release.
        $releases = Invoke-RestMethod -Uri "https://api.github.com/repos/$Repo/releases?per_page=20" `
                                      -Headers @{ 'User-Agent' = 'iotift-installer' }
        foreach ($r in $releases) {
            if ($r.draft) { continue }
            if ($env:IOTIFT_VERSION -and $r.tag_name.TrimStart('v') -ne $env:IOTIFT_VERSION.TrimStart('v')) { continue }
            $wheel = $r.assets | Where-Object { $_.name -like '*.whl' } | Select-Object -First 1
            if ($wheel) { $source = $wheel.browser_download_url; Note "Release $($r.tag_name)"; break }
        }
    } catch {
        Note 'Could not reach the GitHub releases list; using the latest source instead.'
    }
}
if (-not $source) {
    if ($env:IOTIFT_VERSION) { Die "Release $($env:IOTIFT_VERSION) was not found." }
    $source = "https://github.com/$Repo/archive/refs/heads/main.zip"
    Note 'No release yet; installing the latest source from the main branch.'
}

& $venvPy -m pip install --quiet --disable-pip-version-check --upgrade pip
& $venvPy -m pip install --disable-pip-version-check --upgrade $source
if ($LASTEXITCODE -ne 0) { Die 'pip could not install Iotift (see the messages above).' }

# pip's iotift.exe launcher has the environment's python path baked in, so a copy works anywhere.
foreach ($name in 'iotift', 'iotift2') {
    Copy-Item -Force (Join-Path $Venv "Scripts\$name.exe") (Join-Path $Bin "$name.exe")
}
$version = & (Join-Path $Bin 'iotift.exe') --version
Ok "$version"

# ---------------------------------------------------------------- 3. PATH
Step 'Adding iotift to your PATH'
$userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
$parts = @($userPath -split ';' | Where-Object { $_ })
if ($parts -notcontains $Bin) {
    [Environment]::SetEnvironmentVariable('Path', (($parts + $Bin) -join ';'), 'User')
    Ok "Added $Bin"
} else {
    Ok 'Already there'
}
if (($env:Path -split ';') -notcontains $Bin) { $env:Path = "$env:Path;$Bin" }

# ---------------------------------------------------------------- 4. PlatformIO + toolchain
if ($env:IOTIFT_SKIP_SETUP) {
    Step 'Skipping PlatformIO setup (IOTIFT_SKIP_SETUP is set); it runs on your first upload.'
} else {
    Step 'Setting up PlatformIO and the ESP32 toolchain (one time, a few minutes)'
    & (Join-Path $Bin 'iotift.exe') setup --yes
    if ($LASTEXITCODE -ne 0) {
        Note 'Setup did not finish. Iotift is installed; run `iotift setup` again later to retry.'
    }
}

Write-Host ''
Write-Host '  Iotift is installed.' -ForegroundColor Green
Write-Host ''
Write-Host '  Try it:'
Write-Host '    iotift upload blink.iot          build and flash the board on USB'
Write-Host '    iotift upload blink.iot -m       ...then open the serial monitor'
Write-Host '    iotift --help                    every command'
Write-Host ''
Write-Host '  Open a new terminal window if `iotift` is not found in other windows.' -ForegroundColor DarkGray
Write-Host '  No board found? Install the CP210x or CH340 USB driver for your board.' -ForegroundColor DarkGray
Write-Host ''
