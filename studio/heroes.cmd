@echo off
rem Layer8 Studio hero desk: copies the ChatGPT prompts and files your downloads. Double-click me.
cd /d "%~dp0"
python studio.py heroes %*
set "STUDIO_EXIT=%ERRORLEVEL%"
echo.
if "%STUDIO_EXIT%"=="0" (
  echo ========================================
  echo HEROES COMPLETE
  echo Images saved during this run were filed and ingested.
  echo Press any key to close this window.
  echo ========================================
) else (
  echo ========================================
  echo HEROES STOPPED WITH AN ERROR
  echo Exit code: %STUDIO_EXIT%
  echo Review the messages above. Saved images were kept.
  echo Press any key to close this window.
  echo ========================================
)
pause >nul
exit /b %STUDIO_EXIT%
