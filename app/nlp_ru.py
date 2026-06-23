from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import re
from typing import Iterable

try:
    from natasha import Doc, Segmenter, NewsEmbedding, NewsMorphTagger, NewsSyntaxParser, MorphVocab  # type: ignore
except Exception:  # pragma: no cover
    Doc = Segmenter = NewsEmbedding = NewsMorphTagger = NewsSyntaxParser = MorphVocab = None

try:
    import pymorphy3  # type: ignore
except Exception:  # pragma: no cover
    pymorphy3 = None

_CONTENT_POS = {"NOUN", "PROPN", "ADJ", "NUM", "X"}
_NOUN_POS = {"NOUN", "PROPN", "X"}
_TOKEN_RE = re.compile(r"[A-Za-zА-Яа-яЁё0-9][A-Za-zА-Яа-яЁё0-9\-]*")


@dataclass(frozen=True)
class TokenInfo:
    text: str
    lemma: str
    pos: str
    start: int
    stop: int


@dataclass(frozen=True)
class ParsedSentence:
    text: str
    tokens: tuple[TokenInfo, ...]


@lru_cache(maxsize=1)
def _natasha_pipeline():
    if not all([Doc, Segmenter, NewsEmbedding, NewsMorphTagger, NewsSyntaxParser, MorphVocab]):
        return None
    try:
        segmenter = Segmenter()
        emb = NewsEmbedding()
        morph_tagger = NewsMorphTagger(emb)
        syntax_parser = NewsSyntaxParser(emb)
        morph_vocab = MorphVocab()
        return segmenter, morph_tagger, syntax_parser, morph_vocab
    except Exception:
        return None


@lru_cache(maxsize=1)
def _morph():
    if not pymorphy3:
        return None
    try:
        return pymorphy3.MorphAnalyzer()
    except Exception:
        return None


def parse_ru_sentence(sentence: str) -> ParsedSentence:
    sentence = str(sentence or "").strip()
    pipeline = _natasha_pipeline()
    if pipeline:
        try:
            segmenter, morph_tagger, syntax_parser, morph_vocab = pipeline
            doc = Doc(sentence)
            doc.segment(segmenter)
            doc.tag_morph(morph_tagger)
            doc.parse_syntax(syntax_parser)
            tokens: list[TokenInfo] = []
            for token in doc.tokens:
                token.lemmatize(morph_vocab)
                lemma = str(getattr(token, "lemma", "") or token.text).lower().replace("ё", "е")
                tokens.append(TokenInfo(token.text, lemma, str(getattr(token, "pos", "") or ""), int(token.start), int(token.stop)))
            return ParsedSentence(sentence, tuple(tokens))
        except Exception:
            pass
    tokens = []
    morph = _morph()
    for m in _TOKEN_RE.finditer(sentence):
        text = m.group(0)
        lemma = text.lower().replace("ё", "е")
        pos = ""
        if morph:
            try:
                p = morph.parse(text)[0]
                lemma = str(p.normal_form or lemma).lower().replace("ё", "е")
                pos = str(getattr(p.tag, "POS", "") or "")
                pos = {"ADJF": "ADJ", "ADJS": "ADJ", "NOUN": "NOUN", "NUMR": "NUM", "LATN": "X", "ROMN": "X"}.get(pos, pos)
            except Exception:
                pass
        tokens.append(TokenInfo(text, lemma, pos, m.start(), m.end()))
    return ParsedSentence(sentence, tuple(tokens))


def noun_phrases(sentence: str, max_words: int = 6) -> list[str]:
    parsed = parse_ru_sentence(sentence)
    spans: list[list[TokenInfo]] = []
    current: list[TokenInfo] = []
    for token in parsed.tokens:
        text = token.text.strip()
        if not text or text.isdigit():
            if current:
                spans.append(current)
                current = []
            continue
        pos = token.pos
        if not pos or pos in _CONTENT_POS:
            current.append(token)
            if len(current) >= max_words:
                spans.append(current[-max_words:])
        else:
            if current:
                spans.append(current)
                current = []
    if current:
        spans.append(current)

    phrases: list[str] = []
    seen: set[str] = set()
    for span in spans:
        if not span:
            continue
        trimmed = list(span[:max_words])
        while trimmed and trimmed[0].pos not in _CONTENT_POS:
            trimmed.pop(0)
        while trimmed and trimmed[-1].pos not in _NOUN_POS:
            if len(trimmed) <= 2:
                break
            trimmed.pop()
        if not trimmed:
            continue
        if not any(t.pos in _NOUN_POS for t in trimmed):
            continue
        phrase = " ".join(t.text for t in trimmed).strip(" -–—.,:;!?()[]{}«»\"'")
        key = phrase.lower().replace("ё", "е")
        if len(phrase) >= 3 and key not in seen:
            seen.add(key)
            phrases.append(phrase)
    phrases.sort(key=lambda p: (len(p.split()), len(p)), reverse=True)
    return phrases


def lemma_set(text: str) -> set[str]:
    parsed = parse_ru_sentence(text)
    result = set()
    for token in parsed.tokens:
        if token.lemma and len(token.lemma) > 2:
            result.add(token.lemma.lower().replace("ё", "е"))
    return result


def has_content_noun(text: str) -> bool:
    parsed = parse_ru_sentence(text)
    return any(t.pos in _NOUN_POS and len(t.lemma) > 2 for t in parsed.tokens)


def sentence_similarity(a: str, b: str) -> float:
    la = lemma_set(a)
    lb = lemma_set(b)
    if not la or not lb:
        return 0.0
    return len(la & lb) / max(1, len(la | lb))
