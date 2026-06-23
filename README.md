# AI Flashcard Studio

AI Flashcard Studio is a local-first flashcard generation and review application for Windows. It converts text, PDFs, images, URLs, and manual notes into structured study cards using a local AI model pipeline.

The project focuses on fast card creation, visual knowledge mapping, tag-based navigation, source-aware generation, and offline-friendly study workflows.

## Features

- Local AI-assisted flashcard generation
- Source-based card creation from text, PDF, image, URL, and imported files
- Canvas view with draggable cards, sources, links, tags, statuses, and card types
- Tag focus mode for highlighting related cards and source connections
- Card type support:
  - Question / answer
  - Definition
  - Fact
  - Concept
  - Cloze
  - True / false
  - Multiple choice
- Automatic mixed card type classification
- Manual card editing and image attachments
- Review statuses:
  - Inbox
  - Today
  - Planned
  - Done
- Due dates and review scheduling
- Source inspector and source-linked cards
- Similar-by-tag navigation
- Global search across decks, sources, and cards
- Keyboard and touchpad canvas navigation
- Export-oriented card data model

## Current local model direction

The app is designed around local inference. The current generation pipeline is structured so that the UI, parser, database, graph, and export logic are separate from the inference backend.

Current local model direction:

- LiteRTLM / `.litertlm` runtime for local on-device inference experiments
- Future adapter path for `llama.cpp` / GGUF models
- Local-first design without requiring a cloud inference API

Large model files are not included in the repository.

## Architecture

```text
frontend:  HTML / CSS / JavaScript
backend:   Python / FastAPI
storage:   SQLite / SQLAlchemy
runtime:   local model backend
canvas:    custom HTML/CSS/JS graph UI
platform:  Windows-first local app
```

Main layers:

```text
UI → FastAPI backend → generation pipeline → parser/post-processing → SQLite → canvas graph/review/export
```

The generation pipeline handles:

- chunking
- prompt building
- model calls
- JSON/JSONL parsing
- card count control
- card type repair
- tag normalization
- mnemonic handling
- source linking
- scheduling metadata

## Canvas controls

```text
Left click card/source     select object
Click tag                  focus tag
Click status               focus status
Click card type            focus card type
Space                      clear selection/focus/search
Double Space               auto-layout canvas
Escape                     full reset + fit view
Alt/Shift + drag canvas    pan canvas
Middle mouse drag          pan canvas
Arrow keys / WASD          pan canvas
Shift + Arrow/WASD         faster pan
+ / =                      zoom in
-                          zoom out
0                          fit view
```

## Installation

Requirements:

- Windows
- Python 3.11 or 3.12
- Local model runtime files, depending on selected backend

Basic setup:

```bat
cd /d C:\path\to\ai-flashcard-studio
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

Run:

```bat
python main.py
```

Then open:

```text
http://127.0.0.1:8000
```

If the project includes a `.bat` launcher, use it instead of manual commands.

## Repository hygiene

Do not commit local models, caches, databases, virtual environments, logs, or generated exports.

Recommended ignored files:

```text
.venv/
venv/
__pycache__/
*.pyc
models/
*.gguf
*.litertlm
*.safetensors
*.db
*.sqlite
*.sqlite3
logs/
exports/
user_data/
tmp/
cache/
.env
.env.local
node_modules/
dist/
build/
.DS_Store
Thumbs.db
```

## Status

This is an active local AI application prototype. The project already includes the core app flow:

```text
source → AI generation → typed cards → tags/statuses/dates → visual canvas → review/export workflow
```

The next major technical direction is a clean inference adapter layer:

```text
LiteRTLM | llama.cpp / GGUF | OpenAI-compatible endpoint
```

## License

License is not finalized in this repository unless a `LICENSE` file is present.
