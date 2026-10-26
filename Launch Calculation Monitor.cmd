@echo off
if not exist "%~dp0.venv\Scripts\pythonw.exe" (
  echo Quiet UK Python environment is missing. Restore .venv before launching the monitor.
  pause
  exit /b 1
)
start "" "%~dp0.venv\Scripts\pythonw.exe" "%~dp0scripts\calculation_monitor.py"
