import { api, download, exportUrl } from './api.js?v=162';
import { state, nodeKey, splitKey, getNode, selectedCards, selectedSources, allTags, selectedObjects, selectByTag, selectedFocusTags, selectedFocusStatuses, selectedFocusCardTypes, selectByStatus, selectByCardType, setHoverTag, rebuildIndexes } from './store.js?v=162';
import { $, $$, esc, tagsList, displayTag, isProbablyUrl, isYoutubeUrl, localDeckId, setLocalDeckId, iconForSource, localYMD, parseLocalDate, cardTypeLabel as labelCardType, sourceTypeLabel, cardTypeOptionsHtml, tagsInputValue, normalizeTagsForSave } from './utils.js?v=162';
import { renderGraph, renderEdges, refreshTagHighlights, fitView, autoLayoutLocal, focusObject } from './graph.js?v=162';
import { getGenSettings, setGenSettings, loadGenDefaults, buildGenPayload, genSettingsPanelHtml, bindGenSettingsPanel } from './gen_settings.js?v=162';

let pendingSourcePosition = null;
let currentSourceInfoId = null;
let pendingCreateCard = null;


function normalizeUiHexColor(value, fallback = '#d97706') {
  let v = String(value || '').trim();
  if (!v.startsWith('#') && /^([0-9a-f]{3}|[0-9a-f]{6})$/i.test(v)) v = '#' + v;
  if (/^#[0-9a-f]{3}$/i.test(v)) v = '#' + [...v.slice(1)].map(ch => ch + ch).join('');
  return /^#[0-9a-f]{6}$/i.test(v) ? v.toLowerCase() : fallback;
}
function uiHexToRgb(hex) {
  const v = normalizeUiHexColor(hex).slice(1);
  return { r: parseInt(v.slice(0,2),16), g: parseInt(v.slice(2,4),16), b: parseInt(v.slice(4,6),16) };
}
function uiRgbToHex({r,g,b}) {
  const clamp = n => Math.max(0, Math.min(255, Math.round(n)));
  return '#' + [clamp(r), clamp(g), clamp(b)].map(n => n.toString(16).padStart(2,'0')).join('');
}
function uiRgbToHsv({r,g,b}) {
  r/=255; g/=255; b/=255;
  const max=Math.max(r,g,b), min=Math.min(r,g,b), d=max-min;
  let h=0;
  if(d){ if(max===r) h=((g-b)/d)%6; else if(max===g) h=(b-r)/d+2; else h=(r-g)/d+4; h*=60; if(h<0) h+=360; }
  return { h:Math.round(h), s:max?Math.round(d/max*100):0, v:Math.round(max*100) };
}
function uiHsvToRgb({h,s,v}) {
  h=((Number(h)||0)%360+360)%360; s=Math.max(0,Math.min(100,Number(s)||0))/100; v=Math.max(0,Math.min(100,Number(v)||0))/100;
  const c=v*s, x=c*(1-Math.abs((h/60)%2-1)), m=v-c;
  let r=0,g=0,b=0;
  if(h<60)[r,g,b]=[c,x,0]; else if(h<120)[r,g,b]=[x,c,0]; else if(h<180)[r,g,b]=[0,c,x]; else if(h<240)[r,g,b]=[0,x,c]; else if(h<300)[r,g,b]=[x,0,c]; else [r,g,b]=[c,0,x];
  return {r:(r+m)*255,g:(g+m)*255,b:(b+m)*255};
}
function closeUiColorPicker(){ document.getElementById('uiColorPicker')?.remove(); }
function openUiColorPicker(anchor, current, onPick){
  closeUiColorPicker();
  const now=normalizeUiHexColor(current);
  const hsv=uiRgbToHsv(uiHexToRgb(now));
  const pop=document.createElement('div');
  pop.id='uiColorPicker';
  pop.className='menu-popover safe-color-palette native-free-color-picker';
  pop.innerHTML=`<div class="safe-color-head"><b>Цвет</b><button type="button" class="tiny-btn" data-close title="Закрыть">×</button></div>
    <div class="safe-color-preview-row"><span class="safe-color-big" style="--picked-color:${now}"></span><code>${now}</code></div>
    <label class="safe-color-slider"><span>Тон</span><input type="range" min="0" max="360" value="${hsv.h}" data-hue></label>
    <label class="safe-color-slider"><span>Насыщ.</span><input type="range" min="0" max="100" value="${hsv.s}" data-sat></label>
    <label class="safe-color-slider"><span>Яркость</span><input type="range" min="0" max="100" value="${hsv.v}" data-val></label>
    <label class="safe-color-hex"><span>HEX</span><input type="text" maxlength="7" value="${now}" spellcheck="false"></label>
    <button type="button" class="primary" data-apply>Применить</button>`;
  document.body.appendChild(pop);
  const rect=anchor.getBoundingClientRect();
  pop.style.left=`${Math.max(12,Math.min(window.innerWidth-270,rect.left))}px`;
  pop.style.top=`${Math.max(12,Math.min(window.innerHeight-285,rect.bottom+8))}px`;
  const hue=pop.querySelector('[data-hue]'), sat=pop.querySelector('[data-sat]'), val=pop.querySelector('[data-val]');
  const input=pop.querySelector('.safe-color-hex input'), preview=pop.querySelector('.safe-color-big'), code=pop.querySelector('code');
  const syncSliders=()=>{ const hex=uiRgbToHex(uiHsvToRgb({h:hue.value,s:sat.value,v:val.value})); input.value=hex; preview.style.setProperty('--picked-color',hex); code.textContent=hex; };
  const syncHex=()=>{ const raw=String(input.value||'').trim(); if(!/^#?[0-9a-f]{3}([0-9a-f]{3})?$/i.test(raw))return; const hex=normalizeUiHexColor(raw.startsWith('#')?raw:'#'+raw); const next=uiRgbToHsv(uiHexToRgb(hex)); hue.value=next.h; sat.value=next.s; val.value=next.v; preview.style.setProperty('--picked-color',hex); code.textContent=hex; };
  const apply=()=>{ syncHex(); onPick(normalizeUiHexColor(input.value)); closeUiColorPicker(); };
  [hue,sat,val].forEach(el=>el.addEventListener('input',syncSliders));
  input.addEventListener('input',syncHex);
  input.addEventListener('keydown',e=>{ if(e.key==='Enter')apply(); if(e.key==='Escape')closeUiColorPicker(); });
  pop.querySelector('[data-apply]').onclick=apply;
  pop.querySelector('[data-close]').onclick=closeUiColorPicker;
  setTimeout(()=>window.addEventListener('pointerdown',function close(e){ if(!e.target.closest('#uiColorPicker') && e.target!==anchor && !anchor.contains(e.target)){ closeUiColorPicker(); window.removeEventListener('pointerdown',close,true); } },true),0);
}
function isoDate(value){ return value ? localYMD(value) : ''; }
function dateAfter(days){ const d=new Date(); d.setHours(0,0,0,0); d.setDate(d.getDate()+days); return localYMD(d); }
function dateLabel(value){
  if(!value) return 'без даты';
  try { return parseLocalDate(value).toLocaleDateString('ru-RU', {day:'2-digit', month:'2-digit'}); } catch { return String(value); }
}

function statusLabelForFocus(card){
  const status = String(card?.status || 'inbox').toLowerCase();
  if (card?.due_date) return dateLabel(card.due_date);
  return { inbox:'Входящие', today:'Сегодня', planned:'План', done:'Готово' }[status] || 'Входящие';
}
export function todayLocalYMD(){ return localYMD(new Date()); }

function generationModelButtonHtml(modelName, attr='data-gen') {
  const meta = generationModelMeta(modelName);
  const safeName = esc(modelName);
  return `<button class="secondary model-btn" ${attr}="${safeName}"><span><strong>${esc(meta.icon)} ${esc(meta.label)}</strong><em>${esc(meta.short)}</em></span><small>${esc(meta.short)}</small></button>`;
}
function generationModelButtonsHtml(attr='data-gen') {
  const models = Array.isArray(state.availableModels) ? state.availableModels : [];
  if (!models.length) return '<button class="secondary model-btn" disabled><span><strong>⚠️ Модели не найдены</strong><em>положи .litertlm в models/</em></span><small>no model</small></button>';
  return models.map(name => generationModelButtonHtml(name, attr)).join('');
}
function normalizeModelListResponse(data) {
  const candidates = [
    data?.models,
    data?.litert_models,
    data?.available_models,
    data?.llm?.models,
    data?.llm?.litert_models,
  ];
  const out = [];
  const seen = new Set();
  for (const value of candidates) {
    if (!Array.isArray(value)) continue;
    for (const item of value) {
      const name = String(typeof item === 'string' ? item : (item?.name || item?.id || '')).trim();
      if (!name || seen.has(name)) continue;
      seen.add(name);
      out.push(name);
    }
  }
  const current = String(data?.current_model || data?.llm?.current_model || '').trim();
  if (current && !seen.has(current)) out.unshift(current);
  return out;
}
async function refreshModelList() {
  try {
    const data = await api.modelList();
    const models = normalizeModelListResponse(data);
    state.availableModels = models;
    state.modelProfiles = data?.profiles || data?.llm?.profiles || {};
    const current = data?.current_model || data?.llm?.current_model || '';
    if (current && models.includes(current)) state.lastGenerationModel = current;
    else if (models.length && !models.includes(state.lastGenerationModel)) state.lastGenerationModel = models[0];
  } catch (e) {
    console.warn('model list unavailable', e);
    if (!Array.isArray(state.availableModels)) state.availableModels = [];
  }
}
async function updateCards(ids, payload){
  for(const id of ids) await api.updateCard(id, payload);
  await window.appReloadGraph();
}
function flipSelectedCards(){
  const ids = selectedCards();
  for (const id of ids) {
    const c = getNode('card', id);
    if (c) c.__flipped = !c.__flipped;
  }
  if (ids.length) renderGraph();
}


export async function loadDecks() {
  await refreshModelList();
  state.decks = await api.decks();
  let saved = localDeckId();
  if (!saved || !state.decks.some(d=>d.id===saved)) saved = state.decks[0]?.id || null;
  state.currentDeckId = saved;
  setLocalDeckId(saved);
  window.appApplyDeckTheme?.();
  renderChrome();
}

export function renderChrome() {
  renderDecks(); renderSourceList(); renderTagCloud(); renderExportScope(); updateViewStates(); renderTaskView(); applyWorkspaceMode();
}

function renderDecks() {
  const box=$('#deckList'); if(!box) return; box.innerHTML='';
  if(!state.decks.length){ box.innerHTML='<div class="muted" style="padding:8px">Колоды пока нет</div>'; return; }
  for(const d of state.decks){
    const b=document.createElement('button');
    b.className=`deck-item ${d.id===state.currentDeckId?'active':''}`;
    const modelIcon = d.model_icon || '📚';
    const modelTitle = d.model_label ? `Модель: ${d.model_label}` : 'Модель не определена';
    b.innerHTML=`<span class="deck-model-icon" title="${esc(modelTitle)}">${esc(modelIcon)}</span><span class="deck-name">${esc(d.name)}</span><span class="deck-count">${d.card_count||0}</span>`;
    b.title=d.name;
    b.onclick=async()=>{
      state.currentDeckId=d.id; setLocalDeckId(d.id); state.selected.clear(); state.activeSourceId=null; state.inspectDeckId=d.id;
      window.appApplyDeckTheme?.();
      window.appOpenInspector?.();
      renderChrome(); await window.appReloadGraph();
    };
    b.ondblclick=()=>{ state.inspectDeckId=d.id; state.selected.clear(); window.appOpenInspector?.(); renderInspector(); renderChrome(); };
    b.oncontextmenu=e=>{ e.preventDefault(); state.currentDeckId=d.id; setLocalDeckId(d.id); state.inspectDeckId=d.id; state.selected.clear(); window.appOpenInspector?.(); renderInspector(); renderChrome(); showDeckContextMenu(e.clientX, e.clientY, d); };
    box.appendChild(b);
  }
}
function showDeckContextMenu(x, y, deck){
  const menu = document.getElementById('contextMenu');
  if(!menu) return;
  menu.innerHTML = `<div class="ctx-block ctx-main"><button data-deck-menu="create-card">＋ Создать карточку вручную</button><button data-deck-menu="study">▶ Повторять колоду</button></div><div class="ctx-block ctx-view"><button data-deck-menu="inspect">Открыть настройки колоды</button><button data-deck-menu="export">⇧ Экспорт колоды</button></div>`;
  menu.style.left = `${Math.min(window.innerWidth - 280, Math.max(8, x))}px`;
  menu.style.top = `${Math.min(window.innerHeight - 260, Math.max(8, y))}px`;
  menu.classList.remove('hidden');
  menu.onclick = async ev => {
    const btn = ev.target?.closest?.('[data-deck-menu]');
    if(!btn) return;
    const act = btn.dataset.deckMenu;
    menu.classList.add('hidden');
    if(act === 'create-card') openCardCreator({ deckId: deck.id });
    if(act === 'study') await openStudyMode();
    if(act === 'inspect') { state.inspectDeckId = deck.id; window.appOpenInspector?.(); renderInspector(); }
    if(act === 'export') window.appOpenExportAt?.(x, y);
  };
}

function sourceId(value){ return String(value ?? ''); }
function cardsForSource(sourceIdValue){
  const sid = sourceId(sourceIdValue);
  return (state.graph.cards || []).filter(c => sourceId(c.source_node_id || '') === sid);
}
function derivedSourceRows(){
  const sources = state.graph.sources || [];
  const cards = state.graph.cards || [];
  const rows = [];
  const known = new Set();
  for(const s of sources){
    const sid = sourceId(s.id);
    known.add(sid);
    rows.push({ source:s, id:sid, title:s.title || 'Источник', icon:s.icon || iconForSource(s.source_type), preview:s.preview || '', color:s.color || '#d97706', cards:cardsForSource(sid), virtual:false });
  }
  const missing = new Map();
  const orphans = [];
  for(const c of cards){
    const sid = sourceId(c.source_node_id || '');
    if(!sid){ orphans.push(c); continue; }
    if(known.has(sid)) continue;
    if(!missing.has(sid)) missing.set(sid, []);
    missing.get(sid).push(c);
  }
  for(const [sid, list] of missing.entries()){
    rows.push({ source:null, id:sid, title:'Источник из старых карточек', icon:'📄', preview:list[0]?.source_quote || list[0]?.back || '', color:'#d97706', cards:list, virtual:true });
  }
  if(!rows.length && orphans.length){
    rows.push({ source:null, id:'', title:'Без источника', icon:'▫️', preview:'Карточки без привязанного источника', color:'#8b5cf6', cards:orphans, virtual:true });
  }
  return rows.sort((a,b)=>(a.source?.created_at || '').localeCompare(b.source?.created_at || '') || a.title.localeCompare(b.title));
}
function renderSourceList() {
  const box=$('#sourceList'); if(!box) return; box.innerHTML='';
  const rows = derivedSourceRows();
  if(!rows.length){ box.innerHTML='<div class="muted" style="padding:8px;line-height:1.45">Источники будут храниться здесь и на холсте.</div>'; return; }
  for(const row of rows){
    const b=document.createElement('button');
    const active = row.id && state.selected.has(nodeKey('source', row.id));
    b.className=`source-item ${active?'active':''} ${row.virtual?'virtual-source':''}`;
    b.style.setProperty('--source-accent', row.color || '#d97706');
    b.innerHTML=`<span>${esc(row.icon)}</span><span class="source-name">${esc(row.title||'Источник')}</span><span class="source-kind">${row.cards.length}</span><div class="source-preview">${esc(row.preview||'')}</div>`;
    b.onclick=()=>{
      state.inspectDeckId=null; state.selected.clear();
      if(row.id) { state.selected.add(nodeKey('source', row.id)); state.activeSourceId=row.id; window.appOpenInspector?.(); renderGraph(); renderInspector(); }
      else { for(const c of row.cards) state.selected.add(nodeKey('card', c.id)); renderGraph(); renderInspector(); }
      renderSourceList(); renderTaskView();
    };
    b.ondblclick=()=>{ if(row.id) showSourceInfo(row.id); };
    box.appendChild(b);
  }
}
function tagScopeTitle(){
  const objs = selectedObjects();
  if (objs.some(o=>o.kind==='card')) return 'Теги выбранных';
  if (objs.some(o=>o.kind==='source')) return 'Теги источника';
  if (state.inspectDeckId || state.currentDeckId) return 'Теги колоды';
  return 'Теги';
}
function renderTagCloud() {
  const box=$('#tagCloud'); if(!box) return; box.innerHTML='';
  const tagTitle = box.closest('.side-section')?.querySelector('.side-head span');
  if(tagTitle) tagTitle.textContent = tagScopeTitle();
  const focusSet = state.focusTags instanceof Set ? state.focusTags : new Set();
  const all = allTags();
  const byTag = new Map(all);
  const focused = [...focusSet].filter(t => t && byTag.has(t)).map(t => [t, byTag.get(t)]);
  const rest = all.filter(([t]) => !focusSet.has(t));
  const tags=[...focused, ...rest].slice(0,40);
  if(!tags.length){ box.innerHTML='<div class="muted" style="padding:8px">Теги появятся после генерации.</div>'; return; }
  for(const [tag,count] of tags){
    const b=document.createElement('button');
    b.className=`tag-pill ${state.focusTags?.has(tag)?'active':''}`;
    b.textContent=`#${displayTag(tag)} ${count}`;
    b.onmouseenter=()=>{ setHoverTag(tag); refreshTagHighlights(); renderEdges(); };
    b.onmouseleave=()=>{ setHoverTag(''); refreshTagHighlights(); renderEdges(); };
    b.onclick=()=>{ selectByTag(tag); renderGraph(); renderChrome(); };
    box.appendChild(b);
  }
}

export async function reloadGraph() {
  if(!state.currentDeckId){ state.graph={sources:[],cards:[],edges:[]}; renderGraph(); renderChrome(); return; }
  state.graph = await api.graph(state.currentDeckId);
  rebuildIndexes();
  renderGraph(); renderChrome();
}


function inspectorCloseButton(){
  return `<button class="panel-close inspector-close" id="closeInspectorBtn" title="Скрыть инспектор">›</button>`;
}
function generationMaxCards(){ return Math.max(1, Number(state.config?.generation?.max_cards || 200)); }
function generationDefaultCards(){ return Math.max(1, Number(state.config?.generation?.default_cards || 10)); }
function clampGenerationCount(value){ return Math.max(1, Math.min(generationMaxCards(), Number(value || generationDefaultCards()))); }
function estimateCardPlan(source){
  const maxCards = generationMaxCards();
  const chars = Number(source?.char_count || 0);
  const words = Number(source?.word_count || 0);
  const basis = chars || Math.max(0, words * 7) || String(source?.preview || '').length;
  if (basis < 220) return { min:1, normal:1, deep:Math.min(2, maxCards), label:'очень короткий источник' };
  if (basis < 700) return { min:1, normal:Math.min(2, maxCards), deep:Math.min(4, maxCards), label:'короткий источник' };
  const min = Math.max(2, Math.ceil(basis / 1600));
  const normal = Math.max(min + 2, Math.ceil(basis / 650));
  const deep = Math.max(normal + 2, Math.ceil(basis / 420));
  return {
    min:clampGenerationCount(min),
    normal:clampGenerationCount(normal),
    deep:clampGenerationCount(deep),
    label:''
  };
}

function bindTagHover(el){
  el.onpointerdown=(e)=>{ e?.preventDefault?.(); e?.stopPropagation?.(); };
  el.onmousedown=(e)=>{ e?.preventDefault?.(); e?.stopPropagation?.(); };
  el.onmouseenter=()=>{ setHoverTag(el.dataset.tag || el.textContent); refreshTagHighlights(); renderEdges(); };
  el.onmouseleave=()=>{ setHoverTag(''); refreshTagHighlights(); renderEdges(); };
  el.onclick=(e)=>{
    e?.preventDefault?.();
    e?.stopPropagation?.();
    selectByTag(el.dataset.tag || el.textContent);
    renderGraph();
    renderChrome();
  };
}
function bindFocusMarker(el){
  const stop = e => { e?.preventDefault?.(); e?.stopPropagation?.(); };
  el.onpointerdown = stop;
  el.onmousedown = stop;
  el.onclick = e => {
    stop(e);
    if (el.dataset.focusStatus) selectByStatus(el.dataset.focusStatus);
    if (el.dataset.focusCardType) selectByCardType(el.dataset.focusCardType);
    renderGraph();
    renderChrome();
  };
}

function toggleTagFocusOnly(rawTag){
  const clean = String(rawTag || '').replace(/^#+/,'').trim().toLowerCase();
  if(!clean) return;
  if(!(state.focusTags instanceof Set)) state.focusTags = new Set();
  if(state.focusTags.has(clean)) state.focusTags.delete(clean);
  else state.focusTags.add(clean);
}
function bindSourceInspectorTag(el){
  el.onmouseenter=()=>{ setHoverTag(el.dataset.tag || el.textContent); refreshTagHighlights(); renderEdges(); };
  el.onmouseleave=()=>{ setHoverTag(''); refreshTagHighlights(); renderEdges(); };
  el.onclick=(e)=>{
    e.preventDefault();
    e.stopPropagation();
    toggleTagFocusOnly(el.dataset.tag || el.textContent);
    renderGraph();
    renderChrome();
  };
}
function tagCountsFromCards(cards){
  const counts = new Map();
  for(const c of cards || []){
    for(const t of tagsList(c.tags)) counts.set(t, (counts.get(t) || 0) + 1);
  }
  return [...counts.entries()].sort((a,b)=>b[1]-a[1] || a[0].localeCompare(b[0]));
}
async function confirmLocal(message){
  return Promise.resolve(window.confirm(message));
}

export function renderInspector() {
  const box=$('#inspector');
  const keys=[...state.selected];
  if(!keys.length && state.inspectDeckId){ const deck=state.decks.find(d=>String(d.id)===String(state.inspectDeckId)); if(deck){ renderDeckInspector(box, deck); return; } }
  if(!keys.length){
    box.innerHTML = inspectorCloseButton();
    $('#app')?.classList.add('inspector-closed');
    try { localStorage.setItem('inspectorClosed', '1'); } catch(_) {}
    bindInspectorPanelButtons();
    return;
  }
  const objs=selectedObjects();
  if(keys.length>1){ renderMultiInspector(box, objs); return; }
  const [kind,id]=splitKey(keys[0]);
  const obj=getNode(kind,id);
  if(!obj) return;
  if(kind==='source') renderSourceInspector(box,obj); else renderCardInspector(box,obj);
}

function renderDeckInspector(box, deck) {
  const cards = state.graph.cards || [];
  const sources = state.graph.sources || [];
  const generatedTags = allTags().slice(0,18);
  const deckTags = tagsList(deck.tags || '');
  box.innerHTML = `${inspectorCloseButton()}<h3>Колода</h3>
    <div class="inspect-card"><div class="inspect-row"><span>Название</span><b>${esc(deck.name||'Колода')}</b></div><div class="inspect-row"><span>Карточек</span><b>${cards.length}</b></div><div class="inspect-row"><span>Источников</span><b>${sources.length}</b></div><div class="inspect-row"><span>Модель</span><b>${esc(deck.model_icon||'📚')} ${esc(deck.model_label||'—')}</b></div></div>
    <div class="inspect-section"><label>Название колоды</label><div class="date-row"><input id="deckNameInput" type="text" value="${esc(deck.name||'')}"><button class="secondary" data-deck-act="rename">Сохранить</button></div></div>
    <div class="inspect-section"><label>Теги колоды</label><div class="tag-editor-row"><input id="deckTagsInput" type="text" value="${esc(tagsInputValue(deck.tags || ''))}" placeholder="#тема #раздел #экзамен"><button class="secondary" data-deck-act="save-tags">Сохранить</button></div></div>
    ${deckTags.length?`<div class="inspect-card"><div class="mini-title">Ручные теги</div><div class="tags editable-tags">${deckTags.map(t=>`<span class="tag tag-removable" data-tag="${esc(t)}">#${esc(displayTag(t))}<button data-remove-deck-tag="${esc(t)}" title="Удалить тег">×</button></span>`).join('')}</div></div>`:''}
    ${generatedTags.length?`<div class="inspect-card"><div class="mini-title">Теги карточек</div><div class="tags editable-tags card-tags-admin">${generatedTags.map(([t,c])=>`<span class="tag tag-removable" data-tag="${esc(t)}" data-card-tag="${esc(t)}">#${esc(displayTag(t))} ${c}<button data-rename-card-tag="${esc(t)}" title="Переименовать тег">✎</button><button data-remove-card-tag="${esc(t)}" title="Удалить тег из карточек">×</button></span>`).join('')}</div></div>`:''}
    <div class="inspect-section"><div class="inspect-actions"><button class="primary" data-deck-act="create-card">＋ Создать карточку</button><button class="secondary" data-deck-act="study">▶ Повторять</button><button class="secondary" data-deck-act="normalize" title="Исправляет старые вопросы, мнемоники и теги без новой генерации">🧹 Почистить старые карточки</button><button class="danger solid-danger" data-deck-act="delete">🗑️ Удалить колоду</button></div></div>`;
  box.querySelectorAll('[data-tag]').forEach(t=>bindTagHover(t));
  box.querySelectorAll('[data-remove-deck-tag]').forEach(btn=>btn.onclick=async(e)=>{
    e.stopPropagation();
    const remove = btn.dataset.removeDeckTag;
    const next = deckTags.filter(t=>t!==remove).map(displayTag).join(', ');
    await api.updateDeck(deck.id,{tags:next});
    await loadDecks();
    renderInspector();
    renderChrome();
  });
  box.querySelectorAll('[data-remove-card-tag]').forEach(btn=>btn.onclick=async(e)=>{
    e.stopPropagation();
    const remove = btn.dataset.removeCardTag;
    if(!confirm(`Удалить тег #${displayTag(remove)} из карточек этой колоды?`)) return;
    await rewriteCardTagInDeck(remove, '');
  });
  box.querySelectorAll('[data-rename-card-tag]').forEach(btn=>btn.onclick=async(e)=>{
    e.stopPropagation();
    const oldTag = btn.dataset.renameCardTag;
    const raw = prompt(`Новое имя для #${displayTag(oldTag)}`, '#' + displayTag(oldTag));
    if(raw === null) return;
    const next = tagsList(raw)[0];
    if(!next) return alert('Введите тег в формате #тег');
    await rewriteCardTagInDeck(oldTag, next);
  });
  box.querySelectorAll('[data-deck-act]').forEach(btn=>btn.onclick=async()=>{
    const act=btn.dataset.deckAct;
    if(act==='rename') { const name=$('#deckNameInput')?.value?.trim(); if(name) { await api.updateDeck(deck.id,{name}); await loadDecks(); renderInspector(); } }
    if(act==='save-tags') { await api.updateDeck(deck.id,{tags:normalizeTagsForSave($('#deckTagsInput')?.value || '')}); await loadDecks(); renderInspector(); renderChrome(); }
    if(act==='create-card') openCardCreator({ deckId: deck.id });
    if(act==='study') await openStudyMode();
    if(act==='fit') fitView();
    if(act==='export') window.appOpenExport?.(btn);
    if(act==='normalize') { if(!confirm('Почистить старые карточки: исправить вопросы, мнемоники и теги без новой генерации?')) return; btn.disabled=true; btn.textContent='Чищу…'; await api.normalizeDeck(deck.id); await window.appReloadGraph(); await loadDecks(); }
    if(act==='delete') { if(await confirmLocal(`Удалить колоду «${deck.name}» вместе с карточками и источниками?`)){ await api.deleteDeck(deck.id); state.inspectDeckId=null; state.selected.clear(); await loadDecks(); await window.appReloadGraph(); } }
  });
}

function renderMultiInspector(box, objs) {
  const cards=objs.filter(o=>o.kind==='card').map(o=>o.obj);
  const sources=objs.filter(o=>o.kind==='source').map(o=>o.obj);
  const tags=[...new Set(cards.flatMap(c=>tagsList(c.tags)))].slice(0,18);
  box.innerHTML=`${inspectorCloseButton()}<h3>${objs.length} объектов выбрано</h3>
    <div class="inspect-card"><div class="inspect-row"><span>Карточек</span><b>${cards.length}</b></div><div class="inspect-row"><span>Источников</span><b>${sources.length}</b></div></div>
    ${tags.length?`<div class="inspect-card"><div class="tags">${tags.map(t=>`<span class="tag" data-tag="${esc(t)}">#${esc(displayTag(t))}</span>`).join('')}</div></div>`:''}
    <div class="inspect-section"><div class="inspect-actions two"><button class="secondary" data-status="inbox">📥 Входящие</button><button class="secondary" data-status="today">🔥 Сегодня</button><button class="secondary" data-status="planned">📅 План</button><button class="secondary" data-status="done">✅ Готово</button></div></div>
    <div class="inspect-section schedule-panel"><label>Дата повторения выбранных</label><div class="date-row"><input id="bulkDueDate" type="date" value="${dateAfter(1)}"><button class="secondary" data-ins="bulk-plan-date">Назначить</button></div><div class="quick-dates"><button data-due="0">Сегодня</button><button data-due="1">Завтра</button><button data-due="3">+3 дня</button><button data-due="7">+7 дней</button></div></div>
    <div class="inspect-section"><div class="inspect-actions"><button class="primary" data-ins="study-selected">▶ Повторить выбранные</button><button class="secondary" data-ins="layout">⌘ Разложить</button><button class="secondary" data-ins="flip-selected">↻ Ответ / вопрос</button><button class="secondary" data-ins="export">⇧ Экспорт выбранных</button><button class="secondary danger" data-ins="delete">Удалить выбранное</button></div></div>`;
  box.querySelectorAll('[data-tag]').forEach(t=>bindTagHover(t));
  box.querySelectorAll('[data-due]').forEach(btn=>btn.onclick=async()=>{ await updateCards(cards.map(c=>c.id), {status: Number(btn.dataset.due)===0?'today':'planned', due_date: dateAfter(Number(btn.dataset.due))}); });
  box.querySelectorAll('[data-status]').forEach(btn=>btn.onclick=async()=>{ for(const c of cards) await api.updateCard(c.id,{status:btn.dataset.status}); await window.appReloadGraph(); });
  bindInspectorCommon(box);
}

function sourceMediaHtml(s) {
  const raw = Array.isArray(s?.media) ? s.media.filter(m => m && (m.url || m.path)) : [];
  const seen = new Set();
  const media = [];
  raw.forEach((m, index) => {
    const key = String(m.url || m.path || '').trim();
    if(!key || seen.has(key)) return;
    seen.add(key);
    media.push({ ...m, __index:index });
  });
  if(!media.length) return '';
  return `<div class="inspect-section media-panel source-media-panel"><div class="mini-title">Изображения источника <small>${media.length}</small></div><div class="media-grid">${media.slice(0,8).map(m=>{
    const url = esc(m.url||m.path||'');
    const title = esc(m.title||m.filename||'Изображение');
    const label = esc(m.page ? 'стр. '+m.page : (m.title||m.filename||'image'));
    return `<div class="media-thumb-wrap"><a class="media-thumb" href="${url}" target="_blank" title="${title}"><img src="${url}" alt=""><span>${label}</span></a><button type="button" class="media-delete" data-delete-source-media="${m.__index}" data-delete-source-media-key="${url}" title="Убрать изображение">×</button></div>`;
  }).join('')}</div><small class="media-help">PDF может дать несколько найденных картинок. Лишние можно убрать крестиком.</small></div>`;
}

function renderSourceInspector(box, s) {
  const linked = state.graph.cards.filter(c=>c.source_node_id===s.id);
  const sourceTags=tagsList(s.tags || '');
  const sourceCardTags = tagCountsFromCards(linked).slice(0,32);
  const tags = sourceCardTags.map(([tag,count]) => [tag,count]);
  const plan = estimateCardPlan(s);
  const sourceColor = s.color || '#d97706';
  box.innerHTML=`${inspectorCloseButton()}<div class="source-inspector-head"><h3>${esc(s.icon || iconForSource(s.source_type))} ${esc(s.title||'Источник')}</h3><button class="round-toggle" data-ins="source-toggle" title="Развернуть текст">⌄</button></div>
    <p>${esc(s.preview||'')}</p>
    <div class="source-inline-reader hidden" id="sourceInlineReader"><div class="source-inline-top"><span>Полный текст источника</span><div class="inline-actions"><button class="tiny-btn source-inline-action" data-ins="source-edit" title="Редактировать текст">✏ Ред.</button><button class="tiny-btn source-inline-action source-save-action hidden" data-ins="source-save" title="Сохранить текст">✓ Сохранить</button><button class="tiny-btn source-inline-action" data-ins="source-info" title="Открыть в большом окне">↗ Окно</button></div></div><pre id="sourceInlineBody">Загрузка…</pre><textarea id="sourceInlineEdit" class="source-inline-edit hidden" spellcheck="false"></textarea></div>
    <div class="inspect-card source-identity-card" style="--source-accent:${esc(sourceColor)}"><div class="inspect-row"><span>Тип</span><b>${esc(sourceTypeLabel(s.source_type||'text'))}</b></div><div class="inspect-row"><span>Карточек</span><b>${linked.length}</b></div><div class="source-color-control compact-color-control"><button id="sourceColorInput" type="button" class="source-color-picker" data-value="${esc(sourceColor)}" title="Индивидуальный цвет этого источника"><span class="source-color-dot"></span></button><input id="sourceColorHex" class="source-color-hex" type="text" maxlength="7" value="${esc(sourceColor)}" spellcheck="false"><button class="secondary" data-ins="save-source-color">Сохранить</button></div></div>
    <div class="inspect-section source-tags-combined professional-tag-panel compact-tag-manager">
      <div class="tag-panel-title"><label>Теги</label><span>${sourceTags.length + tags.length}</span></div>
      <div class="tag-block-row">
        <div class="tag-block-head"><b>Источник</b><small>${sourceTags.length}</small></div>
        <div class="tag-composer source-tag-composer compact-tag-input"><input id="sourceTagQuickInput" type="text" placeholder="+ тег источника, Enter" autocomplete="off" spellcheck="false"></div>
        ${sourceTags.length?`<div class="tags editable-tags tag-admin-list professional-tag-list compact-tag-list">${sourceTags.map(t=>`<span class="tag tag-removable tag-admin source-color-tag ${state.focusTags?.has(t)?'active':''}" style="--tag-accent:${esc(sourceColor)}" data-source-filter-tag="${esc(t)}" data-tag="${esc(t)}" title="Клик — подсветить, ✎ — переименовать, × — отвязать"><span class="tag-label">#${esc(displayTag(t))}</span><button class="tag-action rename" data-edit-source-tag="${esc(t)}" title="Переименовать">✎</button><button class="tag-action remove" data-remove-source-tag="${esc(t)}" title="Отвязать">×</button></span>`).join('')}</div>`:'<div class="tag-empty-note compact-empty">Нет тегов источника.</div>'}
      </div>
      ${linked.length?`<div class="tag-block-row cards-tags-row"><div class="tag-block-head"><b>Карточки источника</b><small>${tags.length}</small></div><div class="tag-composer source-cards-tag-composer compact-tag-input"><input id="sourceCardsTagInput" type="text" placeholder="+ всем карточкам, Enter" autocomplete="off" spellcheck="false"></div>${tags.length?`<div class="tags source-card-tags tag-admin-list professional-tag-list compact-tag-list">${tags.map(([t,c])=>`<span class="tag tag-admin source-color-tag ${state.focusTags?.has(t)?'active':''}" style="--tag-accent:${esc(sourceColor)}" data-source-filter-tag="${esc(t)}" data-tag="${esc(t)}" title="Клик — подсветить, ✎ — переименовать у карточек, × — удалить у карточек"><span class="tag-label">#${esc(displayTag(t))}</span><small>${c}</small><button class="tag-action rename" data-rename-source-card-tag="${esc(t)}" title="Переименовать у карточек">✎</button><button class="tag-action remove" data-remove-source-card-tag="${esc(t)}" title="Удалить у карточек">×</button></span>`).join('')}</div>`:'<div class="tag-empty-note compact-empty">У карточек нет тегов.</div>'}</div>`:''}
      <button class="secondary full compact-media-add" data-ins="upload-source-image">🖼️ Добавить картинку</button>
    </div>
    ${sourceMediaHtml(s)}
    <div class="inspect-section generation-panel compact-generation-panel gen-v107-panel">
      <div class="gen-count-row">
        <div class="count-plan-head"><span>Объём генерации</span><b>${plan.normal} карт.</b></div>
        <div class="count-chips" id="countChips">
          <button class="count-chip" data-count="${plan.min}"><b>Мин</b><span>${plan.min}</span></button>
          <button class="count-chip selected" data-count="${plan.normal}"><b>Норм</b><span>${plan.normal}</span></button>
          <button class="count-chip" data-count="${plan.deep}"><b>Макс</b><span>${plan.deep}</span></button>
        </div>
        <div class="count-manual compact-count-manual"><label>Свое</label><input id="genCount" type="text" inputmode="numeric" pattern="[0-9]*" value="${plan.normal}" data-max="${generationMaxCards()}"></div>
      </div>
      <div class="model-list compact-model-list gen-model-stack" aria-label="Выбор модели и старт">
        ${generationModelButtonsHtml('data-gen')}
      </div>
      <div class="gen-prompt-card modern-prompt compact-prompt-card"><label><span>📝</span> Пожелания</label><textarea id="genPromptHint" rows="2" placeholder="Термины, определения, причины/следствия"></textarea></div>
      <div class="gen-mini-options" aria-label="Дополнительные параметры">
        <label><span>Тип</span><select id="genCardType">${cardTypeOptionsHtml('auto', true)}</select></label>
        <label><span>Язык</span><select id="genLang"><option value="ru">RU</option><option value="en">EN</option></select></label>
        <label><span>Теги</span><select id="genTagMode"><option value="auto" selected>Авто</option><option value="fast">Fast</option><option value="smart">Smart</option></select></label>
      </div>
      ${genSettingsPanelHtml()}
    </div>
    <div class="inspect-section"><div class="inspect-actions"><button class="primary" data-ins="create-card-source">＋ Создать карточку</button><button class="secondary" data-ins="study-source">▶ Повторить карточки источника</button><button class="secondary export-button" data-ins="export">⇧ Экспорт источника</button><button class="secondary danger soft-danger" data-ins="delete-source-cards">🧹 Удалить карточки источника</button><button class="danger solid-danger" data-ins="delete">Удалить источник</button></div></div>`;
  box.querySelectorAll('.count-chip').forEach(btn=>btn.onclick=()=>{
    box.querySelectorAll('.count-chip').forEach(b=>b.classList.remove('selected'));
    btn.classList.add('selected');
    const input = box.querySelector('#genCount');
    if(input) input.value = btn.dataset.count;
  });
  const genCountInput = box.querySelector('#genCount');
  if (genCountInput) {
    genCountInput.oninput = () => {
      const raw = String(genCountInput.value || '').replace(/[^0-9]/g, '');
      if (genCountInput.value !== raw) genCountInput.value = raw;
      box.querySelectorAll('.count-chip').forEach(b=>b.classList.remove('selected'));
    };
    genCountInput.onblur = () => {
      if (!String(genCountInput.value || '').trim()) genCountInput.value = String(plan.normal);
    };
  }

  const sourceColorInput = box.querySelector('#sourceColorInput');
  const sourceColorHex = box.querySelector('#sourceColorHex');
  if (sourceColorInput) {
    const setSourceColor = (value, save = false) => {
      const color = normalizeUiHexColor(value, sourceColor);
      sourceColorInput.dataset.value = color;
      sourceColorInput.style.setProperty('--picked-color', color);
      if (sourceColorHex && sourceColorHex.value.toLowerCase() !== color) sourceColorHex.value = color;
      box.querySelector('.source-identity-card')?.style.setProperty('--source-accent', color);
      box.querySelectorAll('.source-color-tag').forEach(tag => tag.style.setProperty('--tag-accent', color));
      if (save) box.querySelector('[data-ins="save-source-color"]')?.click();
    };
    setSourceColor(sourceColor);
    sourceColorInput.addEventListener('click', (e) => {
      e.preventDefault();
      e.stopPropagation();
      openUiColorPicker(sourceColorInput, sourceColorInput.dataset.value || sourceColor, setSourceColor);
    });
    sourceColorHex?.addEventListener('input', () => {
      const raw = String(sourceColorHex.value || '').trim();
      if (/^#?[0-9a-f]{3}([0-9a-f]{3})?$/i.test(raw)) setSourceColor(raw.startsWith('#') ? raw : '#' + raw);
    });
  }
  if (!box.dataset.sourceMediaDeleteDelegated) {
    box.dataset.sourceMediaDeleteDelegated = '1';
    box.addEventListener('click', async (e) => {
      const btn = e.target?.closest?.('[data-delete-source-media]');
      if (!btn) return;
      e.preventDefault();
      e.stopPropagation();
      const rawIndex = Number(btn.dataset.deleteSourceMedia);
      const key = String(btn.dataset.deleteSourceMediaKey || '').trim();
      const sid = selectedSources()[0] || s.id;
      if (!sid) return;
      try {
        btn.disabled = true;
        if (key && api.deleteSourceMediaKey) await api.deleteSourceMediaKey(sid, key);
        else if (Number.isInteger(rawIndex) && rawIndex >= 0) await api.deleteSourceMedia(sid, rawIndex);
        await window.appReloadGraph();
      } catch (err) {
        btn.disabled = false;
        alert('Не удалось удалить картинку: ' + (err?.message || err));
      }
    }, true);
  }
  box.querySelectorAll('[data-source-filter-tag]').forEach(t=>bindSourceInspectorTag(t));
  const sourceTagInput = box.querySelector('#sourceTagQuickInput');
  if(sourceTagInput) sourceTagInput.addEventListener('keydown', async (e)=>{
    if(e.key !== 'Enter') return;
    e.preventDefault();
    const value = sourceTagInput.value.trim();
    if(!value) return;
    sourceTagInput.disabled = true;
    try { await addSourceQuickTag(s.id, value); }
    finally { sourceTagInput.disabled = false; sourceTagInput.value = ''; renderInspector(); renderChrome(); }
  });
  const sourceCardsTagInput = box.querySelector('#sourceCardsTagInput');
  if(sourceCardsTagInput) sourceCardsTagInput.addEventListener('keydown', async (e)=>{
    if(e.key !== 'Enter') return;
    e.preventDefault();
    const value = sourceCardsTagInput.value.trim();
    if(!value) return;
    sourceCardsTagInput.disabled = true;
    try { await addTagToSourceCards(s.id, value); }
    finally { sourceCardsTagInput.disabled = false; sourceCardsTagInput.value = ''; }
  });
  box.querySelectorAll('[data-edit-source-tag]').forEach(btn=>btn.onclick=async(e)=>{
    e.preventDefault();
    e.stopPropagation();
    await renameSourceOnlyTag(s.id, btn.dataset.editSourceTag);
  });
  box.querySelectorAll('[data-remove-source-tag]').forEach(btn=>btn.onclick=async(e)=>{
    e.preventDefault();
    e.stopPropagation();
    const remove = btn.dataset.removeSourceTag;
    const next = sourceTags.filter(t=>t!==remove).map(displayTag).join(', ');
    await api.updateSource(s.id,{tags:next});
    await window.appReloadGraph();
  });
  box.querySelectorAll('[data-rename-source-card-tag]').forEach(btn=>btn.onclick=async(e)=>{
    e.preventDefault();
    e.stopPropagation();
    await renameTagInSourceCards(s.id, btn.dataset.renameSourceCardTag);
  });
  box.querySelectorAll('[data-remove-source-card-tag]').forEach(btn=>btn.onclick=async(e)=>{
    e.preventDefault();
    e.stopPropagation();
    await removeTagFromSourceCards(s.id, btn.dataset.removeSourceCardTag);
  });
  box.querySelectorAll('[data-delete-source-media]').forEach(btn=>btn.onclick=async(e)=>{
    e.preventDefault();
    e.stopPropagation();
    const rawIndex = Number(btn.dataset.deleteSourceMedia);
    const key = String(btn.dataset.deleteSourceMediaKey || '').trim();
    if ((!Number.isInteger(rawIndex) || rawIndex < 0) && !key) return;
    try {
      btn.disabled = true;
      if (key && api.deleteSourceMediaKey) await api.deleteSourceMediaKey(s.id, key);
      else await api.deleteSourceMedia(s.id, rawIndex);
      await window.appReloadGraph();
    } catch (err) {
      btn.disabled = false;
      alert('Не удалось удалить картинку: ' + (err?.message || err));
    }
  });
  const genTagSel = box.querySelector('#genTagMode');
  genTagSel?.addEventListener('change', () => { genTagSel.dataset.touched = '1'; });
  box.querySelectorAll('[data-gen]').forEach(btn=>btn.onclick=()=>{
    box.querySelectorAll('[data-gen]').forEach(b=>b.classList.remove('selected'));
    btn.classList.add('selected');
    generateFromSource(s.id, btn.dataset.gen);
  });
  // Bind the generation settings panel (sliders + toggles + presets).
  bindGenSettingsPanel(box, (next) => { /* settings saved in localStorage already */ });
  bindInspectorCommon(box);
}
function renderCardInspector(box, c) {
  const source=state.graph.sources.find(s=>s.id===c.source_node_id);
  const tags=tagsList(c.tags);
  const focusTags = selectedFocusTags();
  const related = state.graph.cards.filter(x => {
    if (x.id === c.id) return false;
    const xt = tagsList(x.tags);
    if (focusTags.size) return xt.some(t => focusTags.has(t) && tags.includes(t));
    return tags.some(t => xt.includes(t));
  }).slice(0,6);
  const statusFocus = selectedFocusStatuses();
  const typeFocus = selectedFocusCardTypes();
  const statusKey = String(c.status || 'inbox').toLowerCase();
  const typeKey = String(c.card_type || 'basic').toLowerCase();
  box.innerHTML=`${inspectorCloseButton()}<h3>Карточка</h3>
    <div class="inspect-card"><div class="inspect-row"><span>Тип</span><b><button class="inspect-focus-chip ${typeFocus.has(typeKey)?'active':''}" data-focus-card-type="${esc(typeKey)}">${esc(cardTypeLabel(c.card_type))}</button></b></div><div class="inspect-row"><span>Статус</span><b><button class="inspect-focus-chip ${statusFocus.has(statusKey)?'active':''}" data-focus-status="${esc(statusKey)}">${esc(statusLabelForFocus(c))}</button></b></div><div class="inspect-row"><span>Повторов</span><b>${Number(c.review_count||0)}</b></div><div class="inspect-row"><span>Интервал</span><b>${Number(c.interval_days||0)} дн.</b></div></div>
    <div class="inspect-card card-inspect-card"><p><b>${esc(c.front||'')}</b></p>${c.image_path?`<img class="card-image-preview" src="${esc(c.image_path)}" alt="">`:''}<p>${esc(c.back||'')}</p>${c.mnemonic?`<p class="mnemonic"><b>Мнемоника:</b> ${esc(c.mnemonic)}</p>`:''}${tags.length?`<div class="tags">${tags.map(t=>`<span class="tag" data-tag="${esc(t)}">#${esc(displayTag(t))}</span>`).join('')}</div>`:''}</div>
    ${source?`<div class="inspect-card"><div class="inspect-row"><span>Источник</span><b>${esc(source.title||'')}</b></div></div>`:''}
    ${related.length?`<div class="inspect-card"><div class="mini-title">Похожие по тегам</div>${related.map(r=>`<button class="related-card" data-card="${r.id}">${esc(r.front||'Карточка')}</button>`).join('')}</div>`:''}
    <div class="inspect-section"><div class="inspect-actions"><button class="primary" data-ins="study-one">▶ Повторить</button><button class="secondary" data-ins="edit-card">✏️ Редактировать</button><button class="secondary" data-ins="upload-card-image">🖼️ Картинка</button>${c.image_path?`<button class="secondary" data-ins="remove-card-image">🗑 Убрать фото</button>`:''}<button class="secondary" data-ins="source-info-card">👁️ Источник</button></div></div>
    <div class="inspect-section"><div class="status-grid"><button class="secondary" data-status="inbox">📥 Входящие</button><button class="secondary" data-status="today">🔥 Сегодня</button><button class="secondary" data-status="planned">📅 План</button><button class="secondary" data-status="done">✅ Готово</button></div></div>
    <div class="inspect-section schedule-panel"><label>Дата повторения</label><div class="date-row"><input id="cardDueDate" type="date" value="${isoDate(c.due_date)}"><button class="secondary" data-ins="save-card-date">Назначить</button></div><div class="quick-dates"><button data-due="0">Сегодня</button><button data-due="1">Завтра</button><button data-due="3">+3 дня</button><button data-due="7">+7 дней</button></div><small>${c.due_date ? `Назначено: ${dateLabel(c.due_date)}` : `Сегодня: ${dateLabel(todayLocalYMD())}`}</small></div>
    <div class="inspect-section"><button class="danger solid-danger full" data-ins="delete">Удалить карточку</button></div>`;
  box.querySelectorAll('[data-tag]').forEach(t=>bindTagHover(t));
  box.querySelectorAll('[data-focus-status],[data-focus-card-type]').forEach(t=>bindFocusMarker(t));
  box.querySelectorAll('[data-card]').forEach(btn=>btn.onclick=()=>{ const cardId = btn.dataset.card; state.selected.clear(); state.selected.add(nodeKey('card', cardId)); window.appOpenInspector?.(); renderGraph(); renderChrome(); setTimeout(()=>focusObject('card', cardId), 40); });
  box.querySelectorAll('[data-due]').forEach(btn=>btn.onclick=async()=>{ const days=Number(btn.dataset.due); await api.updateCard(c.id,{status: days===0?'today':'planned', due_date: dateAfter(days)}); await window.appReloadGraph(); });
  box.querySelectorAll('[data-status]').forEach(btn=>btn.onclick=async()=>{ await api.updateCard(c.id,{status:btn.dataset.status}); await window.appReloadGraph(); });
  bindInspectorCommon(box);
}

async function rewriteCardTagInDeck(oldTag, newTag) {
  const oldClean = tagsList(oldTag)[0] || String(oldTag || '').replace(/^#+/,'').trim().toLowerCase();
  const newClean = newTag ? (tagsList(newTag)[0] || String(newTag || '').replace(/^#+/,'').trim().toLowerCase()) : '';
  if(!oldClean) return;
  const cards = state.graph.cards || [];
  for (const card of cards) {
    const tags = tagsList(card.tags);
    if(!tags.includes(oldClean)) continue;
    const next = [];
    for (const tag of tags) {
      const value = tag === oldClean ? newClean : tag;
      if(value && !next.includes(value)) next.push(value);
    }
    await api.updateCard(card.id, { tags: next.map(displayTag).join(' ') });
  }
  await window.appReloadGraph?.();
  await loadDecks();
  renderInspector();
  renderChrome();
}



function firstCleanTag(value) {
  const fromList = tagsList(value)[0];
  const fallback = String(value || '').replace(/^#+/, '').trim().toLowerCase();
  return (fromList || fallback).replace(/^#+/, '').trim().toLowerCase();
}
function tagsToSave(tags) {
  const out = [];
  for (const raw of tags || []) {
    const clean = firstCleanTag(raw);
    if (clean && !out.includes(clean)) out.push(clean);
  }
  return out.map(displayTag).join(' ');
}
function sourceCardsForTagCrud(sourceIdValue) {
  return (state.graph.cards || []).filter(c => String(c.source_node_id || '') === String(sourceIdValue || ''));
}
function cleanTagsFromInput(value) {
  const parsed = tagsList(value);
  if (parsed.length) return parsed;
  const single = firstCleanTag(value);
  return single ? [single] : [];
}
async function addSourceQuickTag(sourceId, rawValue) {
  const incoming = cleanTagsFromInput(rawValue);
  if (!sourceId || !incoming.length) return;
  let src = getNode('source', sourceId);
  if (!src) {
    try { src = await api.getSource(sourceId); } catch(e) { src = null; }
  }
  const next = tagsList(src?.tags || '');
  for (const tag of incoming) if (!next.includes(tag)) next.push(tag);
  await api.updateSource(sourceId, { tags: tagsToSave(next) });
  await window.appReloadGraph?.();
}
async function renameSourceOnlyTag(sourceId, oldTag) {
  const oldClean = firstCleanTag(oldTag);
  if (!sourceId || !oldClean) return;
  const typed = prompt(`Переименовать тег источника #${displayTag(oldClean)} в:`, `#${displayTag(oldClean)}`);
  if (typed === null) return;
  const nextClean = firstCleanTag(typed);
  let src = getNode('source', sourceId);
  if (!src) {
    try { src = await api.getSource(sourceId); } catch(e) { src = null; }
  }
  const next = [];
  for (const tag of tagsList(src?.tags || '')) {
    const value = tag === oldClean ? nextClean : tag;
    if (value && !next.includes(value)) next.push(value);
  }
  await api.updateSource(sourceId, { tags: tagsToSave(next) });
  await window.appReloadGraph?.();
}
async function addTagToSourceCards(sourceId, rawValue) {
  const incoming = cleanTagsFromInput(rawValue);
  const cards = sourceCardsForTagCrud(sourceId);
  if (!incoming.length || !cards.length) return;
  for (const card of cards) {
    const next = tagsList(card.tags);
    for (const tag of incoming) if (!next.includes(tag)) next.push(tag);
    await api.updateCard(card.id, { tags: tagsToSave(next) });
  }
  await window.appReloadGraph?.();
  await loadDecks();
  renderInspector();
  renderChrome();
}
async function renameTagInSourceCards(sourceId, oldTag) {
  const oldClean = firstCleanTag(oldTag);
  if (!sourceId || !oldClean) return;
  const typed = prompt(`Переименовать тег карточек этого источника #${displayTag(oldClean)} в:`, `#${displayTag(oldClean)}`);
  if (typed === null) return;
  const newClean = firstCleanTag(typed);
  for (const card of sourceCardsForTagCrud(sourceId)) {
    const current = tagsList(card.tags);
    if (!current.includes(oldClean)) continue;
    const next = [];
    for (const tag of current) {
      const value = tag === oldClean ? newClean : tag;
      if (value && !next.includes(value)) next.push(value);
    }
    await api.updateCard(card.id, { tags: tagsToSave(next) });
  }
  await window.appReloadGraph?.();
  await loadDecks();
  renderInspector();
  renderChrome();
}
async function removeTagFromSourceCards(sourceId, oldTag) {
  const oldClean = firstCleanTag(oldTag);
  if (!sourceId || !oldClean) return;
  if (!confirm(`Удалить тег #${displayTag(oldClean)} у карточек этого источника?`)) return;
  for (const card of sourceCardsForTagCrud(sourceId)) {
    const next = tagsList(card.tags).filter(t => t !== oldClean);
    await api.updateCard(card.id, { tags: tagsToSave(next) });
  }
  await window.appReloadGraph?.();
  await loadDecks();
  renderInspector();
  renderChrome();
}

function bindInspectorPanelButtons(){
  const btn = $('#closeInspectorBtn');
  if(btn) btn.onclick = () => {
    $('#app')?.classList.add('inspector-closed');
    $('#openInspectorBtn')?.classList.remove('hidden');
    try { localStorage.setItem('inspectorClosed','1'); } catch(e) {}
  };
}
async function deleteCardsFromSelectedSource(){
  const sid=selectedSources()[0];
  if(!sid) return;
  const count=(state.graph.cards||[]).filter(c=>String(c.source_node_id||'')===String(sid)).length;
  if(!count){ alert('У этого источника нет карточек.'); return; }
  if(!confirm(`Удалить ${count} карточек этого источника? Сам источник останется.`)) return;
  await api.deleteSourceCards(sid);
  state.selected.clear();
  state.selected.add(nodeKey('source', sid));
  await window.appReloadGraph();
}

function bindInspectorCommon(box) {
  bindInspectorPanelButtons();
  box.querySelectorAll('[data-ins]').forEach(btn=>btn.onclick=async()=>{
    const a=btn.dataset.ins;
    if(a==='delete') await window.appDeleteSelection();
    if(a==='export') window.appOpenExport?.(btn);
    if(a==='layout') await autoLayoutLocal('sourceCards');
    if(a==='layout-source') await autoLayoutLocal('sourceCards');
    if(a==='edit-card') openCardEditor(getNode('card', selectedCards()[0]));
    if(a==='flip-card' || a==='flip-selected') flipSelectedCards();
    if(a==='study-one'){ await openStudyMode({mode:'all'}); }
    if(a==='create-card-source'){ const sid=selectedSources()[0]; openCardCreator({ sourceId: sid || '' }); }
    if(a==='study-source'){ const sid=selectedSources()[0]; if(sid) await openStudyMode({mode:'all', source_id:sid}); }
    if(a==='delete-source-cards') await deleteCardsFromSelectedSource();
    if(a==='study-selected'){ await openStudyMode({mode:'selected'}); }
    if(a==='save-card-date'){ const ids=selectedCards(); const val=$('#cardDueDate')?.value || ''; if(ids[0]) await api.updateCard(ids[0],{status:val?'planned':'inbox', due_date:val}); await window.appReloadGraph(); }
    if(a==='bulk-plan-date'){ const ids=selectedCards(); const val=$('#bulkDueDate')?.value || ''; if(ids.length) await updateCards(ids,{status:val?'planned':'inbox', due_date:val}); }
    if(a==='source-toggle') await toggleInlineSource(selectedSources()[0]);
    if(a==='source-info') await showSourceInfo(selectedSources()[0], getNode('source', selectedSources()[0]));
    if(a==='source-info-card'){ const c=getNode('card', selectedCards()[0]); if(c?.source_node_id) await showSourceInfo(c.source_node_id, state.graph.sources.find(s=>String(s.id)===String(c.source_node_id))); }
    if(a==='source-edit') await setInlineSourceEdit(true);
    if(a==='source-save') await saveInlineSourceText();
    if(a==='save-source-tags'){ const sid=selectedSources()[0]; if(sid){ await api.updateSource(sid,{tags:normalizeTagsForSave($('#sourceTagsInput')?.value || '')}); await window.appReloadGraph(); } }
    if(a==='add-source-tag'){ const sid=selectedSources()[0]; const input=$('#sourceTagQuickInput'); if(sid && input?.value){ await addSourceQuickTag(sid, input.value); input.value=''; } }
    if(a==='add-linked-card-tag'){ const sid=selectedSources()[0]; const input=$('#sourceCardsTagInput'); if(sid && input?.value){ await addTagToSourceCards(sid, input.value); input.value=''; } }
    if(a==='save-source-color'){ const sid=selectedSources()[0]; if(sid){ const el=$('#sourceColorInput'); const color=normalizeUiHexColor(el?.dataset?.value || '', '#d97706'); await api.updateSource(sid,{color}); await window.appReloadGraph(); } }
    if(a==='upload-source-image'){ const sid=selectedSources()[0]; const input=$('#hiddenSourceImageInput'); if(input && sid){ input.value=''; input.onchange=async ev=>{ const f=ev.target.files?.[0]; if(f){ await api.uploadSourceMedia(sid,f); await window.appReloadGraph(); } }; input.click(); } }
    if(a==='upload-card-image'){ const id=selectedCards()[0]; const input=$('#hiddenCardImageInput'); if(input && id){ input.value=''; input.onchange=async ev=>{ const f=ev.target.files?.[0]; if(f){ await api.uploadCardImage(id,f); await window.appReloadGraph(); } }; input.click(); } }
    if(a==='remove-card-image'){ const id=selectedCards()[0]; if(id){ await api.updateCard(id,{image_path:''}); await window.appReloadGraph(); } }
  });
}

export async function toggleInlineSource(sourceId, forceOpen=null){
  if(!sourceId) return;
  const reader=$('#sourceInlineReader');
  const body=$('#sourceInlineBody');
  const edit=$('#sourceInlineEdit');
  const toggle=document.querySelector('[data-ins="source-toggle"]');
  if(!reader || !body) return;
  const willOpen = forceOpen === null ? reader.classList.contains('hidden') : Boolean(forceOpen);
  reader.classList.toggle('hidden', !willOpen);
  if(toggle) toggle.textContent = willOpen ? '⌃' : '⌄';
  if(willOpen && !reader.dataset.loaded){
    let src = getNode('source', sourceId);
    if(!src?.legacy){ try { src = await api.getSource(sourceId); } catch(e) {} }
    const text = src?.content||src?.preview||'';
    body.textContent=text;
    if(edit) edit.value=text;
    reader.dataset.loaded='1';
  }
}
async function setInlineSourceEdit(active){
  const sid=selectedSources()[0]; if(!sid) return;
  await toggleInlineSource(sid, true);
  const body=$('#sourceInlineBody'), edit=$('#sourceInlineEdit'), save=document.querySelector('[data-ins="source-save"]');
  if(!body || !edit) return;
  if(active) edit.value = body.textContent || edit.value || '';
  body.classList.toggle('hidden', active);
  edit.classList.toggle('hidden', !active);
  save?.classList.toggle('hidden', !active);
}
async function saveInlineSourceText(){
  const sid=selectedSources()[0]; const edit=$('#sourceInlineEdit'); if(!sid || !edit) return;
  await api.updateSource(sid,{content:edit.value});
  const body=$('#sourceInlineBody'); if(body) body.textContent=edit.value;
  const reader=$('#sourceInlineReader'); if(reader) reader.dataset.loaded='1';
  await setInlineSourceEdit(false);
  await window.appReloadGraph?.();
}

export function openCardEditor(card){
  if(!card) return;
  pendingCreateCard = null;
  $('#cardModal .dialog-head h2').textContent = 'Редактирование карточки';
  $('#editCardId').value=card.id;
  $('#editFront').value=card.front||'';
  $('#editBack').value=card.back||'';
  $('#editQuote').value=card.source_quote||'';
  $('#editMnemonic').value=card.mnemonic||'';
  $('#editTags').value=tagsInputValue(card.tags);
  const ct=$('#editCardType'); if(ct) ct.value=card.card_type||'basic';
  $('#editStatus').value=card.status||'inbox';
  const due=$('#editDueDate'); if(due) due.value=isoDate(card.due_date);
  $('#cardModal').classList.add('active');
}
export function openCardCreator(opts={}){
  if(!state.currentDeckId && opts.deckId) state.currentDeckId = opts.deckId;
  pendingCreateCard = { sourceId: opts.sourceId || '', x: opts.x ?? null, y: opts.y ?? null };
  $('#cardModal .dialog-head h2').textContent = 'Новая карточка';
  $('#editCardId').value='';
  $('#editFront').value='';
  $('#editBack').value='';
  $('#editQuote').value='';
  $('#editMnemonic').value='';
  $('#editTags').value='';
  const ct=$('#editCardType'); if(ct) ct.value='basic';
  $('#editStatus').value='inbox';
  const due=$('#editDueDate'); if(due) due.value='';
  $('#cardModal').classList.add('active');
  setTimeout(()=>$('#editFront')?.focus(), 80);
}
export async function saveCardEditor(){
  const id=$('#editCardId').value;
  const payload={front:$('#editFront').value.trim(),back:$('#editBack').value.trim(),source_quote:$('#editQuote').value.trim(),mnemonic:$('#editMnemonic').value.trim(),tags:normalizeTagsForSave($('#editTags').value),card_type:$('#editCardType')?.value||'basic',status:$('#editStatus').value,due_date:$('#editDueDate')?.value||null};
  if(id) await api.updateCard(id,payload);
  else {
    const create = pendingCreateCard || {};
    await api.createCard(state.currentDeckId, {...payload, source_node_id:create.sourceId || '', x:create.x, y:create.y});
  }
  pendingCreateCard = null;
  $('#cardModal').classList.remove('active'); await window.appReloadGraph();
}
export async function showSourceInfo(id, fallback=null){
  let src = fallback || getNode('source', id);
  if(!src?.legacy){
    try { src = await api.getSource(id); }
    catch(e) { if(!src) { alert('Источник не найден'); return; } }
  }
  currentSourceInfoId = id || src?.id || null;
  const text = src?.content||src?.preview||'';
  $('#sourceInfoTitle').textContent=src?.title||'Источник';
  $('#sourceInfoMeta').textContent=[src?.source_type,src?.url].filter(Boolean).join(' · ');
  $('#sourceInfoBody').textContent=text;
  const edit=$('#sourceInfoEdit'); if(edit) edit.value=text;
  setSourceInfoEditMode(false);
  const editBtn=$('#editSourceInfoBtn'); if(editBtn) editBtn.onclick = () => setSourceInfoEditMode($('#sourceInfoEdit')?.classList.contains('hidden'));
  const saveBtn=$('#saveSourceInfoBtn'); if(saveBtn) saveBtn.onclick = saveSourceInfoText;
  $('#sourceInfoModal').classList.add('active');
}
function setSourceInfoEditMode(active){
  $('#sourceInfoBody')?.classList.toggle('hidden', active);
  $('#sourceInfoEdit')?.classList.toggle('hidden', !active);
  $('#saveSourceInfoBtn')?.classList.toggle('hidden', !active);
  const btn=$('#editSourceInfoBtn'); if(btn) btn.textContent = active ? 'Просмотр' : '✏️ Редактировать текст';
}
async function saveSourceInfoText(){
  if(!currentSourceInfoId) return;
  const text=$('#sourceInfoEdit')?.value || '';
  await api.updateSource(currentSourceInfoId,{content:text});
  $('#sourceInfoBody').textContent=text;
  setSourceInfoEditMode(false);
  await window.appReloadGraph?.();
}
export function openSourceModalAt(x=null,y=null){ pendingSourcePosition = (x!=null && y!=null) ? {x,y} : null; $('#sourceModal').classList.add('active'); setTimeout(()=>$('#sourceText').focus(), 80); }
export async function addDeck(){
  const name=prompt('Название колоды', `Колода ${new Date().toLocaleDateString('ru-RU')}`);
  if(name===null) return;
  const d=await api.createDeck(name.trim()||'Новая колода');
  await loadDecks(); state.currentDeckId=d.id; setLocalDeckId(d.id); window.appApplyDeckTheme?.(); await reloadGraph();
}
export async function saveSourceFromModal(){
  if(!state.currentDeckId) await addDeck(); if(!state.currentDeckId) return;
  const raw=$('#sourceText').value.trim(); if(!raw) return alert('Вставь текст или ссылку');
  let content=raw, sourceType='text', url='';
  try {
    if(isProbablyUrl(raw)){ url=raw; sourceType=isYoutubeUrl(raw)?'youtube':'url'; const parsed=sourceType==='youtube'?await api.parseYoutube(raw):await api.parseUrl(raw); content=parsed.text||''; }
  } catch(e) { alert('Не удалось прочитать ссылку: '+e.message); return; }
  const pos=pendingSourcePosition || { x:120, y:160 };
  const title=$('#sourceTitle').value.trim() || (url ? url : content.slice(0,80));
  await api.createSource(state.currentDeckId,{title,content,source_type:sourceType,url,x:Math.round(pos.x),y:Math.round(pos.y)});
  $('#sourceText').value=''; $('#sourceTitle').value=''; pendingSourcePosition=null; $('#sourceModal').classList.remove('active'); await reloadGraph();
}
export async function uploadFileAsSource(file){
  if(!state.currentDeckId) await addDeck(); if(!state.currentDeckId||!file) return;
  const data=await api.uploadFile(state.currentDeckId,file);
  const lower=file.name.toLowerCase();
  const type=lower.endsWith('.pdf')?'pdf':lower.endsWith('.docx')?'docx':lower.endsWith('.epub')?'epub':lower.endsWith('.fb2')?'fb2':data.is_image?'image':'file';
  const pos=pendingSourcePosition || { x:120, y:160 };
  const src = await api.createSource(state.currentDeckId,{title:file.name,content:data.text||data.text_preview||'',source_type:type,media:data.media||[],x:Math.round(pos.x),y:Math.round(pos.y)});
  pendingSourcePosition=null;
  $('#sourceText').value=''; $('#sourceTitle').value='';
  $('#sourceModal')?.classList.remove('active');
  const input = $('#hiddenFileInput'); if(input) input.value='';
  state.selected.clear(); state.selected.add(nodeKey('source', src.id));
  await reloadGraph();
}
function generationModelMeta(modelName='') {
  const raw = String(modelName || '').toLowerCase();
  if (raw.includes('supergemma') || raw.includes('e4b')) return { icon:'🧠', label:'SuperGemma E4B', short:'smart', cls:'smart' };
  if (raw.includes('gemma') || raw.includes('e2b')) return { icon:'⚡', label:'Gemma E2B', short:'fast', cls:'fast' };
  return { icon:'⚙️', label:'Модель', short:'auto', cls:'auto' };
}
function setProgressBadges(modelName='') {
  const meta = generationModelMeta(modelName || state.lastGenerationModel || 'gemma-4-E2B-it');
  const targetBadge = $('#progressTargetBadge');
  if (targetBadge) {
    targetBadge.textContent = `${meta.icon} ${meta.label}`;
    targetBadge.dataset.modelClass = meta.cls;
    targetBadge.removeAttribute('data-profile-class');
    targetBadge.title = `${meta.label} · ${meta.short}`;
  }
  const modelBadge = $('#progressModelBadge');
  if (modelBadge) {
    modelBadge.textContent = `${meta.icon} ${meta.label}`;
    modelBadge.dataset.modelClass = meta.cls;
    modelBadge.title = `${meta.label} · ${meta.short}`;
  }
}

export async function generateFromSource(sourceId, modelName, countOverride=null, options={}){
  const source=await api.getSource(sourceId);
  const count=clampGenerationCount(countOverride || $('#genCount')?.value || generationDefaultCards());
  const language=options.language || $('#genLang')?.value || 'ru';
  const cardType=options.cardType || $('#genCardType')?.value || 'auto';
  const promptHint=(options.customPrompt ?? $('#genPromptHint')?.value ?? '').trim();
  const generationMode=options.generationMode || $('#genMode')?.value || 'fast';
  const explicitTagMode = options.tagMode || $('#genTagMode')?.value || 'auto';
  const tagMode = explicitTagMode !== 'auto' ? explicitTagMode : 'fast';
  // Pull current slider/toggle values from localStorage.
  const genSettings = buildGenPayload(document.querySelector('#genPanel')?.closest('.inspect-section') || document);
  state.lastGenerationModel = modelName || state.lastGenerationModel || 'gemma-4-E2B-it';
  setProgressBadges(state.lastGenerationModel);
  const buttons=[...document.querySelectorAll('[data-gen]')];
  buttons.forEach(b=>{ b.disabled=true; b.classList.add('busy'); });
  try {
    await api.generate(state.currentDeckId,{
      content:source.content||'',
      source_node_id:sourceId,
      card_count:count,
      manual_count:true,
      card_type:cardType,
      custom_prompt:promptHint,
      generation_mode:generationMode,
      tag_extraction_mode:tagMode,
      model_name:modelName,
      language,
      image_path:source.image_path||'',
      // UI overrides — backend applies these on top of env defaults.
      ...genSettings,
    });
    startProgressLoop();
  } catch(e) {
    alert('Не удалось запустить генерацию: '+e.message);
    buttons.forEach(b=>{ b.disabled=false; b.classList.remove('busy'); });
  }
}
export function startProgressLoop(){
  clearInterval(state.progressTimer);
  const box = $('#progressBox');
  if (!box) return;
  box.classList.remove('hidden');
  box.classList.add('active');
  setProgressBadges(state.lastGenerationModel || 'gemma-4-E2B-it');
  const formatDuration = (rawSeconds) => {
    const seconds = Math.max(0, Math.floor(Number(rawSeconds || 0)));
    const m = Math.floor(seconds / 60);
    const s = seconds % 60;
    if (m >= 60) {
      const h = Math.floor(m / 60);
      const mm = m % 60;
      return `${h}:${String(mm).padStart(2,'0')}:${String(s).padStart(2,'0')}`;
    }
    return `${m}:${String(s).padStart(2,'0')}`;
  };
  const setStep = (pct) => {
    const steps = ['read','generate','save'];
    steps.forEach((name, i) => {
      const el = box.querySelector(`[data-step="${name}"]`);
      if(el) el.classList.toggle('active', pct >= [1, 25, 75][i]);
    });
  };
  state.progressTimer=setInterval(async()=>{
    if(!state.currentDeckId) return;
    const p=await api.progress(state.currentDeckId);
    const total = Number(p.total || 0);
    const current = Number(p.current || 0);
    const pct = total ? Math.round((current/total)*100) : (p.status==='processing'?17:0);
    const safePct = Math.max(0, Math.min(100, pct));
    const progressText = $('#progressText');
    if (progressText) progressText.textContent = p.message || (p.status==='processing' ? 'Генерация карточек…' : p.status || '');
    const progressRatio = $('#progressRatio');
    if (progressRatio) progressRatio.textContent = total ? `${current}/${total}` : '';
    const elapsed = Number(p.elapsed_seconds || 0);
    const eta = Number(p.eta_seconds || 0);
    const meta = $('#progressMeta');
    if (meta) {
      if (p.status === 'processing') {
        meta.textContent = `Идёт ${formatDuration(elapsed)}${eta > 0 ? ` • осталось ~${formatDuration(eta)}` : ''}`;
      } else if (p.status === 'completed') {
        meta.textContent = `готово за ${formatDuration(elapsed)}`;
      } else if (p.status === 'error') {
        meta.textContent = `остановлено через ${formatDuration(elapsed)}`;
      } else {
        meta.textContent = '';
      }
    }
    $('#progressPct') && ($('#progressPct').textContent = `${safePct}%`);
    const progressFill = $('#progressFill');
    if (progressFill) progressFill.style.width=`${safePct}%`;
    box.style.setProperty('--progress', `${safePct}%`);
    setStep(safePct);
    if(p.status==='completed'||p.status==='error'){
      clearInterval(state.progressTimer);
      document.querySelectorAll('[data-gen]').forEach(b=>{ b.disabled=false; b.classList.remove('busy'); });
      if(p.status==='completed') { $('#progressPct') && ($('#progressPct').textContent = '100%'); const progressFillDone = $('#progressFill'); if (progressFillDone) progressFillDone.style.width='100%'; box.style.setProperty('--progress','100%'); setStep(100); }
      setTimeout(()=>{ box.classList.add('hidden'); box.classList.remove('active'); },2200);
      await loadDecks(); await reloadGraph();
    }
  },850);
}
export async function importFile(file){
  if(!state.currentDeckId) await addDeck(); if(!state.currentDeckId||!file) return;
  const r=await api.importCards(state.currentDeckId,file);
  alert(`Импортировано: ${r.imported} из ${r.found}`);
  $('#importModal').classList.remove('active'); await loadDecks(); await reloadGraph();
}

function sourceForCard(card){
  return state.graph.sources.find(s => String(s.id) === String(card.source_node_id || ''));
}
function applyWorkspaceMode(){
  const workspace = $('#workspace');
  const taskView = $('#taskView');
  const app = $('#app');
  if(!workspace || !taskView) return;
  const listMode = state.viewMode === 'list';
  workspace.classList.toggle('list-mode', listMode);
  app?.classList.toggle('task-mode', listMode);
  taskView.classList.toggle('hidden', !listMode);
  const btn = $('#viewModeBtn');
  if(btn) {
    btn.textContent = listMode ? '🕸️' : '✅';
    btn.title = listMode ? 'Вернуться к графу' : 'Открыть TickTick-вид';
    btn.setAttribute('aria-label', btn.title);
  }
}
export function setWorkspaceMode(mode){
  state.viewMode = mode === 'list' ? 'list' : 'canvas';
  try { localStorage.setItem('workspaceViewMode', state.viewMode); } catch(e) {}
  applyWorkspaceMode();
  renderTaskView();
}
export function toggleWorkspaceMode(){
  setWorkspaceMode(state.viewMode === 'list' ? 'canvas' : 'list');
}
function taskGroupMode(){
  const mode = String(state.taskGroupMode || localStorage.getItem('taskGroupMode') || 'source');
  return ['source','type','date','status','model'].includes(mode) ? mode : 'source';
}
function setTaskGroupMode(mode){
  state.taskGroupMode = ['source','type','date','status','model'].includes(mode) ? mode : 'source';
  try { localStorage.setItem('taskGroupMode', state.taskGroupMode); } catch(e) {}
  renderTaskView();
}
async function switchTaskDeck(deckId){
  const id = String(deckId || '');
  if(!id || id === String(state.currentDeckId || '')) return;
  if(!state.decks.some(d => String(d.id) === id)) return;
  state.currentDeckId = id;
  setLocalDeckId(id);
  state.selected.clear();
  state.activeSourceId = null;
  state.inspectDeckId = id;
  window.appApplyDeckTheme?.();
  await reloadGraph();
}
function taskDeckSelectHtml(){
  const decks = state.decks || [];
  if(!decks.length) return '';
  return `<label class="task-deck-switch"><span>Колода</span><select data-task-deck>${decks.map(d => `<option value="${esc(d.id)}" ${String(d.id)===String(state.currentDeckId)?'selected':''}>${esc(d.name || 'Колода')} · ${Number(d.card_count || 0)}</option>`).join('')}</select></label>`;
}
async function studyCardsList(cards){
  const ids = (cards || []).map(c => c?.id).filter(id => id !== undefined && id !== null).map(String);
  if(!ids.length) return;
  state.selected.clear();
  for(const id of ids) state.selected.add(nodeKey('card', id));
  await openStudyMode({ mode:'selected', lockFilters:true });
}
function statusLabel(status){
  const map = { inbox:'Входящие', today:'Сегодня', planned:'Запланировано', done:'Готово' };
  return map[String(status || 'inbox')] || 'Входящие';
}
function dateGroupForCard(c){
  const due = isoDate(c.due_date || '');
  const created = isoDate(c.created_at || '');
  if(due) return { key:`due:${due}`, title:`⏰ ${dateLabel(due)}`, sort:`0:${due}` };
  if(created) return { key:`created:${created}`, title:`🗓️ ${dateLabel(created)}`, sort:`1:${created}` };
  return { key:'no-date', title:'Без даты', sort:'9:' };
}
function taskSourceTitle(sid){
  const s = (state.graph.sources || []).find(x => sourceId(x.id) === sourceId(sid));
  if(s) return `${s.icon || iconForSource(s.source_type)} ${s.title || 'Источник'}`;
  return sid ? '📄 Источник из старых карточек' : '▫️ Без источника';
}
function taskSourceColor(sid){
  const s = (state.graph.sources || []).find(x => sourceId(x.id) === sourceId(sid));
  return s?.color || '#d97706';
}
function buildTaskGroups(){
  const mode = taskGroupMode();
  const groups = new Map();
  const add = (key, title, card, sort='', color='#d97706', source=null) => {
    if(!groups.has(key)) groups.set(key, { key, title, sort:sort || title, color, source, cards:[] });
    if(card) groups.get(key).cards.push(card);
  };
  if(mode === 'source'){
    const sourceRows = derivedSourceRows();
    for(const row of sourceRows) groups.set(`source:${row.id}`, { key:`source:${row.id}`, title:`${row.icon} ${row.title}`, sort:row.source?.created_at || row.title, color:row.color, source:row.source, sourceId:row.id, cards:[...row.cards] });
    for(const c of state.graph.cards || []){
      const sid = sourceId(c.source_node_id || '');
      const k = `source:${sid}`;
      if(!groups.has(k)) groups.set(k, { key:k, title:taskSourceTitle(sid), sort:sid || 'zzz', color:taskSourceColor(sid), sourceId:sid, cards:[] });
      if(!groups.get(k).cards.some(x => String(x.id) === String(c.id))) groups.get(k).cards.push(c);
    }
  } else {
    for(const c of state.graph.cards || []){
      if(mode === 'type') add(`type:${c.card_type || 'basic'}`, `🧩 ${labelCardType(c.card_type)}`, c, labelCardType(c.card_type), taskSourceColor(c.source_node_id));
      if(mode === 'status') add(`status:${c.status || 'inbox'}`, `✅ ${statusLabel(c.status)}`, c, String(['today','planned','inbox','done'].indexOf(c.status || 'inbox')).padStart(2,'0'), taskSourceColor(c.source_node_id));
      if(mode === 'model') add(`model:${c.model || 'unknown'}`, `${c.model_icon || '🤖'} ${c.model || 'Без модели'}`, c, c.model || 'zzz', taskSourceColor(c.source_node_id));
      if(mode === 'date') { const d=dateGroupForCard(c); add(d.key, d.title, c, d.sort, taskSourceColor(c.source_node_id)); }
    }
  }
  return [...groups.values()].filter(g => g.source || g.cards.length).sort((a,b)=>String(a.sort).localeCompare(String(b.sort)) || String(a.title).localeCompare(String(b.title)));
}
function taskCardHtml(c){
  const active = state.selected.has(nodeKey('card', c.id));
  const done = String(c.status || '') === 'done';
  const tags = tagsList(c.tags).slice(0,6);
  const sourceName = taskSourceTitle(c.source_node_id || '').replace(/^\S+\s*/, '');
  const detail = active ? `<div class="task-inline-detail"><div><b>Ответ</b><p>${esc(c.back || 'Нет ответа')}</p></div>${c.source_quote?`<blockquote>${esc(c.source_quote)}</blockquote>`:''}${tags.length?`<div class="tags">${tags.map(t=>`<span class="tag" data-tag="${esc(t)}">#${esc(displayTag(t))}</span>`).join('')}</div>`:''}<div class="task-inline-actions"><button class="secondary" data-edit-card="${esc(c.id)}">✏️ Редактировать</button><button class="secondary" data-study-card="${esc(c.id)}">▶ Повторить</button></div></div>` : '';
  return `<div class="task-card-wrap ${active?'active':''}"><div class="task-card-row ${active?'active':''}" role="button" tabindex="0" data-card="${esc(c.id)}"><button class="task-check ${done?'done':''}" data-card-toggle="${esc(c.id)}" title="${done?'Вернуть во входящие':'Отметить готово'}">${done?'✓':''}</button><span class="task-card-main"><b>${esc(c.front || 'Без вопроса')}</b><small>${esc(c.back || '').slice(0,160)}</small></span><span class="task-card-meta"><span>${esc(labelCardType(c.card_type))}</span><small>${esc(sourceName || '')}</small></span></div>${detail}</div>`;
}
export function renderTaskView(){
  const box = $('#taskView');
  if(!box) return;
  const cards = state.graph.cards || [];
  const sources = state.graph.sources || [];
  if(!cards.length && !sources.length){
    box.innerHTML = '<div class="task-empty">В списке пока нет источников и карточек.</div>';
    return;
  }
  const mode = taskGroupMode();
  const tabs = [
    ['source','📄','Источники'],
    ['type','🧩','Типы'],
    ['date','📅','Даты'],
    ['status','✅','Статус'],
    ['model','🤖','Модель'],
  ];
  const groups = buildTaskGroups();
  const currentDeck = (state.decks || []).find(d => String(d.id) === String(state.currentDeckId));
  const groupSelect = `<label class="task-group-select"><span>Группировка</span><select data-task-group-select>${tabs.map(([m,icon,label])=>`<option value="${m}" ${mode===m?'selected':''}>${icon} ${esc(label)}</option>`).join('')}</select></label>`;
  box.innerHTML = `<div class="task-board-head"><div><h2>✅ Карточки</h2><p>${esc(currentDeck?.name || 'Текущая колода')} · список без боковых панелей: можно менять колоду, группировку и сразу запускать повторение.</p></div><div class="task-board-actions">${taskDeckSelectHtml()}${groupSelect}<button class="secondary" data-task="export" title="Экспорт текущей колоды или выбранного">⇧ Экспорт</button><button class="secondary" data-task="back-canvas" title="Вернуться к canvas">🕸️ Граф</button><button class="secondary" data-task="new-card">＋ Карточка</button></div></div>` + groups.map(group => {
    const sourceTarget = group.sourceId || group.source?.id || '';
    const disabled = group.cards.length ? '' : 'disabled';
    return `<section class="task-source-group" style="--source-accent:${esc(group.color || '#d97706')}"><div class="task-source-headbar"><button class="task-source-title" data-task-source="${esc(sourceTarget)}" title="Открыть источник/группу"><span>${esc(group.title)}</span><small>${group.cards.length} карт.</small></button><div class="task-source-actions"><button class="secondary" data-task-study-group="${esc(group.key)}" ${disabled}>▶ Повторить</button></div></div><div class="task-card-list">${group.cards.map(c => taskCardHtml(c)).join('') || '<div class="task-empty-small">Карточек пока нет</div>'}</div></section>`;
  }).join('');
  box.querySelector('[data-task="new-card"]')?.addEventListener('click', () => openCardCreator({}));
  box.querySelector('[data-task="back-canvas"]')?.addEventListener('click', () => setWorkspaceMode('canvas'));
  box.querySelector('[data-task="export"]')?.addEventListener('click', e => window.appOpenExport?.(e.currentTarget));
  box.querySelector('[data-task-deck]')?.addEventListener('change', async e => { await switchTaskDeck(e.target.value); });
  box.querySelector('[data-task-group-select]')?.addEventListener('change', e => setTaskGroupMode(e.target.value));
  box.querySelectorAll('[data-task-group]').forEach(btn => btn.onclick = () => setTaskGroupMode(btn.dataset.taskGroup));
  box.querySelectorAll('[data-task-study-group]').forEach(btn => btn.onclick = async e => {
    e.preventDefault(); e.stopPropagation();
    const group = groups.find(g => String(g.key) === String(btn.dataset.taskStudyGroup));
    await studyCardsList(group?.cards || []);
  });
  box.querySelectorAll('[data-task-source]').forEach(btn => btn.onclick = () => {
    const id=btn.dataset.taskSource; if(!id) return;
    state.inspectDeckId=null; state.selected.clear(); state.selected.add(nodeKey('source', id));
    renderInspector(); renderSourceList(); renderTaskView();
  });
  const selectCard = id => {
    state.inspectDeckId=null; state.selected.clear(); state.selected.add(nodeKey('card', id));
    renderInspector(); renderSourceList(); renderTaskView();
  };
  box.querySelectorAll('.task-card-row[data-card]').forEach(row => {
    row.addEventListener('click', e => { if(e.target.closest('[data-card-toggle]')) return; selectCard(row.dataset.card); });
    row.addEventListener('keydown', e => { if(e.key === 'Enter' || e.key === ' ') { e.preventDefault(); selectCard(row.dataset.card); } });
  });
  box.querySelectorAll('[data-card-toggle]').forEach(btn => btn.onclick = async e => {
    e.preventDefault(); e.stopPropagation();
    const c = getNode('card', btn.dataset.cardToggle);
    if(!c) return;
    await api.updateCard(c.id, { status: c.status === 'done' ? 'inbox' : 'done' });
    await window.appReloadGraph();
  });
  box.querySelectorAll('[data-edit-card]').forEach(btn => btn.onclick = e => { e.preventDefault(); e.stopPropagation(); const c=getNode('card', btn.dataset.editCard); if(c) openCardEditor(c); });
  box.querySelectorAll('[data-study-card]').forEach(btn => btn.onclick = async e => { e.preventDefault(); e.stopPropagation(); state.selected.clear(); state.selected.add(nodeKey('card', btn.dataset.studyCard)); await openStudyMode({ mode:'selected', lockFilters:true }); });
  box.querySelectorAll('[data-tag]').forEach(t=>bindTagHover(t));
}

function currentExportScope(){
  const cards = selectedCards();
  const sources = selectedSources();
  if (cards.length) return {label:`Экспорт выбранных карточек: ${cards.length}`, cardIds:cards, sourceId:''};
  if (sources.length === 1) {
    const s = state.graph.sources.find(x=>String(x.id)===String(sources[0]));
    const count = state.graph.cards.filter(c=>String(c.source_node_id||'')===String(sources[0])).length;
    return {label:`Экспорт источника: ${count} карт.`, cardIds:[], sourceId:sources[0], title:s?.title||''};
  }
  if (sources.length > 1) {
    const sourceSet = new Set(sources.map(String));
    const ids = state.graph.cards.filter(c=>sourceSet.has(String(c.source_node_id||''))).map(c=>c.id);
    return {label:`Экспорт выбранных источников: ${ids.length} карт.`, cardIds:ids, sourceId:''};
  }
  return {label:'Экспорт текущей колоды', cardIds:[], sourceId:''};
}
function renderExportScope(){
  const label=$('#exportScopeLabel');
  if(label) label.textContent=currentExportScope().label;
}
export function exportDeck(format){
  if(!state.currentDeckId) return;
  const scope=currentExportScope();
  download(exportUrl(state.currentDeckId, format, scope.cardIds, scope.sourceId));
}

export function updateViewStates(){
  const set = (id, value) => { const el=$(id); if(el) el.textContent = value ? '✓' : ''; };
  set('#tagEdgesState', state.prefs.tagEdges);
  set('#compactState', state.prefs.compact);
  set('#hideAnswersState', state.prefs.hideAnswers);
}

export function openGenerateMenuAt(sourceId, x, y){
  const s = state.graph.sources.find(src=>String(src.id)===String(sourceId));
  if(!s) return;
  const old=document.getElementById('quickGenerateMenu');
  if(old) old.remove();
  const plan=estimateCardPlan(s);
  const menu=document.createElement('div');
  menu.id='quickGenerateMenu';
  menu.className='menu-popover quick-generate-menu';
  menu.style.left=`${Math.max(12, Math.min(window.innerWidth-248, x))}px`;
  menu.style.top=`${Math.max(12, Math.min(window.innerHeight-260, y))}px`;
  menu.innerHTML=`<div class="menu-label">Генерация из источника</div>
    <div class="mini-title">${esc(s.title||'Источник')}</div>
    <div class="quick-count-block quick-count-compact">
      <div class="quick-count-title">Объём</div>
      <div class="quick-counts" role="radiogroup" aria-label="Количество карточек"><button type="button" data-count="${plan.min}" aria-pressed="false">Мин<br><b>${plan.min}</b></button><button type="button" class="selected" data-count="${plan.normal}" aria-pressed="true">Норм<br><b>${plan.normal}</b></button><button type="button" data-count="${plan.deep}" aria-pressed="false">Макс<br><b>${plan.deep}</b></button></div>
      <label class="quick-custom-count"><span>Свое</span><input type="text" inputmode="numeric" pattern="[0-9]*" value="${plan.normal}" data-max="${generationMaxCards()}"></label>
    </div>
    <div class="quick-model-stack quick-model-full">${generationModelButtonsHtml('data-model')}</div>
    <label class="quick-prompt-hint"><span>Пожелание</span><textarea rows="2" placeholder="Термины, определения, причины/следствия"></textarea></label>
    <button type="button" class="secondary quick-settings-btn" data-quick-settings title="Открыть панель настроек генерации">⚙ Настройки генерации</button>`;
  let count=plan.normal;
  const countInput = menu.querySelector('.quick-custom-count input');
  const promptInput = menu.querySelector('.quick-prompt-hint textarea');
  const clampCount = value => Math.max(1, Math.min(generationMaxCards(), Number.parseInt(value, 10) || plan.normal));
  const syncCountButtons = (writeInput = true) => {
    const active = String(count);
    menu.querySelectorAll('[data-count]').forEach(b => {
      const isSelected = String(b.dataset.count) === active;
      b.classList.toggle('selected', isSelected);
      b.setAttribute('aria-pressed', isSelected ? 'true' : 'false');
    });
    if(writeInput && countInput && String(countInput.value) !== active) countInput.value = active;
  };
  const normalizeCountInput = () => { count = clampCount(countInput?.value || count); syncCountButtons(true); };
  menu.querySelectorAll('[data-count]').forEach(btn=>btn.onclick=()=>{ count=clampCount(btn.dataset.count); syncCountButtons(true); });
  countInput?.addEventListener('input', () => {
    const raw = String(countInput.value || '').replace(/[^0-9]/g, '');
    if (countInput.value !== raw) countInput.value = raw;
    menu.querySelectorAll('[data-count]').forEach(b => { b.classList.remove('selected'); b.setAttribute('aria-pressed','false'); });
    if (raw !== '') count = clampCount(raw);
  });
  countInput?.addEventListener('change', normalizeCountInput);
  countInput?.addEventListener('blur', normalizeCountInput);
  // Open the inspector with the settings panel visible, so the user can tweak
  // sampler/toggles before picking a model.
  menu.querySelector('[data-quick-settings]')?.addEventListener('click', () => {
    menu.remove();
    state.selected.clear();
    state.selected.add(nodeKey('source', sourceId));
    window.appOpenInspector?.();
    renderGraph();
    // Expand the settings panel if it was collapsed.
    const body = document.querySelector('#genBody');
    if (body && body.classList.contains('gen-collapsed')) {
      body.classList.remove('gen-collapsed');
      try { localStorage.setItem('aifc_gen_collapsed', '0'); } catch(_) {}
    }
    setTimeout(() => document.querySelector('#genPanel')?.scrollIntoView({behavior:'smooth', block:'center'}), 80);
  });
  menu.querySelectorAll('[data-model]').forEach(btn=>btn.onclick=()=>{
    menu.querySelectorAll('[data-model]').forEach(b=>b.classList.remove('selected'));
    btn.classList.add('selected');
    const model=btn.dataset.model;
    count=clampCount(countInput?.value || count);
    const customPrompt = promptInput?.value?.trim() || '';
    setTimeout(()=>{
      menu.remove();
      state.selected.clear();
      state.selected.add(nodeKey('source', sourceId));
      window.appOpenInspector?.();
      renderGraph();
      generateFromSource(sourceId, model, count, { customPrompt });
    }, 90);
  });
  document.body.appendChild(menu);
  setTimeout(()=>window.addEventListener('pointerdown', function close(ev){ if(!ev.target.closest('#quickGenerateMenu')){ menu.remove(); window.removeEventListener('pointerdown', close); }}, {capture:true}),0);
}


function cardTypeLabel(type){ return labelCardType(type); }
let studyQueue = [];
let studyIndex = 0;
let studyAnswerShown = false;
let studySourceId = "";
let studySelectedIds = [];
let studyDateFilter = "";
function setStudyTabActive(mode){
  document.querySelectorAll("#studyStatusTabs [data-study-mode]").forEach(btn => btn.classList.toggle("active", btn.dataset.studyMode === mode));
}
function currentStudyCard(){ return studyQueue[studyIndex] || null; }
function renderStudyCard(){
  const box=$('#studyCardBox');
  const actions=$('#studyAnswerActions');
  const btn=$('#showStudyAnswerBtn');
  const c=currentStudyCard();
  if(!box || !actions || !btn) return;
  if(!c){
    box.innerHTML='<div class="study-empty">Нет карточек для повторения.</div>';
    actions.classList.add('hidden'); btn.classList.add('hidden');
    $('#studyMeta') && ($('#studyMeta').textContent='Готово');
    return;
  }
  btn.classList.remove('hidden');
  actions.classList.toggle('hidden', !studyAnswerShown);
  btn.textContent = studyAnswerShown ? 'Скрыть ответ' : 'Показать ответ';
  $('#studyMeta') && ($('#studyMeta').textContent=`${studyIndex+1}/${studyQueue.length} · ${cardTypeLabel(c.card_type)} · ${c.model_icon||''}`);
  const answer = studyAnswerShown ? `<div class="study-answer"><b>Ответ</b><p>${esc(c.back||'')}</p>${c.mnemonic?`<p class="mnemonic"><b>Мнемоника:</b> ${esc(c.mnemonic)}</p>`:''}${c.source_quote?`<blockquote>${esc(c.source_quote)}</blockquote>`:''}</div>` : '';
  box.innerHTML=`<div class="study-front"><span class="type-chip">${esc(cardTypeLabel(c.card_type))}</span><h2>${esc(c.front||'')}</h2><div class="tags">${tagsList(c.tags).map(t=>`<span class="tag" data-tag="${esc(t)}">#${esc(displayTag(t))}</span>`).join('')}</div></div>${answer}`;
  box.querySelectorAll('[data-tag]').forEach(t=>bindTagHover(t));
}
export async function openStudyMode(opts={}){
  if(!state.currentDeckId) return;
  const modal=$('#studyModal');
  if(!modal) return;
  studySourceId = opts.source_id || '';
  studySelectedIds = opts.mode === 'selected' ? selectedCards().map(String) : [];
  if(opts.mode && $('#studyMode')) $('#studyMode').value=opts.mode;
  studyDateFilter = opts.date || '';
  setStudyTabActive($('#studyMode')?.value || 'due');
  if(opts.card_type && $('#studyType')) $('#studyType').value=opts.card_type;
  if(opts.model_kind && $('#studyModel')) $('#studyModel').value=opts.model_kind;
  modal.classList.toggle('study-locked', !!opts.lockFilters);
  modal.classList.add('active');
  await loadStudyQueue();
}
async function loadStudyQueue(){
  const opts={mode:$('#studyMode')?.value||'due', card_type:$('#studyType')?.value||'', model_kind:$('#studyModel')?.value||'', source_id:studySourceId||'', date:studyDateFilter||'', limit:'80'};
  setStudyTabActive(opts.mode === 'date' ? 'date' : opts.mode);
  if(studySelectedIds.length){
    const ids = new Set(studySelectedIds);
    studyQueue = (state.graph.cards || []).filter(c => ids.has(String(c.id)));
    studyIndex=0; studyAnswerShown=false; renderStudyCard();
    return;
  }
  const data=await api.studyQueue(state.currentDeckId, opts);
  studyQueue=data.cards||[]; studyIndex=0; studyAnswerShown=false; renderStudyCard();
}
export function bindStudyMode(){
  $('#reloadStudyBtn') && ($('#reloadStudyBtn').onclick=loadStudyQueue);
  ['studyMode','studyType','studyModel'].forEach(id=>{ const el=$('#'+id); if(el) el.onchange=async()=>{
    if(id==='studyMode') {
      if(el.value === 'date') {
        const value = prompt('Дата повторения в формате ГГГГ-ММ-ДД', studyDateFilter || window.appLocalYMD?.(new Date()) || '');
        if(!value) { el.value = 'due'; studyDateFilter=''; }
        else studyDateFilter = window.appLocalYMD ? window.appLocalYMD(value) : value;
      } else studyDateFilter='';
    }
    await loadStudyQueue();
  }; });
  document.querySelectorAll('#studyStatusTabs [data-study-mode]').forEach(btn=>btn.onclick=async()=>{
    const mode = btn.dataset.studyMode;
    const select = $('#studyMode');
    if(mode === 'date') {
      const value = prompt('Дата повторения в формате ГГГГ-ММ-ДД', studyDateFilter || window.appLocalYMD?.(new Date()) || '');
      if(!value) return;
      studyDateFilter = window.appLocalYMD ? window.appLocalYMD(value) : value;
      if(select) select.value = 'date';
    } else {
      studyDateFilter = '';
      if(select) select.value = mode;
    }
    setStudyTabActive(mode);
    await loadStudyQueue();
  });
  $('#showStudyAnswerBtn') && ($('#showStudyAnswerBtn').onclick=()=>{ studyAnswerShown=!studyAnswerShown; renderStudyCard(); });
  $('#studyAnswerActions')?.querySelectorAll('[data-rate]').forEach(btn=>btn.onclick=async()=>{
    const c=currentStudyCard(); if(!c) return;
    await api.reviewCard(c.id, btn.dataset.rate);
    studyQueue.splice(studyIndex,1);
    if(studyIndex>=studyQueue.length) studyIndex=Math.max(0,studyQueue.length-1);
    studyAnswerShown=false;
    renderStudyCard();
    await window.appReloadGraph?.();
  });
}

export function openCommand(){
  const modal=$('#commandModal');
  if(!modal) return;
  modal.classList.add('active');
  const input = $('#commandInput');
  if(input) input.value='';
  renderCommands('');
  setTimeout(()=>input?.focus(),50);
}
const commands=[
  { id:'add-source', title:'Создать: новый источник', hint:'Текст / URL / PDF / YouTube / Wikipedia / файл', keywords:'создать добавить источник импорт текст ссылка pdf youtube wikipedia файл' },
  { id:'create-card', title:'Создать: карточка вручную', hint:'Новая карточка в текущей колоде', keywords:'создать карточка вручную новая card' },
  { id:'add-deck', title:'Создать: новая колода', hint:'Добавить новую колоду', keywords:'создать новая колода deck' },
  { id:'import', title:'Импорт: Anki / Quizlet / CSV', hint:'Открыть импорт карточек в текущую колоду', keywords:'импорт anki quizlet csv tsv apkg' },
  { id:'export', title:'Экспорт: текущая колода / выбранное', hint:'Anki .apkg / Quizlet TSV / CSV / PDF / JSON', keywords:'экспорт apkg quizlet csv pdf json' },
  { id:'study', title:'Повторение: открыть режим', hint:'К повторению, входящие, сегодня, по дате', keywords:'повторение study review due inbox today date' },
  { id:'view-list', title:'Режим просмотра: TickTick список', hint:'Полноэкранный список карточек без боковых панелей', keywords:'ticktick список list режим просмотр' },
  { id:'view-canvas', title:'Режим просмотра: граф canvas', hint:'Вернуться к канвасу и связям', keywords:'graph canvas граф канвас режим просмотр' },
  { id:'toggle-view', title:'Режим просмотра: переключить', hint:'Быстро сменить TickTick ↔ граф', keywords:'переключить toggle ticktick граф' },
  { id:'task-group-source', title:'TickTick: группировка по источникам', hint:'Список группируется по источникам', keywords:'ticktick группировка источники source' },
  { id:'task-group-type', title:'TickTick: группировка по типам карточек', hint:'Определения, факты, понимание и т.д.', keywords:'ticktick группировка типы карточек' },
  { id:'task-group-date', title:'TickTick: группировка по датам', hint:'Дата повторения / создания', keywords:'ticktick группировка даты календарь' },
  { id:'task-group-status', title:'TickTick: группировка по статусам', hint:'Входящие, сегодня, запланировано, готово', keywords:'ticktick группировка статус' },
  { id:'task-group-model', title:'TickTick: группировка по моделям', hint:'Gemma / SuperGemma / Импорт', keywords:'ticktick группировка модель gemma' },
  { id:'layout-source', title:'Разметка: источник → карточки', hint:'Разложить карточки вокруг источников', keywords:'разметка layout source cards источник карточки' },
  { id:'layout-review', title:'Разметка: план повторения по датам', hint:'Разложить по датам повторения', keywords:'разметка даты timeline review' },
  { id:'layout-model', title:'Разметка: по моделям', hint:'Сгруппировать по модели генерации', keywords:'разметка модели model groups' },
  { id:'layout-card-types', title:'Разметка: по типам карточек', hint:'Определение, факт, понимание и т.д.', keywords:'разметка типы карточек card types' },
  { id:'fit', title:'Вид: сбросить масштаб и позицию', hint:'Показать весь холст на экране', keywords:'fit reset zoom вид' },
  { id:'date-filter', title:'Вид: показать выбранную дату', hint:'Подсветить карточки по дате повторения', keywords:'дата фильтр highlight due' },
  { id:'toggle-tag-edges', title:'Вид: связи по тегам', hint:'Показать / скрыть связи карточек по общим тегам', keywords:'теги связи tag edges' },
  { id:'toggle-compact', title:'Вид: компактные карточки', hint:'Показать карточки компактнее', keywords:'compact компактные карточки' },
  { id:'toggle-hide-answers', title:'Вид: скрывать ответы', hint:'Размыть ответы на карточках до наведения', keywords:'hide answers скрывать ответы' },
  { id:'theme-light', title:'Тема: светлая', hint:'Переключить интерфейс на светлую тему', keywords:'тема светлая light theme' },
  { id:'theme-dark', title:'Тема: тёмная', hint:'Переключить интерфейс на тёмную тему', keywords:'тема темная dark theme' },
  { id:'sidebar-open', title:'Панели: открыть левую панель', hint:'Показать колоды, источники и теги', keywords:'панель sidebar открыть левую' },
  { id:'sidebar-close', title:'Панели: скрыть левую панель', hint:'Освободить больше места на экране', keywords:'панель sidebar скрыть левую' },
  { id:'inspector-open', title:'Панели: открыть правый инспектор', hint:'Показать настройки выбранного объекта', keywords:'панель inspector открыть правую' },
  { id:'inspector-close', title:'Панели: скрыть правый инспектор', hint:'Спрятать правую панель', keywords:'панель inspector скрыть правую' },
  { id:'delete-selection', title:'Действие: удалить выбранное', hint:'Удалить выделенные карточки / источники', keywords:'удалить выбранное delete selection' },
];
export function renderCommands(q=''){
  const box=$('#commandList');
  if(!box) return;
  const query=String(q||'').trim().toLowerCase();
  const rows=!query ? commands : commands.filter(c => `${c.title} ${c.hint||''} ${c.keywords||''}`.toLowerCase().includes(query));
  if(!rows.length){
    box.innerHTML = '<div class="command-empty">Ничего не найдено</div>';
    return;
  }
  box.innerHTML=rows.map((c,i)=>`<button class="command-item ${i===0?'active':''}" data-cmd="${esc(c.id)}"><span>${esc(c.title)}</span></button>`).join('');
  box.querySelectorAll('[data-cmd]').forEach(b=>b.onclick=()=>runCommand(b.dataset.cmd));
}
export async function runCommand(cmd){
  $('#commandModal').classList.remove('active');
  if(cmd==='add-source') openSourceModalAt();
  if(cmd==='create-card') openCardCreator({});
  if(cmd==='add-deck') await window.appAddDeck?.();
  if(cmd==='import') $('#importModal')?.classList.add('active');
  if(cmd==='export') window.appOpenExport?.();
  if(cmd==='study') await openStudyMode();
  if(cmd==='view-list') setWorkspaceMode('list');
  if(cmd==='view-canvas') setWorkspaceMode('canvas');
  if(cmd==='toggle-view') toggleWorkspaceMode();
  if(cmd==='task-group-source'){ setWorkspaceMode('list'); setTaskGroupMode('source'); }
  if(cmd==='task-group-type'){ setWorkspaceMode('list'); setTaskGroupMode('type'); }
  if(cmd==='task-group-date'){ setWorkspaceMode('list'); setTaskGroupMode('date'); }
  if(cmd==='task-group-status'){ setWorkspaceMode('list'); setTaskGroupMode('status'); }
  if(cmd==='task-group-model'){ setWorkspaceMode('list'); setTaskGroupMode('model'); }
  if(cmd==='layout-source') await autoLayoutLocal('sourceCards');
  if(cmd==='layout-review') await autoLayoutLocal('reviewTimeline');
  if(cmd==='layout-model') await autoLayoutLocal('modelGroups');
  if(cmd==='layout-card-types') await autoLayoutLocal('cardTypeGroups');
  if(cmd==='fit') fitView();
  if(cmd==='date-filter') await window.appSelectCardsByDatePrompt?.();
  if(cmd==='toggle-tag-edges'){ state.prefs.tagEdges=!state.prefs.tagEdges; window.appSavePrefs?.(); }
  if(cmd==='toggle-compact'){ state.prefs.compact=!state.prefs.compact; window.appSavePrefs?.(); }
  if(cmd==='toggle-hide-answers'){ state.prefs.hideAnswers=!state.prefs.hideAnswers; window.appSavePrefs?.(); }
  if(cmd==='theme-light'){ state.prefs.theme='light'; window.appSetTheme?.('light'); window.appSavePrefs?.(); }
  if(cmd==='theme-dark'){ state.prefs.theme='dark'; window.appSetTheme?.('dark'); window.appSavePrefs?.(); }
  if(cmd==='sidebar-open') $('#openSidebarBtn')?.click();
  if(cmd==='sidebar-close') $('#closeSidebarBtn')?.click();
  if(cmd==='inspector-open') window.appOpenInspector?.();
  if(cmd==='inspector-close') window.appCloseInspector?.();
  if(cmd==='delete-selection') await window.appDeleteSelection?.();
}

