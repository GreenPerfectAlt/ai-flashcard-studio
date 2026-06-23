from __future__ import annotations

from dataclasses import dataclass
import json
import re
from typing import Sequence

from app.text_cleaning import (
    clean_source_text,
    compact_spaces,
    is_artifact_text,
    is_useful_sentence,
    looks_like_corrupted_token,
    normalize_unicode,
    remove_service_fragments,
    sentence_noise_score,
)
from app.nlp_ru import sentence_similarity

try:
    from razdel import sentenize  # type: ignore
except Exception:
    sentenize = None

try:
    import pymorphy3  # type: ignore
except Exception:
    pymorphy3 = None


@dataclass(frozen=True)
class EvidenceUnit:
    eid: str
    text: str
    score: float
    order: int


@dataclass(frozen=True)
class EvidenceBatch:
    prompt: str
    count: int
    evidence: tuple[EvidenceUnit, ...]


_WORD_RE = re.compile(r"[A-Za-zА-Яа-яЁё0-9][A-Za-zА-Яа-яЁё0-9\-]*")
_Q_RE = re.compile(
    r"^\s*(?:что|кто|когда|почему|зачем|как|какой|какая|какое|какие|чем|где|сколько|из чего|what|who|when|why|how|which|where|does|do|is|are)\b",
    re.I,
)
_BAD_TEMPLATE_RE = re.compile(
    r"(?i)^(?:"
    r"какой\s+(?:числовой|временной|ключевой)|"
    r"что\s+говорится\s+о\s+фрагменте|"
    r"что\s+важно\s+(?:понять|запомнить)\s+о\s+теме|"
    r"какой\s+факт\s+из\s+источника\s+объясняет\s+тему|"
    r"как\s+источник\s+описывает\s+тему|"
    r"почему\s+тема\s+[^?]{1,80}\s+важна|"
    r"как\s+используется\s+[«\"]?\w{1,12}[»\"]?\s*\?|"
    r"что\s+такое\s+[а-яёa-z0-9\-]{1,12}\s*\?"
    r")"
)
_SINGLE_WORD_WHAT_RE = re.compile(
    r"^\s*что\s+такое\s+([A-Za-zА-Яа-яЁё0-9\-]{1,32})\??\s*$",
    re.I,
)
_SERVICE_RE = re.compile(
    r"(?i)(?:file:///|https?://|www\.|[A-ZА-Я]:[\\/]|converted[-_ ]?repo|\.txt\b|\.pdf\b|\.docx\b|localhost|127\.0\.0\.1)"
)
_LABEL_RE = re.compile(
    r"^(?:ассоциация|мнемоника|образ|подсказка|memory hint|hint)\s*[:：]\s*",
    re.I,
)

_SOURCE_STOPWORDS = {
    "и", "в", "во", "на", "с", "со", "к", "ко", "от", "до", "по", "за", "для",
    "при", "о", "об", "а", "но", "или", "не", "ни", "это", "этот", "эта", "эти",
    "что", "как", "какой", "какая", "какое", "какие", "кто", "где", "когда",
    "почему", "зачем", "чем", "его", "ее", "её", "их", "он", "она", "оно", "они",
    "же", "ли", "бы", "то", "так", "также", "из", "над", "под", "между",
    "the", "and", "or", "of", "to", "in", "on", "for", "with", "from", "what",
    "why", "how", "which", "where", "this", "that", "these", "those",
}

_MORPH = None
_MORPH_INIT = False


def _morph():
    global _MORPH, _MORPH_INIT

    if _MORPH_INIT:
        return _MORPH

    _MORPH_INIT = True

    if pymorphy3 is None:
        return None

    try:
        _MORPH = pymorphy3.MorphAnalyzer()
    except Exception:
        _MORPH = None

    return _MORPH


def _words(value: str) -> list[str]:
    return _WORD_RE.findall(value or "")


def _safe_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _clean_sentence(value: str) -> str:
    value = normalize_unicode(value)
    value = remove_service_fragments(value)
    value = re.sub(r"\s+([,.;:!?])", r"\1", value)
    value = compact_spaces(value)
    return value.strip(" \t\n\r")


def _split_sentences(text: str) -> list[str]:
    text = clean_source_text(text or "", max_chars=160_000)

    if not text:
        return []

    pieces: list[str] = []
    blocks = [
        b.strip()
        for b in re.split(r"\n{2,}|(?<=\.)\s+(?=[А-ЯA-ZЁ])", text)
        if b.strip()
    ]

    for block in blocks:
        if sentenize:
            try:
                for sent in sentenize(block):
                    pieces.append(sent.text)
                continue
            except Exception:
                pass

        pieces.extend(re.split(r"(?<=[.!?])\s+|\n+", block))

    result: list[str] = []

    for raw in pieces:
        sent = _clean_sentence(raw)

        if not sent:
            continue

        if len(sent) < 38 or len(sent) > 620:
            continue

        if _SERVICE_RE.search(sent) or is_artifact_text(sent):
            continue

        if not is_useful_sentence(sent):
            continue

        result.append(sent)

    return _dedupe_sentences(result)


def _dedupe_sentences(sentences: Sequence[str]) -> list[str]:
    result: list[str] = []
    keys: list[str] = []

    for sent in sentences:
        key = re.sub(
            r"[^a-zа-яё0-9]+",
            " ",
            sent.lower().replace("ё", "е"),
        ).strip()

        if not key:
            continue

        duplicate = False

        for old in keys[-40:]:
            if key == old:
                duplicate = True
                break

            if sentence_similarity(key, old) >= 0.86:
                duplicate = True
                break

        if duplicate:
            continue

        keys.append(key)
        result.append(sent)

    return result


def _has_meaningful_predicate(sentence: str) -> bool:
    low = sentence.lower().replace("ё", "е")

    markers = (
        "—",
        " является ",
        " называют ",
        " приводит ",
        " содержит ",
        " состоит ",
        " образует ",
        " используют ",
        " используется ",
        " позволяет ",
        " происходит ",
        " возникает ",
        " зависит ",
        " отличается ",
        " равна ",
        " равен ",
        " меньше ",
        " больше ",
    )

    if any(x in low for x in markers):
        return True

    if re.search(r"\d", sentence):
        return True

    return bool(
        re.search(
            r"[а-яё]{4,}(?:ет|ют|ит|ат|ят|ется|ются|ают|яют|ирует|ируются|овал|или|яли|ает|ены|ена|ено|ан|ана|ано)\b",
            low,
        )
    )


def _sentence_score(sentence: str, index: int) -> float:
    words = _words(sentence)

    if not words:
        return -99.0

    score = 0.0
    length = len(sentence)

    if 70 <= length <= 260:
        score += 2.0
    elif 45 <= length <= 420:
        score += 1.1
    else:
        score -= 0.7

    if re.search(r"\d", sentence):
        score += 0.65

    if re.search(r"[—–:;]", sentence):
        score += 0.55

    if _has_meaningful_predicate(sentence):
        score += 1.2

    if len(words) >= 9:
        score += 0.45

    if len(words) > 55:
        score -= 0.7

    score -= min(2.4, sentence_noise_score(sentence) * 1.4)
    score -= index * 0.0008

    return score


def build_evidence_units(
    text: str,
    desired_count: int,
    language: str = "ru",
) -> list[EvidenceUnit]:
    sentences = _split_sentences(text)

    if not sentences:
        return []

    candidates: list[EvidenceUnit] = []

    for i, sent in enumerate(sentences):
        score = _sentence_score(sent, i)

        if score <= 0.15:
            continue

        candidates.append(
            EvidenceUnit(
                eid=f"E{i + 1}",
                text=sent,
                score=score,
                order=i,
            )
        )

    if not candidates:
        return []

    ranked = sorted(candidates, key=lambda x: (-x.score, x.order))
    selected: list[EvidenceUnit] = []
    max_units = max(desired_count * 3, desired_count + 8, 12)

    for unit in ranked:
        if any(sentence_similarity(unit.text, old.text) >= 0.91 for old in selected):
            continue

        selected.append(unit)

        if len(selected) >= max_units:
            break

    return sorted(selected, key=lambda x: x.order)


def _source_text_payload(evidence: Sequence[EvidenceUnit]) -> list[str]:
    payload: list[str] = []

    for unit in evidence:
        text = compact_spaces(
            remove_service_fragments(
                normalize_unicode(unit.text or "")
            )
        )

        if text:
            payload.append(text)

    return payload


def build_evidence_prompt(
    evidence: Sequence[EvidenceUnit],
    count: int,
    language: str = "ru",
    custom_prompt: str = "",
    forced_card_type: str = "auto",
    avoid_fronts: Sequence[str] | None = None,
    retry_mode: bool = False,
    tag_hints: str = "",
    output_profile: str = "anki",
) -> str:
    count = max(1, int(count))
    avoid_fronts = [
        compact_spaces(x)
        for x in (avoid_fronts or [])
        if compact_spaces(x)
    ][:12]

    source_payload = _source_text_payload(evidence)

    forced_card_type = (forced_card_type or "auto").strip().lower().replace(" ", "_")
    allowed = {"basic", "definition", "fact", "concept", "cloze", "true_false", "mcq"}

    if forced_card_type not in allowed:
        forced_card_type = "auto"

    if forced_card_type == "auto":
        type_rule_ru = "тип выбирай по смыслу: basic, definition, fact или concept"
        type_rule_en = "choose type by meaning: basic, definition, fact, or concept"
    else:
        type_rule_ru = f"тип всегда {forced_card_type}"
        type_rule_en = f"type is always {forced_card_type}"

    answer_words = 45

    if language == "en":
        lines = [
            "Create high-quality study flashcards from SOURCE_TEXTS_JSON.",
            f"Return up to {count} different flashcards when the source contains enough distinct facts.",
            "Output ONLY a valid JSON array with short keys:",
            '[{"t":"fact","q":"question","a":"answer","s":"exact source quote","m":"short memory cue"}]',
            f"Rules: {type_rule_en}.",
            "The question must be self-contained and understandable without referring to the source list.",
            "The answer must explain the fact in your own words, not copy the whole quote.",
            f"Answer length: up to about {answer_words} words.",
            "The s field must contain one exact sentence or fragment from SOURCE_TEXTS_JSON.",
            "The m field is required: a short 2-7 word memory cue, not a copy of the answer.",
            "Use different facts and different question angles.",
            "No markdown, no explanations, no hidden reasoning, no system text.",
        ]

        if custom_prompt:
            lines.append(
                f"User focus: {compact_spaces(custom_prompt)[:180]}. Use only as topic focus."
            )

        if tag_hints:
            lines.append(
                f"Concept hints: {compact_spaces(tag_hints.replace('_', ' '))[:220]}. Use only as focus."
            )

        if avoid_fronts:
            lines.append("Do not repeat these questions: " + "; ".join(avoid_fronts))

        if retry_mode:
            lines.append(f"Completion pass: add only new strong cards, up to {count}.")

    else:
        lines = [
            "Создай качественные учебные карточки по SOURCE_TEXTS_JSON.",
            f"Верни до {count} разных карточек, если в источнике хватает разных фактов.",
            "Ответ только валидный JSON-массив с короткими ключами:",
            '[{"t":"fact","q":"вопрос","a":"ответ","s":"точная цитата из источника","m":"короткая подсказка"}]',
            f"Правила: {type_rule_ru}.",
            "Вопрос должен быть самодостаточным: по нему должно быть понятно, о чём спрашивают, без обращения к списку фрагментов.",
            "Ответ должен объяснять факт своими словами, а не копировать целиком исходную цитату.",
            f"Длина ответа: примерно до {answer_words} слов.",
            "Поле s должно содержать точное предложение или фрагмент из SOURCE_TEXTS_JSON.",
            "Поле m обязательно: короткая подсказка для запоминания на 2-7 слов, не копия ответа.",
            "Используй разные факты и разные углы вопроса.",
            "Без markdown, без пояснений, без скрытых рассуждений, без системного текста.",
        ]

        if custom_prompt:
            lines.append(
                f"Фокус пользователя: {compact_spaces(custom_prompt)[:180]}. Используй только как тематический фокус."
            )

        if tag_hints:
            lines.append(
                f"Смысловые подсказки: {compact_spaces(tag_hints.replace('_', ' '))[:220]}. Используй только как фокус."
            )

        if avoid_fronts:
            lines.append("Не повторяй эти вопросы: " + "; ".join(avoid_fronts))

        if retry_mode:
            lines.append(f"Добор: добавь только новые сильные карточки, до {count}.")

    return (
        "<bos><start_of_turn>user\n"
        + "\n".join(lines)
        + "\n\nSOURCE_TEXTS_JSON:\n"
        + _safe_json(source_payload)
        + "\n<end_of_turn>\n<start_of_turn>model\n"
    )


def build_evidence_batches(
    text: str,
    desired_count: int,
    language: str = "ru",
    model_prompt_chars: int = 4200,
    batch_card_limit: int = 6,
    custom_prompt: str = "",
    forced_card_type: str = "auto",
    avoid_fronts: Sequence[str] | None = None,
    fast_mode: bool = False,
    tag_hints: str = "",
    output_profile: str = "anki",
) -> list[EvidenceBatch]:
    units = build_evidence_units(text, desired_count, language=language)

    if not units:
        return []

    desired_count = max(1, int(desired_count))
    batch_card_limit = max(1, int(batch_card_limit))

    batches: list[EvidenceBatch] = []
    card_left = desired_count
    batch_index = 0

    while card_left > 0 and units:
        cards_here = min(batch_card_limit, card_left)

        evidence_multiplier = 2 if fast_mode else 3
        evidence_extra = 2 if fast_mode else 4
        take = max(cards_here * evidence_multiplier, cards_here + evidence_extra)

        start = (batch_index * max(1, cards_here)) % len(units)
        rotated = list(units[start:]) + list(units[:start])
        chunk_units = rotated[: min(len(rotated), take)]

        min_keep = min(len(chunk_units), max(1, cards_here))

        while (
            len(chunk_units) > min_keep
            and len(
                build_evidence_prompt(
                    chunk_units,
                    cards_here,
                    language,
                    custom_prompt,
                    forced_card_type,
                    avoid_fronts,
                    tag_hints=tag_hints,
                    output_profile=output_profile,
                )
            )
            > model_prompt_chars
        ):
            chunk_units.pop()

        if not chunk_units:
            break

        prompt = build_evidence_prompt(
            chunk_units,
            cards_here,
            language,
            custom_prompt,
            forced_card_type,
            avoid_fronts,
            tag_hints=tag_hints,
            output_profile=output_profile,
        )

        batches.append(
            EvidenceBatch(
                prompt=prompt,
                count=cards_here,
                evidence=tuple(chunk_units),
            )
        )

        card_left -= cards_here
        batch_index += 1

        if batch_index > desired_count + 2:
            break

    return batches


def _normalize_for_match(value: str) -> str:
    value = remove_service_fragments(normalize_unicode(value))
    return re.sub(
        r"[^a-zа-яё0-9]+",
        " ",
        value.lower().replace("ё", "е"),
    ).strip()


def quote_is_supported(quote: str, evidence: Sequence[EvidenceUnit]) -> bool:
    q = _normalize_for_match(quote)

    if not q or len(q) < 20:
        return False

    for unit in evidence:
        e = _normalize_for_match(unit.text)

        if q in e or e in q:
            return True

        if sentence_similarity(q, e) >= 0.62:
            return True

    return False


def best_evidence_quote(
    front: str,
    back: str,
    quote: str,
    evidence: Sequence[EvidenceUnit],
) -> str:
    query = _normalize_for_match(" ".join([front or "", back or "", quote or ""]))

    if not query or not evidence:
        return ""

    best_unit: EvidenceUnit | None = None
    best_score = 0.0

    for unit in evidence:
        e = _normalize_for_match(unit.text)

        if not e:
            continue

        q_words = set(query.split())
        e_words = set(e.split())

        overlap = len(q_words & e_words) / max(1, min(len(q_words), len(e_words)))
        sim = sentence_similarity(query, e)
        score = max(sim, overlap)

        if score > best_score:
            best_score = score
            best_unit = unit

    return best_unit.text if best_unit and best_score >= 0.24 else ""


def _bad_single_word_question(front: str) -> bool:
    m = _SINGLE_WORD_WHAT_RE.match(front or "")

    if not m:
        return False

    token = m.group(1).strip().lower().replace("ё", "е")

    if len(token) <= 4 or looks_like_corrupted_token(token):
        return True

    morph = _morph()

    if not morph:
        return True

    try:
        parses = morph.parse(token)[:5]
    except Exception:
        return True

    for p in parses:
        pos = str(getattr(p.tag, "POS", "") or "")
        case = str(getattr(p.tag, "case", "") or "")

        if pos in {"NOUN", "Abbr"} and case in {"nomn", ""} and getattr(p, "is_known", True):
            return False

    return True


def clean_mnemonic(value: str, back: str = "") -> str:
    value = compact_spaces(
        remove_service_fragments(
            normalize_unicode(value or "")
        )
    )
    value = _LABEL_RE.sub("", value)
    value = re.sub(r"^\s*[^:：]{1,28}\s*[:：]\s+", "", value).strip()
    value = re.sub(r"#[^\s#.,;:!?()\[\]{}<>]+", "", value)
    value = compact_spaces(value).strip(" .,:;—-")

    if not value:
        return ""

    b = compact_spaces(back or "").lower().replace("ё", "е")
    m = value.lower().replace("ё", "е")

    if m == b:
        return ""

    if len(m) > 35 and (m in b or b in m):
        return ""

    if sentence_similarity(m, b) >= 0.84:
        return ""

    if any(looks_like_corrupted_token(w) for w in _words(value) if len(w) > 4):
        return ""

    return value[:360]


def _build_compact_mnemonic(front: str, back: str, quote: str = "") -> str:
    text = " ".join([front or "", back or "", quote or ""])
    tokens: list[str] = []
    seen: set[str] = set()

    for token in _words(text):
        token = token.lower().replace("ё", "е").strip(" .,:;!?«»\"'")

        if len(token) < 4:
            continue

        if token.isdigit():
            continue

        if token in _SOURCE_STOPWORDS:
            continue

        if looks_like_corrupted_token(token):
            continue

        if token in seen:
            continue

        seen.add(token)
        tokens.append(token)

        if len(tokens) >= 4:
            break

    if len(tokens) >= 3:
        cue = " — ".join(tokens[:3])
    elif len(tokens) == 2:
        cue = " — ".join(tokens)
    elif len(tokens) == 1:
        cue = tokens[0]
    else:
        cue = ""

    return clean_mnemonic(cue, back)


def _content_token_set(value: str) -> set[str]:
    tokens: set[str] = set()

    for token in _words(value or ""):
        token = token.lower().replace("ё", "е").strip(" .,:;!?«»\"'")

        if len(token) < 3:
            continue

        if token.isdigit():
            continue

        if token in _SOURCE_STOPWORDS:
            continue

        if looks_like_corrupted_token(token):
            continue

        tokens.add(token)

    return tokens


def _trim_unsupported_question_tail(front: str, back: str, quote: str) -> str:
    front = compact_spaces(front or "").strip()

    if not front:
        return ""

    body = front[:-1].strip() if front.endswith("?") else front
    support_tokens = _content_token_set((back or "") + " " + (quote or ""))

    if not support_tokens:
        return front

    for sep in [",", " — ", " – ", " - "]:
        if sep not in body:
            continue

        head, tail = body.rsplit(sep, 1)
        head = compact_spaces(head).strip(" .,:;—-")
        tail = compact_spaces(tail).strip(" .,:;—-")

        if len(_words(head)) < 4:
            continue

        tail_tokens = _content_token_set(tail)

        if not tail_tokens or len(tail_tokens) > 5:
            continue

        head_tokens = _content_token_set(head)

        tail_overlap = len(tail_tokens & support_tokens) / max(1, len(tail_tokens))
        head_overlap = len(head_tokens & support_tokens) / max(1, len(head_tokens))

        if tail_overlap < 0.25 and head_overlap > 0:
            return head + "?"

    return front


def _answer_too_close_to_quote(back: str, quote: str) -> bool:
    b = _normalize_for_match(back)
    q = _normalize_for_match(quote)

    if len(b) < 90 or len(q) < 90:
        return False

    if b == q:
        return True

    if b in q or q in b:
        return True

    return sentence_similarity(b, q) >= 0.94


def validate_model_card(
    card: dict,
    evidence: Sequence[EvidenceUnit],
    language: str = "ru",
    output_profile: str = "anki",
) -> tuple[dict | None, str]:
    if not isinstance(card, dict):
        return None, "not dict"

    front = compact_spaces(
        remove_service_fragments(
            normalize_unicode(
                str(
                    card.get("front")
                    or card.get("question")
                    or card.get("q")
                    or ""
                )
            )
        )
    )

    back = compact_spaces(
        remove_service_fragments(
            normalize_unicode(
                str(
                    card.get("back")
                    or card.get("answer")
                    or card.get("a")
                    or ""
                )
            )
        )
    )

    quote = compact_spaces(
        remove_service_fragments(
            normalize_unicode(
                str(
                    card.get("source_quote")
                    or card.get("quote")
                    or card.get("s")
                    or ""
                )
            )
        )
    )

    mnemonic = clean_mnemonic(
        str(
            card.get("mnemonic")
            or card.get("hint")
            or card.get("m")
            or ""
        ),
        back,
    )

    card_type = str(
        card.get("card_type")
        or card.get("type")
        or card.get("t")
        or "basic"
    ).strip().lower().replace(" ", "_")

    if card_type not in {"basic", "definition", "fact", "concept", "cloze", "true_false", "mcq"}:
        card_type = "basic"

    if not front or not back:
        return None, "empty"

    front = _trim_unsupported_question_tail(front, back, quote)

    joined = " ".join([front, back, quote, mnemonic])

    if _SERVICE_RE.search(joined) or is_artifact_text(joined):
        return None, "artifact"

    if not front.endswith("?") and _Q_RE.search(front):
        front = front.rstrip(" .;:!—-") + "?"

    front = _trim_unsupported_question_tail(front, back, quote)

    has_question_word = bool(_Q_RE.search(front))

    if not front.endswith("?") or not has_question_word:
        return None, "not question"

    if _BAD_TEMPLATE_RE.match(front) or _bad_single_word_question(front):
        return None, "bad question template"

    if back.endswith("?") or len(_words(back)) < 4:
        return None, "bad answer"

    if not quote_is_supported(quote, evidence):
        repaired_quote = best_evidence_quote(front, back, quote, evidence)

        if repaired_quote:
            quote = repaired_quote
        else:
            return None, "unsupported quote"

    if _answer_too_close_to_quote(back, quote):
        return None, "answer copies quote"

    if not mnemonic:
        mnemonic = _build_compact_mnemonic(front, back, quote)

    result = {
        "card_type": card_type,
        "front": front[:240],
        "back": back[:900],
        "source_quote": quote[:900],
        "mnemonic": mnemonic[:360],
    }

    return result, ""


def select_retry_evidence(
    evidence: Sequence[EvidenceUnit],
    accepted_cards: Sequence[dict],
    need: int,
    language: str = "ru",
) -> list[EvidenceUnit]:
    need = max(1, int(need or 1))

    if not evidence:
        return []

    used_quotes = [
        _normalize_for_match(str(card.get("source_quote") or ""))
        for card in accepted_cards
        if card.get("source_quote")
    ]

    unused: list[EvidenceUnit] = []

    for unit in evidence:
        normalized = _normalize_for_match(unit.text)

        if not normalized:
            continue

        if any(
            q
            and (
                q in normalized
                or normalized in q
                or sentence_similarity(q, normalized) >= 0.80
            )
            for q in used_quotes
        ):
            continue

        unused.append(unit)

    if not unused:
        unused = list(evidence)

    ranked = sorted(unused, key=lambda x: (-x.score, x.order))
    return sorted(ranked[: max(need * 3, need + 4, 8)], key=lambda x: x.order)