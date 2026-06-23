from __future__ import annotations

import re
import unicodedata
from functools import lru_cache

try:
    import ftfy  # type: ignore
except Exception:  # pragma: no cover
    ftfy = None

try:
    import regex as regex_mod  # type: ignore
except Exception:  # pragma: no cover
    regex_mod = None

try:
    import pymorphy3  # type: ignore
except Exception:  # pragma: no cover
    pymorphy3 = None

_STYLIZED_TRANSLATION = str.maketrans({
    "ᴛ": "т", "ᴀ": "а", "ᴏ": "о", "ᴄ": "с", "ᴇ": "е", "ʙ": "в", "ᴧ": "л", "϶": "э",
    "ᴘ": "р", "ʀ": "р", "ɴ": "н", "ᴍ": "м", "ᴋ": "к", "ʏ": "у", "ᴨ": "п", "ᴩ": "р",
    "ʍ": "м", "ɜ": "з", "ɐ": "а", "ꞵ": "б", "ᵃ": "а", "ᴠ": "в", "ᴡ": "ш",
})

_SERVICE_MARKER_RE = re.compile(
    r"(?i)(?:file:\/\/|https?:\/\/|www\.|[A-ZА-Я]:[\\/]|converted[-_ ]?repo|\.txt\b|\.pdf\b|\.docx\b|\.html\b|downloads[\\/]|users[\\/]|chrome-extension:)"
)
_PAGE_COUNTER_RE = re.compile(r"(?<!\d)\b\d{1,4}\s*/\s*\d{1,4}\b(?!\d)")
_EXPORT_TIME_RE = re.compile(r"\b\d{1,2}\.\d{1,2}\.\d{2,4}\s*,?\s*\d{1,2}:\d{0,2}\b")
_MALFORMED_EXPORT_RE = re.compile(r"^\s*\d{2,4}\s*,\s*\d{1,4}:?\s*")
_URL_RE = re.compile(r"(?i)\b(?:https?://|file:///|www\.)\S+")
_PATH_RE = re.compile(r"(?i)\b[A-ZА-Я]:[\\/]\S+|(?:\\|/)?(?:Users|Downloads|Desktop|Documents)[\\/]\S+")
_FILENAME_RE = re.compile(r"(?i)\b[\wА-Яа-яЁё ._()\-]{1,80}\.(?:txt|pdf|docx|html|htm|md|epub|fb2|csv|tsv)\b")
_META_LINE_RE = re.compile(r"(?i)^\s*(?:колода|deck|ответ|мнемоника|вопрос|карточка|source|источник)\s*[:：]?\s*")
_PREVIOUS_CARD_QUESTION_RE = re.compile(r"(?i)^\s*(?:\d+[.)]\s*)?(?:что такое|какой числовой|какой факт|как в тексте|как используется|к чему приводит|почему|что происходит|когда|где|в каких случаях|чем отличается)\b.+\?\s*$")
_STANDALONE_TIME_RE = re.compile(r"^\s*(?:\d+[.)]\s*)?\d{2,4}\s*,?\s*\d{1,2}:?\d{0,2}\s*\.?$")
_YEAR_TIME_PREFIX_RE = re.compile(r"^\s*\d{4}\s*,\s*\d{1,2}:?\d{0,2}\s*[:.]?\s*")
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_SPACE_RE = re.compile(r"[ \t\u00a0\u2000-\u200b\ufeff]+")


@lru_cache(maxsize=1)
def _morph():
    if not pymorphy3:
        return None
    try:
        return pymorphy3.MorphAnalyzer()
    except Exception:
        return None


def looks_like_acronym(token: str) -> bool:
    token = str(token or "").strip(" .,:;!?()[]{}«»\"'")
    if not token:
        return False
    if re.fullmatch(r"[A-ZА-ЯЁ]{2,8}[0-9]{0,3}", token):
        return True
    if re.fullmatch(r"[A-ZА-ЯЁ][a-zа-яё]{1,3}[A-ZА-ЯЁ][A-Za-zА-Яа-яЁё0-9]{0,4}", token):
        return True
    return False


def compact_spaces(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def normalize_unicode(value: str) -> str:
    value = str(value or "")
    if ftfy:
        try:
            value = ftfy.fix_text(value)
        except Exception:
            pass
    value = unicodedata.normalize("NFKC", value)
    value = value.translate(_STYLIZED_TRANSLATION)
    value = _CONTROL_RE.sub(" ", value)
    return value


def remove_service_fragments(value: str) -> str:
    value = _URL_RE.sub(" ", value)
    value = _PATH_RE.sub(" ", value)
    value = _FILENAME_RE.sub(" ", value)
    value = _EXPORT_TIME_RE.sub(" ", value)
    value = _YEAR_TIME_PREFIX_RE.sub(" ", value)
    value = _MALFORMED_EXPORT_RE.sub(" ", value)
    value = _PAGE_COUNTER_RE.sub(" ", value)
    value = re.sub(r"(?i)\bconverted[-_ ]?repo\b", " ", value)
    value = re.sub(r"(?i)\b(?:file|source|download|downloads|users|desktop)\b\s*[:=]?", " ", value)
    value = _MALFORMED_EXPORT_RE.sub(" ", value)
    return value


def line_noise_score(line: str) -> float:
    line = compact_spaces(line)
    if not line:
        return 1.0
    score = 0.0
    if _SERVICE_MARKER_RE.search(line):
        score += 2.2
    if _PAGE_COUNTER_RE.search(line):
        score += 0.8
    if _EXPORT_TIME_RE.search(line):
        score += 0.8
    chars = len(line)
    letters = len(re.findall(r"[A-Za-zА-Яа-яЁё]", line))
    cyr = len(re.findall(r"[А-Яа-яЁё]", line))
    digits = len(re.findall(r"\d", line))
    punctuation = len(re.findall(r"[^\w\sА-Яа-яЁё]", line))
    if chars and letters / chars < 0.38:
        score += 0.7
    if digits >= 10 and digits / max(1, chars) > 0.18:
        score += 0.8
    if punctuation / max(1, chars) > 0.22:
        score += 0.7
    if cyr == 0 and letters > 12 and not any(looks_like_acronym(t) for t in re.findall(r"[A-Za-zА-Яа-яЁё0-9-]+", line)):
        score += 0.4
    if len(re.findall(r"[А-ЯA-Z]{5,}", line)) >= 2:
        score += 0.4
    return score


def is_noise_line(line: str) -> bool:
    line = compact_spaces(line)
    if not line:
        return True
    if _META_LINE_RE.match(line) and len(line.split()) <= 5:
        return True
    if _PREVIOUS_CARD_QUESTION_RE.match(line):
        return True
    if line.endswith("?") and len(line.split()) <= 12:
        return True
    if _STANDALONE_TIME_RE.match(line):
        return True
    if _YEAR_TIME_PREFIX_RE.match(line) and len(line.split()) <= 8:
        return True
    if _MALFORMED_EXPORT_RE.match(line):
        return True
    if re.search(r"(?i)(?:какой числовой или временной факт|мнемоника:|ответ:|колода:)" , line):
        return True
    return line_noise_score(line) >= 1.5




def _line_key(line: str) -> str:
    return re.sub(r"[^a-zа-яё0-9]+", " ", compact_spaces(line).lower().replace("ё", "е")).strip()


def _dedupe_lines(lines: list[str]) -> list[str]:
    result: list[str] = []
    keys: list[str] = []
    for line in lines:
        key = _line_key(line)
        if not key:
            continue
        duplicate = False
        for prev_key in keys[-8:]:
            if key == prev_key:
                duplicate = True
                break
            if len(key) > 35 and len(prev_key) > 35 and (key in prev_key or prev_key in key):
                duplicate = True
                break
        if duplicate:
            continue
        result.append(line)
        keys.append(key)
    return result

def clean_source_text(value: str, max_chars: int = 120_000) -> str:
    value = normalize_unicode(value)
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    value = re.sub(r"(\w+)-\n(\w+)", r"\1\2", value)
    value = remove_service_fragments(value)
    clean_lines: list[str] = []
    for raw_line in value.split("\n"):
        line = _SPACE_RE.sub(" ", raw_line).strip()
        line = re.sub(r"^\s*\d+[.)]\s*", "", line).strip()
        line = _META_LINE_RE.sub("", line).strip()
        line = _YEAR_TIME_PREFIX_RE.sub("", line).strip()
        if is_noise_line(line):
            continue
        clean_lines.append(line)
    clean_lines = _dedupe_lines(clean_lines)
    text = "\n".join(clean_lines)
    text = re.sub(r"(?<![.!?;:])\n(?=\w|[А-Яа-яЁё])", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = _SPACE_RE.sub(" ", text)
    text = remove_service_fragments(text)
    text = re.sub(r"\s+([,.;:!?])", r"\1", text)
    text = re.sub(r"([.!?]){3,}", r"\1..", text)
    text = text.strip()
    if len(text) > max_chars:
        text = text[:max_chars]
    return text


def sentence_noise_score(sentence: str) -> float:
    sentence = compact_spaces(remove_service_fragments(normalize_unicode(sentence)))
    if not sentence:
        return 9.0
    score = line_noise_score(sentence)
    words = re.findall(r"[A-Za-zА-Яа-яЁё0-9][A-Za-zА-Яа-яЁё0-9\-]*", sentence)
    if len(words) < 5:
        score += 0.9
    if len(sentence) < 35:
        score += 0.5
    if len(sentence) > 520:
        score += 0.4
    if re.search(r"(?i)\b(?:converted|repo|download|file|txt|html|http|localhost|127\.0\.0\.1)\b", sentence):
        score += 1.2
    if _PREVIOUS_CARD_QUESTION_RE.match(sentence):
        score += 1.4
    if re.search(r"(?i)(?:какой числовой или временной факт|мнемоника:|ответ:|колода:)" , sentence):
        score += 1.4
    if _STANDALONE_TIME_RE.match(sentence):
        score += 1.5
    if _YEAR_TIME_PREFIX_RE.match(sentence):
        score += 1.4
    bad_words = sum(1 for w in words if looks_like_corrupted_token(w))
    if bad_words:
        score += min(1.4, bad_words * 0.35)
    return score




def looks_like_fact_sentence(sentence: str) -> bool:
    sentence = compact_spaces(sentence)
    if not sentence:
        return False
    if re.search(r"[—–-]|[:：]", sentence):
        return True
    if re.search(r"\d", sentence) and len(re.findall(r"[A-Za-zА-Яа-яЁё]", sentence)) >= 12:
        return True
    return bool(re.search(r"\b[А-Яа-яЁё]{3,}(?:ет|ют|ит|ат|ят|ется|ются|лся|лась|лись|или|ала|ало|ает|яют|ивает|ируются|ирован|ованы|яется)\b", sentence, flags=re.I))

def is_useful_sentence(sentence: str) -> bool:
    sentence = compact_spaces(sentence)
    if not sentence:
        return False
    if sentence_noise_score(sentence) >= 1.4:
        return False
    words = re.findall(r"[A-Za-zА-Яа-яЁё][A-Za-zА-Яа-яЁё0-9\-]*", sentence)
    if len(words) < 6:
        return False
    cyr = len(re.findall(r"[А-Яа-яЁё]", sentence))
    latin = len(re.findall(r"[A-Za-z]", sentence))
    if cyr < 8 and latin > cyr * 2 and not any(looks_like_acronym(t) for t in re.findall(r"[A-Za-zА-Яа-яЁё0-9-]+", sentence)):
        return False
    if not looks_like_fact_sentence(sentence):
        return False
    return True


@lru_cache(maxsize=4096)
def looks_like_corrupted_token(token: str) -> bool:
    token = normalize_unicode(token).strip(" .,:;!?()[]{}«»\"'")
    if not token:
        return True
    if re.search(r"[ᴨᴩʍɜɐʙᴄᴛᴇᴋᴍᴏᴘʀʏ϶]", token):
        return True
    letters = re.findall(r"[A-Za-zА-Яа-яЁё]", token)
    if len(letters) >= 7:
        vowels = re.findall(r"[аеёиоуыэюяaeiouyАЕЁИОУЫЭЮЯAEIOUY]", token)
        if len(vowels) <= 1:
            return True
    if len(token) >= 8 and token.isupper() and not looks_like_acronym(token):
        return True
    if len(re.findall(r"[A-Za-z]", token)) and len(re.findall(r"[А-Яа-яЁё]", token)) and not looks_like_acronym(token):
        return True
    if re.fullmatch(r"[А-Яа-яЁё-]{5,}", token) and not looks_like_acronym(token):
        morph = _morph()
        if morph:
            try:
                parses = morph.parse(token)[:3]
                if parses and not any(getattr(p, "is_known", False) for p in parses):
                    return True
            except Exception:
                pass
    return False


def is_artifact_text(value: str) -> bool:
    value = compact_spaces(normalize_unicode(value)).lower().replace("ё", "е")
    if not value:
        return True
    if _SERVICE_MARKER_RE.search(value):
        return True
    if any(x in value for x in ("converted-repo", "file:///", "c:/users", "downloads/", "localhost", "127.0.0.1")):
        return True
    tokens = re.findall(r"[A-Za-zА-Яа-яЁё0-9\-]+", value)
    if not tokens:
        return True
    if sum(1 for t in tokens if looks_like_corrupted_token(t)) >= 2:
        return True
    return False
