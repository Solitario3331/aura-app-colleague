@echo off
rem Starts Aura. The "Aura" desktop shortcut points here.
rem theme.txt (optional, one word: hermes) picks the professional look; no file = cyberpunk look.
rem ollama_url.txt (optional) = address of the PC whose graphics card runs Qwen.
cd /d "%~dp0"
if not exist .venv\Scripts\pythonw.exe (
  echo Aura is not set up yet. Double-click the setup file first.
  pause
  exit /b
)
call config.bat
if exist theme.txt set /p AURA_THEME=<theme.txt
call .venv\Scripts\activate.bat
python -m pip install --disable-pip-version-check -q -r requirements.txt
start "" "%~dp0.venv\Scripts\pythonw.exe" "%~dp0app\app.py"
