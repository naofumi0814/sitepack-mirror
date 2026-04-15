@echo off
REM ========================================================================
REM  SitePack - Windows single-exe build script
REM  Creates dist\SitePack.exe via PyInstaller.
REM ========================================================================

setlocal
cd /d "%~dp0"

if exist ".venv\Scripts\python.exe" (
    set "PY=.venv\Scripts\python.exe"
) else (
    set "PY=python"
)

echo === [1/3] 依存パッケージを確認しています ===
"%PY%" -m pip install --upgrade pip
if errorlevel 1 goto :fail
"%PY%" -m pip install -e ".[dev]"
if errorlevel 1 goto :fail

echo === [2/3] 古い build/dist を削除 ===
if exist build rmdir /s /q build
if exist dist  rmdir /s /q dist

echo === [3/3] PyInstaller でビルド ===
"%PY%" -m PyInstaller --noconfirm sitepack.spec
if errorlevel 1 goto :fail

if exist "dist\SitePack.exe" (
    echo.
    echo ========================================
    echo  ビルド成功: dist\SitePack.exe
    echo  ダブルクリックで起動できます。
    echo ========================================
    pause
    endlocal
    exit /b 0
)

:fail
echo.
echo ビルドに失敗しました。上のエラーログを確認してください。
pause
endlocal
exit /b 1
