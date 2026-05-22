@echo off
setlocal
set "SCRIPT_DIR=%~dp0"
py -3 "%SCRIPT_DIR%update_checkpoint.py" %*
set "PY_EXIT=%ERRORLEVEL%"
if "%PY_EXIT%"=="103" goto fallback
if "%PY_EXIT%"=="9009" goto fallback
exit /b %PY_EXIT%
:fallback
python "%SCRIPT_DIR%update_checkpoint.py" %*
exit /b %ERRORLEVEL%
