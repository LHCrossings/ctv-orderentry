@echo off
:: Control Room - Launch Script
:: Double-click to start the server and open the browser.
:: Safe to run again if already running — won't start a second instance.

set PORT=8000
set SCRIPT_DIR=%~dp0

:: Check if port is already in use
netstat -an | find ":%PORT% " | find "LISTENING" >nul 2>&1
if %errorlevel%==0 (
    echo Server already running on port %PORT%. Opening browser...
    start "" "http://localhost:%PORT%/"
    exit /b
)

:: Start the server in a minimized background window
echo Starting CTV Order Entry server...
start /min "Control Room Server" cmd /c "cd /d "%SCRIPT_DIR%" && .venv\Scripts\python.exe web_main.py"

:: Wait for server to be ready (up to 10 seconds)
set /a tries=0
:wait_loop
timeout /t 1 /nobreak >nul
netstat -an | find ":%PORT% " | find "LISTENING" >nul 2>&1
if %errorlevel%==0 goto ready
set /a tries+=1
if %tries% lss 10 goto wait_loop
echo ERROR: Server failed to start after 10 seconds.
pause
exit /b 1

:ready
echo Server ready. Opening browser...
start "" "http://localhost:%PORT%/"
