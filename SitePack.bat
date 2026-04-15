@echo off
REM ========================================================================
REM  SitePack — ダブルクリックで起動する Windows ランチャー
REM ------------------------------------------------------------------------
REM  動作概要
REM    1. このバッチと同じフォルダにある .venv\ がある場合はそれを優先
REM    2. なければシステムの pythonw / python を使用
REM    3. コンソールウィンドウを出さずに GUI のみ表示する (pythonw)
REM ========================================================================

setlocal EnableDelayedExpansion

REM --- このバッチが置かれたフォルダへ移動 ---
cd /d "%~dp0"

REM --- Python ランチャを決定 ---
set "PY="
if exist ".venv\Scripts\pythonw.exe" (
    set "PY=.venv\Scripts\pythonw.exe"
) else if exist ".venv\Scripts\python.exe" (
    set "PY=.venv\Scripts\python.exe"
) else (
    where pythonw >nul 2>nul && set "PY=pythonw"
    if not defined PY (
        where python >nul 2>nul && set "PY=python"
    )
)

if not defined PY (
    echo Python が見つかりませんでした。
    echo https://www.python.org/ から Python 3.11 以上をインストールしてください。
    pause
    exit /b 1
)

REM --- src を PYTHONPATH に追加して起動 ---
set "PYTHONPATH=%~dp0src;%PYTHONPATH%"
start "" "%PY%" -m sitepack
endlocal
