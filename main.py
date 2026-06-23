import os
import io
import re
import csv
import uuid
import json
import html
import math
import time
import zipfile
import sqlite3
import tempfile
import traceback
import unicodedata
from datetime import datetime, timedelta
from typing import List, Optional, Dict, Tuple
from urllib.parse import quote, urlparse, parse_qs

import asyncio
from fastapi import FastAPI, HTTPException, Depends, UploadFile, File, BackgroundTasks
from fastapi.responses import FileResponse, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy import create_engine, Column, Integer, String, DateTime, ForeignKey, func, event, Index, text, Float
from sqlalchemy.orm import declarative_base, sessionmaker, Session, relationship

try:
    import yake  # type: ignore
except Exception:
    yake = None

try:
    from keybert import KeyBERT  # type: ignore
except Exception:
    KeyBERT = None

try:
    import pymorphy3  # type: ignore
except Exception:
    pymorphy3 = None

try:
    from razdel import sentenize  # type: ignore
except Exception:
    sentenize = None

from llm_config import ask_litert, ask_litert_v2, ask_litert_stream, init_engine, unload_engine, get_engine_status, benchmark_litert, get_model_prompt_chars, clear_llm_cache, default_system_message
from app.db_migrations import upgrade_sqlite_schema
from app.srs import ReviewState, schedule_review, start_of_local_day
from app.media import save_binary_media, extract_pdf_images, primary_image_path
from app.generation_nlp import build_fact_batches, build_fact_plan, sanitize_mnemonic, repair_question_with_fact, question_subject_is_bad
from app.text_cleaning import clean_source_text, is_artifact_text, is_useful_sentence
from app.flashcard_quality import validate_flashcard_payload
from app.modern_qg import build_evidence_batches, build_evidence_prompt, build_evidence_units, select_retry_evidence, validate_model_card
from app.card_output_parser import parse_cards_from_text
from app.export_profiles import export_profile_options, normalize_output_profile
from app.nlp_ru import sentence_similarity
from app.card_formats import build_export_fields, fields_json, fields_for_export, load_fields_json, normalize_profile as normalize_card_format_profile, one_line

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "flashcards.db")
SQLALCHEMY_DATABASE_URL = f"sqlite:///{DB_PATH}"


# ------------------------- generation configuration -------------------------

def _env_int(name: str, default: int, min_value: int = 1, max_value: int | None = None) -> int:
    """Read integer settings from env once, with sane validation.

    This keeps generation limits configurable instead of scattering magic numbers
    through backend and frontend.
    """
    raw = os.environ.get(name, "").strip()
    try:
        value = int(raw) if raw else int(default)
    except Exception:
        value = int(default)
    value = max(min_value, value)
    if max_value is not None:
        value = min(max_value, value)
    return value


def _env_flag(name: str, default: str = "0") -> bool:
    return os.environ.get(name, default).strip().lower() in {"1", "true", "yes", "on"}


def _env_float(name: str, default: float, min_value: float = 0.0, max_value: float | None = None) -> float:
    raw = os.environ.get(name, "").strip()
    try:
        value = float(raw) if raw else float(default)
    except Exception:
        value = float(default)
    value = max(min_value, value)
    if max_value is not None:
        value = min(max_value, value)
    return value


GENERATION_DEFAULT_CARD_COUNT = _env_int("AIFC_DEFAULT_CARD_COUNT", 10)
GENERATION_MAX_CARD_COUNT = _env_int("AIFC_MAX_CARD_COUNT", 1000)
GENERATION_LITERT_BATCH_CARDS = _env_int("AIFC_LITERT_BATCH_CARDS", 40)
GENERATION_SERVER_BATCH_CARDS = _env_int("AIFC_SERVER_BATCH_CARDS", 18)
GENERATION_COMPLETION_RETRIES = _env_int("AIFC_GENERATION_RETRIES", 4, min_value=0)
GENERATION_FAST_COMPLETION_RETRIES = _env_int("AIFC_FAST_GENERATION_RETRIES", 3, min_value=0, max_value=8)
GENERATION_COMPLETION_ROUND_BONUS = _env_int("AIFC_COMPLETION_ROUND_BONUS", 2, min_value=0, max_value=12)
GENERATION_AUTO_WORDS_PER_CARD = _env_int("AIFC_AUTO_WORDS_PER_CARD", 34, min_value=8)
GENERATION_MAX_PROMPT_REPEAT_AVOID = _env_int("AIFC_AVOID_FRONTS_LIMIT", 32, min_value=4)
# Stage113: no deterministic card-template rescue. Under-produced runs are
# repaired by the selected model; if quality checks still reject cards, the app
# saves fewer cards instead of filling the deck with Python-made templates.
GENERATION_MODE = os.environ.get("AIFC_GENERATION_MODE", "fast").strip().lower()
# auto/fast/smart. smart tries KeyBERT if it is installed; auto keeps YAKE first for speed.
TAG_EXTRACTION_MODE = os.environ.get("AIFC_TAG_EXTRACTION", "auto").strip().lower()
# Generation never fills missing cards with deterministic Python templates.
# By default it also does not save a partial result such as 2/24 as if it were a
# successful generation. Set AIFC_SAVE_PARTIAL_GENERATION=1 only for debugging.
GENERATION_SAVE_PARTIAL = _env_flag("AIFC_SAVE_PARTIAL_GENERATION", "0")
GENERATION_CANDIDATE_FACTOR = _env_float("AIFC_GENERATION_CANDIDATE_FACTOR", 1.5, min_value=1.0, max_value=3.0)

def generation_fast_mode(mode: str | None = None) -> bool:
    current = (mode or GENERATION_MODE or "fast").strip().lower()
    return current not in {"strict", "quality", "slow", "exact"}


def generation_completion_passes(mode: str | None = None) -> int:
    if generation_fast_mode(mode):
        return max(0, GENERATION_FAST_COMPLETION_RETRIES)
    return max(1, GENERATION_COMPLETION_RETRIES or 2)


def generation_limits() -> dict:
    return {
        "default_cards": GENERATION_DEFAULT_CARD_COUNT,
        "max_cards": GENERATION_MAX_CARD_COUNT,
        "litert_batch_cards": GENERATION_LITERT_BATCH_CARDS,
        "server_batch_cards": GENERATION_SERVER_BATCH_CARDS,
        "completion_retries": GENERATION_COMPLETION_RETRIES,
        "fast_completion_retries": GENERATION_FAST_COMPLETION_RETRIES,
        "completion_round_bonus": GENERATION_COMPLETION_ROUND_BONUS,
        "auto_words_per_card": GENERATION_AUTO_WORDS_PER_CARD,
        "candidate_factor": GENERATION_CANDIDATE_FACTOR,
        "save_partial": GENERATION_SAVE_PARTIAL,
        "mode": "fast" if generation_fast_mode() else "strict",
    }


def clamp_generation_count(value, default: int | None = None) -> int:
    fallback = GENERATION_DEFAULT_CARD_COUNT if default is None else default
    try:
        requested = int(value if value not in (None, "") else fallback)
    except Exception:
        requested = int(fallback)
    return max(1, min(GENERATION_MAX_CARD_COUNT, requested))


def generation_batch_size(model_name: str | None = None) -> int:
    """Return only the technical per-call chunk size.

    This function must never change the user's requested final card count.
    It only controls how many cards are requested from the local/server model in
    one prompt, so large runs are split into several calls instead
    of being silently capped. Model-specific caps are intentionally avoided here;
    tune AIFC_LITERT_BATCH_CARDS / AIFC_SERVER_BATCH_CARDS when needed.
    """
    name = str(model_name or "").lower()
    if name.startswith("llama-server") or "server" in name:
        return max(1, min(GENERATION_MAX_CARD_COUNT, GENERATION_SERVER_BATCH_CARDS))
    return max(1, min(GENERATION_MAX_CARD_COUNT, GENERATION_LITERT_BATCH_CARDS))


PROFILE_GENERATION = _env_flag("AIFC_PROFILE_GENERATION", "1")


def _profile_enabled() -> bool:
    return PROFILE_GENERATION or _env_flag("AIFC_PROFILE", "0")


def _profile_log(run_id: str, event: str, **data) -> None:
    """Tiny stdout profiler. No generation behavior changes."""
    if not _profile_enabled():
        return
    clean = {}
    for key, value in data.items():
        try:
            if isinstance(value, float):
                clean[key] = round(value, 4)
            elif isinstance(value, (str, int, bool)) or value is None:
                clean[key] = value
            else:
                clean[key] = str(value)
        except Exception:
            clean[key] = "<unprintable>"
    try:
        payload = json.dumps(clean, ensure_ascii=False, sort_keys=True)
    except Exception:
        payload = str(clean)
    print(f"[PROFILE127][{run_id}][{event}] {payload}")


engine = create_engine(SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False})


@event.listens_for(engine, "connect")
def set_sqlite_pragma(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.execute("PRAGMA temp_store=MEMORY")
    cursor.execute("PRAGMA cache_size=-64000")
    cursor.close()


SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


class Deck(Base):
    __tablename__ = "decks"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    created_at = Column(DateTime, default=func.now())
    tags = Column(String, nullable=True)
    cards = relationship("Card", back_populates="deck", cascade="all, delete-orphan")
    sources = relationship("SourceNode", back_populates="deck", cascade="all, delete-orphan")


class Card(Base):
    __tablename__ = "cards"

    id = Column(Integer, primary_key=True, index=True)
    front = Column(String)
    back = Column(String)
    source_quote = Column(String, nullable=True)
    mnemonic = Column(String, nullable=True)
    tags = Column(String, nullable=True)
    status = Column(String, default="inbox")
    due_date = Column(DateTime, nullable=True)
    card_type = Column(String, default="basic")
    ease_factor = Column(Float, default=2.5)
    interval_days = Column(Integer, default=0)
    review_count = Column(Integer, default=0)
    lapses = Column(Integer, default=0)
    last_reviewed_at = Column(DateTime, nullable=True)
    image_path = Column(String, nullable=True)
    order = Column(Integer, default=0)
    deck_id = Column(Integer, ForeignKey("decks.id"), index=True)
    created_at = Column(DateTime, default=func.now())
    source_node_id = Column(String, nullable=True, index=True)
    model = Column(String, nullable=True)
    export_profile = Column(String, default="anki")
    fields_json = Column(String, nullable=True)
    x = Column(Integer, nullable=True)
    y = Column(Integer, nullable=True)
    deck = relationship("Deck", back_populates="cards")


class SourceNode(Base):
    __tablename__ = "source_nodes"

    id = Column(String, primary_key=True, index=True)
    deck_id = Column(Integer, ForeignKey("decks.id"), index=True, nullable=False)
    title = Column(String, nullable=False)
    source_type = Column(String, default="text")
    url = Column(String, nullable=True)
    content = Column(String, nullable=True)
    media_json = Column(String, nullable=True)
    tags = Column(String, nullable=True)
    color = Column(String, nullable=True)
    preview = Column(String, nullable=True)
    icon = Column(String, default="📄")
    x = Column(Integer, default=120)
    y = Column(Integer, default=160)
    created_at = Column(DateTime, default=func.now())
    deck = relationship("Deck", back_populates="sources")


Base.metadata.create_all(bind=engine)


def upgrade_db() -> None:
    applied = upgrade_sqlite_schema(engine)
    if applied:
        print("[DB] Schema upgraded:", ", ".join(applied))


upgrade_db()
Index("idx_cards_deck_created_order", Card.deck_id, Card.created_at.desc(), Card.order.asc())
Index("idx_source_nodes_deck_created", SourceNode.deck_id, SourceNode.created_at.desc())


def json_dumps(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


task_progress: Dict[int, dict] = {}


def classify_model_name(model_name: str) -> dict:
    low = (model_name or "").lower()
    if not low:
        return {"kind": "unknown", "icon": "📚", "label": "manual"}
    if "llama" in low or "super" in low or "e4b" in low:
        return {"kind": "quality", "icon": "🧠", "label": "SuperGemma"}
    if "e2b" in low or "gemma-4" in low or "gemma" in low:
        return {"kind": "fast", "icon": "⚡", "label": "Gemma E2B"}
    if "import" in low:
        return {"kind": "import", "icon": "📦", "label": "import"}
    return {"kind": "other", "icon": "📚", "label": model_name}


def summarize_deck_models(models: List[str]) -> dict:
    clean = [m for m in models if m]
    if not clean:
        return {"model_icon": "📚", "model_kind": "empty", "model_label": "empty"}
    kinds = [classify_model_name(m) for m in clean]
    unique_kinds = {k["kind"] for k in kinds}
    if len(unique_kinds) == 1:
        k = kinds[0]
        return {"model_icon": k["icon"], "model_kind": k["kind"], "model_label": k["label"]}
    if unique_kinds <= {"fast", "import"}:
        return {"model_icon": "⚡", "model_kind": "fast", "model_label": "Gemma E2B + import"}
    if "quality" in unique_kinds and len(unique_kinds - {"quality", "import"}) == 0:
        return {"model_icon": "🧠", "model_kind": "quality", "model_label": "SuperGemma + import"}
    return {"model_icon": "🧩", "model_kind": "mixed", "model_label": "mixed models"}
uploaded_files: Dict[str, dict] = {}
current_model = "gemma-4-E2B-it"


def make_source_id() -> str:
    return "src_" + uuid.uuid4().hex[:16]


def source_icon(source_type: str) -> str:
    return {
        "url": "🌐",
        "youtube": "▶️",
        "pdf": "📕",
        "docx": "📘",
        "file": "📁",
        "image": "🖼️",
        "import": "⬇️",
        "text": "📄",
    }.get((source_type or "text").lower(), "📄")


def guess_source_title(raw: str, source_type: str = "text", filename: str = None) -> str:
    raw = (raw or "").strip()
    if filename:
        return filename[:120]
    if raw.startswith("http://") or raw.startswith("https://") or "." in raw.split("/")[0]:
        return normalize_url(raw)[:120] if "normalize_url" in globals() else raw[:120]
    first = re.sub(r"\s+", " ", raw).strip().split(". ")[0]
    if not first:
        return {"url": "Ссылка", "youtube": "YouTube", "text": "Текстовый источник"}.get(source_type, "Источник")
    return first[:90] + ("…" if len(first) > 90 else "")


def make_preview(text_value: str, limit: int = 280) -> str:
    text_value = re.sub(r"\s+", " ", (text_value or "")).strip()
    return text_value[:limit] + ("…" if len(text_value) > limit else "")


def _progress_enrich(state: dict) -> dict:
    payload = dict(state or {})
    started_at = payload.get("started_at")
    finished_at = payload.get("finished_at")
    now = time.time()
    try:
        started = float(started_at) if started_at else None
    except Exception:
        started = None
    try:
        finished = float(finished_at) if finished_at else None
    except Exception:
        finished = None

    if started:
        end = finished or now
        elapsed = max(0.0, end - started)
        payload["elapsed_seconds"] = round(elapsed, 1)
        try:
            current = max(0, int(payload.get("current") or 0))
            total = max(0, int(payload.get("total") or 0))
        except Exception:
            current, total = 0, 0
        if payload.get("status") == "processing" and total > 0 and current > 0 and current < total:
            per_step = elapsed / max(1, current)
            raw_eta = round(max(0.0, (total - current) * per_step), 1)
            # UI should not show a jumpy ETA that grows while the model is stuck
            # inside a long LiteRT call. Keep the public estimate monotonic within
            # one task; elapsed time still shows the real duration.
            prev_eta = payload.get("_last_eta_seconds")
            try:
                prev_eta = float(prev_eta) if prev_eta not in (None, "") else None
            except Exception:
                prev_eta = None
            eta = raw_eta if prev_eta is None else min(raw_eta, prev_eta)
            payload["eta_seconds"] = eta
            state["_last_eta_seconds"] = eta
        else:
            payload["eta_seconds"] = None
    else:
        payload.setdefault("elapsed_seconds", 0)
        payload.setdefault("eta_seconds", None)
    return payload


def progress_start(deck_id: int, total: int = 0, message: str = "") -> None:
    task_progress[deck_id] = {
        "status": "processing",
        "current": 0,
        "total": int(total or 0),
        "message": message,
        "started_at": time.time(),
        "finished_at": None,
    }


def progress_update(deck_id: int, **kwargs) -> None:
    state = task_progress.setdefault(deck_id, {
        "status": "processing",
        "current": 0,
        "total": 0,
        "message": "",
        "started_at": time.time(),
        "finished_at": None,
    })
    state.update(kwargs)


def progress_done(deck_id: int, current: int, total: int, message: str) -> None:
    state = task_progress.get(deck_id, {})
    started_at = state.get("started_at") or time.time()
    task_progress[deck_id] = {
        "status": "completed",
        "current": current,
        "total": total,
        "message": message,
        "started_at": started_at,
        "finished_at": time.time(),
    }


def progress_error(deck_id: int, message: str) -> None:
    state = task_progress.get(deck_id, {})
    started_at = state.get("started_at") or time.time()
    task_progress[deck_id] = {
        "status": "error",
        "current": 0,
        "total": 0,
        "message": message,
        "started_at": started_at,
        "finished_at": time.time(),
    }
SOURCE_CACHE_DB_PATH = os.path.join(BASE_DIR, "cache", "source_cache.sqlite3")



# ------------------------- text utils -------------------------

def normalize_text(text_value: str, max_chars: int = 120_000) -> str:
    return clean_source_text(text_value or "", max_chars=max_chars)


def strip_html_tags(value: str) -> str:
    value = re.sub(r"<br\s*/?>", "\n", value or "", flags=re.I)
    value = re.sub(r"<[^>]+>", " ", value)
    value = html.unescape(value)
    return normalize_text(value, max_chars=20_000)


def sentence_units(text_value: str) -> List[str]:
    text_value = normalize_text(text_value)
    if not text_value:
        return []
    if sentenize is not None:
        try:
            return [s.text.strip() for s in sentenize(text_value) if s.text and s.text.strip()]
        except Exception:
            pass
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+|\n+", text_value) if s and s.strip()]


def split_text_into_chunks(text_value: str, chunk_size: int = 5200, overlap_size: int = 260) -> List[str]:
    text_value = normalize_text(text_value)
    if not text_value:
        return []
    if len(text_value) <= chunk_size:
        return [text_value]

    sentences = sentence_units(text_value)
    chunks: List[str] = []
    current: List[str] = []
    current_len = 0

    for sent in sentences:
        sent = sent.strip()
        if not sent:
            continue
        if len(sent) > chunk_size:
            for i in range(0, len(sent), chunk_size):
                part = sent[i:i + chunk_size].strip()
                if part:
                    chunks.append(part)
            current = []
            current_len = 0
            continue
        sent_len = len(sent) + 1
        if current and current_len + sent_len > chunk_size:
            chunks.append(" ".join(current).strip())
            overlap: List[str] = []
            overlap_len = 0
            for s in reversed(current):
                if overlap_len + len(s) + 1 <= overlap_size:
                    overlap.insert(0, s)
                    overlap_len += len(s) + 1
                else:
                    break
            current = overlap
            current_len = overlap_len
        current.append(sent)
        current_len += sent_len

    if current:
        chunks.append(" ".join(current).strip())
    return [c for c in chunks if len(c) > 80]


def select_chunks_for_card_count(chunks: List[str], desired_card_count: int, model_name: str | None = None) -> List[str]:
    """Pick representative chunks for the requested amount of cards.

    The limit is derived from generation configuration, not from hidden UI caps.
    Local LiteRT models get smaller batches to avoid long/invalid decodes; server
    models may process larger batches.
    """
    if not chunks:
        return []
    desired_card_count = clamp_generation_count(desired_card_count)
    batch = generation_batch_size(model_name)
    max_chunks = min(len(chunks), max(1, math.ceil(desired_card_count / batch)))
    if len(chunks) <= max_chunks:
        return chunks
    if max_chunks == 1:
        # Для статей/Wikipedia первый фрагмент обычно содержит определение и структуру.
        return [chunks[0]]
    step = (len(chunks) - 1) / (max_chunks - 1)
    indexes = sorted({round(i * step) for i in range(max_chunks)})
    return [chunks[i] for i in indexes]


def distribute_cards(total_cards: int, chunks_count: int) -> List[int]:
    total_cards = clamp_generation_count(total_cards)
    chunks_count = max(1, chunks_count)
    base = total_cards // chunks_count
    rest = total_cards % chunks_count
    result = [base + (1 if i < rest else 0) for i in range(chunks_count)]
    return [max(1, x) for x in result]


def get_existing_tags_from_deck(db: Session, deck_id: int, limit: int = 80) -> str:
    cards = db.query(Card.tags).filter(Card.deck_id == deck_id, Card.tags.isnot(None)).limit(500).all()
    tags = []
    seen = set()
    for row in cards:
        if not row[0]:
            continue
        for tag in str(row[0]).split():
            tag = tag.strip().lstrip("#")
            if tag and tag not in seen:
                seen.add(tag)
                tags.append("#" + tag)
                if len(tags) >= limit:
                    return " ".join(tags)
    return " ".join(tags)


def extract_tags(mnemonic: str, extra: str = "") -> str:
    raw = " ".join([mnemonic or "", extra or ""])
    matches = re.findall(r"#[^\s#.,;:!?()\[\]{}<>]+", raw)
    clean = []
    seen = set()
    for tag in matches:
        tag = tag.strip().lstrip("#").lower()
        tag = re.sub(r"[^0-9a-zа-яё_-]", "", tag, flags=re.I)
        if tag and tag not in seen:
            seen.add(tag)
            clean.append(tag)
    return " ".join(clean) if clean else ""



_RU_STOPWORDS = {
    "это", "как", "или", "для", "при", "над", "под", "что", "его", "она", "они", "оно", "так", "уже", "еще", "ещё",
    "без", "был", "была", "были", "будет", "является", "который", "которая", "которые", "также", "после",
    "между", "через", "если", "более", "менее", "очень", "может", "могут", "всех", "все", "всё", "этот", "эта", "эти",
    "можно", "нужно", "например", "около", "раздел", "статья", "таблица", "список", "ссылки", "примечания", "литература",
    "данные", "значение", "часть", "группа", "имеет", "имеют", "кроме", "среди", "используется", "используют",
    "факт", "факты", "наука", "научный", "научная", "разное", "прочее", "общие", "общий", "общая", "основной", "главный",
    "количество", "число", "цель", "основание", "основании", "состав", "группа", "блок", "барьер", "функция",
    "вопрос", "ответ", "цитата", "мнемоника", "подсказка", "контекст", "json", "валидный", "компактный",
    "должен", "должна", "должно", "только", "короткий", "точный", "настоящий", "формат", "поле",
    "требование", "условие", "структура", "элемент", "первом", "данном", "связан", "связана", "связано",
    "какой", "какая", "какое", "какие", "каким", "какую", "каком", "образом", "почему", "откуда",
    "быть", "являться", "представляет", "собой", "продукт", "раздел", "тема", "темой", "ключевой",
    "другой", "другая", "другие", "другим", "другими", "других", "некоторый", "некоторые", "некоторых", "некоторым", "некоторыми",
    "однако", "поэтому", "именно", "такой", "такая", "такие", "такое", "большой", "малый", "первый", "второй",
    "того", "тому", "теми", "тех", "затем", "сейчас", "сегодня", "сегодняшний", "зависимости", "вида", "ряд", "ряда",
    "лицо", "лица", "лиц", "высококачественные", "широко", "распространена", "необходимо", "неизменно", "вероятно",
    "автор", "текст", "материал", "источник", "страница", "файл", "документ", "карточка", "карточки",
    "the", "and", "for", "with", "that", "this", "from", "into", "are", "was", "were", "has", "have", "not", "can",
    "about", "which", "when", "where", "what", "who", "why", "how", "into", "than", "then", "also", "more", "most",
}

_RU_STOPWORDS.update({
    # Общие служебные слова интерфейса/LLM; без предметных слов из конкретных источников.
    "записано", "свяжи", "связка", "вот", "главное", "самый", "самая", "самые",
    "грубых", "ошибок", "данные", "скачивания", "users", "downloads",
})

_VERB_LIKE_ENDINGS_RU = (
    "ается", "яется", "ются", "ется", "ишь", "ать", "ять", "ить", "ться", "чь",
    "енный", "анная", "анное", "анные", "енными", "аемый", "яемый", "имый",
    "ющий", "ющая", "ющее", "ющие", "вший", "вшая", "вшие", "ивший",
    "овано", "евано", "ируют", "ируется", "ируется", "ался", "алась", "ались",
)

def _looks_like_bad_tag_word(word: str) -> bool:
    w = normalize_tag_word(word) if 'normalize_tag_word' in globals() else str(word or '').lower().strip()
    if not w:
        return True
    if w in _RU_STOPWORDS or ('_TAG_BLACKLIST' in globals() and w in _TAG_BLACKLIST):
        return True
    if re.search(r"[а-я]", w) and w.endswith(_VERB_LIKE_ENDINGS_RU):
        return True
    if re.search(r"[а-я]", w) and len(w) > 7 and re.search(r"(вш|ющ|емы|имы|ован|ирован)", w):
        return True
    return False

_TAG_BLACKLIST = {
    # Технический мусор из HTML/PDF/Wiki/Markdown. Не предметная логика.
    "displaystyle", "math", "mrow", "style", "thumb", "wikitable", "vector", "mw-parser-output", "reference",
    "class", "span", "div", "href", "title", "edit", "править", "викиданные", "википедия", "commons", "isbn",
    "файл", "изображение", "страница", "категория", "шаблон", "источники", "примечание", "примечания",
    "литература", "ссылки", "архивировано", "проверено", "дата", "англ", "нем", "лат", "рус",
    "хештег", "hashtag", "pdf", "docx", "txt", "md", "html", "источник", "карточка", "карточки",
    "prompt", "system", "assistant", "user", "question", "answer", "source_quote", "mnemonic", "tags",
    "json", "валидный", "компактный", "краткий", "коротким", "хэштегов", "хештегов", "ключевой",
}


_MORPH_ANALYZER = None

def _morph():
    global _MORPH_ANALYZER
    if _MORPH_ANALYZER is not None:
        return _MORPH_ANALYZER
    if pymorphy3 is None:
        return None
    try:
        _MORPH_ANALYZER = pymorphy3.MorphAnalyzer()
    except Exception:
        _MORPH_ANALYZER = False
    return _MORPH_ANALYZER if _MORPH_ANALYZER is not False else None

def _ru_lemma_for_tag(word: str) -> str:
    raw = (word or "").strip().lower().replace("ё", "е")
    if not re.search(r"[а-я]", raw):
        return raw
    morph = _morph()
    if not morph:
        return raw
    try:
        parsed = morph.parse(raw)[0]
        pos = parsed.tag.POS
        if pos in {"VERB", "INFN", "PRTF", "PRTS", "GRND", "NPRO", "PREP", "CONJ", "PRCL", "INTJ"}:
            return ""
        if pos in {"ADJF", "ADJS"}:
            return ""
        if pos not in {"NOUN", "NUMR", "LATN"}:
            return raw
        lemma = str(parsed.normal_form or raw).replace("ё", "е")
        return lemma
    except Exception:
        return raw

_RU_LIGHT_ENDINGS = (
    "иями", "ями", "ами", "его", "ого", "ему", "ому", "ыми", "ими", "иях", "ах", "ях",
    "ов", "ев", "ёв", "ам", "ям", "ою", "ею", "ия", "ие", "ий", "ый", "ой", "ая", "яя", "ое", "ее",
    "а", "я", "ы", "и", "е", "у", "ю", "ом", "ем", "ой", "ей", "ого", "его", "ых", "их",
)



def normalize_tag_word(word: str) -> str:
    word = (word or "").strip().lower().replace("ё", "е")
    word = re.sub(r"^[^0-9a-zа-я_-]+|[^0-9a-zа-я_-]+$", "", word, flags=re.I)
    word = re.sub(r"[^0-9a-zа-я_-]", "", word, flags=re.I)
    word = re.sub(r"_+", "_", word).strip("_")
    if not word or len(word) < 2 or len(word) > 28:
        return ""
    if word in _RU_STOPWORDS or word in _TAG_BLACKLIST:
        return ""
    if re.fullmatch(r"\d+", word):
        return ""
    if re.search(r"\d", word) and not re.search(r"[a-zа-я]{2,}", word, flags=re.I):
        return ""
    def bad_part(part: str) -> bool:
        if part in _RU_STOPWORDS or part in _TAG_BLACKLIST or len(part) < 2:
            return True
        if re.search(r"[а-я]", part) and part.endswith(_VERB_LIKE_ENDINGS_RU):
            return True
        if re.search(r"[а-я]", part) and len(part) > 7 and re.search(r"(вш|ющ|емы|имы|ован|ирован|иваем|ываем)", part):
            return True
        return False
    if bad_part(word):
        return ""
    if "_" in word:
        parts = [p for p in word.split("_") if p]
        if len(parts) > 2:
            return ""
        if any(bad_part(p) for p in parts):
            return ""
        if parts[0] == parts[-1]:
            return ""
        if not all(re.fullmatch(r"[a-zа-я0-9-]{2,18}", p, flags=re.I) for p in parts):
            return ""
    return word[:28]


def _split_tag_input(value: str) -> List[str]:
    value = str(value or "").strip()
    if not value:
        return []
    if re.search(r"[,;#\n]", value):
        chunks = [x.strip() for x in re.split(r"[,;#\n]+", value) if x.strip()]
    else:
        chunks = [x.strip() for x in value.split() if x.strip()]
    result = []
    for chunk in chunks:
        words = [w for w in re.findall(r"[A-Za-zА-Яа-яЁё0-9-]{2,}", chunk) if w]
        if not words:
            continue
        result.append("_".join(words[:2]) if len(words) > 1 else words[0])
    return result

def normalize_tags_string(value: str, max_tags: int = 24) -> str:
    tags = []
    seen = set()
    for raw in _split_tag_input(value):
        tag = normalize_tag_word(raw)
        if tag and tag not in seen:
            seen.add(tag)
            tags.append(tag)
            if len(tags) >= max_tags:
                break
    return " ".join(tags)


def _light_stem_ru(word: str) -> str:
    w = normalize_tag_word(word)
    if not w or re.search(r"[a-z]", w):
        return w
    for ending in _RU_LIGHT_ENDINGS:
        if len(w) > len(ending) + 5 and w.endswith(ending):
            stem = w[: -len(ending)]
            return stem if len(stem) >= 4 else w
    return w


def _is_good_display_word(raw: str) -> str:
    word = (raw or "").strip().lower().replace("ё", "е")
    word = re.sub(r"^[^0-9a-zа-я-]+|[^0-9a-zа-я-]+$", "", word, flags=re.I)
    word = re.sub(r"[^0-9a-zа-я-]", "", word, flags=re.I)
    word = _ru_lemma_for_tag(word)
    word = normalize_tag_word(word)
    if not word or len(word) < 3 or len(word) > 24:
        return ""
    if word in _RU_STOPWORDS or word in _TAG_BLACKLIST:
        return ""
    if re.fullmatch(r"\d+", word):
        return ""
    if re.search(r"[а-я]", word) and word.endswith(_VERB_LIKE_ENDINGS_RU):
        return ""
    if re.search(r"[а-я]", word) and len(word) > 7 and re.search(r"(вш|ющ|емы|имы|ован|ирован)", word):
        return ""
    return word


def _candidate_phrases(text_value: str) -> List[Tuple[str, float]]:
    text = normalize_text(text_value or "", max_chars=32000).replace("ё", "е")
    text = re.sub(r"https?://\S+|www\.\S+", " ", text, flags=re.I)
    text = re.sub(r"#[\w\-А-Яа-яЁё]+", " ", text)
    raw_tokens = re.findall(r"[A-Za-zА-Яа-яЁё][A-Za-zА-Яа-яЁё0-9-]{2,}", text)[:7000]
    tokens: List[Tuple[str, str, int]] = []
    for i, raw in enumerate(raw_tokens):
        norm = _is_good_display_word(raw)
        if not norm:
            continue
        tokens.append((norm, raw, i))

    freq: Dict[str, int] = {}
    first: Dict[str, int] = {}
    for norm, _raw, pos in tokens:
        freq[norm] = freq.get(norm, 0) + 1
        first.setdefault(norm, pos)

    scores: Dict[str, float] = {}
    first_seen: Dict[str, int] = {}

    def add(tag: str, score: float, pos: int) -> None:
        tag = normalize_tag_word(tag)
        if not tag:
            return
        parts = tag.split("_")
        if len(parts) > 2:
            return
        if any(_looks_like_bad_tag_word(p) for p in parts):
            return
        # фразы вида "случайное_слово" почти всегда хуже одиночного понятия
        if len(parts) == 2:
            if parts[0] == parts[1]:
                return
            if len(parts[0]) < 4 or len(parts[1]) < 4:
                return
        scores[tag] = scores.get(tag, 0.0) + score
        first_seen.setdefault(tag, pos)

    for norm, _raw, pos in tokens:
        add(norm, 1.6, pos)

    # Акронимы/латинские термины важны для наук и IT, но не превращаем всё в uppercase-теги.
    for i, raw in enumerate(raw_tokens[:3000]):
        if re.fullmatch(r"[A-ZА-ЯЁ][A-Za-zА-Яа-яЁё0-9-]{2,14}", raw) and raw.lower() not in _RU_STOPWORDS:
            add(raw.lower().replace("ё", "е"), 2.2, i)

    # Биграммы оставляем только если слова идут рядом и оба не похожи на служебные/глагольные.
    for idx in range(len(tokens) - 1):
        a, _araw, apos = tokens[idx]
        b, _braw, _bpos = tokens[idx + 1]
        if a == b:
            continue
        if freq.get(a, 0) == 1 and freq.get(b, 0) == 1 and apos > 80:
            continue
        add(f"{a}_{b}", 1.15 + min(freq.get(a,0), freq.get(b,0)) * 0.25, apos)

    result: List[Tuple[str, float]] = []
    for tag, score in scores.items():
        parts = tag.split("_")
        early = 1.2 if first_seen.get(tag, 99999) < 80 else 0.0
        repetition = min(3.0, sum(freq.get(p, 0) for p in parts) / max(1, len(parts)))
        length_penalty = 0.45 if len(parts) == 2 else 0.0
        # Одиночные понятия предпочтительнее фраз-обрывков.
        result.append((tag, score + early + repetition - length_penalty))
    return result


def _add_tag_candidate(chosen: List[str], seen: set, tag: str, max_tags: int) -> bool:
    tag = normalize_tag_word(tag)
    if not tag or tag in seen:
        return False
    parts = tag.split("_")
    for existing in list(chosen):
        eparts = existing.split("_")
        if tag == existing:
            return False
        if len(parts) == 1 and tag in eparts:
            return False
        if len(eparts) == 1 and existing in parts:
            chosen.remove(existing)
            seen.discard(existing)
    seen.add(tag)
    chosen.append(tag)
    return len(chosen) >= max_tags



_keybert_model_cache = None

def _smart_keybert_enabled(mode: str | None = None) -> bool:
    mode = (mode or TAG_EXTRACTION_MODE or "auto").strip().lower()
    return mode in {"smart", "keybert", "hybrid", "strict", "quality"}

def _get_keybert_model(mode: str | None = None):
    global _keybert_model_cache
    if KeyBERT is None or not _smart_keybert_enabled(mode):
        return None
    if _keybert_model_cache is not None:
        return _keybert_model_cache
    # Heavy optional dependency. Do not make startup depend on it.
    try:
        model_name = os.environ.get("AIFC_KEYBERT_MODEL", "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2")
        _keybert_model_cache = KeyBERT(model_name)
        print(f"[TAGS] KeyBERT enabled: {model_name}")
    except Exception as e:
        print(f"[TAGS] KeyBERT недоступен, fallback на YAKE/Natasha: {e}")
        _keybert_model_cache = False
    return None if _keybert_model_cache is False else _keybert_model_cache

def _keybert_keyphrases(text_value: str, max_tags: int = 8, language: str = "ru", mode: str | None = None) -> List[str]:
    text_value = normalize_text(text_value or "", max_chars=18000)
    if len(text_value) < 180:
        return []
    model = _get_keybert_model(mode)
    if model is None:
        return []
    try:
        raw = model.extract_keywords(
            text_value,
            keyphrase_ngram_range=(1, 2),
            stop_words=None,
            top_n=max(max_tags * 4, 12),
            use_mmr=True,
            diversity=0.62,
        )
    except Exception as e:
        print(f"[TAGS] KeyBERT extraction error: {e}")
        return []
    out: List[str] = []
    seen = set()
    for phrase, _score in raw:
        words = []
        for w in re.findall(r"[A-Za-zА-Яа-яЁё][A-Za-zА-Яа-яЁё0-9-]{2,}", clean_card_text(str(phrase or ""), 96))[:2]:
            norm = _is_good_display_word(w)
            if norm:
                words.append(norm)
        tag = normalize_tag_word("_".join(words))
        if not tag or tag in seen:
            continue
        if any(_looks_like_bad_tag_word(p) for p in tag.split("_")):
            continue
        seen.add(tag)
        out.append(tag)
        if len(out) >= max_tags:
            break
    return out

def _hybrid_keyphrases(text_value: str, max_tags: int = 8, language: str = "ru", mode: str | None = None) -> List[str]:
    """Fast tag hints for generation.

    Default path stays light: YAKE + morphology/candidates.
    Smart mode can add KeyBERT embeddings when installed, without making it a hard dependency.
    """
    chosen: List[str] = []
    seen = set()
    sources: List[str] = []
    if _smart_keybert_enabled(mode):
        sources.extend(_keybert_keyphrases(text_value, max_tags=max_tags, language=language, mode=mode))
    sources.extend(_library_keyphrases(text_value, max_tags=max_tags * 2, language=language))
    for phrase, _score in sorted(_candidate_phrases(text_value), key=lambda kv: (-kv[1], kv[0])):
        sources.append(phrase)
        if len(sources) > max_tags * 6:
            break
    for tag in sources:
        if _add_tag_candidate(chosen, seen, tag, max_tags):
            break
    return chosen[:max_tags]

def _library_keyphrases(text_value: str, max_tags: int = 8, language: str = "ru") -> List[str]:
    text_value = normalize_text(text_value or "", max_chars=32000)
    if not text_value.strip() or yake is None:
        return []
    try:
        extractor = yake.KeywordExtractor(lan=language, n=2, top=max_tags * 4, dedupLim=0.86, windowsSize=1)
        phrases = extractor.extract_keywords(text_value)
    except Exception:
        return []
    out: List[str] = []
    seen = set()
    for phrase, _score in phrases:
        words = []
        for w in re.findall(r"[A-Za-zА-Яа-яЁё][A-Za-zА-Яа-яЁё0-9-]{2,}", clean_card_text(str(phrase or ""), 80))[:2]:
            norm = _is_good_display_word(w)
            if norm:
                words.append(norm)
        raw = "_".join(words)
        tag = normalize_tag_word(raw)
        if not tag or tag in seen:
            continue
        if any(_looks_like_bad_tag_word(p) for p in tag.split("_")):
            continue
        seen.add(tag)
        out.append(tag)
        if len(out) >= max_tags:
            break
    return out

def derive_global_tags(text_value: str, existing_tags: str = "", max_tags: int = 5, tag_extraction_mode: str | None = None) -> str:
    text_value = normalize_text(text_value or "", max_chars=32000)
    text_low = text_value.lower().replace("ё", "е")
    chosen: List[str] = []
    seen = set()

    for t in normalize_tags_string(existing_tags, max_tags=80).split():
        needle = t.replace("_", " ")
        if re.search(rf"\b{re.escape(needle)}\w*\b", text_low, flags=re.I):
            if _add_tag_candidate(chosen, seen, t, max_tags):
                return " ".join(chosen[:max_tags])

    for phrase in _hybrid_keyphrases(text_value, max_tags=max_tags, language="ru", mode=tag_extraction_mode):
        if _add_tag_candidate(chosen, seen, phrase, max_tags):
            break
    return " ".join(chosen[:max_tags])


def merge_tags(*parts: str, max_tags: int = 8) -> str:
    merged = []
    seen = set()
    for part in parts:
        for tag in normalize_tags_string(part or "", max_tags=80).split():
            if _add_tag_candidate(merged, seen, tag, max_tags):
                return " ".join(merged)
    return " ".join(merged)


def derive_card_tags(card: dict, source_tags: str = "", max_tags: int = 4, tag_extraction_mode: str | None = None) -> str:
    text_value = " ".join([
        clean_card_text(card.get("front", ""), 240),
        clean_card_text(card.get("back", ""), 420),
        clean_card_text(card.get("source_quote", ""), 360),
    ])
    text_low = text_value.lower().replace("ё", "е")
    chosen: List[str] = []
    seen = set()
    limit = max(2, min(4, int(max_tags or 3)))

    for raw in normalize_tags_string(source_tags, max_tags=24).split():
        needle = raw.replace("_", " ")
        if re.search(rf"\b{re.escape(needle)}\w*\b", text_low, flags=re.I):
            _add_tag_candidate(chosen, seen, raw, limit)
            if len(chosen) >= min(2, limit):
                break

    for phrase in _hybrid_keyphrases(text_value, max_tags=limit, language="ru", mode=tag_extraction_mode):
        if _add_tag_candidate(chosen, seen, phrase, limit):
            break

    return " ".join(chosen[:limit])


def safe_target_card_count(text_value: str, requested: int, manual_count: bool = False) -> int:
    requested = clamp_generation_count(requested)
    if manual_count:
        # Manual UI selection is authoritative: the backend target remains the
        # user's selected number. The pipeline may split the run into smaller
        # LiteRT prompts, but it must not silently reduce the final count.
        return requested
    words = len(re.findall(r"[A-Za-zА-Яа-яЁё0-9]+", text_value or ""))
    if words <= 18:
        return min(requested, 1)
    if words <= 45:
        return min(requested, 2)
    if words <= 90:
        return min(requested, 3)
    if words <= 180:
        return min(requested, 5)
    reasonable = max(6, min(GENERATION_MAX_CARD_COUNT, math.ceil(words / GENERATION_AUTO_WORDS_PER_CARD)))
    return min(requested, reasonable)


def _fallback_question_from_sentence(sentence: str, language: str = "ru") -> str:
    snt = normalize_text(sentence, 240)
    if language == "en":
        if " is " in snt.lower():
            left = re.split(r"\bis\b", snt, flags=re.I)[0].strip(" —-:,.()")[:80]
            return f"What is {left}?" if left else "What is the key idea?"
        return "What does this fragment explain?"
    # Термин — определение
    if "—" in snt or " - " in snt:
        left = re.split(r"\s+[—-]\s+", snt, maxsplit=1)[0].strip(" .,:;()«»")[:90]
        if 2 <= len(left.split()) <= 8:
            return f"Что такое {left}?"
    m = re.search(r"\b([А-ЯA-ZЁ][A-Za-zА-Яа-яЁё0-9\- ]{2,70})\s+(является|считается|называется|представляет собой)\b", snt)
    if m:
        return f"Что такое {m.group(1).strip()}?"
    low = snt.lower()
    number_match = re.search(r"состав(?:лял[оа]?|ляет|или)\s+([0-9][0-9\s.,]*)", low)
    if number_match:
        if re.search(r"люд|человек|избирател|населен", low):
            return "Сколько людей указано в этом фрагменте?"
        return "Какое число указано в этом фрагменте?"
    if re.search(r"\bне\s+имеет\b", low):
        subject0 = clean_card_text(_extract_subject_phrase(snt), 80).strip(" .,:;«»") if '_extract_subject_phrase' in globals() else ""
        if subject0 and not _generic_subject(subject0):
            rest = re.sub(r"^" + re.escape(subject0), "", snt, flags=re.I).strip(" .,:;—-")[:80]
            return f"Что отсутствует у «{subject0}»?" if not rest else f"Имеет ли «{subject0}» {rest}?"
    subject = clean_card_text(_extract_subject_phrase(snt), 80).strip(" .,:;«»") if '_extract_subject_phrase' in globals() else ""
    if subject:
        if re.search(r"\b(содержит|состоит|состав|%)\b", low):
            return f"Что входит в состав «{subject}»?"
        if re.search(r"\b(использ|служит|являл|примен)\w*", low):
            return f"Как используется «{subject}»?"
        return f"Что такое {subject}?"
    return "Какой главный факт объясняет этот фрагмент?"


def build_fallback_cards(text_value: str, needed: int, avoid_fronts: Optional[List[str]] = None, language: str = "ru") -> List[dict]:
    avoid = {normalize_text(x, 120).lower() for x in (avoid_fronts or []) if x}
    needed = max(0, int(needed or 0))
    if needed <= 0:
        return []
    result: List[dict] = []
    text_value = normalize_text(text_value or "", max_chars=24000)

    for snt in sentence_units(text_value):
        snt = clean_card_text(re.sub(r"#[\w\-А-Яа-яЁё]+", "", snt), 420)
        if not is_useful_sentence(snt) or is_artifact_text(snt):
            continue
        q = repair_question(_fallback_question_from_sentence(snt, language), snt, snt, language)
        if is_low_quality_card({"front": q, "back": snt, "source_quote": snt, "mnemonic": ""}):
            continue
        qkey = q.lower()
        if qkey in avoid:
            continue
        answer = snt[:260].rstrip(" ,;:") + ("…" if len(snt) > 260 else "")
        card = {
            "front": q,
            "back": answer,
            "source_quote": snt[:500],
            "mnemonic": build_mnemonic(q, answer),
            "card_type": infer_card_type(q, answer, snt),
        }
        if not is_prompt_artifact_card(card) and not is_low_quality_card(card):
            result.append(card)
            avoid.add(qkey)
        if len(result) >= needed:
            break
    return result

_STYLIZED_TRANSLATION = str.maketrans({
    "ᴛ": "т", "ᴛ": "т", "ᴀ": "а", "ᴏ": "о", "ᴄ": "с", "ᴇ": "е", "ʙ": "в", "ᴧ": "л",
    "϶": "э", "ᴘ": "р", "ʀ": "р", "ɴ": "н", "ᴍ": "м", "ᴋ": "к", "ʏ": "у",
})


def clean_card_text(value: str, max_chars: int = 1200) -> str:
    """Чистит markdown/странный unicode из PDF и ответов модели."""
    value = unicodedata.normalize("NFKC", str(value or ""))
    value = "".join(ch for ch in value if unicodedata.category(ch) != "Mn")
    value = value.translate(_STYLIZED_TRANSLATION)
    value = re.sub(r"^[\s#*_`>\-•·]+", "", value)
    value = re.sub(r"^\s*\d+[.)]\s*", "", value)
    value = re.sub(r"^\s*(глава|раздел|пункт)\s+\d+[:.)\s-]*", "", value, flags=re.I)
    value = re.sub(r"\s*[#*_`]{1,}\s*", " ", value)
    value = re.sub(r"[🚫✅⚠️📌🔥⚡🧠🧬]+\s*", "", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value[:max_chars]


def looks_generic_question(front: str) -> bool:
    low = clean_card_text(front, 400).lower()
    return (
        low.startswith("что важно знать про")
        or low.startswith("что важно знать о")
        or low.startswith("что важно знать по")
        or low.startswith("what is important about")
        or low.startswith("что говорится о теме")
        or low.startswith("какой ключевой факт")
        or low.startswith("что нужно запомнить")
        or bool(re.search(r"\bпро\s+[0-9]+\s+[а-яa-z]\s+[а-яa-z]\s+[а-яa-z]\??$", low))
    )


def _answer_is_question(value: str) -> bool:
    v = clean_card_text(value, 220)
    return v.endswith("?") or bool(re.match(r"^(что|кто|когда|почему|как|какой|какая|какие|чем|где)\b", v.lower()))



def _extract_subject_phrase(text_value: str, max_words: int = 5) -> str:
    value = clean_card_text(text_value or "", 700)
    if "—" in value or " - " in value:
        left = re.split(r"\s+[—-]\s+", value, maxsplit=1)[0].strip(" .,:;()«»")
        words = re.findall(r"[A-Za-zА-Яа-яЁё0-9\-]+", left)
        if 1 <= len(words) <= max_words:
            return " ".join(words)
    quoted = re.findall(r"[«\"]([^»\"]{3,80})[»\"]", value)
    for q in quoted:
        words = re.findall(r"[A-Za-zА-Яа-яЁё0-9\-]+", q)
        if 1 <= len(words) <= max_words:
            return " ".join(words)
    m = re.search(r"\b([A-Za-zА-Яа-яЁё0-9\- ]{3,90})\s+(является|считается|называется|представляет собой|служит|обозначает|означает|is|are|means|refers to)\b", value, flags=re.I)
    if m:
        words = re.findall(r"[A-Za-zА-Яа-яЁё0-9\-]+", m.group(1))
        words = [w for w in words if normalize_tag_word(w)]
        if words:
            return " ".join(words[-max_words:])
    candidates = _candidate_phrases(value)
    if candidates:
        best = sorted(candidates, key=lambda kv: (-kv[1], kv[0]))[0][0]
        return best.replace("_", " ")
    words = [w for w in re.findall(r"[A-Za-zА-Яа-яЁё0-9\-]+", value) if normalize_tag_word(w)]
    return " ".join(words[:max_words])


def _primary_topic_from_tags(tags: str) -> str:
    for tag in normalize_tags_string(tags or "", max_tags=6).split():
        clean = tag.replace("_", " ").strip()
        if clean and clean not in _RU_STOPWORDS and clean not in _TAG_BLACKLIST:
            return clean
    return ""


def _generic_subject(subject: str) -> bool:
    s = normalize_tag_word(subject.replace(" ", "_")) or subject.lower().replace("ё", "е").strip()
    words = [normalize_tag_word(w) for w in re.findall(r"[A-Za-zА-Яа-яЁё0-9-]+", subject)]
    words = [w for w in words if w]
    if not subject or subject.lower().replace("ё", "е") in {"его", "ее", "её", "они", "оно", "это", "задолго"}:
        return True
    if _bad_definition_subject(subject):
        return True
    if s in _RU_STOPWORDS or s in _TAG_BLACKLIST:
        return True
    if len(words) == 1 and (words[0] in _RU_STOPWORDS or words[0] in _TAG_BLACKLIST):
        return True
    if len(words) > 5:
        return True
    return False


def _topic_from_context(context: str, hint: str = "") -> str:
    low = (context or "").lower().replace("ё", "е")
    hint_topic = _primary_topic_from_tags(hint)
    if hint_topic and re.search(rf"\b{re.escape(hint_topic.lower().replace('ё','е'))}\w*\b", low, flags=re.I):
        return hint_topic
    subject = clean_card_text(_extract_subject_phrase(context), 90).strip(" .,:;«»")
    if not _generic_subject(subject):
        return subject
    candidates = [tag.replace("_", " ") for tag, _score in sorted(_candidate_phrases(context), key=lambda kv: (-kv[1], kv[0]))[:5]]
    for cand in candidates:
        if not _generic_subject(cand):
            return cand
    return hint_topic or subject


def repair_question(front: str, back: str, quote: str = "", language: str = "ru", topic_hint: str = "") -> str:
    f = clean_card_text(front, 360).rstrip(" .")
    b = clean_card_text(back or quote, 900)
    q = clean_card_text(quote, 900)
    context = b or q or f

    banned = (
        "что важно знать", "что говорится о теме", "какой ключевой факт",
        "что нужно запомнить", "что такое задолго", "что говорится про",
        "что такое количество", "что такое цель", "что такое основание",
    )
    low_f = f.lower().replace("ё", "е")
    bad_subject_match = re.match(r"^что\s+такое\s+(.+?)\?*$", low_f, flags=re.I)
    if f and f.endswith("?") and not any(low_f.startswith(x) for x in banned) and not re.search(r"\b[0-9]\s+[а-яa-z]\s+[а-яa-z]\b", low_f) and not (bad_subject_match and _bad_definition_subject(bad_subject_match.group(1))):
        return f

    if _answer_is_question(b) and not looks_generic_question(b) and len(b) <= 180:
        return b if b.endswith("?") else b + "?"

    subject = _topic_from_context(context, topic_hint)
    subject = clean_card_text(subject, 70).strip(" .,:;«»?")
    subject_q = f"«{subject}»" if subject and len(subject.split()) <= 5 else subject

    if language == "en":
        low_en = context.lower()
        if subject_q:
            if re.search(r"\b(contains|consists|composition|percent|%)\b", low_en):
                return f"What does {subject} contain?"
            if re.search(r"\b(produced|made|processed|formed)\b", low_en):
                return f"How is {subject} produced?"
            if re.search(r"\b(used|serves|applied|ingredient|diet)\b", low_en):
                return f"How is {subject} used?"
            if re.search(r"\b(risk|allergen|avoid|fake|counterfeit|danger)\b", low_en):
                return f"What risks or limitations are associated with {subject}?"
            return f"What is {subject}?"
        return _fallback_question_from_sentence(context, language="en")

    low = context.lower().replace("ё", "е")
    if subject_q:
        if re.search(r"\b(содержит|состо[ияи]т|состав|углевод|процент|%)\b", low):
            return f"Из чего состоит {subject_q}?"
        if re.search(r"\b(вырабатыва|получа|производ|образу|созда|переработ)\w*", low):
            return f"Как получают или вырабатывают {subject_q}?"
        if re.search(r"\b(использ|примен|служит|ингредиент|подсластител|потребля|рацион|кухн)\w*", low):
            return f"Как используется {subject_q}?"
        if re.search(r"\b(опас|аллерген|следует избегать|риск|вред|поддел|фальсифиц)\b", low):
            return f"Какие риски или ограничения связаны с {subject_q}?"
        if re.search(r"\b(когда|год|век|дата|период|раньше|истори)\b", low):
            return f"Каков исторический контекст {subject_q}?"
        if re.search(r"\b(роль|функц|значени|позволя|основн)\w*", low):
            return f"Какую роль играет {subject_q}?"
        if re.search(r"\b(означает|обозначает|называется|термин|понятие|представляет собой|является|это|—)\b", low):
            return f"Что такое {subject_q}?"
        return f"Какой факт указан о {subject_q}?"

    if b.endswith("?") and len(b) < 140:
        return b
    return _fallback_question_from_sentence(context, language="ru")


def clean_mnemonic_text(value: str, max_chars: int = 360) -> str:
    value = clean_card_text(value or "", max_chars)
    return sanitize_mnemonic(value)[:max_chars]


def build_mnemonic(question: str, answer: str, tags: str = "") -> str:
    answer_clean = clean_card_text(answer, 220)
    if not answer_clean:
        return ""
    key = answer_clean[:112].rstrip(" .,:;—-")
    return clean_mnemonic_text(key)




def infer_card_type(front: str, back: str = "", quote: str = "") -> str:
    text = " ".join([front or "", back or "", quote or ""]).lower().replace("ё", "е")
    q = clean_card_text(front or "", 320).lower().replace("ё", "е")
    a = clean_card_text(back or "", 500)
    if "{{c" in text or "cloze" in text:
        return "cloze"
    if _looks_like_mcq(front, back) if "_looks_like_mcq" in globals() else False:
        return "mcq"
    if re.match(r"^(верно или неверно|верно ли|правда ли|является ли|можно ли|true or false)\b", q):
        return "true_false"
    if re.match(r"^(что такое|кто такой|кто такая|что называется|что представляет собой|дайте определение|what is|what are|define)\b", q):
        return "definition"
    if re.search(r"\b(почему|зачем|как влияет|как работает|как происходит|каким образом|чем объясняется|какую роль|какова роль|чем отличается|в чем связь|why|how|role|mechanism|difference)\b", q):
        return "concept"
    if len(a.split()) <= 8 and re.search(r"\b(какой|какая|какое|какие|когда|сколько|где|кто|что содержит|what|when|where|who|how many)\b", q):
        return "fact"
    return "basic"


ALLOWED_GENERATED_CARD_TYPES = {"basic", "definition", "fact", "concept", "cloze", "true_false", "mcq"}
CARD_TYPE_SCHEMA_RU = {
    "basic": '{"front":"вопрос?","back":"ответ","source_quote":"цитата","card_type":"basic"}',
    "definition": '{"front":"Что такое термин?","back":"краткое определение","source_quote":"цитата","card_type":"definition"}',
    "fact": '{"front":"Какой факт указан?","back":"короткий факт","source_quote":"цитата","card_type":"fact"}',
    "concept": '{"front":"Почему/как работает явление?","back":"объяснение причины или связи","source_quote":"цитата","card_type":"concept"}',
    "cloze": '{"front":"Текст с {{c1::пропуском}}.","back":"полная фраза или пояснение","source_quote":"цитата","card_type":"cloze"}',
    "true_false": '{"front":"Верно или неверно: утверждение?","back":"Верно/Неверно. Краткое пояснение.","source_quote":"цитата","card_type":"true_false"}',
    "mcq": '{"front":"Вопрос?\\nA) вариант\\nB) вариант\\nC) вариант\\nD) вариант","back":"Правильный ответ: A) вариант. Краткое пояснение.","source_quote":"цитата","card_type":"mcq"}',
}
CARD_TYPE_SCHEMA_EN = {
    "basic": '{"front":"question?","back":"answer","source_quote":"quote","card_type":"basic"}',
    "definition": '{"front":"What is the term?","back":"short definition","source_quote":"quote","card_type":"definition"}',
    "fact": '{"front":"What fact is stated?","back":"short fact","source_quote":"quote","card_type":"fact"}',
    "concept": '{"front":"Why/how does the concept work?","back":"cause or relationship explanation","source_quote":"quote","card_type":"concept"}',
    "cloze": '{"front":"Sentence with {{c1::blank}}.","back":"full sentence or explanation","source_quote":"quote","card_type":"cloze"}',
    "true_false": '{"front":"True or false: statement?","back":"True/False. Short explanation.","source_quote":"quote","card_type":"true_false"}',
    "mcq": '{"front":"Question?\\nA) option\\nB) option\\nC) option\\nD) option","back":"Correct answer: A) option. Short explanation.","source_quote":"quote","card_type":"mcq"}',
}
CARD_TYPE_RULES_RU = {
    "basic": "basic: обычная карточка вопрос/ответ; front обязательно вопрос; back краткий ответ.",
    "definition": "definition: спрашивай определение конкретного термина; front начинается с 'Что такое...' или 'Что называется...'.",
    "fact": "fact: спрашивай один короткий факт; back короткий, без длинного пересказа.",
    "concept": "concept: спрашивай причинно-следственную связь, роль, механизм, отличие, смысл; back объясняет.",
    "cloze": "cloze: front содержит {{c1::...}}; скрывай ключевой термин/число/связь; back даёт полную фразу или пояснение.",
    "true_false": "true_false: front имеет форму 'Верно или неверно: ...?'; back начинается с 'Верно.' или 'Неверно.' и даёт пояснение.",
    "mcq": "mcq: front содержит вопрос и 3-4 варианта A), B), C), D); back начинается с 'Правильный ответ: ...' и даёт пояснение. Варианты должны быть в front, не отдельными JSON-полями.",
}
CARD_TYPE_RULES_EN = {
    "basic": "basic: normal question/answer card; front must be a question; back is concise.",
    "definition": "definition: ask for a concrete term definition; front starts with 'What is...' or 'What is called...'.",
    "fact": "fact: ask one short factual detail; back is short, no long retelling.",
    "concept": "concept: ask about a cause, mechanism, role, relation, or difference; back explains.",
    "cloze": "cloze: front contains {{c1::...}}; hide a key term/number/relation; back gives full sentence or explanation.",
    "true_false": "true_false: front starts with 'True or false: ...?'; back starts with 'True.' or 'False.' and explains.",
    "mcq": "mcq: front contains question and 3-4 choices A), B), C), D); back starts with 'Correct answer: ...' and explains. Choices stay inside front, not separate JSON fields.",
}

def normalize_generated_card_type(value: str, default: str = "auto") -> str:
    value = str(value or default or "auto").strip().lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "qa": "basic", "q_a": "basic", "question_answer": "basic", "вопрос_ответ": "basic", "вопрос/ответ": "basic",
        "def": "definition", "определение": "definition",
        "факт": "fact", "понимание": "concept", "conceptual": "concept",
        "пропуск": "cloze", "gap": "cloze",
        "truefalse": "true_false", "true/false": "true_false", "верно_неверно": "true_false", "верно/неверно": "true_false",
        "multiple_choice": "mcq", "choice": "mcq", "выбор_ответа": "mcq", "тест": "mcq",
    }
    value = aliases.get(value, value)
    if value in ALLOWED_GENERATED_CARD_TYPES or value in {"auto", "mixed", ""}:
        return "auto" if value in {"", "mixed"} else value
    return default if default in ALLOWED_GENERATED_CARD_TYPES or default == "auto" else "auto"

AUTO_CARD_TYPE_SCHEMA_RU = '{"front":"вопрос","back":"ответ","source_quote":"цитата","card_type":"basic|definition|fact|concept|cloze|true_false|mcq"}'
AUTO_CARD_TYPE_SCHEMA_EN = '{"front":"question","back":"answer","source_quote":"quote","card_type":"basic|definition|fact|concept|cloze|true_false|mcq"}'

AUTO_CARD_TYPE_RULES_RU = """AUTO card_type: выбери лучший тип для каждой карточки по смыслу источника. fact НЕ default. Не делай все карточки fact, если в тексте есть термины, причины, механизмы, проверяемые утверждения или места для пропуска. Если текст позволяет, сделай смесь разных типов: definition/fact/concept/cloze/true_false/mcq. Не выводи literal 'basic|definition|fact|concept|cloze|true_false|mcq'; выбери один тип.
Типы:
- basic: обычный вопрос/ответ, когда тип не подходит точнее.
- definition: термин/объект/понятие; вопрос 'Что такое...' или 'Что называется...'.
- fact: конкретный факт, дата, число, свойство, короткое утверждение.
- concept: причина, механизм, роль, связь, отличие; вопрос 'почему/как/какую роль/чем отличается'.
- cloze: фраза с {{c1::ключевым термином/числом}}. Только если пропуск реально помогает.
- true_false: проверяемое утверждение; front 'Верно или неверно: ...?', back 'Верно.' или 'Неверно.' + пояснение.
- mcq: выбор ответа; только если можно дать 4 нормальных варианта A), B), C), D). Варианты внутри front. back начинается 'Правильный ответ: ...'."""

AUTO_CARD_TYPE_RULES_EN = """AUTO card_type: choose the best type for each card from the source meaning. fact is NOT the default. Do not make every card fact if the text contains terms, causes, mechanisms, checkable claims, or cloze-worthy phrases. If the source allows it, mix different types: definition/fact/concept/cloze/true_false/mcq. Do not output literal 'basic|definition|fact|concept|cloze|true_false|mcq'; choose one type.
Types:
- basic: normal question/answer when no more specific type fits.
- definition: term/object/concept; question starts 'What is...' or 'What is called...'.
- fact: concrete fact, date, number, property, short claim.
- concept: cause, mechanism, role, relation, difference; why/how/role/difference question.
- cloze: sentence with {{c1::key term/number}}. Only when the blank helps memorization.
- true_false: checkable claim; front 'True or false: ...?', back 'True.' or 'False.' + explanation.
- mcq: multiple choice; only when 4 good choices A), B), C), D) are possible. Choices stay inside front. back starts 'Correct answer: ...'."""

def card_type_prompt_block(card_type: str, language: str = "ru") -> tuple[str, str]:
    card_type = normalize_generated_card_type(card_type, "auto")
    if (language or "ru").lower().startswith("en"):
        schemas = CARD_TYPE_SCHEMA_EN
        rules = CARD_TYPE_RULES_EN
        if card_type == "auto":
            return (AUTO_CARD_TYPE_SCHEMA_EN, AUTO_CARD_TYPE_RULES_EN)
        return (schemas[card_type], f"All cards must have card_type='{card_type}'. Do not output another card_type. {rules[card_type]}")
    schemas = CARD_TYPE_SCHEMA_RU
    rules = CARD_TYPE_RULES_RU
    if card_type == "auto":
        return (AUTO_CARD_TYPE_SCHEMA_RU, AUTO_CARD_TYPE_RULES_RU)
    return (schemas[card_type], f"Во всех карточках card_type='{card_type}'. Не выводи другой card_type. {rules[card_type]}")

def _looks_like_mcq(front: str, back: str = "") -> bool:
    text = str(front or "")
    has_choices = all(re.search(rf"(?:^|\n|\s){letter}\)\s+", text, flags=re.I) for letter in "ABCD")
    has_answer = bool(re.search(r"(правильный ответ|correct answer)\s*:", str(back or ""), flags=re.I))
    return has_choices and has_answer

def semantic_card_type_hint(front: str, back: str = "", quote: str = "") -> str:
    text = " ".join([front or "", back or "", quote or ""]).lower().replace("ё", "е")
    q = clean_card_text(front or "", 420).lower().replace("ё", "е")
    if _looks_like_mcq(front, back):
        return "mcq"
    if "{{c" in text:
        return "cloze"
    if re.match(r"^(верно или неверно|верно ли|правда ли|является ли|можно ли|true or false)\b", q):
        return "true_false"
    if re.match(r"^(что такое|кто такой|кто такая|что называется|что представляет собой|дайте определение|what is|what are|define)\b", q):
        return "definition"
    if re.search(r"(\b[-—]\s*это\b|\bэто\s+(?:тип|вид|форма|метод|процесс|свойство|явление|система|модель|понятие|термин)\b|\bназывается\b|\bпредставляет собой\b|\bопределяется как\b|\bis defined as\b|\bis a\b|\bis an\b)", text):
        return "definition"
    if re.search(r"\b(почему|зачем|как работает|как происходит|каким образом|механизм|причин|следств|из-за|из за|потому что|поэтому|вследствие|приводит к|вызывает|обеспечивает|позволяет|зависит от|связано с|роль|отличается|why|how|because|therefore|mechanism|cause|effect|role|depends on|leads to|differs)\b", text):
        return "concept"
    if re.search(r"\b(\d{3,4}|\d+[,.]?\d*\s*(?:%|км|м|см|мм|кг|г|мг|байт|бит|сек|мин|час|лет|год|года|годы|°c|mb|gb|kg|km|cm|mm|years?))\b", text):
        return "fact"
    if re.search(r"\b(когда|сколько|где|кто|какой|какая|какое|какие|when|where|who|how many|which)\b", q):
        return "fact"
    return "basic"


def infer_card_type_auto(front: str, back: str = "", quote: str = "", raw_type: str = "") -> str:
    raw = normalize_generated_card_type(raw_type or "auto", "auto")
    inferred = infer_card_type(front, back, quote)
    hint = semantic_card_type_hint(front, back, quote)
    if hint != "basic":
        inferred = hint
    text = " ".join([front or "", back or "", quote or ""]).lower().replace("ё", "е")
    if raw == "auto":
        return inferred if inferred in ALLOWED_GENERATED_CARD_TYPES else "basic"
    if raw == "mcq" and not _looks_like_mcq(front, back):
        return inferred if inferred != "basic" else "basic"
    if raw == "cloze" and "{{c" not in text:
        return inferred if inferred != "basic" else "basic"
    if raw == "true_false" and not re.match(r"^(верно или неверно|true or false)\b", str(front or "").strip().lower().replace("ё", "е")):
        return inferred if inferred != "basic" else "basic"
    if raw in {"fact", "basic"} and inferred not in {"basic", raw}:
        return inferred
    return raw if raw in ALLOWED_GENERATED_CARD_TYPES else (inferred if inferred in ALLOWED_GENERATED_CARD_TYPES else "basic")

def generated_card_type_for(card: dict, front: str, back: str, quote: str, forced_card_type: str = "auto") -> str:
    forced = normalize_generated_card_type(forced_card_type, "auto")
    if forced != "auto":
        return forced
    raw = ""
    if isinstance(card, dict):
        raw = card.get("card_type") or card.get("type") or card.get("тип") or ""
    return infer_card_type_auto(front, back, quote, raw)

def apply_forced_card_type(card: dict, forced_card_type: str) -> dict:
    forced = normalize_generated_card_type(forced_card_type, "auto")
    if forced != "auto":
        card["card_type"] = forced
    return card


def rebalance_auto_card_types(cards: list[dict], forced_card_type: str = "auto") -> list[dict]:
    if normalize_generated_card_type(forced_card_type, "auto") != "auto" or len(cards or []) < 3:
        return cards
    types = [normalize_generated_card_type(c.get("card_type") or "basic", "basic") for c in cards]
    rich = {t for t in types if t not in {"basic", "fact"}}
    if len(rich) >= 2 and len(set(types)) >= 3:
        return cards
    for c in cards:
        hint = semantic_card_type_hint(c.get("front", ""), c.get("back", ""), c.get("source_quote", ""))
        if hint != "basic":
            c["card_type"] = hint
    return cards


def next_review_date_local(days: int) -> datetime:
    return start_of_local_day(days)


def apply_review(card: Card, rating: str) -> Card:
    result = schedule_review(
        ReviewState(
            ease_factor=float(card.ease_factor or 2.5),
            interval_days=int(card.interval_days or 0),
            review_count=int(card.review_count or 0),
            lapses=int(card.lapses or 0),
        ),
        rating,
    )
    card.ease_factor = result.ease_factor
    card.interval_days = result.interval_days
    card.review_count = result.review_count
    card.lapses = result.lapses
    card.last_reviewed_at = result.last_reviewed_at
    card.due_date = result.due_date
    card.status = result.status
    return card


def _ru_single_word_case_quality(word: str) -> str:
    token = clean_card_text(word or "", 80).strip(" .,:;!?«»").lower().replace("ё", "е")
    if not token or not re.fullmatch(r"[а-я-]+", token):
        return "ok"
    morph = _morph()
    if not morph:
        return "ok"
    try:
        parses = morph.parse(token)[:6]
    except Exception:
        return "ok"
    noun_parses = [p for p in parses if getattr(p.tag, "POS", None) in {"NOUN", "NPRO", "Abbr"}]
    if not noun_parses:
        return "bad"
    has_nominative = any(getattr(p.tag, "case", None) == "nomn" for p in noun_parses)
    best = noun_parses[0]
    best_case = getattr(best.tag, "case", None)
    normal = str(getattr(best, "normal_form", "") or token)
    if best_case not in {None, "nomn"} and normal and normal != token:
        return "bad"
    return "ok" if has_nominative or best_case is None else "bad"

def _bad_definition_subject(subject: str) -> bool:
    sub = clean_card_text(subject or "", 80).strip(" .,:;!?«»").lower().replace("ё", "е")
    if not sub or len(sub) < 3:
        return True
    words = re.findall(r"[a-zа-я0-9-]+", sub, flags=re.I)
    if not words or len(words) > 4:
        return True
    normalized_parts = [normalize_tag_word(w) for w in words]
    normalized_parts = [w for w in normalized_parts if w]
    if not normalized_parts:
        return True
    if len(words) == 1 and _ru_single_word_case_quality(words[0]) == "bad":
        return True
    if len(words) == 1 and _looks_like_bad_tag_word(words[0]):
        return True
    morph = _morph()
    if morph and len(words) == 1 and re.search(r"[а-я]", words[0]):
        try:
            parses = morph.parse(words[0])[:5]
            if not any(getattr(p.tag, "POS", None) in {"NOUN", "NPRO", "Abbr"} for p in parses):
                return True
        except Exception:
            pass
    return False


def is_low_quality_card(card: dict, output_profile: str = "anki") -> bool:
    front = clean_card_text(card.get("front", ""), 260)
    back = clean_card_text(card.get("back", ""), 800)
    quote = clean_card_text(card.get("source_quote", ""), 800)
    if not front or not back or len(back.split()) < 4:
        return True
    if is_artifact_text(front) or is_artifact_text(back) or (quote and is_artifact_text(quote)):
        return True
    if re.search(r"(?i)\b(?:converted[-_ ]?repo|file:///|c:/users|downloads|\.txt|localhost|127\.0\.0\.1)\b", " ".join([front, back, quote])):
        return True
    front_low = front.lower().replace("ё", "е")
    weak_question_patterns = [
        r"^какой\s+(?:числовой|временной|числовой\s+или\s+временной)\s+факт\s+указан\s+для\b",
        r"^какая\s+ключевая\s+мысль\s+содержится\s+во\s+фрагменте",
        r"^какой\s+факт\s+указан\s+о\s+[«\"]?[^?]{1,32}[»\"]?\??$",
        r"^каков\s+исторический\s+контекст\s+[«\"]?[^?]{1,40}[»\"]?\??$",
        r"^как\s+объяснить\s+этот\s+факт\??$",
    ]
    if any(re.match(pattern, front_low, flags=re.I) for pattern in weak_question_patterns):
        return True
    m = re.match(r"^что\s+такое\s+(.+?)\?*$", front_low, flags=re.I)
    if question_subject_is_bad(front, "ru") or (m and _bad_definition_subject(m.group(1))):
        return True
    if re.match(r"^что\s+(говорится|сказано)\s+о\s+", front.lower().replace("ё", "е")):
        return True
    if len(front.split()) <= 3 and len(back.split()) > 20:
        return True
    if quote and back and back.lower().strip(" .") == quote.lower().strip(" .") and len(back.split()) < 8:
        return True
    validated, _reason = validate_flashcard_payload({"front": front, "back": back, "source_quote": quote, "mnemonic": clean_card_text(card.get("mnemonic", ""), 360), "card_type": card.get("card_type") or "basic"})
    return validated is None


def is_prompt_artifact_card(card: dict) -> bool:
    joined_raw = " ".join([
        clean_card_text(card.get("front", ""), 260),
        clean_card_text(card.get("back", ""), 420),
        clean_card_text(card.get("source_quote", ""), 420),
    ])
    joined = joined_raw.lower().replace("ё", "е")
    if not joined:
        return True
    if is_artifact_text(joined_raw):
        return True
    artifact_markers = [
        "валидный json", "компактный json", "без markdown", "source_quote",
        "mnemonic", "хэштегов", "хештегов", "вернуть только", "answer must",
        "question must", "do not invent", "теги источника", "формат ответа",
        "поле является", "требование предъявляется", "условие применяется",
        "мнемоника должна", "цитата должна", "только подсказкой", "что такое задолго",
    ]
    hits = sum(1 for m in artifact_markers if m in joined)
    return hits >= 1



def _front_is_single_word_definition(front: str) -> bool:
    return bool(re.match(r"^что\s+такое\s+[A-Za-zА-Яа-яЁё0-9-]+\?*$", clean_card_text(front, 180).lower().replace("ё", "е"), flags=re.I))


def card_needs_fact_fallback(card: dict, draft: dict, language: str = "ru") -> bool:
    if not card:
        return True
    if is_prompt_artifact_card(card) or is_low_quality_card(card):
        return True
    front = clean_card_text(card.get("front", ""), 260)
    back = clean_card_text(card.get("back", ""), 900)
    draft_front = clean_card_text(draft.get("front", ""), 260)
    if _front_is_single_word_definition(front) and draft_front and not _front_is_single_word_definition(draft_front):
        return True
    if language == "ru" and question_subject_is_bad(front, "ru"):
        return True
    if not back or len(back.split()) < 5:
        return True
    return False


def merge_fact_card(model_card: dict | None, draft: dict, global_tags: str = "", language: str = "ru", output_profile: str = "anki") -> dict:
    if model_card:
        fixed = postprocess_generated_card(model_card, global_tags=global_tags, language=language)
        fixed["source_quote"] = clean_card_text(fixed.get("source_quote") or draft.get("source_quote") or "", 700)
        if not fixed.get("mnemonic"):
            fixed["mnemonic"] = clean_mnemonic_text(draft.get("mnemonic") or build_mnemonic(fixed.get("front", ""), fixed.get("back", ""), global_tags))
        validated, _reason = validate_flashcard_payload(fixed)
        if validated and not card_needs_fact_fallback(validated, draft, language):
            return validated
    safe = postprocess_generated_card(draft, global_tags=global_tags, language=language, output_profile=output_profile)
    validated, _reason = validate_flashcard_payload(safe)
    if validated and not card_needs_fact_fallback(validated, {}, language):
        return validated
    safe["front"] = repair_question_with_fact(safe.get("front", ""), safe.get("back", ""), safe.get("source_quote", ""), language=language)
    safe["mnemonic"] = clean_mnemonic_text(safe.get("mnemonic") or build_mnemonic(safe.get("front", ""), safe.get("back", ""), global_tags))
    validated, _reason = validate_flashcard_payload(safe)
    return validated or safe

def postprocess_generated_card(card: dict, global_tags: str = "", language: str = "ru", output_profile: str = "anki") -> dict:
    """Clean model output without authoring cards in Python.

    Earlier stages tried to repair weak questions and auto-build mnemonics with
    templates. That made fallback text look like generation. This function now
    only normalizes fields, swaps a question accidentally placed in the answer,
    trims length, and leaves quality decisions to validate_model_card.
    """
    front = clean_card_text(card.get("front") or card.get("q") or card.get("question") or "", 360)
    back = clean_card_text(card.get("back") or card.get("a") or card.get("answer") or "", 900)
    quote = clean_card_text(card.get("source_quote") or card.get("s") or card.get("quote") or "", 700)
    tags = _tags_for_generated_card(card, source_tags=global_tags, tag_extraction_mode=None, max_tags=4)
    mnemonic = _normalize_generated_mnemonic(card, tags)

    if _answer_is_question(back) and quote and not _answer_is_question(quote):
        front, back = back, quote

    # Do not synthesize a new question or a new answer here. Bad rows are rejected
    # and a model repair pass is attempted instead.
    words = back.split()
    if len(words) > 70:
        back = " ".join(words[:70]).rstrip(" ,;:—-") + "…"

    card_type = generated_card_type_for(card, front, back, quote, "auto")
    return {"front": front, "back": back, "source_quote": quote, "mnemonic": mnemonic, "tags": tags, "card_type": card_type, "image_path": card.get("image_path") or ""}

def generate_prompt_for_chunk(
    chunk: str,
    existing_tags: str = "",
    global_tags: str = "",
    desired_card_count: int = 5,
    language: str = "ru",
    model_name: str = None,
    avoid_fronts: Optional[List[str]] = None,
    custom_prompt: str = "",
    forced_card_type: str = "auto",
) -> str:
    desired_card_count = max(1, min(generation_batch_size(model_name), clamp_generation_count(desired_card_count)))
    chunk = normalize_text(chunk)[:get_model_prompt_chars(model_name)]
    avoid_fronts = avoid_fronts or []

    forced_card_type = normalize_generated_card_type(forced_card_type, "auto")
    _type_schema, _type_rules = card_type_prompt_block(forced_card_type, language)

    avoid_part = ""
    if avoid_fronts:
        avoid_list = "; ".join([normalize_text(x, 90) for x in avoid_fronts[:GENERATION_MAX_PROMPT_REPEAT_AVOID] if x])
        avoid_part = f"\nНе повторяй уже созданные вопросы: {avoid_list}." if language == "ru" else f"\nDo not repeat these questions: {avoid_list}."

    custom_prompt = normalize_text(custom_prompt or "", 240)
    custom_part = ""
    if custom_prompt:
        custom_part = f"\nПожелания пользователя: {custom_prompt}. Не копируй эту строку в карточки." if language == "ru" else f"\nUser focus: {custom_prompt}. Do not copy this line into cards."

    type_part = "\n" + _type_rules

    if language == "en":
        instruction = f"""
You are a professional study-card writer. Create exactly {desired_card_count} useful flashcards from the text.
Return ONLY a valid JSON array. No markdown, no comments, no hidden reasoning.
Each object must contain exactly these keys: card_type, front, back, source_quote, mnemonic.
Required card_type schema: {_type_schema}

Quality rules:
- One card = one complete idea from the text.
- Do not build questions from a random isolated word.
- The question must name the concrete subject and what is being asked about it.
- Prefer natural questions: why, how, what role, what happens, what contains, when.
- Avoid template questions like "what key fact is stated" or "what numerical fact is indicated".
- The answer must be concise and factual.
- source_quote must be an exact fragment from the text.
- mnemonic must be a short memory cue, without labels such as "Association:" or "Mnemonic:".
- Do not invent facts outside the text.
{type_part}{custom_part}{avoid_part}

Example:
[{{"card_type":"definition","front":"What does pressure depend on?","back":"Pressure depends on force and contact area.","source_quote":"Pressure is force divided by area","mnemonic":"A thin heel presses harder because the area is smaller."}}]
""".strip()
        text_label = "TEXT"
    else:
        instruction = f"""
Ты — профессиональный методист по учебным карточкам. Создай ровно {desired_card_count} полезных карточек по тексту.
Верни ТОЛЬКО валидный JSON-массив. Без markdown, без пояснений, без <think>.
Каждый объект должен содержать ровно ключи: card_type, front, back, source_quote, mnemonic.
Обязательная схема типа: {_type_schema}

Правила качества:
- Одна карточка = одна законченная мысль из текста.
- Не строй вопрос из случайного отдельного слова.
- Вопрос должен называть конкретный предмет и что именно о нём спрашивается.
- Предпочитай естественные вопросы: почему, как, какую роль играет, что происходит, что содержит, когда.
- Не используй шаблоны вроде «какой числовой факт указан» или «какой ключевой факт».
- Ответ должен быть коротким и фактическим.
- source_quote — точный фрагмент исходного текста.
- mnemonic — короткая запоминалка без ярлыков «Ассоциация:», «Мнемоника:», без стрелок и хэштегов.
- Не выдумывай факты вне текста.
{type_part}{custom_part}{avoid_part}

Пример:
[{{"card_type":"definition","front":"От чего зависит давление?","back":"Давление зависит от силы и площади контакта.","source_quote":"Давление — это сила, делённая на площадь","mnemonic":"Шпилька давит сильнее, потому что площадь меньше."}}]
""".strip()
        text_label = "ТЕКСТ"

    return (
        "<bos><start_of_turn>user\n"
        f"{instruction}\n\n"
        f"{text_label}:\n{chunk}\n"
        "<end_of_turn>\n<start_of_turn>model\n"
    )

# ------------------------- source/url cache -------------------------

def _init_source_cache_db() -> None:
    os.makedirs(os.path.dirname(SOURCE_CACHE_DB_PATH), exist_ok=True)
    with sqlite3.connect(SOURCE_CACHE_DB_PATH) as con:
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS source_cache (
                cache_key TEXT PRIMARY KEY,
                source_type TEXT NOT NULL,
                source_value TEXT NOT NULL,
                text_value TEXT NOT NULL,
                created_at REAL NOT NULL,
                last_used_at REAL NOT NULL,
                hits INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        con.commit()


def _source_cache_key(source_type: str, source_value: str) -> str:
    import hashlib
    h = hashlib.sha256()
    h.update(source_type.encode("utf-8", errors="ignore"))
    h.update(b"\0")
    h.update((source_value or "").strip().encode("utf-8", errors="ignore"))
    return h.hexdigest()


def source_cache_get(source_type: str, source_value: str) -> Optional[str]:
    _init_source_cache_db()
    key = _source_cache_key(source_type, source_value)
    with sqlite3.connect(SOURCE_CACHE_DB_PATH) as con:
        row = con.execute("SELECT text_value FROM source_cache WHERE cache_key=?", (key,)).fetchone()
        if not row:
            return None
        con.execute("UPDATE source_cache SET hits=hits+1, last_used_at=? WHERE cache_key=?", (datetime.now().timestamp(), key))
        con.commit()
        print(f"[SOURCE] Cache hit: {source_type}")
        return row[0]


def source_cache_set(source_type: str, source_value: str, text_value: str) -> None:
    if not text_value:
        return
    _init_source_cache_db()
    key = _source_cache_key(source_type, source_value)
    now = datetime.now().timestamp()
    with sqlite3.connect(SOURCE_CACHE_DB_PATH) as con:
        con.execute(
            """
            INSERT OR REPLACE INTO source_cache
                (cache_key, source_type, source_value, text_value, created_at, last_used_at, hits)
            VALUES (?, ?, ?, ?, COALESCE((SELECT created_at FROM source_cache WHERE cache_key=?), ?), ?, COALESCE((SELECT hits FROM source_cache WHERE cache_key=?), 0))
            """,
            (key, source_type, source_value, text_value, key, now, now, key),
        )
        con.commit()

# ------------------------- parsers -------------------------

def normalize_input_url(url: str) -> str:
    url = (url or "").strip()
    if not url:
        raise HTTPException(status_code=400, detail="No URL")
    if not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", url):
        url = "https://" + url.lstrip("/")

    parsed = urlparse(url)
    if not parsed.netloc:
        raise HTTPException(status_code=400, detail="Некорректная ссылка")

    # requests не всегда сам аккуратно кодирует кириллицу в path/query.
    safe_path = quote(parsed.path or "/", safe="/%:@")
    safe_query = quote(parsed.query or "", safe="=&%:+,/?#[]@!$'()*;")
    rebuilt = f"{parsed.scheme}://{parsed.netloc}{safe_path}"
    if safe_query:
        rebuilt += "?" + safe_query
    return rebuilt


def try_parse_wikipedia(url: str) -> Optional[str]:
    parsed = urlparse(url)
    host = (parsed.netloc or "").lower()
    if "wikipedia.org" not in host or "/wiki/" not in parsed.path:
        return None
    try:
        import requests
        from urllib.parse import unquote

        title = unquote(parsed.path.split("/wiki/", 1)[1]).replace("_", " ").strip()
        if not title:
            return None
        api = f"{parsed.scheme}://{parsed.netloc}/w/api.php"
        resp = requests.get(
            api,
            params={
                "action": "query",
                "prop": "extracts",
                "explaintext": "1",
                "exsectionformat": "plain",
                "redirects": "1",
                "format": "json",
                "titles": title,
            },
            headers={"User-Agent": "AIFlashcardStudio/0.2 local study tool"},
            timeout=20,
        )
        resp.raise_for_status()
        data = resp.json()
        pages = (data.get("query") or {}).get("pages") or {}
        extracts = []
        for page in pages.values():
            extract = page.get("extract") or ""
            if extract:
                extracts.append(extract)
        text_value = normalize_text(" ".join(extracts))
        return text_value if len(text_value) >= 100 else None
    except Exception:
        return None


def parse_url_to_text(url: str) -> str:
    url = normalize_input_url(url)
    cached = source_cache_get("url", url)
    if cached:
        return cached

    wiki_text = try_parse_wikipedia(url)
    if wiki_text:
        source_cache_set("url", url, wiki_text)
        return wiki_text

    try:
        import requests
        from bs4 import BeautifulSoup

        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        resp = requests.get(url, headers=headers, timeout=18)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        for element in soup(["script", "style", "nav", "footer", "header", "aside", "noscript"]):
            element.decompose()
        content = soup.find("div", {"id": "mw-content-text"}) or soup.find("article") or soup.find("main") or soup.body
        text_value = content.get_text(separator=" ") if content else soup.get_text(separator=" ")
        text_value = normalize_text(text_value)
        if len(text_value) < 200:
            paragraphs = soup.find_all("p")
            text_value = normalize_text(" ".join([p.get_text(" ") for p in paragraphs if len(p.get_text()) > 20]))
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Ошибка парсинга URL: {e}")

    if len(text_value) < 100:
        raise HTTPException(status_code=400, detail="Недостаточно текста на странице")
    source_cache_set("url", url, text_value)
    return text_value


def extract_youtube_id(url: str) -> Optional[str]:
    parsed = urlparse(url)
    host = parsed.hostname or ""
    if host in ("www.youtube.com", "youtube.com", "m.youtube.com"):
        if parsed.path == "/watch":
            return parse_qs(parsed.query).get("v", [None])[0]
        if "/shorts/" in parsed.path:
            return parsed.path.split("/shorts/")[-1].split("?")[0]
        if "/embed/" in parsed.path:
            return parsed.path.split("/embed/")[-1].split("?")[0]
    if host == "youtu.be":
        return parsed.path.lstrip("/").split("?")[0]
    return None


def parse_youtube_to_text(url: str) -> str:
    normalized_url = normalize_input_url(url)
    cached = source_cache_get("youtube", normalized_url)
    if cached:
        return cached
    video_id = extract_youtube_id(normalized_url)
    if not video_id:
        raise HTTPException(status_code=400, detail="Не удалось извлечь ID YouTube видео")

    errors = []
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
        try:
            transcript = YouTubeTranscriptApi.get_transcript(video_id, languages=["ru", "en", "uk"])
        except Exception:
            transcript_list = YouTubeTranscriptApi.list_transcripts(video_id)
            transcript = transcript_list.find_transcript(["ru", "en", "uk"]).fetch()
        text_value = normalize_text(" ".join([seg.get("text", "") for seg in transcript]))
        if len(text_value) >= 100:
            source_cache_set("youtube", normalized_url, text_value)
            return text_value
    except Exception as e:
        errors.append(f"subtitles: {e}")

    # Не обязательный fallback: иногда Яндекс отдаёт краткое описание.
    try:
        import requests
        from bs4 import BeautifulSoup
        resp = requests.post(
            "https://300.ya.ru/api/sharing-url",
            json={"url": f"https://www.youtube.com/watch?v={video_id}"},
            headers={"Content-Type": "application/json"},
            timeout=14,
        )
        if resp.status_code == 200:
            data = resp.json()
            text_value = normalize_text((data.get("description") or "") + " " + (data.get("title") or ""))
            sharing_url = data.get("sharing_url")
            if sharing_url and len(text_value) < 300:
                page = requests.get(sharing_url, timeout=10)
                if page.status_code == 200:
                    soup = BeautifulSoup(page.text, "html.parser")
                    parts = [x.get_text(" ") for x in soup.find_all(["li", "p", "div"]) if len(x.get_text(" ")) > 20]
                    text_value = normalize_text(text_value + " " + " ".join(parts))
            if len(text_value) >= 100:
                source_cache_set("youtube", normalized_url, text_value)
                return text_value
    except Exception as e:
        errors.append(f"300.ya.ru: {e}")

    raise HTTPException(status_code=500, detail="Не удалось извлечь текст YouTube: " + " | ".join(errors))


def parse_upload_content(filename: str, content: bytes) -> Tuple[str, bool, Optional[str], List[dict]]:
    lower = (filename or "").lower()
    is_image = lower.endswith((".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp"))
    image_path = None
    media: List[dict] = []

    if is_image:
        item = save_binary_media(base_dir=BASE_DIR, content=content, filename=filename, kind="image", title=filename or "Изображение")
        image_path = item.get("path")
        media.append(item)
        return f"[Изображение: {filename}]", True, image_path, media

    if lower.endswith(".pdf"):
        try:
            import pypdf
            reader = pypdf.PdfReader(io.BytesIO(content))
            text_value = normalize_text("\n".join([page.extract_text() or "" for page in reader.pages]))
            media = extract_pdf_images(BASE_DIR, filename or "document.pdf", content, limit=12)
            return text_value, False, primary_image_path(media), media
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Ошибка PDF: {e}")

    if lower.endswith(".docx"):
        try:
            import docx
            doc = docx.Document(io.BytesIO(content))
            return normalize_text("\n".join([p.text for p in doc.paragraphs])), False, None, []
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Ошибка DOCX: {e}")

    if lower.endswith((".txt", ".md", ".csv", ".tsv")):
        return normalize_text(content.decode("utf-8", errors="ignore")), False, None, []

    if lower.endswith(".epub"):
        try:
            import ebooklib
            from ebooklib import epub
            book = epub.read_epub(io.BytesIO(content))
            chunks = []
            for item in book.get_items():
                if item.get_type() == ebooklib.ITEM_DOCUMENT:
                    chunks.append(strip_html_tags(item.get_body_content().decode("utf-8", errors="ignore")))
            return normalize_text("\n".join(chunks)), False, None, []
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Ошибка EPUB: {e}")

    if lower.endswith(".fb2"):
        try:
            import xml.etree.ElementTree as ET
            root = ET.fromstring(content)
            texts = ["".join(p.itertext()) for p in root.iter() if p.tag.endswith("p")]
            return normalize_text("\n".join(texts)), False, None, []
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Ошибка FB2: {e}")

    raise HTTPException(status_code=400, detail="Неподдерживаемый формат файла")


# ------------------------- import/export helpers -------------------------

def parse_quizlet_like_text(text_value: str) -> List[Dict[str, str]]:
    """Parse Quizlet/TSV-style text without assuming one exact delimiter.

    Quizlet-compatible text is normally term/definition pairs separated by a
    tab, comma or dash. This parser also tolerates semicolon-separated copied
    rows without breaking normal CSV import.
    """
    text_value = text_value.replace("\r\n", "\n").replace("\r", "\n")
    raw_rows: List[str] = []
    for line in text_value.split("\n"):
        line = line.strip()
        if not line:
            continue
        if "\t" not in line and line.count(";") > 1:
            raw_rows.extend([x.strip() for x in line.split(";") if x.strip()])
        else:
            raw_rows.append(line)
    cards: List[Dict[str, str]] = []

    for line in raw_rows:
        parts = None
        for candidate in ["\t", "|", ",", ";"]:
            if candidate in line:
                parts = [x.strip() for x in line.split(candidate, 1)]
                break
        if parts is None:
            split_dash = re.split(r"\s+[—–-]\s+", line, maxsplit=1)
            if len(split_dash) == 2:
                parts = [split_dash[0].strip(), split_dash[1].strip()]
        if not parts or len(parts) < 2:
            continue
        front = strip_html_tags(parts[0])
        back = strip_html_tags(parts[1])
        if front and back:
            cards.append({"front": front, "back": back, "tags": "", "export_profile": "quizlet"})
    return cards


def parse_csv_cards(content: bytes) -> List[Dict[str, str]]:
    text_value = content.decode("utf-8-sig", errors="ignore")
    try:
        sample = text_value[:4096]
        dialect = csv.Sniffer().sniff(sample, delimiters="\t,;|")
        reader = csv.reader(io.StringIO(text_value), dialect)
        rows = list(reader)
    except Exception:
        return parse_quizlet_like_text(text_value)

    cards: List[Dict[str, str]] = []
    if not rows:
        return cards

    header = [x.strip().lower() for x in rows[0]]
    has_header = any(x in header for x in ["front", "question", "term", "back", "answer", "definition", "source_quote", "mnemonic", "export_profile"])
    data_rows = rows[1:] if has_header else rows

    def idx(names: List[str], default: int) -> int:
        if has_header:
            for name in names:
                if name in header:
                    return header.index(name)
        return default

    front_i = idx(["front", "question", "term", "q", "вопрос", "термин"], 0)
    back_i = idx(["back", "answer", "definition", "a", "ответ", "определение"], 1)
    source_i = idx(["source_quote", "source", "quote", "evidence", "цитата", "источник"], -1)
    mnemonic_i = idx(["mnemonic", "hint", "cue", "мнемоника", "подсказка"], -1)
    tags_i = idx(["tags", "tag", "теги"], -1)
    type_i = idx(["card_type", "type", "тип"], -1)
    profile_i = idx(["export_profile", "profile", "format", "формат"], -1)
    fields_i = idx(["fields_json", "fields"], -1)
    image_i = idx(["image", "image_path", "картинка", "изображение"], -1)

    for row in data_rows:
        if len(row) <= max(front_i, back_i):
            continue
        front = strip_html_tags(row[front_i])
        back = strip_html_tags(row[back_i])
        if not front or not back:
            continue
        item = {
            "front": front,
            "back": back,
            "source_quote": row[source_i].strip() if source_i >= 0 and len(row) > source_i else "",
            "mnemonic": row[mnemonic_i].strip() if mnemonic_i >= 0 and len(row) > mnemonic_i else "",
            "tags": row[tags_i].strip() if tags_i >= 0 and len(row) > tags_i else "",
            "card_type": row[type_i].strip() if type_i >= 0 and len(row) > type_i else "",
            "export_profile": row[profile_i].strip() if profile_i >= 0 and len(row) > profile_i else ("quizlet" if not has_header and "\t" in text_value else "csv"),
            "fields_json": row[fields_i].strip() if fields_i >= 0 and len(row) > fields_i else "",
            "image_path": row[image_i].strip() if image_i >= 0 and len(row) > image_i else "",
        }
        cards.append(item)
    return cards

def parse_graph_json_cards(content: bytes) -> List[Dict[str, str]]:
    """Import this app's JSON graph export back as cards.

    Accepts either {"cards": [...]} or a plain list of card-like objects.
    """
    try:
        payload = json.loads(content.decode("utf-8-sig", errors="ignore"))
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Не удалось прочитать JSON: {e}")
    rows = payload.get("cards") if isinstance(payload, dict) else payload
    if not isinstance(rows, list):
        return []
    cards: List[Dict[str, str]] = []
    for item in rows:
        if not isinstance(item, dict):
            continue
        front = strip_html_tags(str(item.get("front") or item.get("q") or item.get("question") or ""))
        back = strip_html_tags(str(item.get("back") or item.get("a") or item.get("answer") or item.get("definition") or ""))
        if not front or not back:
            continue
        cards.append({
            "front": front,
            "back": back,
            "source_quote": strip_html_tags(str(item.get("source_quote") or item.get("quote") or item.get("s") or item.get("evidence") or "")),
            "mnemonic": strip_html_tags(str(item.get("mnemonic") or item.get("hint") or item.get("m") or item.get("cue") or "")),
            "tags": str(item.get("tags") or ""),
            "card_type": str(item.get("card_type") or item.get("relation_type") or item.get("type") or ""),
            "export_profile": str(item.get("export_profile") or item.get("profile") or "json"),
            "fields_json": json_dumps(item.get("fields_json") or item.get("fields") or {}) if isinstance(item.get("fields_json") or item.get("fields"), dict) else str(item.get("fields_json") or ""),
            "image_path": str(item.get("image_path") or ""),
        })
    return cards


def parse_anki_apkg(content: bytes) -> List[Dict[str, str]]:
    cards: List[Dict[str, str]] = []
    with tempfile.TemporaryDirectory() as tmpdir:
        apkg_path = os.path.join(tmpdir, "deck.apkg")
        with open(apkg_path, "wb") as f:
            f.write(content)
        try:
            with zipfile.ZipFile(apkg_path, "r") as zf:
                zf.extractall(tmpdir)
                media_map = {}
                try:
                    raw_media = json.loads(zf.read("media").decode("utf-8"))
                    for archive_name, original_name in raw_media.items():
                        try:
                            raw = zf.read(str(archive_name))
                            item = save_binary_media(base_dir=BASE_DIR, content=raw, filename=str(original_name), kind="image", title=str(original_name))
                            media_map[str(original_name)] = item.get("url") or item.get("path") or ""
                        except Exception:
                            continue
                except Exception:
                    media_map = {}
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Не удалось открыть APKG: {e}")

        collection = None
        for name in ["collection.anki21", "collection.anki2"]:
            path = os.path.join(tmpdir, name)
            if os.path.exists(path):
                collection = path
                break
        if not collection:
            raise HTTPException(status_code=400, detail="В APKG не найдена collection.anki2/anki21")

        def first_img_path(*html_parts: str) -> str:
            for part in html_parts:
                m = re.search(r'<img[^>]+src=["\']([^"\']+)["\']', str(part or ""), flags=re.I)
                if m:
                    name = html.unescape(m.group(1)).strip()
                    return media_map.get(name, name)
            return ""

        try:
            con = sqlite3.connect(collection)
            cur = con.cursor()
            model_fields: dict[str, list[str]] = {}
            try:
                row = cur.execute("SELECT models FROM col LIMIT 1").fetchone()
                models = json.loads(row[0]) if row and row[0] else {}
                for mid, model_data in (models or {}).items():
                    flds_meta = model_data.get("flds") or []
                    names = [str(f.get("name") or "") for f in flds_meta if isinstance(f, dict)]
                    model_fields[str(mid)] = names
            except Exception:
                model_fields = {}

            def pick(field_map: dict[str, str], fields: list[str], names: list[str], fallback_index: int) -> str:
                for name in names:
                    key = name.lower()
                    if key in field_map and field_map[key].strip():
                        return field_map[key]
                return fields[fallback_index] if len(fields) > fallback_index else ""

            for mid, flds, tags in cur.execute("SELECT mid, flds, tags FROM notes"):
                fields = str(flds or "").split("")
                if len(fields) < 2:
                    continue
                names = model_fields.get(str(mid), [])
                field_map = {str(name).strip().lower(): fields[i] for i, name in enumerate(names) if i < len(fields)}
                front_raw = pick(field_map, fields, ["front", "question", "q", "term", "word", "слово", "вопрос", "термин"], 0)
                back_raw = pick(field_map, fields, ["back", "answer", "a", "definition", "meaning", "ответ", "определение"], 1)
                source_raw = pick(field_map, fields, ["source", "source_quote", "quote", "evidence", "цитата"], 2)
                mnemonic_raw = pick(field_map, fields, ["mnemonic", "hint", "cue", "подсказка", "мнемоника"], 3)
                image_path = first_img_path(*fields)
                front = strip_html_tags(front_raw)
                back = strip_html_tags(back_raw)
                if front and back:
                    tags_clean = " ".join([t.strip().lstrip("#") for t in str(tags or "").split() if t.strip()])
                    cards.append({
                        "front": front,
                        "back": back,
                        "source_quote": strip_html_tags(source_raw),
                        "mnemonic": strip_html_tags(mnemonic_raw),
                        "tags": tags_clean,
                        "image_path": image_path,
                        "export_profile": "anki",
                    })
            con.close()
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Ошибка чтения APKG: {e}")
    return cards

def save_imported_cards(db: Session, deck_id: int, cards: List[Dict[str, str]], model_name: str = "import") -> int:
    if not cards:
        return 0
    min_order = db.query(func.min(Card.order)).filter(Card.deck_id == deck_id).scalar() or 0
    saved = 0
    existing = set(
        (front.lower(), back.lower())
        for front, back in db.query(Card.front, Card.back).filter(Card.deck_id == deck_id).all()
        if front and back
    )
    for item in cards:
        front = normalize_text(item.get("front", ""), max_chars=500)
        back = normalize_text(item.get("back", ""), max_chars=1500)
        if not front or not back:
            continue
        key = (front.lower(), back.lower())
        if key in existing:
            continue
        existing.add(key)
        raw_tags = item.get("tags") or ""
        tags = " ".join([t.strip().lstrip("#") for t in raw_tags.split() if t.strip()]) or None
        export_profile = normalize_card_format_profile(item.get("export_profile") or item.get("profile") or ("quizlet" if "quizlet" in model_name else "anki" if "anki" in model_name else "csv"))
        db.add(
            Card(
                front=front,
                back=back,
                source_quote=item.get("source_quote", "") or "",
                mnemonic=item.get("mnemonic", "") or "",
                tags=tags,
                card_type=item.get("card_type") or infer_card_type(front, back, item.get("source_quote", "")),
                deck_id=deck_id,
                status="inbox",
                order=min_order - saved - 1,
                source_node_id=item.get("source_node_id"),
                model=model_name,
                export_profile=export_profile,
                fields_json=item.get("fields_json") or card_format_payload({**item, "front": front, "back": back, "tags": tags or "", "model": model_name, "status": "inbox"}, export_profile),
                image_path=item.get("image_path") or None,
            )
        )
        saved += 1
    db.commit()
    return saved


def get_cyrillic_font() -> Optional[str]:
    possible_paths = [
        "C:\\Windows\\Fonts\\Arial.ttf",
        "C:\\Windows\\Fonts\\Calibri.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        "/Library/Fonts/Arial.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
        os.path.join(BASE_DIR, "DejaVuSans.ttf"),
    ]
    return next((p for p in possible_paths if os.path.exists(p)), None)






def card_format_payload(card_or_dict, profile: str | None = None) -> str:
    """Serialize the real target-format fields stored with each card.

    This is deliberately separate from front/back so Anki/Quizlet/CSV/PDF/JSON
    can each keep their own output shape without changing the whole UI model.
    """
    return fields_json(card_or_dict, profile or getattr(card_or_dict, "export_profile", None) or (card_or_dict.get("export_profile") if isinstance(card_or_dict, dict) else None) or "anki")

def normalize_tag_extraction_mode(mode: str | None = None, generation_mode: str = "fast", model_name: str = "") -> str:
    """UI rule for tag extraction.

    auto = fast for Gemma/fast mode, smart for strict mode or SuperGemma.
    smart tries KeyBERT if installed, otherwise falls back to YAKE/Natasha/pymorphy3.
    """
    raw = str(mode or "auto").strip().lower()
    if raw in {"fast", "yake", "light"}:
        return "fast"
    if raw in {"smart", "keybert", "hybrid", "quality"}:
        return "smart"
    model_low = str(model_name or "").lower()
    gen_low = str(generation_mode or "fast").lower()
    if "supergemma" in model_low or "e4b" in model_low or gen_low in {"strict", "quality", "slow", "exact"}:
        return "smart"
    return "fast"


def effective_generation_model(requested_model: str) -> tuple[str, str]:
    """Keep model selection literal: only the selected LiteRT model writes cards."""
    requested = (requested_model or "gemma-4-E2B-it").strip()
    return requested, ""


# ------------------------- LLM background task -------------------------

def _fast_prompt_for_cards(
    chunk: str,
    count: int,
    language: str = "ru",
    custom_prompt: str = "",
    forced_card_type: str = "auto",
    avoid_fronts: Optional[List[str]] = None,
) -> str:
    """Minimal fast prompt: model writes cards, Python does not judge/rewrite them.

    This is intentionally closer to the old fast generator: compact instruction,
    one model call, tolerant parser, soft duplicate guard. No evidence graph,
    no quality-first repair, no semantic validators.
    """
    count = max(1, int(count or 1))
    chunk = normalize_text(chunk or "", max_chars=9000)
    custom_prompt = normalize_text(custom_prompt or "", max_chars=260)
    forced_card_type = normalize_generated_card_type(forced_card_type, "auto")
    avoid_fronts = [normalize_text(x, max_chars=110) for x in (avoid_fronts or []) if x]
    avoid_text = "; ".join(avoid_fronts[-18:])

    type_schema, type_rules = card_type_prompt_block(forced_card_type, language)
    if language == "en":
        custom_line = f"\nUser focus: {custom_prompt}." if custom_prompt else ""
        avoid_line = f"\nDo not repeat these existing questions: {avoid_text}." if avoid_text else ""
        instruction = f"""
Create exactly {count} useful flashcards from the text.
Return ONLY valid JSON array, no markdown, no explanations.
Each object uses this schema: {type_schema[:-1]},"mnemonic":"short cue"}}.
Rules: one complete idea per card; concise answers; use only the text; source_quote must be copied from text.
{type_rules}{custom_line}{avoid_line}
TEXT:
{chunk}
""".strip()
    else:
        custom_line = f"\nПожелания пользователя: {custom_prompt}." if custom_prompt else ""
        avoid_line = f"\nНе повторяй уже созданные вопросы: {avoid_text}." if avoid_text else ""
        instruction = f"""
Сделай ровно {count} полезных учебных карточек по тексту.
Верни ТОЛЬКО валидный JSON-массив, без markdown и пояснений.
Каждый объект по схеме: {type_schema[:-1]},"mnemonic":"короткая подсказка"}}.
Правила: одна карточка = одна законченная мысль; ответы короткие; используй только текст; source_quote копируй из текста.
{type_rules}{custom_line}{avoid_line}
ТЕКСТ:
{chunk}
""".strip()

    return "<bos><start_of_turn>user\n" + instruction + "\n<end_of_turn>\n<start_of_turn>model\n"


def _fast_card_key(card: dict) -> str:
    return re.sub(r"[^a-zа-яё0-9]+", " ", ((card.get("front") or "") + " " + (card.get("back") or "")).lower().replace("ё", "е")).strip()


def _fast_accept_card(card: dict) -> Optional[dict]:
    """Only minimal sanity checks. Heavy quality checks were the slowdown."""
    if not isinstance(card, dict):
        return None
    front = clean_card_text(card.get("front") or card.get("q") or card.get("question") or card.get("вопрос") or "", 360)
    back = clean_card_text(card.get("back") or card.get("a") or card.get("answer") or card.get("ответ") or "", 900)
    quote = clean_card_text(card.get("source_quote") or card.get("quote") or card.get("source") or card.get("цитата") or "", 700)
    mnemonic = clean_mnemonic_text(card.get("mnemonic") or card.get("hint") or card.get("m") or card.get("мнемоника") or "", 360)
    if not front or not back:
        return None
    if len(front) < 3 or len(back) < 2:
        return None
    ctype = generated_card_type_for(card, front, back, quote, "auto")
    return {"front": front, "back": back, "source_quote": quote, "mnemonic": mnemonic, "card_type": ctype, "image_path": card.get("image_path") or ""}



def _raw_tags_from_model(card: dict) -> str:
    """Read only model-supplied tags. Supports flat and nested JSON."""
    if not isinstance(card, dict):
        return ""

    def collect(value) -> list[str]:
        if value in (None, ""):
            return []
        if isinstance(value, str):
            return [value]
        if isinstance(value, (list, tuple, set)):
            out = []
            for item in value:
                if isinstance(item, dict):
                    out.extend(collect(item.get("tag") or item.get("name") or item.get("label") or item.get("value") or item.get("тег")))
                else:
                    out.extend(collect(item))
            return out
        if isinstance(value, dict):
            out = []
            for key in ("tags", "tag", "теги", "тег", "хэштеги", "hashtags", "keywords", "terms", "topics", "labels"):
                out.extend(collect(value.get(key)))
            return out
        return [str(value)]

    raw_parts: list[str] = []
    for key in ("tags", "tag", "теги", "тег", "хэштеги", "hashtags", "keywords", "terms", "topics", "labels"):
        raw_parts.extend(collect(card.get(key)))

    for key in ("fields", "metadata", "meta", "extra"):
        nested = card.get(key)
        if isinstance(nested, dict):
            raw_parts.extend(collect(nested))

    return normalize_tags_string(" ".join(x.strip().lstrip("#") for x in raw_parts if str(x).strip()), max_tags=8)


def _tags_for_generated_card(card: dict, source_tags: str = "", tag_extraction_mode: str | None = None, max_tags: int = 4) -> str:
    """Use only model-provided tags for generated cards.

    No Python topic inference here: the user wants real model output, not
    backend-invented tags. If the model omits tags, store an empty tag field
    and let the prompt/retry policy be improved, rather than fabricating tags.
    """
    model_tags = _raw_tags_from_model(card)
    return normalize_tags_string(model_tags, max_tags=max_tags) if model_tags else ""


async def _fill_missing_tags_with_model(
    cards: list[dict],
    *,
    model_name: str,
    language: str = "ru",
    no_think: bool | None = True,
    filter_thinking: bool | None = True,
    sampler_override: dict | None = None,
    max_tags: int = 4,
) -> int:
    """Ask the selected model for missing tags. No Python-made tags."""
    missing = [i for i, c in enumerate(cards or []) if not normalize_tags_string(c.get("tags") or "", max_tags=max_tags)]
    if not missing:
        return 0

    filled = 0
    # Short batches: this runs after card creation and must not create a long SuperGemma decode.
    batch_size = 4 if any(x in str(model_name or "").lower() for x in ("supergemma", "e4b", "abliterated")) else 10
    for start in range(0, len(missing), batch_size):
        idxs = missing[start:start + batch_size]
        payload_lines = []
        for local_n, idx in enumerate(idxs, 1):
            c = cards[idx]
            payload_lines.append(
                json.dumps({
                    "i": local_n,
                    "front": clean_card_text(c.get("front") or "", 260),
                    "back": clean_card_text(c.get("back") or "", 420),
                    "source_quote": clean_card_text(c.get("source_quote") or "", 260),
                }, ensure_ascii=False)
            )
        if (language or "ru").lower().startswith("en"):
            prompt = (
                f"For each flashcard below, add 2-4 concise topic tags. Return exactly {len(idxs)} JSONL lines.\n"
                "Only JSONL. No markdown. Keep same i. Schema: {\"i\":1,\"tags\":[\"topic\",\"term\"]}\n"
                "tags is mandatory. Tags must be nouns or short noun phrases, no #, no generic words.\n"
                + "\n".join(payload_lines)
            )
        else:
            prompt = (
                f"Для каждой карточки ниже добавь 2-4 коротких тематических тега. Верни ровно {len(idxs)} строк JSONL.\n"
                "Только JSONL. Без markdown. Сохрани i. Схема: {\"i\":1,\"tags\":[\"тема\",\"термин\"]}\n"
                "Поле tags обязательно. Теги: существительные или короткие именные фразы, без #, без общих слов.\n"
                + "\n".join(payload_lines)
            )
        try:
            tagged = await ask_litert_v2(
                prompt,
                model_name=model_name,
                language=language,
                early_stop_lines=len(idxs),
                no_think=no_think,
                filter_thinking=filter_thinking,
                sampler_override=sampler_override,
                system_message=(
                    "Ты добавляешь теги к уже готовым учебным карточкам. "
                    "Отвечай только JSONL: {\"i\":1,\"tags\":[\"тег\",\"тег\"]}. Поле tags обязательно. Не добавляй пояснения."
                ),
            )
        except Exception as exc:
            print(f"[GEN] tag-fill failed: {exc}")
            continue
        mapped: dict[int, str] = {}
        positional: list[str] = []
        for item in tagged:
            tag_text = _tags_for_generated_card(item, max_tags=max_tags)
            if not tag_text:
                continue
            raw_i = str(item.get("i") or item.get("index") or item.get("idx") or "").strip() if isinstance(item, dict) else ""
            if raw_i.isdigit():
                mapped[int(raw_i)] = tag_text
            else:
                positional.append(tag_text)
        pos_i = 0
        for local_n, idx in enumerate(idxs, 1):
            tag_text = mapped.get(local_n)
            if not tag_text and pos_i < len(positional):
                tag_text = positional[pos_i]
                pos_i += 1
            if tag_text:
                cards[idx]["tags"] = tag_text
                filled += 1
    return filled


def _mnemonic_looks_like_keyword_junk(value: str, front: str = "", back: str = "") -> bool:
    text = clean_card_text(value or "", 300).strip()
    if not text:
        return True
    words = re.findall(r"[A-Za-zА-Яа-яЁё0-9]+", text)
    if len(words) <= 2:
        return True
    # Examples of bad cues we saw in export: "ледяная — кровь — влияет".
    if ("—" in text or " - " in text) and len(words) <= 5 and not re.search(r"[.!?]", text):
        return True
    # Bare keyword list without a verb/relationship is not a mnemonic.
    if len(words) <= 5 and not re.search(r"[.!?]|потому|если|как|чтобы|значит|связан|помни|запом", text.lower()):
        return True
    # If it is mostly copied from the question, it is not a useful cue.
    fw = set(re.findall(r"[a-zа-яё0-9]{4,}", (front or "").lower().replace("ё", "е")))
    tw = [w.lower().replace("ё", "е") for w in words if len(w) >= 4]
    if tw and fw and sum(1 for w in tw if w in fw) / max(1, len(tw)) >= 0.75:
        return True
    return False


def _fallback_mnemonic(front: str, back: str, tags: str = "") -> str:
    """Safe fallback: a short answer-based cue, not a three-keyword list."""
    answer = clean_card_text(back or "", 150).strip()
    if not answer:
        return ""
    answer = re.split(r"(?<=[.!?])\s+", answer)[0].strip()
    topic = ""
    for tag in normalize_tags_string(tags or "", max_tags=1).split():
        topic = tag.lstrip("#").replace("_", " ")
        break
    if topic and topic.lower().replace("ё", "е") not in answer.lower().replace("ё", "е"):
        cue = f"{topic}: {answer}"
    else:
        cue = answer
    return clean_mnemonic_text(cue, 220)


def _normalize_generated_mnemonic(card: dict, tags: str = "") -> str:
    raw = card.get("mnemonic") or card.get("m") or card.get("hint") or card.get("cue") or card.get("мнемоника") or card.get("короткая мнемоника") or ""
    front = card.get("front") or card.get("q") or card.get("question") or card.get("вопрос") or ""
    back = card.get("back") or card.get("a") or card.get("answer") or card.get("ответ") or card.get("короткий ответ") or ""
    cleaned = clean_mnemonic_text(str(raw or ""), 600)
    if _mnemonic_looks_like_keyword_junk(cleaned, str(front), str(back)):
        return ""
    return cleaned


async def background_card_generator(
    deck_id: int,
    text_value: str,
    source_node_id: str = None,
    desired_card_count: int = 10,
    image_path: str = None,
    model_name: str = None,
    language: str = "ru",
    custom_prompt: str = "",
    forced_card_type: str = "auto",
    manual_count: bool = False,
    generation_mode: str = "fast",
    tag_extraction_mode: str = "auto",
    output_profile: str = "anki",
    # UI overrides from the right generation-settings panel.
    temperature: float | None = None,
    top_k: int | None = None,
    top_p: float | None = None,
    min_p: float | None = None,
    seed: int | None = None,
    no_think: bool | None = None,
    quality_gate: bool | None = None,
    evidence_select: bool | None = None,
    stream_gen: bool | None = None,
    filter_thinking: bool | None = None,
    allow_duplicates: bool | None = None,
    cards_per_call_override: int | None = None,
    generate_tags: bool | None = None,
    generate_mnemonics: bool | None = None,
):
    """generation dispatcher.

    Default path is  MASS generation: selected count is the main goal.
    The generation evidence/validator path is still available only through an
    explicit legacy_quality/strict mode or AIFC_QUALITY_GEN=1.
    """
    # generation: UI quality/evidence toggles no longer switch the whole pipeline
    # into generation evidence-batch, because that was the source of 11/13 and
    # "ask=4" behavior in the smart preset. Exact count stays the default.
    force_quality = _env_flag("AIFC_QUALITY_GEN", "0") or str(generation_mode or "").lower() in {"strict", "slow", "exact", "legacy_quality"}

    if force_quality:
        return await _background_card_generator_v2(
            deck_id=deck_id,
            text_value=text_value,
            source_node_id=source_node_id,
            desired_card_count=desired_card_count,
            image_path=image_path,
            model_name=model_name,
            language=language,
            custom_prompt=custom_prompt,
            forced_card_type=forced_card_type,
            manual_count=manual_count,
            generation_mode=generation_mode,
            tag_extraction_mode=tag_extraction_mode,
            output_profile=output_profile,
            temperature=temperature,
            top_k=top_k,
            top_p=top_p,
            min_p=min_p,
            seed=seed,
            no_think=no_think,
            quality_gate=quality_gate,
            evidence_select=evidence_select,
            stream_gen=stream_gen,
            filter_thinking=filter_thinking,
            allow_duplicates=allow_duplicates,
            cards_per_call_override=cards_per_call_override,
            generate_tags=generate_tags,
            generate_mnemonics=generate_mnemonics,
        )

    return await _background_card_generator_count_first(
        deck_id=deck_id,
        text_value=text_value,
        source_node_id=source_node_id,
        desired_card_count=desired_card_count,
        image_path=image_path,
        model_name=model_name,
        language=language,
        custom_prompt=custom_prompt,
        forced_card_type=forced_card_type,
        manual_count=manual_count,
        generation_mode=generation_mode,
        tag_extraction_mode=tag_extraction_mode,
        output_profile=output_profile,
        temperature=temperature,
        top_k=top_k,
        top_p=top_p,
        min_p=min_p,
        seed=seed,
        no_think=no_think,
        stream_gen=stream_gen,
        filter_thinking=filter_thinking,
        allow_duplicates=allow_duplicates,
        cards_per_call_override=cards_per_call_override,
        generate_tags=generate_tags,
        generate_mnemonics=generate_mnemonics,
    )

# ------------------------- generation generation (default) -------------------------

def _norm_question_key(value: str) -> str:
    value = (value or "").lower().replace("ё", "е")
    value = re.sub(r"[^0-9a-zа-я]+", " ", value, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", value).strip()


def _persist_cards_worker(
    db_path: str,
    deck_id: int,
    source_node_id,
    model_name: str,
    cards,
    base_order: int,
) -> int:
    """Synchronous DB writer, designed to run in `asyncio.to_thread`.

    Returns the number of cards actually written. Each card is a fully
    validated dict from `validate_model_card`.
    """
    if not cards:
        return 0
    imported = 0
    conn = sqlite3.connect(db_path, timeout=30)
    try:
        cur = conn.cursor()
        for offset, card in enumerate(cards):
            front = (card.get("front") or "")[:650]
            back = (card.get("back") or "")[:1400]
            quote = (card.get("source_quote") or "")[:900]
            tags = normalize_tags_string(card.get("tags") or "", max_tags=8)
            mnemonic = _normalize_generated_mnemonic({"front": front, "back": back, "mnemonic": card.get("mnemonic", "")}, tags)[:600]
            card_type = (card.get("card_type") or infer_card_type(front, back, quote))[:32]
            if not front or not back:
                continue
            payload = {
                "front": front, "back": back, "source_quote": quote, "mnemonic": mnemonic,
                "tags": tags, "model": model_name, "status": "inbox", "image_path": "",
            }
            order = base_order - offset - 1
            fields_json_str = card_format_payload(payload, "qa")
            cur.execute(
                """INSERT INTO cards
                   (front, back, source_quote, mnemonic, tags, deck_id, status, "order",
                    source_node_id, model, export_profile, fields_json, card_type, image_path, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    front, back, quote, mnemonic, tags or None, deck_id, "inbox", order,
                    source_node_id, model_name, "qa", fields_json_str, card_type, None,
                    datetime.now().isoformat(),
                ),
            )
            imported += 1
        conn.commit()
    finally:
        conn.close()
    return imported


async def _background_card_generator_v2(
    deck_id: int,
    text_value: str,
    source_node_id: str = None,
    desired_card_count: int = 10,
    image_path: str = None,
    model_name: str = None,
    language: str = "ru",
    custom_prompt: str = "",
    forced_card_type: str = "auto",
    manual_count: bool = False,
    generation_mode: str = "fast",
    tag_extraction_mode: str = "auto",
    output_profile: str = "anki",
    # UI-passthrough overrides. None means: fall back to env / preset default.
    temperature: float | None = None,
    top_k: int | None = None,
    top_p: float | None = None,
    min_p: float | None = None,
    seed: int | None = None,
    no_think: bool | None = None,
    quality_gate: bool | None = None,
    evidence_select: bool | None = None,
    stream_gen: bool | None = None,
    filter_thinking: bool | None = None,
    allow_duplicates: bool | None = None,
    cards_per_call_override: int | None = None,
    generate_tags: bool | None = None,
    generate_mnemonics: bool | None = None,
):
    """generation quality+speed path.

    Every toggle/sampler param can be overridden per-call. None means: use the
    env-derived default. This is what lets the UI panel change behavior without
    a server restart.
    """
    db = SessionLocal()
    started_at = datetime.now()
    run_id = f"gen-d{deck_id}-{uuid.uuid4().hex[:6]}"
    saved = 0
    rejected_total = 0
    try:
        model_name = model_name or current_model or "gemma-4-E2B-it"
        target = clamp_generation_count(desired_card_count)
        clean_text = normalize_text(text_value or "", max_chars=400_000)
        if not clean_text and not image_path:
            raise ValueError("Пустой текст после очистки")
        source_tags = ""

        # Per-call card target. 6 is the sweet spot: the model reliably
        # delivers 4-6 quality cards per call, and the prompt stays small.
        # UI override wins over env.
        if cards_per_call_override is not None:
            cards_per_call = max(1, min(GENERATION_MAX_CARD_COUNT, int(cards_per_call_override)))
        else:
            cards_per_call = _env_int(
                "AIFC_CARDS_PER_CALL",
                min(6, max(1, target)),
                min_value=1,
                max_value=GENERATION_MAX_CARD_COUNT,
            )
        # Hard ceiling on the number of inference calls.
        max_calls = _env_int(
            "AIFC_GEN_MAX_CALLS",
            max(math.ceil(target / max(1, cards_per_call)) * 3 + 2, 6),
            min_value=1,
            max_value=max(8, GENERATION_MAX_CARD_COUNT * 2),
        )
        # Toggle quality gate. Default ON — this is the main "адекватность" fix.
        # UI override wins over env.
        quality_gate = quality_gate if quality_gate is not None else _env_flag("AIFC_QUALITY_GATE", "1")
        # Toggle evidence pre-selection. Default ON — pre-ranking source
        # sentences means we never feed noise (Wikipedia headers, refs, etc.)
        # to the model.
        evidence_select = evidence_select if evidence_select is not None else _env_flag("AIFC_EVIDENCE_SELECT", "1")
        # Toggle streaming + early-stop. Default ON.
        stream_gen = stream_gen if stream_gen is not None else _env_flag("AIFC_STREAM_GENERATION", "1")
        # Default OFF: dedup by question (generation default was ON — too many duplicates).
        allow_duplicates = allow_duplicates if allow_duplicates is not None else _env_flag("AIFC_ALLOW_DUPLICATES", "0")
        generate_tags = generate_tags if generate_tags is not None else _env_flag("AIFC_GENERATE_TAGS", "0")
        generate_mnemonics = generate_mnemonics if generate_mnemonics is not None else _env_flag("AIFC_GENERATE_MNEMONICS", "0")

        progress_start(deck_id, total=target, message=f"Генерация: 0/{target}")
        print(f"[GEN] start deck={deck_id} run={run_id} model={model_name} target={target} cards_per_call={cards_per_call} max_calls={max_calls} quality_gate={quality_gate} evidence_select={evidence_select} stream={stream_gen} allow_duplicates={allow_duplicates} tags={generate_tags} mnemonics={generate_mnemonics}")

        # ------------------------- Step 1: pre-rank evidence ONCE -------------------------
        evidence_units = []
        if evidence_select and clean_text:
            try:
                evidence_units = list(build_evidence_units(clean_text, desired_count=target, language=language))
            except Exception as e:
                print(f"[GEN] evidence_units failed, will fall back to chunks: {e}")
                evidence_units = []
        if not evidence_units and clean_text:
            # Fallback: simple sentence split. Keeps the generator working even
            # if natasha/razdel/pymorphy3 fail to produce useful units.
            for s in sentence_units(clean_text):
                if 35 <= len(s) <= 650 and is_useful_sentence(s):
                    evidence_units.append(type("U", (), {"text": s, "score": 0.5, "order": len(evidence_units)})())
        print(f"[GEN] evidence_units={len(evidence_units)}")

        # Build a stable fallback chunk list when evidence is too thin.
        chunk_chars = _env_int("AIFC133_CHUNK_CHARS", 3000, min_value=800, max_value=12000)
        chunks = split_text_into_chunks(clean_text, chunk_size=chunk_chars, overlap_size=0) if clean_text else []
        if not chunks and clean_text:
            chunks = [clean_text[:chunk_chars]]

        # ------------------------- Step 2: build batches (evidence-driven) -------------------------
        # Each batch gets a fresh slice of evidence so we never re-feed the same
        # source sentence twice. This eliminates the "duplicate question" failure
        # mode that hurt generation.
        batches = []  # each: {"prompt": str, "count": int, "evidence": list, "tag": str}
        if evidence_units:
            card_left = target
            cursor = 0
            batch_index = 0
            while card_left > 0 and cursor < len(evidence_units):
                cards_here = min(cards_per_call, card_left)
                # ~3 evidence units per requested card, capped at 18 per batch.
                take = min(len(evidence_units) - cursor, max(cards_here * 3, cards_here + 4, 8), 18)
                batch_units = evidence_units[cursor:cursor + take]
                cursor += take
                if not batch_units:
                    break
                try:
                    prompt = build_evidence_prompt(
                        batch_units,
                        count=cards_here,
                        language=language,
                        custom_prompt=custom_prompt,
                        forced_card_type=forced_card_type,
                        avoid_fronts=None,
                        retry_mode=False,
                        tag_hints="",
                        output_profile=output_profile,
                    )
                except Exception as e:
                    print(f"[GEN] build_evidence_prompt failed for batch {batch_index}: {e}")
                    break
                batches.append({"prompt": prompt, "count": cards_here, "evidence": list(batch_units), "tag": f"evidence-batch-{batch_index}"})
                card_left -= cards_here
                batch_index += 1
                if batch_index > target + 4:
                    break
        else:
            # No evidence (image-only or very short source) — fall back to chunked prompts.
            card_left = target
            for ci, chunk in enumerate(chunks):
                if card_left <= 0:
                    break
                cards_here = min(cards_per_call, card_left)
                prompt = _fast_prompt_for_cards(
                    chunk=chunk,
                    count=cards_here,
                    language=language,
                    custom_prompt=custom_prompt,
                    forced_card_type=forced_card_type,
                    avoid_fronts=None,
                )
                batches.append({"prompt": prompt, "count": cards_here, "evidence": [], "tag": f"chunk-{ci}"})
                card_left -= cards_here
        print(f"[GEN] batches={len(batches)} (evidence-driven={bool(evidence_units)})")

        # ------------------------- Step 3: iterate batches, validate, persist -------------------------
        seen_questions = set()
        accepted_fronts = []
        zero_streak = 0
        call_index = 0
        persist_task = None

        async def _await_persist():
            nonlocal persist_task
            if persist_task is not None and not persist_task.done():
                try:
                    await persist_task
                except Exception as e:
                    print(f"[GEN] persist task error: {e}")
            persist_task = None

        while saved < target and call_index < max_calls and batches:
            need = target - saved
            batch = batches.pop(0)
            count_now = min(batch["count"], need)
            prompt = batch["prompt"]
            evidence = batch["evidence"]
            progress_update(deck_id, current=saved, total=target, message=f"Генерация: {saved}/{target}")
            print(f"[GEN] call={call_index + 1}/{max_calls} need={need} ask={count_now} batch={batch['tag']} prompt_chars={len(prompt)}")

            t_call = time.perf_counter()
            try:
                sampler_override = None
                if any(x is not None for x in (temperature, top_k, top_p, min_p, seed)):
                    sampler_override = {}
                    if temperature is not None: sampler_override["temperature"] = temperature
                    if top_k is not None: sampler_override["top_k"] = top_k
                    if top_p is not None: sampler_override["top_p"] = top_p
                    if min_p is not None: sampler_override["min_p"] = min_p
                    if seed is not None: sampler_override["seed"] = seed
                ask_kwargs = {
                    "model_name": model_name,
                    "language": language,
                    "no_think": no_think,
                    "filter_thinking": filter_thinking,
                    "sampler_override": sampler_override,
                }
                if stream_gen:
                    ask_kwargs["early_stop_lines"] = count_now
                raw_cards = await ask_litert_v2(prompt, **ask_kwargs)
            except Exception as e:
                err = str(e)
                print(f"[GEN] call failed: {err}")
                if saved <= 0:
                    raise
                break

            # ------------------------- Step 4: parse + validate -------------------------
            accepted = []
            rejected = 0
            for raw in raw_cards:
                if not isinstance(raw, dict):
                    continue
                front_raw = str(raw.get("front") or raw.get("q") or raw.get("question") or raw.get("вопрос") or "")
                back_raw = str(raw.get("back") or raw.get("a") or raw.get("answer") or raw.get("ответ") or "")
                quote_raw = str(raw.get("source_quote") or raw.get("s") or raw.get("quote") or raw.get("source") or raw.get("цитата") or "")
                mnemonic_raw = str(raw.get("mnemonic") or raw.get("m") or raw.get("hint") or raw.get("мнемоника") or "")
                ctype = generated_card_type_for(raw, front_raw, back_raw, quote_raw, forced_card_type)

                tag_raw = _tags_for_generated_card(raw, source_tags=source_tags, tag_extraction_mode=tag_extraction_mode, max_tags=4)
                card_payload = {
                    "card_type": ctype,
                    "front": front_raw,
                    "back": back_raw,
                    "source_quote": quote_raw,
                    "mnemonic": mnemonic_raw,
                    "tags": tag_raw,
                }

                if quality_gate and evidence:
                    try:
                        validated, reason = validate_model_card(card_payload, evidence, language=language, output_profile=output_profile)
                    except Exception as ve:
                        validated, reason = None, f"validator-exception: {ve}"
                    if validated is None:
                        rejected += 1
                        continue
                    card_payload = validated
                    card_payload["tags"] = tag_raw or _tags_for_generated_card(card_payload, source_tags=source_tags, tag_extraction_mode=tag_extraction_mode, max_tags=4)
                    card_payload["mnemonic"] = _normalize_generated_mnemonic(card_payload, card_payload.get("tags", ""))
                else:
                    # Soft path: only minimal sanity (matching old _fast_accept_card).
                    front = clean_card_text(front_raw, 360)
                    back = clean_card_text(back_raw, 900)
                    quote = clean_card_text(quote_raw, 700)
                    tag_soft = tag_raw or _tags_for_generated_card(raw, source_tags=source_tags, tag_extraction_mode=tag_extraction_mode, max_tags=4)
                    mnemonic = _normalize_generated_mnemonic({**raw, "front": front, "back": back}, tag_soft)
                    if not front or not back or len(front) < 5 or len(back) < 4:
                        rejected += 1
                        continue
                    if not front.endswith("?"):
                        front = front.rstrip(" .;:!") + "?"
                    card_payload = {
                        "card_type": ctype,
                        "front": front,
                        "back": back,
                        "source_quote": quote,
                        "mnemonic": mnemonic,
                        "tags": tag_soft,
                    }

                if not generate_tags:
                    card_payload["tags"] = ""
                if not generate_mnemonics:
                    card_payload["mnemonic"] = ""

                # Dedup by normalized question.
                key = _norm_question_key(card_payload["front"])
                if not allow_duplicates and key and key in seen_questions:
                    rejected += 1
                    continue
                seen_questions.add(key)
                accepted.append(card_payload)
                accepted_fronts.append(card_payload["front"])
                if len(accepted) >= need:
                    break

            # ------------------------- Step 5: persist (pipelined) -------------------------
            # Wait for any in-flight persist task before starting a new one.
            await _await_persist()

            if accepted:
                accepted = rebalance_auto_card_types(accepted, forced_card_type)
                zero_streak = 0
                # Snapshot the current min(order) for this deck so the worker
                # thread can compute unique order values without racing.
                base_order = db.query(func.min(Card.order)).filter(Card.deck_id == deck_id).scalar() or 0
                cards_to_persist = list(accepted)
                saved_pending = len(cards_to_persist)
                persist_task = asyncio.create_task(
                    asyncio.to_thread(
                        _persist_cards_worker,
                        DB_PATH,
                        deck_id,
                        source_node_id,
                        model_name,
                        cards_to_persist,
                        base_order,
                    )
                )
                saved += saved_pending
                print(f"[GEN] persisted-pending={saved_pending} saved={saved}/{target} (call raw={len(raw_cards)} accepted={len(accepted)} rejected={rejected})")
            else:
                zero_streak += 1
                print(f"[GEN] call produced 0 accepted cards (raw={len(raw_cards)} rejected={rejected})")

            rejected_total += rejected
            elapsed = time.perf_counter() - t_call
            print(f"[GEN] call done time={elapsed:.1f}s zero_streak={zero_streak}")
            call_index += 1

            # If we've exhausted the evidence batches but still need more cards,
            # build repair batches from unused evidence (or fresh chunk rotations).
            if saved < target and not batches:
                unused_evidence = select_retry_evidence(evidence_units, accepted=[], need=cards_per_call, language=language) if evidence_units else []
                if unused_evidence:
                    cards_here = min(cards_per_call, target - saved)
                    try:
                        repair_prompt = build_evidence_prompt(
                            unused_evidence,
                            count=cards_here,
                            language=language,
                            custom_prompt=custom_prompt,
                            forced_card_type=forced_card_type,
                            avoid_fronts=accepted_fronts[-18:],
                            retry_mode=True,
                            tag_hints="",
                            output_profile=output_profile,
                        )
                        batches.append({"prompt": repair_prompt, "count": cards_here, "evidence": list(unused_evidence), "tag": f"repair-{call_index}"})
                    except Exception as e:
                        print(f"[GEN] repair prompt build failed: {e}")

            if zero_streak >= max(3, min(len(chunks) or 4, 6)):
                print(f"[GEN] stopping: zero_streak={zero_streak}, saved={saved}/{target}")
                break

        # Final wait for any in-flight persist.
        await _await_persist()

        elapsed_total = (datetime.now() - started_at).total_seconds()
        if saved <= 0:
            raise ValueError(f"Модель не дала валидных карточек (отвергнуто {rejected_total}). Ничего не сохранено.")
        suffix = "" if saved >= target else f" Сохранено {saved} из {target}: модель не добила остаток (отвергнуто {rejected_total})."
        progress_done(deck_id, current=saved, total=target, message=f"Готово: {saved}/{target} карточек за {elapsed_total:.1f} сек.{suffix}")
        print(f"[GEN] done deck={deck_id} run={run_id} model={model_name} saved={saved}/{target} rejected={rejected_total} elapsed={elapsed_total:.1f}s")

        async def auto_clean():
            await asyncio.sleep(300)
            task_progress.pop(deck_id, None)
        asyncio.create_task(auto_clean())
    except Exception as exc:
        db.rollback()
        print(f"[GEN] fatal: {exc}")
        progress_error(deck_id, f"Ошибка генерации: {exc}")
    finally:
        db.close()



# ------------------------- generation generation (default count-first exact path) -------------------------

async def _background_card_generator_count_first(
    deck_id: int,
    text_value: str,
    source_node_id: str = None,
    desired_card_count: int = 10,
    image_path: str = None,
    model_name: str = None,
    language: str = "ru",
    custom_prompt: str = "",
    forced_card_type: str = "auto",
    manual_count: bool = False,
    generation_mode: str = "fast",
    tag_extraction_mode: str = "auto",
    output_profile: str = "anki",
    temperature: float | None = None,
    top_k: int | None = None,
    top_p: float | None = None,
    min_p: float | None = None,
    seed: int | None = None,
    no_think: bool | None = None,
    stream_gen: bool | None = None,
    filter_thinking: bool | None = None,
    allow_duplicates: bool | None = None,
    cards_per_call_override: int | None = None,
    generate_tags: bool | None = None,
    generate_mnemonics: bool | None = None,
):
    """generation count-first exact generator.

    Goal: produce the selected number of cards, with minimal filtering. The UI
    sampling/no_think/stream settings are still respected. No evidence gate,
    no strict quote validation, no Python-made fake cards.
    """
    db = SessionLocal()
    started_at = datetime.now()
    run_id = f"gen-d{deck_id}-{uuid.uuid4().hex[:6]}"
    saved = 0
    rejected_total = 0
    try:
        model_name = model_name or current_model or "gemma-4-E2B-it"
        model_low = str(model_name or "").lower()
        is_supergemma = ("supergemma" in model_low) or ("abliterated" in model_low) or ("e4b" in model_low)
        target = clamp_generation_count(desired_card_count)
        clean_text = normalize_text(text_value or "", max_chars=500_000)
        if not clean_text and not image_path:
            raise ValueError("Пустой текст после очистки")
        source_tags = ""

        chunk_chars = _env_int("AIFC143_CHUNK_CHARS", 3000, min_value=800, max_value=16000)
        requested_cards_per_call = int(cards_per_call_override) if cards_per_call_override is not None else _env_int("AIFC143_CARDS_PER_CALL", min(target, 40), min_value=1, max_value=GENERATION_MAX_CARD_COUNT)
        cards_per_call = max(1, min(GENERATION_MAX_CARD_COUNT, requested_cards_per_call))
        # SuperGemma E4B is a heavier thinking model. It must stay on the same
        # model/backend, but the work is split into short calls to avoid LiteRT
        # WebGPU readback timeouts.
        if is_supergemma:
            chunk_chars = min(chunk_chars, _env_int("AIFC_SUPERGEMMA_CHUNK_CHARS", 1400, min_value=600, max_value=6000))
        # Lots of calls are allowed because the priority is to reach the target.
        max_calls_default = max(8, math.ceil(target / max(1, cards_per_call)) * 10 + 6)
        max_calls = _env_int("AIFC143_MAX_CALLS", max_calls_default, min_value=1, max_value=max(24, GENERATION_MAX_CARD_COUNT * 5))
        allow_duplicates = allow_duplicates if allow_duplicates is not None else _env_flag("AIFC_ALLOW_DUPLICATES", "1")
        stream_gen = stream_gen if stream_gen is not None else _env_flag("AIFC_STREAM_GENERATION", "1")
        filter_thinking = filter_thinking if filter_thinking is not None else _env_flag("AIFC_FILTER_THINKING", "1")
        no_think = no_think if no_think is not None else _env_flag("AIFC_NO_THINK", "1")
        if is_supergemma:
            # SuperGemma is kept on the same model/backend, but long thinking calls
            # are what hit LiteRT WebGPU readback timeout. Respect an explicit UI
            # no_think=False for the deep preset; otherwise default to safe no_think.
            force_sg = os.environ.get("AIFC_SUPERGEMMA_FORCE_NO_THINK", "").strip().lower()
            if force_sg in {"1", "true", "yes", "on"}:
                no_think = True
            elif force_sg in {"0", "false", "no", "off"}:
                no_think = False
            elif no_think is None:
                no_think = True
            filter_thinking = True
            stream_gen = True
        generate_tags = generate_tags if generate_tags is not None else _env_flag("AIFC_GENERATE_TAGS", "0")
        generate_mnemonics = generate_mnemonics if generate_mnemonics is not None else _env_flag("AIFC_GENERATE_MNEMONICS", "0")

        if is_supergemma:
            # Keep the 4 preset values visible in the UI, but cap the *effective*
            # per-call request for the heavier E4B model so LiteRT GPU does not
            # spend >~59s in one decode/readback. Tags alone are cheap enough for 6;
            # mnemonic or explicit thinking mode gets smaller batches.
            sg_batch = _env_int("AIFC_SUPERGEMMA_CARDS_PER_CALL", 6, min_value=1, max_value=12)
            if generate_mnemonics:
                sg_batch = min(sg_batch, _env_int("AIFC_SUPERGEMMA_MNEMONIC_CARDS_PER_CALL", 4, min_value=1, max_value=8))
            if no_think is False:
                sg_batch = min(sg_batch, _env_int("AIFC_SUPERGEMMA_THINKING_CARDS_PER_CALL", 2, min_value=1, max_value=6))
            cards_per_call = max(1, min(cards_per_call, sg_batch, target))
            max_calls = max(max_calls, math.ceil(target / max(1, cards_per_call)) * 12 + 8)

        chunks = split_text_into_chunks(clean_text, chunk_size=chunk_chars, overlap_size=0)
        if not chunks:
            chunks = [clean_text[:chunk_chars]]

        progress_start(deck_id, total=target, message=f"Генерация: 0/{target}")
        print(f"[GEN] start deck={deck_id} run={run_id} model={model_name} target={target} cards_per_call={cards_per_call} requested_batch={requested_cards_per_call} supergemma={is_supergemma} chunks={len(chunks)} chunk_chars={chunk_chars} max_calls={max_calls} stream={stream_gen} allow_duplicates={allow_duplicates} tags={generate_tags} mnemonics={generate_mnemonics}")

        def build_mass_prompt(chunk: str, count_now: int, attempt_index: int) -> str:
            user_extra = (custom_prompt or "").strip()
            forced = normalize_generated_card_type(forced_card_type, "auto")
            type_schema_base, type_rules = card_type_prompt_block(forced, language)
            type_note = "\n" + type_rules
            extra_note = f"\nПожелание пользователя: {user_extra}" if user_extra else ""
            if is_supergemma:
                # Compact prompt for the heavier model: less prefill, more chance to finish.
                tag_part = ', "tags":["тема","термин","раздел"]' if generate_tags else ''
                mem_part = ', "mnemonic":"короткая фраза"' if generate_mnemonics else ''
                base_schema = type_schema_base[:-1] if type_schema_base.endswith('}') else type_schema_base
                schema = base_schema + mem_part + tag_part + '}'
                if (language or "ru").lower().startswith("en"):
                    return (
                        f"Return exactly {count_now} JSONL flashcards. Only JSONL, no markdown, no thinking.\n"
                        f"Schema: {schema}\n"
                        + ("Field tags is mandatory in every line. " if generate_tags else "")
                        + ("Field mnemonic is mandatory in every line. " if generate_mnemonics else "")
                        + f"Attempt {attempt_index}.{type_note}{extra_note}\nTEXT:\n{chunk}"
                    )
                return (
                    f"Верни ровно {count_now} учебных карточек JSONL. Только JSONL, без markdown, без размышлений.\n"
                    f"Схема: {schema}\n"
                    + ("Поле tags обязательно в каждой строке. " if generate_tags else "")
                    + ("Поле mnemonic обязательно в каждой строке. " if generate_mnemonics else "")
                    + f"Попытка {attempt_index}.{type_note}{extra_note}\nТЕКСТ:\n{chunk}"
                )
            if generate_tags and generate_mnemonics:
                schema_en_base, _ = card_type_prompt_block(forced, "en")
                schema_ru_base, _ = card_type_prompt_block(forced, "ru")
                schema_en = schema_en_base[:-1] + ',"mnemonic":"useful memory cue","tags":["topic","term"]}'
                schema_ru = schema_ru_base[:-1] + ',"mnemonic":"полезная запоминалка","tags":["тема","термин"]}'
                fields_en = "Tags are required: 2-4 short topic tags, no #. Mnemonic is required: a short memory cue sentence, not a keyword list."
                fields_ru = "tags обязательны: 2-4 коротких тематических тега, без #. mnemonic обязательна: короткая фраза для запоминания, НЕ список слов через тире."
            elif generate_tags:
                schema_en_base, _ = card_type_prompt_block(forced, "en")
                schema_ru_base, _ = card_type_prompt_block(forced, "ru")
                schema_en = schema_en_base[:-1] + ',"tags":["topic","term"]}'
                schema_ru = schema_ru_base[:-1] + ',"tags":["тема","термин"]}'
                fields_en = "Tags are required: 2-4 short topic tags, no #. Do not output mnemonic."
                fields_ru = "tags обязательны: 2-4 коротких тематических тега, без #. Не выводи mnemonic."
            elif generate_mnemonics:
                schema_en_base, _ = card_type_prompt_block(forced, "en")
                schema_ru_base, _ = card_type_prompt_block(forced, "ru")
                schema_en = schema_en_base[:-1] + ',"mnemonic":"useful memory cue"}'
                schema_ru = schema_ru_base[:-1] + ',"mnemonic":"полезная запоминалка"}'
                fields_en = "Mnemonic is required: a short memory cue sentence, not a keyword list. Do not output tags."
                fields_ru = "mnemonic обязательна: короткая фраза для запоминания, НЕ список слов через тире. Не выводи tags."
            else:
                schema_en, _ = card_type_prompt_block(forced, "en")
                schema_ru, _ = card_type_prompt_block(forced, "ru")
                fields_en = "Do not output tags or mnemonic."
                fields_ru = "Не выводи tags и mnemonic."
            if (language or "ru").lower().startswith("en"):
                return (
                    "Generate flashcards from the source text. COUNT IS THE PRIORITY.\n"
                    f"Return exactly {count_now} JSONL lines. One line = one JSON object.\n"
                    "Do not use markdown. Do not wrap in an array. Do not explain.\n"
                    f"Schema per line: {schema_en}\n"
                    f"Front/back are required. {fields_en}\n"
                    "Short answers are allowed. Duplicate cards are allowed if needed to hit the requested count.\n"
                    "Use facts, definitions, causes, consequences, examples, comparisons, dates, names, terms, and small details.\n"
                    "If there are not enough large facts, split details into smaller cards.\n"
                    f"Attempt {attempt_index}. Need exactly {count_now} lines.{type_note}{extra_note}\n\nSOURCE TEXT:\n{chunk}"
                )
            return (
                "Сделай учебные карточки по тексту. ГЛАВНАЯ ЦЕЛЬ — КОЛИЧЕСТВО.\n"
                f"Верни ровно {count_now} строк JSONL. Одна строка = один JSON-объект.\n"
                "Не markdown. Не массив. Не пояснения. Только строки JSON.\n"
                f"Схема строки: {schema_ru}\n"
                f"front/back обязательны. {fields_ru}\n"
                "Короткие ответы разрешены. Дубли разрешены, если они помогают добрать количество.\n"
                "Вытаскивай факты, определения, причины, следствия, примеры, сравнения, даты, имена, термины и мелкие детали.\n"
                "Если крупных фактов мало — дроби материал на маленькие карточки.\n"
                f"Попытка {attempt_index}. Нужно ровно {count_now} строк.{type_note}{extra_note}\n\nТЕКСТ ИСТОЧНИКА:\n{chunk}"
            )

        def _norm_question(value: str) -> str:
            value = (value or "").lower().replace("ё", "е")
            value = re.sub(r"[^0-9a-zа-я]+", " ", value, flags=re.IGNORECASE)
            return re.sub(r"\s+", " ", value).strip()

        sampler_override = None
        if any(x is not None for x in (temperature, top_k, top_p, min_p, seed)):
            sampler_override = {}
            if temperature is not None: sampler_override["temperature"] = temperature
            if top_k is not None: sampler_override["top_k"] = top_k
            if top_p is not None: sampler_override["top_p"] = top_p
            if min_p is not None: sampler_override["min_p"] = min_p
            if seed is not None: sampler_override["seed"] = seed
        if is_supergemma:
            sampler_override = dict(sampler_override or {})
            sampler_override.setdefault("temperature", 0.25)
            sampler_override.setdefault("top_p", 0.90)
            # LiteRT WebGPU is more stable on this model with a narrow top-k.
            # User top_k=0 still means disabled in UI, but for this runtime we pass a legal value.
            sampler_override["top_k"] = max(1, min(int(sampler_override.get("top_k", 1) or 1), _env_int("AIFC_SUPERGEMMA_TOP_K_MAX", 8, min_value=1, max_value=64)))

        seen_questions = set()
        call_index = 0
        zero_streak = 0
        while saved < target and call_index < max_calls:
            need = target - saved
            count_now = min(cards_per_call, need)
            chunk = chunks[call_index % len(chunks)]
            prompt = build_mass_prompt(chunk, count_now, call_index + 1)
            progress_update(deck_id, current=saved, total=target, message=f"Генерация: {saved}/{target}")
            print(f"[GEN] call={call_index + 1}/{max_calls} saved={saved}/{target} need={need} ask={count_now} chunk={call_index % len(chunks)} prompt_chars={len(prompt)}")
            t_call = time.perf_counter()
            try:
                raw_cards = await ask_litert_v2(
                    prompt,
                    model_name=model_name,
                    language=language,
                    early_stop_lines=count_now if stream_gen else None,
                    no_think=no_think,
                    filter_thinking=filter_thinking,
                    sampler_override=sampler_override,
                    system_message=(
                        "Ты генератор учебных карточек. Отвечай только JSONL. Главное — добрать запрошенное количество карточек. "
                        "Выводи только поля из схемы запроса: если tags/mnemonic не указаны в схеме, не добавляй их; если указаны — заполни их. "
                        "Не пиши размышления, markdown или пояснения."
                    ),
                )
            except Exception as e:
                err = str(e)
                print(f"[GEN] call failed: {err}")
                # Same-model auto-splitting for SuperGemma WebGPU readback timeouts.
                # Do not switch model and do not switch backend: just ask fewer cards per call.
                if is_supergemma and ("timeout" in err.lower() or "read" in err.lower() or "ABORTED" in err):
                    if cards_per_call > 1:
                        cards_per_call = max(1, cards_per_call // 2)
                        call_index += 1
                        progress_update(deck_id, current=saved, total=target, message=f"Генерация продолжается: {saved}/{target}")
                        continue
                if saved <= 0:
                    raise
                break

            accepted = []
            for card in raw_cards:
                if not isinstance(card, dict):
                    continue
                front = clean_card_text(str(card.get("front") or card.get("q") or card.get("question") or card.get("вопрос") or ""), 650)
                back = clean_card_text(str(card.get("back") or card.get("a") or card.get("answer") or card.get("ответ") or card.get("короткий ответ") or ""), 1400)
                quote = clean_card_text(str(card.get("source_quote") or card.get("quote") or card.get("source") or card.get("цитата") or card.get("цитата из текста") or ""), 900)
                tag_text = _tags_for_generated_card({**card, "front": front, "back": back, "source_quote": quote}, source_tags=source_tags, tag_extraction_mode=tag_extraction_mode, max_tags=4) if generate_tags else ""
                mnemonic = _normalize_generated_mnemonic({**card, "front": front, "back": back}, tag_text) if generate_mnemonics else ""
                ctype = generated_card_type_for(card, front, back, quote, forced_card_type)
                if not front or not back:
                    rejected_total += 1
                    continue
                if len(front) < 3 or len(back) < 1:
                    rejected_total += 1
                    continue
                if not allow_duplicates:
                    key = _norm_question(front)
                    if key and key in seen_questions:
                        rejected_total += 1
                        continue
                    seen_questions.add(key)
                accepted.append({"front": front, "back": back, "source_quote": quote, "mnemonic": mnemonic, "tags": tag_text, "card_type": ctype})
                if len(accepted) >= need:
                    break

            if accepted:
                accepted = rebalance_auto_card_types(accepted, forced_card_type)
                if generate_tags:
                    tags_from_main = sum(1 for c in accepted if normalize_tags_string(c.get("tags") or "", max_tags=4))
                    print(f"[GEN] tags main accepted={len(accepted)} with_tags={tags_from_main}")
                if generate_tags and any(not normalize_tags_string(c.get("tags") or "", max_tags=4) for c in accepted):
                    filled_tags = await _fill_missing_tags_with_model(
                        accepted,
                        model_name=model_name,
                        language=language,
                        no_think=no_think,
                        filter_thinking=filter_thinking,
                        sampler_override=sampler_override,
                        max_tags=4,
                    )
                    print(f"[GEN] tag-fill accepted={len(accepted)} filled={filled_tags}")
                zero_streak = 0
                min_order = db.query(func.min(Card.order)).filter(Card.deck_id == deck_id).scalar() or 0
                for card in accepted:
                    if saved >= target:
                        break
                    payload = {**card, "tags": card.get("tags", ""), "model": model_name, "status": "inbox", "image_path": "", "export_profile": output_profile or "anki"}
                    db.add(Card(
                        front=card["front"],
                        back=card["back"],
                        source_quote=card.get("source_quote", ""),
                        mnemonic=card.get("mnemonic", ""),
                        tags=normalize_tags_string(card.get("tags") or "", max_tags=8) or None,
                        deck_id=deck_id,
                        status="inbox",
                        order=min_order - saved - 1,
                        source_node_id=source_node_id,
                        model=model_name,
                        export_profile=output_profile or "anki",
                        fields_json=card_format_payload(payload, output_profile or "anki"),
                        card_type=card.get("card_type") or infer_card_type(card.get("front", ""), card.get("back", ""), card.get("source_quote", "")),
                        image_path=None,
                    ))
                    saved += 1
                db.commit()
            else:
                zero_streak += 1

            elapsed = time.perf_counter() - t_call
            print(f"[GEN] call done raw={len(raw_cards)} accepted={len(accepted)} saved={saved}/{target} time={elapsed:.1f}s zero_streak={zero_streak}")
            call_index += 1
            if zero_streak >= max(4, min(len(chunks), 8)):
                print(f"[GEN] stopping zero_streak={zero_streak} saved={saved}/{target}")
                break

        elapsed_total = (datetime.now() - started_at).total_seconds()
        if saved <= 0:
            raise ValueError(f"Модель не дала карточек. Отвергнуто {rejected_total}. Ничего не сохранено.")
        suffix = "" if saved >= target else f" Сохранено {saved} из {target}: модель/рантайм не добили остаток."
        progress_done(deck_id, current=saved, total=target, message=f"Готово: {saved}/{target} карточек за {elapsed_total:.1f} сек.{suffix}")
        print(f"[GEN] done deck={deck_id} run={run_id} model={model_name} saved={saved}/{target} rejected={rejected_total} elapsed={elapsed_total:.1f}s")

        async def auto_clean():
            await asyncio.sleep(300)
            task_progress.pop(deck_id, None)
        asyncio.create_task(auto_clean())
    except Exception as exc:
        db.rollback()
        print(f"[GEN] fatal: {exc}")
        progress_error(deck_id, f"Ошибка генерации: {exc}")
    finally:
        db.close()

# ------------------------- generation generation (legacy, behind AIFC_LEGACY_GEN=1) -------------------------

async def _background_card_generator_legacy(
    deck_id: int,
    text_value: str,
    source_node_id: str = None,
    desired_card_count: int = 10,
    image_path: str = None,
    model_name: str = None,
    language: str = "ru",
    custom_prompt: str = "",
    forced_card_type: str = "auto",
    manual_count: bool = False,
    generation_mode: str = "fast",
    tag_extraction_mode: str = "auto",
    output_profile: str = "anki",
):
    """generation no-BAT count-first mass generator (preserved for reproducibility)."""
    db = SessionLocal()
    started_at = datetime.now()
    run_id = f"gen-d{deck_id}-{uuid.uuid4().hex[:6]}"
    saved = 0
    try:
        model_name = model_name or current_model or "gemma-4-E2B-it"
        target = clamp_generation_count(desired_card_count)
        clean_text = normalize_text(text_value or "", max_chars=400_000)
        if not clean_text and not image_path:
            raise ValueError("Пустой текст после очистки")

        chunk_chars = _env_int("AIFC132_CHUNK_CHARS", _env_int("AIFC131_CHUNK_CHARS", 3000, min_value=800, max_value=12000), min_value=800, max_value=12000)
        cards_per_call = _env_int("AIFC132_CARDS_PER_CALL", _env_int("AIFC131_CARDS_PER_CALL", min(target, 40), min_value=1, max_value=GENERATION_MAX_CARD_COUNT), min_value=1, max_value=GENERATION_MAX_CARD_COUNT)
        chunks = split_text_into_chunks(clean_text, chunk_size=chunk_chars, overlap_size=0)
        if not chunks:
            chunks = [clean_text[:chunk_chars]]
        max_calls_default = max(len(chunks) * 4, math.ceil(target / max(1, cards_per_call)) * 6 + 4)
        max_calls = _env_int("AIFC132_MAX_CALLS", _env_int("AIFC131_MAX_CALLS", max_calls_default, min_value=1, max_value=max(4, GENERATION_MAX_CARD_COUNT * 4)), min_value=1, max_value=max(4, GENERATION_MAX_CARD_COUNT * 4))
        allow_duplicates = str(os.environ.get("AIFC132_ALLOW_DUPLICATES", os.environ.get("AIFC131_ALLOW_DUPLICATES", "1"))).strip().lower() not in {"0", "false", "no", "off"}

        progress_start(deck_id, total=target, message=f"Генерация: 0/{target}")
        print(f"[GEN] start deck={deck_id} run={run_id} model={model_name} target={target} cards_per_call={cards_per_call} chunks={len(chunks)} chunk_chars={chunk_chars} max_calls={max_calls} allow_duplicates={allow_duplicates}")

        def build_count_prompt(chunk: str, count_now: int, attempt_index: int) -> str:
            user_extra = (custom_prompt or "").strip()
            forced = normalize_generated_card_type(forced_card_type, "auto")
            type_schema_base, type_rules = card_type_prompt_block(forced, language)
            type_note = "\n" + type_rules
            extra_note = f"\nПожелание пользователя: {user_extra}" if user_extra else ""
            if (language or "ru").lower().startswith("en"):
                return (
                    "<bos><start_of_turn>user\n"
                    "Make as many flashcards as requested. COUNT IS THE PRIORITY.\n"
                    f"Return EXACTLY {count_now} flashcards from the text below. Do not stop early.\n"
                    "Use small facts, definitions, causes, consequences, examples and comparisons.\n"
                    "Output JSONL only: one compact JSON object per line, no markdown, no array, no comments.\n"
                    f"Schema per line: {type_schema_base[:-1]},\"mnemonic\":\"useful memory cue\",\"tags\":[\"topic\",\"term\"]}}\n"
                    "Rules: front/back must be non-empty. Short answers are allowed. Reuse the text aggressively.\n"
                    "If there are not enough major facts, split details into smaller cards.\n"
                    f"Attempt: {attempt_index}. Need exactly {count_now} lines.{type_note}{extra_note}\n\nTEXT:\n{chunk}<end_of_turn>\n<start_of_turn>model\n"
                )
            return (
                "<bos><start_of_turn>user\n"
                "Сделай карточки по тексту. РЕЖИМ: КОЛИЧЕСТВО ВАЖНЕЕ ИДЕАЛЬНОГО КАЧЕСТВА.\n"
                f"Верни РОВНО {count_now} карточек. Не останавливайся раньше.\n"
                "Вытаскивай всё: факты, определения, причины, следствия, примеры, сравнения, детали.\n"
                "Если крупных фактов мало — дроби предложения на маленькие карточки.\n"
                "Формат: только JSONL, одна строка = один JSON-объект. НЕ массив. НЕ markdown. НЕ пояснения.\n"
                f"Схема каждой строки: {type_schema_base[:-1]},\"mnemonic\":\"\"}}\n"
                "Правила: front и back не пустые. Короткие ответы разрешены. source_quote можно оставить пустым, если мешает скорости.\n"
                f"Попытка: {attempt_index}. Нужно ровно {count_now} строк.{type_note}{extra_note}\n\nТЕКСТ:\n{chunk}<end_of_turn>\n<start_of_turn>model\n"
            )

        def _norm_question(value: str) -> str:
            value = (value or "").lower().replace("ё", "е")
            value = re.sub(r"[^0-9a-zа-я]+", " ", value, flags=re.IGNORECASE)
            return re.sub(r"\s+", " ", value).strip()

        seen_questions = set()
        call_index = 0
        zero_streak = 0
        while saved < target and call_index < max_calls:
            need = target - saved
            count_now = min(cards_per_call, need)
            chunk = chunks[call_index % len(chunks)]
            prompt = build_count_prompt(chunk, count_now, call_index + 1)
            progress_update(deck_id, current=saved, total=target, message=f"Генерация: {saved}/{target}")
            print(f"[GEN] call={call_index + 1}/{max_calls} need={need} ask={count_now} chunk={call_index % len(chunks)} prompt_chars={len(prompt)}")
            t_call = time.perf_counter()
            try:
                raw_cards = await ask_litert(prompt, image_path=None, model_name=model_name, use_cache=False, prefer_tools=False)
            except Exception as e:
                err = str(e)
                print(f"[GEN] call failed: {err}")
                if saved <= 0:
                    raise
                break

            accepted = []
            for card in raw_cards:
                front = clean_card_text(str(card.get("front") or card.get("q") or card.get("question") or card.get("вопрос") or ""), 650)
                back = clean_card_text(str(card.get("back") or card.get("a") or card.get("answer") or card.get("ответ") or card.get("короткий ответ") or ""), 1400)
                quote = clean_card_text(str(card.get("source_quote") or card.get("quote") or card.get("source") or card.get("цитата") or card.get("цитата из текста") or ""), 900)
                mnemonic = clean_card_text(str(card.get("mnemonic") or card.get("hint") or card.get("мнемоника") or ""), 600)
                tag_text = _tags_for_generated_card({**card, "front": front, "back": back, "source_quote": quote}, source_tags="", tag_extraction_mode=tag_extraction_mode, max_tags=4)
                ctype = generated_card_type_for(card, front, back, quote, forced_card_type)
                if not front or not back:
                    continue
                if not allow_duplicates:
                    key = _norm_question(front)
                    if key and key in seen_questions:
                        continue
                    seen_questions.add(key)
                accepted.append({"front": front, "back": back, "source_quote": quote, "mnemonic": mnemonic, "tags": tag_text, "card_type": ctype})
                if len(accepted) >= need:
                    break

            if accepted:
                accepted = rebalance_auto_card_types(accepted, forced_card_type)
                zero_streak = 0
                min_order = db.query(func.min(Card.order)).filter(Card.deck_id == deck_id).scalar() or 0
                for card in accepted:
                    if saved >= target:
                        break
                    payload = {**card, "tags": "", "model": model_name, "status": "inbox", "image_path": ""}
                    db.add(Card(
                        front=card["front"], back=card["back"], source_quote=card.get("source_quote", ""), mnemonic=card.get("mnemonic", ""),
                        tags=None, deck_id=deck_id, status="inbox", order=min_order - saved - 1, source_node_id=source_node_id,
                        model=model_name, export_profile="qa", fields_json=card_format_payload(payload, "qa"),
                        card_type=card.get("card_type") or infer_card_type(card.get("front", ""), card.get("back", ""), card.get("source_quote", "")), image_path=None,
                    ))
                    saved += 1
                db.commit()
            else:
                zero_streak += 1

            elapsed = time.perf_counter() - t_call
            print(f"[GEN] call done raw={len(raw_cards)} accepted={len(accepted)} saved={saved}/{target} time={elapsed:.1f}s")
            call_index += 1
            if zero_streak >= max(3, min(len(chunks), 8)):
                print(f"[GEN] stopping: zero_streak={zero_streak}, saved={saved}/{target}")
                break

        elapsed_total = (datetime.now() - started_at).total_seconds()
        if saved <= 0:
            raise ValueError("Модель не дала карточек. Ничего не сохранено.")
        suffix = "" if saved >= target else f" Сохранено {saved} из {target}: модель/рантайм не добили остаток."
        progress_done(deck_id, current=saved, total=target, message=f"Готово: {saved}/{target} карточек за {elapsed_total:.1f} сек.{suffix}")
        print(f"[GEN] done deck={deck_id} run={run_id} model={model_name} saved={saved}/{target} elapsed={elapsed_total:.1f}s")

        async def auto_clean():
            await asyncio.sleep(300)
            task_progress.pop(deck_id, None)
        asyncio.create_task(auto_clean())
    except Exception as exc:
        db.rollback()
        print(f"[GEN] fatal: {exc}")
        progress_error(deck_id, f"Ошибка генерации: {exc}")
    finally:
        db.close()

def build_rescue_cards_from_evidence(*args, **kwargs) -> List[dict]:
    """Deprecated in stage113.

    Deterministic Python-made card templates are intentionally disabled. If the
    selected model under-produces, the generator uses model completion passes and
    then saves only validated model-authored cards.
    """
    return []



# ------------------------- FastAPI -------------------------

class NoCacheStaticFiles(StaticFiles):
    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        return response


app = FastAPI(title="AI Flashcards — Knowledge Graph")
STATIC_DIR = os.path.join(BASE_DIR, "static")
UPLOADS_DIR = os.path.join(BASE_DIR, "uploads")
os.makedirs(UPLOADS_DIR, exist_ok=True)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
if os.path.isdir(STATIC_DIR):
    app.mount("/static", NoCacheStaticFiles(directory=STATIC_DIR), name="static")
if os.path.isdir(UPLOADS_DIR):
    app.mount("/uploads", StaticFiles(directory=UPLOADS_DIR), name="uploads")


@app.on_event("startup")
async def startup_event():
    try:
        init_engine(model_name=current_model)
    except Exception as e:
        print(f"[STARTUP] Модель не загружена при старте: {e}")


@app.on_event("shutdown")
async def shutdown_event():
    unload_engine()


@app.get("/")
async def root():
    return FileResponse(os.path.join(BASE_DIR, "index.html"), headers={"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0", "Pragma": "no-cache"})


@app.get("/api/health")
async def health():
    return {"status": "ok", "llm": get_engine_status()}


@app.get("/api/config")
async def app_config():
    return {"generation": generation_limits()}


@app.get("/api/generation/defaults")
async def generation_defaults():
    """Return the env-derived defaults for the generation UI panel.

    The frontend renders the panel with these values on first load, then
    stores user overrides in localStorage.
    """
    return {
        "temperature": _env_float("AIFC_TEMPERATURE", 0.35, min_value=0.0, max_value=5.0),
        "top_k": _env_int("AIFC_TOP_K", 40, min_value=0, max_value=1000),
        "top_p": _env_float("AIFC_TOP_P", 0.92, min_value=0.0, max_value=1.0),
        "min_p": _env_float("AIFC_MIN_P", 0.0, min_value=0.0, max_value=1.0),
        "seed": (lambda s: int(s) if s else None)(os.environ.get("AIFC_SEED", "").strip() or None),
        "cards_per_call": _env_int("AIFC_CARDS_PER_CALL", 40, min_value=1, max_value=200),
        "no_think_default_for_thinking_models": True,
        "toggles": {
            "no_think": _env_flag("AIFC_NO_THINK", "1"),
            "force_no_think": _env_flag("AIFC_FORCE_NO_THINK", "0"),
            "quality_gate": _env_flag("AIFC_QUALITY_GATE", "0"),
            "evidence_select": _env_flag("AIFC_EVIDENCE_SELECT", "0"),
            "stream_gen": _env_flag("AIFC_STREAM_GENERATION", "1"),
            "filter_thinking": _env_flag("AIFC_FILTER_THINKING", "1"),
            "allow_duplicates": _env_flag("AIFC_ALLOW_DUPLICATES", "1"),
            "generate_tags": _env_flag("AIFC_GENERATE_TAGS", "0"),
            "generate_mnemonics": _env_flag("AIFC_GENERATE_MNEMONICS", "0"),
        },
    }


@app.get("/api/generation/presets")
async def generation_presets():
    """Named presets for the UI quick-switcher."""
    return {
        "presets": [
            {
                "id": "fast",
                "title": "⚡ Быстро",
                "description": "Массовая генерация: главное — добрать выбранное количество.",
                "settings": {
                    "temperature": 0.35, "top_k": 40, "top_p": 0.92, "min_p": 0.0,
                    "no_think": True, "quality_gate": False,
                    "evidence_select": False, "stream_gen": True,
                    "filter_thinking": True, "allow_duplicates": True,
                    "generate_tags": False, "generate_mnemonics": False,
                    "cards_per_call": 40,
                },
            },
            {
                "id": "balanced",
                "title": "⚖ Баланс",
                "description": "Масса + чуть спокойнее сэмплинг. Всё ещё count-first.",
                "settings": {
                    "temperature": 0.25, "top_k": 35, "top_p": 0.9, "min_p": 0.0,
                    "no_think": True, "quality_gate": False,
                    "evidence_select": False, "stream_gen": True,
                    "filter_thinking": True, "allow_duplicates": True,
                    "generate_tags": True, "generate_mnemonics": False,
                    "cards_per_call": 30,
                },
            },
            {
                "id": "quality",
                "title": "🎯 Качество",
                "description": "Более строгий prompt и спокойный sampler, но всё равно count-first: добирает выбранное число.",
                "settings": {
                    "temperature": 0.2, "top_k": 20, "top_p": 0.85, "min_p": 0.05,
                    "no_think": True, "quality_gate": False,
                    "evidence_select": False, "stream_gen": True,
                    "filter_thinking": True, "allow_duplicates": True,
                    "generate_tags": True, "generate_mnemonics": True,
                    "cards_per_call": 12,
                },
            },
            {
                "id": "deep",
                "title": "🧠 С размышлением",
                "description": "Разрешить thinking-канал у SuperGemma, но без evidence-режима, чтобы не терять количество.",
                "settings": {
                    "temperature": 0.5, "top_k": 50, "top_p": 0.95, "min_p": 0.05,
                    "no_think": False, "quality_gate": False,
                    "evidence_select": False, "stream_gen": False,
                    "filter_thinking": True, "allow_duplicates": True,
                    "generate_tags": True, "generate_mnemonics": True,
                    "cards_per_call": 6,
                },
            },
        ]
    }


@app.get("/api/decks/{deck_id}/progress")
async def get_deck_progress(deck_id: int):
    state = task_progress.get(deck_id, {"status": "idle", "current": 0, "total": 0, "message": ""})
    return _progress_enrich(state)


@app.get("/api/decks")
async def get_decks(db: Session = Depends(get_db)):
    decks = db.query(Deck).order_by(Deck.created_at.desc()).all()
    result = []
    for d in decks:
        cards = db.query(Card.model).filter(Card.deck_id == d.id).all()
        models = [row[0] for row in cards if row[0]]
        summary = summarize_deck_models(models)
        result.append({
            "id": d.id,
            "name": d.name,
            "tags": d.tags or "",
            "created_at": d.created_at.isoformat() if d.created_at else None,
            "card_count": len(cards),
            **summary,
        })
    return result


@app.post("/api/decks")
async def create_deck(deck_data: dict, db: Session = Depends(get_db)):
    name = (deck_data.get("name") or "").strip()
    if not name or name.lower() in ["новая колода", "new deck"]:
        name = datetime.now().strftime("%d.%m.%Y %H:%M")
    deck = Deck(name=name, tags=normalize_tags_string(deck_data.get("tags") or "", max_tags=24) or None)
    db.add(deck)
    db.commit()
    db.refresh(deck)
    return {"id": deck.id, "name": deck.name, "tags": deck.tags or "", "created_at": deck.created_at.isoformat()}


@app.put("/api/decks/{deck_id}")
async def update_deck(deck_id: int, deck_data: dict, db: Session = Depends(get_db)):
    deck = db.query(Deck).filter(Deck.id == deck_id).first()
    if not deck:
        raise HTTPException(status_code=404, detail="Deck not found")
    if "name" in deck_data:
        deck.name = (deck_data.get("name") or deck.name).strip()
    if "tags" in deck_data:
        deck.tags = normalize_tags_string(deck_data.get("tags") or "", max_tags=24) or None
    db.commit()
    return {"status": "success", "id": deck.id, "name": deck.name, "tags": deck.tags or ""}


@app.delete("/api/decks/{deck_id}")
async def delete_deck(deck_id: int, db: Session = Depends(get_db)):
    deck = db.query(Deck).filter(Deck.id == deck_id).first()
    if not deck:
        raise HTTPException(status_code=404, detail="Deck not found")
    db.delete(deck)
    db.commit()
    task_progress.pop(deck_id, None)
    return {"status": "success"}


@app.get("/api/decks/{deck_id}/cards")
async def get_cards(deck_id: int, db: Session = Depends(get_db)):
    cards = db.query(Card).filter(Card.deck_id == deck_id).order_by(Card.created_at.desc(), Card.order.asc()).all()
    return [
        {
            "id": c.id,
            "front": c.front or "",
            "back": c.back or "",
            "source_quote": c.source_quote or "",
            "mnemonic": c.mnemonic or "",
            "tags": c.tags or "",
            "status": c.status or "inbox",
            "due_date": c.due_date.isoformat() if c.due_date else None,
            "card_type": c.card_type or infer_card_type(c.front or "", c.back or "", c.source_quote or ""),
            "ease_factor": c.ease_factor or 2.5,
            "interval_days": c.interval_days or 0,
            "review_count": c.review_count or 0,
            "lapses": c.lapses or 0,
            "last_reviewed_at": c.last_reviewed_at.isoformat() if c.last_reviewed_at else None,
            "image_path": c.image_path or "",
            "created_at": c.created_at.isoformat() if c.created_at else None,
            "source_node_id": c.source_node_id,
            "model": c.model,
            "export_profile": getattr(c, "export_profile", None) or "anki",
            "fields_json": load_fields_json(getattr(c, "fields_json", None)),
            "x": c.x,
            "y": c.y,
        }
        for c in cards
    ]


@app.post("/api/decks/{deck_id}/cards")
async def create_card(deck_id: int, card_data: dict, db: Session = Depends(get_db)):
    deck = db.query(Deck).filter(Deck.id == deck_id).first()
    if not deck:
        raise HTTPException(status_code=404, detail="Deck not found")
    front = str(card_data.get("front") or "").strip()
    back = str(card_data.get("back") or "").strip()
    if not front and not back:
        raise HTTPException(status_code=400, detail="Введите вопрос или ответ")
    card_type = str(card_data.get("card_type") or "basic").strip().lower().replace(" ", "_")
    if card_type not in ALLOWED_GENERATED_CARD_TYPES:
        card_type = infer_card_type(front, back, str(card_data.get("source_quote") or ""))
    min_order = db.query(func.min(Card.order)).filter(Card.deck_id == deck_id).scalar() or 0
    card = Card(
        front=front or "Без вопроса",
        back=back or "",
        source_quote=str(card_data.get("source_quote") or "").strip(),
        mnemonic=str(card_data.get("mnemonic") or "").strip(),
        tags=normalize_tags_string(card_data.get("tags") or "", max_tags=24) or None,
        status=str(card_data.get("status") or "inbox").strip() or "inbox",
        card_type=card_type,
        deck_id=deck_id,
        source_node_id=(str(card_data.get("source_node_id") or "").strip() or None),
        model="manual",
        export_profile=normalize_card_format_profile(card_data.get("export_profile") or "anki"),
        order=min_order - 1,
        x=int(card_data.get("x")) if card_data.get("x") not in (None, "") else None,
        y=int(card_data.get("y")) if card_data.get("y") not in (None, "") else None,
    )
    card.fields_json = card_format_payload(card, card.export_profile)
    if card_data.get("due_date"):
        try:
            card.due_date = datetime.fromisoformat(str(card_data["due_date"]).replace("Z", "+00:00"))
        except Exception:
            card.due_date = None
    db.add(card)
    db.commit()
    db.refresh(card)
    return card_payload(card)


@app.put("/api/cards/{card_id}")
async def update_card(card_id: int, card_data: dict, db: Session = Depends(get_db)):
    card = db.query(Card).filter(Card.id == card_id).first()
    if not card:
        raise HTTPException(status_code=404, detail="Card not found")
    for field in ["front", "back", "source_quote", "mnemonic", "tags", "status", "card_type", "source_node_id", "model", "export_profile", "x", "y", "interval_days", "review_count", "lapses", "ease_factor", "image_path"]:
        if field in card_data:
            setattr(card, field, card_data[field])
    if "due_date" in card_data:
        try:
            card.due_date = datetime.fromisoformat(str(card_data["due_date"]).replace("Z", "+00:00")) if card_data["due_date"] else None
        except Exception:
            card.due_date = None
    card.export_profile = normalize_card_format_profile(card.export_profile or card_data.get("export_profile") or "anki")
    card.fields_json = card_format_payload(card, card.export_profile)
    db.commit()
    return {"status": "success", "card": card_payload(card)}


@app.delete("/api/cards/{card_id}")
async def delete_card(card_id: int, db: Session = Depends(get_db)):
    card = db.query(Card).filter(Card.id == card_id).first()
    if not card:
        raise HTTPException(status_code=404, detail="Card not found")
    db.delete(card)
    db.commit()
    return {"status": "success"}


@app.patch("/api/cards/{card_id}/status")
async def update_card_status(card_id: int, status_data: dict, db: Session = Depends(get_db)):
    card = db.query(Card).filter(Card.id == card_id).first()
    if not card:
        raise HTTPException(status_code=404, detail="Card not found")
    card.status = status_data.get("status") or card.status
    db.commit()
    return {"status": "success"}


@app.post("/api/cards/{card_id}/review")
async def review_card(card_id: int, review_data: dict, db: Session = Depends(get_db)):
    card = db.query(Card).filter(Card.id == card_id).first()
    if not card:
        raise HTTPException(status_code=404, detail="Card not found")
    rating = (review_data.get("rating") or "good").lower()
    if rating not in {"again", "hard", "good", "easy"}:
        raise HTTPException(status_code=400, detail="rating must be again/hard/good/easy")
    apply_review(card, rating)
    db.commit()
    db.refresh(card)
    return {"status":"success", "card": card_payload(card)}


@app.get("/api/decks/{deck_id}/study/queue")
async def study_queue(deck_id: int, mode: str = "due", limit: int = 50, card_type: str = "", model_kind: str = "", source_id: str = "", date: str = "", db: Session = Depends(get_db)):
    deck = db.query(Deck).filter(Deck.id == deck_id).first()
    if not deck:
        raise HTTPException(status_code=404, detail="Deck not found")
    today = next_review_date_local(0)
    q = db.query(Card).filter(Card.deck_id == deck_id)
    if card_type:
        q = q.filter(Card.card_type == card_type)
    if source_id:
        q = q.filter(Card.source_node_id == source_id)
    cards = q.order_by(Card.due_date.asc().nullsfirst(), Card.created_at.desc()).limit(1000).all()
    if model_kind:
        cards = [c for c in cards if classify_model_name(c.model).get("kind") == model_kind]
    mode = (mode or "due").lower()
    target_date = None
    if date:
        try:
            target_date = datetime.strptime(date[:10], "%Y-%m-%d")
        except Exception:
            target_date = None
    if mode == "new":
        cards = [c for c in cards if (c.review_count or 0) == 0 and (c.status or "inbox") != "done"]
    elif mode == "all":
        cards = [c for c in cards if (c.status or "inbox") != "done"]
    elif mode in {"inbox", "today", "planned", "done"}:
        cards = [c for c in cards if (c.status or "inbox") == mode]
    elif mode == "date" and target_date is not None:
        day = target_date.date()
        cards = [c for c in cards if c.due_date and c.due_date.date() == day]
    else:
        cards = [c for c in cards if (c.status or "inbox") in {"inbox", "today"} or not c.due_date or c.due_date <= today]
    limit = max(1, min(200, int(limit or 50)))
    return {"deck": {"id": deck.id, "name": deck.name}, "today": today.date().isoformat(), "cards": [card_payload(c) for c in cards[:limit]]}


@app.get("/api/decks/{deck_id}/study/stats")
async def study_stats(deck_id: int, db: Session = Depends(get_db)):
    if not db.query(Deck).filter(Deck.id == deck_id).first():
        raise HTTPException(status_code=404, detail="Deck not found")
    today = next_review_date_local(0)
    cards = db.query(Card).filter(Card.deck_id == deck_id).all()
    by_status: Dict[str, int] = {}
    by_type: Dict[str, int] = {}
    due = 0
    for c in cards:
        st = c.status or "inbox"
        by_status[st] = by_status.get(st, 0) + 1
        typ = c.card_type or infer_card_type(c.front or "", c.back or "", c.source_quote or "")
        by_type[typ] = by_type.get(typ, 0) + 1
        if st != "done" and (not c.due_date or c.due_date <= today or st in {"inbox", "today"}):
            due += 1
    return {"total": len(cards), "due": due, "by_status": by_status, "by_type": by_type}


@app.post("/api/decks/{deck_id}/cards/generate")
async def generate_cards(deck_id: int, request: dict, bg_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    deck = db.query(Deck).filter(Deck.id == deck_id).first()
    if not deck:
        raise HTTPException(status_code=404, detail="Deck not found")
    if task_progress.get(deck_id, {}).get("status") == "processing":
        raise HTTPException(status_code=409, detail="Генерация уже выполняется")

    # Совместимость со старым frontend: text/desired_card_count/model_name.
    content = request.get("content") or request.get("text") or ""
    source_node_id = request.get("source_node_id")
    card_count = request.get("card_count") or request.get("desired_card_count") or 10
    image_path = request.get("image_path")
    language = request.get("language") or "ru"
    model_name = request.get("model_name") or current_model
    custom_prompt = request.get("custom_prompt") or request.get("prompt_hint") or ""
    forced_card_type = normalize_generated_card_type(request.get("card_type") or "auto", "auto")
    manual_count = bool(request.get("manual_count"))
    generation_mode = str(request.get("generation_mode") or request.get("mode") or "fast")
    tag_extraction_mode = normalize_tag_extraction_mode(request.get("tag_extraction_mode") or request.get("tag_mode"), generation_mode=generation_mode, model_name=model_name)
    # Export format is intentionally ignored by generation. It belongs to export actions only.
    output_profile = "anki"

    # ----- generation UI-passthrough overrides -----
    # All optional. None means: use env / preset default at generation time.
    temperature = _coerce_float(request.get("temperature"))
    top_k = _coerce_int(request.get("top_k"))
    top_p = _coerce_float(request.get("top_p"))
    min_p = _coerce_float(request.get("min_p"))
    seed = _coerce_int(request.get("seed"))
    no_think = _coerce_optional_bool(request.get("no_think"))
    quality_gate = _coerce_optional_bool(request.get("quality_gate"))
    evidence_select = _coerce_optional_bool(request.get("evidence_select"))
    stream_gen = _coerce_optional_bool(request.get("stream_gen"))
    filter_thinking = _coerce_optional_bool(request.get("filter_thinking"))
    allow_duplicates = _coerce_optional_bool(request.get("allow_duplicates"))
    cards_per_call_override = _coerce_int(request.get("cards_per_call"))
    generate_tags = _coerce_optional_bool(request.get("generate_tags"))
    generate_mnemonics = _coerce_optional_bool(request.get("generate_mnemonics"))
    print(f"[API146] generate deck={deck_id} model={model_name} count={card_count} mode={generation_mode} cards_per_call={cards_per_call_override} generate_tags={generate_tags} generate_mnemonics={generate_mnemonics}")

    if not content and not image_path:
        raise HTTPException(status_code=400, detail="No content")

    bg_tasks.add_task(
        background_card_generator,
        deck_id, content, source_node_id, card_count, image_path,
        model_name, language, custom_prompt, forced_card_type,
        manual_count, generation_mode, tag_extraction_mode, output_profile,
        temperature, top_k, top_p, min_p, seed,
        no_think, quality_gate, evidence_select, stream_gen,
        filter_thinking, allow_duplicates, cards_per_call_override,
        generate_tags, generate_mnemonics,
    )
    return {"status": "processing"}


# ----- helpers for UI-passthrough request decoding -----
def _coerce_float(value):
    if value is None or value == "":
        return None
    try:
        return float(value)
    except Exception:
        return None


def _coerce_int(value):
    if value is None or value == "":
        return None
    try:
        return int(value)
    except Exception:
        return None


def _coerce_optional_bool(value):
    """Accept True/False/None/"1"/"0"/"true"/"false"/"on"/"off". Returns None if missing."""
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    s = str(value).strip().lower()
    if s in {"1", "true", "yes", "on"}:
        return True
    if s in {"0", "false", "no", "off"}:
        return False
    return None


@app.post("/api/decks/{deck_id}/upload-file")
async def upload_file_for_later(deck_id: int, file: UploadFile = File(...), db: Session = Depends(get_db)):
    if not db.query(Deck).filter(Deck.id == deck_id).first():
        raise HTTPException(status_code=404, detail="Deck not found")
    content = await file.read()
    if len(content) > 80 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Файл слишком большой")
    text_value, is_image, image_path, media = parse_upload_content(file.filename, content)
    if not text_value.strip() and not is_image:
        raise HTTPException(status_code=400, detail="Текст не найден")
    file_id = f"file_{uuid.uuid4().hex[:10]}"
    uploaded_files[file_id] = {"text": text_value, "filename": file.filename, "is_image": is_image, "image_path": image_path, "media": media}
    return {
        "file_id": file_id,
        "filename": file.filename,
        "text": text_value,
        "text_preview": (text_value[:400] + "...") if len(text_value) > 400 else text_value,
        "is_image": is_image,
        "image_path": image_path,
        "media": media,
    }


# Старый endpoint, который уже использует твой index.html.
@app.post("/api/upload")
async def upload_file_legacy(file: UploadFile = File(...)):
    content = await file.read()
    if len(content) > 80 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Файл слишком большой")
    text_value, is_image, image_path, media = parse_upload_content(file.filename, content)
    return {"filename": file.filename, "text": text_value, "is_image": is_image, "image_path": image_path, "media": media}


@app.post("/api/decks/{deck_id}/cards/generate-from-file")
async def generate_cards_from_file(deck_id: int, request: dict, bg_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    if not db.query(Deck).filter(Deck.id == deck_id).first():
        raise HTTPException(status_code=404, detail="Deck not found")
    if task_progress.get(deck_id, {}).get("status") == "processing":
        raise HTTPException(status_code=409, detail="Генерация уже выполняется")
    file_id = request.get("file_id")
    if not file_id or file_id not in uploaded_files:
        raise HTTPException(status_code=404, detail="Файл не найден")
    file_data = uploaded_files.pop(file_id)
    bg_tasks.add_task(
        background_card_generator,
        deck_id,
        file_data["text"],
        request.get("source_node_id"),
        request.get("card_count") or request.get("desired_card_count") or 10,
        file_data.get("image_path"),
        request.get("model_name") or current_model,
        request.get("language") or "ru",
        request.get("custom_prompt") or request.get("prompt_hint") or "",
        request.get("card_type") or "auto",
        bool(request.get("manual_count")),
        str(request.get("generation_mode") or request.get("mode") or "fast"),
        normalize_tag_extraction_mode(request.get("tag_extraction_mode") or request.get("tag_mode"), generation_mode=str(request.get("generation_mode") or request.get("mode") or "fast"), model_name=request.get("model_name") or current_model),
        "anki",
    )
    return {"status": "processing"}


@app.post("/api/decks/{deck_id}/cards/generate-from-url")
async def generate_from_url(deck_id: int, request: dict, bg_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    text_value = parse_url_to_text(request.get("url"))
    request = dict(request)
    request["content"] = text_value
    return await generate_cards(deck_id, request, bg_tasks, db)


@app.post("/api/decks/{deck_id}/cards/generate-from-youtube")
async def generate_from_youtube(deck_id: int, request: dict, bg_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    text_value = parse_youtube_to_text(request.get("url"))
    request = dict(request)
    request["content"] = text_value
    return await generate_cards(deck_id, request, bg_tasks, db)


@app.post("/api/parse/url")
async def parse_url_global(request: dict):
    return {"text": parse_url_to_text(request.get("url"))}


@app.post("/api/parse/youtube")
async def parse_youtube_global(request: dict):
    return {"text": parse_youtube_to_text(request.get("url"))}


@app.post("/api/decks/{deck_id}/parse-url")
async def parse_url_deck(deck_id: int, request: dict):
    return {"text": parse_url_to_text(request.get("url"))}


@app.post("/api/decks/{deck_id}/parse-youtube")
async def parse_youtube_deck(deck_id: int, request: dict):
    return {"text": parse_youtube_to_text(request.get("url"))}



# ------------------------- persistent graph API -------------------------

def card_payload(c: Card) -> dict:
    return {
        "id": c.id,
        "front": c.front or "",
        "back": c.back or "",
        "source_quote": c.source_quote or "",
        "mnemonic": c.mnemonic or "",
        "tags": c.tags or "",
        "status": c.status or "inbox",
        "due_date": c.due_date.isoformat() if c.due_date else None,
        "card_type": c.card_type or infer_card_type(c.front or "", c.back or "", c.source_quote or ""),
        "ease_factor": c.ease_factor or 2.5,
        "interval_days": c.interval_days or 0,
        "review_count": c.review_count or 0,
        "lapses": c.lapses or 0,
        "last_reviewed_at": c.last_reviewed_at.isoformat() if c.last_reviewed_at else None,
        "image_path": c.image_path or "",
        "created_at": c.created_at.isoformat() if c.created_at else None,
        "source_node_id": c.source_node_id,
        "model": c.model,
        "export_profile": getattr(c, "export_profile", None) or "anki",
        "fields_json": load_fields_json(getattr(c, "fields_json", None)),
        "model_icon": classify_model_name(c.model).get("icon"),
        "model_kind": classify_model_name(c.model).get("kind"),
        "x": c.x,
        "y": c.y,
    }


def source_payload(s: SourceNode, include_content: bool = False) -> dict:
    try:
        media = json.loads(s.media_json or "[]") if getattr(s, "media_json", None) else []
    except Exception:
        media = []
    primary_img_path = primary_image_path(media)
    primary_img_url = ""
    for item in media:
        if (item.get("kind") or "") == "image":
            primary_img_url = item.get("url") or ""
            break
    data = {
        "id": s.id,
        "deck_id": s.deck_id,
        "title": s.title or "Источник",
        "source_type": s.source_type or "text",
        "url": s.url or "",
        "preview": s.preview or make_preview(s.content or ""),
        "char_count": len(s.content or ""),
        "word_count": len(re.findall(r"\w+", s.content or "")),
        "tags": (getattr(s, "tags", None) or derive_global_tags(s.content or "", max_tags=8)),
        "color": getattr(s, "color", None) or "",
        "icon": s.icon or source_icon(s.source_type),
        "x": s.x if s.x is not None else 120,
        "y": s.y if s.y is not None else 160,
        "created_at": s.created_at.isoformat() if s.created_at else None,
        "media": media,
        "image_path": primary_img_path,
        "image_url": primary_img_url,
    }
    if include_content:
        data["content"] = s.content or ""
    return data


@app.get("/api/decks/{deck_id}/graph")
async def get_graph(deck_id: int, db: Session = Depends(get_db)):
    deck = db.query(Deck).filter(Deck.id == deck_id).first()
    if not deck:
        raise HTTPException(status_code=404, detail="Deck not found")
    sources = db.query(SourceNode).filter(SourceNode.deck_id == deck_id).order_by(SourceNode.created_at.asc()).all()
    cards = db.query(Card).filter(Card.deck_id == deck_id).order_by(Card.created_at.asc(), Card.order.asc()).all()

    source_ids = {s.id for s in sources}
    virtual_sources = []
    for sid in sorted({c.source_node_id for c in cards if c.source_node_id and c.source_node_id not in source_ids}):
        linked = [c for c in cards if c.source_node_id == sid]
        virtual_sources.append({
            "id": sid,
            "deck_id": deck_id,
            "title": "Источник",
            "source_type": "legacy",
            "url": "",
            "preview": "",
            "char_count": 0,
            "word_count": 0,
            "icon": "📄",
            "x": 120,
            "y": 160,
            "created_at": None,
            "legacy": True,
            "media": [],
            "image_path": None,
            "image_url": "",
            "color": "",
        })

    edges = []
    for c in cards:
        if c.source_node_id:
            edges.append({"id": f"src:{c.source_node_id}->card:{c.id}", "from": f"source:{c.source_node_id}", "to": f"card:{c.id}", "type": "source"})

    return {
        "deck": {"id": deck.id, "name": deck.name, "tags": deck.tags or "", "created_at": deck.created_at.isoformat() if deck.created_at else None, **summarize_deck_models([c.model for c in cards if c.model])},
        "sources": [source_payload(s) for s in sources] + virtual_sources,
        "cards": [card_payload(c) for c in cards],
        "edges": edges,
    }


@app.post("/api/decks/{deck_id}/sources")
async def create_source(deck_id: int, payload: dict, db: Session = Depends(get_db)):
    if not db.query(Deck).filter(Deck.id == deck_id).first():
        raise HTTPException(status_code=404, detail="Deck not found")
    content = normalize_text(payload.get("content") or payload.get("text") or "", max_chars=250_000)
    source_type = (payload.get("source_type") or payload.get("type") or "text").strip().lower()
    url = (payload.get("url") or "").strip()
    title = (payload.get("title") or guess_source_title(url or content, source_type)).strip()[:160]
    src = SourceNode(
        id=payload.get("id") or make_source_id(),
        deck_id=deck_id,
        title=title or "Источник",
        source_type=source_type,
        url=url or None,
        content=content,
        media_json=json.dumps(payload.get("media") or [], ensure_ascii=False) if "media" in payload else payload.get("media_json"),
        tags=normalize_tags_string(payload.get("tags") or "", max_tags=24) or None,
        color=(payload.get("color") or "").strip()[:32] or None,
        preview=make_preview(content),
        icon=payload.get("icon") or source_icon(source_type),
        x=int(payload.get("x") or 120),
        y=int(payload.get("y") or 160),
    )
    db.add(src)
    db.commit()
    db.refresh(src)
    return source_payload(src, include_content=True)


@app.get("/api/sources/{source_id}")
async def get_source(source_id: str, db: Session = Depends(get_db)):
    src = db.query(SourceNode).filter(SourceNode.id == source_id).first()
    if src:
        return source_payload(src, include_content=True)
    linked_count = db.query(Card).filter(Card.source_node_id == source_id).count()
    if linked_count:
        return {
            "id": source_id,
            "title": "Источник",
            "source_type": "legacy",
            "url": "",
            "preview": "",
            "content": "",
            "char_count": 0,
            "word_count": 0,
            "tags": "",
            "icon": "📄",
            "legacy": True,
            "media": [],
            "image_path": None,
            "image_url": "",
        }
    raise HTTPException(status_code=404, detail="Source not found")


@app.put("/api/sources/{source_id}")
async def update_source(source_id: str, payload: dict, db: Session = Depends(get_db)):
    src = db.query(SourceNode).filter(SourceNode.id == source_id).first()
    if not src:
        raise HTTPException(status_code=404, detail="Source not found")
    for field in ["title", "source_type", "url", "content", "preview", "icon", "media_json"]:
        if field in payload:
            setattr(src, field, payload[field])
    if "tags" in payload:
        src.tags = normalize_tags_string(payload.get("tags") or "", max_tags=24) or None
    if "color" in payload:
        src.color = (payload.get("color") or "").strip()[:32] or None
    if "media" in payload:
        src.media_json = json.dumps(payload.get("media") or [], ensure_ascii=False)
    if "x" in payload:
        src.x = int(float(payload["x"]))
    if "y" in payload:
        src.y = int(float(payload["y"]))
    if "content" in payload and "preview" not in payload:
        src.preview = make_preview(payload.get("content") or "")
    db.commit()
    return source_payload(src, include_content=True)


@app.delete("/api/sources/{source_id}")
async def delete_source(source_id: str, cascade: bool = True, db: Session = Depends(get_db)):
    src = db.query(SourceNode).filter(SourceNode.id == source_id).first()
    if not src:
        raise HTTPException(status_code=404, detail="Source not found")
    if cascade:
        db.query(Card).filter(Card.source_node_id == source_id).delete(synchronize_session=False)
    else:
        db.query(Card).filter(Card.source_node_id == source_id).update({"source_node_id": None}, synchronize_session=False)
    db.delete(src)
    db.commit()
    return {"status": "success"}


@app.delete("/api/sources/{source_id}/cards")
async def delete_source_cards(source_id: str, db: Session = Depends(get_db)):
    src = db.query(SourceNode).filter(SourceNode.id == source_id).first()
    if not src:
        raise HTTPException(status_code=404, detail="Source not found")
    deleted = db.query(Card).filter(Card.source_node_id == source_id).delete(synchronize_session=False)
    db.commit()
    return {"status": "success", "deleted_cards": deleted, "source_id": source_id}


@app.put("/api/cards/{card_id}/position")
async def update_card_position(card_id: int, payload: dict, db: Session = Depends(get_db)):
    card = db.query(Card).filter(Card.id == card_id).first()
    if not card:
        raise HTTPException(status_code=404, detail="Card not found")
    card.x = int(float(payload.get("x", card.x or 0)))
    card.y = int(float(payload.get("y", card.y or 0)))
    db.commit()
    return {"status": "success", "id": card.id, "x": card.x, "y": card.y}


@app.post("/api/graph/positions")
async def update_graph_positions(payload: dict, db: Session = Depends(get_db)):
    updated = 0
    for item in payload.get("nodes", []):
        kind = item.get("kind")
        node_id = item.get("id")
        if kind == "source":
            src = db.query(SourceNode).filter(SourceNode.id == str(node_id)).first()
            if src:
                src.x = int(float(item.get("x", src.x or 0)))
                src.y = int(float(item.get("y", src.y or 0)))
                updated += 1
        elif kind == "card":
            try:
                cid = int(node_id)
            except Exception:
                continue
            card = db.query(Card).filter(Card.id == cid).first()
            if card:
                card.x = int(float(item.get("x", card.x or 0)))
                card.y = int(float(item.get("y", card.y or 0)))
                updated += 1
    db.commit()
    return {"status": "success", "updated": updated}


@app.post("/api/graph/delete")
async def delete_graph_nodes(payload: dict, db: Session = Depends(get_db)):
    cards = payload.get("cards") or []
    sources = payload.get("sources") or []
    cascade_sources = bool(payload.get("cascade_sources", True))
    deleted_cards = 0
    deleted_sources = 0
    if cards:
        ids = []
        for x in cards:
            try:
                ids.append(int(x))
            except Exception:
                pass
        if ids:
            deleted_cards += db.query(Card).filter(Card.id.in_(ids)).delete(synchronize_session=False)
    for sid in sources:
        src = db.query(SourceNode).filter(SourceNode.id == str(sid)).first()
        if not src:
            continue
        if cascade_sources:
            deleted_cards += db.query(Card).filter(Card.source_node_id == src.id).delete(synchronize_session=False)
        else:
            db.query(Card).filter(Card.source_node_id == src.id).update({"source_node_id": None}, synchronize_session=False)
        db.delete(src)
        deleted_sources += 1
    db.commit()
    return {"status": "success", "deleted_cards": deleted_cards, "deleted_sources": deleted_sources}


@app.post("/api/decks/{deck_id}/layout")
async def auto_layout_deck(deck_id: int, db: Session = Depends(get_db)):
    sources = db.query(SourceNode).filter(SourceNode.deck_id == deck_id).order_by(SourceNode.created_at.asc()).all()
    cards = db.query(Card).filter(Card.deck_id == deck_id).order_by(Card.created_at.asc(), Card.order.asc()).all()
    if not sources:
        missing = sorted({c.source_node_id for c in cards if c.source_node_id})
        for i, sid in enumerate(missing):
            src = SourceNode(id=sid, deck_id=deck_id, title="Источник", source_type="legacy", content="", preview="", icon="📄", x=80, y=140+i*220)
            db.add(src)
            sources.append(src)
    sx = 80
    for i, src in enumerate(sources):
        src.x = sx
        src.y = 140 + i * 320
        linked = [c for c in cards if c.source_node_id == src.id]
        cols = 3
        for j, card in enumerate(linked):
            card.x = 500 + (j % cols) * 370
            card.y = src.y + (j // cols) * 300
    orphans = [c for c in cards if not c.source_node_id]
    for j, card in enumerate(orphans):
        card.x = 500 + (j % 3) * 370
        card.y = 120 + (j // 3) * 300
    db.commit()
    return {"status": "success"}


@app.post("/api/decks/{deck_id}/cards/normalize")
async def normalize_deck_cards(deck_id: int, db: Session = Depends(get_db)):
    deck = db.query(Deck).filter(Deck.id == deck_id).first()
    if not deck:
        raise HTTPException(status_code=404, detail="Deck not found")
    cards = db.query(Card).filter(Card.deck_id == deck_id).all()
    sources = {s.id: s for s in db.query(SourceNode).filter(SourceNode.deck_id == deck_id).all()}
    updated = 0
    for card in cards:
        src = sources.get(card.source_node_id or "")
        source_tags = derive_global_tags(src.content or src.preview or "", max_tags=5) if src else ""
        raw = {
            "front": card.front or "",
            "back": card.back or "",
            "source_quote": card.source_quote or "",
            "mnemonic": card.mnemonic or "",
        }
        fixed = postprocess_generated_card(raw, global_tags=source_tags, language="ru", output_profile="anki")
        new_tags = derive_card_tags(fixed, source_tags=source_tags, max_tags=4)
        changed = False
        for field in ["front", "back", "source_quote", "mnemonic"]:
            if getattr(card, field) != fixed[field]:
                setattr(card, field, fixed[field])
                changed = True
        if (card.tags or "") != new_tags:
            card.tags = new_tags or None
            changed = True
        if changed:
            updated += 1
    db.commit()
    return {"status": "success", "updated": updated, "total": len(cards)}


@app.get("/api/search")
async def global_search(q: str = "", limit: int = 30, db: Session = Depends(get_db)):
    query = normalize_text(q or "", max_chars=120).strip().lower()
    if not query:
        return {"query": "", "results": []}
    limit = max(1, min(80, int(limit or 30)))
    like = f"%{query}%"
    results = []
    for d in db.query(Deck).filter((Deck.name.ilike(like)) | (Deck.tags.ilike(like))).limit(limit).all():
        results.append({"kind": "deck", "deck_id": d.id, "id": d.id, "title": d.name, "subtitle": d.tags or ""})
    for s0 in db.query(SourceNode).filter((SourceNode.title.ilike(like)) | (SourceNode.preview.ilike(like)) | (SourceNode.content.ilike(like)) | (SourceNode.tags.ilike(like))).limit(limit).all():
        results.append({"kind": "source", "deck_id": s0.deck_id, "id": s0.id, "title": s0.title, "subtitle": s0.preview or ""})
    for c0 in db.query(Card).filter((Card.front.ilike(like)) | (Card.back.ilike(like)) | (Card.tags.ilike(like)) | (Card.mnemonic.ilike(like))).limit(limit).all():
        results.append({"kind": "card", "deck_id": c0.deck_id, "id": c0.id, "title": c0.front or "Карточка", "subtitle": c0.back or ""})
    return {"query": query, "results": results[:limit]}


@app.post("/api/sources/{source_id}/media")
async def upload_source_media(source_id: str, file: UploadFile = File(...), db: Session = Depends(get_db)):
    src = db.query(SourceNode).filter(SourceNode.id == source_id).first()
    if not src:
        raise HTTPException(status_code=404, detail="Source not found")
    content = await file.read()
    item = save_binary_media(base_dir=BASE_DIR, content=content, filename=file.filename or "image.png", kind="image", title=file.filename or "Изображение")
    try:
        media = json.loads(src.media_json or "[]") if src.media_json else []
    except Exception:
        media = []
    media.append(item)
    src.media_json = json.dumps(media, ensure_ascii=False)
    db.commit()
    return source_payload(src, include_content=True)



@app.delete("/api/sources/{source_id}/media")
def delete_source_media_by_key(source_id: str, key: str = "", db: Session = Depends(get_db)):
    src = db.query(SourceNode).filter(SourceNode.id == source_id).first()
    if not src:
        raise HTTPException(status_code=404, detail="Source not found")
    try:
        media = json.loads(src.media_json or "[]") if src.media_json else []
    except Exception:
        media = []
    norm_key = (key or "").strip()
    if not norm_key:
        raise HTTPException(status_code=400, detail="Media key is required")
    next_media = []
    removed = []
    for item in media:
        item_key = str((item or {}).get("url") or (item or {}).get("path") or "").strip()
        if item_key == norm_key:
            removed.append(item)
        else:
            next_media.append(item)
    if not removed:
        raise HTTPException(status_code=404, detail="Media not found")
    src.media_json = json.dumps(next_media, ensure_ascii=False)
    db.commit()
    return {"status": "success", "removed": removed, "source": source_payload(src, include_content=True)}

@app.delete("/api/sources/{source_id}/media/{media_index}")
def delete_source_media(source_id: str, media_index: int, db: Session = Depends(get_db)):
    src = db.query(SourceNode).filter(SourceNode.id == source_id).first()
    if not src:
        raise HTTPException(status_code=404, detail="Source not found")
    try:
        media = json.loads(src.media_json or "[]") if src.media_json else []
    except Exception:
        media = []
    if media_index < 0 or media_index >= len(media):
        raise HTTPException(status_code=404, detail="Media not found")
    removed = media.pop(media_index)
    src.media_json = json.dumps(media, ensure_ascii=False)
    db.commit()
    # Do not delete the physical file here: extracted PDF images may be shared by
    # cache/digest names. This only removes it from the source inspector.
    return {"status": "success", "removed": removed, "source": source_payload(src, include_content=True)}


@app.post("/api/cards/{card_id}/image")
async def upload_card_image(card_id: int, file: UploadFile = File(...), db: Session = Depends(get_db)):
    card = db.query(Card).filter(Card.id == card_id).first()
    if not card:
        raise HTTPException(status_code=404, detail="Card not found")
    content = await file.read()
    item = save_binary_media(base_dir=BASE_DIR, content=content, filename=file.filename or "card.png", kind="image", title=file.filename or "Изображение карточки")
    card.image_path = item.get("url") or item.get("path")
    db.commit()
    return {"status": "success", "card": card_payload(card), "media": item}

# ------------------------- import/export -------------------------

@app.post("/api/decks/{deck_id}/import/cards")
async def import_cards(deck_id: int, file: UploadFile = File(...), db: Session = Depends(get_db)):
    deck = db.query(Deck).filter(Deck.id == deck_id).first()
    if not deck:
        raise HTTPException(status_code=404, detail="Deck not found")
    content = await file.read()
    lower = (file.filename or "").lower()
    if lower.endswith(".apkg"):
        cards = parse_anki_apkg(content)
        source = "anki-import"
    elif lower.endswith(".json"):
        cards = parse_graph_json_cards(content)
        source = "json-graph-import"
    elif lower.endswith((".csv", ".tsv", ".txt")):
        cards = parse_csv_cards(content)
        source = "quizlet-csv-import"
    else:
        raise HTTPException(status_code=400, detail="Поддерживаются .apkg, .json, .csv, .tsv, .txt")
    src = SourceNode(
        id=make_source_id(),
        deck_id=deck_id,
        title=f"Импорт: {file.filename or 'cards'}",
        source_type="import",
        content=f"Импортировано из {file.filename or 'файла'}",
        preview=f"Готовые карточки из {file.filename or 'файла'}",
        icon="⬇️",
        x=120,
        y=160,
    )
    db.add(src)
    db.flush()
    for item in cards:
        item["source_node_id"] = src.id
    saved = save_imported_cards(db, deck_id, cards, model_name=source)
    return {"status": "success", "imported": saved, "found": len(cards), "source_id": src.id}


@app.post("/api/decks/{deck_id}/import/quizlet")
async def import_quizlet(deck_id: int, file: UploadFile = File(...), db: Session = Depends(get_db)):
    return await import_cards(deck_id, file, db)


@app.post("/api/decks/{deck_id}/import/anki")
async def import_anki(deck_id: int, file: UploadFile = File(...), db: Session = Depends(get_db)):
    return await import_cards(deck_id, file, db)


def query_export_cards(deck_id: int, card_ids: Optional[str], source_id: Optional[str], db: Session) -> Tuple[Deck, List[Card]]:
    deck = db.query(Deck).filter(Deck.id == deck_id).first()
    if not deck:
        raise HTTPException(status_code=404, detail="Deck not found")
    query = db.query(Card).filter(Card.deck_id == deck_id)
    if card_ids:
        ids = [int(x) for x in card_ids.split(",") if x.strip().isdigit()]
        if ids:
            query = query.filter(Card.id.in_(ids))
    elif source_id:
        query = query.filter(Card.source_node_id == source_id)
    cards = query.order_by(Card.order.asc(), Card.created_at.asc()).all()
    if not cards:
        raise HTTPException(status_code=404, detail="No cards to export")
    return deck, cards


@app.get("/api/decks/{deck_id}/export/anki")
async def export_deck_anki(deck_id: int, card_ids: Optional[str] = None, source_id: Optional[str] = None, db: Session = Depends(get_db)):
    try:
        import genanki
    except ImportError:
        raise HTTPException(status_code=500, detail="Установите genanki")
    deck, cards = query_export_cards(deck_id, card_ids, source_id, db)
    model = genanki.Model(
        1607392319,
        "AI Flashcards Basic",
        fields=[{"name": "Question"}, {"name": "Answer"}, {"name": "Source"}, {"name": "Mnemonic"}, {"name": "Image"}],
        templates=[
            {
                "name": "Card 1",
                "qfmt": "{{Question}}<br>{{Image}}",
                "afmt": '{{FrontSide}}<hr id="answer">{{Answer}}<br><br><small>{{Source}}</small><br><em>{{Mnemonic}}</em>',
            }
        ],
        css=""".card{font-family:Arial,sans-serif;font-size:20px;text-align:left}.card img{max-width:100%;max-height:320px}small{color:#666}em{color:#777}""",
    )
    anki_deck = genanki.Deck(abs(hash(deck.name)) % (10**10), deck.name)
    media_files = []
    for card in cards:
        fields = fields_for_export(card, "anki")
        tags = [t.strip().lstrip("#") for t in (card.tags or "").split() if t.strip()]
        image_html = ""
        img = fields.get("Image") or getattr(card, "image_path", None) or ""
        if img:
            img = str(img)
            img_abs = os.path.join(BASE_DIR, img.lstrip("/")) if img.startswith("/uploads/") else img
            if os.path.exists(img_abs):
                media_files.append(img_abs)
                image_html = f'<img src="{html.escape(os.path.basename(img_abs))}">'
        question = clean_card_text(fields.get("Question") or card.front or "")
        answer = clean_card_text(fields.get("Answer") or card.back or "")
        source = clean_card_text(fields.get("Source") or card.source_quote or "")
        mnemonic = clean_card_text(fields.get("Mnemonic") or card.mnemonic or "")
        anki_deck.add_note(genanki.Note(model=model, fields=[question, answer, source, mnemonic, image_html], tags=tags))
    package = genanki.Package(anki_deck)
    package.media_files = list(dict.fromkeys(media_files))
    with tempfile.NamedTemporaryFile(suffix=".apkg", delete=False) as tmp:
        tmp_path = tmp.name
    try:
        package.write_to_file(tmp_path)
        with open(tmp_path, "rb") as f:
            file_bytes = f.read()
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
    encoded_filename = quote(f"{deck.name}.apkg")
    return Response(file_bytes, media_type="application/apkg", headers={"Content-Disposition": f"attachment; filename*=UTF-8''{encoded_filename}"})


@app.get("/api/decks/{deck_id}/export/quizlet")
async def export_deck_quizlet(deck_id: int, card_ids: Optional[str] = None, source_id: Optional[str] = None, db: Session = Depends(get_db)):
    deck, cards = query_export_cards(deck_id, card_ids, source_id, db)
    rows = []
    for c in cards:
        fields = fields_for_export(c, "quizlet")
        term = one_line(fields.get("Term") or c.front or "", 500)
        definition = one_line(fields.get("Definition") or c.back or "", 1500)
        if term and definition:
            rows.append(f"{term}\t{definition}")
    body = "\n".join(rows)
    encoded_filename = quote(f"{deck.name}_quizlet.tsv")
    return Response(
        body.encode("utf-8-sig"),
        media_type="text/tab-separated-values; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{encoded_filename}"},
    )


@app.get("/api/decks/{deck_id}/export/csv")
async def export_deck_csv(deck_id: int, card_ids: Optional[str] = None, source_id: Optional[str] = None, db: Session = Depends(get_db)):
    deck, cards = query_export_cards(deck_id, card_ids, source_id, db)
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["front", "back", "source_quote", "mnemonic", "tags", "status", "card_type", "image_path", "export_profile", "fields_json"])
    for c in cards:
        profile = getattr(c, "export_profile", None) or "csv"
        fields = fields_for_export(c, "csv")
        writer.writerow([
            fields.get("front") or c.front or "",
            fields.get("back") or c.back or "",
            fields.get("source_quote") or c.source_quote or "",
            fields.get("mnemonic") or c.mnemonic or "",
            fields.get("tags") or c.tags or "",
            fields.get("status") or c.status or "",
            fields.get("card_type") or c.card_type or "basic",
            fields.get("image_path") or c.image_path or "",
            profile,
            getattr(c, "fields_json", None) or card_format_payload(c, profile),
        ])
    encoded_filename = quote(f"{deck.name}.csv")
    return Response(buffer.getvalue().encode("utf-8-sig"), media_type="text/csv; charset=utf-8", headers={"Content-Disposition": f"attachment; filename*=UTF-8''{encoded_filename}"})


@app.get("/api/decks/{deck_id}/export/pdf")
async def export_deck_pdf(deck_id: int, card_ids: Optional[str] = None, source_id: Optional[str] = None, db: Session = Depends(get_db)):
    try:
        from reportlab.pdfgen import canvas
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.utils import simpleSplit
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont
    except ImportError:
        raise HTTPException(status_code=500, detail="Установите reportlab")

    deck, cards = query_export_cards(deck_id, card_ids, source_id, db)
    font_path = get_cyrillic_font()
    font_name = "Helvetica"
    if font_path:
        try:
            pdfmetrics.registerFont(TTFont("Cyrillic", font_path))
            font_name = "Cyrillic"
        except Exception:
            pass

    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4
    y = height - 40
    c.setFont(font_name, 14)
    c.drawString(50, y, f"Колода: {deck.name}")
    y -= 30

    for idx, card in enumerate(cards, 1):
        fields = fields_for_export(card, "pdf")
        heading = clean_card_text(fields.get("Heading") or card.front or "", 500)
        summary = clean_card_text(fields.get("Summary") or card.back or "", 1200)
        evidence = clean_card_text(fields.get("Evidence") or card.source_quote or "", 900)
        cue = clean_card_text(fields.get("Cue") or card.mnemonic or "", 500)
        raw_pdf_tags = (fields.get("tags") if isinstance(fields, dict) else "") or card.tags or ""
        if isinstance(raw_pdf_tags, (list, tuple, set)):
            raw_pdf_tags = " ".join(str(x) for x in raw_pdf_tags)
        tag_line = normalize_tags_string(raw_pdf_tags, max_tags=8)
        block_lines = []
        c.setFont(font_name, 11)
        block_lines.extend((11, line) for line in simpleSplit(f"{idx}. {heading}", font_name, 11, width - 100))
        c.setFont(font_name, 10)
        block_lines.extend((10, line) for line in simpleSplit(f"Ответ: {summary}", font_name, 10, width - 100))
        if evidence:
            block_lines.extend((8, line) for line in simpleSplit(f"Источник: {evidence}", font_name, 8, width - 100))
        if cue:
            block_lines.extend((8, line) for line in simpleSplit(f"Подсказка: {cue}", font_name, 8, width - 100))
        if tag_line:
            block_lines.extend((8, line) for line in simpleSplit(f"Теги: {tag_line}", font_name, 8, width - 100))
        needed_h = 15 * len(block_lines) + 10
        if y - needed_h < 40:
            c.showPage(); y = height - 40
        for size, line in block_lines:
            c.setFont(font_name, size)
            c.drawString(50, y, line); y -= 14 if size >= 10 else 12
        y -= 8
    c.save()
    buffer.seek(0)
    encoded_filename = quote(f"{deck.name}.pdf")
    return Response(buffer.read(), media_type="application/pdf", headers={"Content-Disposition": f"attachment; filename*=UTF-8''{encoded_filename}"})


@app.get("/api/decks/{deck_id}/export/json")
async def export_deck_json(deck_id: int, card_ids: Optional[str] = None, source_id: Optional[str] = None, db: Session = Depends(get_db)):
    deck, cards = query_export_cards(deck_id, card_ids, source_id, db)
    source_ids = {c.source_node_id for c in cards if c.source_node_id}
    sources = db.query(SourceNode).filter(SourceNode.deck_id == deck_id, SourceNode.id.in_(source_ids)).all() if source_ids else []
    nodes = []
    for c in cards:
        fields = fields_for_export(c, "json")
        nodes.append({
            "id": f"card:{c.id}",
            "card_id": c.id,
            "node_label": fields.get("node_label") or c.front or "",
            "relation_type": fields.get("relation_type") or c.card_type or "basic",
            "explanation": fields.get("explanation") or c.back or "",
            "evidence": fields.get("evidence") or c.source_quote or "",
            "tags": fields.get("tags") or [t for t in (c.tags or "").split() if t],
            "source_node_id": c.source_node_id,
            "export_profile": getattr(c, "export_profile", None) or "json",
            "fields": fields,
        })
    payload = {
        "schema": "ai_flashcards.graph.v2",
        "deck": {"id": deck.id, "name": deck.name, "tags": deck.tags or "", "created_at": deck.created_at.isoformat() if deck.created_at else None},
        "sources": [source_payload(s, include_content=True) for s in sources],
        "nodes": nodes,
        "cards": [card_payload(c) for c in cards],
        "edges": [{"from": f"source:{c.source_node_id}", "to": f"card:{c.id}", "type": "source"} for c in cards if c.source_node_id],
        "exported_at": datetime.now().isoformat(),
    }
    data = json_dumps(payload).encode("utf-8")
    encoded_filename = quote(f"{deck.name}_graph.json")
    return Response(data, media_type="application/json; charset=utf-8", headers={"Content-Disposition": f"attachment; filename*=UTF-8''{encoded_filename}"})


# ------------------------- model/review -------------------------


@app.get("/api/generation/profiles")
async def generation_profiles():
    return {"profiles": export_profile_options()}

@app.post("/api/model/switch")
async def switch_model(request: dict = None, model_name: Optional[str] = None):
    global current_model
    requested = model_name or (request or {}).get("model_name")
    backend = (request or {}).get("backend")
    known = get_engine_status().get("models", [])
    if requested not in known:
        raise HTTPException(status_code=400, detail="Unknown model")
    if requested.startswith("llama-server-"):
        current_model = requested
        return {"status": "switched", "current_model": current_model, "llm": get_engine_status()}
    if requested == current_model and get_engine_status().get("loaded") and not backend:
        return {"status": "already", "current_model": current_model, "llm": get_engine_status()}
    await asyncio.to_thread(init_engine, requested, backend)
    current_model = requested
    return {"status": "switched", "current_model": current_model, "llm": get_engine_status()}


@app.get("/api/model/current")
async def get_current_model():
    return {"current_model": current_model, "llm": get_engine_status()}


@app.get("/api/model/list")
async def get_model_list():
    return get_engine_status()


@app.post("/api/model/preload")
async def preload_model(request: dict = None):
    requested = (request or {}).get("model_name") or current_model
    backend = (request or {}).get("backend")
    known = get_engine_status().get("models", [])
    if requested not in known:
        raise HTTPException(status_code=400, detail="Unknown model")
    if requested.startswith("llama-server-"):
        return {"status": "external", "message": "llama-server model uses already running external server", "llm": get_engine_status()}
    await asyncio.to_thread(init_engine, requested, backend)
    return {"status": "loaded", "llm": get_engine_status()}


@app.post("/api/model/benchmark")
async def benchmark_model(request: dict = None):
    requested = (request or {}).get("model_name") or current_model
    language = (request or {}).get("language") or "ru"
    known = get_engine_status().get("models", [])
    if requested not in known:
        raise HTTPException(status_code=400, detail="Unknown model")
    try:
        return await benchmark_litert(requested, language=language)
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/model/benchmark/all")
async def benchmark_all_models(request: dict = None):
    language = (request or {}).get("language") or "ru"
    out = []
    for name in ["gemma-4-E2B-it", "supergemma4-e4b-abliterated"]:
        try:
            out.append(await benchmark_litert(name, language=language))
        except Exception as e:
            out.append({"model": name, "error": str(e), "llm": get_engine_status()})
    return {"results": out}


@app.delete("/api/model/cache")
async def clear_model_cache():
    deleted = await asyncio.to_thread(clear_llm_cache)
    return {"status": "cleared", "deleted": deleted}


@app.post("/api/cards/{card_id}/quick-review")
async def quick_review_card(card_id: int, review_data: dict, db: Session = Depends(get_db)):
    card = db.query(Card).filter(Card.id == card_id).first()
    if not card:
        raise HTTPException(status_code=404, detail="Card not found")
    rating = (review_data.get("rating") or "good").lower()
    if rating == "medium":
        rating = "good"
    if rating not in ["again", "hard", "good", "easy"]:
        raise HTTPException(status_code=400, detail="Invalid rating")
    apply_review(card, rating)
    db.commit()
    return {"status": "updated", "card": card_payload(card)}
