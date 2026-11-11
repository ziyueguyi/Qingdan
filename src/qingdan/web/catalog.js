/** Bounded catalog cache: visible page plus adjacent pages, five rows per request. */
import { call } from './api.js';
export const PAGE_SIZE = 5;
export const ROW_HEIGHT = 36;
export const catalogs = {
  group: { page: 0, total: 0, pages: new Map(), pending: new Map(), generation: 0 },
  tag: { page: 0, total: 0, pages: new Map(), pending: new Map(), generation: 0 },
  filter: { page: 0, total: 0, pages: new Map(), pending: new Map(), generation: 0 },
};
let changed = () => {};
export function subscribeCatalog(fn) { changed = fn; }
export function catalogItems(kind) {
  return [...catalogs[kind].pages.entries()].sort((a,b)=>a[0]-b[0]).flatMap(([,items])=>items);
}
export async function loadCatalogPage(kind, page) {
  const c = catalogs[kind];
  if (page < 0 || (page > 0 && page * PAGE_SIZE >= c.total)) return;
  if (c.pages.has(page)) return;
  if (c.pending.has(page)) return c.pending.get(page);
  const generation = c.generation;
  const request = (async () => {
    const result = await call(kind + '.list', { limit: PAGE_SIZE, offset: page * PAGE_SIZE });
    if (generation !== c.generation) return;
    c.total = result.total;
    c.pages.set(page, result.items);
    for (const key of c.pages.keys()) if (Math.abs(key - c.page) > 1) c.pages.delete(key);
    changed(kind);
  })();
  c.pending.set(page, request);
  try { await request; } finally { if (generation === c.generation) c.pending.delete(page); }
}
export async function refreshCatalogs() {
  await Promise.all(Object.keys(catalogs).map(async kind => {
    const c = catalogs[kind];
    c.generation++;
    const generation = c.generation;
    c.pages.clear(); c.pending.clear();
    // A deletion can remove the last page. Clamp it after receiving the count.
    const result = await call(kind + '.list', { limit: PAGE_SIZE, offset: c.page * PAGE_SIZE });
    if (generation !== c.generation) return;
    c.total = result.total;
    c.page = Math.min(c.page, Math.max(0, Math.ceil(c.total / PAGE_SIZE) - 1));
    if (result.offset === c.page * PAGE_SIZE) c.pages.set(c.page, result.items);
    else await loadCatalogPage(kind, c.page);
    await Promise.all([loadCatalogPage(kind,c.page - 1), loadCatalogPage(kind,c.page + 1)]);
  }));
}
export async function scrollCatalog(kind, scrollTop) {
  const c = catalogs[kind];
  const page = Math.min(Math.max(0, Math.floor(scrollTop / (PAGE_SIZE * ROW_HEIGHT))), Math.max(0, Math.ceil(c.total/PAGE_SIZE)-1));
  c.page = page;
  await loadCatalogPage(kind,page);
  await Promise.all([loadCatalogPage(kind,page-1),loadCatalogPage(kind,page+1)]);
}
