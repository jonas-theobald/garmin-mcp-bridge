#Requires -Version 5.1
<#
.SYNOPSIS
    Install garmin-mcp-bridge and register it with Claude Desktop on Windows.

.DESCRIPTION
    Checks for uv, builds the environment, verifies the Intervals.icu API key
    against the live API, and adds the server to claude_desktop_config.json.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\scripts\install.ps1
#>

$ErrorActionPreference = 'Stop'

$RepoDir    = Split-Path -Parent $PSScriptRoot
$ConfigPath = Join-Path $env:APPDATA 'Claude\claude_desktop_config.json'
$ClientName = 'Claude Desktop'

function Write-Head($msg) { Write-Host $msg -ForegroundColor White }
function Write-Ok($msg)   { Write-Host "  ok    $msg" -ForegroundColor Green }
function Write-Warn($msg) { Write-Host "  warn  $msg" -ForegroundColor Yellow }
function Stop-Install($msg) { Write-Host "  error $msg" -ForegroundColor Red; exit 1 }

Write-Head 'garmin-mcp-bridge installer'
Write-Host ''

# --- 1. uv -------------------------------------------------------------------

Write-Head '1/5  Checking for uv'
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Warn 'uv not found'
    $reply = Read-Host '  Install uv now via the official installer? [y/N]'
    if ($reply -match '^[Yy]$') {
        Invoke-RestMethod https://astral.sh/uv/install.ps1 | Invoke-Expression
        $uvBin = Join-Path $env:USERPROFILE '.local\bin'
        if (Test-Path (Join-Path $uvBin 'uv.exe')) { $env:Path = "$uvBin;$env:Path" }
        if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
            Stop-Install 'uv installed but not on PATH. Open a new terminal and re-run.'
        }
    } else {
        Stop-Install 'uv is required. See https://docs.astral.sh/uv/getting-started/installation/'
    }
}
Write-Ok "uv $((uv --version) -split ' ' | Select-Object -Last 1)"

# --- 2. environment ----------------------------------------------------------

Write-Head '2/5  Building the environment'
Push-Location $RepoDir
try { uv sync --quiet } finally { Pop-Location }

$PythonBin = Join-Path $RepoDir '.venv\Scripts\python.exe'
if (-not (Test-Path $PythonBin)) { Stop-Install "Expected interpreter missing at $PythonBin" }
Write-Ok $PythonBin

# --- 3. API key --------------------------------------------------------------

Write-Head '3/5  Intervals.icu API key'
Write-Host "  Get it at https://intervals.icu/settings - bottom of the page, 'Developer Settings'."
$ApiKey = $env:INTERVALS_API_KEY
if (-not $ApiKey) {
    $secure = Read-Host '  Paste your API key' -AsSecureString
    $ApiKey = [Runtime.InteropServices.Marshal]::PtrToStringAuto(
        [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure))
}
if (-not $ApiKey) { Stop-Install 'No API key given.' }

# --- 4. verify ---------------------------------------------------------------

Write-Head '4/5  Verifying against the live API'
$env:INTERVALS_API_KEY = $ApiKey
& $PythonBin (Join-Path $RepoDir 'selftest.py')
if ($LASTEXITCODE -ne 0) {
    Write-Host ''
    Write-Warn 'Selftest reported failures.'
    Write-Warn 'If activities are missing, run the Garmin backfill: Intervals.icu >'
    Write-Warn "Settings > Connections > Garmin > 'Download old data'."
    $reply = Read-Host "  Register with $ClientName anyway? [y/N]"
    if ($reply -notmatch '^[Yy]$') { Stop-Install 'Aborted.' }
}

# --- 5. register -------------------------------------------------------------

Write-Head "5/5  Registering with $ClientName"
New-Item -ItemType Directory -Force -Path (Split-Path $ConfigPath) | Out-Null
if (-not (Test-Path $ConfigPath)) { '{}' | Set-Content -Path $ConfigPath -Encoding UTF8 }

$env:CONFIG_PATH = $ConfigPath
$env:PYTHON_BIN  = $PythonBin
$env:API_KEY     = $ApiKey

# Patching is done in Python: ConvertTo-Json mangles nested structures on
# PowerShell 5.1 and would silently reshape unrelated settings.
$patch = @'
import json, os, shutil, sys

path = os.environ["CONFIG_PATH"]
try:
    with open(path, encoding="utf-8-sig") as fh:
        config = json.load(fh)
except json.JSONDecodeError as exc:
    sys.exit(f"  error {path} is not valid JSON ({exc}). Fix or remove it, then re-run.")

if not isinstance(config, dict):
    sys.exit(f"  error {path} does not contain a JSON object.")

backup = path + ".bak"
shutil.copy(path, backup)

config.setdefault("mcpServers", {})["garmin"] = {
    "command": os.environ["PYTHON_BIN"],
    "args": ["-m", "garmin_mcp_bridge.server"],
    "env": {"INTERVALS_API_KEY": os.environ["API_KEY"]},
}

with open(path, "w", encoding="utf-8") as fh:
    json.dump(config, fh, indent=2, ensure_ascii=False)

print(f"  ok    servers now configured: {', '.join(config['mcpServers'])}")
print(f"  ok    backup written to {backup}")
'@

$patch | & $PythonBin -
if ($LASTEXITCODE -ne 0) { Stop-Install 'Could not update the config file.' }

Write-Host ''
Write-Head 'Done.'
Write-Host "  Quit $ClientName completely (right-click the tray icon > Quit) and start it again."
Write-Host '  Then ask it: "Call check_connection."'
Write-Host ''
Write-Host '  Your API key is stored in plain text in:'
Write-Host "    $ConfigPath"
Write-Host "  See the README section 'Keeping the key out of the config' for an alternative."
