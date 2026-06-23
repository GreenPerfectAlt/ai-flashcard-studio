import { api } from './api.js?v=162';
import { state, clearTagFocus, nodeKey } from './store.js?v=162';
import { $, $$, debounce, localYMD, setLocalDeckId, esc } from './utils.js?v=162';
import { initGraph, applyTransform, fitView, zoomBy, deleteSelection, autoLayoutLocal, savePrefs, renderGraph, focusObject } from './graph.js?v=162';
import { loadDecks, reloadGraph, renderChrome, addDeck, saveSourceFromModal, uploadFileAsSource, saveCardEditor, importFile, exportDeck, openSourceModalAt, generateFromSource, showSourceInfo, openCommand, renderCommands, runCommand, openGenerateMenuAt, bindStudyMode, openStudyMode, toggleInlineSource, openCardCreator, toggleWorkspaceMode, setWorkspaceMode, renderInspector } from './ui.js?v=162';
import { loadGenDefaults } from './gen_settings.js?v=162';


function applyTheme(theme) {
  const t = theme || localStorage.getItem('theme') || 'light';
  document.body.dataset.theme = t;
  try { localStorage.setItem('theme', t); state.prefs.theme = t; } catch(e) {}
  const themeSelect = $('#themeSelect');
  if (themeSelect && themeSelect.value !== t) themeSelect.value = t;
}

const DEFAULT_DECK_ACCENT = '#8f2942';
function deckAccentKey(deckId = state.currentDeckId) { return `deckAccent:${deckId || 'default'}`; }
function normalizeHexColor(value) {
  let v = String(value || '').trim();
  if (/^#[0-9a-f]{3}$/i.test(v)) v = '#' + [...v.slice(1)].map(ch => ch + ch).join('');
  return /^#[0-9a-f]{6}$/i.test(v) ? v.toLowerCase() : DEFAULT_DECK_ACCENT;
}
function hexToRgb(hex) {
  const v = normalizeHexColor(hex).slice(1);
  return { r: parseInt(v.slice(0, 2), 16), g: parseInt(v.slice(2, 4), 16), b: parseInt(v.slice(4, 6), 16) };
}
function rgbToHex({r,g,b}) {
  const clamp = n => Math.max(0, Math.min(255, Math.round(n)));
  return '#' + [clamp(r), clamp(g), clamp(b)].map(n => n.toString(16).padStart(2, '0')).join('');
}
function rgbToHsv({r,g,b}) {
  r /= 255; g /= 255; b /= 255;
  const max = Math.max(r,g,b), min = Math.min(r,g,b);
  const d = max - min;
  let h = 0;
  if (d) {
    if (max === r) h = ((g - b) / d) % 6;
    else if (max === g) h = (b - r) / d + 2;
    else h = (r - g) / d + 4;
    h *= 60;
    if (h < 0) h += 360;
  }
  return { h: Math.round(h), s: max ? Math.round((d / max) * 100) : 0, v: Math.round(max * 100) };
}
function hsvToRgb({h,s,v}) {
  h = ((Number(h) || 0) % 360 + 360) % 360;
  s = Math.max(0, Math.min(100, Number(s) || 0)) / 100;
  v = Math.max(0, Math.min(100, Number(v) || 0)) / 100;
  const c = v * s;
  const x = c * (1 - Math.abs((h / 60) % 2 - 1));
  const m = v - c;
  let r=0,g=0,b=0;
  if (h < 60) [r,g,b] = [c,x,0];
  else if (h < 120) [r,g,b] = [x,c,0];
  else if (h < 180) [r,g,b] = [0,c,x];
  else if (h < 240) [r,g,b] = [0,x,c];
  else if (h < 300) [r,g,b] = [x,0,c];
  else [r,g,b] = [c,0,x];
  return { r:(r+m)*255, g:(g+m)*255, b:(b+m)*255 };
}
function mixRgb(a, b, amount = .5) {
  return { r: a.r + (b.r - a.r) * amount, g: a.g + (b.g - a.g) * amount, b: a.b + (b.b - a.b) * amount };
}
function luminance({r,g,b}) {
  const srgb = [r,g,b].map(v => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); });
  return 0.2126 * srgb[0] + 0.7152 * srgb[1] + 0.0722 * srgb[2];
}
function getDeckAccent(deckId = state.currentDeckId) {
  try { return normalizeHexColor(localStorage.getItem(deckAccentKey(deckId)) || DEFAULT_DECK_ACCENT); }
  catch { return DEFAULT_DECK_ACCENT; }
}
function buildDeckPalette(accent) {
  const c = hexToRgb(accent);
  const white = {r:255,g:255,b:255};
  const cream = {r:247,g:231,b:207};
  const amber = {r:202,g:167,b:123};
  const ink = {r:37,g:25,b:19};
  const black = {r:8,g:8,b:12};
  const luma = luminance(c);
  const textOnAccent = luma > 0.48 ? '#17110d' : '#ffffff';
  return {
    '--deck-accent': accent,
    '--deck-accent-rgb': `${c.r} ${c.g} ${c.b}`,
    '--deck-on-accent': textOnAccent,
    '--deck-canvas-a': rgbToHex(mixRgb(c, white, .54)),
    '--deck-canvas-b': rgbToHex(mixRgb(c, amber, .72)),
    '--deck-canvas-c': rgbToHex(mixRgb(c, black, .46)),
    '--deck-panel': rgbToHex(mixRgb(c, cream, .78)),
    '--deck-panel-strong': rgbToHex(mixRgb(c, cream, .64)),
    '--deck-node': rgbToHex(mixRgb(c, white, .82)),
    '--deck-node-head': rgbToHex(mixRgb(c, white, .70)),
    '--deck-chip': rgbToHex(mixRgb(c, white, .76)),
    '--deck-text-strong': rgbToHex(mixRgb(c, ink, .80)),
    '--deck-text-soft': rgbToHex(mixRgb(c, ink, .60)),
    '--deck-line': `rgba(${c.r},${c.g},${c.b},.30)`,
    '--deck-line-strong': `rgba(${c.r},${c.g},${c.b},.46)`,
    '--deck-grid': `rgba(${c.r},${c.g},${c.b},.16)`,
    '--deck-glow': `rgba(${c.r},${c.g},${c.b},.28)`,
    '--deck-dark-panel': rgbToHex(mixRgb(c, black, .34)),
    '--deck-dark-node': rgbToHex(mixRgb(c, {r:18,g:20,b:28}, .30)),
    '--deck-dark-head': rgbToHex(mixRgb(c, {r:26,g:29,b:40}, .28)),
    '--deck-dark-chip': rgbToHex(mixRgb(c, {r:30,g:34,b:46}, .32))
  };
}
function setSafeColorControlValue(el, color) {
  if (!el) return;
  const accent = normalizeHexColor(color);
  if ('value' in el) el.value = accent;
  el.dataset.value = accent;
  el.style.setProperty('--picked-color', accent);
  const preview = el.querySelector?.('.safe-color-preview');
  if (preview) preview.style.background = accent;
}
function safeColorControlValue(el) {
  return normalizeHexColor(el?.dataset?.value || el?.value || DEFAULT_DECK_ACCENT);
}
function closeSafeColorPalette() {
  document.getElementById('safeColorPalette')?.remove();
}
function openSafeColorPalette(anchor, current, onPick) {
  closeSafeColorPalette();
  const palette = document.createElement('div');
  palette.id = 'safeColorPalette';
  palette.className = 'menu-popover safe-color-palette native-free-color-picker';
  const now = normalizeHexColor(current);
  const hsv = rgbToHsv(hexToRgb(now));
  palette.innerHTML = `<div class="safe-color-head"><b>Цвет</b><button type="button" class="tiny-btn" data-close title="Закрыть">×</button></div>
    <div class="safe-color-preview-row"><span class="safe-color-big" style="--picked-color:${now}"></span><code>${now}</code></div>
    <label class="safe-color-slider"><span>Тон</span><input type="range" min="0" max="360" value="${hsv.h}" data-hue></label>
    <label class="safe-color-slider"><span>Насыщ.</span><input type="range" min="0" max="100" value="${hsv.s}" data-sat></label>
    <label class="safe-color-slider"><span>Яркость</span><input type="range" min="0" max="100" value="${hsv.v}" data-val></label>
    <label class="safe-color-hex"><span>HEX</span><input type="text" maxlength="7" value="${now}" spellcheck="false"></label>
    <button type="button" class="primary" data-apply>Применить</button>`;
  document.body.appendChild(palette);
  const rect = anchor.getBoundingClientRect();
  palette.style.left = `${Math.max(12, Math.min(window.innerWidth - 270, rect.left))}px`;
  palette.style.top = `${Math.max(12, Math.min(window.innerHeight - 285, rect.bottom + 8))}px`;
  const hue = palette.querySelector('[data-hue]');
  const sat = palette.querySelector('[data-sat]');
  const val = palette.querySelector('[data-val]');
  const input = palette.querySelector('.safe-color-hex input');
  const preview = palette.querySelector('.safe-color-big');
  const code = palette.querySelector('code');
  const syncFromSliders = () => {
    const hex = rgbToHex(hsvToRgb({ h:hue.value, s:sat.value, v:val.value }));
    input.value = hex;
    preview.style.setProperty('--picked-color', hex);
    code.textContent = hex;
  };
  const syncFromHex = () => {
    const raw = String(input.value || '').trim();
    if (!/^#?[0-9a-f]{3}([0-9a-f]{3})?$/i.test(raw)) return;
    const hex = normalizeHexColor(raw.startsWith('#') ? raw : '#' + raw);
    const next = rgbToHsv(hexToRgb(hex));
    hue.value = next.h; sat.value = next.s; val.value = next.v;
    preview.style.setProperty('--picked-color', hex);
    code.textContent = hex;
  };
  const apply = () => { syncFromHex(); onPick(normalizeHexColor(input.value)); closeSafeColorPalette(); };
  [hue, sat, val].forEach(el => el.addEventListener('input', syncFromSliders));
  input.addEventListener('input', syncFromHex);
  input.addEventListener('keydown', e => { if (e.key === 'Enter') apply(); if (e.key === 'Escape') closeSafeColorPalette(); });
  palette.querySelector('[data-apply]').onclick = apply;
  palette.querySelector('[data-close]').onclick = closeSafeColorPalette;
  setTimeout(() => window.addEventListener('pointerdown', function close(e){ if(!e.target.closest('#safeColorPalette') && e.target !== anchor && !anchor.contains(e.target)){ closeSafeColorPalette(); window.removeEventListener('pointerdown', close, true); } }, true), 0);
}

function applyDeckTheme(color = null) {
  const accent = normalizeHexColor(color || getDeckAccent());
  const root = document.documentElement;
  for (const [name, value] of Object.entries(buildDeckPalette(accent))) root.style.setProperty(name, value);
  const input = $('#deckThemeColor');
  setSafeColorControlValue(input, accent);
  const hexInput = $('#deckThemeHex');
  if (hexInput && hexInput.value.toLowerCase() !== accent.toLowerCase()) hexInput.value = accent;
}
function saveDeckThemeColor(color) {
  const accent = normalizeHexColor(color);
  try { if (state.currentDeckId) localStorage.setItem(deckAccentKey(), accent); } catch(e) {}
  applyDeckTheme(accent);
}
function resetDeckThemeColor() {
  try { if (state.currentDeckId) localStorage.removeItem(deckAccentKey()); } catch(e) {}
  applyDeckTheme(DEFAULT_DECK_ACCENT);
}

function bindImageHoverPreview() {
  const box = $('#imageHoverPreview');
  const img = box?.querySelector('img');
  if (!box || !img) return;
  const selector = '.source-node-image, .card-node-image, .card-image-preview, .media-thumb img';
  let active = null;
  const place = e => {
    const pad = 18;
    const rect = box.getBoundingClientRect();
    let left = e.clientX + 22;
    let top = e.clientY + 22;
    if (left + rect.width > window.innerWidth - pad) left = e.clientX - rect.width - 22;
    if (top + rect.height > window.innerHeight - pad) top = e.clientY - rect.height - 22;
    box.style.left = `${Math.max(pad, left)}px`;
    box.style.top = `${Math.max(pad, top)}px`;
  };
  document.addEventListener('mouseover', e => {
    const target = e.target?.closest?.(selector);
    if (!target) return;
    active = target;
    img.src = target.currentSrc || target.src;
    box.classList.remove('hidden');
    requestAnimationFrame(() => place(e));
  }, true);
  document.addEventListener('mousemove', e => { if (active) place(e); }, true);
  document.addEventListener('mouseout', e => {
    const target = e.target?.closest?.(selector);
    if (!target || target !== active) return;
    active = null;
    box.classList.add('hidden');
  }, true);
  window.addEventListener('blur', () => { active = null; box.classList.add('hidden'); });
}

function bindPanelChrome() {
  const app = $('#app');
  const closeSide = $('#closeSidebarBtn');
  const openSide = $('#openSidebarBtn');
  const closeInspector = $('#closeInspectorBtn');
  const openInspector = $('#openInspectorBtn');
  const setSidebar = closed => {
    app.classList.toggle('sidebar-closed', closed);
    try { localStorage.setItem('sidebarClosed', closed ? '1' : '0'); } catch(e) {}
  };
  const setInspector = closed => {
    app.classList.toggle('inspector-closed', closed);
    openInspector?.classList.toggle('hidden', !closed);
    try { localStorage.setItem('inspectorClosed', closed ? '1' : '0'); } catch(e) {}
  };
  closeSide && (closeSide.onclick = () => setSidebar(true));
  openSide && (openSide.onclick = () => setSidebar(false));
  closeInspector && (closeInspector.onclick = () => setInspector(true));
  openInspector && (openInspector.onclick = () => setInspector(false));
  try {
    if(localStorage.getItem('sidebarClosed') === '1') setSidebar(true);
    if(localStorage.getItem('inspectorClosed') === '1') setInspector(true);
  } catch(e) {}
}

function openInspectorPanel() {
  const app = $('#app');
  app?.classList.remove('inspector-closed');
  $('#openInspectorBtn')?.classList.add('hidden');
  try { localStorage.setItem('inspectorClosed', '0'); } catch(e) {}
}

function closeInspectorPanel() {
  const app = $('#app');
  app?.classList.add('inspector-closed');
  try { localStorage.setItem('inspectorClosed', '1'); } catch(e) {}
}

function bindGlobalUI() {
  $$('[data-action="add-source"]').forEach(btn => btn.onclick = () => openSourceModalAt());
  $('#addDeckBtn').onclick = addDeck;
  const emptyImportBtn = $('#emptyImportBtn');
  if (emptyImportBtn) emptyImportBtn.onclick = () => $('#importModal').classList.add('active');
  $('#saveSourceBtn').onclick = saveSourceFromModal;
  $('#saveCardBtn').onclick = saveCardEditor;
  $('#uploadFileBtn').onclick = () => $('#hiddenFileInput').click();
  $('#hiddenFileInput').onchange = e => uploadFileAsSource(e.target.files?.[0]);
  $('#chooseImportBtn').onclick = () => $('#hiddenImportInput').click();
  const importCardsBtn = $('#importCardsBtn');
  if (importCardsBtn) importCardsBtn.onclick = () => $('#hiddenImportInput').click();
  $('#hiddenImportInput').onchange = e => importFile(e.target.files?.[0]);
  $('#fitBtn').onclick = fitView;
  $('#layoutBtn').onclick = () => autoLayoutLocal('sourceCards');

  const viewModeBtn = $('#viewModeBtn');
  if (viewModeBtn) {
    viewModeBtn.onclick = e => {
      e.preventDefault();
      e.stopPropagation();
      toggleWorkspaceMode();
    };
  }
  $('#zoomOutBtn').onclick = () => zoomBy(.86);
  $('#zoomInBtn').onclick = () => zoomBy(1.16);
  const themeSelect = $('#themeSelect');
  if (themeSelect) {
    themeSelect.value = state.prefs.theme || localStorage.getItem('theme') || 'light';
    themeSelect.onchange = () => { applyTheme(themeSelect.value); window.appSavePrefs?.(); };
  }
  const deckColor = $('#deckThemeColor');
  const deckHex = $('#deckThemeHex');
  if (deckColor) {
    setSafeColorControlValue(deckColor, getDeckAccent());
    deckColor.addEventListener('click', (e) => {
      e.preventDefault();
      e.stopPropagation();
      openSafeColorPalette(deckColor, safeColorControlValue(deckColor), saveDeckThemeColor);
    });
  }
  if (deckHex) {
    deckHex.value = getDeckAccent();
    deckHex.addEventListener('input', () => {
      const raw = String(deckHex.value || '').trim();
      if (/^#[0-9a-f]{3}$/i.test(raw) || /^#[0-9a-f]{6}$/i.test(raw)) saveDeckThemeColor(raw);
    });
    deckHex.addEventListener('change', () => {
      const raw = String(deckHex.value || '').trim();
      if (/^#?[0-9a-f]{3}$/i.test(raw) || /^#?[0-9a-f]{6}$/i.test(raw)) saveDeckThemeColor(raw.startsWith('#') ? raw : '#' + raw);
      else applyDeckTheme();
    });
  }
  $('#deckThemeResetBtn')?.addEventListener('click', resetDeckThemeColor);
  bindImageHoverPreview();
  $('#clearFilterBtn').onclick = () => { clearTagFocus(); state.search=''; state.selected.clear(); $('#searchInput').value=''; renderChrome(); window.appRenderGraph?.(); };
  $('#commandBtn').onclick = openCommand;
  const search = $('#searchInput');
  search.addEventListener('input', debounce(async () => {
    state.search=search.value;
    window.appRenderGraph?.();
    await renderGlobalSearch(search.value);
  }, 160));
  search.addEventListener('keydown', async e => {
    if(e.key==='Enter') {
      const first = $('#searchResults [data-search-kind]');
      if(first) { e.preventDefault(); first.click(); }
    }
    if(e.key==='Escape') $('#searchResults')?.classList.add('hidden');
  });

  $$('[data-close]').forEach(btn => btn.onclick = () => $('#'+btn.dataset.close).classList.remove('active'));
  $$('.modal').forEach(m => m.addEventListener('pointerdown', e => { if(e.target === m) m.classList.remove('active'); }));

  const exportBtn = $('#exportBtn'); if (exportBtn) exportBtn.onclick = e => openExportAt(e.currentTarget);
  $('#exportMenu').onclick = e => { const f=e.target?.dataset?.export; if(f){ hidePopovers(); exportDeck(f); } };
  window.addEventListener('pointerdown', e => { if(!e.target.closest('.menu-popover') && !e.target.closest('#exportBtn') && !e.target.closest('#searchResults') && !e.target.closest('#searchInput')) { hidePopovers(); $('#searchResults')?.classList.add('hidden'); } });

  const sourceDrop = $('#sourceDropZone');
  for (const target of [sourceDrop, $('#workspace')]) {
    target.addEventListener('dragover', e => { e.preventDefault(); sourceDrop.classList.add('drag-over'); });
    target.addEventListener('dragleave', e => { if(e.target === target) sourceDrop.classList.remove('drag-over'); });
    target.addEventListener('drop', async e => {
      e.preventDefault(); sourceDrop.classList.remove('drag-over');
      const file = e.dataTransfer?.files?.[0];
      if (file) await uploadFileAsSource(file);
    });
  }
  $('#pasteClipboardBtn').onclick = async () => {
    try { $('#sourceText').value = await navigator.clipboard.readText(); }
    catch { alert('Браузер не дал доступ к буферу. Вставь Ctrl+V вручную.'); }
  };

  $('#commandInput').addEventListener('input', e => renderCommands(e.target.value));
  $('#commandInput').addEventListener('keydown', e => {
    if(e.key==='Enter') { const first=$('#commandList .command-item'); if(first) runCommand(first.dataset.cmd); }
    if(e.key==='Escape') $('#commandModal').classList.remove('active');
  });

  window.addEventListener('keydown', e => {
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase()==='k') { e.preventDefault(); openCommand(); }
  });
}


async function selectCardsByDatePrompt() {
  const value = prompt('Дата повторения в формате ГГГГ-ММ-ДД', state.highlightedDate || localYMD(new Date()));
  if (!value) return;
  const target = localYMD(value);
  if (!target) return;
  const alreadyActive = state.highlightedDate === target;
  state.selected.clear();
  state.inspectDeckId = null;
  if (alreadyActive) {
    state.highlightedDate = '';
    renderGraph();
    window.appRenderChrome?.();
    return;
  }
  state.highlightedDate = target;
  for (const c of state.graph.cards || []) {
    if (localYMD(c.due_date) === target || (target === localYMD(new Date()) && c.status === 'today' && !c.due_date)) {
      state.selected.add(nodeKey('card', c.id));
    }
  }
  await autoLayoutLocal('reviewTimeline');
  window.appOpenInspector?.();
  window.appRenderChrome?.();
}

async function renderGlobalSearch(q) {
  const box = $('#searchResults');
  if(!box) return;
  const query = String(q || '').trim();
  if(query.length < 2) { box.classList.add('hidden'); box.innerHTML=''; return; }
  try {
    const data = await api.search(query);
    const rows = data.results || [];
    if(!rows.length) { box.innerHTML = '<div class="search-empty">Ничего не найдено</div>'; box.classList.remove('hidden'); return; }
    box.innerHTML = rows.slice(0,14).map(r=>`<button data-search-kind="${esc(r.kind)}" data-deck="${esc(r.deck_id)}" data-id="${esc(r.id)}"><b>${r.kind==='deck'?'Колода':r.kind==='source'?'Источник':'Карточка'}</b><span>${esc(r.title||'')}</span><small>${esc(String(r.subtitle||'').slice(0,120))}</small></button>`).join('');
    box.classList.remove('hidden');
    box.querySelectorAll('[data-search-kind]').forEach(btn=>btn.onclick=async()=>{
      const deckId = Number(btn.dataset.deck);
      if(deckId && deckId !== state.currentDeckId) { state.currentDeckId = deckId; setLocalDeckId(deckId); state.selected.clear(); state.inspectDeckId=null; await loadDecks(); await reloadGraph(); }
      const kind = btn.dataset.searchKind;
      const id = btn.dataset.id;
      state.selected.clear(); state.inspectDeckId = null;
      if(kind === 'deck') state.inspectDeckId = Number(id);
      if(kind === 'source') state.selected.add(`source:${id}`);
      if(kind === 'card') state.selected.add(`card:${id}`);
      window.appOpenInspector?.(); window.appRenderGraph?.(); renderInspector(); window.appRenderChrome?.();
      if(kind === 'source' || kind === 'card') {
        setTimeout(() => focusObject(kind, id), 40);
      }
      box.classList.add('hidden');
    });
  } catch(e) { console.warn('global search failed', e); }
}

function togglePopover(sel, anchor) {
  const pop=$(sel);
  const isHidden=pop.classList.contains('hidden');
  hidePopovers();
  if (!isHidden) return;
  const r=anchor.getBoundingClientRect();
  pop.style.left=`${r.left}px`; pop.style.top=`${r.bottom+8}px`; pop.classList.remove('hidden');
}
function openExportAt(anchorOrX, y=null) {
  hidePopovers();
  window.appRenderChrome?.();
  const pop=$('#exportMenu');
  pop.classList.remove('hidden');
  pop.style.visibility='hidden';
  pop.style.left='0px';
  pop.style.top='0px';
  const rect = pop.getBoundingClientRect();
  const w = rect.width || 270;
  const h = rect.height || 250;
  let left, top;
  if (!anchorOrX) {
    left = (window.innerWidth - w) / 2;
    top = Math.max(72, (window.innerHeight - h) / 2);
  } else if (typeof anchorOrX === 'number') {
    left = anchorOrX;
    top = (y ?? 12) + 8;
    if (top + h > window.innerHeight - 12) top = (y ?? 12) - h - 8;
  } else {
    const r=anchorOrX.getBoundingClientRect();
    left = r.right - w;
    top = r.bottom + 8;
    if (top + h > window.innerHeight - 12) top = r.top - h - 8;
  }
  pop.style.left=`${Math.max(12, Math.min(window.innerWidth-w-12, left))}px`;
  pop.style.top=`${Math.max(12, Math.min(window.innerHeight-h-12, top))}px`;
  pop.style.visibility='visible';
}
function hidePopovers(){ $$('.menu-popover').forEach(p=>p.classList.add('hidden')); }

async function boot() {
  applyTheme(localStorage.getItem('theme') || 'light');
  try {
    const cfg = await api.config();
    if (cfg && cfg.generation) state.config.generation = { ...state.config.generation, ...cfg.generation };
  } catch (e) { console.warn('config load failed', e); }
  // Pre-fetch env-derived defaults so the UI panel can fall back to them on
  // first load (before the user moves any slider).
  try { await loadGenDefaults(); } catch (e) { console.warn('gen defaults load failed', e); }
  bindGlobalUI();
  bindStudyMode();
  bindPanelChrome();
  initGraph();
  await loadDecks();
  applyDeckTheme();
  await reloadGraph();
  applyTransform();
}

window.appReloadGraph = reloadGraph;
window.appRenderGraph = renderGraph;
window.appRenderChrome = renderChrome;
window.appDeleteSelection = deleteSelection;
window.appAutoLayout = () => autoLayoutLocal('sourceCards');
window.appOpenExport = (anchor=null) => openExportAt(anchor || $('#exportBtn') || null);
window.appOpenExportAt = (x,y) => openExportAt(x,y);
window.appOpenInspector = openInspectorPanel;
window.appCloseInspector = closeInspectorPanel;
window.appFocusSource = id => { state.selected.clear(); state.selected.add(`source:${id}`); openInspectorPanel(); renderGraph(); setTimeout(()=>{ focusObject('source', id); document.querySelector('[data-gen]')?.scrollIntoView({block:'center', behavior:'smooth'}); }, 60); };
window.appFocusCard = id => { state.selected.clear(); state.selected.add(`card:${id}`); openInspectorPanel(); renderGraph(); setTimeout(()=>focusObject('card', id), 60); };
window.appGenerateFromSource = generateFromSource;
window.appOpenGenerateMenuAt = openGenerateMenuAt;
window.appShowSourceInfo = showSourceInfo;
window.appOpenCommand = openCommand;
window.appOpenStudy = openStudyMode;
window.appSavePrefs = savePrefs;
window.appLocalYMD = localYMD;
window.appSetTheme = applyTheme;
window.appApplyDeckTheme = applyDeckTheme;
window.appFocusObject = focusObject;
window.appSelectCardsByDatePrompt = selectCardsByDatePrompt;
window.appToggleSourceInline = toggleInlineSource;
window.appOpenCardCreator = openCardCreator;
window.appSetWorkspaceMode = setWorkspaceMode;
window.appToggleWorkspaceMode = toggleWorkspaceMode;
window.appAddDeck = addDeck;

boot().catch(err => { console.error(err); alert('Ошибка запуска интерфейса: ' + err.message); });
