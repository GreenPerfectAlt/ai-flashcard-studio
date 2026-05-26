import os
import re
import json
import asyncio
from typing import List, Dict, Any
import litert_lm

llm_engine = None
current_model_path = None

# Динамически вычисляем корень папки проекта для полной переносимости на любой ПК/ноутбук
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

MODELS = {
    "gemma-4-E2B-it": os.path.join(BASE_DIR, "models", "gemma-4-E2B-it.litertlm"),
    "supergemma4-e4b-abliterated": os.path.join(BASE_DIR, "models", "supergemma4-e4b-abliterated.litertlm")
}

def init_engine(model_name="gemma-4-E2B-it"):
    global llm_engine, current_model_path
    if model_name not in MODELS:
        raise ValueError(f"Неизвестная модель: {model_name}")
    model_path = MODELS[model_name]
    current_model_path = model_path
    print(f"[LLM] Инициализация: {model_path}...")
    try:
        llm_engine = litert_lm.Engine(model_path, backend=litert_lm.Backend.GPU())
        print("[LLM] Загружено в VRAM")
    except Exception as e:
        print(f"[LLM] Ошибка инициализации LiteRT-LM: {e}")
        raise

def unload_engine():
    global llm_engine, current_model_path
    if llm_engine:
        del llm_engine
        llm_engine = None
        current_model_path = None

def clean_json(raw: str) -> List[Dict[str, Any]]:
    # Удаляем мыслительную цепочку <think>...</think>, если она присутствует в выводе
    raw = re.sub(r'<think>.*?</think>', '', raw, flags=re.DOTALL)
    raw = raw.strip()
    
    # Ищем границы валидной JSON-структуры (массив или одиночный объект)
    match = re.search(r'(\[.*\]|\{.*\})', raw, re.DOTALL)
    if not match:
        raise ValueError("Модель не вернула структурированный JSON")
        
    json_str = match.group(1)
    
    # Очищаем от возможных остатков markdown-тегов разметки кода
    json_str = json_str.replace("```json", "").replace("```", "").strip()
    
    # Нормализуем синтаксис: убираем висящие запятые, ломающие стандартный json.loads
    json_str = re.sub(r',\s*}', '}', json_str)
    json_str = re.sub(r',\s*]', ']', json_str)
    
    try:
        data = json.loads(json_str)
    except json.JSONDecodeError as je:
        raise ValueError(f"Ошибка синтаксиса JSON модели: {je.msg} (строка {je.lineno}, колонка {je.colno})")
    
    if isinstance(data, dict):
        data = [data]
    elif not isinstance(data, list):
        raise ValueError(f"Неверный корневой тип JSON: {type(data)}")
    
    normalized = []
    for card in data:
        front_text = card.get("front", card.get("question", "")).strip()
        back_text = card.get("back", card.get("answer", "")).strip()
        
        if front_text and back_text:
            normalized.append({
                "front": front_text,
                "back": back_text,
                "source_quote": card.get("source_quote", card.get("quote", "")).strip(),
                "mnemonic": card.get("mnemonic", "").strip()
            })
            
    return normalized

async def ask_litert(prompt: str) -> List[Dict[str, Any]]:
    if not llm_engine:
        raise RuntimeError(f"Движок ИИ не инициализирован. Проверьте наличие файла модели по пути: {current_model_path}")
    if len(prompt) > 7000:
        prompt = prompt[:7000] + "\n[Текст обрезан...]"
    
    def _run():
        with llm_engine.create_conversation() as conv:
            return conv.send_message(prompt)
    
    try:
        response = await asyncio.to_thread(_run)
        if not response or "content" not in response or not response["content"]:
            raise ValueError("Модель вернула пустой ответ (сбой VRAM или контекста)")
            
        raw_text = response["content"][0]["text"].strip()
        print(f"[LLM] Ответ модели получен, длина: {len(raw_text)} символов.")
        cards = clean_json(raw_text)
        return cards
    except Exception as e:
        print(f"[LLM] Ошибка инференса/парсинга: {e}")
        raise ValueError(str(e))