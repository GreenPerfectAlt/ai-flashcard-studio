@echo off
chcp 65001 > nul
cls
title AI Flashcard Studio - Автозапуск
echo ============================================================
echo [INFO] Запуск AI Flashcard Studio...
echo ============================================================

:: 1. Определяем букву диска и папку проекта
set DRIVE=%~d0
set PROJECT_DIR=%~dp0
cd /d "%PROJECT_DIR%"

:: 2. Ищем портативный Python в корне флешки
set PORTABLE_PYTHON=%DRIVE%\Python\Python311\python.exe
if exist "%PORTABLE_PYTHON%" goto mode_flash_drive

:: 3. Ищем портативный Python внутри папки самого проекта
set PORTABLE_PYTHON=%PROJECT_DIR%Python\Python311\python.exe
if exist "%PORTABLE_PYTHON%" goto mode_flash_drive

:: 4. Если не найден — режим ноутбука с .venv
goto mode_laptop


:mode_flash_drive
echo [MODE] Обнаружен портативный Python. Запуск в режиме ФЛЕШКИ.
echo ------------------------------------------------------------
set PATH=%DRIVE%\Python\Python311\Scripts;%DRIVE%\Python\Python311;%PATH%
set RUN_COMMAND="%PORTABLE_PYTHON%" -m uvicorn main:app --host 127.0.0.1 --port 8000
goto start_server


:mode_laptop
echo [MODE] Портативный Python не найден. Запуск в режиме ПК (.venv).
echo ------------------------------------------------------------
set PYTHON_PATH=%PROJECT_DIR%.venv
set VENV_PY=%PYTHON_PATH%\Scripts\python.exe
set VENV_PIP=%PYTHON_PATH%\Scripts\pip.exe
set VENV_UVICORN=%PYTHON_PATH%\Scripts\uvicorn.exe

:: Если .venv не существует — создаём через системный python
if exist "%VENV_PY%" goto skip_venv_creation
echo [INFO] Создание локального окружения .venv...
where python >nul 2>&1
if errorlevel 1 goto no_system_python
python -m venv .venv
if not exist "%VENV_PY%" goto venv_create_failed
:skip_venv_creation

:: Если pip в venv битый — переустанавливаем через ensurepip (однострочно!)
"%VENV_PY%" -m pip --version >nul 2>&1
if errorlevel 1 call "%VENV_PY%" -m ensurepip --default-pip

:: Если uvicorn не установлен — ставим все зависимости через venv-pip
if exist "%VENV_UVICORN%" goto skip_install
echo [ALERT] Библиотеки в .venv не найдены! Начинаю установку...
"%VENV_PY%" -m pip install --upgrade pip
"%VENV_PY%" -m pip install -r requirements.txt
if not exist "%VENV_UVICORN%" goto install_failed
:skip_install

set RUN_COMMAND="%VENV_PY%" -m uvicorn main:app --host 127.0.0.1 --port 8000
goto start_server


:start_server
if not exist "models" mkdir models

echo [INFO] Запуск интерфейса в браузере (через 2 сек)...
timeout /t 2 /nobreak > nul
start http://127.0.0.1:8000

echo [INFO] Старт веб-сервера FastAPI...
echo ------------------------------------------------------------
%RUN_COMMAND%
echo ------------------------------------------------------------
echo [INFO] Сервер остановлен.
echo ============================================================
echo Если выше есть ошибка — сделай скриншот и закрой это окно.
echo ============================================================
pause
goto end


:no_system_python
echo ------------------------------------------------------------
echo [ERROR] Системный Python не найден в PATH.
echo          Установи Python 3.11+ с https://python.org
echo          ИЛИ положи portable Python в PROJECT_DIR\Python\Python311\
echo ============================================================
pause
goto end

:venv_create_failed
echo ------------------------------------------------------------
echo [ERROR] Не удалось создать .venv. Удали папку .venv и запусти снова.
echo ============================================================
pause
goto end

:install_failed
echo ------------------------------------------------------------
echo [ERROR] Не удалось установить uvicorn. Проверь интернет и requirements.txt.
echo          Попробуй вручную: "%VENV_PY%" -m pip install -r requirements.txt
echo ============================================================
pause
goto end

:end
