@echo off
chcp 65001 >nul
where py >nul 2>nul
if %errorlevel%==0 (
    py -3 "%~dp0vram_monitor.py" %*
) else (
    python "%~dp0vram_monitor.py" %*
)
