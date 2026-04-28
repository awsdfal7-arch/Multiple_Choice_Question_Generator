@echo off
setlocal

cd /d "%~dp0"

set "APP_VERSION=%~1"
set "ISCC_PATH="
set "PACKAGING_DIR=%~dp0packaging"
set "OUTPUT_DIR=%~dp0installer_output"
set "ERROR_MESSAGE="

if exist "C:\Install\Inno Setup 6\ISCC.exe" set "ISCC_PATH=C:\Install\Inno Setup 6\ISCC.exe"
if not defined ISCC_PATH if exist "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" set "ISCC_PATH=C:\Program Files (x86)\Inno Setup 6\ISCC.exe"
if not defined ISCC_PATH if exist "C:\Program Files\Inno Setup 6\ISCC.exe" set "ISCC_PATH=C:\Program Files\Inno Setup 6\ISCC.exe"

where python >nul 2>nul
if errorlevel 1 (
    echo [ERROR] python was not found in PATH.
    set "ERROR_MESSAGE=python was not found in PATH."
    goto :fail
)

if not exist "%PACKAGING_DIR%\build_windows_onedir.ps1" (
    echo [ERROR] Missing packaging\build_windows_onedir.ps1
    set "ERROR_MESSAGE=Missing packaging\build_windows_onedir.ps1"
    goto :fail
)

if not exist "%PACKAGING_DIR%\build_windows_installer.ps1" (
    echo [ERROR] Missing packaging\build_windows_installer.ps1
    set "ERROR_MESSAGE=Missing packaging\build_windows_installer.ps1"
    goto :fail
)

echo [1/2] Building onedir package...
powershell -ExecutionPolicy Bypass -File "%PACKAGING_DIR%\build_windows_onedir.ps1" -Clean
if errorlevel 1 (
    echo [ERROR] Failed to build onedir package.
    set "ERROR_MESSAGE=Failed to build onedir package."
    goto :fail
)

echo [2/2] Building installer package...
if defined APP_VERSION (
    if defined ISCC_PATH (
        powershell -ExecutionPolicy Bypass -File "%PACKAGING_DIR%\build_windows_installer.ps1" -Clean -AppVersion "%APP_VERSION%" -IsccPath "%ISCC_PATH%"
    ) else (
        powershell -ExecutionPolicy Bypass -File "%PACKAGING_DIR%\build_windows_installer.ps1" -Clean -AppVersion "%APP_VERSION%"
    )
) else (
    if defined ISCC_PATH (
        powershell -ExecutionPolicy Bypass -File "%PACKAGING_DIR%\build_windows_installer.ps1" -Clean -IsccPath "%ISCC_PATH%"
    ) else (
        powershell -ExecutionPolicy Bypass -File "%PACKAGING_DIR%\build_windows_installer.ps1" -Clean
    )
)
if errorlevel 1 (
    echo [ERROR] Failed to build installer package.
    set "ERROR_MESSAGE=Failed to build installer package."
    goto :fail
)

if exist "%~dp0build" rmdir /s /q "%~dp0build"
if exist "%~dp0dist" rmdir /s /q "%~dp0dist"

echo [DONE] Installer output:
dir /b "%OUTPUT_DIR%\*.exe"

endlocal
exit /b 0

:fail
echo.
echo [FAILED] Build stopped.
if defined ERROR_MESSAGE echo [INFO] %ERROR_MESSAGE%
echo.
echo Press any key to close this window...
pause >nul
endlocal
exit /b 1
