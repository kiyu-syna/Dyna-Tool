@echo off
setlocal
title Dyna Payment Server

cd /d "%~dp0"
set "PYTHON=%~dp0..\.venv\Scripts\python.exe"

if not exist "%PYTHON%" (
  echo.
  echo [LOI] Khong tim thay Python tai:
  echo %PYTHON%
  echo.
  echo Hay tao moi truong .venv trong thu muc C:\Dyna Tool truoc.
  pause
  exit /b 1
)

echo.
echo ========================================
echo   Dyna Payment Server
echo   http://localhost:8000
echo   Nhan Ctrl+C de dung server
echo ========================================
echo.

"%PYTHON%" -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
set "EXIT_CODE=%ERRORLEVEL%"

echo.
echo Payment Server da dung. Ma thoat: %EXIT_CODE%
pause
exit /b %EXIT_CODE%
