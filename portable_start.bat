@echo off
setlocal EnableExtensions

set "SRC=%~dp0"
set "DST=%LOCALAPPDATA%\AIFlashcardStudio"
set "RUN_DST=%DST%\run.bat"

title AI Flashcard Studio Portable Start

echo ============================================================
echo AI Flashcard Studio - Portable Start
echo ============================================================
echo Source:
echo %SRC%
echo.
echo Local copy:
echo %DST%
echo.

if not exist "%DST%" mkdir "%DST%"

echo [COPY] Copying project to local disk...
echo [COPY] This can take time if models are large.
echo.

:: Добавлена точка после %SRC%, чтобы экранирующий слэш в конце пути не ломал синтаксис кавычек
robocopy "%SRC%." "%DST%" /E /XD .git .venv venv __pycache__ .pytest_cache .mypy_cache .ruff_cache uploads cache logs exports user_data tmp media_cache /XF *.pyc *.pyo *.pyd *.db *.db-* *.sqlite *.sqlite3 .env .env.local *.local
set "RC=%ERRORLEVEL%"

if %RC% GEQ 8 (
  echo.
  echo [ERROR] Robocopy failed. ErrorLevel=%RC%
  pause
  exit /b 1
)

echo.
echo [OK] Local copy ready.
echo.

if not exist "%RUN_DST%" (
  echo [ERROR] run.bat not found in local copy:
  echo %RUN_DST%
  pause
  exit /b 1
)

cd /d "%DST%" || (
  echo [ERROR] Cannot enter local copy.
  pause
  exit /b 1
)

call "%RUN_DST%"
exit /b %ERRORLEVEL%