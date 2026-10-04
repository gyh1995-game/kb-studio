@echo off
chcp 65001 >nul
REM 新机器上第一次装依赖用这个。加 --asr 连中文转写主引擎一起装。
cd /d "%~dp0"
if "%1"=="" (
  python tools\install.py
) else (
  python tools\install.py %*
)
echo.
pause
