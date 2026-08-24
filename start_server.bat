@echo off
REM Windows counterpart of start_server.sh. Same job: find an interpreter
REM that has flask and qiskit, launch the server detached, write what
REM happened into launch.log, and return immediately.
REM
REM UNTESTED. I have no Windows machine in this sandbox, so treat this as a
REM starting point rather than something known to work. The Mac path is the
REM tested one.
setlocal
cd /d "%~dp0"

set PORT=5055
set SERVER=eigenstate_server.py
set LOG=server.log
set LAUNCHLOG=launch.log

echo --- start_server.bat --- >> "%LAUNCHLOG%"

REM already listening? then there is nothing to do
netstat -ano | findstr /c:"LISTENING" | findstr /c:":%PORT%" >nul 2>&1
if not errorlevel 1 (
    echo port %PORT% already held >> "%LAUNCHLOG%"
    exit /b 0
)

set "PY="
if exist "%USERPROFILE%\eigenstate312\Scripts\python.exe" set "PY=%USERPROFILE%\eigenstate312\Scripts\python.exe"
if not defined PY if exist "venv\Scripts\python.exe" set "PY=venv\Scripts\python.exe"
if not defined PY for /f "delims=" %%P in ('where python 2^>nul') do if not defined PY set "PY=%%P"

if not defined PY (
    echo FAILED: no python found. Run setup.bat. >> "%LAUNCHLOG%"
    exit /b 1
)

REM verify it has what the server needs, rather than launching something
REM that will die three seconds later with an ImportError nobody reads
"%PY%" -c "import flask, qiskit" >nul 2>&1
if errorlevel 1 (
    echo FAILED: %PY% has no flask+qiskit. Run setup.bat. >> "%LAUNCHLOG%"
    exit /b 1
)

REM credentials, if present. KEY=VALUE per line, no export keyword.
if exist moth.env (
    for /f "usebackq eol=# tokens=1,* delims==" %%A in ("moth.env") do set "%%A=%%B"
)

echo starting %SERVER% with %PY% >> "%LAUNCHLOG%"
start "eigenstate-server" /min "%PY%" "%SERVER%"
exit /b 0