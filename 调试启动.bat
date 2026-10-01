@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ================================================
echo  抖音续火花 - 调试模式（带控制台，方便排查问题）
echo ================================================
echo.
"%~dp0runtime\python.exe" "%~dp0app\main.py"
echo.
echo 程序已退出。
pause
