@echo off
setlocal
title Dyna Telegram Server

cd /d "%~dp0"
set "PYTHON=%~dp0..\.venv\Scripts\python.exe"

for /f "tokens=5" %%P in ('netstat -ano ^| findstr ":8000" ^| findstr "LISTENING"') do goto already_running

if not exist "%PYTHON%" (
  echo.
  echo [LOI] Khong tim thay Python tai:
  echo %PYTHON%
  echo.
  echo Hay tao moi truong .venv trong thu muc C:\Dyna Tool truoc.
  pause
  exit /b 1
)

goto start_server

:already_running
echo.
echo [CANH BAO] Telegram Server da dang chay tren port 8000.
echo Khong khoi dong them instance de tranh Telegram 409 Conflict.
echo Hay dong instance dang chay truoc khi thu lai.
pause
exit /b 0

:start_server

echo.
echo ========================================
echo   Dyna Telegram Server
echo   http://localhost:8000
echo   Nhan Ctrl+C de dung server
echo ========================================
echo.

"%PYTHON%" -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
set "EXIT_CODE=%ERRORLEVEL%"

echo.
echo Telegram Server da dung. Ma thoat: %EXIT_CODE%
pause
exit /b %EXIT_CODE%
