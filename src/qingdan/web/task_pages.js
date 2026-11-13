/** Bounded task-page cache. In-flight requests belong to a query + revision. */
export const TASK_PAGE_SIZE = 50;

export class TaskPager {
  constructor(fetchPage, size = TASK_PAGE_SIZE) {
    this.fetchPage = fetchPage;
    this.size = size;
    this.generation = 0;
    this.key = null;
    this.cache = new Map();
    this.pending = new Map();
    this.center = 0;
  }

  configure(feature, params, revision) {
    const key = JSON.stringify([feature, params, revision]);
    if (key === this.key) return false;
    this.key = key;
    this.feature = feature;
    this.params = {...params};
    this.revision = JSON.stringify(revision);
    this.generation++;
    this.cache.clear();
    this.pending.clear();
    this.center = 0;
    return true;
  }

  trim() {
    while (this.cache.size > 3) {
      const farthest = [...this.cache.keys()].sort((a,b)=>Math.abs(b-this.center)-Math.abs(a-this.center))[0];
      this.cache.delete(farthest);
    }
  }

  async get(offset, prefetch = false) {
    offset = Math.max(0, Math.floor(offset / this.size) * this.size);
    if (!prefetch) { this.center = offset; this.trim(); }
    if (this.cache.has(offset)) return this.cache.get(offset);
    if (this.pending.has(offset)) return this.pending.get(offset);
    const generation = this.generation, revision = this.revision;
    // Capture the descriptor before another configure() changes this instance.
    const feature = this.feature, params = {...this.params};
    const request = Promise.resolve().then(()=>this.fetchPage(feature, {...params, limit:this.size, offset}));
    const result = request.then(page => {
      if (JSON.stringify(page.change_token) !== revision) {
        const error = new Error("PAGE_CHANGED"); error.code = "PAGE_CHANGED"; throw error;
      }
      if (generation === this.generation) {
        this.cache.set(offset, page); this.trim();
      }
      return page;
    }).finally(()=>{
      if (this.pending.get(offset) === result) this.pending.delete(offset);
    });
    this.pending.set(offset, result);
    return result;
  }

  warm(offset, page) {
    if (page.has_more) this.get(offset + this.size, true).catch(()=>{});
    if (offset > 0) this.get(offset - this.size, true).catch(()=>{});
  }
}
