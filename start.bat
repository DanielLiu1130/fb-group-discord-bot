@echo off
chcp 65001 >nul
title 清交二手拍 Discord 機器人
cd /d "%~dp0"
echo ===================================================
echo   正在啟動 FB 二手社團 Discord 機器人...
echo ===================================================
python bot.py
pause
