@echo off
rem Run as administrator — needed to kill other/system processes
powershell -NoProfile -Command "Start-Process cmd -Verb RunAs -ArgumentList '/k','\"%~dp0run.bat\"'"
