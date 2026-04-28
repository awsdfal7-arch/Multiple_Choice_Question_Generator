param(
    [string]$Python = "python",
    [switch]$Clean
)

$ErrorActionPreference = "Stop"

$ScriptRoot = if ($PSScriptRoot) { $PSScriptRoot } else { Split-Path -Parent $MyInvocation.MyCommand.Path }
$ProjectRoot = Split-Path -Parent $ScriptRoot
$SpecFile = Join-Path $ScriptRoot "pyinstaller_onedir.spec"
$PackagingRequirements = Join-Path $ScriptRoot "requirements-packaging.txt"

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

Push-Location $ProjectRoot
try {
    if ($Clean) {
        Remove-Item -Recurse -Force (Join-Path $ProjectRoot "build") -ErrorAction SilentlyContinue
        Remove-Item -Recurse -Force (Join-Path $ProjectRoot "dist") -ErrorAction SilentlyContinue
    }

    Invoke-NativeOrThrow -FilePath $Python -ArgumentList @("-m", "pip", "install", "-r", (Join-Path $ProjectRoot "requirements.txt"))
    Invoke-NativeOrThrow -FilePath $Python -ArgumentList @("-m", "pip", "install", "-r", $PackagingRequirements)
    Invoke-NativeOrThrow -FilePath $Python -ArgumentList @("-m", "PyInstaller", "--noconfirm", $SpecFile)
}
finally {
    Pop-Location
}
