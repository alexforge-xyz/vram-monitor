@echo off
rem GUI as administrator — needed to kill other/system processes
powershell -NoProfile -Command "Start-Process pythonw -Verb RunAs -ArgumentList '\"%~dp0vram_gui.py\"'"
