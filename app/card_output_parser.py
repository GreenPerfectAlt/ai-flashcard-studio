from __future__ import annotations

import ast
import json
import re
from typing import Any, List


_THINKING_PATTERNS = (
    re.compile(r"<think>.*?</think>", re.I | re.S),
    re.compile(r"<\|think\|>.*?(?=<\|turn>|<turn\|>|\[|\{|\Z)", re.I | re.S),
    re.compile(r"<\|channel\>\s*thought\s*\n.*?<channel\|>", re.I | re.S),
    re.compile(r"<channel\|>.*?<\|channel\>", re.I | re.S),
    re.compile(r"(?is)^\s*(?:analysis|thought|reasoning)\s*:\s*.*?(?=\[|\{)"),
)


def strip_thinking_channels(text: str) -> str:
    value = str(text or "")
    for pattern in _THINKING_PATTERNS:
        value = pattern.sub(" ", value)
    value = value.replace("<|end_of_text|>", " ").replace("<end_of_turn>", " ")
    return value.strip()


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        key = value.strip()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(key)
    return out


def _extract_fenced_blocks(text: str) -> list[str]:
    blocks: list[str] = []
    for match in re.finditer(r"```(?:json|JSON)?\s*(.*?)```", text or "", flags=re.S):
        block = match.group(1).strip()
        if block:
            blocks.append(block)
    return blocks


def _extract_balanced_json_blocks(text: str) -> list[str]:
    """Return balanced JSON-ish blocks while respecting strings.

    Regex-only extraction is fragile when a card contains brackets inside quoted
    text. This scanner is intentionally small and dependency-free.
    """
    value = text or ""
    blocks: list[str] = []
    for opening, closing in (("[", "]"), ("{", "}")):
        stack = 0
        start = -1
        in_str = False
        quote = ""
        esc = False
        for i, ch in enumerate(value):
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == quote:
                    in_str = False
                continue
            if ch in {'"', "'"}:
                in_str = True
                quote = ch
                continue
            if ch == opening:
                if stack == 0:
                    start = i
                stack += 1
            elif ch == closing and stack:
                stack -= 1
                if stack == 0 and start >= 0:
                    block = value[start : i + 1].strip()
                    if len(block) >= 2:
                        blocks.append(block)
                    start = -1
    return blocks


def _decode_json_loose(candidate: str) -> Any | None:
    if not candidate:
        return None
    text = candidate.strip().strip("\ufeff")
    text = re.sub(r"^```(?:json)?", "", text, flags=re.I).strip()
    text = re.sub(r"```$", "", text).strip()
    attempts = [text, re.sub(r",(\s*[}\]])", r"\1", text)]
    # Some small local models use Python-ish single quotes. Try literal_eval only
    # after strict JSON attempts; do not execute anything.
    for attempt in attempts:
        try:
            return json.loads(attempt)
        except Exception:
            pass
    try:
        data = ast.literal_eval(text)
        if isinstance(data, (dict, list, tuple)):
            return data
    except Exception:
        pass
    return None


def _unwrap_card_container(data: Any) -> list[Any]:
    if isinstance(data, dict):
        for key in ("cards", "flashcards", "items", "data", "result", "results"):
            value = data.get(key)
            if isinstance(value, list):
                return value
        # Single card object.
        if any(k in data for k in ("front", "question", "q", "вопрос")) and any(k in data for k in ("back", "answer", "a", "ответ")):
            return [data]
        return []
    if isinstance(data, tuple):
        data = list(data)
    if isinstance(data, list):
        return data
    return []


def _json_string_literals(text: str) -> list[str]:
    values: list[str] = []
    for m in re.finditer(r'"(?:\\.|[^"\\])*"', text or "", flags=re.S):
        try:
            values.append(json.loads(m.group(0)))
        except Exception:
            values.append(m.group(0).strip('"'))
    return [str(v).strip() for v in values if str(v).strip()]


def _salvage_malformed_card_objects(text: str) -> list[Any]:
    repaired: list[Any] = []
    if not text:
        return repaired
    allowed = {"basic", "definition", "fact", "concept", "cloze", "true_false", "mcq"}
    labels = {
        "type", "card_type", "t", "q", "a", "s", "m", "front", "back", "question", "answer",
        "source_quote", "quote", "mnemonic", "вопрос", "ответ", "цитата", "источник", "мнемоника", "тип",
    }
    for block in re.findall(r"\{[^{}]{16,4000}\}", text, flags=re.S):
        data = _decode_json_loose(block)
        cards = _unwrap_card_container(data)
        if cards:
            repaired.extend(cards)
            continue
        strings = _json_string_literals(block)
        if len(strings) < 2:
            continue
        card_type = "fact"
        payload: list[str] = []
        skip_next = False
        for i, value in enumerate(strings):
            key = value.strip().lower().replace(" ", "_")
            if skip_next:
                skip_next = False
                continue
            if key in {"type", "card_type", "t"}:
                if i + 1 < len(strings):
                    maybe = strings[i + 1].strip().lower().replace(" ", "_")
                    if maybe in allowed:
                        card_type = maybe
                skip_next = True
                continue
            if key in labels or (key in allowed and not payload):
                continue
            payload.append(value)
        if len(payload) >= 2:
            repaired.append({
                "card_type": card_type,
                "front": payload[0],
                "back": payload[1],
                "source_quote": payload[2] if len(payload) >= 3 else "",
                "mnemonic": payload[3] if len(payload) >= 4 else "",
            })
    return repaired


def _salvage_malformed_array_rows(text: str) -> list[Any]:
    repaired: list[Any] = []
    allowed = {"basic", "definition", "fact", "concept", "cloze", "true_false", "mcq"}
    for block in re.findall(r"\[[^\[\]]{10,3000}\]", text or "", flags=re.S):
        data = _decode_json_loose(block)
        if isinstance(data, list) and data:
            if all(isinstance(x, (dict, list, tuple)) for x in data):
                repaired.extend(data)
                continue
            strings = [str(x).strip() for x in data if str(x).strip()]
        else:
            strings = _json_string_literals(block)
        if len(strings) < 2:
            continue
        if strings[0].strip().lower().replace(" ", "_") in allowed and len(strings) >= 3:
            repaired.append(strings[:5])
        else:
            repaired.append(strings[:4])
    return repaired


def _salvage_labelled_qa_text(text: str) -> list[Any]:
    if not text:
        return []
    value = strip_thinking_channels(text)
    parts = re.split(r"(?m)(?:^|\n)\s*(?:\d+[.)]|[-*•])\s*", value)
    if len(parts) <= 1:
        parts = [value]
    cards: list[Any] = []
    q_re = r"(?:вопрос|question|front|q)\s*[:：-]\s*(.+?)"
    a_re = r"(?:ответ|answer|back|a)\s*[:：-]\s*(.+?)"
    s_re = r"(?:цитата|источник|source_quote|quote|s)\s*[:：-]\s*(.+?)"
    m_re = r"(?:мнемоника|ассоциация|mnemonic|hint|m)\s*[:：-]\s*(.+?)"
    for part in parts:
        block = part.strip()
        if not block:
            continue
        q = re.search(q_re + r"(?=\n\s*(?:ответ|answer|back|a)\s*[:：-]|$)", block, flags=re.I | re.S)
        a = re.search(a_re + r"(?=\n\s*(?:цитата|источник|source_quote|quote|s|мнемоника|ассоциация|mnemonic|hint|m)\s*[:：-]|$)", block, flags=re.I | re.S)
        if not q or not a:
            continue
        src = re.search(s_re + r"(?=\n\s*(?:мнемоника|ассоциация|mnemonic|hint|m)\s*[:：-]|$)", block, flags=re.I | re.S)
        mem = re.search(m_re + r"(?=\n\s*(?:\d+[.)]|[-*•]|$))", block, flags=re.I | re.S)
        cards.append({
            "front": re.sub(r"\s+", " ", q.group(1)).strip(' "«»'),
            "back": re.sub(r"\s+", " ", a.group(1)).strip(' "«»'),
            "source_quote": re.sub(r"\s+", " ", src.group(1)).strip(' "«»') if src else "",
            "mnemonic": re.sub(r"\s+", " ", mem.group(1)).strip(' "«»') if mem else "",
            "card_type": "fact",
        })
    return cards


def _salvage_inline_qa_pairs(text: str) -> list[Any]:
    value = strip_thinking_channels(text)
    # Handles: Q: ... A: ... Q: ... A: ...
    pattern = re.compile(
        r"(?:^|\n)\s*(?:Q|Вопрос)\s*[:：-]\s*(.+?)\s*(?:\n|\s+)\s*(?:A|Ответ)\s*[:：-]\s*(.+?)(?=(?:\n\s*(?:Q|Вопрос)\s*[:：-])|\Z)",
        flags=re.I | re.S,
    )
    cards: list[Any] = []
    for match in pattern.finditer(value):
        q = re.sub(r"\s+", " ", match.group(1)).strip(' "«»')
        a = re.sub(r"\s+", " ", match.group(2)).strip(' "«»')
        if q and a:
            cards.append({"front": q, "back": a, "card_type": "fact"})
    return cards


def parse_cards_from_text(text: str) -> List[Any]:
    """Extract model-produced cards from JSON, markdown JSON, or labelled Q/A.

    This parser is deliberately only a parser. It never invents fallback cards;
    final card content must still come from the model output.
    """
    if not text:
        return []
    cleaned = strip_thinking_channels(text).strip()
    cleaned = re.sub(r"^```(?:json)?", "", cleaned, flags=re.I).strip()
    cleaned = re.sub(r"```$", "", cleaned).strip()

    candidates = _dedupe(_extract_fenced_blocks(cleaned) + _extract_balanced_json_blocks(cleaned) + [cleaned])
    loose_objects: list[Any] = []
    for candidate in candidates:
        data = _decode_json_loose(candidate)
        cards = _unwrap_card_container(data)
        if cards:
            return cards
        if isinstance(data, dict):
            loose_objects.append(data)
    if loose_objects:
        return loose_objects

    object_cards = _salvage_malformed_card_objects(cleaned)
    if object_cards:
        return object_cards

    row_cards = _salvage_malformed_array_rows(cleaned)
    if row_cards:
        return row_cards

    labelled = _salvage_labelled_qa_text(cleaned)
    if labelled:
        return labelled

    return _salvage_inline_qa_pairs(cleaned)
