@echo off
setlocal
cd /d "%~dp0"
title Install Aura
echo ==== Install Aura (run this once) ====
echo.
set WHERE=1
set LOOK=1
if defined AURA_WHERE goto presetanswers
echo Where should the free helper (Qwen) run?
echo   1 = on Amar's PC over the home network (this PC has no big graphics card)
echo   2 = on this PC (needs Ollama and a big graphics card)
set WHERE=1
set /p WHERE=Type 1 or 2 and press Enter [1]:
echo.
echo Which look do you want?
echo   1 = Cyberpunk
echo   2 = Professional
set LOOK=1
set /p LOOK=Type 1 or 2 and press Enter [1]:
echo.
goto answersdone
:presetanswers
set WHERE=%AURA_WHERE%
if defined AURA_LOOK set LOOK=%AURA_LOOK%
echo Using the answers from the installer you started.
echo.
:answersdone

echo STEP 1 of 5: Python
python --version >nul 2>&1
if errorlevel 1 (
  echo Python is missing. Installing it now...
  winget install -e --id Python.Python.3.12 --accept-package-agreements --accept-source-agreements
  echo.
  echo Python is installed. CLOSE this window and double-click install.bat again.
  pause
  exit /b
)
echo OK

echo.
echo STEP 2 of 5: Aura's parts
if not exist .venv\Scripts\python.exe python -m venv .venv
if not exist .venv\Scripts\python.exe (
  echo Could not set up Python for Aura. Restart the PC and run install.bat again.
  pause
  exit /b
)
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -q -r requirements.txt
if errorlevel 1 (
  echo Could not install the parts. Check your internet and run this again.
  pause
  exit /b
)
echo OK

echo.
echo STEP 3 of 5: Desktop icon and look
if "%LOOK%"=="2" (> theme.txt echo hermes) else (if exist theme.txt del theme.txt)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0make_shortcut.ps1"
if errorlevel 1 (echo Could not make the icon.) else (echo Aura icon is on your desktop.)

echo.
echo STEP 4 of 5: The free helper (Qwen)
if "%WHERE%"=="2" goto local
set IP=
if exist ollama_url.txt set /p IP=<ollama_url.txt
:askip
if defined IP (
  set /p IP=Address of Amar's PC [%IP%] - press Enter to keep it:
) else (
  set /p IP=Address of Amar's PC ^(for example 192.168.1.20^):
)
if not defined IP (
  echo Please type the address. Amar can see it on his PC with: ipconfig
  goto askip
)
rem accept "192.168.1.20", "192.168.1.20:11434" or "http://192.168.1.20:11434/"
set IP=%IP:http://=%
set IP=%IP:https://=%
set IP=%IP:/=%
set IP=%IP::11434=%
set IP=%IP: =%
> ollama_url.txt echo http://%IP%:11434
curl.exe -s -m 8 http://%IP%:11434/api/tags >nul 2>&1
if errorlevel 1 (
  echo.
  echo WARNING: Amar's PC did not answer at http://%IP%:11434
  echo Make sure his PC is on and Ollama is running. Finishing the rest anyway.
  echo Later, double-click check_setup.bat to test again.
) else (
  echo OK: Amar's PC answers.
)
goto claude

:local
if exist ollama_url.txt del ollama_url.txt
set /p MODEL=<model.txt
where ollama >nul 2>&1
if errorlevel 1 (
  echo Ollama was not found. Install it from https://ollama.com and run install.bat again.
  pause
  exit /b
)
curl.exe -s -m 5 http://localhost:11434/api/tags >nul 2>&1
if errorlevel 1 (
  echo Starting Ollama...
  start "" /min ollama serve
  timeout /t 6 /nobreak >nul
)
ollama list | findstr /i /c:"%MODEL%" >nul 2>&1
if errorlevel 1 (
  echo Downloading %MODEL% - about 17 GB, this can take a while...
  ollama pull %MODEL%
) else (
  echo %MODEL% is already installed.
)

:claude
echo.
echo STEP 5 of 5: Claude
where claude >nul 2>&1
if errorlevel 1 (
  echo Installing Claude Code...
  winget install -e --id Anthropic.ClaudeCode --accept-package-agreements --accept-source-agreements
  where claude >nul 2>&1
  if errorlevel 1 (
    echo.
    echo Claude Code is installed, but this window cannot see it yet.
    echo CLOSE this window and double-click install.bat again. It continues from here.
    pause
    exit /b
  )
)
echo A Claude window opens now. Sign in with YOUR Claude account, then type /exit and close it.
start "Claude sign-in" /wait cmd /c claude

echo.
echo ==== Done. Open "Aura" from your desktop. ====
pause
