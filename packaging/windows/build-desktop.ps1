param(
    [string]$Version = '0.1.0',
    [string]$RawTherapeePayloadDir = '',
    [string]$OutputDir = ''
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$SpecPath = Join-Path $PSScriptRoot 'imagic.spec'
$InstallerScript = Join-Path $PSScriptRoot 'imagic-installer.iss'
$BrandingScript = Join-Path $PSScriptRoot 'generate_branding_assets.py'
$BrandingDir = Join-Path $PSScriptRoot 'branding'
$BuildRoot = Join-Path $RepoRoot 'build\windows'
$PyInstallerWork = Join-Path $BuildRoot 'pyinstaller\work'
$PyInstallerDist = Join-Path $BuildRoot 'pyinstaller\dist'
$VenvPython = Join-Path $RepoRoot '.venv\Scripts\python.exe'
$PythonExe = if (Test-Path $VenvPython) { $VenvPython } else { 'python' }

if (-not $OutputDir) {
    $OutputDir = Join-Path $RepoRoot 'dist\windows'
}

function Resolve-Iscc {
    $command = Get-Command 'ISCC.exe' -ErrorAction SilentlyContinue
    if ($command) {
        return $command.Source
    }

    $candidates = @(
        'C:\Program Files (x86)\Inno Setup 6\ISCC.exe',
        'C:\Program Files\Inno Setup 6\ISCC.exe'
    )

    foreach ($candidate in $candidates) {
        if (Test-Path $candidate) {
            return $candidate
        }
    }

    throw 'Inno Setup compiler (ISCC.exe) was not found. Install Inno Setup 6 first.'
}

function Invoke-ExternalCommand {
    param(
        [Parameter(Mandatory = $true)]
        [string]$FilePath,

        [string[]]$Arguments = @(),

        [Parameter(Mandatory = $true)]
        [string]$FailureMessage
    )

    & $FilePath @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "$FailureMessage (exit code $LASTEXITCODE)."
    }
}

Write-Host 'Checking packaging tools...'
Invoke-ExternalCommand -FilePath $PythonExe -Arguments @('-m', 'PyInstaller', '--version') -FailureMessage 'PyInstaller is not available'
$iscc = Resolve-Iscc

Write-Host 'Generating branding assets...'
Invoke-ExternalCommand -FilePath $PythonExe -Arguments @(
    $BrandingScript,
    '--icon-svg', (Join-Path $RepoRoot 'assets\icons\imagic-app-icon.svg'),
    '--output-dir', $BrandingDir
) -FailureMessage 'Branding asset generation failed'

Write-Host 'Cleaning previous Windows build artifacts...'
Remove-Item $BuildRoot -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force -Path $PyInstallerWork | Out-Null
New-Item -ItemType Directory -Force -Path $PyInstallerDist | Out-Null
New-Item -ItemType Directory -Force -Path $OutputDir | Out-Null

Write-Host 'Building standalone imagic desktop app with PyInstaller...'
Invoke-ExternalCommand -FilePath $PythonExe -Arguments @('-m', 'PyInstaller', $SpecPath, '--noconfirm', '--clean', '--distpath', $PyInstallerDist, '--workpath', $PyInstallerWork) -FailureMessage 'PyInstaller build failed'

$sourceDist = Join-Path $PyInstallerDist 'imagic'
if (-not (Test-Path (Join-Path $sourceDist 'imagic.exe'))) {
    throw 'PyInstaller build failed: imagic.exe was not produced.'
}

Write-Host 'Compiling standard installer...'
Invoke-ExternalCommand -FilePath $iscc -Arguments @($InstallerScript, "/DMyAppVersion=$Version", "/DSourceDist=$sourceDist", "/DInstallerOutputDir=$OutputDir", '/DIncludeRawTherapee=0') -FailureMessage 'Standard installer compilation failed'

if ($RawTherapeePayloadDir) {
    $resolvedPayload = (Resolve-Path $RawTherapeePayloadDir).Path
    $payloadCli = Get-ChildItem -Path $resolvedPayload -Filter 'rawtherapee-cli.exe' -Recurse -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $payloadCli) {
        throw 'The provided RawTherapee payload directory does not contain rawtherapee-cli.exe.'
    }

    Write-Host 'Compiling recommended installer with bundled RawTherapee payload...'
    Invoke-ExternalCommand -FilePath $iscc -Arguments @($InstallerScript, "/DMyAppVersion=$Version", "/DSourceDist=$sourceDist", "/DInstallerOutputDir=$OutputDir", '/DIncludeRawTherapee=1', "/DRawTherapeePayloadDir=$resolvedPayload") -FailureMessage 'Bundled RawTherapee installer compilation failed'
}
else {
    Write-Host 'No RawTherapee payload directory was supplied. Only the standard installer was built.'
}

Write-Host ''
Write-Host 'Windows packaging complete.'
Write-Host "PyInstaller app folder: $sourceDist"
Write-Host "Installer output folder: $OutputDir"