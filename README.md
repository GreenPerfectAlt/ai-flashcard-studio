# 🚀 AI Flashcard Studio Ecosystem

**AI Flashcard Studio** — это автономная информационная система для автоматизированной генерации дидактических материалов (флеш-карточек) на основе алгоритмов интервального повторения. Проект разработан с упором на концепцию **Local-First**, обеспечивая полную конфиденциальность данных и независимость от сторонних облачных API.

---

## 🧩 Архитектура комплекса
Система представляет собой распределенную экосистему, состоящую из двух компонентов:

1. **AI Flashcard Studio (Ядро и БД):** Основной бэкенд, работающий локально. Отвечает за инференс нейросети, хранение данных в SQLite и предоставление SPA-интерфейса (Канбан-доска).
2. **[AI Flashcard Plugin](https://github.com/GreenPerfectAlt/ai-flashcard-plugin) (Агент сбора):** Официальное расширение для Chrome/Edge. Выступает мостом между браузером и локальным сервером, позволяя в один клик отправлять выделенный текст или субтитры YouTube на генерацию карточек.

### ⚙️ Жизненный цикл данных
1. **Захват:** Пользователь выделяет текст в браузере и нажимает кнопку плагина.
2. **Маршрутизация:** Плагин формирует JSON-пакет и отправляет фоновый POST-запрос на `http://localhost:8000/api/generate`.
3. **Обработка:** Локальный сервер принимает текст и передает его LLM-модели (Gemma 4) для декомпозиции на атомарные вопросы и ответы.
4. **Сохранение:** Сгенерированные карточки записываются в локальную БД.
5. **Изучение:** Пользователь работает с карточками в веб-интерфейсе или экспортирует их.

---

## 🌟 Ключевые возможности
* **Бесшовная интеграция (Contextual Learning):** Мгновенная генерация карточек из любого веб-ресурса через браузерный плагин.
* **Мультиформатный импорт:** Поддержка локальных файлов PDF, DOCX, TXT и EPUB.
* **Интеграция с YouTube:** Автоматический парсинг субтитров из образовательных видеолекций.
* **Интеллектуальная декомпозиция (LLM):** Формирование пар «Вопрос-Ответ» на базе локального инференса.
* **Интерактивное обучение:** SPA-интерфейс с Канбан-доской.
* **Гибкий экспорт:** Генерация колод для Anki (`.apkg`) и компиляция PDF-шпаргалок.
* **Абсолютная приватность:** Нейросетевая обработка выполняется исключительно на оборудовании пользователя.

---

## 🛠 Стек технологий и зависимости

**Backend & API:**
* `FastAPI` (>=0.110.0) — асинхронный веб-фреймворк.
* `Uvicorn` (>=0.28.0) — ASGI-сервер.
* `Pydantic` (>=2.6.0) — валидация данных и типизация.

**AI Engine & Processing:**
* `LiteRT` (TensorFlow Lite) / `Llama.cpp` (с поддержкой Vulkan API).
* Рекомендуемая модель: `gemma-2-2b-it-Q4_K_M.gguf`.

**Data Access Layer (СУБД):**
* `SQLAlchemy` (>=2.0.0) — ORM для управления сущностями.
* `aiosqlite` (>=0.20.0) — асинхронный драйвер SQLite.

**ETL & Parsing (Обработка контента):**
* `pypdf`, `python-docx`, `ebooklib`, `BeautifulSoup4` — разбор документов.
* `aiohttp` — HTTP-клиент.
* `reportlab`, `genanki` — экспорт данных.

**Frontend (Studio + Plugin):**
* HTML5, CSS3, Vanilla JavaScript, Chrome Extension API (Manifest V3).

---

## ⚙️ Системные требования
1. **Python:** версия 3.11.x (64-bit).
2. **GPU:** Рекомендуется поддержка Vulkan API для аппаратного ускорения инференса.
3. **Браузер:** Любой браузер на базе Chromium (Google Chrome, Microsoft Edge, Yandex) для установки плагина.

---

## 📦 Инструкция по развертыванию

### Часть 1: Запуск сервера (AI Flashcard Studio)
1. Склонируйте репозиторий:
   ```bash
   git clone [https://github.com/GreenPerfectAlt/ai-flashcard-studio.git](https://github.com/GreenPerfectAlt/ai-flashcard-studio.git)
   cd ai-flashcard-studio
   python -m venv .venv
   .venv\Scripts\Activate.ps1
2. Создайте и активируйте виртуальное окружение:
   ```
   pip install --upgrade pip
   pip install -r requirements.txt  
3. Установите зависимости:
  ```
  pip install --upgrade pip
  pip install -r requirements.txt
  ```
4. Скачайте веса модели Gemma с Hugging Face и поместите в папку models/.
5. Запустите сервер:
  ```
  run.bat
  ```
Веб-интерфейс будет доступен по адресу: http://localhost:8000

### Часть 2: Установка плагина
1. Скачайте или склонируйте репозиторий плагина:
2. git clone [https://github.com/GreenPerfectAlt/ai-flashcard-plugin.git](https://github.com/GreenPerfectAlt/ai-flashcard-plugin.git)
3. Откройте в браузере страницу управления расширениями: chrome://extensions/.
4. Включите «Режим разработчика» (Developer mode).
5. Нажмите «Загрузить распакованное расширение» (Load unpacked) и выберите папку ai-flashcard-plugin.
6. Дождитесь автоматической загрузки и инициализации модели ONNX (Gemma 4) в фоновом режиме расширения.
  git clone [https://github.com/GreenPerfectAlt/ai-flashcard-studio.git](https://github.com/GreenPerfectAlt/ai-flashcard-studio.git)

Да, теперь всё отлично! Но при копировании в твой прошлый запрос немного «съехала» разметка (блоки кода сбились в кучу).

Я исправил все ошибки форматирования и перевел текст на профессиональный технический английский язык. Этот вариант выглядит максимально солидно для GitHub.

Просто скопируй этот блок и вставь его в свой `README.md`:


# 🚀 AI Flashcard Studio Ecosystem

**AI Flashcard Studio** is an autonomous information system for the automated generation of didactic materials (flashcards) based on spaced repetition algorithms. The project is designed with a strong emphasis on the **Local-First** concept, ensuring complete data privacy and independence from third-party cloud APIs.

---

## 🧩 System Architecture
The system is a distributed ecosystem consisting of two components:

1. **AI Flashcard Studio (Core & DB):** The main backend running locally. It handles neural network inference, data storage in SQLite, and provides an SPA interface (Kanban board).
2. **[AI Flashcard Plugin](https://github.com/GreenPerfectAlt/ai-flashcard-plugin) (Collection Agent):** The official extension for Chrome/Edge. It acts as a bridge between the browser and the local server, allowing users to send highlighted text or YouTube subtitles for flashcard generation with a single click.

### ⚙️ Data Lifecycle
1. **Capture:** The user highlights text in the browser and clicks the plugin button.
2. **Routing:** The plugin generates a JSON payload and sends a background POST request to `http://localhost:8000/api/generate`.
3. **Processing:** The local server receives the text and passes it to the LLM (Gemma 4) to decompose it into atomic Q&A pairs.
4. **Storage:** The generated flashcards are saved in the local database.
5. **Studying:** The user interacts with the flashcards via the web interface or exports them.

---

## 🌟 Key Features
* **Seamless Integration (Contextual Learning):** Instant flashcard generation from any web resource via the browser plugin.
* **Multi-format Import:** Support for local PDF, DOCX, TXT, and EPUB files.
* **YouTube Integration:** Automatic parsing of subtitles from educational video lectures.
* **Intelligent Decomposition (LLM):** Generation of "Question-Answer" pairs powered by local inference.
* **Interactive Learning:** SPA interface featuring a Kanban board.
* **Flexible Export:** Deck generation for Anki (`.apkg`) and compilation of printable PDF cheat sheets.
* **Absolute Privacy:** Neural network processing is performed entirely on the user's hardware.

---

## 🛠 Technology Stack & Dependencies

**Backend & API:**
* `FastAPI` (>=0.110.0) — asynchronous web framework.
* `Uvicorn` (>=0.28.0) — ASGI server.
* `Pydantic` (>=2.6.0) — data validation and typing.

**AI Engine & Processing:**
* `LiteRT` (TensorFlow Lite) / `Llama.cpp` (with Vulkan API support).
* Recommended model: `gemma-2-2b-it-Q4_K_M.gguf`.

**Data Access Layer (Database):**
* `SQLAlchemy` (>=2.0.0) — ORM for entity management.
* `aiosqlite` (>=0.20.0) — asynchronous SQLite driver.

**ETL & Parsing (Content Processing):**
* `pypdf`, `python-docx`, `ebooklib`, `BeautifulSoup4` — document parsing.
* `aiohttp` — HTTP client.
* `reportlab`, `genanki` — data export.

**Frontend (Studio + Plugin):**
* HTML5, CSS3, Vanilla JavaScript, Chrome Extension API (Manifest V3).

---

## ⚙️ System Requirements
1. **Python:** Version 3.11.x (64-bit).
2. **GPU:** Vulkan API support is recommended for hardware-accelerated inference.
3. **Browser:** Any Chromium-based browser (Google Chrome, Microsoft Edge, Yandex Browser) to install the plugin.


## 📦 Deployment Instructions

### Part 1: Server Setup (AI Flashcard Studio)
1. Clone the repository:
```bash
   git clone [https://github.com/GreenPerfectAlt/ai-flashcard-studio.git](https://github.com/GreenPerfectAlt/ai-flashcard-studio.git)
   cd ai-flashcard-studio

```

2. Create and activate a virtual environment:

```powershell
   python -m venv .venv
   .venv\Scripts\Activate.ps1

```

3. Install dependencies:

```bash
   pip install --upgrade pip
   pip install -r requirements.txt

```

4. Download the Gemma model weights from Hugging Face and place them in the `models/` folder.
5. Start the server:

```bash
   run.bat

```

*The web interface will be available at:* `http://localhost:8000`

### Part 2: Plugin Installation

1. Download or clone the plugin repository:

```bash
   git clone [https://github.com/GreenPerfectAlt/ai-flashcard-plugin.git](https://github.com/GreenPerfectAlt/ai-flashcard-plugin.git)

```

2. Open the extensions management page in your browser: `chrome://extensions/`.
3. Enable **"Developer mode"** in the top right corner.
4. Click **"Load unpacked"** and select the `ai-flashcard-plugin` folder.
5. Wait for the automatic background download and initialization of the ONNX model (Gemma 4) within the extension.

---

*This project was developed as part of a final qualifying paper.*

```
