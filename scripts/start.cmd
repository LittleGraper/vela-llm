@echo off
setlocal

cd /d "%~dp0.."
uv run vl stop
if errorlevel 1 exit /b %errorlevel%

uv run vl start %*
