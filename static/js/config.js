export const CLIENT_DEFAULTS = Object.freeze({
  generation: { default_cards: 10, max_cards: 200, litert_batch_cards: 8, server_batch_cards: 12 },
  layout: {
    node_w: 316,
    node_h: 262,
    source_w: 340,
    source_h: 210,
    source_x: 80,
    source_y: 120,
    source_gap_y: 220,
    card_x: 510,
    card_gap_x: 380,
    card_gap_y: 320,
    default_scale: 0.82,
    default_offset_x: 80,
    default_offset_y: 100
  },
  ui: {
    default_deck_accent: '#8f2942',
    card_size_min_w: 260,
    card_size_min_h: 220,
    card_size_max_w: 760,
    card_size_max_h: 760,
    menu_margin: 8,
    image_preview_margin: 18,
    debounce_ms: 350
  },
  card_types: {
    basic: { label: 'Вопрос / ответ', icon: '◇' },
    definition: { label: 'Определение', icon: '◧' },
    fact: { label: 'Факт', icon: '•' },
    concept: { label: 'Понимание', icon: '◈' },
    cloze: { label: 'Пропуск', icon: '□' },
    true_false: { label: 'Верно / неверно', icon: '✓' },
    mcq: { label: 'Выбор ответа', icon: '◉' }
  },
  source_types: {
    url: { label: 'Ссылка', icon: '🌐' },
    youtube: { label: 'YouTube', icon: '▶️' },
    pdf: { label: 'PDF', icon: '📕' },
    docx: { label: 'DOCX', icon: '📘' },
    text: { label: 'Текст', icon: '📝' },
    image: { label: 'Изображение', icon: '🖼️' },
    epub: { label: 'EPUB', icon: '📚' },
    fb2: { label: 'FB2', icon: '📚' },
    file: { label: 'Файл', icon: '📎' },
    import: { label: 'Импорт', icon: '⬇️' },
    legacy: { label: 'Источник', icon: '📄' }
  },
  tag_blacklist: [
    'fact','факт','факты','прочее','разное','pdf','docx','txt','html','md',
    'ответ','вопрос','цитата','мнемоника','подсказка','контекст','source_quote','mnemonic','tags',
    'какое','какой','какая','какие','каким','почему','откуда','образом','только','должен','должна','должно',
    'валидный','компактный','markdown','формат','поле','структура','элемент','json','данном_контекст',
    'быть_коротким','быть_настоящим','валидный_компактный','валидный_компактный_json',
    'запоминани_информаци_мнемоник','подсказк_памят_хэштегов','только_подсказк_памят',
    'danil_brizz','displaystyle','style','задолго','начала','производства','даже_того','некоторых_растений',
    'растен_насеком','вырабатываем_медоносн','валидный_json','компактный_json','ключевой_факт'
  ]
});

export function mergeConfig(base, incoming) {
  const out = (typeof structuredClone === 'function') ? structuredClone(base) : JSON.parse(JSON.stringify(base));
  const merge = (target, src) => {
    if (!src || typeof src !== 'object') return target;
    for (const [k, v] of Object.entries(src)) {
      if (v && typeof v === 'object' && !Array.isArray(v) && target[k] && typeof target[k] === 'object' && !Array.isArray(target[k])) merge(target[k], v);
      else target[k] = v;
    }
    return target;
  };
  return merge(out, incoming || {});
}

export function layoutValue(config, key) {
  return Number(config?.layout?.[key] ?? CLIENT_DEFAULTS.layout[key]);
}
