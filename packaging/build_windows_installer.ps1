param(
    [string]$AppVersion = "",
    [string]$IsccPath = "",
    [switch]$Clean
)

$ErrorActionPreference = "Stop"
$ScriptRoot = if ($PSScriptRoot) { $PSScriptRoot } else { Split-Path -Parent $MyInvocation.MyCommand.Path }
$ProjectRoot = Split-Path -Parent $ScriptRoot
$DistParent = Join-Path $ProjectRoot "dist"
$InstallerScript = Join-Path $ScriptRoot "installer_inno.iss"
$OutputDir = Join-Path $ProjectRoot "installer_output"
$OutputBaseName = "sj-generator-setup-cn-dev"
$FinalInstallerName = [string]::new([char[]](
    0x601D,
    0x653F,
    0x667A,
    0x80FD,
    0x4E91,
    0x67A2
)) + ".exe"

function Invoke-NativeOrThrow {
    param(
        [Parameter(Mandatory = $true)]
        [string]$FilePath,
        [string[]]$ArgumentList = @()
    )

    & $FilePath @ArgumentList
    if ($LASTEXITCODE -ne 0) {
        throw "Command failed with exit code ${LASTEXITCODE}: $FilePath $($ArgumentList -join ' ')"
    }
}

function Resolve-ExpectedInstallerPath {
    param(
        [string]$Directory,
        [string]$BaseName
    )

    return Join-Path $Directory ($BaseName + ".exe")
}

function Resolve-IsccPath {
    param([string]$Candidate)

    if ($Candidate -and (Test-Path $Candidate)) {
        return $Candidate
    }

    $common = @(
        "C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
        "C:\Program Files\Inno Setup 6\ISCC.exe"
    )
    foreach ($path in $common) {
        if (Test-Path $path) {
            return $path
        }
    }

    $cmd = Get-Command ISCC.exe -ErrorAction SilentlyContinue
    if ($cmd) {
        return $cmd.Source
    }
    return ""
}

Push-Location $ProjectRoot
try {
    $distExe = Get-ChildItem -Path $DistParent -Filter *.exe -Recurse -File -ErrorAction SilentlyContinue |
        Select-Object -First 1 -ExpandProperty FullName
    if (-not $distExe) {
        throw "Missing dist output. Build the PyInstaller onedir package first."
    }

    if ($Clean) {
        Remove-Item -Recurse -Force $OutputDir -ErrorAction SilentlyContinue
    }
    New-Item -ItemType Directory -Force -Path $OutputDir | Out-Null

    $resolvedIscc = Resolve-IsccPath -Candidate $IsccPath
    if (-not $resolvedIscc) {
        throw "ISCC.exe was not found. Install Inno Setup 6 first, or pass -IsccPath."
    }

    if ($AppVersion) {
        $env:SJ_GENERATOR_APP_VERSION = $AppVersion
        $OutputBaseName = "sj-generator-setup-cn-$AppVersion"
    }

    $expectedInstallerPath = Resolve-ExpectedInstallerPath -Directory $OutputDir -BaseName $OutputBaseName
    $finalInstallerPath = Join-Path $OutputDir $FinalInstallerName
    Remove-Item $expectedInstallerPath -Force -ErrorAction SilentlyContinue
    Remove-Item $finalInstallerPath -Force -ErrorAction SilentlyContinue

    Invoke-NativeOrThrow -FilePath $resolvedIscc -ArgumentList @(("/O" + $OutputDir), ("/F" + $OutputBaseName), $InstallerScript)

    $actualInstallerPath = ""
    if (Test-Path $expectedInstallerPath) {
        $actualInstallerPath = $expectedInstallerPath
    }
    else {
        $fallbackInstaller = Get-ChildItem -Path $OutputDir -Filter *.exe -File -ErrorAction SilentlyContinue |
            Sort-Object LastWriteTime -Descending |
            Select-Object -First 1
        if ($fallbackInstaller) {
            if ($fallbackInstaller.FullName -ne $expectedInstallerPath) {
                Move-Item -Force $fallbackInstaller.FullName $expectedInstallerPath
            }
            $actualInstallerPath = $expectedInstallerPath
        }
    }

    if (-not $actualInstallerPath) {
        throw "Installer build finished but no .exe was found in $OutputDir."
    }

    if ($actualInstallerPath -ne $finalInstallerPath) {
        Move-Item -Force $actualInstallerPath $finalInstallerPath
    }

    Write-Host "Installer created: $finalInstallerPath"
}
finally {
    Pop-Location
}
