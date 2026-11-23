@echo off
cd /d "%~dp0"
call "%~dp0config.bat"
call .venv\Scripts\activate.bat
python agent.py %*
