@echo off
rem Launch GUI without a console window
where pyw >nul 2>nul
if %errorlevel%==0 (
    start "" pyw -3 "%~dp0vram_gui.py"
) else (
    start "" pythonw "%~dp0vram_gui.py"
)
