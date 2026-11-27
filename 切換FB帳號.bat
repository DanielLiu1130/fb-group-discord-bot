@echo off
chcp 65001 >nul
title FB 切換帳號登入
cd /d "%~dp0"
echo ===================================================
echo   正在清除舊 FB 帳號並開啟全新登入瀏覽器...
echo ===================================================
python save_fb_session.py
pause
