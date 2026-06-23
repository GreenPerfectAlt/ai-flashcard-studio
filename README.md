# AI Flashcard Studio

Local AI-assisted flashcard generation app.

## Features

* text-to-flashcard generation
* multiple card formats
* local database support
* import/export workflow
* canvas/graph-style study interface
* model configuration through `models.json`
* FastAPI backend
* HTML/CSS/JavaScript frontend

## Stack

* Python
* FastAPI
* HTML/CSS
* JavaScript
* SQLite
* local / configurable LLM backends

## Setup

Install dependencies:

```bat
pip install -r requirements.txt
```

Run:

```bat
run.bat
```

Or:

```bat
python main.py
```

Open the local URL shown in the terminal.

## Local files not included

Do not commit:

```text
*.db
*.db-*
*.sqlite
*.sqlite3
uploads/
exports/
logs/
models/
.venv/
.env
.env.local
__pycache__/
```

## Status

Work-in-progress public release.
