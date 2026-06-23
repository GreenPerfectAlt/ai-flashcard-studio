from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

from app.text_cleaning import compact_spaces, is_artifact_text, is_useful_sentence, looks_like_corrupted_token, normalize_unicode, remove_service_fragments
from app.nlp_ru import has_content_noun, sentence_similarity

CardType = Literal["basic", "definition", "fact", "concept", "cloze", "true_false", "mcq"]

_SINGLE_WORD_DEFINITION_RE = re.compile(r"^\s*что\s+такое\s+([A-Za-zА-Яа-яЁё0-9\-]+)\??\s*$", re.I)
_GENERIC_QUESTION_RE = re.compile(
    r"^\s*(?:какой\s+(?:ключевой\s+)?факт|что\s+важно\s+знать|что\s+говорится|что\s+нужно\s+запомнить|какой\s+числовой|какой\s+временной)",
    re.I,
)
_ARTIFACT_RE = re.compile(r"(?i)(?:file:///|https?://|www\.|[A-ZА-Я]:[\\/]|\.(?:txt|pdf|docx|html)\b|converted[-_ ]?repo|localhost|127\.0\.0\.1)")


def normalize_card_text(value: str, max_chars: int = 900) -> str:
    value = normalize_unicode(str(value or ""))
    value = remove_service_fragments(value)
    value = re.sub(r"^[\s#*_`>\-•·]+", "", value)
    value = re.sub(r"\s+([,.;:!?])", r"\1", value)
    return compact_spaces(value)[:max_chars]


def _words(value: str) -> list[str]:
    return re.findall(r"[A-Za-zА-Яа-яЁё0-9][A-Za-zА-Яа-яЁё0-9\-]*", value or "")


def _bad_single_word_question(front: str) -> bool:
    m = _SINGLE_WORD_DEFINITION_RE.match(front or "")
    if not m:
        return False
    token = m.group(1)
    if looks_like_corrupted_token(token):
        return True
    return not has_content_noun(token)


def _mnemonic_is_copy(mnemonic: str, back: str) -> bool:
    m = compact_spaces(mnemonic).lower().replace("ё", "е").strip(" .")
    b = compact_spaces(back).lower().replace("ё", "е").strip(" .")
    if not m or not b:
        return False
    if m == b:
        return True
    if len(m) > 35 and (m in b or b in m):
        return True
    return sentence_similarity(mnemonic, back) >= 0.82


class FlashcardCandidate(BaseModel):
    card_type: CardType = "basic"
    front: str = Field(min_length=8, max_length=240)
    back: str = Field(min_length=16, max_length=900)
    source_quote: str = Field(default="", max_length=900)
    mnemonic: str = Field(default="", max_length=360)
    image_path: str = ""

    @field_validator("front", "back", "source_quote", "mnemonic", mode="before")
    @classmethod
    def clean_text_fields(cls, value):
        return normalize_card_text(str(value or ""), max_chars=900)

    @field_validator("mnemonic")
    @classmethod
    def clean_mnemonic(cls, value: str) -> str:
        value = re.sub(r"^(?:ассоциация|мнемоника|образ|подсказка|memory hint|hint)\s*[:：]\s*", "", value, flags=re.I)
        value = re.sub(r"^\s*[^:：]{1,32}\s*[:：]\s+", "", value).strip()
        return compact_spaces(value).strip(" .,:;—-")

    @model_validator(mode="after")
    def validate_quality(self):
        joined = " ".join([self.front, self.back, self.source_quote, self.mnemonic])
        if _ARTIFACT_RE.search(joined) or any(is_artifact_text(part) for part in (self.front, self.back, self.source_quote) if part):
            raise ValueError("service artifact in card")
        if _GENERIC_QUESTION_RE.match(self.front):
            raise ValueError("generic question")
        if _bad_single_word_question(self.front):
            raise ValueError("bad single-word definition question")
        if self.front.rstrip().endswith("?") is False:
            raise ValueError("front must be a question")
        if self.back.rstrip().endswith("?"):
            raise ValueError("answer is question")
        if len(_words(self.back)) < 5:
            raise ValueError("answer too short")
        if self.source_quote and not is_useful_sentence(self.source_quote) and len(_words(self.source_quote)) < 6:
            raise ValueError("bad source quote")
        if self.mnemonic and _mnemonic_is_copy(self.mnemonic, self.back):
            object.__setattr__(self, "mnemonic", "")
        if self.mnemonic and any(looks_like_corrupted_token(w) for w in _words(self.mnemonic) if len(w) > 4):
            object.__setattr__(self, "mnemonic", "")
        return self


def validate_flashcard_payload(payload: dict) -> tuple[dict | None, str]:
    try:
        model = FlashcardCandidate.model_validate(payload or {})
        return model.model_dump(), ""
    except ValidationError as exc:
        return None, "; ".join(str(e.get("msg", "validation error")) for e in exc.errors()[:3])
    except Exception as exc:
        return None, str(exc)


def card_similarity(a: dict, b: dict) -> float:
    return max(
        sentence_similarity(str(a.get("front", "")), str(b.get("front", ""))),
        sentence_similarity(str(a.get("back", "")), str(b.get("back", ""))),
    )
