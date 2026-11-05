@echo off
REM Double-click this file on Windows to open the Deal Tracker.
cd /d "%~dp0"
where py >nul 2>nul && (set PY=py) || (set PY=python)
%PY% --version >nul 2>nul || (echo Python is not installed. Get it from https://www.python.org/downloads/ and tick "Add python.exe to PATH". & pause & exit /b 1)
if not exist config.json %PY% dealfinder.py setup
%PY% dealfinder.py dashboard
pause
