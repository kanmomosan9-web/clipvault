@echo off
REM 自宅PCの文字起こしワーカー。
REM Tailscale Funnel の公開URLに直接つなぐため、SSHトンネルは不要。
cd /d %~dp0..
:loop
python worker.py
echo.
echo worker stopped. restarting in 15s (Ctrl+C to quit)
timeout /t 15 /nobreak >nul
goto loop
