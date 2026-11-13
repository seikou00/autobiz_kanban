@echo off
setlocal

"%~dp0package_workspace.cmd" --gray-release --gray %*
