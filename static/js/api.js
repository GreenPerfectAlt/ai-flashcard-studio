export async function request(path, options = {}) {
  const opts = { ...options };
  opts.headers = opts.headers || {};
  if (opts.body && !(opts.body instanceof FormData) && typeof opts.body !== 'string') {
    opts.headers['Content-Type'] = 'application/json';
    opts.body = JSON.stringify(opts.body);
  }
  const res = await fetch(path, opts);
  if (!res.ok) {
    let msg = `${res.status} ${res.statusText}`;
    try { const data = await res.json(); msg = data.detail || data.message || msg; } catch {}
    throw new Error(msg);
  }
  const ct = res.headers.get('content-type') || '';
  if (ct.includes('application/json')) return await res.json();
  return res;
}
export const api = {
  config: () => request('/api/config'),
  decks: () => request('/api/decks'),
  createDeck: (name) => request('/api/decks', { method:'POST', body:{ name } }),
  updateDeck: (id, payload) => request(`/api/decks/${id}`, { method:'PUT', body:payload }),
  deleteDeck: (id) => request(`/api/decks/${id}`, { method:'DELETE' }),
  normalizeDeck: (id) => request(`/api/decks/${id}/cards/normalize`, { method:'POST' }),
  graph: (deckId) => request(`/api/decks/${deckId}/graph`),
  progress: (deckId) => request(`/api/decks/${deckId}/progress`),
  createSource: (deckId, payload) => request(`/api/decks/${deckId}/sources`, { method:'POST', body:payload }),
  getSource: (id) => request(`/api/sources/${id}`),
  updateSource: (id, payload) => request(`/api/sources/${id}`, { method:'PUT', body:payload }),
  deleteSource: (id, cascade=true) => request(`/api/sources/${id}?cascade=${cascade}`, { method:'DELETE' }),
  deleteSourceCards: (id) => request(`/api/sources/${id}/cards`, { method:'DELETE' }),
  createCard: (deckId, payload) => request(`/api/decks/${deckId}/cards`, { method:'POST', body:payload }),
  updateCard: (id, payload) => request(`/api/cards/${id}`, { method:'PUT', body:payload }),
  deleteCard: (id) => request(`/api/cards/${id}`, { method:'DELETE' }),
  savePositions: (nodes) => request('/api/graph/positions', { method:'POST', body:{ nodes } }),
  deleteNodes: (cards, sources) => request('/api/graph/delete', { method:'POST', body:{ cards, sources, cascade_sources:true } }),
  autoLayout: (deckId) => request(`/api/decks/${deckId}/layout`, { method:'POST' }),
  parseUrl: (url) => request('/api/parse/url', { method:'POST', body:{ url } }),
  parseYoutube: (url) => request('/api/parse/youtube', { method:'POST', body:{ url } }),
  generate: (deckId, payload) => request(`/api/decks/${deckId}/cards/generate`, { method:'POST', body:payload }),
  modelList: () => request('/api/model/list'),
  modelCurrent: () => request('/api/model/current'),
  modelSwitch: (model_name, backend = null) => request('/api/model/switch', { method:'POST', body:{ model_name, backend } }),
  modelPreload: (model_name, backend = null) => request('/api/model/preload', { method:'POST', body:{ model_name, backend } }),
  uploadFile: (deckId, file) => { const fd = new FormData(); fd.append('file', file); return request(`/api/decks/${deckId}/upload-file`, { method:'POST', body:fd }); },
  importCards: (deckId, file) => { const fd = new FormData(); fd.append('file', file); return request(`/api/decks/${deckId}/import/cards`, { method:'POST', body:fd }); },
  search: (q) => request(`/api/search?${new URLSearchParams({q})}`),
  uploadSourceMedia: (sourceId, file) => { const fd = new FormData(); fd.append('file', file); return request(`/api/sources/${sourceId}/media`, { method:'POST', body:fd }); },
  deleteSourceMedia: (sourceId, index) => request(`/api/sources/${sourceId}/media/${index}`, { method:'DELETE' }),
  deleteSourceMediaKey: (sourceId, key) => request(`/api/sources/${sourceId}/media?key=${encodeURIComponent(key || '')}`, { method:'DELETE' }),
  uploadCardImage: (cardId, file) => { const fd = new FormData(); fd.append('file', file); return request(`/api/cards/${cardId}/image`, { method:'POST', body:fd }); },
  studyQueue: (deckId, opts={}) => request(`/api/decks/${deckId}/study/queue?${new URLSearchParams(opts)}`),
  studyStats: (deckId) => request(`/api/decks/${deckId}/study/stats`),
  reviewCard: (id, rating) => request(`/api/cards/${id}/review`, { method:'POST', body:{ rating } }),
};
export function download(url) { window.location.href = url; }
export function exportUrl(deckId, format, cardIds=[], sourceId='') {
  const base = `/api/decks/${deckId}/export/${format}`;
  const params = new URLSearchParams();
  if (cardIds && cardIds.length) params.set('card_ids', cardIds.join(','));
  else if (sourceId) params.set('source_id', sourceId);
  const qs = params.toString();
  return qs ? `${base}?${qs}` : base;
}
