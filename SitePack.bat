@echo off
REM ========================================================================
REM  SitePack - Windows double-click launcher
REM    1. Prefer local .venv\Scripts\pythonw.exe if present
REM    2. Otherwise use system pythonw / python
REM    3. Launches GUI without a console window
REM ========================================================================

setlocal EnableDelayedExpansion
cd /d "%~dp0"

set "PY="
if exist ".venv\Scripts\pythonw.exe" (
    set "PY=.venv\Scripts\pythonw.exe"
) else if exist ".venv\Scripts\python.exe" (
    set "PY=.venv\Scripts\python.exe"
) else (
    where pythonw >/dev/null 2>/dev/null && set "PY=pythonw"
    if not defined PY (
        where python >/dev/null 2>/dev/null && set "PY=python"
    )
)

if not defined PY (
    echo Python が見つかりませんでした。
    echo https://www.python.org/ から Python 3.11 以上をインストールしてください。
    pause
    exit /b 1
)

set "PYTHONPATH=%~dp0src;%PYTHONPATH%"
start "" "%PY%" -m sitepack
endlocal
