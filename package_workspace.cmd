@echo off
setlocal

where py >nul 2>nul
if not errorlevel 1 (
  py -3 "%~dp0scripts\package_workspace.py" %*
) else (
  python "%~dp0scripts\package_workspace.py" %*
)
exit /b %ERRORLEVEL%
