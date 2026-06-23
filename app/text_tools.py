"""Text helpers with library-first fallbacks.

`razdel` is used when installed because it handles Russian sentence boundaries much
better than a hand-written regex.  The regex fallback keeps the app runnable if a
user launches an older environment without the new dependency.
"""

from __future__ import annotations

import re
from typing import Iterable

try:  # optional but listed in requirements from stage39
    from razdel import sentenize  # type: ignore
except Exception:  # pragma: no cover - fallback for old installs
    sentenize = None


def split_sentences(text: str) -> list[str]:
    text = (text or "").strip()
    if not text:
        return []
    if sentenize is not None:
        try:
            return [s.text.strip() for s in sentenize(text) if s.text and s.text.strip()]
        except Exception:
            pass
    return [s.strip() for s in re.split(r"(?<=[.!?…])\s+|\n+", text) if s.strip()]


def words(text: str) -> list[str]:
    return re.findall(r"[A-Za-zА-Яа-яЁё0-9]+", text or "")


def compact_ws(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()
