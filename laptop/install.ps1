# spok1 laptop installer (Windows). Safe to rerun.
#
#   powershell -ExecutionPolicy Bypass -File laptop\install.ps1 [-BrainRepo <url>] [-Machine <name>] [-DryRun]
#
# Bootstraps the prerequisites, then hands over to `spok1 install` (all real logic is there).

param(
    [string]$BrainRepo = "",
    [string]$Machine = "",
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot

function Test-Command($Name) { return [bool](Get-Command $Name -ErrorAction SilentlyContinue) }
function Add-ToPath($Dir) {
    if ((Test-Path $Dir) -and -not (($env:Path -split ";") -contains $Dir)) { $env:Path = "$Dir;$env:Path" }
}

# Never run with an API key: it would bill the API instead of the Max plan.
$keyUser = [Environment]::GetEnvironmentVariable("ANTHROPIC_API_KEY", "User")
$keyMachine = [Environment]::GetEnvironmentVariable("ANTHROPIC_API_KEY", "Machine")
if ($env:ANTHROPIC_API_KEY -or $keyUser -or $keyMachine) {
    Write-Error "ANTHROPIC_API_KEY is set. Remove it (user/system environment variables), open a new terminal, and rerun."
}

if (-not (Test-Command git)) { Write-Error "git is required: https://git-scm.com/download/win (includes Git Bash)" }

# Claude Code CLI (official native installer)
Add-ToPath "$env:USERPROFILE\.local\bin"
if (-not (Test-Command claude)) {
    if ($DryRun) { Write-Host "would: install Claude Code CLI (irm https://claude.ai/install.ps1 | iex)" }
    else {
        Write-Host "Installing Claude Code CLI (official installer)..."
        Invoke-RestMethod https://claude.ai/install.ps1 | Invoke-Expression
        Add-ToPath "$env:USERPROFILE\.local\bin"
        if (-not (Test-Command claude)) { Write-Error "claude still not on PATH. Open a new terminal and rerun." }
    }
}

# uv (official installer)
if (-not (Test-Command uv)) {
    if ($DryRun) { Write-Host "would: install uv (irm https://astral.sh/uv/install.ps1 | iex)" }
    else {
        Write-Host "Installing uv (official installer)..."
        Invoke-RestMethod https://astral.sh/uv/install.ps1 | Invoke-Expression
        Add-ToPath "$env:USERPROFILE\.local\bin"
    }
}

if ($DryRun -and -not (Test-Command uv)) { Write-Host "(dry run stops here: uv not installed yet)"; exit 0 }

Push-Location $Root
try {
    uv sync --locked --quiet
    if ($LASTEXITCODE -ne 0) { Write-Error "uv sync failed" }
} finally { Pop-Location }

$spok1Args = @("install")
if ($BrainRepo) { $spok1Args += @("--brain-repo", $BrainRepo) }
if ($Machine) { $spok1Args += @("--machine", $Machine) }
if ($DryRun) { $spok1Args += "--dry-run" }

& (Join-Path $Root ".venv\Scripts\spok1.exe") @spok1Args
exit $LASTEXITCODE
