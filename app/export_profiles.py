from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from app.card_formats import schema_options

ProfileId = Literal["anki", "quizlet", "csv", "pdf", "json"]


@dataclass(frozen=True)
class GenerationExportProfile:
    """Declarative generation target used by prompts, validators and UI.

    The profile describes the export/import destination, not a source topic and
    not a fixed card count. It intentionally contains no target-count or
    subject-specific rules.
    """

    id: ProfileId
    label: str
    export_format: str
    front_kind: str
    default_card_type: str
    allow_term_front: bool
    answer_max_words: int
    prompt_ru: str
    prompt_en: str


GENERATION_EXPORT_PROFILES: dict[str, GenerationExportProfile] = {
    "anki": GenerationExportProfile(
        id="anki",
        label="📦 Anki .apkg",
        export_format="anki",
        front_kind="recall_question",
        default_card_type="basic",
        allow_term_front=False,
        answer_max_words=55,
        prompt_ru=(
            "Назначение: экспорт в Anki .apkg. Генерируй Anki Note, а не общий текст: "
            "q/front станет полем Question, a/back станет Answer, s станет Source, m станет Mnemonic. "
            "Один факт — одна карточка; вопрос должен проверять активное вспоминание, ответ — быть самодостаточным."
        ),
        prompt_en=(
            "Target: Anki .apkg export. Generate an Anki Note, not generic text: "
            "q/front maps to Question, a/back maps to Answer, s maps to Source, m maps to Mnemonic. "
            "One fact per card; question tests active recall and answer is self-contained."
        ),
    ),
    "quizlet": GenerationExportProfile(
        id="quizlet",
        label="🧩 Quizlet TSV",
        export_format="quizlet",
        front_kind="term_or_short_prompt",
        default_card_type="definition",
        allow_term_front=True,
        answer_max_words=38,
        prompt_ru=(
            "Назначение: экспорт в Quizlet TSV. Генерируй строго пары Term/Definition: "
            "q/front станет Term, a/back станет Definition. Term должен быть коротким термином/понятием, "
            "definition — кратким объяснением. Без табов, markdown, длинных вопросов и цитат в Term."
        ),
        prompt_en=(
            "Target: Quizlet TSV export. Generate strict Term/Definition pairs: "
            "q/front maps to Term and a/back maps to Definition. Term is a short concept; definition is concise. "
            "No tabs, markdown, long questions, or quotes in Term."
        ),
    ),
    "csv": GenerationExportProfile(
        id="csv",
        label="📊 CSV",
        export_format="csv",
        front_kind="structured_question",
        default_card_type="basic",
        allow_term_front=False,
        answer_max_words=65,
        prompt_ru=(
            "Назначение: экспорт в CSV. Генерируй строки таблицы с явными колонками: "
            "front, back, source_quote, mnemonic, tags, card_type. Без markdown и случайных заголовков. "
            "Каждая строка должна читаться как самостоятельная запись."
        ),
        prompt_en=(
            "Target: CSV export. Generate clean table rows with explicit columns: "
            "front, back, source_quote, mnemonic, tags, card_type. No markdown or random headings. "
            "Each row must be readable as a standalone record."
        ),
    ),
    "pdf": GenerationExportProfile(
        id="pdf",
        label="📄 PDF-шпаргалка",
        export_format="pdf",
        front_kind="cheatsheet_question",
        default_card_type="fact",
        allow_term_front=False,
        answer_max_words=45,
        prompt_ru=(
            "Назначение: PDF-шпаргалка. Генерируй компактные блоки конспекта: "
            "q/front станет Heading, a/back станет Summary, s станет Evidence, m станет Cue. "
            "Без длинных вопросов, служебных шаблонов и повторов."
        ),
        prompt_en=(
            "Target: PDF cheat sheet. Generate compact cheat-sheet blocks: "
            "q/front maps to Heading, a/back maps to Summary, s maps to Evidence, m maps to Cue. "
            "No long questions, boilerplate, or duplicates."
        ),
    ),
    "json": GenerationExportProfile(
        id="json",
        label="🗺️ JSON графа",
        export_format="json",
        front_kind="graph_relation_question",
        default_card_type="concept",
        allow_term_front=False,
        answer_max_words=60,
        prompt_ru=(
            "Назначение: JSON графа знаний. Генерируй данные для узлов и связей: "
            "q/front станет node_label, card_type — relation_type, a/back — explanation, s — evidence. "
            "Нужны связи, причины, роли и отличия, а не случайный список фактов."
        ),
        prompt_en=(
            "Target: knowledge-graph JSON export. Generate data for nodes and edges: "
            "q/front maps to node_label, card_type maps to relation_type, a/back maps to explanation, s maps to evidence. "
            "Prefer relations, causes, roles, and contrasts over random isolated facts."
        ),
    ),
}

_PROFILE_ALIASES = {
    "apkg": "anki",
    "anki_apkg": "anki",
    "anki.package": "anki",
    "anki_package": "anki",
    "srs": "anki",
    "spaced_repetition": "anki",
    "quizlet_tsv": "quizlet",
    "tsv": "quizlet",
    "term_definition": "quizlet",
    "terms": "quizlet",
    "spreadsheet": "csv",
    "table": "csv",
    "pdf_cheatsheet": "pdf",
    "pdf_shpargalka": "pdf",
    "cheatsheet": "pdf",
    "шпаргалка": "pdf",
    "graph": "json",
    "knowledge_graph": "json",
    "json_graph": "json",
    "canvas": "json",
    "default": "anki",
    "auto": "anki",
    "exam": "anki",
    "exam_prep": "anki",
}


def normalize_output_profile(profile: str | None) -> ProfileId:
    raw = str(profile or "anki").strip().lower().replace(" ", "_").replace("-", "_").replace("/", "_")
    raw = _PROFILE_ALIASES.get(raw, raw)
    return raw if raw in GENERATION_EXPORT_PROFILES else "anki"  # type: ignore[return-value]


def get_generation_profile(profile: str | None) -> GenerationExportProfile:
    return GENERATION_EXPORT_PROFILES[normalize_output_profile(profile)]


def output_profile_instruction(profile: str | None, language: str = "ru") -> str:
    cfg = get_generation_profile(profile)
    return cfg.prompt_en if language == "en" else cfg.prompt_ru


def output_profile_label(profile: str | None) -> str:
    return get_generation_profile(profile).label


def output_profile_allows_term_front(profile: str | None) -> bool:
    return bool(get_generation_profile(profile).allow_term_front)


def output_profile_default_card_type(profile: str | None) -> str:
    return get_generation_profile(profile).default_card_type


def output_profile_answer_max_words(profile: str | None) -> int:
    return int(get_generation_profile(profile).answer_max_words)


def export_profile_options() -> list[dict[str, object]]:
    schemas = {item["id"]: item for item in schema_options()}
    return [
        {
            "id": p.id,
            "label": p.label,
            "export_format": p.export_format,
            "front_kind": p.front_kind,
            "schema": schemas.get(p.id, {}),
        }
        for p in GENERATION_EXPORT_PROFILES.values()
    ]
