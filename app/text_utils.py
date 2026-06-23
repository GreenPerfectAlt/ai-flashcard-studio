from __future__ import annotations

import re
import unicodedata


def compact_spaces(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def strip_references(value: str) -> str:
    return re.sub(r"\[[0-9,\s]+\]", "", value or "")


def normalize_unicode_text(value: str) -> str:
    return unicodedata.normalize("NFKC", value or "")
