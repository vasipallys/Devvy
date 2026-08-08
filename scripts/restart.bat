@echo off
setlocal EnableDelayedExpansion
title Devvy - restart

REM ---------------------------------------------------------------------------------------
REM  Stop whatever is already serving Devvy, then start the backend and the frontend.
REM
REM  Processes are found by the PORT THEY ARE LISTENING ON, never by image name. Killing
REM  every python.exe or node.exe would take down unrelated work that has nothing to do with
REM  this project -- an editor's language server, another dev server, a long-running script.
REM  The two ports below are the only thing this script claims ownership of.
REM
REM  Stopping first is not optional: Vite is configured with strictPort, so a stale process on
REM  5173 makes the new one exit rather than quietly move to another port.
REM ---------------------------------------------------------------------------------------

set "BACKEND_PORT=8765"
set "FRONTEND_PORT=5173"

pushd "%~dp0.." || (echo Could not find the repository root. & exit /b 1)

echo.
echo   Devvy
echo   ------------------------------------------------------------
echo   Repository : %CD%
echo.

call :stop_port %BACKEND_PORT%  "backend"
call :stop_port %FRONTEND_PORT% "frontend"

REM -- Interpreter: match start-backend.ps1, which accepts either layout --------------------
if exist ".venv\Scripts\python.exe" (
    set "PYTHON=%CD%\.venv\Scripts\python.exe"
) else if exist "venv\Scripts\python.exe" (
    set "PYTHON=%CD%\venv\Scripts\python.exe"
) else (
    echo   [x] No virtual environment found.
    echo       Run scripts\setup.ps1 first.
    popd & exit /b 1
)

if not exist "frontend\node_modules" (
    echo   [x] frontend\node_modules is missing.
    echo       Run scripts\setup.ps1 first.
    popd & exit /b 1
)

REM -- Start both, each in its own window so their logs stay readable -----------------------
REM `start /d <dir>` sets each window's working directory. Doing it that way, rather than
REM embedding `cd /d "..." &&` inside the command, avoids nesting a second pair of quotes
REM inside the quoted command: cmd strips the outer pair, the inner pair then breaks the
REM parse, and the window closes immediately with an error nobody is around to read. That is
REM exactly how the frontend silently failed to start while the backend beside it worked.
echo   Starting backend  ... http://127.0.0.1:%BACKEND_PORT%
start "Devvy backend" /d "%CD%" cmd /k ""%PYTHON%" -m backend"

echo   Starting frontend ... http://localhost:%FRONTEND_PORT%
start "Devvy frontend" /d "%CD%\frontend" cmd /k "npm run dev"

REM -- Report readiness rather than assuming it --------------------------------------------
REM  The backend loads its model lazily, so listening is the honest signal that it is up;
REM  a slow first request afterwards is the model loading, not a failed start.
call :wait_port %BACKEND_PORT%  "backend"  40
call :wait_port %FRONTEND_PORT% "frontend" 40

echo.
echo   Open http://localhost:%FRONTEND_PORT%
echo   Close the two windows named "Devvy backend" and "Devvy frontend" to stop, or run
echo   this script again to restart cleanly.
echo.

popd
endlocal
exit /b 0


REM =======================================================================================
REM  :stop_port <port> <label>
REM  Terminate whatever is LISTENING on the port, and nothing else.
REM =======================================================================================
:stop_port
set "PORT=%~1"
set "LABEL=%~2"
set "FOUND="

REM No `-p TCP` filter: that shows IPv4 only, and Node binds IPv6 -- Vite listens on
REM [::1]:5173, which the filtered view hides completely. The script then reported "no
REM frontend running", started a second one, and Vite exited with "port already in use"
REM against a process the script could not see. Plain `netstat -ano` lists both families.
REM
REM The trailing space in the pattern matters: ":5173 " must not also match ":51730".
REM Only LISTENING rows are matched, so a client connection to the port is never killed.
REM Column 5 of a TCP row is the owning PID.
for /f "tokens=5" %%P in ('netstat -ano 2^>nul ^| findstr /r /c:"TCP .*:%PORT% .*LISTENING"') do (
    if not "%%P"=="0" (
        set "FOUND=1"
        for /f "tokens=1 delims=," %%N in ('tasklist /fi "PID eq %%P" /fo csv /nh 2^>nul') do (
            echo   Stopping %LABEL% on port %PORT% - %%~N ^(PID %%P^)
        )
        REM /T also ends children, which is what actually stops npm: `npm run dev` spawns
        REM Vite as a child, and killing only the launcher leaves the port held.
        taskkill /PID %%P /T /F >nul 2>&1
    )
)
if not defined FOUND echo   No %LABEL% running on port %PORT%.
exit /b 0


REM =======================================================================================
REM  :wait_port <port> <label> <attempts>
REM  Poll until the port is listening. Reports the truth either way -- a script that says
REM  "started" without checking is worse than one that says nothing.
REM =======================================================================================
:wait_port
set "PORT=%~1"
set "LABEL=%~2"
set /a "TRIES=%~3"

for /l %%I in (1,1,%TRIES%) do (
    REM Same IPv4/IPv6 reason as :stop_port -- a filtered view reports a healthy service down.
    netstat -ano 2>nul | findstr /r /c:"TCP .*:%PORT% .*LISTENING" >nul 2>&1
    if !errorlevel! equ 0 (
        echo   [ok] %LABEL% is listening on %PORT%.
        exit /b 0
    )
    REM `timeout` refuses to run when stdin is redirected -- "Input redirection is not
    REM supported" -- which is exactly what happens when this script is launched from another
    REM tool rather than double-clicked. It then returns instantly, so the whole loop burned
    REM through in milliseconds and reported both services down while they were starting fine.
    REM `ping` waits the same second and does not care about stdin.
    ping -n 2 127.0.0.1 >nul 2>&1
)

echo   [warn] %LABEL% is not listening on %PORT% yet. Check its window for the error.
exit /b 0
