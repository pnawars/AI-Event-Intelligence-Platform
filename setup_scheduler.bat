@echo off
REM ============================================================
REM  setup_scheduler.bat
REM  Registers the daily AI Event Pipeline in Windows Task Scheduler.
REM  Run this ONCE as Administrator.
REM ============================================================

REM -- Project directory (folder containing this .bat file) ---
set "PROJECT_DIR=%~dp0"
REM Strip trailing backslash
if "%PROJECT_DIR:~-1%"=="\" set "PROJECT_DIR=%PROJECT_DIR:~0,-1%"

REM -- Auto-detect Python executable -------------------------
for /f "usebackq tokens=*" %%i in (`where python 2^>nul`) do (
    set "PYTHON_EXE=%%i"
    goto :found_python
)
echo ERROR: python not found in PATH.
echo        Install Python and ensure it is on PATH, then re-run this script.
pause
exit /b 1

:found_python
echo ============================================================
echo  AI Event Pipeline — Windows Task Scheduler Setup
echo ============================================================
echo  Project dir : %PROJECT_DIR%
echo  Python      : %PYTHON_EXE%
echo  Schedule    : Daily at 07:00
echo ============================================================
echo.

REM -- Delete any existing task with the same name -----------
schtasks /delete /tn "AiEventPipeline" /f >nul 2>&1

REM -- Register the new task ---------------------------------
schtasks /create ^
  /tn "AiEventPipeline" ^
  /tr "\"%PYTHON_EXE%\" \"%PROJECT_DIR%\run_pipeline.py\" --once" ^
  /sc daily ^
  /st 07:00 ^
  /rl HIGHEST ^
  /f

if %errorlevel% equ 0 (
    echo.
    echo SUCCESS — task "AiEventPipeline" registered.
    echo It will run daily at 07:00 using:
    echo   %PYTHON_EXE% "%PROJECT_DIR%\run_pipeline.py" --once
    echo.
    echo To verify:  schtasks /query /tn "AiEventPipeline" /fo LIST
    echo To run now: schtasks /run   /tn "AiEventPipeline"
    echo To remove:  schtasks /delete /tn "AiEventPipeline" /f
) else (
    echo.
    echo ERROR — failed to register task.
    echo Make sure you are running this script as Administrator.
)

echo.
pause
