@echo off
rem Settings used by all scripts. Change the address if Ollama runs on another PC.
set OLLAMA_URL=http://localhost:11434
rem A PC that uses another PC's 5090 keeps its address in ollama_url.txt (updates never touch that file).
if exist "%~dp0ollama_url.txt" set /p OLLAMA_URL=<"%~dp0ollama_url.txt"
rem Model name now lives in model.txt (one line). Change it there.
set /p QWEN_MODEL=<"%~dp0model.txt"
