@echo off
rem Final polish of a finished project with Claude Opus 5.5 (one pass). Usage: polish.bat "folder or what to polish"
call "%~dp0config.bat"
cd /d "%~dp0"
claude -p --model claude-opus-5-5 --permission-mode acceptEdits "FINAL POLISH, one pass only: %~1. Review the finished project and make it more modern, professional and solid: design/UX (layout, spacing, typography, colors, responsive, accessibility), clean structure, error handling, speed, small bugs. Make focused edits only inside this folder, never delete files, confirm nothing broke (run tests or open it), then reply with 3-5 lines on what you improved."
pause
