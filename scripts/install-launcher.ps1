# Install a thin Windows launcher that runs scripts/dev.sh via bash.
# Generates Atelier.bat under %LOCALAPPDATA%\Atelier and creates Start Menu
# + Desktop shortcuts pointing at it.
#
# Run from PowerShell:
#     powershell -ExecutionPolicy Bypass -File scripts\install-launcher.ps1

$ErrorActionPreference = 'Stop'

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
$RepoRoot  = (Resolve-Path (Join-Path $ScriptDir '..')).Path
$Template  = Join-Path $ScriptDir 'launchers\Atelier.bat.tmpl'
$IconSrc   = Join-Path $ScriptDir 'launchers\icons\Atelier.ico'

if (-not (Test-Path $Template)) {
    throw "Template not found: $Template"
}

$InstallDir = Join-Path $env:LOCALAPPDATA 'Atelier'
$BatPath    = Join-Path $InstallDir 'Atelier.bat'
$IconPath   = Join-Path $InstallDir 'Atelier.ico'
New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null

# Substitute __REPO_ROOT__ -> resolved path (literal replace, no regex).
$Content = (Get-Content -Raw -LiteralPath $Template).Replace('__REPO_ROOT__', $RepoRoot)
Set-Content -LiteralPath $BatPath -Value $Content -Encoding ASCII

if (Test-Path $IconSrc) {
    Copy-Item -LiteralPath $IconSrc -Destination $IconPath -Force
} else {
    Write-Warning "Icon not found at $IconSrc; shortcuts will use the default .bat icon."
    $IconPath = $null
}

# Create Start Menu + Desktop shortcuts pointing at the .bat.
$WScriptShell = New-Object -ComObject WScript.Shell

$StartMenuDir = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs\Atelier'
New-Item -ItemType Directory -Force -Path $StartMenuDir | Out-Null

foreach ($Target in @(
    (Join-Path $StartMenuDir 'Atelier.lnk'),
    (Join-Path ([Environment]::GetFolderPath('Desktop')) 'Atelier.lnk')
)) {
    $Shortcut = $WScriptShell.CreateShortcut($Target)
    $Shortcut.TargetPath       = $BatPath
    $Shortcut.WorkingDirectory = $RepoRoot
    $Shortcut.Description      = 'Atelier dev launcher'
    if ($IconPath) {
        $Shortcut.IconLocation = "$IconPath,0"
    }
    $Shortcut.Save()
    Write-Host "Installed shortcut: $Target"
}

Write-Host ""
Write-Host "Launcher script: $BatPath"
Write-Host "Requires Git for Windows or WSL on PATH (provides 'bash')."
