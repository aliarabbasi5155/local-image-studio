@echo off
setlocal
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\windows.ps1" start
set "lis_exit=%errorlevel%"
echo.
echo Press any key to close this window. The studio keeps running in Docker Desktop.
pause >nul
exit /b %lis_exit%
