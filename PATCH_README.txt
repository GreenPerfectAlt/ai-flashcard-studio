AI Flashcard Studio — stage118 generation patch

Как ставить:
1) Распакуй архив поверх папки проекта с заменой файлов.
2) В папке проекта должны лежать модели: models/gemma-4-E2B-it.litertlm и/или models/supergemma4-e4b-abliterated.litertlm.
3) Запусти приложение.

Что исправлено:
- генерация не добивает недостающие карточки Python-шаблонами;
- если выбрано N, система добирает недостающие карточки через модель;
- если модель всё равно дала меньше N, по умолчанию ничего не сохраняется и показывается честная ошибка;
- частичное сохранение можно включить только вручную: AIFC_SAVE_PARTIAL_GENERATION=1;
- добавлен устойчивый парсер ответов модели: JSON, markdown JSON, Q/A текст;
- UI берёт список моделей из /api/model/list и показывает только найденные .litertlm;
- теги в генерации принудительно fast, чтобы не тормозить лишними NLP-проходами.

Проверено локально:
python -m py_compile main.py llm_config.py app/*.py
node --check static/js/api.js static/js/ui.js static/js/store.js
PYTHONPATH=. pytest -q  -> 10 passed

Важно:
Реальный прогон Gemma/SuperGemma в этом окружении не выполнялся, потому что .litertlm модели в архив не входят.
