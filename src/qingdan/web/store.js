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
  const [health, , folders, countdowns, counts] = await Promise.all([
    call("app.health"),
    refreshCatalogs(),
    call("folder.list"),
    call("countdown.list"),
    call("view.counts"),
  ]);
  let tasks = [];
  let board = null;
  let calendar = null;
  let stats = null;
  let buckets = [];
  let groupNames = {};

  if (state.searchQ) {
    const r = await call("search.query", { q: state.searchQ });
    tasks = r.items;
  } else if (state.view.startsWith("filter:")) {
    // 自定义过滤器有自己的条件求值，不走内置视图
    const r = await call("filter.tasks", {
      item_id: state.view.slice(7), group: state.group, sort: state.sort, reverse: state.reverse });
    tasks = r.items;
    buckets = r.groups;
    groupNames = r.list_names || {};
  } else if (state.layout === "board") {
    // 分组下拉的值（date/priority/tag/group/none）和看板的 group_by 不是同一套枚举，
    // 直接把 state.group 透传会被后端拒绝成 422（实测踩过：看板一片空白且没有明显报错）
    const by = ["status", "group", "priority"].includes(state.group) ? state.group : "status";
    board = await call("view.board", { group_by: by, list_id: state.view.startsWith("group:") ? state.view.slice(6) : null });
    tasks = board.columns.flatMap((c) => c.items);
    groupNames = board.list_names || {};
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
  } else {
    // 复合视图：group:<id> / tag:<name> 用内置 all 视图 + 参数过滤
    const v = state.view;
    let name = v === 'inbox' ? 'all' : v, params = {};
    if (v.startsWith("group:")) { name = "all"; params = { list_id: v.slice(6) }; }
    else if (v.startsWith("tag:")) { name = "all"; params = { tag: v.slice(4) }; }
    const r = await call("view.query", {
      name, ...params, group: state.group, sort: state.sort,
      reverse: state.reverse, include_done: v === "done",
    });
    tasks = r.items;
    buckets = r.groups;
    groupNames = r.list_names || {};
    stats = await call("stats.today");
  }
  for (const item of catalogItems("group")) groupNames[item.id] = item.data.name;
  if (state.view.startsWith("group:")) {
    const id = state.view.slice(6);
    if (!groupNames[id]) {
      try { groupNames[id] = (await call("group.get", {item_id:id})).data.name; }
      catch (error) { if (error.code !== "NOT_FOUND") throw error; }
    }
  }
  if (!ok() || (opts.guard && !opts.guard())) return;
  let filterTitle = null;
  if (state.view.startsWith('filter:') && !catalogItems('filter').some(f=>f.id===state.view.slice(7))) {
    const selectedFilter = await call('filter.get',{item_id:state.view.slice(7)});
    filterTitle = selectedFilter.data.name;
  }
  if (!ok() || (opts.guard && !opts.guard())) return;
  patch({ health, filterTitle, groups: catalogItems("group"), tags: catalogItems("tag"), filters: catalogItems('filter'),
          folders: list(folders), countdowns: list(countdowns), counts, tasks, board, calendar,
          buckets, groupNames, stats: stats || state.stats });
  if (!opts.keepDetail && state.selected) await loadDetail(state.selected, opts);
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
  let auth;
  try {
    auth = await call("auth.status");
  } catch (e) {
    if (!opts.guard || opts.guard()) patch({ auth: { authed: false }, syncStatus: null });
    return;
  }
  let syncStatus = null;
  if (auth && auth.authed) {
    // 仓库还没就绪、或换机后 DPAPI 解不开令牌时，界面仍要能显示"未同步"这个状态
    syncStatus = await call("sync.status").catch(() => null);
  }
  if (!opts.guard || opts.guard()) patch({ auth, syncStatus });
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

