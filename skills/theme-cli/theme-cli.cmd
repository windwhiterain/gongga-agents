@echo off
rem theme-cli - launcher for the theme-cli skill.
setlocal
set "PS=pwsh"
where pwsh >nul 2>nul || set "PS=powershell"
"%PS%" -NoProfile -ExecutionPolicy Bypass -File "%~dp0theme-cli.ps1" %*
