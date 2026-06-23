"""Robust JSON extraction helpers for local LLM output."""

from __future__ import annotations

import json
import re
from typing import Any


def strip_code_fences(text: str) -> str:
    text = (text or "").strip()
    text = re.sub(r"^```(?:json)?", "", text, flags=re.IGNORECASE).strip()
    text = re.sub(r"```$", "", text).strip()
    return text


def iter_json_values(text: str):
    """Yield JSON values embedded anywhere in `text` using JSONDecoder.

    This avoids brittle regex-only parsing and handles both a JSON array and a
    chain of objects: `{...}\n{...}`.
    """
    decoder = json.JSONDecoder(strict=False)
    src = strip_code_fences(text).replace("<|end_of_text|>", "").replace("<end_of_turn>", "").strip()
    idx = 0
    while idx < len(src):
        match = re.search(r"[\[{]", src[idx:])
        if not match:
            break
        idx += match.start()
        try:
            value, end = decoder.raw_decode(src, idx)
            yield value
            idx = end
        except json.JSONDecodeError:
            idx += 1


def normalize_single_quotes(candidate: str) -> str:
    return re.sub(
        r"'([^'\\]*(?:\\.[^'\\]*)*)'",
        lambda m: json.dumps(m.group(1), ensure_ascii=False),
        candidate,
    )


def extract_json_cards(text: str) -> list[Any]:
    cards: list[Any] = []
    for value in iter_json_values(text):
        if isinstance(value, dict):
            maybe = value.get("cards") or value.get("flashcards")
            if isinstance(maybe, list):
                cards.extend(x for x in maybe if isinstance(x, (dict, list, tuple)))
            elif any(k in value for k in ("front", "question", "term", "back", "answer", "definition")):
                cards.append(value)
        elif isinstance(value, list):
            cards.extend(x for x in value if isinstance(x, (dict, list, tuple)))
        if cards:
            return cards

    # Last-resort repair for responses where the model used single quoted JSON.
    repaired = normalize_single_quotes(strip_code_fences(text))
    if repaired != text:
        for value in iter_json_values(repaired):
            if isinstance(value, dict):
                maybe = value.get("cards") or value.get("flashcards")
                if isinstance(maybe, list):
                    return [x for x in maybe if isinstance(x, (dict, list, tuple))]
                if any(k in value for k in ("front", "question", "term", "back", "answer", "definition")):
                    return [value]
            if isinstance(value, list):
                return [x for x in value if isinstance(x, (dict, list, tuple))]
    return cards
