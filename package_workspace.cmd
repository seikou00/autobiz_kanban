@echo off
setlocal

set "SCRIPT_DIR=%~dp0"
set "OUTPUT=%~1"
if "%OUTPUT%"=="" set "OUTPUT=autobiz_kanban_workspace.zip"

pushd "%SCRIPT_DIR%" >nul

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$ErrorActionPreference = 'Stop';" ^
  "$items = @();" ^
  "if (Test-Path '.mcp.json') { $items += '.mcp.json' }" ^
  "if (Test-Path 'board_core') { $items += 'board_core' }" ^
  "if (Test-Path 'hooks') { $items += 'hooks' }" ^
  "if (Test-Path 'skills') { $items += 'skills' }" ^
  "$items += Get-ChildItem -Path . -Filter '*.py' -File | Sort-Object Name | ForEach-Object { $_.Name };" ^
  "$items += Get-ChildItem -Path . -Filter '*.json' -File | Where-Object { $_.Name -ne '.mcp.json' } | Sort-Object Name | ForEach-Object { $_.Name };" ^
  "if ($items.Count -eq 0) { throw 'No matching files found to package.' }" ^
  "if (Test-Path '%OUTPUT%') { Remove-Item '%OUTPUT%' -Force }" ^
  "Compress-Archive -Path $items -DestinationPath '%OUTPUT%' -Force;" ^
  "Write-Host ('Created ' + (Resolve-Path '%OUTPUT%'))"

set "EXIT_CODE=%ERRORLEVEL%"
popd >nul
exit /b %EXIT_CODE%
