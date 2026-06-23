import { api } from './api.js?v=162';
import { state, nodeKey, splitKey, getNode, selectedCards, selectByTag, selectedFocusTags, selectedFocusStatuses, selectedFocusCardTypes, selectByStatus, selectByCardType, clearTagFocus, setHoverTag } from './store.js?v=162';
import { $, $$, esc, tagsList, displayTag, debounce, clamp, saveLocalView, saveLocalPrefs, iconForSource, localYMD, cardTypeLabel, cardTypeIcon, sourceTypeLabel } from './utils.js?v=162';
import { openCardEditor, openCardCreator, renderInspector, showSourceInfo, openSourceModalAt } from './ui.js?v=162';

let canvas, nodesLayer, edgesSvg, workspace, lasso;
let drag = null, pan = null, lassoStart = null;
let lastSpaceTapAt = 0;
let singleSpaceTimer = null;
const DOUBLE_SPACE_MS = 360;
let edgeFragment = null;
const NODE_W = 316, NODE_H = 262, SOURCE_W = 340, SOURCE_H = 210;
const BRICK_X = 380, BRICK_Y = 320;
function cardSizeKey(id){ return `cardSize:${id}`; }
function getCardSize(id){
  try {
    const raw = localStorage.getItem(cardSizeKey(id));
    if(!raw) return null;
    const v = JSON.parse(raw);
    const w = Math.max(260, Math.min(760, Number(v.w || 0)));
    const h = Math.max(220, Math.min(760, Number(v.h || 0)));
    return w && h ? {w,h} : null;
  } catch { return null; }
}
function saveCardSize(id,w,h){
  try { localStorage.setItem(cardSizeKey(id), JSON.stringify({w:Math.round(w),h:Math.round(h)})); } catch {}
}
function cardW(card){ return getCardSize(card?.id)?.w || NODE_W; }
function cardH(card){ return getCardSize(card?.id)?.h || NODE_H; }
function removeCardSize(id){ try { localStorage.removeItem(cardSizeKey(id)); } catch {} }

function layoutBrickGrid(list, x, y, columns = 3, gapX = BRICK_X, gapY = BRICK_Y) {
  const sorted = [...list].sort((a,b)=>(a.y ?? 0)-(b.y ?? 0) || Number(a.id||0)-Number(b.id||0));
  sorted.forEach((item, i) => {
    item.x = Math.round(x + (i % columns) * gapX);
    item.y = Math.round(y + Math.floor(i / columns) * gapY);
  });
}
function layoutSideGrid(list, x, y) {
  const cols = list.length <= 3 ? list.length || 1 : list.length <= 8 ? 2 : 3;
  layoutBrickGrid(list, x, y, cols, BRICK_X, BRICK_Y);
  return cols * BRICK_X + 110;
}
function cardsOverlap(a, b) {
  return Math.abs(Number(a.x || 0) - Number(b.x || 0)) < NODE_W + 28 && Math.abs(Number(a.y || 0) - Number(b.y || 0)) < NODE_H + 28;
}
function groupHasOverlap(list) {
  for (let i = 0; i < list.length; i++) {
    for (let j = i + 1; j < list.length; j++) if (cardsOverlap(list[i], list[j])) return true;
  }
  return false;
}

function edgeColor(seed, saturation = 78, lightness = 58, alpha = .72) {
  const text = String(seed || 'edge');
  let hash = 0;
  for (let i = 0; i < text.length; i++) hash = ((hash << 5) - hash + text.charCodeAt(i)) | 0;
  const hue = Math.abs(hash) % 360;
  return `hsla(${hue}, ${saturation}%, ${lightness}%, ${alpha})`;
}

function selectedCardData() {
  const ids = new Set(selectedCards().map(String));
  return state.graph.cards.filter(c => ids.has(String(c.id)));
}
function cardDueKey(c) {
  if (!c) return '';
  const d = localYMD(c.due_date);
  if (d) return d;
  if ((c.status || '') === 'today') return localYMD(new Date());
  return '';
}

function statusKey(card) {
  return String(card?.status || 'inbox').trim().toLowerCase() || 'inbox';
}
function typeKey(card) {
  return String(card?.card_type || 'basic').trim().toLowerCase() || 'basic';
}
function cardHasAnyStatusFocus(card, focus = selectedFocusStatuses()) {
  return Boolean(focus && focus.size && focus.has(statusKey(card)));
}
function cardHasAnyTypeFocus(card, focus = selectedFocusCardTypes()) {
  return Boolean(focus && focus.size && focus.has(typeKey(card)));
}
function cardHasAnyMetaFocus(card) {
  return cardHasAnyStatusFocus(card) || cardHasAnyTypeFocus(card);
}
function sourceHasAnyMetaFocus(source) {
  const sid = String(source?.id || '');
  return (state.graph.cards || []).some(c => String(c.source_node_id || '') === sid && cardHasAnyMetaFocus(c));
}
function dateLabelShort(value) {
  const ymd = localYMD(value);
  if (!ymd) return '';
  const [y,m,d] = ymd.split('-');
  return d && m ? `${d}.${m}.${y}` : ymd;
}
function statusPlainLabel(card) {
  const st = statusKey(card);
  if (st === 'today') return 'Сегодня';
  if (st === 'planned') return 'Запланировано';
  if (st === 'done') return 'Готово';
  return 'Входящие';
}
function statusDisplayLabel(card) {
  const due = localYMD(card?.due_date);
  if (due) return dateLabelShort(due);
  return statusPlainLabel(card);
}
function statusTooltipLabel(card) {
  const due = localYMD(card?.due_date);
  const plain = statusPlainLabel(card);
  return due ? `Статус: ${plain} · ${dateLabelShort(due)}` : `Статус: ${plain}`;
}

function statusEmoji(card) {
  const st = statusKey(card);
  if (st === 'done') return '✅';
  if (st === 'today') return '🔥';
  if (st === 'planned') return '📅';
  return '📩';
}
function typeEmoji(type) {
  return cardTypeIcon(type);
}


function relationContext() {
  const selected = selectedCardData();
  const tags = new Set();
  const sources = new Set();
  const dates = new Set();
  const statuses = new Set();
  const types = new Set();
  for (const c of selected) {
    for (const t of tagsList(c.tags).slice(0, 10)) tags.add(t);
    if (c.source_node_id) sources.add(String(c.source_node_id));
    const d = cardDueKey(c);
    if (d) dates.add(d);
    statuses.add(statusKey(c));
    types.add(typeKey(c));
  }
  for (const key of state.selected || []) {
    if (String(key).startsWith('source:')) sources.add(String(key).slice(7));
  }
  return { selected, tags, sources, dates, statuses, types };
}

function cardTagsSet(card) {
  return new Set(tagsList(card?.tags || '').slice(0, 12));
}
function cardHasAnyFocusTag(card, focus = selectedFocusTags()) {
  if (!focus || !focus.size) return false;
  for (const t of tagsList(card?.tags || '')) if (focus.has(t)) return true;
  return false;
}
function sourceHasAnyFocusTag(source, focus = selectedFocusTags()) {
  if (!focus || !focus.size) return false;
  for (const t of tagsList(source?.tags || '')) if (focus.has(t)) return true;
  const sid = String(source?.id || '');
  return (state.graph.cards || []).some(c => String(c.source_node_id || '') === sid && cardHasAnyFocusTag(c, focus));
}
function sharedFocusedTag(a, b, focus = selectedFocusTags()) {
  if (!focus || !focus.size) return '';
  const bTags = cardTagsSet(b);
  for (const t of tagsList(a?.tags || '')) if (focus.has(t) && bTags.has(t)) return t;
  return '';
}
let edgeTooltip = null;
function ensureEdgeTooltip() {
  if (edgeTooltip) return edgeTooltip;
  edgeTooltip = document.createElement('div');
  edgeTooltip.className = 'edge-tooltip hidden';
  document.body.appendChild(edgeTooltip);
  return edgeTooltip;
}
function showEdgeTooltip(label, ev) {
  if (!label) return;
  const tip = ensureEdgeTooltip();
  tip.textContent = label;
  tip.classList.remove('hidden');
  moveEdgeTooltip(ev);
}
function moveEdgeTooltip(ev) {
  if (!edgeTooltip || edgeTooltip.classList.contains('hidden')) return;
  edgeTooltip.style.left = `${Math.min(window.innerWidth - 220, ev.clientX + 14)}px`;
  edgeTooltip.style.top = `${Math.max(8, ev.clientY - 34)}px`;
}
function hideEdgeTooltip() {
  if (edgeTooltip) edgeTooltip.classList.add('hidden');
}
function bindEdgeTooltip(path, label) {
  if (!label) return;
  path.dataset.edgeLabel = label;
  path.addEventListener('mouseenter', ev => showEdgeTooltip(label, ev));
  path.addEventListener('mousemove', moveEdgeTooltip);
  path.addEventListener('mouseleave', hideEdgeTooltip);
}

const savePositionsDebounced = debounce(async () => {
  const nodes = [];
  for (const s of state.graph.sources) nodes.push({ kind:'source', id:s.id, x:Math.round(s.x||0), y:Math.round(s.y||0) });
  for (const c of state.graph.cards) nodes.push({ kind:'card', id:c.id, x:Math.round(c.x||0), y:Math.round(c.y||0) });
  try { await api.savePositions(nodes); } catch (e) { console.warn('positions save failed', e); }
}, 420);

const saveViewDebounced = debounce(() => saveLocalView({ scale:state.scale, offsetX:state.offsetX, offsetY:state.offsetY }), 300);

export function initGraph() {
  canvas = $('#canvas');
  nodesLayer = $('#nodesLayer');
  edgesSvg = $('#edgesSvg');
  workspace = $('#workspace');
  lasso = $('#lasso');
  if (!canvas || !nodesLayer || !edgesSvg || !workspace) {
    console.error('[graph] missing DOM nodes', { canvas, nodesLayer, edgesSvg, workspace });
    return false;
  }
  workspace.addEventListener('wheel', onWheel, { passive:false });
  workspace.addEventListener('pointerdown', onWorkspaceDown);
  window.addEventListener('pointermove', onPointerMove);
  window.addEventListener('pointerup', onPointerUp);
  window.addEventListener('keydown', onKeyDown);
  window.addEventListener('keyup', e => { if (e.code === 'Space' && workspace && !pan) workspace.style.cursor=''; });
  window.addEventListener('contextmenu', onCanvasContextMenu);
  workspace.addEventListener('dblclick', onWorkspaceDoubleClick);
  return true;
}

function isListModeInteraction(e) {
  return state.viewMode === 'list' || !!e.target?.closest?.('.task-view');
}

function isTypingContext(target = null) {
  const el = target || document.activeElement;
  if (!el) return false;
  if (el.isContentEditable) return true;
  const tag = String(el.tagName || '').toUpperCase();
  if (['INPUT','TEXTAREA','SELECT','OPTION'].includes(tag)) return true;
  return Boolean(el.closest?.('input, textarea, select, [contenteditable="true"], #searchInput, #searchResults, #commandInput, .modal, .menu-popover'));
}

function hasActiveCanvasFocus() {
  return Boolean(
    state.selected?.size ||
    state.filterTag ||
    state.hoverTag ||
    state.highlightedDate ||
    state.focusTags?.size ||
    state.focusStatuses?.size ||
    state.focusCardTypes?.size
  );
}

function resetCanvasFocus({ fit = false, clearSearch = false } = {}) {
  state.selected.clear();
  state.highlightedDate = '';
  clearTagFocus();
  setHoverTag('');
  hideContext();
  if (clearSearch) {
    state.search = '';
    const searchInput = $('#searchInput');
    if (searchInput) searchInput.value = '';
    const searchResults = $('#searchResults');
    if (searchResults) { searchResults.classList.add('hidden'); searchResults.innerHTML = ''; }
  }
  if (fit) {
    renderGraph();
    fitView();
  } else {
    renderGraph();
  }
  window.appRenderChrome?.();
  window.appRenderInspector?.();
}

function triggerAutoMarkupLayout() {
  hideContext();
  if (singleSpaceTimer) { clearTimeout(singleSpaceTimer); singleSpaceTimer = null; }
  lastSpaceTapAt = 0;
  if (workspace) workspace.style.cursor = '';
  autoLayoutLocal('sourceCards')
    .then(() => { fitView(); window.appRenderChrome?.(); })
    .catch(err => console.warn('auto layout failed', err));
}

function onKeyDown(e) {
  if (isTypingContext(e.target) || isTypingContext(document.activeElement)) return;
  if (state.viewMode === 'list' && (e.code === 'Space' || e.key === 'Delete' || e.key === 'Backspace')) return;
  if (e.code === 'Space') {
    e.preventDefault();
    if (e.repeat) return;
    const now = performance.now();
    if (now - lastSpaceTapAt <= DOUBLE_SPACE_MS) {
      if (singleSpaceTimer) { clearTimeout(singleSpaceTimer); singleSpaceTimer = null; }
      triggerAutoMarkupLayout();
      return;
    }
    lastSpaceTapAt = now;
    if (singleSpaceTimer) clearTimeout(singleSpaceTimer);
    singleSpaceTimer = setTimeout(() => {
      singleSpaceTimer = null;
      lastSpaceTapAt = 0;
      resetCanvasFocus({ fit: false, clearSearch: true });
    }, DOUBLE_SPACE_MS + 40);
    return;
  }
  if (e.key === 'Escape') { e.preventDefault(); resetCanvasFocus({ fit: true, clearSearch: true }); return; }
  if (e.key === 'Delete' || e.key === 'Backspace') { e.preventDefault(); deleteSelection(); return; }
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase()==='a') { e.preventDefault(); selectAllCards(); return; }
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase()==='k') { e.preventDefault(); window.appOpenCommand?.(); return; }

  const key = String(e.key || '').toLowerCase();
  const panStep = e.shiftKey ? 160 : 72;
  if (e.key === 'ArrowLeft' || key === 'a' || key === 'ф') { e.preventDefault(); panBy(panStep, 0); return; }
  if (e.key === 'ArrowRight' || key === 'd' || key === 'в') { e.preventDefault(); panBy(-panStep, 0); return; }
  if (e.key === 'ArrowUp' || key === 'w' || key === 'ц') { e.preventDefault(); panBy(0, panStep); return; }
  if (e.key === 'ArrowDown' || key === 's' || key === 'ы') { e.preventDefault(); panBy(0, -panStep); return; }
  if (e.key === '+' || e.key === '=' || e.code === 'NumpadAdd') { e.preventDefault(); zoomBy(1.12); return; }
  if (e.key === '-' || e.key === '_' || e.code === 'NumpadSubtract') { e.preventDefault(); zoomBy(.88); return; }
  if (e.key === '0' || e.code === 'Numpad0') { e.preventDefault(); fitView(); return; }
}

export function applyTransform() {
  canvas.style.transform = `translate(${state.offsetX}px, ${state.offsetY}px) scale(${state.scale})`;
  document.body.classList.toggle('zoom-far', state.scale < .52);
  $('#zoomLabel').textContent = `${Math.round(state.scale*100)}%`;
  saveViewDebounced();
  renderEdges();
}

export function fitView() {
  const all = [...state.graph.sources, ...state.graph.cards];
  if (!all.length) { state.scale=.86; state.offsetX=86; state.offsetY=98; applyTransform(); return; }
  let minX=Infinity,minY=Infinity,maxX=-Infinity,maxY=-Infinity;
  for (const n of all) { minX=Math.min(minX,n.x||0); minY=Math.min(minY,n.y||0); maxX=Math.max(maxX,(n.x||0)+340); maxY=Math.max(maxY,(n.y||0)+210); }
  const wr = workspace.getBoundingClientRect();
  const pad = 130;
  const sx = (wr.width - pad*2) / Math.max(400, maxX-minX);
  const sy = (wr.height - pad*2) / Math.max(300, maxY-minY);
  state.scale = clamp(Math.min(sx, sy), .32, 1.05);
  state.offsetX = Math.round((wr.width - (maxX-minX)*state.scale)/2 - minX*state.scale);
  state.offsetY = Math.round((wr.height - (maxY-minY)*state.scale)/2 - minY*state.scale);
  applyTransform();
}

export function zoomBy(factor) {
  const r = workspace.getBoundingClientRect();
  zoomAt(r.left + r.width/2, r.top + r.height/2, factor);
}

function panBy(dx, dy) {
  state.offsetX = Math.round((state.offsetX || 0) + dx);
  state.offsetY = Math.round((state.offsetY || 0) + dy);
  applyTransform();
}
function zoomAt(clientX, clientY, factor) {
  const before = screenToWorld(clientX, clientY);
  state.scale = clamp(state.scale * factor, .22, 2.35);
  const after = screenToWorld(clientX, clientY);
  state.offsetX += (after.x - before.x) * state.scale;
  state.offsetY += (after.y - before.y) * state.scale;
  applyTransform();
}
export function screenToWorld(x,y) {
  const r = workspace.getBoundingClientRect();
  return { x:(x-r.left-state.offsetX)/state.scale, y:(y-r.top-state.offsetY)/state.scale };
}
function worldToScreen(x,y) {
  const r = workspace.getBoundingClientRect();
  return { x:r.left+state.offsetX+x*state.scale, y:r.top+state.offsetY+y*state.scale };
}

export function hydrateMissingPositions() {
  let changed = false;
  const sources = state.graph.sources || [];
  const cards = state.graph.cards || [];
  sources.forEach((src, i) => {
    if (src.x == null || src.y == null) {
      src.x = 80;
      src.y = 140 + i * 240;
      changed = true;
    }
  });
  const placed = [];
  for (const src of sources) {
    let x = Number(src.x || 0), y = Number(src.y || 0);
    let guard = 0;
    while (placed.some(p => Math.abs(p.x - x) < SOURCE_W * 0.72 && Math.abs(p.y - y) < SOURCE_H + 70) && guard < 48) {
      y += SOURCE_H + 90;
      if (guard && guard % 8 === 0) { x += SOURCE_W + 120; y = 140; }
      guard += 1;
    }
    const nx = Math.round(x);
    const ny = Math.round(y);
    if (src.x !== nx || src.y !== ny) changed = true;
    src.x = nx;
    src.y = ny;
    placed.push({ x: src.x, y: src.y });
  }

  const bySource = new Map();
  for (const c of cards) {
    const sid = c.source_node_id || '__orphans';
    if (!bySource.has(sid)) bySource.set(sid, []);
    bySource.get(sid).push(c);
  }
  let orphanRow = 0;
  for (const [sid, list] of bySource.entries()) {
    const src = sources.find(s => String(s.id) === String(sid));
    const baseX = src ? (src.x || 80) + 470 : 540;
    const baseY = src ? (src.y || 140) - 16 : 150 + orphanRow++ * 340;
    const hasMissing = list.some(c => c.x == null || c.y == null);
    list.forEach((c, j) => {
      if (c.x == null || c.y == null) {
        c.x = baseX + (j % 3) * BRICK_X;
        c.y = baseY + Math.floor(j / 3) * BRICK_Y;
        changed = true;
      }
    });
    if (hasMissing) {
      layoutBrickGrid(list, baseX, baseY, Math.min(3, Math.max(1, list.length)));
      changed = true;
    }
  }
  return changed;
}

function matches(kind,obj) {
  const q = state.search.trim().toLowerCase();
  const ft = state.filterTag.trim().toLowerCase();
  if (ft && kind === 'card' && !tagsList(obj.tags).includes(ft)) return false;
  if (ft && kind === 'source') {
    const linked = state.graph.cards.filter(c=>c.source_node_id===obj.id);
    if (!linked.some(c=>tagsList(c.tags).includes(ft))) return false;
  }
  if (!q) return true;
  if (q.startsWith('#')) return kind==='card' && tagsList(obj.tags).some(t=>('#'+t).includes(q));
  const hay = kind==='card' ? `${obj.front} ${obj.back} ${obj.mnemonic} ${obj.tags}` : `${obj.title} ${obj.preview} ${obj.url} ${obj.source_type}`;
  return hay.toLowerCase().includes(q);
}

function modelIconForCard(c) {
  const low = String(c?.model || '').toLowerCase();
  if (c?.model_icon) return c.model_icon;
  if (low.includes('llama') || low.includes('super') || low.includes('e4b')) return '🧠';
  if (low.includes('e2b') || low.includes('gemma')) return '⚡';
  if (low.includes('import')) return '📦';
  return '⚡';
}

function sourceToneClass(s) {
  const type = String(s?.source_type || '').toLowerCase();
  const title = String(s?.title || '').toLowerCase();
  const url = String(s?.url || '').toLowerCase();
  const hasImage = Boolean(s?.image_path || s?.image_url || (Array.isArray(s?.media) && s.media.length));
  if (type.includes('pdf') || title.endsWith('.pdf')) return 'source-pdf';
  if (type.includes('youtube') || url.includes('youtube') || url.includes('youtu.be')) return 'source-video';
  if (type.includes('wiki') || url.includes('wikipedia')) return 'source-wiki';
  if (type.includes('url') || type.includes('web') || /^https?:/.test(url)) return 'source-web';
  if (type.includes('import') || title.includes('import') || title.includes('импорт') || title.includes('anki') || title.includes('quizlet') || title.endsWith('.apkg') || title.endsWith('.csv') || title.endsWith('.tsv')) return 'source-import';
  if (hasImage || type.includes('image')) return 'source-media';
  return 'source-text';
}

export function refreshTagHighlights() {
  const focus = selectedFocusTags();
  const ctx = relationContext();
  const hasCardFocus = ctx.selected.length > 0;
  const statusFocus = selectedFocusStatuses();
  const typeFocus = selectedFocusCardTypes();
  const hasExplicitTagFocus = focus.size > 0;
  const hasMetaFocus = statusFocus.size > 0 || typeFocus.size > 0;
  document.body.classList.toggle('tag-focus-active', hasExplicitTagFocus);
  document.body.classList.toggle('meta-focus-active', hasMetaFocus);
  document.body.classList.toggle('card-selection-active', hasCardFocus);
  $$('.node.card').forEach(el => {
    const c = getNode('card', el.dataset.id);
    const key = nodeKey('card', el.dataset.id);
    const isSelected = state.selected.has(key);
    const tagHit = cardHasAnyFocusTag(c, focus);
    const metaHit = cardHasAnyMetaFocus(c);
    const sameSource = hasCardFocus && c?.source_node_id && ctx.sources.has(String(c.source_node_id));
    const sameDate = hasCardFocus && cardDueKey(c) && ctx.dates.has(cardDueKey(c));
    const focusedShared = hasCardFocus ? ctx.selected.map(base => sharedFocusedTag(base, c, focus)).find(Boolean) : '';

    el.classList.toggle('tag-related', Boolean(tagHit && !isSelected));
    el.classList.toggle('filter-related', Boolean(metaHit && !isSelected));
    el.classList.toggle('selection-neighbor', Boolean(focusedShared && !isSelected));
    el.classList.toggle('selection-source-neighbor', Boolean(sameSource && !focusedShared && !isSelected));
    el.classList.toggle('selection-date-neighbor', Boolean(sameDate && !focusedShared && !sameSource && !isSelected));

    if (focusedShared) {
      el.style.setProperty('--relation-color', edgeColor(focusedShared, 96, 58, .98));
      el.dataset.relationTag = focusedShared;
    } else {
      el.style.removeProperty('--relation-color');
      delete el.dataset.relationTag;
    }
  });
  $$('.node.source').forEach(el => {
    const sid = String(el.dataset.id || '');
    const s = getNode('source', sid);
    const sourceTagHit = sourceHasAnyFocusTag(s, focus);
    const sourceMetaHit = sourceHasAnyMetaFocus(s);
    const relatedSource = hasCardFocus && ctx.sources.has(sid) && !state.selected.has(nodeKey('source', sid));
    el.classList.toggle('selection-source-neighbor', Boolean(relatedSource));
    el.classList.toggle('tag-related', Boolean(sourceTagHit && !state.selected.has(nodeKey('source', sid))));
    el.classList.toggle('filter-related', Boolean(sourceMetaHit && !state.selected.has(nodeKey('source', sid))));
  });
  $$('.tag[data-tag]').forEach(el => {
    const t = String(el.dataset.tag || '').replace(/^#+/,'').trim().toLowerCase();
    el.classList.toggle('active', Boolean(t && (focus.has(t) || state.filterTag === t)));
  });
  $$('[data-status]').forEach(el => {
    const st = String(el.dataset.status || '').trim().toLowerCase();
    el.classList.toggle('active', Boolean(st && statusFocus.has(st)));
  });
  $$('[data-card-type]').forEach(el => {
    const tp = String(el.dataset.cardType || '').trim().toLowerCase();
    el.classList.toggle('active', Boolean(tp && typeFocus.has(tp)));
  });
}


export function renderGraph() {
  if (!nodesLayer || !edgesSvg || !workspace) {
    canvas = canvas || $('#canvas');
    nodesLayer = nodesLayer || $('#nodesLayer');
    edgesSvg = edgesSvg || $('#edgesSvg');
    workspace = workspace || $('#workspace');
    lasso = lasso || $('#lasso');
  }
  if (!nodesLayer) {
    console.error('[graph] nodesLayer missing; renderGraph skipped');
    return;
  }
  const layoutChanged = hydrateMissingPositions();
  document.body.classList.toggle('hide-answers', state.prefs.hideAnswers);
  document.body.classList.toggle('tag-focus-active', selectedFocusTags().size > 0);
  nodesLayer.innerHTML = '';
  for (const s of state.graph.sources) nodesLayer.appendChild(renderSourceNode(s, !matches('source',s)));
  for (const c of state.graph.cards) nodesLayer.appendChild(renderCardNode(c, !matches('card',c)));
  const emptyState = $('#emptyState');
  if (emptyState) emptyState.classList.toggle('hidden', Boolean(state.currentDeckId && (state.graph.sources.length || state.graph.cards.length)));
  updateSelectionToast();
  renderEdges();
  refreshTagHighlights();
  renderInspector();
  if (layoutChanged) savePositionsDebounced();
}

function bindTagEvents(el){
  const stop = e => { e.preventDefault(); e.stopPropagation(); };
  el.addEventListener('pointerdown', stop);
  el.addEventListener('mousedown', stop);
  el.addEventListener('mouseenter', e => { setHoverTag(el.dataset.tag || el.textContent); refreshTagHighlights(); renderEdges(); });
  el.addEventListener('mouseleave', e => { setHoverTag(''); refreshTagHighlights(); renderEdges(); });
  el.addEventListener('click', e => {
    e.preventDefault();
    e.stopPropagation();
    selectByTag(el.dataset.tag || el.textContent);
    window.appRenderChrome?.();
    renderGraph();
  });
}
function bindFocusChipEvents(el){
  const stop = e => { e.preventDefault(); e.stopPropagation(); };
  el.addEventListener('pointerdown', stop);
  el.addEventListener('mousedown', stop);
  el.addEventListener('mouseenter', e => {
    const label = el.dataset.focusLabel || el.textContent || '';
    if (label) showEdgeTooltip(label, e);
  });
  el.addEventListener('mousemove', moveEdgeTooltip);
  el.addEventListener('mouseleave', hideEdgeTooltip);
  el.addEventListener('click', e => {
    e.preventDefault();
    e.stopPropagation();
    if (el.dataset.status) selectByStatus(el.dataset.status);
    if (el.dataset.cardType) selectByCardType(el.dataset.cardType);
    window.appRenderChrome?.();
    renderGraph();
  });
}

function renderSourceNode(s, dimmed) {
  const key = nodeKey('source', s.id);
  const focus=selectedFocusTags();
  const allSourceTags = tagsList(s.tags || '');
  const linkedCardsForSource = (state.graph.cards || []).filter(card => String(card.source_node_id || '') === String(s.id));
  const relatedByTag = Boolean(focus.size && (allSourceTags.some(t => focus.has(t)) || linkedCardsForSource.some(card => tagsList(card.tags).some(t => focus.has(t)))));
  const relatedByMeta = sourceHasAnyMetaFocus(s);
  const el = document.createElement('div');
  el.className = `node source ${sourceToneClass(s)} ${state.selected.has(key)?'selected':''} ${relatedByTag&&!state.selected.has(key)?'tag-related':''} ${relatedByMeta&&!state.selected.has(key)?'filter-related':''} ${dimmed?'dimmed':''} ${state.prefs.compact?'compact':''}`;
  el.dataset.kind='source'; el.dataset.id=s.id; el.style.left=`${s.x||0}px`; el.style.top=`${s.y||0}px`; if(s.color) el.style.setProperty('--node-accent', s.color);
  const linkedCount = state.graph.cards.filter(c=>c.source_node_id===s.id).length;
  const sourceTags = allSourceTags.slice(0,5).map(t=>`<span class="tag source-tag ${focus.has(t)?'active':''}" data-tag="${esc(t)}">#${esc(displayTag(t))}</span>`).join('');
  const sourceImage = (s.image_url||s.image_path) ? `<img class="source-node-image" src="${esc(s.image_url||s.image_path)}" alt="">` : '';
  el.innerHTML = `<div class="node-head"><span class="node-icon">${esc(s.icon || iconForSource(s.source_type))}</span><div class="node-title">${esc(s.title||'Источник')}</div></div><div class="node-body">${sourceImage}<div class="preview">${esc(s.preview||'')}</div><div class="tags"><span class="tag">${linkedCount} карт.</span><span class="tag">${esc(sourceTypeLabel(s.source_type||'text'))}</span>${sourceTags}</div></div><div class="node-foot"><span>Источник</span><span>${esc(s.created_at ? new Date(s.created_at).toLocaleDateString('ru-RU') : '')}</span></div>`;
  bindNodeEvents(el);
  bindResizeHandle(el);
  el.querySelectorAll('[data-tag]').forEach(bindTagEvents);
  return el;
}

function renderCardNode(c, dimmed) {
  const key = nodeKey('card', c.id);
  const status = statusKey(c);
  const statusName = statusDisplayLabel(c);
  const model = (c.model||'').includes('llama')?'LLAMA':(c.model||'').toLowerCase().includes('super') || (c.model||'').toLowerCase().includes('e4b')?'E4B':'E2B';
  const modelIcon = modelIconForCard(c);
  const cardTags = tagsList(c.tags).slice(0,9);
  const focus = selectedFocusTags();
  const statusFocus = selectedFocusStatuses();
  const typeFocus = selectedFocusCardTypes();
  const type = typeKey(c);
  const relatedByTag = Boolean(focus.size && cardTags.some(t => focus.has(t)));
  const relatedByMeta = Boolean((statusFocus.size && statusFocus.has(status)) || (typeFocus.size && typeFocus.has(type)));
  const highlightedByDate = Boolean(state.highlightedDate && (localYMD(c.due_date) === state.highlightedDate || (state.highlightedDate === localYMD(new Date()) && status === 'today' && !c.due_date)));
  const tagHtml = cardTags.map(t=>`<span class="tag ${focus.has(t)?'active':''}" data-tag="${esc(t)}">#${esc(displayTag(t))}</span>`).join('');
  const imageHtml = c.image_path ? `<img class="card-node-image" src="${esc(c.image_path)}" alt="">` : '';
  const flipped = Boolean(c.__flipped);
  const title = flipped ? 'Ответ' : (c.front || 'Без вопроса');
  const main = flipped ? (c.back || '') : (c.front || '');
  const secondary = flipped ? `Вопрос: ${c.front || ''}` : (c.back || '');
  const el = document.createElement('div');
  el.className = `node card status-${status} ${state.selected.has(key)?'selected':''} ${relatedByTag&&!state.selected.has(key)?'tag-related':''} ${relatedByMeta&&!state.selected.has(key)?'filter-related':''} ${highlightedByDate?'date-highlight':''} ${dimmed?'dimmed':''} ${state.prefs.compact?'compact':''} ${flipped?'flipped':''}`;
  el.dataset.kind='card'; el.dataset.id=c.id; el.style.left=`${c.x||0}px`; el.style.top=`${c.y||0}px`;
  const savedSize = getCardSize(c.id);
  if(savedSize){ el.style.width = `${savedSize.w}px`; el.style.height = `${savedSize.h}px`; el.classList.add('custom-size'); }
  const typeLabel = cardTypeLabel(type);
  const statusActive = statusFocus.has(status) ? 'active' : '';
  const typeActive = typeFocus.has(type) ? 'active' : '';
  const statusTip = statusTooltipLabel(c);
  const typeTip = `Тип: ${typeLabel}`;
  const statusIcon = statusEmoji(c);
  const typeIcon = typeEmoji(type);
  el.innerHTML = `<div class="node-head"><span class="node-icon">${esc(modelIcon)}</span><div class="node-title">${esc(title)}</div></div><div class="node-body">${imageHtml}<div class="question">${esc(main)}</div><div class="answer">${esc(secondary)}</div>${tagHtml?`<div class="tags">${tagHtml}</div>`:''}</div><div class="node-foot"><span class="node-chip status-chip status-strip ${statusActive}" data-status="${esc(status)}" data-focus-label="${esc(statusTip)}" title="${esc(statusTip)}"><span class="chip-emoji">${esc(statusIcon)}</span><span class="chip-text">${esc(statusName)}</span></span><span class="node-chip type-chip type-strip ${typeActive}" data-card-type="${esc(type)}" data-focus-label="${esc(typeTip)}" title="${esc(typeTip)}"><span class="chip-emoji">${esc(typeIcon)}</span><span class="chip-text">${esc(typeLabel)}</span><span class="node-model-mini">${model}</span></span></div><span class="node-resize" title="Потяни, чтобы расширить карточку"></span>`;
  bindNodeEvents(el);
  bindResizeHandle(el);
  el.querySelectorAll('[data-tag]').forEach(bindTagEvents);
  el.querySelectorAll('[data-status],[data-card-type]').forEach(bindFocusChipEvents);
  return el;
}

function bindResizeHandle(el) {
  const h = el.querySelector('.node-resize');
  if(!h) return;
  h.addEventListener('pointerdown', e => {
    e.preventDefault(); e.stopPropagation();
    const startX = e.clientX, startY = e.clientY;
    const rect = el.getBoundingClientRect();
    const startW = rect.width, startH = rect.height;
    const id = el.dataset.id;
    h.setPointerCapture?.(e.pointerId);
    const move = ev => {
      const w = Math.max(280, Math.min(760, startW + ev.clientX - startX));
      const hgt = Math.max(230, Math.min(760, startH + ev.clientY - startY));
      el.style.width = `${w}px`;
      el.style.height = `${hgt}px`;
      el.classList.add('custom-size');
      renderEdges();
    };
    const up = ev => {
      window.removeEventListener('pointermove', move, true);
      window.removeEventListener('pointerup', up, true);
      const r = el.getBoundingClientRect();
      saveCardSize(id, r.width, r.height);
    };
    window.addEventListener('pointermove', move, true);
    window.addEventListener('pointerup', up, true);
  });
}

function bindNodeEvents(el) {
  el.addEventListener('pointerdown', e => {
    if (e.button !== 0) return;
    e.stopPropagation();
    hideContext();
    const key = nodeKey(el.dataset.kind, el.dataset.id);
    if (e.altKey && el.dataset.kind === 'card') { flipCards(state.selected.has(key) ? selectedCards() : [el.dataset.id]); return; }
    const alreadySelected = state.selected.has(key);
    if (e.shiftKey) {
      state.inspectDeckId=null;
      if (alreadySelected) state.selected.delete(key); else state.selected.add(key);
      window.appOpenInspector?.(); renderGraph(); return;
    }
    if (!alreadySelected || state.selected.size !== 1) {
      state.selected.clear();
      state.inspectDeckId=null;
      state.selected.add(key);
      window.appOpenInspector?.();
      renderGraph();
    } else {
      state.inspectDeckId=null;
      window.appOpenInspector?.();
      renderInspector();
    }
    const start = screenToWorld(e.clientX, e.clientY);
    const selectedNodes = [...state.selected].map(k => { const [kind,id]=splitKey(k); const n=getNode(kind,id); return n ? { kind,id,node:n,x:n.x||0,y:n.y||0 } : null; }).filter(Boolean);
    drag = { pointerId:e.pointerId, start, selectedNodes, moved:false };
    el.setPointerCapture?.(e.pointerId);
  });
  el.addEventListener('dblclick', e => { e.stopPropagation(); if(el.dataset.kind==='card') openCardEditor(getNode('card', el.dataset.id)); else showSourceInfo(el.dataset.id, getNode('source', el.dataset.id)); });
  el.addEventListener('contextmenu', e => { e.preventDefault(); e.stopPropagation(); const key=nodeKey(el.dataset.kind, el.dataset.id); if(!state.selected.has(key)){ state.selected.clear(); state.inspectDeckId=null; state.selected.add(key); renderGraph(); } showNodeMenu(e.clientX,e.clientY,el.dataset.kind,el.dataset.id); });
}

export function renderEdges() {
  if (!edgesSvg) return;
  edgeFragment = document.createDocumentFragment();
  const focus = selectedFocusTags();
  const statusFocus = selectedFocusStatuses();
  const typeFocus = selectedFocusCardTypes();
  const metaActive = statusFocus.size > 0 || typeFocus.size > 0;
  const sourceById = new Map(state.graph.sources.map(s=>[String(s.id),s]));
  for (const c of state.graph.cards) {
    const s = sourceById.get(String(c.source_node_id));
    if(!s) continue;
    const selectedEdge = state.selected.has(nodeKey('source',s.id)) || state.selected.has(nodeKey('card',c.id));
    const focusEdge = focus.size && cardHasAnyFocusTag(c, focus);
    const metaEdge = metaActive && cardHasAnyMetaFocus(c);
    if (focus.size && !focusEdge && !selectedEdge) continue;
    if (!focus.size && metaActive && !metaEdge && !selectedEdge) continue;
    const edgeCls = focusEdge ? 'edge-tag-scope' : (metaEdge ? 'edge-filter-scope' : '');
    drawBezier(
      (s.x||0)+SOURCE_W,
      (s.y||0)+84,
      (c.x||0),
      (c.y||0)+Math.min(120, cardH(c)/2),
      `edge-source ${edgeCls}`,
      selectedEdge || Boolean(focusEdge) || Boolean(metaEdge),
      focusEdge ? firstFocusedTagColor(c, focus) : (metaEdge ? edgeColor(firstMetaFocusKey(c), 86, 58, .90) : (s.color || edgeColor(s.id))),
      focusEdge ? `Тег: #${displayTag(firstFocusedTag(c, focus))}` : (metaEdge ? firstMetaFocusLabel(c) : `Источник: ${s.title || 'источник'}`)
    );
  }
  if (state.prefs.tagEdges) {
    drawSelectedCardTagEdges();
    if (focus.size) drawTagEdges();
  }
  edgesSvg.replaceChildren(edgeFragment);
  edgeFragment = null;
}

function firstFocusedTag(card, focus = selectedFocusTags()) {
  for (const t of tagsList(card?.tags || '')) if (focus.has(t)) return t;
  return '';
}
function firstFocusedTagColor(card, focus = selectedFocusTags()) {
  const t = firstFocusedTag(card, focus);
  return t ? edgeColor(t, 96, 58, .98) : '';
}

function firstMetaFocusKey(card) {
  const sf = selectedFocusStatuses();
  const tf = selectedFocusCardTypes();
  if (sf.has(statusKey(card))) return `status:${statusKey(card)}`;
  if (tf.has(typeKey(card))) return `type:${typeKey(card)}`;
  return 'meta';
}
function firstMetaFocusLabel(card) {
  const sf = selectedFocusStatuses();
  const tf = selectedFocusCardTypes();
  if (sf.has(statusKey(card))) return `Статус: ${statusDisplayLabel(card)}`;
  if (tf.has(typeKey(card))) return `Тип: ${cardTypeLabel(typeKey(card))}`;
  return 'Фильтр';
}

function drawSelectedCardTagEdges() {
  const focus = selectedFocusTags();
  if (!focus.size) return;
  const ids = new Set(selectedCards().map(String));
  if (!ids.size) return;
  const selected = state.graph.cards.filter(c => ids.has(String(c.id)));
  if (!selected.length) return;
  const drawn = new Set();
  let count = 0;
  for (const base of selected) {
    const baseTags = tagsList(base.tags).filter(t => focus.has(t)).slice(0, 8);
    if (!baseTags.length) continue;
    const baseSet = new Set(baseTags);
    for (const other of state.graph.cards) {
      if (String(other.id) === String(base.id)) continue;
      const shared = tagsList(other.tags).find(t => focus.has(t) && baseSet.has(t));
      if (!shared) continue;
      const pair = [String(base.id), String(other.id), shared].sort().join(':');
      if (drawn.has(pair)) continue;
      drawn.add(pair);
      drawBezier(
        (base.x || 0) + cardW(base) / 2,
        (base.y || 0) + cardH(base) / 2,
        (other.x || 0) + cardW(other) / 2,
        (other.y || 0) + cardH(other) / 2,
        'edge-card-tag edge-tag-focused edge-relation-tag',
        true,
        edgeColor(shared, 96, 58, .98),
        `Тег: #${displayTag(shared)}`
      );
      count += 1;
      if (count > 70) return;
    }
  }
}

function drawTagEdges() {
  const focusedTags = selectedFocusTags();
  if (!focusedTags.size) return;
  const tagMap = new Map();
  for (const c of state.graph.cards) {
    for (const t of tagsList(c.tags).slice(0,7)) {
      if (!focusedTags.has(t)) continue;
      if(!tagMap.has(t)) tagMap.set(t,[]);
      tagMap.get(t).push(c);
    }
  }
  for (const [tag, list] of tagMap.entries()) {
    if (list.length < 2 || list.length > 40) continue;
    const ordered = [...list].sort((a,b)=>(a.y||0)-(b.y||0) || (a.x||0)-(b.x||0));
    for (let i=0;i<ordered.length-1;i++) {
      drawBezier((ordered[i].x||0)+cardW(ordered[i])/2,(ordered[i].y||0)+cardH(ordered[i]),(ordered[i+1].x||0)+cardW(ordered[i+1])/2,(ordered[i+1].y||0), 'edge-tag edge-tag-focused', true, edgeColor(tag, 96, 57, .98), `Тег: #${displayTag(tag)}`);
    }
  }
}
function drawBezier(x1,y1,x2,y2,cls,selected,color='',label='') {
  const path=document.createElementNS('http://www.w3.org/2000/svg','path');
  const dx=Math.max(90, Math.abs(x2-x1)*.42);
  const d=`M ${x1} ${y1} C ${x1+dx} ${y1}, ${x2-dx} ${y2}, ${x2} ${y2}`;
  path.setAttribute('d', d);
  path.setAttribute('fill','none');
  path.setAttribute('class', `${cls}${selected?' edge-selected':''}`);
  if(color) path.style.setProperty('stroke', color, 'important');
  (edgeFragment || edgesSvg).appendChild(path);

  if(label) {
    const hitbox=document.createElementNS('http://www.w3.org/2000/svg','path');
    hitbox.setAttribute('d', d);
    hitbox.setAttribute('fill','none');
    hitbox.setAttribute('class','edge-hitbox');
    bindEdgeTooltip(hitbox, label);
    (edgeFragment || edgesSvg).appendChild(hitbox);
  }
}

function onWorkspaceDoubleClick(e) {
  if (isListModeInteraction(e) || e.target.closest('.node') || e.target.closest('.commandbar') || e.target.closest('.canvas-hud') || e.target.closest('.context-menu') || e.target.closest('.menu-popover')) return;
  const p = screenToWorld(e.clientX, e.clientY);
  openSourceModalAt(p.x, p.y);
}
function onWheel(e) { if (isListModeInteraction(e)) return; e.preventDefault(); zoomAt(e.clientX,e.clientY,e.deltaY<0?1.08:.92); }
function onWorkspaceDown(e) {
  if (isListModeInteraction(e) || e.button === 2 || e.target.closest('.node') || e.target.closest('.commandbar') || e.target.closest('.canvas-hud')) return;
  hideContext();
  if (e.button === 1 || (e.button === 0 && (e.altKey || e.shiftKey))) {
    e.preventDefault();
    pan = { x:e.clientX, y:e.clientY, ox:state.offsetX, oy:state.offsetY, pointerId:e.pointerId };
    workspace.style.cursor = 'grabbing';
    workspace.setPointerCapture?.(e.pointerId);
    return;
  }
  state.selected.clear();
  state.inspectDeckId = null;
  window.appCloseInspector?.();
  lassoStart={x:e.clientX,y:e.clientY}; lasso.classList.remove('hidden'); placeLasso(e.clientX,e.clientY); renderGraph();
}
function onPointerMove(e) {
  if (pan) { state.offsetX=pan.ox+(e.clientX-pan.x); state.offsetY=pan.oy+(e.clientY-pan.y); applyTransform(); return; }
  if (drag) {
    const p=screenToWorld(e.clientX,e.clientY); const dx=p.x-drag.start.x, dy=p.y-drag.start.y; if(Math.abs(dx)+Math.abs(dy)>1) drag.moved=true;
    for (const item of drag.selectedNodes) { item.node.x=Math.round(item.x+dx); item.node.y=Math.round(item.y+dy); const el=document.querySelector(`.node[data-kind="${item.kind}"][data-id="${CSS.escape(String(item.id))}"]`); if(el){ el.style.left=`${item.node.x}px`; el.style.top=`${item.node.y}px`; el.classList.add('dragging'); } }
    renderEdges(); return;
  }
  if (lassoStart) placeLasso(e.clientX,e.clientY);
}
function onPointerUp() {
  if (pan) { try { workspace.releasePointerCapture?.(pan.pointerId); } catch {} pan=null; workspace.style.cursor=''; }
  if (drag) { $$('.node.dragging').forEach(n=>n.classList.remove('dragging')); if(drag.moved) savePositionsDebounced(); drag=null; renderInspector(); }
  if (lassoStart) { selectByLasso(); lassoStart=null; lasso.classList.add('hidden'); renderGraph(); }
}
function placeLasso(x,y) { const x1=Math.min(lassoStart.x,x), y1=Math.min(lassoStart.y,y), x2=Math.max(lassoStart.x,x), y2=Math.max(lassoStart.y,y); Object.assign(lasso.style,{left:`${x1}px`,top:`${y1}px`,width:`${x2-x1}px`,height:`${y2-y1}px`}); }
function selectByLasso() { const r=lasso.getBoundingClientRect(); $$('.node').forEach(el=>{ const nr=el.getBoundingClientRect(); const hit=!(nr.right<r.left||nr.left>r.right||nr.bottom<r.top||nr.top>r.bottom); if(hit) state.selected.add(nodeKey(el.dataset.kind,el.dataset.id)); }); }
function selectAllCards() { state.inspectDeckId=null; state.selected = new Set(state.graph.cards.map(c=>nodeKey('card',c.id))); window.appOpenInspector?.(); renderGraph(); }
function updateSelectionToast() { const el=$('#selectionToast'); const n=state.selected.size; if(!n){el.classList.add('hidden');return;} el.textContent = n===1 ? '1 объект выбран' : `${n} объектов выбрано`; el.classList.remove('hidden'); }

function flipCards(ids = []) {
  const unique = [...new Set(ids.map(String))];
  const targets = unique.length ? unique : selectedCards().map(String);
  const cardIds = targets.length ? targets : [];
  for (const id of cardIds) {
    const card = getNode('card', id);
    if (card) card.__flipped = !card.__flipped;
  }
  if (cardIds.length) renderGraph();
}

export async function deleteSelection() {
  if(!state.selected.size) return;
  const cards=[], sources=[];
  for(const k of state.selected){ const [kind,id]=splitKey(k); if(kind==='card') cards.push(String(id)); else sources.push(String(id)); }
  const linkedCardIds = new Set(cards.map(String));
  for (const sid of sources) {
    for (const c of state.graph.cards) if (String(c.source_node_id || '') === String(sid)) linkedCardIds.add(String(c.id));
  }
  const totalCards = linkedCardIds.size;
  const msg = sources.length ? `Удалить ${sources.length} источн. и ${totalCards} связанн. карточ.?` : `Удалить ${totalCards} карточ.?`;
  if(!confirm(msg)) return;
  try {
    await api.deleteNodes([...linkedCardIds], sources);
    state.selected.clear(); state.inspectDeckId=null; clearTagFocus();
    await window.appReloadGraph();
    await window.appRenderChrome?.();
  } catch (e) {
    alert('Не удалось удалить: ' + (e?.message || e));
  }
}

export async function autoLayoutLocal(mode='sourceCards') {
  if (mode === 'backend') { await api.autoLayout(state.currentDeckId); await window.appReloadGraph(); fitView(); return; }
  const sources = state.graph.sources;
  const cards = state.graph.cards;
  if (!sources.length && !cards.length) return;
  if (mode === 'reviewTimeline') {
    const today = new Date();
    today.setHours(0,0,0,0);
    const parse = value => {
      if (!value) return null;
      const m = String(value).match(/^(\d{4})-(\d{2})-(\d{2})/);
      const d = m ? new Date(Number(m[1]), Number(m[2])-1, Number(m[3])) : new Date(value);
      if (Number.isNaN(d.getTime())) return null;
      d.setHours(0,0,0,0);
      return d;
    };
    const bucketOf = card => {
      if ((card.status || '') === 'done') return 'Готово';
      const due = parse(card.due_date);
      if ((card.status || '') === 'today' && !due) return 'Сегодня';
      if (!due) return 'Входящие';
      const diff = Math.round((due - today) / 86400000);
      if (diff < 0) return 'Просрочено';
      if (diff === 0) return 'Сегодня';
      if (diff <= 7) return 'Ближайшие дни';
      return 'Позже';
    };
    const order = ['Просрочено','Сегодня','Ближайшие дни','Позже','Входящие','Готово'];
    const groups = new Map(order.map(name => [name, []]));
    cards.forEach(c => groups.get(bucketOf(c))?.push(c));
    sources.forEach((s,i)=>{ s.x=80; s.y=120+i*210; });
    order.forEach((name, col) => {
      const list = groups.get(name) || [];
      const x = 500 + col * 360;
      list.forEach((c, i) => { c.x = x; c.y = 130 + i * BRICK_Y; });
    });
  } else if (mode === 'modelGroups') {
    const groups = new Map();
    cards.forEach(c=>{
      const key = (c.model_kind || '').includes('quality') ? '🧠 SuperGemma' : (c.model_kind || '').includes('fast') ? '⚡ Gemma' : (c.model_kind || '').includes('import') ? '📦 Импорт' : '📚 Прочее';
      if(!groups.has(key)) groups.set(key, []); groups.get(key).push(c);
    });
    let x=500;
    for (const [_name,list] of groups.entries()) { x += layoutSideGrid(list, x, 130); }
    sources.forEach((s,i)=>{ s.x=70; s.y=120+i*230; });
  } else if (mode === 'cardTypeGroups') {
    const labels = Object.fromEntries(['basic','definition','fact','concept','cloze','true_false','mcq'].map(t => [t, cardTypeLabel(t)]));
    const groups = new Map();
    cards.forEach(c=>{
      const key = labels[c.card_type || 'basic'] || cardTypeLabel('basic');
      if(!groups.has(key)) groups.set(key, []);
      groups.get(key).push(c);
    });
    let x = 500;
    for (const [_name, list] of groups.entries()) {
      x += layoutSideGrid(list, x, 130);
    }
    sources.forEach((s,i)=>{ s.x=70; s.y=120+i*230; });
  } else {
    let sourceY = 140;
    sources.forEach((s,i)=>{
      s.x=80; s.y=sourceY;
      const linked=cards.filter(c=>String(c.source_node_id||'')===String(s.id));
      layoutBrickGrid(linked, 510, s.y-20, 3, BRICK_X, BRICK_Y);
      const rows = Math.max(1, Math.ceil(linked.length / 3));
      sourceY += Math.max(360, rows * BRICK_Y + 120);
    });
    layoutBrickGrid(cards.filter(c=>!c.source_node_id), 510, sourceY + 40, 3, BRICK_X, BRICK_Y);
  }
  renderGraph(); savePositionsDebounced(); fitView();
}

export function focusObject(kind, id) {
  const node = getNode(kind, id);
  if (!node || !workspace) return;
  const w = kind === 'card' ? cardW(node) : SOURCE_W;
  const h = kind === 'card' ? cardH(node) : SOURCE_H;
  const r = workspace.getBoundingClientRect();
  state.scale = clamp(state.scale || .86, .35, 1.15);
  state.offsetX = Math.round(r.width / 2 - (Number(node.x || 0) + w / 2) * state.scale);
  state.offsetY = Math.round(r.height / 2 - (Number(node.y || 0) + h / 2) * state.scale);
  applyTransform();
}

function menuCheck(value){ return value ? '✓' : ''; }
function closeSidePanelsForCanvasContext() {
  const app = $('#app');
  if (app && !app.classList.contains('sidebar-closed')) $('#closeSidebarBtn')?.click();
  window.appCloseInspector?.();
}
async function runCanvasAction(action, x, y) {
  if(action==='add') { const p=screenToWorld(x,y); openSourceModalAt(p.x,p.y); }
  if(action==='add-card') { const p=screenToWorld(x,y); openCardCreator({ x:Math.round(p.x), y:Math.round(p.y) }); }
  if(action==='fit') fitView();
  if(action==='layout' || action==='sourceCards') await autoLayoutLocal('sourceCards');
  if(action==='reviewTimeline') await autoLayoutLocal('reviewTimeline');
  if(action==='modelGroups') await autoLayoutLocal('modelGroups');
  if(action==='cardTypeGroups') await autoLayoutLocal('cardTypeGroups');
  if(action==='study') window.appOpenStudy?.();
  if(action==='view-list') window.appSetWorkspaceMode?.('list');
  if(action==='view-canvas') window.appSetWorkspaceMode?.('canvas');
  if(action==='toggle-view') window.appToggleWorkspaceMode?.();
  if(action==='dateFilter') {
    if (state.highlightedDate) { state.highlightedDate=''; state.selected.clear(); renderGraph(); window.appRenderChrome?.(); }
    else await window.appSelectCardsByDatePrompt?.();
  }
  if(action==='export') window.appOpenExportAt?.(x, y);
  if(action==='tagEdges' || action==='compact' || action==='hideAnswers') {
    state.prefs[action] = !state.prefs[action];
    window.appSavePrefs?.();
    window.appRenderChrome?.();
  }
}
async function deleteCardsFromSource(sourceId) {
  const count = state.graph.cards.filter(c => String(c.source_node_id || '') === String(sourceId)).length;
  if (!count) { alert('У этого источника нет карточек.'); return; }
  if (!confirm(`Удалить ${count} карточек этого источника? Сам источник останется.`)) return;
  await api.deleteSourceCards(sourceId);
  state.selected.clear();
  state.selected.add(nodeKey('source', sourceId));
  await window.appReloadGraph?.();
}

function viewContextHtml(includeAdd=false) {
  return `
    <div class="ctx-block ctx-main">
      ${includeAdd ? '<button data-bg="add">＋ Добавить источник здесь</button><button data-bg="add-card">＋ Создать карточку вручную</button>' : ''}
      <button data-bg="study">▶ Режим повторения</button>
    </div>
    <div class="ctx-block ctx-layout">
      <button data-bg="fit">⛶ Сбросить вид</button>
      <button data-bg="layout">⌘ Авто-разметка</button>
    </div>
    <div class="ctx-block ctx-study">
      <button data-bg="sourceCards">Источник → карточки</button>
      <button data-bg="reviewTimeline">План повторения по датам</button>
      <button data-bg="modelGroups">Группировка по модели</button>
      <button data-bg="cardTypeGroups">Группировка по типу карточки</button>
      <button data-bg="dateFilter">${state.highlightedDate ? 'Снять подсветку даты' : 'Показать выбранную дату…'}</button>
    </div>
    <div class="ctx-block ctx-view">
      <button data-bg="view-list">✅ TickTick-вид <span>${menuCheck(state.viewMode === 'list')}</span></button>
      <button data-bg="tagEdges">Связи по тегам <span>${menuCheck(state.prefs.tagEdges)}</span></button>
      <button data-bg="compact">Компактные карточки <span>${menuCheck(state.prefs.compact)}</span></button>
      <button data-bg="hideAnswers">Скрывать ответы <span>${menuCheck(state.prefs.hideAnswers)}</span></button>
      <button data-bg="export">⇧ Экспорт</button>
    </div>`;
}


function showNodeMenu(x,y,kind,id) {
  const menu=$('#contextMenu');
  if(kind==='source') menu.innerHTML=`
    <div class="ctx-block ctx-main">
      <button data-act="quick-generate">⚡ Генерировать карточки…</button>
      <button data-act="create-card">＋ Создать карточку вручную</button>
      <button data-act="layout">⌘ Разложить вокруг источника</button>
    </div>
    <div class="ctx-block ctx-view">
      <button data-act="edit-source">✏️ Редактировать текст и теги</button>
    </div>
    <div class="ctx-block ctx-export">
      <button class="export-action" data-act="export">⇧ Экспорт источника</button>
      <button data-act="study-source">▶ Повторить карточки источника</button>
      <button data-act="delete-cards">🧹 Удалить карточки источника</button>
    </div>
    <div class="ctx-block ctx-danger">
      <button class="danger solid-danger" data-act="delete">🗑️ Удалить источник и карточки</button>
    </div>`;
  else menu.innerHTML=`<div class="ctx-block ctx-main"><button data-act="edit">✏️ Редактировать</button><button data-act="flip">↻ Показать ответ / вопрос</button><button data-act="review">▶ Повторить сейчас</button></div><div class="ctx-block ctx-view"><button data-act="expand">↔️ Расширить карточку</button><button data-act="reset-size">Сбросить размер</button><button data-act="plan">📅 Запланировать на завтра</button><button data-act="done">✅ Отметить готово</button></div><div class="ctx-block ctx-export"><button class="export-action" data-act="export">⇧ Экспорт карточек</button></div><div class="ctx-block ctx-danger"><button class="danger" data-act="delete">🗑️ Удалить карточку</button></div>`;
  menu.style.left=`${Math.min(window.innerWidth-170, Math.max(8,x))}px`; menu.style.top=`${Math.min(window.innerHeight-260, Math.max(8,y))}px`; menu.classList.remove('hidden');
  menu.onclick=async e=>{ const btn=e.target?.closest?.('[data-act]'); const act=btn?.dataset?.act; if(!act) return; hideContext();
    if(kind==='source'){
      if(act==='open') await showSourceInfo(id, getNode('source', id));
      if(act==='quick-generate') window.appOpenGenerateMenuAt?.(id, x, y);
      if(act==='create-card') openCardCreator({ sourceId:id });
      if(act==='edit-source'){ state.selected.clear(); state.selected.add(nodeKey('source', id)); window.appOpenInspector?.(); renderGraph(); setTimeout(()=>window.appToggleSourceInline?.(id, true), 60); }
      if(act==='study-source') window.appOpenStudy?.({mode:'all', source_id:id});
      if(act==='layout') await autoLayoutLocal('sourceCards');
      if(act==='export') window.appOpenExportAt?.(x, y);
      if(act==='delete-cards') await deleteCardsFromSource(id);
      if(act==='delete') await deleteSelection();
    } else {
      const card=getNode('card',id);
      if(act==='edit') openCardEditor(card);
      if(act==='review') window.appOpenStudy?.();
      if(act==='flip') flipCards(state.selected.has(nodeKey('card', id)) ? selectedCards() : [id]);
      if(act==='expand'){ const el=document.querySelector(`.node.card[data-id=\"${CSS.escape(String(id))}\"]`); if(el){ const wide = (el.getBoundingClientRect().width < 430); const w=wide?520:316, h=wide?420:262; el.style.width=`${w}px`; el.style.height=`${h}px`; el.classList.add('custom-size'); saveCardSize(id,w,h); renderEdges(); } }
      if(act==='reset-size'){ removeCardSize(id); renderGraph(); }
      if(act==='plan'){ const d=new Date(); d.setDate(d.getDate()+1); await api.updateCard(id,{status:'planned', due_date:window.appLocalYMD ? window.appLocalYMD(d) : d.toLocaleDateString('en-CA')}); await window.appReloadGraph(); }
      if(act==='done'){ const ids = selectedCards().length ? selectedCards() : [id]; for (const cid of ids) await api.updateCard(cid,{status:'done'}); await window.appReloadGraph(); }
      if(act==='export') window.appOpenExportAt?.(x, y);
      if(act==='delete') await deleteSelection();
    }
  };
}
function onCanvasContextMenu(e) {
  if (isListModeInteraction(e) || e.target.closest('.node') || e.target.closest('.menu-popover') || e.target.closest('.modal') || e.target.closest('.commandbar') || e.target.closest('.canvas-hud') || e.target.closest('.sidebar') || e.target.closest('.inspector')) return;
  e.preventDefault();
  closeSidePanelsForCanvasContext();
  const menu=$('#contextMenu');
  menu.innerHTML=viewContextHtml(true);
  menu.style.left=`${Math.min(window.innerWidth-170, Math.max(8,e.clientX))}px`; menu.style.top=`${Math.min(window.innerHeight-300, Math.max(8,e.clientY))}px`; menu.classList.remove('hidden');
  menu.onclick=async ev=>{ const btn=ev.target?.closest?.('[data-bg]'); const a=btn?.dataset?.bg; if(!a) return; hideContext(); await runCanvasAction(a, e.clientX, e.clientY); };
}
export function hideContext(){ $('#contextMenu')?.classList.add('hidden'); }
window.addEventListener('click', e => { if(!e.target.closest('#contextMenu')) hideContext(); });
export function savePrefs(){ saveLocalPrefs(state.prefs); window.appSetTheme?.(state.prefs.theme); document.body.classList.toggle('hide-answers', state.prefs.hideAnswers);
  document.body.classList.toggle('tag-focus-active', selectedFocusTags().size > 0); renderGraph(); }
