import { localPrefs, localView, isDisplayTag } from './utils.js?v=162';
const prefs = localPrefs();
const view = localView();
export const state = {
  decks: [],
  currentDeckId: null,
  graph: { sources: [], cards: [], edges: [] },
  selected: new Set(),
  activeSourceId: null,
  tool: 'select',
  scale: view.scale || 0.82,
  offsetX: view.offsetX || 80,
  offsetY: view.offsetY || 100,
  search: '',
  filterTag: '',
  focusTags: new Set(),
  hoverTag: '',
  focusStatuses: new Set(),
  focusCardTypes: new Set(),
  highlightedDate: '',
  indexes: { tagToCards: new Map(), sourceToCards: new Map(), deckToCards: new Map() },
  inspectDeckId: null,
  tagFocusMode: 'explicit',
  viewMode: localStorage.getItem('workspaceViewMode') || 'canvas',
  taskGroupMode: localStorage.getItem('taskGroupMode') || 'source',
  expandedSourceIds: new Set(),
  progressTimer: null,
  lastGenerationModel: 'gemma-4-E2B-it',
  config: { generation: { default_cards: 10, max_cards: 200, litert_batch_cards: 8, server_batch_cards: 18 } },
  prefs: {
    tagEdges: prefs.tagEdges ?? true,
    compact: prefs.compact ?? false,
    hideAnswers: prefs.hideAnswers ?? false,
    theme: prefs.theme || 'light',
  },
};
export function nodeKey(kind, id) { return `${kind}:${id}`; }
export function splitKey(key) { const i=key.indexOf(':'); return [key.slice(0,i), key.slice(i+1)]; }
export function getNode(kind, id) {
  if (kind === 'card') return state.graph.cards.find(c => String(c.id) === String(id));
  return state.graph.sources.find(s => String(s.id) === String(id));
}
export function selectedCards() { return [...state.selected].filter(k=>k.startsWith('card:')).map(k=>parseInt(k.slice(5),10)).filter(Number.isFinite); }
export function selectedSources() { return [...state.selected].filter(k=>k.startsWith('source:')).map(k=>k.slice(7)); }
export function selectedObjects() { return [...state.selected].map(k => { const [kind,id]=splitKey(k); const obj=getNode(kind,id); return obj ? {kind,id,obj} : null; }).filter(Boolean); }
export function rebuildIndexes() {
  const tagToCards = new Map();
  const sourceToCards = new Map();
  const deckToCards = new Map();
  for (const c of state.graph.cards || []) {
    const cid = String(c.id);
    const sid = String(c.source_node_id || '');
    const did = String(c.deck_id || state.currentDeckId || '');
    if (sid) { if (!sourceToCards.has(sid)) sourceToCards.set(sid, new Set()); sourceToCards.get(sid).add(cid); }
    if (did) { if (!deckToCards.has(did)) deckToCards.set(did, new Set()); deckToCards.get(did).add(cid); }
    for (const tag of tagsList(c.tags)) {
      if (!isUiTag(tag)) continue;
      if (!tagToCards.has(tag)) tagToCards.set(tag, new Set());
      tagToCards.get(tag).add(cid);
    }
  }
  state.indexes = { tagToCards, sourceToCards, deckToCards };
}


export function selectedFocusTags() {
  const result = new Set(state.focusTags || []);
  const hover = String(state.hoverTag || '').replace(/^#+/,'').trim().toLowerCase();
  if (hover) result.add(hover);
  return result;
}
export function selectedFocusStatuses() { return new Set(state.focusStatuses || []); }
export function selectedFocusCardTypes() { return new Set(state.focusCardTypes || []); }
export function selectByStatus(status) {
  const clean = String(status || '').trim().toLowerCase();
  if (!['inbox','today','planned','done'].includes(clean)) return;
  if (!(state.focusStatuses instanceof Set)) state.focusStatuses = new Set();
  if (state.focusStatuses.has(clean)) state.focusStatuses.delete(clean);
  else state.focusStatuses.add(clean);
}
export function selectByCardType(type) {
  const clean = String(type || 'basic').trim().toLowerCase() || 'basic';
  if (!(state.focusCardTypes instanceof Set)) state.focusCardTypes = new Set();
  if (state.focusCardTypes.has(clean)) state.focusCardTypes.delete(clean);
  else state.focusCardTypes.add(clean);
}
export function setHoverTag(tag) {
  state.hoverTag = String(tag || '').replace(/^#+/,'').trim().toLowerCase();
}
function tagsList(tags) {
  if (Array.isArray(tags)) return tags.map(x=>String(x||'').replace(/^#+/,'').trim().toLowerCase()).filter(Boolean);
  return String(tags || '').split(/[\s,;#]+/).map(x=>String(x||'').replace(/^#+/,'').trim().toLowerCase()).filter(Boolean);
}

function isUiTag(raw) { return isDisplayTag(raw); }
function addTagToMap(map, raw) {
  const t = String(raw || '').replace(/^#+/,'').trim().toLowerCase();
  if(!isUiTag(t)) return;
  map.set(t, (map.get(t)||0)+1);
}
function scopeCardsForTags() {
  // Левая панель тегов всегда показывает теги всей текущей колоды.
  // Выбор карточки/источника не должен сужать список: так можно выбрать несколько тегов подряд
  // и увидеть связи между ними на canvas.
  return state.graph.cards || [];
}
export function allTags() {
  const counts = new Map();
  const scope = scopeCardsForTags();
  for (const c of scope) {
    for (const raw of tagsList(c.tags)) addTagToMap(counts, raw);
  }
  return [...counts.entries()].sort((a,b)=>b[1]-a[1] || a[0].localeCompare(b[0]));
}
export function selectByTag(tag) {
  const clean = String(tag||'').replace(/^#+/,'').trim().toLowerCase();
  if (!clean || !isUiTag(clean)) return;
  state.filterTag = '';
  state.inspectDeckId = null;
  if (!(state.focusTags instanceof Set)) state.focusTags = new Set();
  if (state.focusTags.has(clean)) state.focusTags.delete(clean);
  else state.focusTags.add(clean);
}

export function clearTagFocus() {
  state.filterTag = '';
  state.focusTags = new Set();
  state.hoverTag = '';
  state.focusStatuses = new Set();
  state.focusCardTypes = new Set();
}
