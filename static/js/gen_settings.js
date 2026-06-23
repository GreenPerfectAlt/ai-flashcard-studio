const STORAGE_KEY = 'aifc_gen_settings_v146';
const CUSTOM_PRESETS_KEY = 'aifc_custom_presets_v145';
const ACTIVE_PRESET_KEY = 'aifc_active_gen_preset_v145';

// Sensible defaults if backend endpoint is not reachable. These mirror the
// env-derived defaults from main.py /api/generation/defaults.
const FALLBACK_DEFAULTS = {
  temperature: 0.35,
  top_k: 40,
  top_p: 0.92,
  min_p: 0.0,
  seed: null,
  cards_per_call: 40,
  no_think: true,
  quality_gate: false,
  evidence_select: false,
  stream_gen: true,
  filter_thinking: true,
  allow_duplicates: true,
  generate_tags: false,
  generate_mnemonics: false,
};

let cache = null;
let defaultsCache = null;
let customPresetsCache = null;

function getActivePreset() {
  try {
    const raw = localStorage.getItem(ACTIVE_PRESET_KEY);
    return raw ? JSON.parse(raw) : null;
  } catch (e) {
    return null;
  }
}

function setActivePreset(kind, id, name) {
  const value = kind && id ? { kind, id, name: name || id } : null;
  try {
    if (value) localStorage.setItem(ACTIVE_PRESET_KEY, JSON.stringify(value));
    else localStorage.removeItem(ACTIVE_PRESET_KEY);
  } catch (e) {}
  return value;
}

function isActivePreset(kind, id) {
  const active = getActivePreset();
  return !!active && active.kind === kind && active.id === id;
}

function activePresetLabel() {
  const active = getActivePreset();
  return active?.name || '';
}

function numOr(value, fallback) {
  const n = Number(value);
  return Number.isFinite(n) ? n : fallback;
}


export function getGenSettings() {
  if (cache) return cache;
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    cache = raw ? { ...FALLBACK_DEFAULTS, ...JSON.parse(raw) } : { ...FALLBACK_DEFAULTS };
  } catch (e) {
    cache = { ...FALLBACK_DEFAULTS };
  }
  return cache;
}

export function setGenSettings(next) {
  cache = { ...cache, ...next };
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(cache));
  } catch (e) {}
  return cache;
}

export function resetGenSettings() {
  cache = { ...FALLBACK_DEFAULTS, ...(defaultsCache || {}) };
  try {
    localStorage.removeItem(STORAGE_KEY);
  } catch (e) {}
  return cache;
}

export async function loadGenDefaults() {
  try {
    const res = await fetch('/api/generation/defaults');
    if (!res.ok) return null;
    const data = await res.json();
    defaultsCache = {
      temperature: data.temperature,
      top_k: data.top_k,
      top_p: data.top_p,
      min_p: data.min_p,
      seed: data.seed,
      cards_per_call: data.cards_per_call,
      no_think: data.toggles?.no_think ?? true,
      quality_gate: data.toggles?.quality_gate ?? false,
      evidence_select: data.toggles?.evidence_select ?? false,
      stream_gen: data.toggles?.stream_gen ?? true,
      filter_thinking: data.toggles?.filter_thinking ?? true,
      allow_duplicates: data.toggles?.allow_duplicates ?? true,
      generate_tags: data.toggles?.generate_tags ?? false,
      generate_mnemonics: data.toggles?.generate_mnemonics ?? false,
    };
    return defaultsCache;
  } catch (e) {
    return null;
  }
}

export async function loadBuiltInPresets() {
  try {
    const res = await fetch('/api/generation/presets');
    if (!res.ok) return [];
    const data = await res.json();
    return Array.isArray(data.presets) ? data.presets : [];
  } catch (e) {
    return [];
  }
}

// ----- custom presets (user-saved chips with × delete) -----

export function getCustomPresets() {
  if (customPresetsCache) return customPresetsCache;
  try {
    const raw = localStorage.getItem(CUSTOM_PRESETS_KEY);
    customPresetsCache = raw ? JSON.parse(raw) : [];
  } catch (e) {
    customPresetsCache = [];
  }
  if (!Array.isArray(customPresetsCache)) customPresetsCache = [];
  return customPresetsCache;
}

export function saveCustomPreset(name, settings) {
  const list = getCustomPresets();
  const id = 'cust_' + Date.now().toString(36) + '_' + Math.random().toString(36).slice(2, 6);
  const preset = { id, name: String(name || '').trim().slice(0, 40), settings: { ...settings } };
  list.push(preset);
  try { localStorage.setItem(CUSTOM_PRESETS_KEY, JSON.stringify(list)); } catch (e) {}
  customPresetsCache = list;
  return preset;
}

export function deleteCustomPreset(id) {
  const list = getCustomPresets().filter(p => p.id !== id);
  try { localStorage.setItem(CUSTOM_PRESETS_KEY, JSON.stringify(list)); } catch (e) {}
  customPresetsCache = list;
  return list;
}

// ----- payload builder for api.generate() -----

function readLiveGenSettings(root = document) {
  const live = {};
  const scope = root && typeof root.querySelectorAll === 'function' ? root : document;
  scope.querySelectorAll('[data-gen-field]').forEach(input => {
    const key = input.dataset.genField;
    if (!key) return;
    const raw = String(input.value ?? '').trim();
    if (key === 'seed') {
      live[key] = raw === '' ? null : Number(raw);
    } else {
      live[key] = raw === '' ? null : Number(raw);
    }
    if (Number.isNaN(live[key]) || !Number.isFinite(live[key])) live[key] = null;
  });
  scope.querySelectorAll('[data-gen-toggle]').forEach(input => {
    const key = input.dataset.genToggle;
    if (!key) return;
    live[key] = !!input.checked;
  });
  if (Object.keys(live).length) setGenSettings(live);
  return live;
}

export function buildGenPayload(root = document) {
  const live = readLiveGenSettings(root);
  const s = { ...getGenSettings(), ...live };
  const payload = {};
  if (s.temperature !== null && s.temperature !== undefined && !Number.isNaN(Number(s.temperature))) {
    payload.temperature = Number(s.temperature);
  }
  if (s.top_k !== null && s.top_k !== undefined && !Number.isNaN(Number(s.top_k))) {
    payload.top_k = Number(s.top_k);
  }
  if (s.top_p !== null && s.top_p !== undefined && !Number.isNaN(Number(s.top_p))) {
    payload.top_p = Number(s.top_p);
  }
  if (s.min_p !== null && s.min_p !== undefined && !Number.isNaN(Number(s.min_p))) {
    payload.min_p = Number(s.min_p);
  }
  if (s.seed !== null && s.seed !== undefined && String(s.seed).trim() !== '' && !Number.isNaN(Number(s.seed))) {
    payload.seed = Number(s.seed);
  }
  if (s.cards_per_call !== null && s.cards_per_call !== undefined && !Number.isNaN(Number(s.cards_per_call))) {
    payload.cards_per_call = Number(s.cards_per_call);
  }
  if (s.no_think !== null && s.no_think !== undefined) payload.no_think = !!s.no_think;
  if (s.quality_gate !== null && s.quality_gate !== undefined) payload.quality_gate = !!s.quality_gate;
  if (s.evidence_select !== null && s.evidence_select !== undefined) payload.evidence_select = !!s.evidence_select;
  if (s.stream_gen !== null && s.stream_gen !== undefined) payload.stream_gen = !!s.stream_gen;
  if (s.filter_thinking !== null && s.filter_thinking !== undefined) payload.filter_thinking = !!s.filter_thinking;
  if (s.allow_duplicates !== null && s.allow_duplicates !== undefined) payload.allow_duplicates = !!s.allow_duplicates;
  if (s.generate_tags !== null && s.generate_tags !== undefined) payload.generate_tags = !!s.generate_tags;
  if (s.generate_mnemonics !== null && s.generate_mnemonics !== undefined) payload.generate_mnemonics = !!s.generate_mnemonics;
  return payload;
}

export function applyPreset(preset, kind = 'builtin') {
  if (!preset || !preset.settings) return getGenSettings();
  const next = setGenSettings({ ...preset.settings });
  setActivePreset(kind, preset.id, preset.title || preset.name || preset.id);
  return next;
}

// ----- HTML rendering -----

export function genSettingsPanelHtml() {
  const s = getGenSettings();
  const activeLabel = activePresetLabel();
  const tempVal = numOr(s.temperature, 0.35);
  const topKVal = Math.trunc(numOr(s.top_k, 40));
  const topPVal = numOr(s.top_p, 0.92);
  const minPVal = numOr(s.min_p, 0.0);
  const seedVal = (s.seed === null || s.seed === undefined) ? '' : String(s.seed);
  const cpcVal = Math.trunc(numOr(s.cards_per_call, 40));
  const fieldToggles = [
    { key: 'generate_tags', label: 'Генерировать теги' },
    { key: 'generate_mnemonics', label: 'Генерировать мнемоники' },
  ];
  const toggles = [
    { key: 'no_think', label: 'Без размышлений' },
    { key: 'quality_gate', label: 'Валидатор карточек' },
    { key: 'evidence_select', label: 'Отбирать лучшие предложения' },
    { key: 'stream_gen', label: 'Стриминг + ранняя остановка' },
    { key: 'filter_thinking', label: 'Чистить thinking из KV-cache' },
    { key: 'allow_duplicates', label: 'Разрешить дубликаты' },
  ];
  const toggleHtml = (items) => items.map(t => `
    <label class="gen-toggle">
      <span class="gen-toggle-switch">
        <input type="checkbox" data-gen-toggle="${t.key}" ${s[t.key] ? 'checked' : ''}>
        <span class="gen-toggle-track"></span>
      </span>
      <span class="gen-toggle-text"><b>${t.label}</b></span>
    </label>`).join('');
  const fieldTogglesHtml = toggleHtml(fieldToggles);
  const togglesHtml = toggleHtml(toggles);

  // Custom presets chips (user-saved).
  const custom = getCustomPresets();
  const customChipsHtml = custom.length
    ? custom.map(p => `
        <span class="gen-preset-chip gen-preset-custom ${isActivePreset('custom', p.id) ? 'active' : ''}" data-gen-custom-preset="${p.id}" title="Применить «${esc(p.name)}»">
          <span class="gen-preset-chip-name">${esc(p.name)}</span>
          <button type="button" class="gen-preset-chip-x" data-gen-delete-preset="${p.id}" title="Удалить пресет">×</button>
        </span>`).join('')
    : `<span class="gen-preset-empty">Своих пресетов пока нет — сохрани текущие настройки под именем.</span>`;

  return `
  <div class="gen-panel" id="genPanel">
    <div class="gen-head">
      <button type="button" class="gen-collapse-btn" data-gen-collapse title="Свернуть/развернуть">
        <span class="gen-chevron">▾</span> <span>⚙ Настройки генерации</span>
      </button>
    </div>
    <div class="gen-body" id="genBody">
      <div class="gen-active-row">Выбран пресет: <b>${activeLabel ? esc(activeLabel) : '—'}</b></div>
      <div class="gen-section">
        <div class="gen-section-title">Пресеты</div>
        <div class="gen-presets" role="group" aria-label="Быстрые пресеты">
          <button type="button" class="gen-preset-btn ${isActivePreset('builtin', 'fast') ? 'active' : ''}" data-gen-preset="fast"    >⚡ Быстро</button>
          <button type="button" class="gen-preset-btn ${isActivePreset('builtin', 'balanced') ? 'active' : ''}" data-gen-preset="balanced">⚖ Баланс</button>
          <button type="button" class="gen-preset-btn ${isActivePreset('builtin', 'quality') ? 'active' : ''}" data-gen-preset="quality" >🎯 Качество</button>
          <button type="button" class="gen-preset-btn ${isActivePreset('builtin', 'deep') ? 'active' : ''}" data-gen-preset="deep"    >🧠 С размышлением</button>
        </div>
      </div>
      <div class="gen-section">
        <div class="gen-section-title">Свои пресеты</div>
        <div class="gen-custom-presets" id="genCustomPresets">${customChipsHtml}</div>
        <div class="gen-save-preset-row">
          <input type="text" id="genNewPresetName" placeholder="Имя пресета, например «Экзамен»" maxlength="40">
          <button type="button" class="secondary" data-gen-save-preset>＋ Сохранить</button>
        </div>
      </div>
      <div class="gen-section">
        <div class="gen-section-title">Сэмплинг</div>
        <div class="gen-sliders">
          <label class="gen-slider">
            <span class="gen-slider-label">Temperature <code data-gen-display="temperature">${Number(tempVal).toFixed(2)}</code></span>
            <input type="number" min="0" max="5" step="0.01" value="${tempVal}" data-gen-field="temperature">
          </label>
          <label class="gen-slider">
            <span class="gen-slider-label">Top-K <code data-gen-display="top_k">${topKVal}</code></span>
            <input type="number" min="0" max="1000" step="1" value="${topKVal}" data-gen-field="top_k">
          </label>
          <label class="gen-slider">
            <span class="gen-slider-label">Top-P <code data-gen-display="top_p">${Number(topPVal).toFixed(2)}</code></span>
            <input type="number" min="0" max="1" step="0.01" value="${topPVal}" data-gen-field="top_p">
          </label>
          <label class="gen-slider">
            <span class="gen-slider-label">Min-P <code data-gen-display="min_p">${Number(minPVal).toFixed(2)}</code></span>
            <input type="number" min="0" max="1" step="0.01" value="${minPVal}" data-gen-field="min_p">
          </label>
          <label class="gen-slider">
            <span class="gen-slider-label">Карточек за вызов <code data-gen-display="cards_per_call">${cpcVal}</code></span>
            <input type="number" min="1" max="200" step="1" value="${cpcVal}" data-gen-field="cards_per_call">
          </label>
          <label class="gen-seed">
            <span class="gen-slider-label">Seed <code data-gen-display="seed">${seedVal || '—'}</code></span>
            <input type="number" value="${seedVal}" placeholder="случайно" data-gen-field="seed">
          </label>
        </div>
      </div>
      <div class="gen-section">
        <div class="gen-section-title">Поля карточек</div>
        <div class="gen-toggles">
          ${fieldTogglesHtml}
        </div>
      </div>
      <div class="gen-section">
        <div class="gen-section-title">Поведение</div>
        <div class="gen-toggles">
          ${togglesHtml}
        </div>
      </div>
      <div class="gen-actions">
        <button type="button" class="secondary gen-reset" data-gen-reset>↺ Сброс к дефолту</button>
        <span class="gen-status" id="genStatus"></span>
      </div>
    </div>
  </div>`;
}

// Wire up the panel after it has been inserted into the DOM.
export function bindGenSettingsPanel(container, onChange) {
  if (!container || typeof container.querySelector !== 'function') return;
  const panel = container.querySelector('#genPanel');
  if (!panel) return;

  const body = panel.querySelector('#genBody');
  if (!body) return;
  const collapseBtn = panel.querySelector('[data-gen-collapse]');
  const statusEl = panel.querySelector('#genStatus');

  const collapsed = localStorage.getItem('aifc_gen_collapsed') === '1';
  if (collapsed) body.classList.add('gen-collapsed');

  collapseBtn?.addEventListener('click', () => {
    body.classList.toggle('gen-collapsed');
    localStorage.setItem('aifc_gen_collapsed', body.classList.contains('gen-collapsed') ? '1' : '0');
  });

  const updateDisplay = (key, value) => {
    const display = panel.querySelector(`[data-gen-display="${key}"]`);
    if (!display) return;
    if (key === 'temperature' || key === 'top_p' || key === 'min_p') {
      display.textContent = Number(value || 0).toFixed(2);
    } else if (key === 'seed') {
      display.textContent = (value === null || value === undefined || value === '') ? '—' : String(value);
    } else {
      display.textContent = String(value);
    }
  };

  const flash = (msg) => {
    if (!statusEl) return;
    statusEl.textContent = msg;
    statusEl.classList.add('gen-flash');
    clearTimeout(statusEl._flashTimer);
    statusEl._flashTimer = setTimeout(() => {
      statusEl.classList.remove('gen-flash');
      statusEl.textContent = '';
    }, 1600);
  };

  const rerenderPanel = () => {
    const fresh = document.createElement('div');
    const html = genSettingsPanelHtml();
    if (typeof html !== 'string' || !html.trim()) return;
    fresh.innerHTML = html;
    const newPanel = fresh.firstElementChild;
    if (!newPanel) return;
    // Preserve collapsed state.
    const wasCollapsed = body?.classList?.contains('gen-collapsed');
    panel.replaceWith(newPanel);
    if (wasCollapsed) newPanel.querySelector('#genBody')?.classList.add('gen-collapsed');
    bindGenSettingsPanel(newPanel.parentElement || document, onChange);
  };

  // Sliders + seed input
  panel.querySelectorAll('[data-gen-field]').forEach(input => {
    const key = input.dataset.genField;
    input.addEventListener('input', () => {
      let value;
      if (key === 'seed') {
        const raw = String(input.value || '').trim();
        value = raw === '' ? null : Number(raw);
        if (raw !== '' && (Number.isNaN(value) || !Number.isFinite(value))) value = null;
      } else {
        value = Number(input.value);
        if (Number.isNaN(value)) value = null;
      }
      const next = setGenSettings({ [key]: value });
      updateDisplay(key, value);
      onChange?.(next);
    });
  });

  // Toggles
  panel.querySelectorAll('[data-gen-toggle]').forEach(input => {
    const key = input.dataset.genToggle;
    input.addEventListener('change', () => {
      const next = setGenSettings({ [key]: !!input.checked });
      onChange?.(next);
    });
  });

  // Built-in presets
  panel.querySelectorAll('[data-gen-preset]').forEach(btn => {
    btn.addEventListener('click', async () => {
      const presets = await loadBuiltInPresets();
      const preset = presets.find(p => p.id === btn.dataset.genPreset);
      if (!preset) return;
      const next = applyPreset(preset, 'builtin');
      rerenderPanel();
      flash(`Пресет: ${preset.title}`);
      onChange?.(next);
    });
  });

  // Custom presets — click chip to apply
  panel.querySelectorAll('[data-gen-custom-preset]').forEach(chip => {
    chip.addEventListener('click', (e) => {
      // Ignore clicks on the × button (it has its own handler).
      if (e.target.closest('[data-gen-delete-preset]')) return;
      const id = chip.dataset.genCustomPreset;
      const preset = getCustomPresets().find(p => p.id === id);
      if (!preset) return;
      const next = applyPreset(preset, 'custom');
      rerenderPanel();
      flash(`Применён: ${preset.name}`);
      onChange?.(next);
    });
  });

  // Custom presets — delete via ×
  panel.querySelectorAll('[data-gen-delete-preset]').forEach(btn => {
    btn.addEventListener('click', (e) => {
      e.stopPropagation();
      const id = btn.dataset.genDeletePreset;
      const preset = getCustomPresets().find(p => p.id === id);
      if (!preset) return;
      if (!confirm(`Удалить пресет «${preset.name}»?`)) return;
      deleteCustomPreset(id);
      if (isActivePreset('custom', id)) setActivePreset(null, null, null);
      rerenderPanel();
      flash('Пресет удалён');
    });
  });

  // Save current settings as a new custom preset
  panel.querySelector('[data-gen-save-preset]')?.addEventListener('click', () => {
    const nameInput = panel.querySelector('#genNewPresetName');
    const rawName = String(nameInput?.value || '').trim();
    if (!rawName) {
      flash('Введи имя пресета');
      nameInput?.focus();
      return;
    }
    const settings = { ...getGenSettings() };
    // Drop nullish fields so the preset only stores real values.
    const cleaned = {};
    for (const [k, v] of Object.entries(settings)) {
      if (v !== null && v !== undefined && v !== '') cleaned[k] = v;
    }
    const newPreset = saveCustomPreset(rawName, cleaned);
    setActivePreset('custom', newPreset.id, newPreset.name);
    if (nameInput) nameInput.value = '';
    rerenderPanel();
    flash(`Сохранён: ${rawName}`);
  });

  // Enter key in the preset name input also saves.
  panel.querySelector('#genNewPresetName')?.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') {
      e.preventDefault();
      panel.querySelector('[data-gen-save-preset]')?.click();
    }
  });

  // Reset
  panel.querySelector('[data-gen-reset]')?.addEventListener('click', async () => {
    await loadGenDefaults();
    const next = resetGenSettings();
    setActivePreset(null, null, null);
    rerenderPanel();
    flash('Сброшено к дефолту');
    onChange?.(next);
  });
}

// Local escape for chip names — same as utils.esc but inline so this module
// can be loaded without circular deps.
function esc(value) {
  return String(value == null ? '' : value)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}
