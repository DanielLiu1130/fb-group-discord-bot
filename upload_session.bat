@echo off
chcp 65001 >nul
title 上傳 FB 憑證到雲端 (Cloudflare KV)
cd /d "%~dp0"
echo ===================================================
echo   正在驗證並加密上傳 FB 憑證到 Cloudflare KV...
echo ===================================================
python upload_session.py %*
pause
