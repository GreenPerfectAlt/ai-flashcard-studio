@echo off
cls
title AI Flashcard Studio Portable Launch
echo ============================================================
echo [INFO] Запуск AI Flashcard Studio с флешки (внешний Python)...
echo ============================================================

:: Автоматически определяем букву диска флешки (например, D: или E:)
set FLASH_DRIVE=%~d0
:: Определяем текущую папку проекта
set PROJECT_DIR=%~dp0

cd /d "%PROJECT_DIR%"

:: Прописываем путь к Python относительно корня флешки
set PYTHON_PATH=%FLASH_DRIVE%\Python\Python311

:: Проверяем, существует ли там Python
if not exist "%PYTHON_PATH%\python.exe" (
    echo [ERROR] Python не найден по пути: %PYTHON_PATH%\python.exe
    echo Проверь правильность расположения папки Python на флешке!
    pause
    exit
)

echo [INFO] Подтягиваем пути к портативному окружению...
set PATH=%PYTHON_PATH%;%PYTHON_PATH%\Scripts;%PATH%

echo [INFO] Запуск интерфейса в браузере...
timeout /t 2 /nobreak > nul
start http://127.0.0.1:8000

echo [INFO] Старт веб-сервера FastAPI (Uvicorn)...
echo ------------------------------------------------------------
"%PYTHON_PATH%\python.exe" -m uvicorn main:app --host 127.0.0.1 --port 8000

echo ------------------------------------------------------------
echo [INFO] Сервер остановлен.
pause