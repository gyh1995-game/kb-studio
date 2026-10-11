@echo off
setlocal
title KB Studio
cd /d "%~dp0"

rem ---------------------------------------------------------------
rem  Pick a Python that ACTUALLY has the deps.
rem  (Old version hard-picked the .workbuddy Python, which has no
rem   bs4/requests -> every digest failed with "No module named bs4")
rem  Echo lines are ASCII on purpose: cmd.exe is not UTF-8.
rem ---------------------------------------------------------------
set "PY="
call :try "%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
call :try "%USERPROFILE%\AppData\Local\Programs\Python\Python312\python.exe"
call :try "%USERPROFILE%\.workbuddy\binaries\python\versions\3.13.12\python.exe"
if not defined PY (
  where python >nul 2>nul
  if not errorlevel 1 call :try python
)

if not defined PY (
  echo.
  echo   [ERROR] No usable Python found.
  echo   Install Python 3, then run:  python -m pip install requests beautifulsoup4
  echo.
  pause
  exit /b 1
)

echo.
echo   ============================================
echo      KB Studio   (personal knowledge base)
echo   ============================================
echo.
echo   Python : %PY%
echo   URL    : http://127.0.0.1:8777/
echo   Keep this window open; close it to stop the server.
echo.
echo   Already running? Check with:  netstat -ano ^| findstr :8777
echo.

"%PY%" server.py --port 8777

echo.
echo   Server stopped.
pause
exit /b 0

:try
if defined PY exit /b 0
%1 -c "import bs4, requests" >nul 2>nul && set "PY=%~1"
exit /b 0
