@echo off
rem Layer8 Studio hero desk: copies the ChatGPT prompts and files your downloads. Double-click me.
cd /d "%~dp0"
python studio.py heroes %*
echo.
pause
