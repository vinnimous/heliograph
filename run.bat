@echo off
rem heliograph launcher for Windows. See run.py for details.
setlocal
set "HERE=%~dp0"
if defined VIRTUAL_ENV if exist "%VIRTUAL_ENV%\Scripts\python.exe" (
    "%VIRTUAL_ENV%\Scripts\python.exe" "%HERE%run.py" %*
    exit /b
)
where py >nul 2>nul && (
    py -3 "%HERE%run.py" %*
    exit /b
)
where python >nul 2>nul && (
    python "%HERE%run.py" %*
    exit /b
)
echo [heliograph] ERROR: Python 3 not found. Install it from https://www.python.org/downloads/ 1>&2
exit /b 1
