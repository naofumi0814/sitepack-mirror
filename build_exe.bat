@echo off
REM ========================================================================
REM  SitePack — Windows 用「単一 .exe」ビルドスクリプト
REM ------------------------------------------------------------------------
REM  PyInstaller を使って dist\SitePack.exe を作成します。
REM  生成された .exe はダブルクリックだけで起動でき、Python のインス
REM  トールなしに配布可能です。
REM ========================================================================

setlocal
cd /d "%~dp0"

if exist ".venv\Scripts\python.exe" (
    set "PY=.venv\Scripts\python.exe"
) else (
    set "PY=python"
)

echo === [1/3] 依存パッケージを確認 ===
"%PY%" -m pip install --upgrade pip
"%PY%" -m pip install -e ".[dev]"

echo === [2/3] 古い build/dist を削除 ===
if exist build rmdir /s /q build
if exist dist  rmdir /s /q dist

echo === [3/3] PyInstaller でビルド ===
"%PY%" -m PyInstaller --noconfirm sitepack.spec

if exist "dist\SitePack.exe" (
    echo.
    echo ========================================
    echo  ビルド成功: dist\SitePack.exe
    echo  ダブルクリックで起動できます。
    echo ========================================
) else (
    echo.
    echo ビルドに失敗しました。エラーログを確認してください。
    pause
    exit /b 1
)

endlocal
pause
