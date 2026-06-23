from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Dict, Tuple


DEFAULT_MODEL_METADATA: Dict[str, Dict[str, Any]] = {
    "gemma-4-E2B-it": {
        "title": "Gemma 4 E2B LiteRT",
        "role": "fast",
        "description": "Основная быстрая локальная модель для генерации карточек.",
        "preferred_backends": ["GPU", "CPU"],
        "prompt_chars": 5200,
        "warmup": True,
        "tool_fallback": True,
        "backend_type": "litert",
    },
    "supergemma4-e4b-abliterated": {
        "title": "SuperGemma 4 E4B LiteRT",
        "role": "quality",
        "description": "Более тяжёлая локальная модель. GPU-first, CPU fallback.",
        "preferred_backends": ["GPU", "CPU"],
        "prompt_chars": 4200,
        "warmup": False,
        "tool_fallback": False,
        "backend_type": "litert",
    },
}


def _canonical_name(path: Path) -> str:
    raw = path.stem.lower().replace("_", "-")
    if "supergemma" in raw and "e4b" in raw:
        return "supergemma4-e4b-abliterated"
    if "gemma" in raw and "e2b" in raw:
        return "gemma-4-E2B-it"
    name = re.sub(r"[^a-z0-9а-яё.-]+", "-", raw).strip("-.")
    return name or path.stem


def _candidate_dirs(base_dir: str | os.PathLike[str]) -> list[Path]:
    base = Path(base_dir).resolve()
    values: list[Path] = [
        base / "models",
        base,
        base.parent / "models",
        base.parent,
        Path.cwd() / "models",
        Path.cwd(),
    ]
    env_dirs = os.environ.get("AIFC_MODEL_DIRS", "")
    for part in re.split(r"[;|]", env_dirs):
        part = part.strip().strip('"')
        if part:
            values.append(Path(part).expanduser())
    # Preserve order while removing duplicates.
    seen: set[str] = set()
    out: list[Path] = []
    for d in values:
        try:
            key = str(d.resolve())
        except Exception:
            key = str(d)
        if key not in seen:
            seen.add(key)
            out.append(d)
    return out


def _read_models_json(base_dir: Path) -> dict[str, Path]:
    result: dict[str, Path] = {}
    cfg = base_dir / "models.json"
    if not cfg.exists():
        return result
    try:
        data = json.loads(cfg.read_text(encoding="utf-8"))
    except Exception:
        return result
    models = data.get("models") if isinstance(data, dict) else None
    if not isinstance(models, dict):
        return result
    for name, meta in models.items():
        if not isinstance(meta, dict):
            continue
        file_name = str(meta.get("file") or "").strip()
        if not file_name:
            continue
        p = Path(file_name)
        if not p.is_absolute():
            p = base_dir / p
        if p.exists() and p.suffix.lower() == ".litertlm":
            result[str(name)] = p.resolve()
    return result


def build_local_litert_registry(base_dir: str | os.PathLike[str]) -> Tuple[Dict[str, str], Dict[str, Dict[str, Any]]]:
    """Find local .litertlm bundles and build MODELS/MODEL_PROFILES.

    Only files that actually exist are exposed to the UI and backend. This avoids
    the common diploma-demo failure where the UI lets the user pick a model whose
    file was not copied to the flash drive.
    """
    base = Path(base_dir).resolve()
    found: dict[str, Path] = {}

    # models.json wins for canonical names when the file exists.
    found.update(_read_models_json(base))

    for directory in _candidate_dirs(base):
        try:
            if not directory.exists():
                continue
            for path in directory.rglob("*.litertlm"):
                if not path.is_file():
                    continue
                name = _canonical_name(path)
                found.setdefault(name, path.resolve())
        except Exception:
            continue

    # Stable order: fast Gemma first, SuperGemma second, then anything custom.
    ordered_names = ["gemma-4-E2B-it", "supergemma4-e4b-abliterated"]
    ordered_names += sorted(n for n in found.keys() if n not in ordered_names)

    models: Dict[str, str] = {}
    profiles: Dict[str, Dict[str, Any]] = {}
    for name in ordered_names:
        path = found.get(name)
        if not path:
            continue
        models[name] = str(path)
        profile = dict(DEFAULT_MODEL_METADATA.get(name, {}))
        if not profile:
            profile = {
                "title": name,
                "role": "local",
                "description": "Локальная LiteRT модель, найденная автоматически.",
                "preferred_backends": ["GPU", "CPU"],
                "prompt_chars": 4200,
                "warmup": False,
                "tool_fallback": False,
                "backend_type": "litert",
            }
        profile["path"] = str(path)
        profile["file"] = str(path)
        profile["available"] = True
        profiles[name] = profile
    return models, profiles
