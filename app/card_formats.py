from __future__ import annotations

import html
import json
import re
from dataclasses import dataclass
from typing import Any, Mapping

ProfileId = str

_WS_RE = re.compile(r"\s+")
_HTML_RE = re.compile(r"<[^>]+>")


def clean_field(value: Any, max_chars: int = 2000) -> str:
    text = html.unescape(str(value or ""))
    text = _HTML_RE.sub(" ", text)
    text = text.replace("\x1f", " ").replace("\r", "\n")
    text = re.sub(r"[\t ]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if max_chars > 0 and len(text) > max_chars:
        text = text[:max_chars].rstrip() + "…"
    return text


def one_line(value: Any, max_chars: int = 1200) -> str:
    return clean_field(value, max_chars=max_chars).replace("\n", " ").replace("\t", " ").strip()


def tag_list(value: Any) -> list[str]:
    if isinstance(value, list):
        raw = value
    else:
        raw = re.split(r"[\s,;]+", str(value or ""))
    out: list[str] = []
    seen: set[str] = set()
    for item in raw:
        tag = re.sub(r"[^A-Za-zА-Яа-яЁё0-9_\-]+", "", str(item).strip().lstrip("#"))
        if not tag:
            continue
        key = tag.lower().replace("ё", "е")
        if key in seen:
            continue
        seen.add(key)
        out.append(tag)
    return out


def _get(card: Any, name: str, default: str = "") -> str:
    if isinstance(card, Mapping):
        return str(card.get(name) or default)
    return str(getattr(card, name, default) or default)


@dataclass(frozen=True)
class ExportCardSchema:
    id: ProfileId
    label: str
    storage_fields: tuple[str, ...]
    generated_keys: tuple[str, ...]
    export_columns: tuple[str, ...]
    import_extensions: tuple[str, ...]
    description: str


EXPORT_CARD_SCHEMAS: dict[str, ExportCardSchema] = {
    "anki": ExportCardSchema(
        id="anki",
        label="📦 Anki .apkg",
        storage_fields=("Question", "Answer", "Source", "Mnemonic", "Image", "Tags"),
        generated_keys=("q", "a", "s", "m", "tags"),
        export_columns=("Question", "Answer", "Source", "Mnemonic", "Image", "Tags"),
        import_extensions=(".apkg", ".csv", ".tsv", ".txt"),
        description="Anki note fields for a Basic-style APKG package.",
    ),
    "quizlet": ExportCardSchema(
        id="quizlet",
        label="🧩 Quizlet TSV",
        storage_fields=("Term", "Definition"),
        generated_keys=("term", "definition"),
        export_columns=("Term", "Definition"),
        import_extensions=(".tsv", ".csv", ".txt"),
        description="Quizlet import rows: Term and Definition separated by tab/comma/dash; one row per card.",
    ),
    "csv": ExportCardSchema(
        id="csv",
        label="📊 CSV",
        storage_fields=("front", "back", "source_quote", "mnemonic", "tags", "status", "card_type", "image_path", "export_profile"),
        generated_keys=("front", "back", "source_quote", "mnemonic", "tags", "card_type"),
        export_columns=("front", "back", "source_quote", "mnemonic", "tags", "status", "card_type", "image_path", "export_profile", "fields_json"),
        import_extensions=(".csv", ".tsv", ".txt"),
        description="Application CSV table with explicit columns and UTF-8 output.",
    ),
    "pdf": ExportCardSchema(
        id="pdf",
        label="📄 PDF-шпаргалка",
        storage_fields=("Heading", "Summary", "Evidence", "Cue", "tags"),
        generated_keys=("heading", "summary", "evidence", "cue", "tags"),
        export_columns=("Heading", "Summary", "Evidence", "Cue", "tags"),
        import_extensions=(".json", ".csv", ".tsv", ".txt"),
        description="Compact cheat-sheet blocks for PDF rendering.",
    ),
    "json": ExportCardSchema(
        id="json",
        label="🗺️ JSON графа",
        storage_fields=("node_label", "relation_type", "explanation", "evidence", "tags", "source_node_id"),
        generated_keys=("node_label", "relation_type", "explanation", "evidence", "tags"),
        export_columns=("node_label", "relation_type", "explanation", "evidence", "tags", "source_node_id"),
        import_extensions=(".json",),
        description="Knowledge-graph node payload with source links and edges.",
    ),
}


def normalize_profile(profile: Any) -> str:
    raw = str(profile or "anki").strip().lower().replace(" ", "_").replace("-", "_").replace("/", "_")
    aliases = {
        "apkg": "anki", "anki_apkg": "anki", "anki_package": "anki", "srs": "anki",
        "quizlet_tsv": "quizlet", "tsv": "quizlet", "term_definition": "quizlet",
        "spreadsheet": "csv", "table": "csv",
        "pdf_cheatsheet": "pdf", "cheatsheet": "pdf", "шпаргалка": "pdf",
        "graph": "json", "json_graph": "json", "knowledge_graph": "json", "canvas": "json",
        "auto": "anki", "default": "anki",
    }
    raw = aliases.get(raw, raw)
    return raw if raw in EXPORT_CARD_SCHEMAS else "anki"


def build_export_fields(card: Any, profile: Any = None) -> dict[str, Any]:
    profile_id = normalize_profile(profile or _get(card, "export_profile", "anki"))
    front = one_line(_get(card, "front"), 700)
    back = clean_field(_get(card, "back"), 1800)
    quote = clean_field(_get(card, "source_quote"), 1200)
    mnemonic = one_line(_get(card, "mnemonic"), 500)
    tags = tag_list(_get(card, "tags"))
    image_path = one_line(_get(card, "image_path"), 500)
    card_type = one_line(_get(card, "card_type", "basic"), 100) or "basic"
    status = one_line(_get(card, "status", "inbox"), 100) or "inbox"
    source_node_id = one_line(_get(card, "source_node_id"), 200)
    model = one_line(_get(card, "model"), 200)

    if profile_id == "quizlet":
        return {
            "profile": "quizlet",
            "Term": front,
            "Definition": one_line(back, 1400),
        }
    if profile_id == "csv":
        return {
            "profile": "csv",
            "front": front,
            "back": back,
            "source_quote": quote,
            "mnemonic": mnemonic,
            "tags": " ".join(tags),
            "status": status,
            "card_type": card_type,
            "image_path": image_path,
            "export_profile": profile_id,
            "model": model,
        }
    if profile_id == "pdf":
        return {
            "profile": "pdf",
            "Heading": front,
            "Summary": back,
            "Evidence": quote,
            "Cue": mnemonic,
            "tags": tags,
        }
    if profile_id == "json":
        return {
            "profile": "json",
            "node_label": front.rstrip("?"),
            "relation_type": card_type,
            "explanation": back,
            "evidence": quote,
            "tags": tags,
            "source_node_id": source_node_id,
            "model": model,
        }
    return {
        "profile": "anki",
        "Question": front,
        "Answer": back,
        "Source": quote,
        "Mnemonic": mnemonic,
        "Image": image_path,
        "Tags": tags,
    }


def fields_json(card: Any, profile: Any = None) -> str:
    return json.dumps(build_export_fields(card, profile), ensure_ascii=False, separators=(",", ":"))


def load_fields_json(value: Any) -> dict[str, Any]:
    if not value:
        return {}
    if isinstance(value, dict):
        return value
    try:
        data = json.loads(str(value))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def fields_for_export(card: Any, profile: Any) -> dict[str, Any]:
    profile_id = normalize_profile(profile)
    stored = load_fields_json(_get(card, "fields_json"))
    if stored.get("profile") == profile_id:
        return stored
    return build_export_fields(card, profile_id)


def schema_options() -> list[dict[str, Any]]:
    return [
        {
            "id": schema.id,
            "label": schema.label,
            "storage_fields": list(schema.storage_fields),
            "generated_keys": list(schema.generated_keys),
            "export_columns": list(schema.export_columns),
            "import_extensions": list(schema.import_extensions),
            "description": schema.description,
        }
        for schema in EXPORT_CARD_SCHEMAS.values()
    ]
