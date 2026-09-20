@echo off
:: Dyna Tool - Chay ban EXE Release toi uu RAM, co System Tray va giu nguyen 100% du lieu hien tai
set "DYNA_DATA_DIR=%~dp0"
:: Bo dau gach cheo nguoc o cuoi neu co
if "%DYNA_DATA_DIR:~-1%"=="\" set "DYNA_DATA_DIR=%DYNA_DATA_DIR:~0,-1%"

echo ========================================================
echo   Dyna Tool - Khoi dong ban EXE Release toi uu
echo   Data folder: %DYNA_DATA_DIR%
echo ========================================================

start "" "%~dp0desktop\src-tauri\target\release\dyna.exe"
exit
