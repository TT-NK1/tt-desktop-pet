@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\pythonw.exe" (
  echo Install Python dependencies first. See README.md.
  pause
  exit /b 1
)
start "" ".venv\Scripts\pythonw.exe" "%~dp0desktop_pet_search.py"
