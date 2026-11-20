@echo off
cd /d "%~dp0\.."
.venv\Scripts\python.exe scripts\restore_windows.py
pause
