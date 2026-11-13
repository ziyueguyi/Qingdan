/**
 * 状态与数据加载。渲染只读 state，写操作一律走 mutate() → 重新拉取。
 *
 * 两条刻意的设计：
 * 1. **不做乐观更新**。UI 先改本地再等后端返回，一旦后端算出的 rev 与本地猜测不同，
 *    撤销和同步判断都会错。清单量级下等一次本地 HTTP 往返不可感知，正确性优先。
 * 2. **竞态保护**。视图切换和写操作并发时，慢响应会覆盖新视图；用自增序号丢弃过期结果。
 */
import { call, ApiError } from "./api.js";
import { refreshCatalogs, catalogItems } from "./catalog.js";
import { TaskPager, TASK_PAGE_SIZE } from "./task_pages.js";
import { BoardPager, BOARD_CARD_SIZE, BOARD_COLUMN_SIZE } from "./board_pages.js";
import { t } from "./i18n.js";

const taskPager = new TaskPager(call);
const boardPager = new BoardPager(call);
const boardRequests = new Map();
let boardRequestSequence = 0;
const taskQueryKey = () => JSON.stringify([state.view,state.layout,state.searchQ,state.group,state.sort,state.reverse]);

function selectedTaskScope() {
  const v = state.view;
  let scope = {name:v === 'inbox' ? 'all' : v,include_done:v === 'done'};
  if (v.startsWith('group:')) scope = {name:'all',list_id:v.slice(6)};
  else if (v.startsWith('tag:')) scope = {name:'all',tag:v.slice(4)};
  else if (v.startsWith('filter:')) scope = {name:'all',filter_id:v.slice(7)};
  return scope;
}

function boardNames(board) {
  const names = {...board.list_names};
  for (const column of board.columns) Object.assign(names,column.list_names || {});
  for (const item of catalogItems('group')) names[item.id] = item.data.name;
  return names;
}

function keepBoardRequests(board) {
  const visible = new Set(board.columns.map(c=>c.key));
  for (const key of boardRequests.keys()) if (typeof key==='string' && !visible.has(key)) boardRequests.delete(key);
}

export const state = {
  booted: false,
  fatal: null,
  busy: false,

  appPrefs: {},          // theme / accent / size / lang —— 设备本地
  viewPrefs: {},         // view / layout / group / sort / colorBy —— 设备本地

  view: "today",
  nav: "task",            // 图标条高亮：task / cal / stat（没实现的功能不占位）
  layout: "list",
  group: "date",
  sort: "smart",
  reverse: false,
  colorBy: "prio",
  searchQ: "",

  tasks: [],
  taskPage: null,
  groups: [],
  groupNames: {},
  tags: [],
  filters: [],
  filterTitle: null,
  folders: [],
  countdowns: [],
  board: null,
  calendar: null,
  calendarMonth: null,
  stats: null,
  health: null,
  counts: {},            // 侧栏徽标，来自 view.counts（不能用当前视图自己数）
  buckets: [],           // 后端算好的分组桶，前端不再自己实现一遍
  alertConfig: null,     // 告警配置（脱敏后的），来自 alert.config_get
  alertStatus: null,     // 待触发数、渠道状态，来自 alert.status
  backupStatus: null,    // 最近备份与是否过期，来自 backup.status
  backups: [],           // 备份列表，来自 backup.list
  autostart: null,       // 开机自启状态，来自 app.autostart_get
  auth: null,            // 登录态（login / repo / clock 偏差），来自 auth.status
  syncStatus: null,      // 引擎状态（mode / pending / conflicts / push_blocked_key）
  syncProgress: null,
  autoSync: null,

  selected: null,        // 当前详情任务 id
  detail: null,          // task.get 结果
  notes: [],
  children: [],

  bell: true,
};

const subs = new Set();
export function subscribe(fn) { subs.add(fn); return () => subs.delete(fn); }
export function notify() { subs.forEach((fn) => fn(state)); }

export function patch(p) { Object.assign(state, p); notify(); }

let seq = 0;
function fresh() { const my = ++seq; return () => my === seq; }

/** 统一写操作：调用接口 → 重新加载 → 错误上抛给 UI。 */
export async function mutate(feature, params = {}) {
  patch({ busy: true });
  try {
    const data = await call(feature, params);
    await loadAll({ keepDetail: feature.startsWith("task.set") || feature === "task.update" });
    return data;
  } catch (e) {
    notify();
    throw e;
  } finally {
    patch({ busy: false });
  }
}

export async function loadAll(opts = {}) {
  const ok = fresh();
  const list = (r) => r.items || [];          // 后端集合类接口统一返回 {total, items}
  if (state.view === 'all') state.view = 'inbox';
  const query = taskQueryKey();
  let [health, , folders, countdowns, counts, stats] = await Promise.all([
    call("app.health"),
    refreshCatalogs(),
    call("folder.list"),
    call("countdown.list"),
    call("view.counts"),
    call("stats.today"),
  ]);
  let tasks = [];
  let board = null;
  let calendar = null;
  let buckets = [];
  let groupNames = {};
  let taskPage = null;
  const current = () => ok() && query === taskQueryKey() && (!opts.guard || opts.guard());
  if (!current()) return;
  async function paged(feature, params) {
    for (let attempt=0; attempt<3; attempt++) {
      if (!current()) return null;
      if ('today' in params) params = {...params, today:health.today};
      const changed = taskPager.configure(feature, params, health.task_token);
      const offset = changed || opts.pageReset || state.taskPage?.key !== taskPager.key ? 0 : state.taskPage.offset;
      try {
        let result = await taskPager.get(offset);
        if (!current()) return null;
        // Deleting the last row of the last page must not strand an empty page.
        if (!result.items.length && result.total && offset) result = await taskPager.get(Math.floor((result.total-1)/TASK_PAGE_SIZE)*TASK_PAGE_SIZE);
        if (!current()) return null;
        taskPage = {...result, items:undefined, groups:undefined, list_names:undefined,
                    key:taskPager.key, query, loading:false};
        taskPager.warm(result.offset, result);
        return result;
      } catch (error) {
        if (!current()) return null;
        if (error.code !== 'PAGE_CHANGED') throw error;
        health = await call('app.health');
      }
    }
    if (!current()) return null;
    throw new ApiError('PAGE_CHANGED', t('task_page_changed'), {}, 409);
  }

  async function loadBoard() {
    for (let attempt=0; attempt<3; attempt++) {
      if (!current()) return null;
      const by = ["status","group","priority"].includes(state.group) ? state.group : "status";
      const params = {...selectedTaskScope(),group_by:by,sort:state.sort,reverse:state.reverse,today:health.today};
      const changed = boardPager.configure(params,health.task_token);
      const offset = changed || opts.boardReset || state.board?.key!==boardPager.key ? 0 : state.board.columns_offset;
      try {
        const result = await boardPager.get(offset);
        if (!current() || !result) return null;
        keepBoardRequests(result);
        return result;
      } catch (error) {
        if (!current()) return null;
        if (error.code!=='PAGE_CHANGED') throw error;
        health = await call('app.health');
      }
    }
    if (!current()) return null;
    throw new ApiError('PAGE_CHANGED',t('task_page_changed'),{},409);
  }

  if (state.searchQ) {
    const r = await paged("search.query", { q: state.searchQ });
    if (!r) return;
    tasks = r.items;
  } else if (state.layout === "board") {
    board = await loadBoard();
    if (!board) return;
    tasks = board.columns.flatMap(c=>c.items);
    groupNames = boardNames(board);
  } else if (state.layout === "calendar") {
    const month = state.calendarMonth ? new Date(state.calendarMonth + "T12:00:00") : new Date();
    month.setDate(1);
    const first = new Date(month);
    first.setDate(1 - (first.getDay() + 6) % 7);
    const dateText = d => `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,"0")}-${String(d.getDate()).padStart(2,"0")}`;
    calendar = await call("view.calendar", { start: dateText(first), days: 42, list_id: state.view.startsWith("group:") ? state.view.slice(6) : null });
    calendar.month = dateText(month);
    calendar.cells.forEach(c => { c.in_month = c.date.slice(0,7) === calendar.month.slice(0,7); });
    tasks = [];
  } else if (state.view.startsWith("filter:")) {
    // 自定义过滤器有自己的条件求值，不走内置视图
    const r = await paged("filter.tasks", {
      item_id: state.view.slice(7), group: state.group, sort: state.sort, reverse: state.reverse, today:health.today });
    if (!r) return;
    tasks = r.items;
    buckets = r.groups;
    groupNames = r.list_names || {};
  } else {
    // 复合视图：group:<id> / tag:<name> 用内置 all 视图 + 参数过滤
    const v = state.view;
    let name = v === 'inbox' ? 'all' : v, params = {};
    if (v.startsWith("group:")) { name = "all"; params = { list_id: v.slice(6) }; }
    else if (v.startsWith("tag:")) { name = "all"; params = { tag: v.slice(4) }; }
    const r = await paged("view.query", {
      name, ...params, group: state.group, sort: state.sort,
      reverse: state.reverse, include_done: v === "done", today:health.today,
    });
    if (!r) return;
    tasks = r.items;
    buckets = r.groups;
    groupNames = r.list_names || {};
  }
  for (const item of catalogItems("group")) groupNames[item.id] = item.data.name;
  if (state.view.startsWith("group:")) {
    const id = state.view.slice(6);
    if (!groupNames[id]) {
      try { groupNames[id] = (await call("group.get", {item_id:id})).data.name; }
      catch (error) { if (error.code !== "NOT_FOUND") throw error; }
    }
  }
  if (!current()) return;
  let filterTitle = null;
  if (state.view.startsWith('filter:') && !catalogItems('filter').some(f=>f.id===state.view.slice(7))) {
    const selectedFilter = await call('filter.get',{item_id:state.view.slice(7)});
    filterTitle = selectedFilter.data.name;
  }
  if (!current()) return;
  patch({ health, filterTitle, groups: catalogItems("group"), tags: catalogItems("tag"), filters: catalogItems('filter'),
          folders: list(folders), countdowns: list(countdowns), counts, tasks, taskPage, board, calendar,
          buckets, groupNames, stats: stats || state.stats });
  if (!opts.keepDetail && state.selected) await loadDetail(state.selected, opts);
}

export async function changeTaskPage(direction) {
  const page = state.taskPage;
  if (!page || page.loading || state.busy || (direction>0 && !page.has_more) || (direction<0 && !page.offset)) return;
  const query = taskQueryKey();
  if (page.query !== query) return;
  const ok = fresh();
  const current = () => ok() && state.taskPage?.key === page.key &&
    query === taskQueryKey();
  patch({taskPage:{...page, loading:true}});
  try {
    // Probe revision before using a prefetched page, including external API edits.
    const health = await call('app.health');
    if (!current()) return;
    if (health.task_token !== state.health?.task_token || health.today !== state.health?.today) {
      await loadAll({pageReset:true}); return;
    }
    const result = await taskPager.get(Math.max(0,page.offset + Math.sign(direction)*TASK_PAGE_SIZE));
    if (!current()) return;
    const names = {...(result.list_names || {})};
    for (const item of catalogItems('group')) names[item.id] = item.data.name;
    patch({tasks:result.items, buckets:result.groups || [], groupNames:names,
      taskPage:{...page,total:result.total,offset:result.offset,has_more:result.has_more,loading:false}});
    taskPager.warm(result.offset, result);
    document.getElementById('scroll')?.scrollTo({top:0});
  } catch (error) {
    if (!current()) return;
    if (error.code === 'PAGE_CHANGED') { await loadAll({pageReset:true}); return; }
    throw error;
  } finally {
    if (state.taskPage?.key === page.key && state.taskPage.loading) patch({taskPage:{...state.taskPage,loading:false}});
  }
}

export async function changeBoardPage(columnKey, direction) {
  const board = state.board;
  const column = columnKey === null ? null : board?.columns.find(c=>c.key===columnKey);
  if (!board || state.busy || board.columns_loading || (columnKey!==null && (!column || column.loading))) return;
  const offset = column ? column.offset : board.columns_offset;
  const more = column ? column.has_more : board.columns_has_more;
  if ((direction>0 && !more) || (direction<0 && !offset)) return;
  const requestKey = columnKey === null ? Symbol.for('board-columns') : columnKey;
  const ticket = ++boardRequestSequence, query = taskQueryKey();
  boardRequests.set(requestKey,ticket);
  const current = () => boardPager.key===board.key && state.board?.key===board.key && state.board.window===board.window &&
    (columnKey===null || boardPager.window===board.window) && query===taskQueryKey() && boardRequests.get(requestKey)===ticket;
  const mark = loading => {
    if (!current()) return;
    if (column ? state.board.columns.find(c=>c.key===columnKey)?.loading===loading : state.board.columns_loading===loading) return;
    patch({board:column ? {...state.board,columns:state.board.columns.map(c=>c.key===columnKey ? {...c,loading} : c)}
      : {...state.board,columns_loading:loading}});
  };
  mark(true);
  try {
    const health = await call('app.health');
    if (!current() || state.busy) return;
    if (health.task_token!==state.health?.task_token || health.today!==state.health?.today) {
      await loadAll({boardReset:true});return;
    }
    const next = Math.max(0,offset+Math.sign(direction)*(column ? BOARD_CARD_SIZE : BOARD_COLUMN_SIZE));
    if (column) {
      const result = await boardPager.cards(columnKey,next);
      if (!current() || state.busy) return;
      const updated = {...state.board,columns:state.board.columns.map(c=>c.key===columnKey ? {...result,loading:false} : c)};
      patch({board:updated,tasks:updated.columns.flatMap(c=>c.items),groupNames:boardNames(updated)});
      [...document.querySelectorAll('.board-cards')].find(el=>el.dataset.column===columnKey)?.scrollTo({top:0});
    } else {
      const result = await boardPager.get(next);
      if (!current() || state.busy || !result) return;
      keepBoardRequests(result);
      patch({board:result,tasks:result.columns.flatMap(c=>c.items),groupNames:boardNames(result)});
      document.querySelector('.board')?.scrollTo({left:0});
    }
  } catch (error) {
    if (!current()) return;
    if (error.code==='PAGE_CHANGED') { await loadAll({boardReset:true});return; }
    throw error;
  } finally { mark(false); }
}

export async function loadAlert(opts = {}) {
  const [alertConfig, alertStatus] = await Promise.all([call("alert.config_get"), call("alert.status")]);
  if (!opts.guard || opts.guard()) patch({ alertConfig, alertStatus, bell: !!alertConfig.master });
}

/** 数据类状态：备份、自启。开设置页或做完写操作后刷新。 */
export async function loadData() {
  const [backupStatus, backupList, autostart] = await Promise.all([
    call("backup.status"), call("backup.list"), call("app.autostart_get"),
  ]);
  patch({ backupStatus, backups: backupList.items, autostart });
}

/**
 * 同步类状态。未登录时 `sync.status` 会 401 —— 那是**正常态不是错误**，
 * 所以 auth 与 syncStatus 分开兜：登录与否必须能画出来，不能被一个异常带走
 * （数据页第一次上线就是被 app.autostart_get 的异常整块拖没的）。
 */
export async function loadSync(opts = {}) {
  const [autoSync, syncProgress] = await Promise.all([call('autosync.config_get'), call('sync.progress')]);
  let auth;
  try {
    auth = await call("auth.status");
  } catch (e) {
    if (!opts.guard || opts.guard()) patch({ auth: { authed: false }, syncStatus: null, autoSync, syncProgress });
    return;
  }
  let syncStatus = null;
  if (auth && auth.authed) {
    // 仓库还没就绪、或换机后 DPAPI 解不开令牌时，界面仍要能显示"未同步"这个状态
    syncStatus = await call("sync.status").catch(() => null);
  }
  if (!opts.guard || opts.guard()) patch({ auth, syncStatus, autoSync, syncProgress });
}

export async function loadDetail(id, opts = {}) {
  const ok = fresh();
  if (!id) { patch({ selected: null, detail: null, notes: [], children: [] }); return; }
  let detail;
  try {
    detail = await call("task.get", { item_id: id });
  } catch (e) {
    if (e instanceof ApiError && e.code === "NOT_FOUND") {
      patch({ selected: null, detail: null });
      return;
    }
    throw e;
  }
  const [notes, children] = await Promise.all([
    call("note.list", { item_id: id }),
    call("task.list", { parent_id: id }),
  ]);
  if (!ok() || (opts.guard && !opts.guard())) return;
  patch({ selected: id, detail, notes: notes.items, children: children.items });
}

export function select(id) {
  patch({ selected: id });
  loadDetail(id).catch(showError);
}

/** 全局错误出口：校验类就地提示，系统类弹红条。 */
export function showError(e) {
  const msg = e instanceof ApiError ? `${e.code}: ${e.message}` : String(e && e.message || e);
  console.error("[qingdan]", e);
  const el = document.getElementById("toast");
  if (el) {
    el.textContent = msg;
    el.hidden = false;
    clearTimeout(showError._t);
    showError._t = setTimeout(() => { el.hidden = true; }, 6000);
  }
}

