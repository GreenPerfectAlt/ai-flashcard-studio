export const $ = (sel, root=document) => root.querySelector(sel);
export const $$ = (sel, root=document) => [...root.querySelectorAll(sel)];
export const esc = (s='') => String(s).replace(/[&<>'"]/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[ch]));
export const clamp = (v,min,max) => Math.max(min, Math.min(max, v));
export const debounce = (fn, ms=350) => { let t; return (...args) => { clearTimeout(t); t=setTimeout(()=>fn(...args), ms); }; };
export const TAG_BLACKLIST = new Set([
  'fact','факт','факты','прочее','разное','pdf','docx','txt','html','md',
  'ответ','вопрос','цитата','мнемоника','подсказка','контекст','source_quote','mnemonic','tags',
  'какое','какой','какая','какие','каким','почему','откуда','образом','только','должен','должна','должно',
  'валидный','компактный','markdown','формат','поле','структура','элемент','json','данном_контекст',
  'быть_коротким','быть_настоящим','валидный_компактный','валидный_компактный_json',
  'запоминани_информаци_мнемоник','подсказк_памят_хэштегов','только_подсказк_памят',
  'displaystyle','style','валидный_json','компактный_json','ключевой_факт'
]);
export function cleanTag(t) { return String(t || '').replace(/^#+/,'').trim().toLowerCase(); }
const RU_BAD_TAG_ENDINGS = /(ается|яется|ются|ется|ать|ять|ить|ться|ющий|ющая|ющее|ющие|вший|вшая|вшие|енный|анная|анное|анные|аемый|яемый|имый|ировано|ирован|овано)$/i;
const RU_BAD_TAG_MID = /(вш|ющ|емы|имы|ован|ирован|иваем|ываем)/i;
function looksLikeGeneratedNoise(part) {
  const p = cleanTag(part).replace(/-/g, '');
  if (!p) return true;
  if (TAG_BLACKLIST.has(p)) return true;
  if (/[а-я]/i.test(p) && (RU_BAD_TAG_ENDINGS.test(p) || (p.length > 7 && RU_BAD_TAG_MID.test(p)))) return true;
  return false;
}
export function isDisplayTag(tag) {
  const t = cleanTag(tag);
  if (!t || t.length < 2 || t.length > 40 || TAG_BLACKLIST.has(t)) return false;
  if (/^\d+$/.test(t)) return false;
  if ((t.match(/_/g) || []).length > 2) return false;
  return true;
}


export function displayTag(tag) {
  return cleanTag(tag).replace(/_/g, ' ');
}

export function tagsList(tags) {
  const raw = Array.isArray(tags) ? tags : String(tags || '').split(/[\s,;#]+/);
  const out = [];
  const seen = new Set();
  for (const item of raw) {
    const t = cleanTag(item);
    if (!isDisplayTag(t) || seen.has(t)) continue;
    seen.add(t);
    out.push(t);
  }
  return out;
}
export function localYMD(value = new Date()) {
  const d = value instanceof Date ? new Date(value) : parseLocalDate(value);
  if (!(d instanceof Date) || Number.isNaN(d.getTime())) return '';
  const y = d.getFullYear();
  const m = String(d.getMonth()+1).padStart(2,'0');
  const day = String(d.getDate()).padStart(2,'0');
  return `${y}-${m}-${day}`;
}
export function parseLocalDate(value) {
  if (!value) return new Date();
  if (typeof value === 'string') {
    const m = value.match(/^(\d{4})-(\d{2})-(\d{2})/);
    if (m) return new Date(Number(m[1]), Number(m[2])-1, Number(m[3]));
  }
  return new Date(value);
}
export function isProbablyUrl(s) { return /^(https?:\/\/)?([\wа-яё-]+\.)+[\wа-яё-]{2,}(\/|$)/i.test(String(s||'').trim()); }
export function isYoutubeUrl(s) { return /(youtube\.com|youtu\.be)/i.test(String(s||'')); }
export function localDeckId(){ const v=localStorage.getItem('currentDeckId'); return v ? parseInt(v,10) : null; }
export function setLocalDeckId(id){ if(id) localStorage.setItem('currentDeckId', String(id)); else localStorage.removeItem('currentDeckId'); }
export function localView(){ try { return JSON.parse(localStorage.getItem('canvasView') || '{}'); } catch { return {}; } }
export function saveLocalView(view){ localStorage.setItem('canvasView', JSON.stringify(view)); }
export function localPrefs(){ try { return JSON.parse(localStorage.getItem('canvasPrefs') || '{}'); } catch { return {}; } }
export function saveLocalPrefs(prefs){ localStorage.setItem('canvasPrefs', JSON.stringify(prefs)); }
export function nodeLabel(kind, obj) { return kind === 'source' ? (obj.title || 'Источник') : (obj.front || 'Карточка'); }
export function iconForSource(type='text') {
  const map = { url:'🌐', youtube:'▶️', pdf:'📕', docx:'📄', text:'📝', image:'🖼️', epub:'📚', fb2:'📚', file:'📎', legacy:'📄' };
  return map[String(type).toLowerCase()] || '📄';
}


export const SOURCE_TYPE_LABELS_RU = Object.freeze({
  import: 'Импорт',
  text: 'Текст',
  pdf: 'PDF',
  docx: 'DOCX',
  url: 'Ссылка',
  youtube: 'YouTube',
  image: 'Изображение',
  epub: 'EPUB',
  fb2: 'FB2',
  file: 'Файл',
  legacy: 'Источник',
  orphan: 'Без источника',
});
export function sourceTypeLabel(type = 'text') {
  const key = String(type || 'text').trim().toLowerCase();
  return SOURCE_TYPE_LABELS_RU[key] || key.replace(/_/g, ' ');
}

export const CARD_TYPE_LABELS_RU = Object.freeze({
  basic: 'Вопрос / ответ',
  definition: 'Определение',
  fact: 'Факт',
  concept: 'Понимание',
  cloze: 'Пропуск',
  true_false: 'Верно / неверно',
  mcq: 'Выбор ответа',
});
export const CARD_TYPE_ICONS_RU = Object.freeze({
  basic: '❔',
  definition: '📘',
  fact: '⚡',
  concept: '💡',
  cloze: '🧩',
  true_false: '✅',
  mcq: '🔘',
});
export function cardTypeLabel(type = 'basic') {
  const key = String(type || 'basic').trim().toLowerCase();
  return CARD_TYPE_LABELS_RU[key] || CARD_TYPE_LABELS_RU.basic;
}
export function cardTypeIcon(type = 'basic') {
  const key = String(type || 'basic').trim().toLowerCase();
  return CARD_TYPE_ICONS_RU[key] || CARD_TYPE_ICONS_RU.basic;
}
export function cardTypeOptionsHtml(selected = 'auto', includeAuto = true) {
  const options = includeAuto ? [['auto', 'Авто / mixed']] : [];
  for (const [value, label] of Object.entries(CARD_TYPE_LABELS_RU)) options.push([value, label]);
  return options.map(([value, label]) => `<option value="${esc(value)}" ${String(selected) === value ? 'selected' : ''}>${esc(label)}</option>`).join('');
}
export function tagsInputValue(tags) {
  return tagsList(tags).map(t => '#' + displayTag(t)).join(' ');
}
export function normalizeTagsForSave(value) {
  return tagsList(value).map(displayTag).join(' ');
}
