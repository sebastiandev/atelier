@echo off
REM Thin shim so `atelier <command>` works from the repo root on Windows.
REM Implementation is scripts\atelier.py — standard library only, so the
REM system Python runs it without the backend venv existing yet.
setlocal
cd /d "%~dp0"
where python >nul 2>&1 && (python scripts\atelier.py %* & exit /b %errorlevel%)
where py >nul 2>&1 && (py -3 scripts\atelier.py %* & exit /b %errorlevel%)
echo atelier: needs Python 3.11+ on PATH (looked for python, py) 1>&2
exit /b 1
