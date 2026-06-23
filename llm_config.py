import os
import re
import json
import time
import asyncio
import inspect
from typing import List, Dict, Any, Optional, AsyncIterator, Tuple

import litert_lm
from litert_lm import SamplerConfig

try:
    _SAMPLER_CONFIG_FIELDS = set(inspect.signature(SamplerConfig).parameters.keys())
except Exception:
    _SAMPLER_CONFIG_FIELDS = {"top_k", "top_p", "temperature", "seed"}


def _supports_sampler_field(name: str) -> bool:
    return name in _SAMPLER_CONFIG_FIELDS

# generation: prompt-quality + speed patch on top of legacy_generation LiteRT hot path.
#
# What changed compared to legacy_generation:
#   * stop double-wrapping the Gemma chat template (old code sent
#     "<bos><start_of_turn>user\n...<end_of_turn>\n<start_of_turn>model\n"
#     as a user string, so the engine wrapped it again -> model saw garbage);
#   * use real `system_message` parameter of create_conversation() for the role;
#   * use `SamplerConfig(temperature, top_k, top_p)` so the model stops producing
#     random trivia and converges faster (fewer retries = faster);
#   * use `filter_channel_content_from_kv_cache=True` for models with thinking
#     channel (SuperGemma4 abliterated etc.) so thinking tokens are dropped from
#     the KV cache -> dramatically faster multi-batch generation;
#   * expose `ask_litert_stream()` async generator that wraps
#     `send_message_async()` for token-level streaming (SSE-friendly);
#   * keep the old `ask_litert()` entry point as a thin wrapper so existing
#     callers in main.py keep working unchanged.

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

MODELS = {
    "gemma-4-E2B-it": os.path.join(BASE_DIR, "models", "gemma-4-E2B-it.litertlm"),
    "supergemma4-e4b-abliterated": os.path.join(BASE_DIR, "models", "supergemma4-e4b-abliterated.litertlm"),
}

MODEL_PROFILES: Dict[str, Dict[str, Any]] = {
    "gemma-4-E2B-it": {
        "title": "Gemma 4 E2B LiteRT",
        "role": "fast",
        "description": "Etalon LiteRT runtime: old fast hot path.",
        "preferred_backends": ["GPU", "CPU"],
        "prompt_chars": int(os.environ.get("AIFC132_PROMPT_CHARS", "7000")),
        "backend_type": "litert",
    },
    "supergemma4-e4b-abliterated": {
        "title": "SuperGemma 4 E4B LiteRT",
        "role": "quality",
        "description": "Etalon LiteRT runtime: old fast hot path.",
        "preferred_backends": ["GPU", "CPU"],
        "prompt_chars": int(os.environ.get("AIFC132_PROMPT_CHARS", "7000")),
        "backend_type": "litert",
    },
}

llm_engine = None
current_model_name: Optional[str] = None
current_model_path: Optional[str] = None
current_backend_name: Optional[str] = None
last_load_seconds: Optional[float] = None
last_inference_seconds: Optional[float] = None
last_error: Optional[str] = None


# ------------------------- env helpers (local) -------------------------

def _env_int(name: str, default: int, min_value: int = 1, max_value: Optional[int] = None) -> int:
    raw = os.environ.get(name, "").strip()
    try:
        value = int(raw) if raw else int(default)
    except Exception:
        value = int(default)
    value = max(min_value, value)
    if max_value is not None:
        value = min(max_value, value)
    return value


def _env_float(name: str, default: float, min_value: float = 0.0, max_value: Optional[float] = None) -> float:
    raw = os.environ.get(name, "").strip()
    try:
        value = float(raw) if raw else float(default)
    except Exception:
        value = float(default)
    value = max(min_value, value)
    if max_value is not None:
        value = min(max_value, value)
    return value


def _env_flag(name: str, default: str = "0") -> bool:
    return os.environ.get(name, default).strip().lower() in {"1", "true", "yes", "on"}


# ------------------------- backend / engine -------------------------

def _backend_obj(name: Optional[str] = None):
    backend = (name or os.environ.get("AIFC_LITERT_BACKEND", "GPU") or "GPU").strip().upper()
    if backend == "CPU":
        return backend, litert_lm.Backend.CPU()
    return "GPU", litert_lm.Backend.GPU()


def get_model_prompt_chars(model_name: Optional[str] = None) -> int:
    try:
        if _is_thinking_model(model_name):
            return int(os.environ.get("AIFC_SUPERGEMMA_PROMPT_CHARS", "2600"))
        return int(os.environ.get("AIFC143_PROMPT_CHARS", os.environ.get("AIFC_ETALON_PROMPT_CHARS", "7000")))
    except Exception:
        return 2600 if _is_thinking_model(model_name) else 7000


def _is_thinking_model(model_name: Optional[str]) -> bool:
    """SuperGemma4-abliterated and similar models emit a <channel>thought channel.

    For these we want filter_channel_content_from_kv_cache=True so the thinking
    tokens do not pollute the KV cache and slow down subsequent batches.
    """
    name = str(model_name or "").lower()
    if not name:
        return False
    return any(tag in name for tag in ("supergemma", "abliterated", "think"))


# Gemma 3/4 chat-template control tokens. Placed at the very start of the user
# turn, on their own line, BEFORE the actual question. The chat-template
# processor recognizes them and either suppresses or forces the thinking
# channel. See: https://ai.google.dev/gemma/docs/core/thinking
#
#   /no_think  -> model answers immediately, no <channel>thought...<channel|> block
#   /think     -> force thinking (we never use this for flashcard generation)
#
# Abliterated models may partially ignore these tokens, so we keep TWO more
# layers of defense: (a) system_message explicitly forbids reasoning blocks,
# (b) filter_channel_content_from_kv_cache=True drops thinking from KV cache,
# (c) _clean_raw() strips any residual <think>/<channel>thought from output.
_NO_THINK_TOKEN = "/no_think"
_THINK_TOKEN = "/think"
_THINKING_PATTERNS_IN_OUTPUT = (
    "<channel>thought",
    "<channel|>thought",
    "<|channel|>thought",
    "<think>",
    "<|think|>",
)


def _should_no_think(model_name: Optional[str], override: Optional[bool] = None) -> bool:
    """Decide whether to prepend /no_think to the user message.

    Priority:
      1. Explicit `override` parameter (per-call).
      2. `AIFC_FORCE_NO_THINK` env var (applies to ALL models, even non-thinking).
      3. `AIFC_NO_THINK` env var (default ON for thinking models).
      4. Default: ON for thinking models, OFF otherwise.
    """
    if override is not None:
        return bool(override)
    # Force on every model (even gemma-4-E2B-it which usually doesn't think).
    # Useful if you observe stray <think> tokens from a model that nominally
    # shouldn't emit them.
    force = os.environ.get("AIFC_FORCE_NO_THINK", "").strip().lower()
    if force in {"1", "true", "yes", "on"}:
        return True
    if force in {"0", "false", "no", "off"}:
        return False
    # Default behavior: /no_think only for thinking models, but allow turning
    # it OFF via AIFC_NO_THINK=0.
    if not _is_thinking_model(model_name):
        return False
    return _env_flag("AIFC_NO_THINK", "1")


def _apply_no_think(user_text: str, model_name: Optional[str], override: Optional[bool] = None) -> str:
    """Prepend `/no_think\n` to the user message when appropriate.

    The token goes on its own line at the very start of the user turn — this is
    the position the Gemma 3/4 chat-template processor looks for it.
    """
    if not _should_no_think(model_name, override=override):
        return user_text
    if not user_text:
        return _NO_THINK_TOKEN
    # Avoid double-prepending if the caller already added it.
    if user_text.lstrip().startswith(_NO_THINK_TOKEN):
        return user_text
    return f"{_NO_THINK_TOKEN}\n{user_text}"


def _output_has_thinking_tokens(raw_text: str) -> bool:
    """Detect residual thinking tokens in the model output.

    Used to log a warning when /no_think did not fully suppress thinking — this
    typically happens with abliterated models that ignore the chat-template
    control token.
    """
    if not raw_text:
        return False
    low = raw_text.lower()
    return any(p in low for p in _THINKING_PATTERNS_IN_OUTPUT)


def _build_sampler_config(
    temperature: Optional[float] = None,
    top_k: Optional[int] = None,
    top_p: Optional[float] = None,
    min_p: Optional[float] = None,
    seed: Optional[int] = None,
) -> SamplerConfig:
    """Read sampling params from env, with optional per-call overrides.

    Priority: explicit parameter > env var > sensible default.

    Defaults are tuned for factual flashcards: low temperature, capped top_k,
    modest top_p -> less randomness, fewer retries.
    """
    if temperature is None:
        temperature = _env_float("AIFC_TEMPERATURE", 0.35, min_value=0.0, max_value=5.0)
    else:
        temperature = max(0.0, min(5.0, float(temperature)))

    if top_k is None:
        top_k = _env_int("AIFC_TOP_K", 40, min_value=0, max_value=1000)
    else:
        top_k = max(0, min(1000, int(top_k)))
    if top_k <= 0:
        top_k = _env_int("AIFC_TOP_K_DISABLED_VALUE", 1000, min_value=1, max_value=1000)

    if top_p is None:
        top_p = _env_float("AIFC_TOP_P", 0.92, min_value=0.0, max_value=1.0)
    else:
        top_p = max(0.0, min(1.0, float(top_p)))
    if top_p <= 0.0:
        top_p = 1.0

    if min_p is None:
        min_p = _env_float("AIFC_MIN_P", 0.0, min_value=0.0, max_value=1.0)
    else:
        min_p = max(0.0, min(1.0, float(min_p)))

    if seed is None:
        seed_env = os.environ.get("AIFC_SEED", "").strip()
        if seed_env:
            try:
                seed = int(seed_env)
            except Exception:
                seed = None
    kwargs = {"temperature": temperature, "top_k": top_k, "top_p": top_p, "seed": seed}
    if _supports_sampler_field("min_p"):
        kwargs["min_p"] = min_p
    return SamplerConfig(**kwargs)


def _build_sampler_from_dict(params: Optional[Dict[str, Any]]) -> Optional[SamplerConfig]:
    """Build a SamplerConfig from supported LiteRTLM sampler keys."""
    if not params:
        return None
    has_any = any(k in params for k in ("temperature", "top_k", "top_p", "min_p", "seed"))
    if not has_any:
        return None
    return _build_sampler_config(
        temperature=params.get("temperature"),
        top_k=params.get("top_k"),
        top_p=params.get("top_p"),
        min_p=params.get("min_p"),
        seed=params.get("seed"),
    )


def init_engine(model_name: str = "gemma-4-E2B-it", force_backend: Optional[str] = None) -> None:
    global llm_engine, current_model_name, current_model_path, current_backend_name, last_load_seconds, last_error
    model_name = model_name or "gemma-4-E2B-it"
    if model_name not in MODELS:
        raise ValueError(f"Неизвестная модель: {model_name}")
    model_path = MODELS[model_name]
    if not os.path.exists(model_path):
        raise FileNotFoundError(f"Файл модели не найден: {model_path}")

    backend_name, backend_obj = _backend_obj(force_backend)
    if llm_engine is not None and current_model_name == model_name and current_backend_name == backend_name:
        print(f"[LLM] already loaded: {model_name}/{backend_name}")
        return

    unload_engine()
    started = time.perf_counter()
    current_model_path = model_path
    print(f"[LLM] init etalon LiteRT: {model_name}, backend={backend_name}, path={model_path}")
    try:
        # generation: do NOT pass huge max_num_tokens by default.
        # On LiteRT WebGPU a large value like 32768 can make the C++ GPU readback
        # call run past the internal timeout on SuperGemma. The safe default is
        # to let the model/runtime choose its own cache/output limit and control
        # work size through short calls (cards_per_call), not one giant decode.
        kwargs: Dict[str, Any] = {}
        max_tokens_env = os.environ.get("AIFC_MAX_NUM_TOKENS", "").strip()
        if max_tokens_env:
            if os.environ.get("AIFC_ENABLE_MAX_NUM_TOKENS_OVERRIDE", "0").strip().lower() in {"1", "true", "yes", "on"}:
                try:
                    requested_max_tokens = int(max_tokens_env)
                    if _is_thinking_model(model_name):
                        cap = int(os.environ.get("AIFC_SUPERGEMMA_MAX_NUM_TOKENS", "8192") or 8192)
                        requested_max_tokens = max(1024, min(requested_max_tokens, cap))
                    kwargs["max_num_tokens"] = requested_max_tokens
                    print(f"[LLM] max_num_tokens override enabled: {requested_max_tokens}")
                except Exception:
                    pass
            else:
                print(f"[LLM] ignoring AIFC_MAX_NUM_TOKENS={max_tokens_env}; using LiteRT/model default")
        llm_engine = litert_lm.Engine(model_path, backend=backend_obj, **kwargs)
        current_model_name = model_name
        current_backend_name = backend_name
        last_load_seconds = time.perf_counter() - started
        last_error = None
        print(f"[LLM] loaded: {model_name}/{backend_name}, load={last_load_seconds:.1f}s, thinking_model={_is_thinking_model(model_name)}")
    except Exception as e:
        last_error = str(e)
        llm_engine = None
        current_model_name = None
        current_backend_name = None
        print(f"[LLM] init error: {e}")
        raise


def unload_engine() -> None:
    global llm_engine, current_model_name, current_model_path, current_backend_name, last_load_seconds
    if llm_engine is not None:
        try:
            del llm_engine
        except Exception:
            pass
    llm_engine = None
    current_model_name = None
    current_model_path = None
    current_backend_name = None
    last_load_seconds = None


def clear_llm_cache() -> int:
    return 0


def get_engine_status() -> Dict[str, Any]:
    return {
        "loaded": llm_engine is not None,
        "current_model": current_model_name,
        "backend": current_backend_name,
        "models": list(MODEL_PROFILES.keys()),
        "litert_models": list(MODELS.keys()),
        "profiles": MODEL_PROFILES,
        "last_load_seconds": last_load_seconds,
        "last_inference_seconds": last_inference_seconds,
        "last_error": last_error,
        "runtime": "local_litert_generation",
        "sampler": {
            "temperature": _env_float("AIFC_TEMPERATURE", 0.35),
            "top_k": _env_int("AIFC_TOP_K", 40),
            "top_p": _env_float("AIFC_TOP_P", 0.92),
            "min_p": _env_float("AIFC_MIN_P", 0.0),
            "seed": os.environ.get("AIFC_SEED", "") or None,
            "supported_fields": sorted(_SAMPLER_CONFIG_FIELDS),
        },
        "thinking_filter": _is_thinking_model(current_model_name) and _env_flag("AIFC_FILTER_THINKING", "1"),
        "thinking_model": _is_thinking_model(current_model_name),
        "no_think": _should_no_think(current_model_name),
        "no_think_token": _NO_THINK_TOKEN if _should_no_think(current_model_name) else None,
    }


# ------------------------- raw text / chat template helpers -------------------------

# Some old prompts (legacy_generation) were pre-wrapped with the Gemma chat template by
# the caller. send_message() already applies the chat template internally, so
# the old wrapping caused double-wrapping. Detect and strip it so legacy
# prompts keep working without behavior regression.
_CHAT_WRAP_RE = re.compile(
    r"^\s*<bos>\s*<start_of_turn>user\s*\n(.*?)\n<end_of_turn>\s*<start_of_turn>model\s*\n?$",
    re.DOTALL,
)


def _strip_legacy_chat_wrap(prompt: str) -> str:
    """If `prompt` was manually wrapped with the Gemma chat template, unwrap it.

    Returns the raw user text. If the prompt does not look pre-wrapped, returns
    it unchanged.
    """
    if not prompt:
        return prompt
    m = _CHAT_WRAP_RE.match(prompt.strip())
    if not m:
        return prompt
    return m.group(1).strip()


def _clean_raw(raw: str) -> str:
    raw = str(raw or "")
    raw = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL)
    raw = re.sub(r"<\|channel>thought\n.*?<channel\|>", "", raw, flags=re.DOTALL)
    raw = re.sub(r"<\|channel\|>thought\n.*?<\|channel\|>", "", raw, flags=re.DOTALL)
    raw = raw.replace("```json", "```").replace("<end_of_turn>", "").replace("<|end_of_turn|>", "").replace("<|end_of_text|>", "")
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.strip("`").strip()
    return raw


def _extract_json_text(raw: str) -> str:
    raw = _clean_raw(raw)
    if raw.startswith("[") or raw.startswith("{"):
        return raw
    m = re.search(r"(\[.*\]|\{.*\})", raw, re.DOTALL)
    if not m:
        raise ValueError("Модель не вернула структурированный JSON")
    return m.group(1).strip()


def _scan_json_objects(text: str) -> List[Any]:
    objs: List[Any] = []
    decoder = json.JSONDecoder(strict=False)
    i = 0
    n = len(text or "")
    while i < n:
        pos = text.find("{", i)
        if pos < 0:
            break
        try:
            obj, end = decoder.raw_decode(text[pos:])
            if isinstance(obj, dict):
                objs.append(obj)
            i = pos + max(end, 1)
        except Exception:
            i = pos + 1
    return objs


def _json_load_loose(raw: str) -> Any:
    cleaned = _clean_raw(raw)
    json_str = cleaned
    if not (json_str.startswith("[") or json_str.startswith("{")):
        try:
            json_str = _extract_json_text(cleaned)
        except Exception:
            json_str = cleaned
    json_str = re.sub(r",\s*([}\]])", r"\1", json_str)
    try:
        return json.loads(json_str, strict=False)
    except json.JSONDecodeError as je:
        objs = _scan_json_objects(json_str)
        if objs:
            return objs
        objs = []
        for line in cleaned.splitlines():
            line = line.strip().rstrip(',')
            if not line or not line.startswith('{'):
                continue
            try:
                obj = json.loads(line, strict=False)
                if isinstance(obj, dict):
                    objs.append(obj)
            except Exception:
                pass
        if objs:
            return objs
        raise ValueError(f"Ошибка синтаксиса JSON модели: {je.msg} (строка {je.lineno}, колонка {je.colno})")


def _first(card: Dict[str, Any], keys: List[str]) -> str:
    for key in keys:
        value = card.get(key)
        if value not in (None, ""):
            return str(value).strip()
    return ""


def _raw_tags_value(item: Dict[str, Any]) -> str:
    """Preserve model-provided tags. No backend topic inference here."""
    if not isinstance(item, dict):
        return ""

    keys = ("tags", "tag", "теги", "тег", "хэштеги", "hashtags", "keywords", "terms", "topics", "labels")

    def collect(value) -> List[str]:
        if value in (None, ""):
            return []
        if isinstance(value, str):
            return [value]
        if isinstance(value, (list, tuple, set)):
            out: List[str] = []
            for x in value:
                if isinstance(x, dict):
                    out.extend(collect(x.get("tag") or x.get("name") or x.get("label") or x.get("value") or x.get("тег")))
                else:
                    out.extend(collect(x))
            return out
        if isinstance(value, dict):
            out: List[str] = []
            for k in keys:
                out.extend(collect(value.get(k)))
            return out
        return [str(value)]

    raw_parts: List[str] = []
    for k in keys:
        raw_parts.extend(collect(item.get(k)))
    for k in ("fields", "metadata", "meta", "extra"):
        nested = item.get(k)
        if isinstance(nested, dict):
            raw_parts.extend(collect(nested))
    return " ".join(str(x).strip().lstrip("#") for x in raw_parts if str(x).strip())


def _list_value(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return [str(x).strip() for x in value if str(x).strip()]
    text = str(value).strip()
    if not text:
        return []
    parts = re.split(r"\s*[|;\n]\s*", text)
    return [p.strip() for p in parts if p.strip()]


def _mcq_front_back(item: Dict[str, Any], front: str, back: str, card_type: str) -> tuple[str, str]:
    if str(card_type or "").strip().lower() != "mcq":
        return front, back
    options = []
    for key in ("options", "choices", "variants", "answers", "варианты", "варианты_ответа"):
        options = _list_value(item.get(key))
        if options:
            break
    if options and not re.search(r"\bA\)\s+", front, flags=re.I):
        letters = "ABCD"
        lines = []
        for i, opt in enumerate(options[:4]):
            label = letters[i] if i < len(letters) else str(i + 1)
            opt = re.sub(r"^[A-DА-Г][).]\s*", "", str(opt).strip(), flags=re.I)
            lines.append(f"{label}) {opt}")
        front = (front.rstrip() + "\n" + "\n".join(lines)).strip()
    correct = _first(item, ["correct", "correct_answer", "answer_key", "right", "правильный", "правильный_ответ"])
    explanation = _first(item, ["explanation", "why", "пояснение", "объяснение"])
    if correct and not re.search(r"^(правильный ответ|correct answer)\s*:", back, flags=re.I):
        prefix = "Правильный ответ"
        back = f"{prefix}: {correct}. {explanation or back}".strip()
    return front, back


def clean_json(raw: str) -> List[Dict[str, Any]]:
    data = _json_load_loose(raw)
    if isinstance(data, dict):
        data = data.get("cards") or data.get("flashcards") or [data]
    if not isinstance(data, list):
        raise ValueError(f"Неверный корневой тип JSON: {type(data)}")

    normalized: List[Dict[str, Any]] = []
    for item in data:
        tags = ""
        card_type = ""
        item_index = ""
        if isinstance(item, dict):
            front = _first(item, ["front", "question", "q", "term", "вопрос"])
            back = _first(item, ["back", "answer", "a", "definition", "ответ", "короткий ответ"])
            quote = _first(item, ["source_quote", "quote", "source", "цитата", "цитата из текста", "источник"])
            mnemonic = _first(item, ["mnemonic", "hint", "cue", "m", "мнемоника", "короткая мнемоника", "ассоциация", "подсказка"])
            tags = _raw_tags_value(item)
            card_type = _first(item, ["card_type", "type", "тип"])
            item_index = _first(item, ["i", "index", "idx", "n", "номер", "id"])
        elif isinstance(item, (list, tuple)):
            item_index = ""
            vals = [str(x).strip() for x in item]
            front = vals[0] if len(vals) > 0 else ""
            back = vals[1] if len(vals) > 1 else ""
            quote = vals[2] if len(vals) > 2 else ""
            mnemonic = vals[3] if len(vals) > 3 else ""
            tags = vals[4] if len(vals) > 4 else ""
            card_type = vals[5] if len(vals) > 5 else ""
        else:
            continue
        front = re.sub(r"\s+", " ", front).strip()
        back = re.sub(r"\s+", " ", back).strip()
        quote = re.sub(r"\s+", " ", quote).strip()
        mnemonic = re.sub(r"\s+", " ", mnemonic).strip()
        tags = re.sub(r"\s+", " ", tags).strip()
        card_type = re.sub(r"\s+", "_", card_type.strip().lower())
        if isinstance(item, dict):
            front, back = _mcq_front_back(item, front, back, card_type)
        # Main card generation requires front/back. Tag-repair calls may return
        # tag-only objects; keep them so the repair layer can map tags back.
        if (not front or not back) and not tags:
            continue
        normalized.append({
            "front": front[:650],
            "back": back[:1400],
            "source_quote": quote[:900],
            "mnemonic": mnemonic[:600],
            "tags": tags[:300],
            "card_type": card_type[:80],
            "i": item_index[:32],
        })
    if not normalized:
        preview = _clean_raw(raw).replace("\n", " ")[:600]
        raise ValueError(f"Модель не вернула валидные карточки. Превью: {preview}")
    return normalized


def _response_text(response: Any) -> str:
    if isinstance(response, str):
        return response
    if isinstance(response, dict):
        content = response.get("content")
        if isinstance(content, list) and content:
            first = content[0]
            if isinstance(first, dict):
                return str(first.get("text") or first.get("content") or "")
            return str(first)
        return str(response.get("text") or response.get("content") or response)
    return str(response or "")


# ------------------------- streaming chunk helpers -------------------------

def _chunk_text(chunk: Any) -> str:
    """Extract text delta from one streaming chunk returned by send_message_async.

    Each chunk is a Mapping that looks like {"content": [{"text": "..."}], ...}.
    We are defensive about the shape because the C layer may send auxiliary
    fields (finish_reason, usage, etc.) without text.
    """
    if chunk is None:
        return ""
    if isinstance(chunk, str):
        return chunk
    if isinstance(chunk, dict):
        content = chunk.get("content")
        if isinstance(content, list):
            parts: List[str] = []
            for item in content:
                if isinstance(item, dict):
                    piece = item.get("text") or item.get("content")
                    if piece:
                        parts.append(str(piece))
                elif isinstance(item, str):
                    parts.append(item)
            return "".join(parts)
        if isinstance(content, str):
            return content
        text = chunk.get("text") or chunk.get("delta")
        if text:
            return str(text)
    return ""


# ------------------------- conversation creation -------------------------

def _make_conversation(
    system_message: Optional[str],
    filter_thinking: Optional[bool] = None,
    sampler_config: Optional[SamplerConfig] = None,
):
    """Create a one-shot Conversation with the generation settings applied.

    `filter_thinking` defaults to AIFC_FILTER_THINKING for thinking models.
    `sampler_config` defaults to the env-derived config.
    """
    if llm_engine is None:
        raise RuntimeError("Движок ИИ не инициализирован")
    if sampler_config is None:
        sampler_config = _build_sampler_config()
    if filter_thinking is None:
        filter_thinking = _is_thinking_model(current_model_name) and _env_flag("AIFC_FILTER_THINKING", "1")
    kwargs: Dict[str, Any] = {
        "sampler_config": sampler_config,
        "automatic_tool_calling": False,  # we don't use tools; saves a parse pass
        "filter_channel_content_from_kv_cache": bool(filter_thinking),
    }
    if system_message:
        kwargs["system_message"] = system_message
    return llm_engine.create_conversation(**kwargs)


# ------------------------- default system messages -------------------------

_DEFAULT_SYSTEM_RU = (
    "Ты — методист учебных карточек. Создаёшь ровно запрошенное количество карточек по тексту источника.\n"
    "Одна карточка = одна законченная мысль. Вопрос самодостаточный, без обращения к списку.\n"
    "Ответ — короткий и фактический, своими словами. Цитата — короткий фрагмент источника, если она запрошена.\n"
    "Выводи только те поля, которые запрошены в схеме пользовательского сообщения. Не добавляй tags/mnemonic, если их нет в схеме.\n"
    "Не выдумывай факты вне текста. Без markdown, без пояснений.\n"
    "ОТВЕЧАЙ НЕМЕДЛЕННО. Не используй цепочку рассуждений. Не пиши блок <think>, <channel>thought или любое другое размышление перед ответом."
)

_DEFAULT_SYSTEM_EN = (
    "You are a study-flashcard author. Produce exactly the requested number of cards from the source text.\n"
    "One card = one complete idea. The question must be self-contained.\n"
    "Answer: short and factual, in your own words. Source quote: a short source fragment if requested.\n"
    "Output only the fields requested by the user schema. Do not add tags/mnemonic if the schema does not contain them.\n"
    "Do not invent facts. No markdown, no explanations.\n"
    "ANSWER IMMEDIATELY. Do not produce chain-of-thought or any <think>/<channel>thought block."
)


def default_system_message(language: str = "ru") -> str:
    return _DEFAULT_SYSTEM_EN if (language or "ru").lower().startswith("en") else _DEFAULT_SYSTEM_RU


# ------------------------- public API: streaming -------------------------

async def ask_litert_stream(
    prompt: str,
    system_message: Optional[str] = None,
    model_name: Optional[str] = None,
    language: str = "ru",
    filter_thinking: Optional[bool] = None,
    sampler_config: Optional[SamplerConfig] = None,
    no_think: Optional[bool] = None,
) -> AsyncIterator[Tuple[str, str]]:
    """Async generator yielding (delta_text, full_text_so_far).

    Wraps `Conversation.send_message_async()` which is a blocking iterator
    internally (it pulls from a `queue.Queue` populated by a C callback).
    We offload the iteration to a thread so the event loop stays responsive.

    The prompt is treated as raw user text. Legacy prompts that were manually
    wrapped with the Gemma chat template are auto-unwrapped.
    """
    global last_inference_seconds, last_error
    target = model_name or current_model_name or "gemma-4-E2B-it"
    if _is_thinking_model(target):
        force_sg = os.environ.get("AIFC_SUPERGEMMA_FORCE_NO_THINK", "").strip().lower()
        if force_sg in {"1", "true", "yes", "on"}:
            no_think = True
        elif force_sg in {"0", "false", "no", "off"}:
            no_think = False
        if filter_thinking is None:
            filter_thinking = True
    if llm_engine is None or current_model_name != target:
        init_engine(target)
    if llm_engine is None:
        raise RuntimeError(f"Движок ИИ не инициализирован: {current_model_path}")

    sys_msg = system_message if system_message is not None else default_system_message(language)
    user_text = _strip_legacy_chat_wrap(prompt or "")
    # Prepend /no_think for thinking models (or when AIFC_FORCE_NO_THINK=1).
    # This is the primary thinking-suppression mechanism in Gemma 3/4.
    user_text = _apply_no_think(user_text, target, override=no_think)
    max_chars = get_model_prompt_chars(target)
    if len(user_text) > max_chars:
        user_text = user_text[:max_chars] + "\n[Текст обрезан...]"

    started = time.perf_counter()
    try:
        conv = _make_conversation(sys_msg, filter_thinking=filter_thinking, sampler_config=sampler_config)
    except Exception as e:
        last_error = str(e)
        raise

    full_text_holder: Dict[str, str] = {"value": ""}

    def _drain():
        # send_message_async returns a synchronous iterator of mapping chunks.
        try:
            iterator = conv.send_message_async(user_text)
            for chunk in iterator:
                delta = _chunk_text(chunk)
                if delta:
                    full_text_holder["value"] += delta
                yield delta
        finally:
            try:
                conv.close()
            except Exception:
                pass

    # Bridge the sync generator to async via to_thread + queue.
    queue: asyncio.Queue = asyncio.Queue(maxsize=64)
    SENTINEL = object()

    async def _producer():
        def _work():
            try:
                for delta in _drain():
                    # put_nowait would block if queue full; use asyncio run-coroutine.
                    asyncio.run_coroutine_threadsafe(queue.put(("delta", delta)), loop).result()
            except Exception as e:
                asyncio.run_coroutine_threadsafe(queue.put(("error", e)), loop).result()
            finally:
                asyncio.run_coroutine_threadsafe(queue.put(("end", None)), loop).result()

        await asyncio.to_thread(_work)

    loop = asyncio.get_event_loop()
    producer_task = asyncio.create_task(_producer())
    try:
        while True:
            kind, payload = await queue.get()
            if kind == "end":
                break
            if kind == "error":
                raise payload
            # payload is delta string (may be empty)
            yield payload, full_text_holder["value"]
    finally:
        if not producer_task.done():
            await producer_task
        last_inference_seconds = time.perf_counter() - started
        last_error = None


# ------------------------- public API: one-shot (v2) -------------------------

async def ask_litert_v2(
    prompt: str,
    system_message: Optional[str] = None,
    model_name: Optional[str] = None,
    language: str = "ru",
    use_cache: bool = False,
    early_stop_lines: Optional[int] = None,
    filter_thinking: Optional[bool] = None,
    sampler_config: Optional[SamplerConfig] = None,
    no_think: Optional[bool] = None,
    sampler_override: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """generation fast+quality path.

    - Treats `prompt` as raw user text (auto-unwraps legacy chat-template wrap).
    - Prepends `/no_think` toggle for thinking models (Gemma 3/4 control token).
    - Uses real `system_message` from create_conversation().
    - Uses `SamplerConfig` for low-temperature factual output.
      * `sampler_override` (dict with keys temperature/top_k/top_p/seed) wins
        over `sampler_config` over env defaults. This is the bridge from HTTP.
    - For thinking models, enables `filter_channel_content_from_kv_cache`.
    - If `early_stop_lines` is set, stops the stream as soon as that many JSONL
      lines have been seen. This is a major speedup: the model no longer keeps
      emitting after we have what we need.
    """
    global last_inference_seconds, last_error
    target = model_name or current_model_name or "gemma-4-E2B-it"
    if _is_thinking_model(target):
        force_sg = os.environ.get("AIFC_SUPERGEMMA_FORCE_NO_THINK", "").strip().lower()
        if force_sg in {"1", "true", "yes", "on"}:
            no_think = True
        elif force_sg in {"0", "false", "no", "off"}:
            no_think = False
        if filter_thinking is None:
            filter_thinking = True
    if llm_engine is None or current_model_name != target:
        init_engine(target)
    if llm_engine is None:
        raise RuntimeError(f"Движок ИИ не инициализирован: {current_model_path}")

    # Resolve sampler: explicit sampler_config > sampler_override dict > env.
    if sampler_config is None and sampler_override:
        sampler_config = _build_sampler_from_dict(sampler_override)

    sys_msg = system_message if system_message is not None else default_system_message(language)
    user_text = _strip_legacy_chat_wrap(prompt or "")
    # Prepend /no_think for thinking models (or when AIFC_FORCE_NO_THINK=1).
    # This is the primary thinking-suppression mechanism in Gemma 3/4.
    user_text = _apply_no_think(user_text, target, override=no_think)
    max_chars = get_model_prompt_chars(target)
    if len(user_text) > max_chars:
        user_text = user_text[:max_chars] + "\n[Текст обрезан...]"

    started = time.perf_counter()

    def _run():
        with _make_conversation(sys_msg, filter_thinking=filter_thinking, sampler_config=sampler_config) as conv:
            if early_stop_lines and early_stop_lines > 0:
                # Streaming + early-stop path: collect JSONL lines as they arrive
                # and abort once we have enough. The C layer supports cancellation
                # via conv.cancel_process(); we call it from the consumer thread.
                full: List[str] = []
                lines_collected = 0
                try:
                    for chunk in conv.send_message_async(user_text):
                        delta = _chunk_text(chunk)
                        if not delta:
                            continue
                        full.append(delta)
                        # Count complete JSON objects, not just newline-terminated JSONL.
                        # Some Gemma/SuperGemma replies produce objects inside an array or without
                        # clean line breaks; cancelling only after balanced objects prevents both
                        # premature truncation and long useless decode.
                        joined = "".join(full)
                        complete_objects = len(_scan_json_objects(joined))
                        if complete_objects >= early_stop_lines:
                            try:
                                conv.cancel_process()
                            except Exception:
                                pass
                            break
                    raw_text = "".join(full)
                except Exception as e_inner:
                    # If we got cancelled mid-stream, salvage what we have.
                    if "CANCELLED" in str(e_inner) or "Max number of tokens" in str(e_inner):
                        raw_text = "".join(full)
                    else:
                        raise
            else:
                # Plain one-shot: use send_message and grab the full response.
                response = conv.send_message(user_text)
                raw_text = _response_text(response).strip()
            if not raw_text:
                raise ValueError("Модель вернула пустой ответ")
            return raw_text

    try:
        raw_text = await asyncio.to_thread(_run)
        # Detect residual thinking tokens -> log a warning so the user knows
        # the /no_think toggle did not fully suppress thinking. Common with
        # abliterated models that ignore chat-template control tokens.
        if _output_has_thinking_tokens(raw_text):
            print(f"[LLM] WARNING: model emitted thinking tokens despite /no_think (raw_len={len(raw_text)}). Consider AIFC_FORCE_NO_THINK=1 + system-message hardening.")
        cards = clean_json(raw_text)
        last_inference_seconds = time.perf_counter() - started
        last_error = None
        print(f"[LLM] response raw={len(raw_text)} chars, cards={len(cards)}, time={last_inference_seconds:.1f}s, no_think={_should_no_think(target, override=no_think)}")
        return cards
    except Exception as e:
        last_inference_seconds = time.perf_counter() - started
        last_error = str(e)
        print(f"[LLM] Ошибка инференса/парсинга: {e} ({last_inference_seconds:.1f}s)")
        raise ValueError(str(e))


# ------------------------- backward-compat entry point -------------------------

async def ask_litert(
    prompt: str,
    image_path: Optional[str] = None,
    model_name: Optional[str] = None,
    use_cache: bool = False,
    prefer_tools: Optional[bool] = None,
) -> List[Dict[str, Any]]:
    """Backward-compatible wrapper. Routes to ask_litert_v2() by default.

    Set AIFC_LEGACY_ASK_LITERT=1 to fall back to the legacy_generation behavior
    (manual chat-template wrap, no sampler config, no thinking filter).
    """
    if _env_flag("AIFC_LEGACY_ASK_LITERT", "0"):
        return await _ask_litert_legacy(prompt, image_path=image_path, model_name=model_name, use_cache=use_cache, prefer_tools=prefer_tools)
    return await ask_litert_v2(prompt, model_name=model_name)


async def _ask_litert_legacy(
    prompt: str,
    image_path: Optional[str] = None,
    model_name: Optional[str] = None,
    use_cache: bool = False,
    prefer_tools: Optional[bool] = None,
) -> List[Dict[str, Any]]:
    """legacy_generation path: keep as fallback for reproducibility. Do not call directly."""
    global last_inference_seconds, last_error
    target = model_name or current_model_name or "gemma-4-E2B-it"
    if _is_thinking_model(target):
        if os.environ.get("AIFC_SUPERGEMMA_FORCE_NO_THINK", "1").strip().lower() in {"1", "true", "yes", "on"}:
            no_think = True
        if filter_thinking is None:
            filter_thinking = True
    if llm_engine is None or current_model_name != target:
        init_engine(target)
    if llm_engine is None:
        raise RuntimeError(f"Движок ИИ не инициализирован: {current_model_path}")

    max_chars = get_model_prompt_chars(target)
    if len(prompt or "") > max_chars:
        prompt = prompt[:max_chars] + "\n[Текст обрезан...]"

    def _run():
        with llm_engine.create_conversation() as conv:
            return conv.send_message(prompt)

    started = time.perf_counter()
    try:
        response = await asyncio.to_thread(_run)
        raw_text = _response_text(response).strip()
        if not raw_text:
            raise ValueError("Модель вернула пустой ответ")
        cards = clean_json(raw_text)
        last_inference_seconds = time.perf_counter() - started
        last_error = None
        print(f"[LLM132] response raw={len(raw_text)} chars, cards={len(cards)}, time={last_inference_seconds:.1f}s")
        return cards
    except Exception as e:
        last_inference_seconds = time.perf_counter() - started
        last_error = str(e)
        print(f"[LLM132] Ошибка инференса/парсинга: {e} ({last_inference_seconds:.1f}s)")
        raise ValueError(str(e))


async def benchmark_litert(model_name: str = "gemma-4-E2B-it", language: str = "ru") -> Dict[str, Any]:
    prompt = (
        "Создай 2 карточки. Верни JSON-массив объектов front/back/source_quote/mnemonic.\n"
        "ТЕКСТ:\nАлгоритм — это пошаговый способ решения задачи. Он преобразует входные данные в результат через понятные операции.\n"
    )
    if language == "en":
        prompt = (
            "Create 2 flashcards. Return JSON array with front/back/source_quote/mnemonic.\n"
            "TEXT:\nAn algorithm is a step-by-step procedure for solving a task. It turns input into output.\n"
        )
    t0 = time.perf_counter()
    if llm_engine is None or current_model_name != model_name:
        init_engine(model_name)
    load_wait = time.perf_counter() - t0
    t1 = time.perf_counter()
    cards = await ask_litert_v2(prompt, model_name=model_name, language=language, early_stop_lines=2)
    return {
        "model": model_name,
        "backend": current_backend_name,
        "loaded": llm_engine is not None,
        "load_wait_seconds": round(load_wait, 3),
        "generation_seconds": round(time.perf_counter() - t1, 3),
        "cards": len(cards),
        "sample": cards[:2],
        "status": get_engine_status(),
    }
