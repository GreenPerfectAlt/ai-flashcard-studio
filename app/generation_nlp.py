from __future__ import annotations

from dataclasses import dataclass
import json
import math
import re
from functools import lru_cache
from typing import Iterable

from app.text_cleaning import clean_source_text, is_artifact_text, is_useful_sentence, sentence_noise_score, looks_like_corrupted_token
from app.nlp_ru import noun_phrases, has_content_noun

try:
    import yake  # type: ignore
except Exception:  # pragma: no cover
    yake = None

try:
    import pymorphy3  # type: ignore
except Exception:  # pragma: no cover
    pymorphy3 = None

try:
    from razdel import sentenize  # type: ignore
except Exception:  # pragma: no cover
    sentenize = None

try:
    from stop_words import get_stop_words  # type: ignore
except Exception:  # pragma: no cover
    get_stop_words = None


@dataclass(frozen=True)
class FactUnit:
    id: int
    focus: str
    fact: str
    score: float
    order: int


@dataclass(frozen=True)
class FactBatch:
    payload: str
    count: int
    facts: tuple[FactUnit, ...]
    drafts: tuple[dict, ...]


def compact_spaces(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def strip_refs(value: str) -> str:
    value = re.sub(r"\[[0-9,\s]+\]", "", value or "")
    value = re.sub(r"(?im)^\s*(?:колода|ответ|мнемоника|вопрос|карточка)\s*[:：]\s*", "", value)
    value = re.sub(r"\b(?:Ответ|Мнемоника)\s*[:：]\s*", "", value)
    value = clean_source_text(value, max_chars=50_000)
    value = re.sub(r"\s+([,.;:!?])", r"\1", value)
    return compact_spaces(value)


@lru_cache(maxsize=4)
def stopword_set(language: str = "ru") -> set[str]:
    lang = "russian" if language == "ru" else "english"
    if get_stop_words:
        try:
            words = set(get_stop_words(lang)) | set(get_stop_words("english"))
            words |= {"ответ", "мнемоника", "вопрос", "карточка", "источник", "если", "который", "которая", "которые", "которого", "которым", "этот", "эта", "это", "эти", "они", "она", "оно", "он", "его", "ее", "её", "их", "уже", "также"}
            return {w.lower().replace("ё", "е") for w in words}
        except Exception:
            pass
    return {
        "и", "в", "во", "на", "с", "со", "к", "ко", "от", "до", "по", "за", "для", "при", "о", "об",
        "это", "что", "как", "кто", "где", "когда", "почему", "какой", "какая", "какое", "какие",
        "его", "ее", "её", "они", "она", "оно", "уже", "также", "или", "не", "ни", "а", "но",
        "ответ", "мнемоника", "вопрос", "карточка", "колода", "источник", "текст", "факт", "пример", "если", "который", "которая", "которые", "которого", "которым", "этот", "эта", "это", "эти", "они", "она", "оно", "он", "его", "ее", "её", "их", "уже", "также",
        "the", "and", "or", "of", "to", "in", "on", "for", "with", "from", "what", "why", "how"
    }


@lru_cache(maxsize=1)
def morph():
    if not pymorphy3:
        return None
    try:
        return pymorphy3.MorphAnalyzer()
    except Exception:
        return None


def sentence_units(text: str) -> list[str]:
    text = strip_refs(text)
    if not text:
        return []
    if sentenize:
        try:
            units = [strip_refs(s.text) for s in sentenize(text)]
            return [s for s in units if 35 <= len(s) <= 650 and is_useful_sentence(s)]
        except Exception:
            pass
    units = [strip_refs(s) for s in re.split(r"(?<=[.!?])\s+|\n+", text)]
    return [s for s in units if 35 <= len(s) <= 650 and is_useful_sentence(s)]


def _token_pos(token: str) -> tuple[str, str, str]:
    m = morph()
    if not m:
        return token.lower(), "", ""
    try:
        p = m.parse(token)[0]
        return str(p.normal_form or token).lower().replace("ё", "е"), str(p.tag.POS or ""), str(getattr(p.tag, "case", "") or "")
    except Exception:
        return token.lower(), "", ""




def _looks_like_ru_verb_form(token: str) -> bool:
    token = token.lower().replace("ё", "е").strip(" .,:;!?«»\"")
    if not re.fullmatch(r"[а-я-]{4,}", token):
        return False
    return token.endswith(("ет", "ют", "ит", "ат", "ят", "ется", "ются", "ает", "яют", "ивает", "ируют", "ировал", "ила", "или", "ыло", "ело", "ала", "яли", "бьет", "ьют"))

def _is_content_token(token: str, language: str = "ru") -> bool:
    token = token.strip(" -–—.,:;!?()[]{}«»\"'").lower().replace("ё", "е")
    if len(token) < 3 or re.fullmatch(r"\d+", token) or looks_like_corrupted_token(token):
        return False
    if token in stopword_set(language):
        return False
    lemma, pos, _case = _token_pos(token)
    if lemma in stopword_set(language):
        return False
    if language == "ru" and not pos and _looks_like_ru_verb_form(token):
        return False
    if pos and pos not in {"NOUN", "ADJF", "ADJS", "NUMR", "NPRO", "UNKN", "LATN", "ROMN"}:
        return False
    return True




def _looks_like_oblique_single_word(token: str) -> bool:
    token = token.lower().replace("ё", "е").strip(" .,:;!?«»\"")
    if not re.fullmatch(r"[а-я-]{4,}", token):
        return False
    return token.endswith(("ами", "ями", "ого", "его", "ому", "ему", "ыми", "ими", "ых", "их", "ой", "ей", "ою", "ею", "ах", "ях", "ов", "ев", "ью", "ия", "ию", "ие"))

def _safe_focus_phrase(value: str, language: str = "ru") -> str:
    value = strip_refs(value).strip(" .,:;!?«»\"'")
    value = re.sub(r"\s+", " ", value)
    words = re.findall(r"[A-Za-zА-Яа-яЁё0-9][A-Za-zА-Яа-яЁё0-9\-]*", value)
    words = [w for w in words if _is_content_token(w, language)]
    if not words:
        return ""
    if language == "ru" and len(words) == 1:
        token = words[0]
        if _looks_like_oblique_single_word(token):
            return ""
        m = morph()
        if m:
            try:
                p = m.parse(token)[0]
                pos = str(getattr(p.tag, "POS", "") or "")
                if pos not in {"NOUN", "ADJF", "ADJS", "Abbr"}:
                    return ""
                inflected = p.inflect({"nomn"})
                if inflected:
                    token = str(inflected.word)
            except Exception:
                pass
        return token[:80]
    phrase = " ".join(words[:4])
    return phrase[:80]


def extract_keywords(text: str, language: str = "ru", top: int = 80) -> list[tuple[str, float]]:
    text = strip_refs(text)[:50000]
    if not text:
        return []
    if yake:
        lan = "ru" if language == "ru" else "en"
        try:
            extractor = yake.KeywordExtractor(lan=lan, n=3, dedupLim=0.78, top=top, features=None)
            result = []
            for phrase, score in extractor.extract_keywords(text):
                safe = _safe_focus_phrase(phrase, language)
                if safe:
                    result.append((safe, float(score)))
            if result:
                return result
        except Exception:
            pass
    freq: dict[str, int] = {}
    for token in re.findall(r"[A-Za-zА-Яа-яЁё][A-Za-zА-Яа-яЁё0-9\-]{2,}", text):
        if _is_content_token(token, language):
            lemma, _pos, _case = _token_pos(token)
            freq[lemma] = freq.get(lemma, 0) + 1
    ranked = sorted(freq.items(), key=lambda x: (-x[1], x[0]))[:top]
    return [(x, 1 / max(1, n)) for x, n in ranked]


def _direct_focus_from_sentence(sentence: str, language: str = "ru") -> str:
    sentence = compact_spaces(sentence)
    prefix = re.match(r"^(.{3,120}?)[：:]\s+", sentence)
    if prefix:
        value = prefix.group(1).strip(" .,:;!?«»\"'")
        first = (re.findall(r"[A-Za-zА-Яа-яЁё]+", value[:40]) or [""])[0].lower().replace("ё", "е")
        if first not in stopword_set(language) and len(re.findall(r"[A-Za-zА-Яа-яЁё0-9]{2,}", value)) >= 2:
            return value[:110]
    dash = re.match(r"^(.{3,100}?)\s+[—–-]\s+", sentence)
    if dash:
        value = dash.group(1).strip(" .,:;!?«»\"'")
        if len(re.findall(r"[A-Za-zА-Яа-яЁё0-9]{2,}", value)) <= 6:
            return value[:100]
    possession = re.match(r"^У\s+(.{3,70}?)\s+в\s+([A-ZА-ЯЁ0-9][A-Za-zА-Яа-яЁё0-9\-]{1,60})\s+(?:наход|есть|име|содерж)", sentence)
    if possession:
        holder = possession.group(1).strip(" .,:;!?«»\"'")
        obj = possession.group(2).strip(" .,:;!?«»\"'")
        if holder and obj:
            return f"{obj} у {holder}"[:100]
    possession2 = re.match(r"^У\s+(.{3,70}?)\s+([A-ZА-ЯЁ]{2,}[A-ZА-ЯЁ0-9\-]*)\b", sentence)
    if possession2:
        holder = possession2.group(1).strip(" .,:;!?«»\"'")
        obj = possession2.group(2).strip(" .,:;!?«»\"'")
        if holder and obj:
            return f"{obj} у {holder}"[:100]
    return ""


def _sentence_focus(sentence: str, keywords: list[tuple[str, float]], language: str = "ru") -> str:
    direct = _direct_focus_from_sentence(sentence, language)
    if direct:
        return direct
    low = sentence.lower().replace("ё", "е")
    np = [p for p in noun_phrases(sentence) if not is_artifact_text(p)]
    np = [_safe_focus_phrase(p, language) for p in np]
    np = [p for p in np if p and not is_artifact_text(p)]
    if np:
        return np[0]

    candidates = []
    for phrase, score in keywords[:120]:
        p = phrase.lower().replace("ё", "е")
        if p and p in low:
            word_count = len(phrase.split())
            single_penalty = -2 if word_count == 1 else 0
            candidates.append((word_count + single_penalty, -score, phrase))
    if candidates:
        candidates.sort(reverse=True)
        best = candidates[0][2]
        if best:
            return best

    tokens = re.findall(r"[A-Za-zА-Яа-яЁё][A-Za-zА-Яа-яЁё0-9\-]{2,}", sentence)
    phrases: list[str] = []
    current: list[str] = []
    for token in tokens:
        lemma, pos, _case = _token_pos(token)
        if _is_content_token(token, language) and (not pos or pos in {"NOUN", "ADJF", "ADJS", "NUMR", "UNKN", "LATN", "ROMN"}):
            current.append(token)
            if len(current) >= 4:
                phrases.append(" ".join(current[-4:]))
        else:
            if current:
                phrases.append(" ".join(current))
                current = []
    if current:
        phrases.append(" ".join(current))
    phrases = [_safe_focus_phrase(p, language) for p in phrases]
    phrases = [p for p in phrases if p]
    if not phrases:
        return ""
    phrases.sort(key=lambda p: (len(p.split()), len(p)), reverse=True)
    return phrases[0]


def _sentence_score(sentence: str, keywords: list[tuple[str, float]], language: str = "ru") -> float:
    low = sentence.lower().replace("ё", "е")
    score = 0.0
    if re.search(r"\d", sentence):
        score += 1.1
    if re.search(r"[А-ЯA-Z][а-яa-z]{2,}(?:\s+[А-ЯA-Z][а-яa-z]{2,})?", sentence):
        score += 0.45
    if 70 <= len(sentence) <= 360:
        score += 0.8
    else:
        score += 0.25
    for phrase, kw_score in keywords[:80]:
        if phrase.lower().replace("ё", "е") in low:
            score += max(0.08, 1.0 - min(float(kw_score), 1.0))
    content = sum(1 for w in re.findall(r"[A-Za-zА-Яа-яЁё][A-Za-zА-Яа-яЁё0-9\-]{2,}", sentence) if _is_content_token(w, language))
    score += math.log1p(content) * 0.35
    return score


def select_fact_units(text: str, target_count: int, language: str = "ru") -> list[FactUnit]:
    text = clean_source_text(text, max_chars=80_000)
    sentences = sentence_units(text)
    if not sentences:
        return []
    keywords = extract_keywords(text, language=language, top=max(120, target_count * 8))
    units: list[FactUnit] = []
    seen = set()
    for i, sentence in enumerate(sentences):
        if is_artifact_text(sentence) or sentence_noise_score(sentence) >= 1.25:
            continue
        focus = _sentence_focus(sentence, keywords, language)
        if not focus or is_artifact_text(focus):
            continue
        focus_words = re.findall(r"[A-Za-zА-Яа-яЁё0-9][A-Za-zА-Яа-яЁё0-9\-]*", focus)
        if not focus_words or len(focus_words) > 8:
            continue
        if len(focus_words) == 1:
            one = focus_words[0]
            if _looks_like_oblique_single_word(one) or looks_like_corrupted_token(one):
                continue
            if language == "ru" and one.islower() and len(one) < 7:
                continue
        normalized = compact_spaces(sentence.lower().replace("ё", "е"))
        if normalized in seen:
            continue
        seen.add(normalized)
        score = _sentence_score(sentence, keywords, language) - sentence_noise_score(sentence) * 0.85
        units.append(FactUnit(id=len(units) + 1, focus=focus, fact=sentence, score=score, order=i))
    if not units:
        return []

    units.sort(key=lambda u: (-u.score, u.order))
    balanced: list[FactUnit] = []
    focus_seen: set[str] = set()
    for unit in units:
        fkey = compact_spaces(unit.focus.lower().replace("ё", "е"))
        if fkey in focus_seen and len(balanced) < target_count:
            continue
        focus_seen.add(fkey)
        balanced.append(unit)
        if len(balanced) >= max(1, target_count):
            break
    if len(balanced) < target_count:
        for unit in units:
            if unit not in balanced:
                balanced.append(unit)
            if len(balanced) >= target_count:
                break
    balanced.sort(key=lambda u: u.order)
    return [FactUnit(id=i + 1, focus=u.focus, fact=u.fact, score=u.score, order=u.order) for i, u in enumerate(balanced[:target_count])]

def _surface_focus(focus: str, fact: str) -> str:
    focus = compact_spaces(focus).strip(" .,:;!?«»\"'")
    fact = compact_spaces(fact)
    if not focus:
        return ""
    low_fact = fact.lower().replace("ё", "е")
    low_focus = focus.lower().replace("ё", "е")
    pos = low_fact.find(low_focus)
    if pos >= 0:
        return fact[pos:pos + len(focus)]
    if " у " in low_focus:
        return focus
    for word in sorted(re.findall(r"[A-Za-zА-Яа-яЁё0-9][A-Za-zА-Яа-яЁё0-9\-]{2,}", focus), key=len, reverse=True):
        w = word.lower().replace("ё", "е")
        pos = low_fact.find(w)
        if pos >= 0:
            return fact[pos:pos + len(word)]
    return focus


def _preserve_word_case(original: str, value: str) -> str:
    if original.isupper():
        return value.upper()
    if original[:1].isupper():
        return value[:1].upper() + value[1:]
    return value


def russian_case_phrase(phrase: str, gram: str = "loct") -> str:
    phrase = compact_spaces(phrase).strip(" .,:;!?«»\"'")
    if not phrase:
        return phrase
    mobj = morph()
    if not mobj:
        return phrase
    parts = re.split(r"(\W+)", phrase)
    out: list[str] = []
    for part in parts:
        if not re.search(r"[А-Яа-яЁё]", part) or part.isupper():
            out.append(part)
            continue
        try:
            parsed = mobj.parse(part)[0]
            if str(parsed.tag.POS or "") in {"NOUN", "ADJF", "ADJS", "PRTF", "PRTS", "NPRO", "NUMR"}:
                inflected = parsed.inflect({gram})
                if inflected:
                    out.append(_preserve_word_case(part, str(inflected.word)))
                    continue
        except Exception:
            pass
        out.append(part)
    return "".join(out)


def _about_focus(focus: str, language: str = "ru") -> str:
    focus = compact_spaces(focus)
    if not focus:
        return "тексте" if language == "ru" else "the text"
    if language != "ru":
        return focus
    return russian_case_phrase(focus, "loct")


def _trim_question_subject(focus: str) -> str:
    focus = compact_spaces(focus).strip(" .,:;!?«»\"'")
    focus = re.sub(r"^(?:это|такой|такая|такие|самый|именно)\s+", "", focus, flags=re.I)
    words = re.findall(r"[A-Za-zА-Яа-яЁё0-9][A-Za-zА-Яа-яЁё0-9\-]*", focus)[:6]
    if not words:
        return ""
    blocked_first = {"если", "когда", "где", "как", "что", "который", "которая", "которые", "это"}
    while words and words[0].lower().replace("ё", "е") in blocked_first:
        words = words[1:]
    return " ".join(words) if words else ""


def _sentence_subject(sentence: str, focus: str = "") -> str:
    sentence = compact_spaces(sentence)
    for pattern in (
        r"^(.{3,90}?)\s+[—–-]\s+",
        r"^(.{3,90}?)\s+(?:—\s+)?это\b",
        r"^(.{3,90}?)\s+(?:является|считается|называется|представляет собой)\b",
        r"^У\s+(.{3,70}?)\s+(?:есть|наход|име|содерж)",
    ):
        m = re.search(pattern, sentence, flags=re.I)
        if m:
            subject = _trim_question_subject(m.group(1))
            if subject and not is_artifact_text(subject):
                return subject
    return _trim_question_subject(focus)


def _number_phrase(sentence: str) -> str:
    m = re.search(r"\b\d[\d\s.,]*(?:%|\s*(?:процент(?:а|ов)?|копи[яйи]|лет|год(?:а|ов)?|млн|миллион(?:а|ов)?|тыс\.?|тысяч(?:а|и)?|кг|кДж|нм|Å|°C))\b", sentence, flags=re.I)
    if m:
        return compact_spaces(m.group(0))
    m = re.search(r"\b\d[\d\s.,]*\b", sentence)
    return compact_spaces(m.group(0)) if m else ""


def draft_question_from_fact(fact: FactUnit, language: str = "ru") -> str:
    source = compact_spaces(fact.fact)
    focus = _surface_focus(fact.focus, source)
    subject = _sentence_subject(source, focus)
    if language != "ru":
        target = subject or focus
        low = source.lower()
        if re.search(r"\b(transmit|spread|pass)\w*\b", low):
            return f"How does {target} spread?" if target else "How does this process spread?"
        if re.search(r"\b(contain|consist|include|made of)\w*\b", low):
            return f"What does {target} contain?" if target else "What does the text describe?"
        if re.search(r"\b(use|serve|apply)\w*\b", low):
            return f"How is {target} used?" if target else "How is this used?"
        if re.search(r"\d", source):
            num = _number_phrase(source)
            return f"What does {num} show about {target}?" if num and target else "What numerical fact is important here?"
        return f"What role does {target} play here?" if target else "What key idea is stated?"

    target = subject or focus
    if not target or is_artifact_text(target):
        return "Что объясняет этот фрагмент?"
    quoted = f"«{target}»"
    low = source.lower().replace("ё", "е")
    num = _number_phrase(source)

    if re.search(r"\b(переда|распространя|заража|переход)\w*", low):
        return f"Как передаётся {quoted}?"
    if re.search(r"\b(размножа|делит|делиться|делятся|растет|растут)\w*", low):
        return f"Как ведёт себя {quoted}?"
    if re.search(r"\b(разруша|убива|погиба|уничтож|забира)\w*", low):
        return f"К чему приводит {quoted}?"
    if re.search(r"\b(содерж|состо[ияи]т|включа|имеет|есть|находится|окружена)\w*", low):
        return f"Что содержит или включает {quoted}?"
    if re.search(r"\b(использ|примен|служит|позволя|помога)\w*", low):
        return f"Как используется {quoted}?"
    if re.search(r"\b(счита|называ|явля|представляет собой|это)\w*|\s[—–-]\s", low):
        return f"Как в тексте объясняется {quoted}?"
    if num:
        return f"Что показывает число {num} про {quoted}?"
    if re.search(r"\b(почему|из-за|потому что|так как)\b", low):
        return f"Почему {quoted} важно в этом процессе?"
    return f"Какую роль играет {quoted}?"

def draft_answer_from_fact(fact: FactUnit, max_chars: int = 260) -> str:
    answer = compact_spaces(fact.fact).strip(" .")
    if len(answer) <= max_chars:
        return answer + "."
    cut = answer[:max_chars].rsplit(" ", 1)[0].rstrip(" ,;:—-")
    return cut + "…"


def draft_mnemonic_from_fact(fact: FactUnit, answer: str, language: str = "ru") -> str:
    focus = compact_spaces(_surface_focus(fact.focus, fact.fact)).strip(" .,:;!?«»\"")
    answer = compact_spaces(answer).strip(" .")
    if not answer:
        return ""
    if language != "ru":
        cue = f"Remember the role of {focus}." if focus else "Link the cue to the main fact."
        return sanitize_mnemonic(cue)
    if focus and len(focus.split()) <= 6:
        return sanitize_mnemonic(f"Образ для памяти: {focus} + главное действие из ответа")
    return sanitize_mnemonic("Представь главное действие и его последствие одной сценой")


def draft_card_from_fact(fact: FactUnit, language: str = "ru") -> dict:
    answer = draft_answer_from_fact(fact)
    question = draft_question_from_fact(fact, language)
    mnemonic = draft_mnemonic_from_fact(fact, answer, language)
    return {
        "front": question,
        "back": answer,
        "source_quote": compact_spaces(fact.fact),
        "mnemonic": mnemonic,
        "card_type": "basic",
    }


def build_fact_plan(text: str, target_count: int, batch_size: int, language: str = "ru", max_prompt_chars: int = 6000) -> list[FactBatch]:
    facts = select_fact_units(text, target_count, language=language)
    if not facts:
        return []
    batch_size = max(1, int(batch_size or 6))
    batches: list[FactBatch] = []
    current: list[FactUnit] = []
    for fact in facts:
        current.append(fact)
        payload = _facts_payload(current, language)
        if len(current) >= batch_size or len(payload) > max_prompt_chars:
            if len(payload) > max_prompt_chars and len(current) > 1:
                last = current.pop()
                batches.append(FactBatch(_facts_payload(current, language), len(current), tuple(current), tuple(draft_card_from_fact(f, language) for f in current)))
                current = [last]
            else:
                batches.append(FactBatch(payload, len(current), tuple(current), tuple(draft_card_from_fact(f, language) for f in current)))
                current = []
    if current:
        batches.append(FactBatch(_facts_payload(current, language), len(current), tuple(current), tuple(draft_card_from_fact(f, language) for f in current)))
    return batches


def build_fact_batches(text: str, target_count: int, batch_size: int, language: str = "ru", max_prompt_chars: int = 6000) -> list[tuple[str, int]]:
    return [(b.payload, b.count) for b in build_fact_plan(text, target_count, batch_size, language, max_prompt_chars)]


def _facts_payload(facts: Iterable[FactUnit], language: str = "ru") -> str:
    data = []
    for f in facts:
        draft = draft_card_from_fact(f, language)
        data.append({
            "id": f.id,
            "focus": _surface_focus(f.focus, f.fact),
            "fact": f.fact,
            "draft_front": draft["front"],
            "draft_back": draft["back"],
            "draft_mnemonic": draft["mnemonic"],
        })
    return "FACTS_JSON:\n" + json.dumps(data, ensure_ascii=False, separators=(",", ":"))


def sanitize_mnemonic(value: str) -> str:
    value = compact_spaces(value)
    value = re.sub(r"^(?:ассоциация|мнемоника|образ|подсказка|memory hint|hint)\s*[:：]\s*", "", value, flags=re.I)
    value = re.sub(r"^\s*[A-Za-zА-Яа-яЁё0-9_\- ]{1,36}\s*(?:→|=>)\s*", "", value).strip()
    value = re.sub(r"#[^\s#.,;:!?()\[\]{}<>]+", "", value)
    value = re.sub(r"(?i)\b(?:converted[-_ ]?repo|file:///|https?://|c:/users|\.txt)\S*", "", value)
    value = compact_spaces(value).strip(" .,:;—-")
    return (value + ".") if value else ""



def russian_prepositional_focus(focus: str) -> str:
    focus = compact_spaces(focus)
    words = focus.split()
    if not words:
        return focus
    mobj = morph()
    if not mobj:
        return focus
    try:
        last = words[-1]
        parsed = mobj.parse(last)[0]
        inflected = parsed.inflect({"loct"}) or parsed.inflect({"gent"})
        if inflected:
            words[-1] = str(inflected.word)
            return " ".join(words)
    except Exception:
        pass
    return focus

def question_subject_is_bad(question: str, language: str = "ru") -> bool:
    question = compact_spaces(question).lower().replace("ё", "е")
    m = re.match(r"^что\s+такое\s+(.+?)\?*$", question, flags=re.I)
    if not m:
        return False
    subject = m.group(1).strip(" .,:;!?«»")
    words = re.findall(r"[а-яa-z][а-яa-z0-9\-]*", subject, flags=re.I)
    if not words or len(words) > 4:
        return True
    if len(words) == 1 and language == "ru":
        mobj = morph()
        if not mobj:
            w = words[0].lower().replace("ё", "е")
            return w in stopword_set("ru") or w.endswith(("ами", "ями", "ого", "его", "ому", "ему", "ых", "их", "ой", "ей", "ою", "ею", "ах", "ях"))
        try:
            parses = mobj.parse(words[0])[:5]
            noun_parses = [p for p in parses if str(p.tag.POS or "") in {"NOUN", "NPRO"}]
            if not noun_parses:
                return True
            best = max(noun_parses, key=lambda p: float(getattr(p, "score", 0.0) or 0.0))
            best_case = str(getattr(best.tag, "case", "") or "")
            if best_case not in {"nomn", ""}:
                return True
            return False
        except Exception:
            return False
    return False


def repair_question_with_fact(front: str, back: str, quote: str, language: str = "ru") -> str:
    """Do not author fallback questions in Python.

    Earlier stages generated generic Russian question templates here. That made
    deterministic fallback text look like model output. The generation pipeline
    now either keeps a valid model question or rejects the row and asks the model
    again.
    """
    front = compact_spaces(front).rstrip(" .")
    return front if front.endswith("?") and not question_subject_is_bad(front, language) else ""
