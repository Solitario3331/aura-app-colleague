@echo off
setlocal
cd /d "%~dp0"
title Install Aura on my own PC
rem Updates for this PC come from the colleague repo (never from the other one). Updates never overwrite this file.
> "%~dp0update.json" echo {"repo":"Solitario3331/aura-app-colleague","branch":"main","token":""}
echo ==== Install Aura: the free helper runs on YOUR PC, professional look ====
echo.
where ollama >nul 2>&1
if errorlevel 1 (
  echo Ollama - the program that runs the free helper - is missing. Installing it now...
  winget install -e --id Ollama.Ollama --accept-package-agreements --accept-source-agreements
  echo.
  echo Ollama is installed. CLOSE this window and double-click Install-Colleague.bat again.
  pause
  exit /b
)
echo Ollama found. Starting the installer with the right answers for you...
echo If it asks you to close the window and run it again, double-click Install-Colleague.bat again.
echo.
set AURA_WHERE=2
set AURA_LOOK=2
call "%~dp0install.bat"
