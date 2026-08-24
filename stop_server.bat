@echo off
REM Windows counterpart of stop_server.sh. Kills whatever is listening on
REM the port, because a stale process holding 5055 is the single most
REM confusing failure mode here.
REM
REM UNTESTED, same caveat as start_server.bat.
setlocal
set PORT=5055

for /f "tokens=5" %%P in ('netstat -ano ^| findstr /c:"LISTENING" ^| findstr /c:":%PORT%"') do (
    taskkill /f /pid %%P >nul 2>&1
    echo stopped %%P
)
echo port %PORT% is clear
exit /b 0