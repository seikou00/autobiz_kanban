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
  "$destination = [IO.Path]::GetFullPath('%OUTPUT%');" ^
  "$staging = Join-Path ([IO.Path]::GetTempPath()) ('autobiz_kanban_package_' + [guid]::NewGuid().ToString('N'));" ^
  "New-Item -ItemType Directory -Path $staging | Out-Null;" ^
  "try {" ^
  "  foreach ($item in $items) { Copy-Item -LiteralPath $item -Destination $staging -Recurse -Force }" ^
  "  Get-ChildItem -Path $staging -Directory -Recurse -Force | Where-Object { $_.Name -eq '__pycache__' } | Remove-Item -Recurse -Force;" ^
  "  Get-ChildItem -Path $staging -File -Recurse -Force | Where-Object { $_.Name -eq '.DS_Store' -or $_.Extension -eq '.pyc' } | Remove-Item -Force;" ^
  "  if (Test-Path $destination) { Remove-Item $destination -Force }" ^
  "  Compress-Archive -Path (Join-Path $staging '*') -DestinationPath $destination -Force;" ^
  "  Write-Host ('Created ' + $destination)" ^
  "} finally {" ^
  "  Remove-Item $staging -Recurse -Force -ErrorAction SilentlyContinue" ^
  "}"

set "EXIT_CODE=%ERRORLEVEL%"
popd >nul
exit /b %EXIT_CODE%
