@echo off
rem Optional: enables the "Xbox-only game" profile. Shows the ViGEmBus driver installer if needed.
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (echo Run run.bat once first. & pause & exit /b 1)
.venv\Scripts\python.exe -m pip install -r requirements-optional.txt
pause
