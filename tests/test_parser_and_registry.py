from pathlib import Path

from app.card_output_parser import parse_cards_from_text
from app.model_registry import build_local_litert_registry


def test_parser_reads_markdown_json():
    raw = """Вот карточки:
```json
[{"q":"Что такое SQL?","a":"SQL — язык запросов.","s":"SQL — декларативный язык запросов.","m":"язык запросов"}]
```"""
    cards = parse_cards_from_text(raw)
    assert len(cards) == 1
    assert cards[0]["q"].startswith("Что")


def test_parser_reads_labelled_qa():
    raw = """1. Вопрос: Что ускоряет индекс?
Ответ: Индекс ускоряет поиск строк в таблице.
Цитата: Индекс ускоряет поиск строк.
"""
    cards = parse_cards_from_text(raw)
    assert len(cards) == 1
    assert cards[0]["front"].startswith("Что")


def test_registry_finds_litertlm(tmp_path: Path):
    models = tmp_path / "models"
    models.mkdir()
    (models / "gemma-4-E2B-it.litertlm").write_bytes(b"dummy")
    found, profiles = build_local_litert_registry(tmp_path)
    assert "gemma-4-E2B-it" in found
    assert profiles["gemma-4-E2B-it"]["available"] is True
