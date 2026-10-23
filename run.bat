@echo off
REM ============================================================
REM  SafeExam - one-click launcher for Windows
REM  First run: creates .venv, installs requirements, seeds the
REM  demo accounts. Later runs just start the server.
REM ============================================================
setlocal
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
    echo Python was not found. Install Python 3.10+ from https://www.python.org/downloads/
    echo and tick "Add python.exe to PATH" during setup.
    pause
    exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
    echo Creating virtual environment...
    python -m venv .venv || goto :fail
)

REM Reinstall only when requirements.txt changed since the last install.
fc /b requirements.txt ".venv\.installed-requirements" >nul 2>nul
if errorlevel 1 (
    echo Installing dependencies...
    ".venv\Scripts\python.exe" -m pip install --upgrade pip >nul
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt || goto :fail
    copy /y requirements.txt ".venv\.installed-requirements" >nul
)

if not exist ".env" if exist ".env.example" copy ".env.example" ".env" >nul

REM Safe to run every time: only creates accounts/exam that are missing.
".venv\Scripts\python.exe" seed.py || goto :fail

if "%SEB_PORT%"=="" set SEB_PORT=5001
echo.
echo  SafeExam is starting on http://127.0.0.1:%SEB_PORT%/
echo  Demo logins: admin/admin123  teacher/teacher123  student/student123
echo  Press Ctrl+C to stop.
echo.
REM Open the browser once the server has had a moment to start.
start "" /min cmd /c "timeout /t 3 /nobreak >nul & start http://127.0.0.1:%SEB_PORT%/"
".venv\Scripts\python.exe" app.py
goto :eof

:fail
echo.
echo Setup failed - see the messages above.
pause
exit /b 1
