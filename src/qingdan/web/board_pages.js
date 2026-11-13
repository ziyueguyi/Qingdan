import {TaskPager} from './task_pages.js';

export const BOARD_CARD_SIZE = 20;
export const BOARD_COLUMN_SIZE = 5;

export class BoardPager {
  constructor(fetchPage) {
    this.fetchPage = fetchPage;
    this.pagers = new Map();
    this.window = 0;
    this.visibleOffset = null;
    this.loadSequence = 0;
    this.groups = new TaskPager(async (feature, params) => {
      const {offset,limit,...scope} = params;
      const root = await fetchPage(feature, {...scope,limit:BOARD_CARD_SIZE,offset:0,
        columns_limit:limit,columns_offset:offset});
      return {items:root.columns,board:{...root,items:undefined},offset:root.columns_offset,
        limit:BOARD_COLUMN_SIZE,total:root.columns_total,has_more:root.columns_has_more,
        change_token:root.change_token};
    }, BOARD_COLUMN_SIZE);
  }

  get key() { return this.groups.key; }

  configure(params, revision) {
    const changed = this.groups.configure('view.board', params, revision);
    if (changed) {
      this.loadSequence++;
      this.pagers.clear(); this.visibleOffset = null;
      this.params = {...params}; this.revision = revision;
    }
    return changed;
  }

  async get(offset) {
    const key = this.key, sequence = ++this.loadSequence;
    let result;
    try { result = await this.groups.get(offset); }
    catch (error) { if (key!==this.key || sequence!==this.loadSequence) return null; throw error; }
    if (key !== this.key || sequence!==this.loadSequence) return null;
    if (this.visibleOffset !== result.offset) { this.visibleOffset=result.offset; this.window++; }
    const visible = new Set(result.board.columns.map(c=>c.key));
    for (const id of this.pagers.keys()) if (!visible.has(id)) this.pagers.delete(id);
    const names = {...result.board.list_names};
    const columns = await Promise.all(result.board.columns.map(async column => {
      let pager = this.pagers.get(column.key);
      if (!pager) {
        pager = new TaskPager(async (feature,params) => {
          const root = await this.fetchPage(feature,params);
          return {...root.columns[0],list_names:root.list_names,change_token:root.change_token};
        }, BOARD_CARD_SIZE);
        pager.configure('view.board',{...this.params,column:column.key},this.revision);
        pager.seed({...column,list_names:result.board.list_names,change_token:result.change_token});
        this.pagers.set(column.key,pager);
      }
      const page = await pager.get(pager.center);
      if (key!==this.key || sequence!==this.loadSequence || this.pagers.get(column.key)!==pager) return null;
      Object.assign(names,page.list_names || {});
      pager.warm(page.offset,page);
      return {...page,loading:false};
    }));
    if (key !== this.key || sequence!==this.loadSequence) return null;
    this.groups.warm(result.offset,result);
    return {...result.board,columns,list_names:names,key:this.key,window:this.window,columns_loading:false};
  }

  async cards(column,offset) {
    const pager = this.pagers.get(column);
    if (!pager) { const error=new Error('PAGE_CHANGED');error.code='PAGE_CHANGED';throw error; }
    const result = await pager.get(offset);
    if (this.pagers.get(column) === pager) pager.warm(result.offset,result);
    return result;
  }
}
