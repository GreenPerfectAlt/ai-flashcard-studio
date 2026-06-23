@echo off
setlocal EnableExtensions

set "PROJECT_ROOT=%~dp0"
cd /d "%PROJECT_ROOT%" || (
  echo [ERROR] Cannot enter project folder.
  pause
  exit /b 1
)

title AI Flashcard Studio

echo ============================================================
echo AI Flashcard Studio
echo ============================================================

set "PORT=8000"
set "HOST=127.0.0.1"
set "APP_URL=http://127.0.0.1:8000"
set "VENV_DIR=%PROJECT_ROOT%.venv"
set "VENV_PY=%VENV_DIR%\Scripts\python.exe"

if not exist "models" mkdir "models"

call :find_python
if errorlevel 1 (
  echo [ERROR] Python 3.11 or 3.12 not found.
  echo Install Python 3.11 or 3.12 and run this file again.
  echo https://www.python.org/downloads/
  pause
  exit /b 1
)

if not exist "%VENV_PY%" (
  echo [SETUP] Creating .venv using %PY_CMD%...
  %PY_CMD% -m venv ".venv"
  if errorlevel 1 goto fail_venv
)

echo [SETUP] Installing/updating dependencies...
"%VENV_PY%" -m pip install -U pip setuptools wheel
if errorlevel 1 goto fail_install

"%VENV_PY%" -m pip install -r requirements.txt
if errorlevel 1 goto fail_install

echo.
echo [INFO] Opening browser:
echo %APP_URL%
start "" "%APP_URL%"

echo.
echo [INFO] Starting FastAPI server...
echo [INFO] Press Ctrl+C to stop.
echo ============================================================

"%VENV_PY%" -m uvicorn main:app --host %HOST% --port %PORT%

echo ============================================================
echo [INFO] Server stopped.
pause
exit /b 0

:find_python
set "PY_CMD="

if exist "%PROJECT_ROOT%Python\Python311\python.exe" (
  set "PY_CMD=%PROJECT_ROOT%Python\Python311\python.exe"
  exit /b 0
)

where py >nul 2>nul
if not errorlevel 1 (
  py -3.12 -c "import sys; sys.exit(0 if sys.version_info[:2] == (3,12) else 1)" >nul 2>nul
  if not errorlevel 1 set "PY_CMD=py -3.12"

  if not defined PY_CMD (
    py -3.11 -c "import sys; sys.exit(0 if sys.version_info[:2] == (3,11) else 1)" >nul 2>nul
    if not errorlevel 1 set "PY_CMD=py -3.11"
  )
)

if not defined PY_CMD (
  python -c "import sys; sys.exit(0 if sys.version_info[:2] in [(3,11),(3,12)] else 1)" >nul 2>nul
  if not errorlevel 1 set "PY_CMD=python"
)

if not defined PY_CMD exit /b 1
exit /b 0

:fail_venv
echo.
echo [ERROR] Failed to create .venv.
echo Delete .venv and try again.
pause
exit /b 1

:fail_install
echo.
echo [ERROR] Failed to install dependencies.
echo Check internet connection and requirements.txt.
pause
exit /b 1
