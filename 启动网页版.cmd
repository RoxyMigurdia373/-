@echo off
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" -m vectopo_check.cli web
) else (
  python -m vectopo_check.cli web
)
pause
