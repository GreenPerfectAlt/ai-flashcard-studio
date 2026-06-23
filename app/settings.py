"""Central runtime settings for AI Flashcards.

The app used to have many UI/generation limits embedded directly in routes,
prompts and JS defaults.  Keeping them here makes changes explicit and lets the
frontend read the same values through /api/config.
"""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent.parent


def env_int(name: str, default: int, *, min_value: int = 0, max_value: int | None = None) -> int:
    raw = os.environ.get(name, "").strip()
    try:
        value = int(raw) if raw else int(default)
    except (TypeError, ValueError):
        value = int(default)
    value = max(min_value, value)
    return min(value, max_value) if max_value is not None else value


def env_float(name: str, default: float, *, min_value: float = 0.0, max_value: float | None = None) -> float:
    raw = os.environ.get(name, "").strip()
    try:
        value = float(raw) if raw else float(default)
    except (TypeError, ValueError):
        value = float(default)
    value = max(min_value, value)
    return min(value, max_value) if max_value is not None else value


def env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name, "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "y", "on", "да"}


def env_list(name: str, default: list[str]) -> list[str]:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return list(default)
    return [x.strip() for x in raw.split(",") if x.strip()]


@dataclass(frozen=True)
class GenerationSettings:
    default_cards: int = field(default_factory=lambda: env_int("AIFC_DEFAULT_CARD_COUNT", 10, min_value=1))
    max_cards: int = field(default_factory=lambda: env_int("AIFC_MAX_CARD_COUNT", 200, min_value=1))
    litert_batch_cards: int = field(default_factory=lambda: env_int("AIFC_LITERT_BATCH_CARDS", 8, min_value=1))
    server_batch_cards: int = field(default_factory=lambda: env_int("AIFC_SERVER_BATCH_CARDS", 12, min_value=1))
    completion_retries: int = field(default_factory=lambda: env_int("AIFC_GENERATION_RETRIES", 4, min_value=0))
    auto_words_per_card: int = field(default_factory=lambda: env_int("AIFC_AUTO_WORDS_PER_CARD", 34, min_value=8))
    avoid_fronts_limit: int = field(default_factory=lambda: env_int("AIFC_AVOID_FRONTS_LIMIT", 32, min_value=1))
    min_text_words_for_many_cards: int = field(default_factory=lambda: env_int("AIFC_MIN_TEXT_WORDS_FOR_MANY_CARDS", 180, min_value=10))
    target_cards_floor: int = field(default_factory=lambda: env_int("AIFC_TARGET_CARDS_FLOOR", 6, min_value=1))
    fallback_sentence_min_chars: int = field(default_factory=lambda: env_int("AIFC_FALLBACK_SENTENCE_MIN_CHARS", 45, min_value=10))
    fallback_sentence_max_chars: int = field(default_factory=lambda: env_int("AIFC_FALLBACK_SENTENCE_MAX_CHARS", 360, min_value=80))


@dataclass(frozen=True)
class TextSettings:
    max_text_chars: int = field(default_factory=lambda: env_int("AIFC_TEXT_MAX_CHARS", 120_000, min_value=1_000))
    max_source_text_chars: int = field(default_factory=lambda: env_int("AIFC_SOURCE_TEXT_MAX_CHARS", 250_000, min_value=10_000))
    chunk_chars: int = field(default_factory=lambda: env_int("AIFC_CHUNK_CHARS", 5_200, min_value=1_000))
    chunk_overlap_chars: int = field(default_factory=lambda: env_int("AIFC_CHUNK_OVERLAP_CHARS", 260, min_value=0))
    min_chunk_chars: int = field(default_factory=lambda: env_int("AIFC_MIN_CHUNK_CHARS", 80, min_value=1))
    preview_chars: int = field(default_factory=lambda: env_int("AIFC_PREVIEW_CHARS", 280, min_value=20))
    title_chars: int = field(default_factory=lambda: env_int("AIFC_TITLE_CHARS", 120, min_value=20))
    short_title_chars: int = field(default_factory=lambda: env_int("AIFC_SHORT_TITLE_CHARS", 90, min_value=20))
    card_front_chars: int = field(default_factory=lambda: env_int("AIFC_CARD_FRONT_CHARS", 360, min_value=80))
    card_back_chars: int = field(default_factory=lambda: env_int("AIFC_CARD_BACK_CHARS", 900, min_value=120))
    card_quote_chars: int = field(default_factory=lambda: env_int("AIFC_CARD_QUOTE_CHARS", 700, min_value=120))
    card_mnemonic_chars: int = field(default_factory=lambda: env_int("AIFC_CARD_MNEMONIC_CHARS", 360, min_value=80))
    upload_max_mb: int = field(default_factory=lambda: env_int("AIFC_UPLOAD_MAX_MB", 80, min_value=1))

    @property
    def upload_max_bytes(self) -> int:
        return self.upload_max_mb * 1024 * 1024


@dataclass(frozen=True)
class StudySettings:
    default_queue_limit: int = field(default_factory=lambda: env_int("AIFC_STUDY_DEFAULT_LIMIT", 50, min_value=1))
    max_queue_limit: int = field(default_factory=lambda: env_int("AIFC_STUDY_MAX_LIMIT", 200, min_value=1))
    query_prefetch_limit: int = field(default_factory=lambda: env_int("AIFC_STUDY_PREFETCH_LIMIT", 1000, min_value=50))


@dataclass(frozen=True)
class LayoutSettings:
    source_x: int = field(default_factory=lambda: env_int("AIFC_LAYOUT_SOURCE_X", 80, min_value=-100_000))
    source_y: int = field(default_factory=lambda: env_int("AIFC_LAYOUT_SOURCE_Y", 120, min_value=-100_000))
    card_x: int = field(default_factory=lambda: env_int("AIFC_LAYOUT_CARD_X", 510, min_value=-100_000))
    source_gap_y: int = field(default_factory=lambda: env_int("AIFC_LAYOUT_SOURCE_GAP_Y", 220, min_value=20))
    card_gap_x: int = field(default_factory=lambda: env_int("AIFC_LAYOUT_CARD_GAP_X", 380, min_value=40))
    card_gap_y: int = field(default_factory=lambda: env_int("AIFC_LAYOUT_CARD_GAP_Y", 320, min_value=40))
    node_w: int = field(default_factory=lambda: env_int("AIFC_NODE_WIDTH", 316, min_value=120))
    node_h: int = field(default_factory=lambda: env_int("AIFC_NODE_HEIGHT", 262, min_value=120))
    source_w: int = field(default_factory=lambda: env_int("AIFC_SOURCE_WIDTH", 340, min_value=140))
    source_h: int = field(default_factory=lambda: env_int("AIFC_SOURCE_HEIGHT", 210, min_value=100))
    default_scale: float = field(default_factory=lambda: env_float("AIFC_DEFAULT_SCALE", 0.82, min_value=0.1, max_value=4.0))
    default_offset_x: int = field(default_factory=lambda: env_int("AIFC_DEFAULT_OFFSET_X", 80, min_value=-100_000))
    default_offset_y: int = field(default_factory=lambda: env_int("AIFC_DEFAULT_OFFSET_Y", 100, min_value=-100_000))


@dataclass(frozen=True)
class AppSettings:
    default_model: str = field(default_factory=lambda: os.environ.get("AIFC_DEFAULT_MODEL", "gemma-4-E2B-it"))
    cache_cleanup_seconds: int = field(default_factory=lambda: env_int("AIFC_PROGRESS_CLEANUP_SECONDS", 300, min_value=5))
    generation: GenerationSettings = field(default_factory=GenerationSettings)
    text: TextSettings = field(default_factory=TextSettings)
    study: StudySettings = field(default_factory=StudySettings)
    layout: LayoutSettings = field(default_factory=LayoutSettings)


SETTINGS = AppSettings()

CARD_TYPES: dict[str, dict[str, str]] = {
    "basic": {"label": "Вопрос / ответ", "icon": "◇"},
    "definition": {"label": "Определение", "icon": "◧"},
    "fact": {"label": "Факт", "icon": "•"},
    "concept": {"label": "Понимание", "icon": "◈"},
    "cloze": {"label": "Пропуск", "icon": "□"},
    "true_false": {"label": "Верно / неверно", "icon": "✓"},
    "mcq": {"label": "Выбор ответа", "icon": "◉"},
}
CARD_TYPE_VALUES = set(CARD_TYPES)

CARD_STATUSES: dict[str, str] = {
    "inbox": "Входящие",
    "today": "Сегодня",
    "planned": "План",
    "done": "Готово",
}
REVIEW_RATINGS = {"again", "hard", "good", "easy"}

SOURCE_TYPES: dict[str, dict[str, str]] = {
    "url": {"label": "Ссылка", "icon": "🌐"},
    "youtube": {"label": "YouTube", "icon": "▶️"},
    "pdf": {"label": "PDF", "icon": "📕"},
    "docx": {"label": "DOCX", "icon": "📘"},
    "file": {"label": "Файл", "icon": "📁"},
    "image": {"label": "Изображение", "icon": "🖼️"},
    "import": {"label": "Импорт", "icon": "⬇️"},
    "text": {"label": "Текстовый источник", "icon": "📄"},
    "legacy": {"label": "Источник", "icon": "📄"},
}

MODEL_ALIASES: dict[str, dict[str, str]] = {
    "quality": {"icon": "🧠", "label": "SuperGemma"},
    "fast": {"icon": "⚡", "label": "Gemma E2B"},
    "import": {"icon": "📦", "label": "import"},
    "manual": {"icon": "📚", "label": "manual"},
    "mixed": {"icon": "🧩", "label": "mixed models"},
    "empty": {"icon": "📚", "label": "empty"},
    "cloud": {"icon": "☁️", "label": "OpenRouter"},
    "other": {"icon": "📚", "label": "other"},
}

GENERIC_BAD_QUESTION_SUBJECTS = {
    "количество", "число", "цель", "год", "тема", "персонаж", "факт", "функция",
    "состав", "блок", "барьер", "основание", "основании", "данные", "значение",
}

PROMPT_ARTIFACT_MARKERS = {
    "валидный json", "компактный json", "без markdown", "source_quote", "mnemonic",
    "хэштегов", "хештегов", "вернуть только", "answer must", "question must",
    "do not invent", "теги источника", "формат ответа", "поле является",
    "требование предъявляется", "условие применяется", "мнемоника должна",
    "цитата должна", "только подсказкой", "что такое задолго",
}

STOPWORDS_RU_EXTRA = {
    "это", "как", "или", "для", "при", "над", "под", "что", "его", "она", "они", "оно",
    "так", "уже", "еще", "ещё", "без", "был", "была", "были", "будет", "является",
    "который", "которая", "которые", "также", "после", "между", "через", "если",
    "более", "менее", "очень", "может", "могут", "всех", "все", "всё", "этот",
    "эта", "эти", "можно", "нужно", "например", "около", "раздел", "статья",
    "таблица", "список", "ссылки", "примечания", "литература", "вопрос", "ответ",
    "цитата", "мнемоника", "карточка", "карточки",
} | GENERIC_BAD_QUESTION_SUBJECTS


def settings_payload() -> dict[str, Any]:
    data = asdict(SETTINGS)
    data.update(
        {
            "card_types": CARD_TYPES,
            "card_statuses": CARD_STATUSES,
            "source_types": SOURCE_TYPES,
            "review_ratings": sorted(REVIEW_RATINGS),
        }
    )
    return data


def clamp_card_count(value: Any, default: int | None = None) -> int:
    fallback = SETTINGS.generation.default_cards if default is None else int(default)
    try:
        requested = int(value if value not in (None, "") else fallback)
    except (TypeError, ValueError):
        requested = fallback
    return max(1, min(SETTINGS.generation.max_cards, requested))


def generation_batch_size(model_name: str | None = None) -> int:
    name = str(model_name or "").lower()
    size = SETTINGS.generation.server_batch_cards if name.startswith("llama-server") or "server" in name else SETTINGS.generation.litert_batch_cards
    return max(1, min(SETTINGS.generation.max_cards, int(size)))
