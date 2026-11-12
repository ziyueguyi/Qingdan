/**
 * 渲染与交互。约定：
 *  · 渲染只读 state，不直接发请求；写操作一律走 store.mutate()。
 *  · 事件用委托挂在 document 上，靠 data-act / data-field 派发，避免每次重渲染重复绑定。
 *  · 所有用户数据经 esc() 输出 —— 任务标题里写个 <img> 不该把页面弄坏。
 */
import { catalogs, catalogItems, subscribeCatalog, scrollCatalog, PAGE_SIZE, ROW_HEIGHT } from "./catalog.js";
import { configureAccessUI, loadAccess, renderAccessTab, renderCliTab, handleAccessAction, handleAccessChange, clearAccessSecret } from "./access.js";
import { call, hasFeature, loadFeatures, featureCount } from "./api.js";
import { t, setLang, locale } from "./i18n.js";
import { state, subscribe, patch, loadAll, loadDetail, loadAlert, loadData, loadSync, select, mutate, showError } from "./store.js";

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

const PC = { 1: "var(--p1)", 2: "var(--p2)", 3: "var(--p3)", 4: "var(--text-3)" };
const PL = { 1: "p_top", 2: "p_med", 3: "p_low", 4: "p_none" };
const REPEAT_LABEL = { daily: "rp_daily", weekly: "rp_weekly", monthly: "rp_monthly", yearly: "rp_yearly" };
const SWATCHES = ["#e5484d", "#e8912d", "#3b82f6", "#0f9d8e", "#7457e8"];

// --------------------------------------------------------------------------- 时间

function dayDiff(iso, base) {
  if (!iso) return null;
  const d = new Date(iso.length <= 10 ? `${iso}T00:00:00` : iso);
  if (Number.isNaN(d.getTime())) return null;
  const a = new Date(d.getFullYear(), d.getMonth(), d.getDate());
  return Math.round((a - base) / 864e5);
}

function todayBase() {
  const n = new Date();
  return new Date(n.getFullYear(), n.getMonth(), n.getDate());
}

function fmtDue(iso) {
  if (!iso) return "";
  const n = dayDiff(iso, todayBase());
  const d = new Date(iso.length <= 10 ? `${iso}T00:00:00` : iso);
  const hm = iso.includes("T") && d.getHours() + d.getMinutes() > 0
    ? ` ${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}` : "";
  if (n < 0) return `${t("overdue")} ${-n}d${hm}`;
  if (n === 0) return `${t("today2")}${hm}`;
  if (n === 1) return `${t("tomorrow")}${hm}`;
  const wd = new Intl.DateTimeFormat(locale(), { weekday: "short" }).format(d);
  return n < 7 ? `${wd}${hm}` : `${d.getMonth() + 1}/${d.getDate()}${hm}`;
}

function dueClass(iso) {
  const n = dayDiff(iso, todayBase());
  if (n === null) return "";
  return n < 0 ? "due over" : n === 0 ? "due today" : "due";
}

const groupName = (id) => (state.groups.find((g) => g.id === id) || {}).data?.name || state.groupNames[id] || "";
const repeatLabel = (v) => (REPEAT_LABEL[v] ? t(REPEAT_LABEL[v]) : v);

// --------------------------------------------------------------------------- 渲染

function render() {
  applyTheme();
  renderIconbar();
  renderTopbar();
  renderSidebar();
  renderToolbar();
  renderMain();
  renderStatusbar();
  renderDetail();
}

function applyTheme() {
  const r = document.documentElement;
  r.dataset.theme = state.appPrefs.theme || "light";
  r.dataset.accent = state.appPrefs.accent || "blue";
  r.dataset.size = state.appPrefs.size || "m";
  setLang(state.appPrefs.lang || "zh");
  document.title = t("app");
}

function renderIconbar() {
  // 键名写全，不用 t("nav_" + k) 动态拼 —— 动态拼会让「i18n 缺键」守卫失效。
  // 番茄专注没实现，按钮就不摆：空壳按钮让人以为程序坏了
  const items = [["task", "▤", "nav_task"], ["cal", "▦", "nav_cal"], ["stat", "◔", "nav_stat"]];
  $("iconbar").innerHTML =
    `<div class="logo">${esc(t("app").slice(0, 1))}</div>` +
    items.map(([k, ic, key], i) =>
      `<button class="ib ${state.nav === k ? "on" : ""}" title="${esc(t(key))}" data-act="nav" data-k="${k}">${ic}</button>`).join("") +
    `<div class="grow"></div>
     <button class="ib ${state.syncStatus && state.syncStatus.conflicts ? "warn" : ""}"
             title="${esc(t("sync_now"))}" data-act="sync-quick">⟳</button>
     <button class="ib" title="${esc(t("tab_alert"))}" data-act="open-settings" data-tab="alert">🔔</button>
     <button class="ib" title="${esc(t("settings"))}" data-act="open-settings" data-tab="appearance">⚙</button>`;
}

function renderTopbar() {
  const h = state.health || {};
  const pending = h.dirty ?? 0;
  const cls = pending ? "pill warn" : "pill off";
  const label = pending ? `${t("sync_pending")} ${pending}` : t("sync_off");
  $("topbar").innerHTML = `
    <h1>${esc(viewTitle())}</h1>
    <div class="searchbox">🔍<input id="q" placeholder="${esc(t("search"))}" value="${esc(state.searchQ)}"></div>
    <div class="spacer"></div>
    <div class="${cls}"><span class="dot"></span>${esc(label)}</div>
    <button class="iconbtn ${state.bell ? "on" : ""}" title="${esc(t(state.bell ? "bell_on" : "bell_off"))}"
            data-act="bell">${state.bell ? "🔔" : "🔕"}</button>
    <button class="iconbtn" data-act="open-settings" data-tab="appearance">⚙</button>`;
}

function viewTitle() {
  if (state.searchQ) return t("search_results", { q: state.searchQ });
  const v = state.view;
  if (v.startsWith("group:")) return groupName(v.slice(6)) || t("g_list");
  if (v.startsWith("tag:")) return "#" + v.slice(4);
  if (v.startsWith("filter:")) return (state.filters.find((f) => f.id === v.slice(7)) || {}).data?.name || state.filterTitle || t("g_filter");
  return t(v);
}

function navRow(key, icon, label, count, extra = "") {
  // 标签包进 .lbl：折叠成图标列时只隐藏文字、保留图标与角标；title 保证悬停能知道名字
  return `<button class="nav ${state.view === key && !state.searchQ ? "on" : ""}"
            data-act="view" data-view="${esc(key)}" title="${esc(label)}">
            <span class="ic">${icon}</span><span class="lbl">${esc(label)}</span>
            <span class="cnt">${count || ""}</span>${extra}</button>`;
}

function catalogBox(kind, row) {
  const c = catalogs[kind];
  const rows = [...c.pages.entries()].sort((a,b)=>a[0]-b[0]).flatMap(([page,items]) => items.map((item,i) => `<div class="catalog-row" style="top:${(page * PAGE_SIZE + i) * ROW_HEIGHT}px">${row(item)}</div>`)).join("");
  return `<div class="catalog-scroll" data-catalog="${kind}" tabindex="0" aria-label="${esc(t(kind === "tag" ? "g_tag" : kind === "filter" ? "g_filter" : "g_list"))}" style="height:${Math.min(PAGE_SIZE,c.total)*ROW_HEIGHT}px"><div class="catalog-space" style="height:${c.total*ROW_HEIGHT}px">${rows}</div></div>`;
}

function renderSidebar() {
  // 徽标一律来自 view.counts（后端全量算），不能用当前视图的 tasks 自己数
  const c = state.counts || {};

  // Catalogs use the same bounded five-row viewport and adjacent-page prefetch.
  const sbMin = !!state.viewPrefs?.sb_min;

  const tagRows = catalogBox("tag", tg => `<div class="list-nav"><button class="nav ${state.view === "tag:" + tg.data.name ? "on" : ""}" data-act="view" data-view="tag:${esc(tg.data.name)}" title="#${esc(tg.data.name)}"><span class="ic">${esc(tg.data.icon || '#')}</span><span class="lbl">${esc(tg.data.name)}</span><span class="cnt">${tg.task_count || ""}</span></button><button class="iconbtn list-edit" data-act="edit-tag" data-id="${esc(tg.id)}" title="${esc(t("edit_tag"))}">⋯</button></div>`);
  const listRows = catalogBox("group", g => `<div class="list-nav"><button class="nav ${state.view === "group:" + g.id ? "on" : ""}" data-act="view" data-view="group:${esc(g.id)}" title="${esc(g.data.name)}"><span class="ic">${esc(g.data.icon || g.data.emoji || '▤')}</span><span class="lbl">${esc(g.data.name)}</span></button><button class="iconbtn list-edit" data-act="edit-list" data-id="${esc(g.id)}" title="${esc(t("edit_list"))}">⋯</button></div>`);

  const filterRows = catalogBox('filter', f => `<div class="list-nav"><button class="nav ${state.view === 'filter:' + f.id ? 'on' : ''}" data-act="view" data-view="filter:${esc(f.id)}" title="${esc(f.data.name)}"><span class="ic">${esc(f.data.icon || '⚑')}</span><span class="lbl">${esc(f.data.name)}</span></button><button class="iconbtn list-edit" data-act="edit-filter" data-id="${esc(f.id)}" title="${esc(t('edit_filter'))}">⋯</button></div>`);

  $("sidebar").innerHTML = `
    <div class="sb-head"><button class="fold" data-act="fold-sb"
      title="${esc(t(sbMin ? "unfold_sb" : "fold_sb"))}">${sbMin ? "»" : "«"}</button></div>
    <div class="group-t">${esc(t("g_smart"))}</div>
    ${navRow("today", "📅", t("today"), c.today)}
    ${navRow("next7", "🗓", t("next7"), c.next7)}
    ${navRow("inbox", "📥", t("inbox"), c.all)}
    ${navRow("done", "☑", t("done"), c.done)}
    ${navRow("trash", "🗑", t("trash"), c.trash)}
    <div class="group-t">${esc(t("g_list"))}<button data-act="new-list" title="${esc(t("new_list"))}">＋</button></div>
    ${listRows}
    <div class="group-t">${esc(t("g_tag"))}<button data-act="new-tag" title="${esc(t("new_tag_t"))}">＋</button></div>
    <div class="desc">${esc(t("tag_desc"))}</div>
    ${tagRows}
    <div class="group-t">${esc(t("g_filter"))}<button data-act="new-filter" title="${esc(t("new_filter_t"))}">＋</button></div>
    <div class="desc">${esc(t("filter_desc"))}</div>
    ${filterRows}`;
  $("sidebar").querySelectorAll(".catalog-scroll").forEach(el => {
    const c = catalogs[el.dataset.catalog];
    const max = Math.max(0, (c.total - PAGE_SIZE) * ROW_HEIGHT);
    el.scrollTop = Math.min(c.scrollTop || 0, max);
  });
}

function renderToolbar() {
  const seg = [["list", "layout_list"], ["board", "layout_board"], ["calendar", "layout_calendar"]]
    .map(([v, k]) => `<button class="${state.layout === v ? "on" : ""}" data-act="layout" data-v="${v}">${esc(t(k))}</button>`)
    .join("");
  // 「按清单」分组随清单界面一起移除；老偏好里若还存着 group，显示时回落到按日期
  const groupVal = state.group;
  const groups = [["none", "g_none"], ["date", "g_date"],
                  ["priority", "g_prio"], ["tag", "g_tag2"], ["group", "g_list"]]
    .map(([v, k]) => `<option value="${v}" ${groupVal === v ? "selected" : ""}>${esc(t(k))}</option>`)
    .join("");
  const sorts = [["smart", "s_smart"], ["due", "s_due"], ["priority", "s_prio"],
                 ["title", "s_title"], ["created", "s_created"], ["updated", "s_updated"], ["manual", "s_manual"]]
    .map(([v, k]) => `<option value="${v}" ${state.sort === v ? "selected" : ""}>${esc(t(k))}</option>`)
    .join("");
  $("toolbar").innerHTML = `
    <div class="seg">${seg}</div>
    <select class="mini" data-field="group" title="${esc(t("group"))}">${groups}</select>
    <select class="mini" data-field="sort" title="${esc(t("sort"))}">${sorts}</select>
    <button class="iconbtn ${state.reverse ? "on" : ""}" data-act="reverse" title="↑↓">⇅</button>
    <div class="spacer"></div>
    <button class="btn pri" data-act="new-task" title="${esc(t("new_task_hint"))}">＋ ${esc(t("new_task"))}</button>
    <button class="btn" data-act="new-countdown">＋ ${esc(t("cd_title"))}</button>`;
}

function quickAddRow() {
  return `<div class="qa"><input id="quickadd" placeholder="${esc(t("quickadd"))}" autocomplete="off"></div>
          <div class="qa-hint">${t("qa_hint", {
            d: `<code>${esc(t("qa_sample_date"))}</code>`,
            c: `<code>${esc(t("qa_sample_tag"))}</code>`, p: "<code>!P1</code>" })}</div>
          <div id="qa-preview"></div>`;
}

function taskRow(it) {
  const d = it.data;
  const bar = it.deleted ? "var(--p1)" : (d.color || PC[d.priority] || "transparent");
  const showFlag = !it.deleted && d.priority < 4;
  return `<div class="task ${state.selected === it.id ? "on" : ""} ${d.status === "done" ? "done" : ""}"
            style="--bar:${esc(bar)}" data-act="open" data-id="${esc(it.id)}">
    <div class="cbx" data-act="toggle" data-id="${esc(it.id)}">${d.status === "done" ? "✓" : ""}</div>
    <div class="body">
      <div class="ttl">${showFlag ? `<span class="flag" style="color:${esc(PC[d.priority])}">⚑</span>` : ""}${esc(d.title)}</div>
      <div class="meta">
        ${d.due_at && !it.deleted ? `<span class="${dueClass(d.due_at)}">${esc(fmtDue(d.due_at))}</span>` : ""}
        ${it.deleted ? `<span style="color:var(--p1)">${esc(t("trash"))}</span>` : ""}
        ${d.list_id ? `<span>◍ ${esc(groupName(d.list_id))}</span>` : ""}
        ${(d.tags || []).map((x) => `<span class="tag">${esc(x)}</span>`).join("")}
        ${d.color ? `<span class="dotc" style="--c:${esc(d.color)}"></span>` : ""}
        ${d.repeat ? `<span>↻ ${esc(repeatLabel(d.repeat))}</span>` : ""}
      </div>
    </div>
    <div class="ops">
      <button data-act="edit-task" data-id="${esc(it.id)}" title="${esc(t("edit_task_t"))}">📅</button>
      ${!it.deleted && d.status !== "done"
        ? `<button data-act="snooze" data-id="${esc(it.id)}" title="${esc(t("snooze_t"))}">◷</button>` : ""}
      <button data-act="del" data-id="${esc(it.id)}" title="${esc(t("del"))}">🗑</button>
    </div>
  </div>`;
}

/** 分组标题：label_key 是 UI 文案要翻译，label 是用户数据（分组名/标签名）永不翻译。 */
const bucketLabel = (b) => (b.label_key ? t(b.label_key) : (b.label || ""));

function emptyKey() {
  if (state.searchQ) return "empty_search";
  const v = state.view;
  if (v.startsWith("group:") || v.startsWith("tag:") || v.startsWith("filter:")) return "empty_list";
  return { today: "empty_today", inbox: "empty_inbox", done: "empty_done",
           trash: "empty_trash" }[v] || "empty_list";
}

function renderMain() {
  const el = $("scroll");
  if (!state.booted) { el.innerHTML = `<div class="loading">${esc(t("booting"))}</div>`; return; }
  if (state.layout === "board") { el.innerHTML = renderBoard(); return; }
  if (state.layout === "calendar") { el.innerHTML = renderCalendar(); return; }

  let html = "";
  if (state.view === "today" && state.countdowns.length) html += renderCountdownBanner();
  if (!state.searchQ && !["done", "trash"].includes(state.view)) html += quickAddRow();
  if (state.searchQ) html += `<div class="sec-h">${esc(t("search_results", { q: state.searchQ }))}
                              <button class="btn" data-act="clear-search">${esc(t("back_to_view"))}</button><span class="line"></span></div>`;

  if (state.layout === "list" && state.group !== "none") {
    for (const b of state.buckets) {
      html += `<div class="sec-h ${b.key === "sec_overdue" ? "late" : ""}">${esc(bucketLabel(b))}<span class="line"></span></div>` +
        b.items.map(taskRow).join("");
    }
  } else {
    html += state.tasks.map(taskRow).join("");
  }
  if (!state.tasks.length) {
    html += `<div class="empty">${esc(t(emptyKey()))}</div>`;
  }
  el.innerHTML = html;
}

function renderCountdownBanner() {
  // 文案在这里拼：后端只给 direction + days（label_key 契约）。
  // 之前后端还给了 label，前端没用，英文界面却会跟着后端返回的中文走。
  return `<div class="banner"><span class="lbl">${esc(t("cd_title"))}</span>` +
    state.countdowns.map((c) => `<div class="cd" data-act="open-countdown" data-id="${esc(c.id)}">
      <b>${esc(c.title)}</b>${c.direction === "today"
        ? `<div class="n">${esc(t("cd_today"))}</div>`
        : `<div class="n">${esc(t(c.days > 0 ? "days_left" : "days_passed"))}
      ${Math.abs(c.days)}<small>${esc(t("days_unit"))}</small></div>`}</div>`).join("") + "</div>";
}

function renderBoard() {
  const b = state.board;
  if (!b || !b.columns.length) return `<div class="empty">${esc(t("empty_list"))}</div>`;
  return `<div class="board">` + b.columns.map((c) =>
    `<div class="col"><h4>${esc(bucketLabel(c))}<span>${c.total}</span></h4>` +
    c.items.map((it) => `<div class="card ${state.selected === it.id ? "on" : ""}"
       data-act="open" data-id="${esc(it.id)}">
       ${it.data.priority < 4 ? `<span style="color:${esc(PC[it.data.priority])}">⚑</span> ` : ""}${esc(it.data.title)}
       <div class="meta">${it.data.due_at ? `<span class="${dueClass(it.data.due_at)}">${esc(fmtDue(it.data.due_at))}</span>` : ""}</div>
     </div>`).join("") + "</div>").join("") + "</div>";
}

function renderCalendar() {
  const cal = state.calendar;
  if (!cal) return `<div class="loading">…</div>`;
  const wd = ["Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"].map((k, i) =>
    new Intl.DateTimeFormat(locale(), { weekday: "short" }).format(new Date(2024, 0, 1 + i)));
  const cells = cal.cells.map((c) => `<div class="d ${c.in_month ? "" : "out"} ${c.is_today ? "now" : ""}"
      data-act="cal-day" data-date="${esc(c.date)}"><b>${esc(c.date.slice(-2))}</b>` +
    c.events.slice(0, 3).map((e) => `<div class="ev ${e.occurrence ? "rec" : ""}" style="--ec:${esc(e.color || PC[e.priority] || "var(--accent)")}"
        data-act="${e.kind === "countdown" ? "open-countdown" : "open"}" data-id="${esc(e.id)}">${e.occurrence ? "↻ " : ""}${esc(e.title)}</div>`).join("") +
    (c.events.length > 3 ? `<div class="ev" style="--ec:var(--text-3)">+${c.events.length - 3}</div>` : "") +
    "</div>").join("");
  return `<div style="display:flex;align-items:center;gap:10px;padding-top:12px">
      <button class="iconbtn" data-act="cal-month" data-v="-1" title="${esc(t("cal_prev"))}">‹</button>
      <b>${esc(new Intl.DateTimeFormat(locale(), {year:"numeric",month:"long"}).format(new Date(cal.month + "T12:00:00")))}</b>
      <button class="iconbtn" data-act="cal-month" data-v="1" title="${esc(t("cal_next"))}">›</button>
      <button class="btn" data-act="cal-today">${esc(t("today"))}</button></div>
      <div class="cal">${wd.map((w) => `<div class="hd">${esc(w)}</div>`).join("")}${cells}</div>`;
}

function renderStatusbar() {
  const s = state.stats || {};
  const h = state.health || {};
  const authed = !!(state.auth && state.auth.authed);
  // 数据库位置与来源**不再占状态栏**（2026-11-10 用户要求去除）：信息没有丢，
  // 设置页 → 数据 仍是唯一权威展示处（全路径 + DIR_SOURCE_KEY 文案都在那里）。
  // 状态栏只留"扫一眼就要知道"的数：任务/逾期/今天/今日完成/待推送 + 在线状态点。
  const item = (icon, key, value, cls = "") =>
    `<span class="st-i ${cls}" title="${esc(t(key))}"><i>${icon}</i>` +
    `<span class="st-l">${esc(t(key))}</span><b>${value}</b></span>`;
  const modeKey = authed ? "sync_on" : "sync_off";
  $("statusbar").innerHTML = `
    ${item("▤", "status_items", h.items ?? "-")}
    ${item("⚠", "status_overdue", s.overdue ?? "-", (s.overdue ?? 0) > 0 ? "bad" : "")}
    ${item("📅", "today", s.due_today ?? "-")}
    ${item("☑", "status_done", s.done_today ?? "-")}
    ${item("⇪", "status_dirty", h.dirty ?? 0, (h.dirty ?? 0) > 0 ? "warn" : "")}
    <span class="spacer"></span>
    <span class="mode ${authed ? "ok" : "off"}" title="${esc(t(modeKey))}">
      <i class="dot"></i><span class="st-l">${esc(t(modeKey))}</span></span>`;
}

// --------------------------------------------------------------------------- 详情

function renderDetail() {
  const el = $("detail");
  const it = state.detail;
  const dtHead = `<div class="dt-head"><button class="fold" data-act="fold-dt"
    title="${esc(t("fold_dt"))}">»</button></div>`;
  if (!it) {
    el.innerHTML = dtHead + `<div class="empty">${esc(t("empty_list"))}</div>
      <div class="hint">${esc(t("hint_offline"))}</div>`;
    return;
  }
  const d = it.data;
  const isTask = it.kind === "task";

  const prio = isTask ? `<div class="row"><span class="k">${esc(t("d_prio"))}</span>
    <div class="prio">${[1, 2, 3, 4].map((p) =>
      `<button data-act="set-priority" data-id="${it.id}" data-v="${p}" style="--pc:${PC[p]}"
        class="${(d.priority || 4) === p ? "on" : ""}">${p < 4 ? "P" + p : t("p_none")}</button>`).join("")}</div></div>` : "";

  const colors = isTask ? `<div class="row"><span class="k">${esc(t("d_color"))}</span>
    <div class="sw">${SWATCHES.map((c) =>
      `<button style="background:${c}" data-act="set-color" data-id="${it.id}" data-v="${c}"
        class="${d.color === c ? "on" : ""}"></button>`).join("")}
      <button class="none ${!d.color ? "on" : ""}" data-act="set-color" data-id="${it.id}" data-v="" title="${esc(t("c_none"))}"></button>
    </div></div>` : "";

  const due = isTask ? `<div class="row"><span class="k">${esc(t("d_due"))}</span>
    <span class="v"><input class="mini" type="datetime-local" data-field="due" data-id="${it.id}"
      value="${esc((d.due_at || "").slice(0, 16))}"></span></div>
    <div class="row"><span class="k">${esc(t("d_remind"))}</span>
    <span class="v">${(d.remind_at || []).length ? esc((d.remind_at || []).map(fmtDue).join(", ")) : esc(t("r_none"))}</span></div>
    <div class="row"><span class="k">${esc(t("d_repeat"))}</span>
    <span class="v"><select class="mini" data-field="repeat" data-id="${it.id}">
      ${[["", "no_repeat"], ["daily", "rp_daily"], ["weekly", "rp_weekly"],
         ["monthly", "rp_monthly"], ["yearly", "rp_yearly"]]
        .map(([v, k]) => `<option value="${v}" ${d.repeat === v ? "selected" : ""}>${esc(t(k))}</option>`).join("")}
      ${/^FREQ=/i.test(d.repeat || "")
        ? `<option value="${esc(d.repeat)}" selected>${esc(t("rp_custom"))}</option>` : ""}
      <option value="__custom">${esc(t("rp_custom_set"))}</option>
    </select></span></div>
    <div id="rp-info" data-id="${it.id}"></div>
    <div class="row"><span class="k">${esc(t("d_tags"))}</span>
    <span class="v">${(d.tags || []).map((x) =>
      `<span class="chip">${esc(x)}<button data-act="untag" data-id="${it.id}" data-v="${esc(x)}">×</button></span>`).join(" ") || "—"}
      <button class="btn" data-act="add-tag" data-id="${it.id}">＋</button></span></div>` : "";

  const notes = isTask ? `<div style="margin-top:12px;font-size:var(--fs-s);color:var(--text-3)">${esc(t("d_note"))}</div>
    <textarea class="note" id="note" data-id="${it.id}" placeholder="…">${esc(d.note || "")}</textarea>
    <div class="row" style="border:none"><span class="k">${esc(t("d_sub"))}</span>
      <span class="v">${state.children.length} ·
        ${state.children.map((c) => esc(c.data.title)).join(", ") || "—"}</span></div>
    <div class="row" style="border:none"><span class="k">＋ ${esc(t("d_note"))}</span>
      <span class="v"><input class="mini" id="noteadd" data-id="${it.id}" style="width:100%"
        placeholder="${esc(t("add"))}"></span></div>
    ${state.notes.map((n) => `<div class="sub">· ${esc(n.data.text)}
      <button class="del" data-act="del-note" data-id="${n.id}">×</button></div>`).join("")}` : "";

  const meta = `<div class="row" style="border:none"><span class="k">${esc(t("d_sync"))}</span>
    <span class="v" style="font-family:ui-monospace,Consolas;font-size:11.5px;color:var(--text-2)">
      rev ${esc(it.rev)}<br>${esc(t("d_created"))} ${esc((d.created_at || "").slice(0, 16))}</span></div>
    <div class="hint">${esc(t("hint_sync"))}</div>
    <div class="hint warn">${esc(t("hint_trash"))}</div>`;

  const actions = it.deleted
    ? `<button class="btn pri" data-act="restore" data-id="${it.id}">${esc(t("restore"))}</button>`
    : `<button class="btn pri" data-act="save" data-id="${it.id}">${esc(t("save"))}</button>
       ${d.status === "done"
         ? `<button class="btn" data-act="reopen" data-id="${it.id}">${esc(t("reopen"))}</button>`
         : `<button class="btn" data-act="complete" data-id="${it.id}">${esc(t("complete"))}</button>`}
       ${d.repeat && d.status !== "done"
         ? `<button class="btn" data-act="skip" data-id="${it.id}" title="${esc(t("rp_skip_hint"))}">${esc(t("rp_skip"))}</button>` : ""}
       <button class="btn danger" data-act="del" data-id="${it.id}">${esc(t("del"))}</button>`;

  el.innerHTML = `
    ${dtHead}
    <input class="tin" id="title" data-id="${it.id}" value="${esc(d.title)}">
    ${prio}${colors}${due}${notes}${meta}
    <div class="btnrow">${actions}</div>`;

  // 重复信息（下一次/未来几次/历史）由后端算，这里只做异步填充。
  // 放在 innerHTML 之后：面板先出结构，不因一次查询卡住整个详情。
  if (isTask && d.repeat) fillRepeatInfo(it.id);
}

async function fillRepeatInfo(id) {
  let box = $("rp-info");
  if (!box) return;
  box.innerHTML = `<div class="row"><span class="k">${esc(t("rp_next"))}</span>
    <span class="v desc">…</span></div>`;
  try {
    const info = await call("task.repeat_info", { item_id: id, count: 4 });
    box = $("rp-info");
    // 详情可能已经切到别的任务：别把上一项的内容写进当前面板
    if (!box || box.dataset.id !== id) return;
    const s = info.summary || {};
    const parts = [];
    if (s.key) parts.push(t(s.key, { n: s.n }));
    if (s.days && s.days.length) parts.push(s.days.map((k) => t(k)).join(" "));
    if (s.monthdays && s.monthdays.length) parts.push(t("rp_on_days", { d: s.monthdays.join(", ") }));
    if (s.until) parts.push(t("rp_until", { d: s.until }));
    if (s.count) parts.push(t("rp_count", { n: s.count }));
    const h = info.history || {};
    const up = info.upcoming || [];
    box.innerHTML = `
      ${info.needs_due_key ? `<div class="hint warn">${esc(t(info.needs_due_key))}</div>` : ""}
      ${info.invalid_rule_key ? `<div class="hint warn">${esc(t(info.invalid_rule_key))}</div>` : ""}
      <div class="row"><span class="k">${esc(t("rp_rule"))}</span>
        <span class="v">${esc(parts.filter(Boolean).join(" · ") || t("rp_none"))}</span></div>
      <div class="row"><span class="k">${esc(t("rp_next"))}</span>
        <span class="v">${up.length ? esc(fmtDue(up[0])) : esc(t("rp_no_next"))}</span></div>
      ${up.length > 1 ? `<div class="row"><span class="k">${esc(t("rp_upcoming"))}</span>
        <span class="v desc">${esc(up.slice(1).map(fmtDue).join(" · "))}</span></div>` : ""}
      <div class="row"><span class="k">${esc(t("rp_history"))}</span>
        <span class="v desc">${esc(t("rp_hist_n", { done: h.done || 0, skip: h.skipped || 0 }))}
        <em class="src">${esc(t("rp_local"))}</em></span></div>`;
  } catch (e) {
    box = $("rp-info");
    if (box && box.dataset.id === id) {
      box.innerHTML = `<div class="hint warn">${esc(t("rp_load_failed"))}</div>`;
    }
  }
}

// --------------------------------------------------------------------------- 折叠（侧栏 / 详情）

function applyCollapse() {
  const vp = state.viewPrefs || {};
  const app = $("app");
  app.classList.toggle("sb-min", !!vp.sb_min);
  app.classList.toggle("dt-min", !!vp.dt_min);
  const rs = $("dt-restore");
  if (rs) rs.textContent = t("dt_restore");      // 还原按钮文案走 i18n，不写死
}

async function setCollapse(side, val) {
  const key = side === "sb" ? "sb_min" : "dt_min";
  patch({ viewPrefs: { ...state.viewPrefs, [key]: !!val } });
  applyCollapse();
  // 偏好存失败不该打断折叠动作本身（与 folded_folders 同口径）
  try { await call("view.pref_set", { prefs: { [key]: !!val } }); } catch { /* ignore */ }
}

// --------------------------------------------------------------------------- Snackbar

let snackbarTimer = null;
function snackbar(text, undoFeature) {
  const el = $("snackbar");
  el.innerHTML = esc(text) + (undoFeature
    ? `<button data-act="undo">${esc(t("undo"))}</button>` : "");
  el.hidden = false;
  clearTimeout(snackbarTimer);
  snackbarTimer = setTimeout(() => { el.hidden = true; }, 5000);
}


/** 弹窗唯一出口：内容 + 是否固定尺寸。
 *  四处各自写 innerHTML 时，谁没设样式就各自长成一个高度 —— 设置页切页签整个框上下跳。 */
function openModal(html, fixed = false) {
  $('modal').classList.remove('settings-modal');
  const m = $("modal");
  m.innerHTML = html;
  m.classList.toggle("fixed", fixed);
  $("mask").hidden = false;
}


/// 数据来源 → 文案键。后端 `app.health.data_dir_source` 新增取值时必须同时加这里，
/// test_data_dir_source_labels_exist 会抓到漏加。
const DIR_SOURCE_KEY = {
  app: "dir_app", legacy: "dir_legacy", env: "dir_env", file: "dir_file",
  explicit: "dir_explicit", readonly_fallback: "dir_readonly_fallback",
};

// --------------------------------------------------------------------------- 设置

function openSettings(tab = "appearance") {
  // 页签键必须和这里的内容键完全一致 —— 曾经页签用 "lang"、内容键写 "language"，
  // 点进去直接渲染成 undefined。
  const body = {
    appearance: `
      <div class="field"><label>${esc(t("theme"))}</label><div class="ctl seg">
        ${[["light", "theme_light"], ["dark", "theme_dark"]].map(([v, k]) =>
          `<button class="${(state.appPrefs.theme || "light") === v ? "on" : ""}"
             data-act="pref" data-k="theme" data-v="${v}">${esc(t(k))}</button>`).join("")}</div></div>
      <div class="field"><label>${esc(t("accent"))}</label><div class="ctl accent-row">
        ${[["blue", "#2f6fed"], ["teal", "#0f9d8e"], ["amber", "#dd9330"],
           ["pink", "#d9547a"], ["violet", "#7457e8"], ["navy", "#2b4a8b"]].map(([a, c]) =>
          `<button class="${(state.appPrefs.accent || "blue") === a ? "on" : ""}"
             data-act="pref" data-k="accent" data-v="${a}" style="background:${c}"></button>`).join("")}</div></div>
      <div class="field"><label>${esc(t("fsize"))}</label><div class="ctl seg">
        ${[["s", "size_s"], ["m", "size_m"], ["l", "size_l"]].map(([v, k]) =>
          `<button class="${(state.appPrefs.size || "m") === v ? "on" : ""}"
             data-act="pref" data-k="size" data-v="${v}">${esc(t(k))}</button>`).join("")}</div></div>
      <div class="hint">${esc(t("pref_saved"))}</div>`,
    lang: `
      <div class="field"><label>${esc(t("lang"))}</label><div class="ctl seg">
        ${[["zh", "lang_zh"], ["en", "lang_en"]].map(([v, k]) =>
          `<button class="${(state.appPrefs.lang || "zh") === v ? "on" : ""}"
             data-act="pref" data-k="lang" data-v="${v}">${esc(t(k))}</button>`).join("")}</div></div>
      <div class="hint">${esc(t("lang_note"))}</div>`,
    alert: renderAlertTab(),
    data: renderDataTab(),
    sync: renderSyncTab(),
    access: renderAccessTab(),
    cli: renderCliTab(),
  }[tab];

  const settingsTabs = [["appearance", "tab_appearance", "◐"], ["lang", "tab_lang", "◎"],
    ["alert", "tab_alert", "♧"], ["data", "tab_data", "▤"], ["sync", "tab_sync", "↻"], ["access", "tab_access", "⌘"], ["cli", "tab_cli", ">_"]].filter(x=>!globalThis.QingdanAndroid||!['access','cli'].includes(x[0]));
  openModal(`
    <div class="mh">${esc(t("settings"))}<button class="iconbtn" data-act="close-modal">✕</button></div>
    <div class="settings-layout"><nav class="tabs settings-nav" aria-label="${esc(t('settings'))}">${settingsTabs.map(([k, l, icon]) =>
      `<button class="${tab === k ? "on" : ""}" data-act="settings-tab" data-tab="${k}" aria-current="${tab===k?'page':'false'}"><span class="settings-nav-icon" aria-hidden="true">${icon}</span>${esc(t(l))}</button>`).join("")}</nav>
    <div class="mb settings-content" data-settings-page="${tab}"><h2 class="settings-page-title">${esc(t(settingsTabs.find(x=>x[0]===tab)[1]))}</h2>${body}</div></div>
    <div class="foot settings-foot"><span>Qingdan</span><span>v${esc(state.health?.version || '')}</span></div>`, true);
  $('modal').classList.add('settings-modal');
}

/** 后端只给 reason_key（机器键），文案在前端：写死在 i18n 字典里，两种语言都有。
 *  这里不做「autostart_ 前缀 + key」那种拼键名 —— i18n 静态扫描查不到拼出来的键，
 *  缺键会在运行时把裸键名糊在界面上。（注释里也不能写那种形式：扫描器连注释一起抓。） */
function autostartReasonText(key) {
  if (key === "dev_mode") return t("autostart_dev_mode");
  return t("autostart_unchanged");
}

/** 告警设置页：全部走真接口（alert.config_get / status / channel_test / config_update）。 */
function renderAlertTab() {
  const c = state.alertConfig, s = state.alertStatus;
  if (!c) return `<div class="loading">${esc(t("booting"))}</div>`;
  const chanMeta = { toast: ["ch_toast", "ch_toast_d"], dingtalk: ["ch_ding", "ch_ding_d"],
                     email: ["ch_mail", "ch_mail_d"] };
  const cards = Object.entries(chanMeta).map(([name, [tk, dk]]) => {
    const st = (s?.channels || []).find((x) => x.name === name) || {};
    const on = !!c.channels[name];
    const fields = name === "dingtalk" ? `
        <div class="field"><label>${esc(t("webhook"))}</label><input class="ctl" type="text"
          data-alert="secrets.dingtalk_webhook" placeholder="https://oapi.dingtalk.com/robot/send?access_token=…"></div>
        <div class="field"><label>${esc(t("secret"))}</label><input class="ctl" type="password"
          data-alert="secrets.dingtalk_secret" placeholder="${esc(c.secrets_mask?.dingtalk_secret || 'SEC…')}"></div>`
      : name === "email" ? `
        <div class="field"><label>${esc(t("smtp_preset"))}</label><select class="ctl" data-alert="smtp.preset">
          ${["qq", "163", "gmail"].map((p) => `<option ${c.smtp?.preset === p ? "selected" : ""}>${p}</option>`).join("")}
        </select></div>
        <div class="field"><label>${esc(t("from_addr"))}</label><input class="ctl" type="text"
          data-alert="smtp.from_addr" value="${esc(c.smtp?.from_addr || "")}"></div>
        <div class="field"><label>${esc(t("auth_code"))}</label><input class="ctl" type="password"
          data-alert="secrets.smtp_auth_code" placeholder="${esc(c.secrets_mask?.smtp_auth_code || "••••")}"></div>
        <div class="field"><label>${esc(t("to_addr"))}</label><input class="ctl" type="text"
          data-alert="smtp.to_addrs" value="${esc((c.smtp?.to_addrs || []).join(","))}"></div>` : "";
    return `<div class="chan">
      <div class="top"><b>${esc(t(tk))}</b><span class="desc">${esc(t(dk))}</span>
        <div class="tog ${on ? "on" : ""}" data-act="alert-channel" data-name="${name}"></div></div>
      <p>${esc(st.configured
        ? `✓ ${st.backend || t("alert_ready")}`
        : `⚠ ${t("alert_not_configured")}`)}</p>
      ${fields}
      <div class="btnrow"><button class="btn" data-act="alert-test" data-name="${name}">${esc(t("test"))}</button></div>
    </div>`;
  }).join("");

  return `
    <div class="field"><label>${esc(t("alert_master"))}</label>
      <div class="ctl" style="display:flex;gap:10px;align-items:center">
        <div class="tog ${c.master ? "on" : ""}" data-act="alert-master"></div>
        <span class="desc">${esc(t("alert_master_d"))}</span></div></div>
    ${cards}
    <div class="field"><label>${esc(t("dnd"))}</label><div class="ctl" style="display:flex;gap:8px">
      <input class="mini" style="width:78px" data-alert="dnd.start" value="${esc(c.dnd.start)}">
      <span style="color:var(--text-3)">→</span>
      <input class="mini" style="width:78px" data-alert="dnd.end" value="${esc(c.dnd.end)}">
      <span class="desc">${esc(t("dnd_note"))}</span></div></div>
    <div class="field"><label>${esc(t("digest"))}</label>
      <div class="ctl" style="display:flex;gap:10px;align-items:center">
        <div class="tog ${c.digest.enabled ? "on" : ""}" data-act="alert-digest"></div>
        <span class="desc">${esc(t("digest_d"))}</span></div></div>
    <div class="field"><label></label><div class="ctl" style="display:flex;gap:8px">
      <button class="btn" data-act="alert-silence">🔕 ${esc(t("bell_off"))} 30min</button>
      <button class="btn" data-act="alert-preview">👁 ${esc(t("nav_stat"))}</button></div></div>
    <div class="group-t" style="margin-top:14px">${esc(t("alert_log_t"))}</div>
    <div class="field"><label></label><div class="ctl">
      <button class="btn" data-act="alert-log">${esc(t("alert_log_btn"))}</button>
      <div id="alert-log-box" class="desc" style="margin-top:6px"></div></div></div>
    <div class="hint">${esc(t("alert_note_local"))}</div>
    <div class="warnbar">${esc(t("alert_rate_limit"))}</div>`;
}

async function alertSave(patchObj, tab = "alert") {
  try {
    const cfg = await mutate("alert.config_update", { config: patchObj });
    await loadAlert();
    openSettings(tab);
  } catch (e) { showError(e); }
}

async function alertSetField(path, value) {
  const [group, key] = path.split(".");
  await alertSave(group === "secrets" ? { secrets: { [key]: value } }
                 : { [group]: { [key]: value } });
}

async function alertTest(name) {
  try {
    const r = await call("alert.channel_test", { name });
    snackbar(`✓ ${name}: ${r.message || "ok"}`, false);
  } catch (e) { showError(e); }
}

async function alertPreview() {
  try {
    const p = await call("alert.preview", { hours: 24 });
    $("modal").innerHTML = `
      <div class="mh">${esc(t("alert_preview"))}<button class="iconbtn" data-act="close-modal">✕</button></div>
      <div class="mb">${p.items.length
        ? p.items.map((i) => `<div class="sub">· ${esc(i.fire_at)} — ${esc(i.title)}
            <span class="chip">${esc(i.channel)}</span></div>`).join("")
        : `<div class="empty">${esc(t("alert_preview_empty"))}</div>`}</div>`;
  } catch (e) { showError(e); }
}

/** 数据页：备份 / 导出 / 开机自启。全部走真接口，没有本地伪造状态。 */
function renderDataTab() {
  const bs = state.backupStatus, as = state.autostart;
  const newest = bs && bs.newest;
  const backups = (state.backups || []).slice(0, 5);

  const backupRows = backups.map((b) => `
    <div class="sub">· ${esc(b.name)} — ${(b.size / 1024).toFixed(0)} KB
      ${b.valid ? "" : `<span style="color:var(--p1)">${esc(t("backup_stale"))}</span>`}
      <button class="btn" data-act="backup-restore" data-name="${esc(b.name)}">${esc(t("backup_restore"))}</button>
    </div>`).join("");

  return `
    <div class="group-t">${esc(t("backup_section"))}</div>
    <div class="field"><label>${esc(t("backup_latest_label"))}</label><div class="ctl">
      ${newest ? esc(t("backup_last", { name: newest.name, days: bs.age_days }))
               : `<span style="color:var(--p2)">${esc(t("backup_never"))}</span>`}</div></div>
    <div class="field"><label></label><div class="ctl" style="display:flex;gap:8px;align-items:center">
      <button class="btn pri" data-act="backup-now">${esc(t("backup_now"))}</button>
      <span class="desc">${esc(bs && bs.stale ? t("backup_stale") : t("backup_fresh"))}
        · ${esc(t("backup_count", { n: bs ? bs.count : 0, keep: 14 }))}</span></div></div>
    ${backupRows}
    <div class="hint">${esc(t("backup_restore_note"))}</div>

    <div class="group-t" style="margin-top:14px">${esc(t("export_section"))}</div>
    <div class="field"><label></label><div class="ctl" style="display:flex;gap:8px;flex-wrap:wrap">
      <button class="btn" data-act="export" data-v="csv">${esc(t("export_csv"))}</button>
      <button class="btn" data-act="export" data-v="json">${esc(t("export_json"))}</button>
      <button class="btn" data-act="export" data-v="md">${esc(t("export_md"))}</button></div></div>
    <div class="desc">${esc(t("export_hint"))}</div>

    <div class="group-t" style="margin-top:14px">${esc(t("import_section"))}</div>
    <div class="field"><label></label><div class="ctl" style="display:flex;gap:8px;align-items:center">
      <button class="btn" data-act="data-import">${esc(t("import_btn"))}</button>
      <input type="file" id="import-file" accept=".json" style="display:none" data-act="import-file">
      <span class="desc">${esc(t("import_hint"))}</span></div></div>

    ${globalThis.QingdanAndroid ? '' : `<div class="group-t" style="margin-top:14px">${esc(t("autostart_section"))}</div>
    <div class="field"><label>${esc(t("autostart_on"))}</label>
      <div class="ctl" style="display:flex;gap:10px;align-items:center">
        <div class="tog ${as && as.enabled ? "on" : ""} ${as && !as.frozen ? "dis" : ""}"
             data-act="autostart-toggle" data-v="${as && as.enabled ? "false" : "true"}"></div>
        <span class="desc">${as && as.reason_key ? esc(autostartReasonText(as.reason_key))
          : (as && as.command ? esc(t("autostart_path", { path: as.command })) : "")}</span></div></div>`}

    <div class="group-t" style="margin-top:14px">${esc(t("data_dir"))}</div>
    <div class="desc">${esc((state.health && state.health.data_dir) || "")}
      <span class="chip">${esc(t(DIR_SOURCE_KEY[(state.health && state.health.data_dir_source) || "app"]))}</span></div>
    ${state.health && state.health.data_dir_source !== "app"
      ? `<div class="cf-note">${esc(t("data_dir_migrate"))}</div>` : ""}
    <div class="desc">${esc(t("data_dir_custom"))}</div>`;
}

/** 同步页签：登录 → 建私有仓 → 双向同步。全部走真接口（auth.* / repo.ensure / sync.*）。 */
function renderSyncTab() {
  const a = state.auth, s = state.syncStatus;
  if (!a) return `<div class="loading">${esc(t("booting"))}</div>`;

  if (!a.authed) {
    return `
    <div class="field"><label>${esc(t("token"))}</label>
      <div class="ctl" style="display:flex;gap:8px">
        <input class="ctl" type="password" id="qd-token" autocomplete="off" placeholder="••••••••••••">
        <button class="btn pri" data-act="sync-login">${esc(t("login"))}</button></div></div>
    <div class="desc">${esc(t("token_d"))}</div>
    <div class="hint">${esc(t("sync_never"))}</div>
    <div class="warnbar">${esc(t("sync_offline_ok"))}</div>`;
  }

  const repoName = (a.repo && a.repo.name) || t("repo_not_ready");
  const blocked = s && s.push_blocked_key ? syncBlockedText(s) : "";
  return `
    <div class="field"><label>${esc(t("account"))}</label>
      <div class="ctl desc">${esc(a.login || "—")} · ${esc(repoName)}
        <span class="lock">🔒 ${esc(t("priv_lock"))}</span></div></div>
    <div class="field"><label>${esc(t("sync_state"))}</label><div class="ctl desc">
      ${esc(SYNC_MODE_KEY[(s && s.mode) || "merge"] ? t(SYNC_MODE_KEY[s.mode || "merge"]) : t("sync_mode_unknown"))}
      · ${esc(t("pending_n", { n: s ? s.pending : 0 }))}
      · ${esc(t("conflicts_n", { n: s ? s.conflicts : 0 }))}
      ${s && s.conflicts ? `<button class="btn warn" data-act="sync-conflicts">⚠ ${esc(t("conflicts_open", { n: s.conflicts }))}</button>` : ""}
      ${s && s.last_sync_at ? "· " + esc(t("last_sync")) + " " + esc(new Date(s.last_sync_at).toLocaleString(locale())) : ""}
    </div></div>
    ${blocked ? `<div class="warnbar">${esc(blocked)}</div>` : ""}
    <div class="field"><label></label><div class="ctl" style="display:flex;gap:8px;flex-wrap:wrap">
      <button class="btn pri" data-act="sync-run">⇅ ${esc(t("sync_now"))}</button>
      <button class="btn" data-act="sync-pull">↓ ${esc(t("pull_only"))}</button>
      <button class="btn" data-act="sync-logout">${esc(t("logout"))}</button></div></div>
    <div class="field"><label>${esc(t("sentinel"))}</label>
      <div class="ctl desc">${esc(t("sentinel_d"))}</div></div>
    <div class="field"><label>${esc(t("bootstrap"))}</label>
      <div class="ctl desc">${esc(t("bootstrap_d"))}</div></div>
    ${renderAutoSync()}
    <div class="hint">${esc(t("sync_delete_note"))}</div>`;
}

function renderAutoSync() {
  const a=state.autoSync;if(!a)return '';
  const c=a.config,s=a.status;
  return `<section class="api-section"><h3>${esc(t('auto_sync_title'))}</h3><div class="field"><label for="auto-sync-enabled">${esc(t('auto_sync_enabled'))}</label><input id="auto-sync-enabled" type="checkbox" ${c.enabled?'checked':''}></div>
    <div class="field"><label>${esc(t('auto_sync_mode'))}</label><select class="ctl" id="auto-sync-mode"><option value="interval" ${c.mode==='interval'?'selected':''}>${esc(t('auto_sync_interval'))}</option><option value="daily" ${c.mode==='daily'?'selected':''}>${esc(t('auto_sync_daily'))}</option></select></div>
    <div class="field"><label>${esc(t('auto_sync_minutes'))}</label><input class="ctl" type="number" id="auto-sync-minutes" min="${a.minimum_interval_minutes}" max="10080" value="${c.interval_minutes}"></div>
    <div class="field"><label>${esc(t('auto_sync_times'))}</label><input class="ctl" id="auto-sync-times" value="${esc(c.times.join(', '))}" placeholder="09:00, 18:00"></div>
    <p class="desc">${esc(t('auto_sync_note'))}</p><p class="desc">${esc(t('auto_sync_next'))}: ${s.next_at?esc(new Date(s.next_at).toLocaleString(locale())):'—'}</p><p class="desc">${esc(t('auto_sync_last'))}: ${s.last_success_at?esc(new Date(s.last_success_at).toLocaleString(locale())):'—'} ${esc(s.error_code||'')}</p>
    <div class="btnrow"><button class="btn pri" data-act="auto-sync-save">${esc(t('save'))}</button></div></section>`;
}

// 后端只回机器键，文案在这里配（拼键名的写法会被 i18n 静态扫描漏掉，所以用显式表）。
const SYNC_MODE_KEY = { bootstrap: "sync_mode_bootstrap", first_merge: "sync_mode_first_merge",
                        merge: "sync_mode_merge" };

function syncBlockedText(s) {
  const min = Math.round(Math.abs(s.clock_offset_ms || 0) / 60000);
  if (s.push_blocked_key === "clock_skew") return t("warn_clock_skew", { min });
  return t("warn_push_blocked");
}

async function doLogin() {
  const input = $("qd-token");
  const token = (input && input.value || "").trim();
  if (!token) { snackbar(t("need_token"), false); return; }
  try {
    const me = await call("auth.login", { token });
    if (input) input.value = "";                 // 令牌绝不留在校验框里
    await loadSync();
    openSettings("sync");
    snackbar(t("login_ok", { name: me.login }), false);
  } catch (e) { showError(e); }
}

async function doSync(feature) {
  try {
    if (!(state.auth && state.auth.repo && state.auth.repo.name)) await call("repo.ensure", {});
    const r = await call(feature, {});
    await Promise.all([loadAll(), loadSync()]);
    openSettings("sync");
    if (r.ok === false && r.error_key === "clock_skew") {
      snackbar(syncBlockedText({ push_blocked_key: "clock_skew",
                                 clock_offset_ms: (state.syncStatus || {}).clock_offset_ms }), false);
    } else {
      snackbar(t("sync_done", { pulled: r.pulled || 0, pushed: r.pushed || 0,
                                conflicts: r.conflicts || 0 }), false);
    }
  } catch (e) { showError(e); }
}

async function doLogout() {
  try {
    await call("auth.logout", {});
    await loadSync();
    openSettings("sync");
    snackbar(t("logout_done"), false);            // 说清"本地数据没动"，不然像被删了
  } catch (e) { showError(e); }
}

async function doBackup() {
  try {
    // force：手动点「立即备份」必须真的落一份新快照。
    // 不带 force 时同名的日期备份已存在 → 接口返回 created:false，按钮成了空操作。
    const out = await call("backup.create", { force: true });
    await loadData();
    // 设置页的正文是一次性渲染的，state 变了不会自己重画：
    // 光 loadData + snackbar 的话，列表还停在点之前的样子（实测备份成功了、界面仍显示 1 份）。
    openSettings("data");
    snackbar(out.created ? t("backup_created", { name: out.name }) : t("backup_exists"), false);
  } catch (e) { showError(e); }
}

async function doRestore(name) {
  if (!confirm(t("backup_restore_confirm"))) return;
  try {
    await call("backup.restore", { name });
    await loadData();
    openSettings("data");
    snackbar(t("backup_restart_pending"), false);
  } catch (e) { showError(e); }
}

async function doExport(format) {
  try {
    // 导出文件的表头语言跟着界面语言走：后端不猜，由调用方指定
    const out = await call("data.export", { format, lang: state.appPrefs.lang || "zh" });
    snackbar(`${t("export_done", { n: out.items })} → ${out.path}`, false);
  } catch (e) { showError(e); }
}

async function doAutostart(on) {
  try {
    const out = await call("app.autostart_set", { on: String(on) !== "false" });
    await loadData();
    openSettings("data");
    // 后端拒绝时（开发态）必须说一句话：以前这里静默重画，开关看着能点、点完什么也没发生。
    if (out.changed === false) {
      snackbar(autostartReasonText(out.reason_key), false);
    }
  } catch (e) { showError(e); }
}


/** 图标条上的 ⟳：没登录就去设置页登录，登录了就真的同步。
 *  以前它只弹「该功能尚未实现」，而同步早就做完了 —— 提示骗人比没按钮更糟。 */
async function quickSync() {
  await loadSync();
  if (!(state.auth && state.auth.authed)) {
    openSettings("sync");
    snackbar(t("sync_need_login"), false);
    return;
  }
  await doSync("sync.run");
}

/** 统计面板：数字全部来自 stats.today，前端不重算一遍（重算就有第二套口径）。 */
function renderStats() {
  const s = state.stats;
  if (!s) { snackbar(t("booting"), false); return; }
  const row = (key, val) => `<div class="cf-row"><div class="cf-name">${esc(t(key))}</div>
      <div class="cf-cells"><b class="st-val">${val}</b></div></div>`;
  const prio = [1, 2, 3, 4].map((p) =>
    `<span class="chip" style="border-color:${PC[p]}">${esc(t(PL[p]))} ${s.by_priority[String(p)] || 0}</span>`).join("");
  const soon = (s.countdown_soon || []).map((c) =>
    `<span class="chip">${esc(c.title)} ${c.days_left}d</span>`).join("")
    || `<span class="cf-none">${esc(t("none_yet"))}</span>`;
  openModal(`
    <div class="mh">${esc(t("nav_stat"))}<button class="iconbtn" data-act="close-modal">✕</button></div>
    <div class="mb">
      ${row("st_overdue", s.overdue)}
      ${row("st_due_today", s.due_today)}
      ${row("st_done_today", s.done_today)}
      ${row("st_open_total", s.open_total)}
      <div class="cf-row"><div class="cf-name">${esc(t("st_by_priority"))}</div>
        <div class="cf-cells">${prio}</div></div>
      <div class="cf-row"><div class="cf-name">${esc(t("st_groups"))}</div>
        <div class="cf-cells"><b class="st-val">${s.groups}</b> · ${esc(t("st_tags"))} <b class="st-val">${s.tags}</b></div></div>
      <div class="cf-row"><div class="cf-name">${esc(t("st_soon"))}</div>
        <div class="cf-cells">${soon}</div></div>
      <div class="cf-note">${esc(t("stats_note"))}</div>
    </div>`, true);
}

// --------------------------------------------------------------------------- 冲突裁决

/// 显式映射表而不是拼键名：静态 i18n 扫描查不到 `t("cs_" + x)` 这种运行时键，
/// 后端多出一个新值就会把裸键名糊在界面上。
const CS_STATE_KEY = {
  local_only: "cs_local_only", remote_only: "cs_remote_only", both_diff: "cs_both_diff",
  local_missing: "cs_local_missing", remote_missing: "cs_remote_missing",
};
const KIND_LABEL_KEY = {
  task: "kind_task", list: "kind_list", folder: "kind_folder", section: "kind_section",
  tag: "kind_tag", note: "kind_note", countdown: "kind_countdown", filter: "kind_filter",
};
const CONFLICT_REASON_KEY = {
  both_changed: "conflict_reason_both_changed",
  resurrect_after_compaction: "conflict_reason_resurrect_after_compaction",
};

let conflicts = [];   // 只属于这个面板：离开就该丢掉，留着会污染下一次打开

const CONFLICT_EDITABLE = new Set(["text", "int", "list"]);

/** 拉一次冲突列表并打开面板。空列表也要打开，否则用户以为按钮坏了。 */
async function openConflicts() {
  try {
    const out = await call("sync.conflicts", {});
    conflicts = (out.items || []).map((c) => {
      const picks = {};
      (c.fields || []).forEach((f) => {
        // 只有一边改过的先替用户选好；两边都改的必须他自己看一眼——默认值就是答案的一半
        if (f.state === "local_only") picks[f.key] = "local";
        else if (f.state === "remote_only") picks[f.key] = "remote";
      });
      return { ...c, picks, edits: {} };
    });
    renderConflicts();
  } catch (e) { showError(e); }
}

function cfVal(row, side) {
  const v = row[side];
  if (v === null || v === undefined || v === "" || (Array.isArray(v) && !v.length))
    return `<span class="cf-none">${esc(t("conflict_value_none"))}</span>`;
  switch (row.type) {
    case "enum_status":
      return esc(t({ todo: "st_todo", doing: "st_doing", done: "st_done",
                     cancelled: "st_cancelled" }[v] || "conflict_value_none"));
    case "enum_priority":
      return esc(t({ 1: "p_top", 2: "p_med", 3: "p_low", 4: "p_none" }[String(v)] || "p_none"));
    case "enum_repeat":
      return esc(v ? t({ daily: "rp_daily", weekly: "rp_weekly", monthly: "rp_monthly",
                         yearly: "rp_yearly" }[v] || "no_repeat") : t("no_repeat"));
    case "bool": return esc(t(v ? "conflict_bool_on" : "conflict_bool_off"));
    case "color":
      return /^#[0-9a-fA-F]{6}$/.test(String(v))
        ? `<span class="cf-dot" style="background:${String(v)}"></span>${esc(String(v))}`
        : esc(String(v));
    case "list":
      return (Array.isArray(v) ? v : [v])
        .map((x) => `<span class="chip">${esc(x)}</span>`).join("");
    case "datetime": case "date": return esc(fmtDue(String(v)) || String(v));
    case "json": return `<code>${esc(JSON.stringify(v))}</code>`;
    default: return esc(String(v));
  }
}

function renderConflicts() {
  const cards = conflicts.map((c) => {
    const rows = c.fields.map((f) => {
      const picked = c.picks[f.key], edited = c.edits[f.key];
      const cell = (side) => `<button class="cf-cell ${picked === side && edited === undefined ? "on" : ""}"
            data-act="cf-pick" data-id="${esc(c.item_id)}" data-k="${esc(f.key)}" data-v="${side}">
          ${cfVal(f, side)}</button>`;
      const edit = edited !== undefined
        ? `<div class="cf-edit">✎ ${cfVal({ type: f.type, local: edited, remote: edited }, "local")}
             <button class="lnk" data-act="cf-clear" data-id="${esc(c.item_id)}" data-k="${esc(f.key)}"
               >${esc(t("conflict_clear_edit"))}</button></div>`
        : (CONFLICT_EDITABLE.has(f.type)
            ? `<input class="cf-input" data-act="cf-edit" data-id="${esc(c.item_id)}" data-k="${esc(f.key)}"
                 placeholder="${esc(t("conflict_edit_ph"))}" value="${esc(
                   f.type === "list" && Array.isArray(f.local) ? f.local.join(", ") : (f.local ?? ""))}">`
            : "");
      return `<div class="cf-row">
        <div class="cf-name">${esc(t(f.label_key))}
          <span class="cf-state ${esc(f.state)}">${esc(t(CS_STATE_KEY[f.state] || "cs_both_diff"))}</span>
          ${f.base !== null && f.base !== undefined ? `<em>${esc(t("conflict_base_was"))} ${cfVal(f, "base")}</em>` : ""}</div>
        <div class="cf-cells">${cell("local")}${cell("remote")}${edit}</div></div>`;
    }).join("");
    const del = (c.local_deleted ? `<span class="cf-del">${esc(t("conflict_deleted_local"))}</span>` : "")
      + (c.remote_deleted ? `<span class="cf-del">${esc(t("conflict_deleted_remote"))}</span>` : "");
    return `<div class="cf-item">
      <div class="cf-head"><b>${esc(c.title || c.item_id)}</b>
        <span class="cf-kind">${esc(t(KIND_LABEL_KEY[c.kind] || "kind_task"))}</span>${del}
        <span class="cf-when">${esc(new Date(c.created_at).toLocaleString(locale()))}</span></div>
      <div class="cf-reason">${esc(t("conflict_reason_prefix"))}${esc(t(CONFLICT_REASON_KEY[c.reason] || "conflict_reason_both_changed"))}</div>
      ${rows || `<div class="cf-one">${esc(t("conflict_only_state"))}</div>`}
      ${(c.local_deleted || c.remote_deleted)
        ? `<div class="cf-note">${esc(t("conflict_revive_note"))}</div>` : ""}
      <div class="cf-acts">
        <button class="btn" data-act="cf-resolve" data-id="${esc(c.item_id)}" data-v="local">${esc(t("keep_local"))}</button>
        <button class="btn" data-act="cf-resolve" data-id="${esc(c.item_id)}" data-v="remote">${esc(t("keep_remote"))}</button>
        <button class="btn pri" data-act="cf-resolve" data-id="${esc(c.item_id)}" data-v="merge">${esc(t("conflict_save_merge"))}</button>
      </div></div>`;
  }).join("");

  openModal(`
    <div class="mh">${esc(t("conflicts_title"))} <span class="cnt">${conflicts.length}</span>
      <button class="iconbtn" data-act="close-modal">✕</button></div>
    <div class="mb"><div class="cf-intro">${esc(t("conflicts_intro"))}</div>
      ${cards || `<div class="empty">${esc(t("conflicts_empty"))}</div>`}</div>`, true);
}

const conflictCard = (item_id) => conflicts.find((c) => c.item_id === item_id);

async function resolveConflict(item_id, choice) {
  const c = conflictCard(item_id);
  const params = { item_id, choice };
  if (choice === "merge") {
    const picks = { ...(c && c.picks || {}) };
    const values = { ...(c && c.edits || {}) };
    for (const k of Object.keys(values)) delete picks[k];   // 改写就是最终答案，别再被选边覆盖
    if (!Object.keys(picks).length && !Object.keys(values).length) {
      snackbar(t("conflict_nothing_picked"), false); return;
    }
    if (Object.keys(picks).length) params.picks = picks;
    if (Object.keys(values).length) params.values = values;
  }
  try {
    await call("sync.conflict_resolve", params);
    conflicts = conflicts.filter((x) => x.item_id !== item_id);
    await Promise.all([loadAll(), loadSync()]);
    renderConflicts();
    snackbar(t("conflict_saved", { title: (c && c.title) || item_id }), false);
  } catch (e) { showError(e); }
}

// --------------------------------------------------------------------------- 写操作

async function act(feature, params, undoText) {
  try {
    const data = await mutate(feature, params);
    if (undoText !== false && hasFeature(feature)) snackbar(undoText || t("save"), feature);
    return data;
  } catch (e) { showError(e); }
}

async function addQuickAdd(text, dueOverride) {
  try {
    const out = await mutate("task.quick_add", { text, create: true, list_id: state.view.startsWith("group:") ? state.view.slice(6) : null });
    snackbar(t("add"), "task.quick_add");
    if (out?.created) select(out.created.id);
    return out;
  } catch (e) { showError(e); }
}

let previewTimer = null;
async function previewQuickAdd(text) {
  clearTimeout(previewTimer);
  if (!text.trim()) { const p = $("qa-preview"); if (p) p.innerHTML = ""; return; }
  previewTimer = setTimeout(async () => {
    try {
      const out = await call("task.quick_add", { text });
      const el = $("qa-preview");
      if (!el) return;
      // 接口返回 {created, parsed}：识别结果在 parsed 里。曾经直接读顶层字段，
      // 预览永远显示"未识别"——用户输入了日期却看不到确认，以为功能坏了（2026-11-10 实测）。
      const p = out.parsed || {};
      const bits = [];
      if (p.due_at) bits.push("🕒 " + fmtDue(p.due_at));
      if (p.tags?.length) bits.push("🏷 " + p.tags.join(", "));
      if (p.priority) bits.push("⚑ P" + p.priority);
      el.innerHTML = bits.length
        ? `<div class="qa-preview">${esc(t("qa_parsed", { parts: bits.join("  ·  ") }))}</div>`
        : `<div class="qa-preview">${esc(t("qa_none"))}</div>`;
    } catch { /* 预览失败不值得打扰用户 */ }
  }, 260);
}

// --------------------------------------------------------------------------- 事件

async function onClick(ev) {
  const el = ev.target.closest("[data-act]");
  if (!el) return;
  const { act: a, id, v, k, view, date, tab, what, name } = el.dataset;
  ev.stopPropagation();
  if (a.startsWith('access-')) { await handleAccessAction(el); return; }

  switch (a) {
    case "mobile-nav": document.getElementById('app').classList.toggle('phone-nav'); break;
    case "view":
      document.getElementById('app').classList.remove('phone-nav');
      patch({ searchQ: "", view, layout: state.layout === "calendar" ? "list" : state.layout });
      await saveViewPrefs(); await loadAll();
      break;
    case "clear-search": patch({ searchQ: "" }); $("q").value = ""; await loadAll(); break;
    case "layout":
      patch({ layout: v }); await saveViewPrefs(); await loadAll(); break;
    case "reverse":
      patch({ reverse: !state.reverse }); await saveViewPrefs(); await loadAll(); break;
    case "open": if(globalThis.QingdanAndroid)document.getElementById('app').classList.add('phone-detail');select(id); break;
    case "toggle": {
      const it = state.tasks.find((x) => x.id === id);
      if (!it) break;
      await act(it.data.status === "done" ? "task.reopen" : "task.complete", { item_id: id });
      break;
    }
    case "del":
      if (!confirm(t("confirm_del"))) break;
      await act("task.delete", { item_id: id });
      break;
    case "restore": await act("task.restore", { item_id: id }); break;
    case "complete": await act("task.complete", { item_id: id }); break;
    case "skip": await act("task.skip", { item_id: id }, t("rp_skipped")); break;
    case "reopen": await act("task.reopen", { item_id: id }); break;
    case "snooze": await act("task.snooze", { item_id: id, minutes: 10 }); break;
    case "save": await saveDetail(el.dataset.id); break;
    case "set-priority": await act("task.set_priority", { item_id: id, priority: Number(v) }); break;
    case "set-color": await act("task.set_color", { item_id: id, color: v || null }); break;
    case "untag": await act("tag.unassign", { item_id: id, tag: v }); break;
    case "add-tag": {
      const name = prompt(t("new_tag_t"));
      if (name?.trim()) await act("tag.assign", { item_id: id, tag: name.trim() });
      break;
    }
    case "del-note": await act("note.delete", { item_id: id }); break;
    case "undo": {
      const out = await mutate("undo.last");
      snackbar(t("undone", { name: out.undone }), false);
      break;
    }
    case "fold-sb": if(globalThis.QingdanAndroid)document.getElementById('app').classList.toggle('phone-nav');else await setCollapse("sb", !state.viewPrefs?.sb_min); break;
    case "fold-dt": if(globalThis.QingdanAndroid)document.getElementById('app').classList.remove('phone-detail');else await setCollapse("dt", true); break;
    case "unfold-dt": await setCollapse("dt", false); break;
    case "ctx-toggle": {
      const it = state.tasks.find((x) => x.id === id) || await call("task.get", { item_id: id });
      await act(it && it.data.status === "done" ? "task.reopen" : "task.complete", { item_id: id });
      break;
    }
    case "ctx-due": {
      const p = (n) => String(n).padStart(2, "0");
      const base = v === "tomorrow" ? new Date(Date.now() + 86400000) : new Date();
      const due = v === "clear" ? null
        : `${base.getFullYear()}-${p(base.getMonth() + 1)}-${p(base.getDate())}`;
      await act("task.set_due", { item_id: id, due_at: due, all_day: true });
      break;
    }
    case "ctx-prio": await act("task.set_priority", { item_id: id, priority: Number(v) }); break;
    case "ctx-skip": await act("task.skip", { item_id: id }); break;
    case "ctx-snooze": await act("task.snooze", { item_id: id, minutes: 10 }); break;
    case "ctx-sched": await openTaskDialog(id); break;
    case "ctx-edit": await openTaskDialog(id); break;
    case "ctx-dup": {
      const src = state.tasks.find((x) => x.id === id) || await call("task.get", { item_id: id });
      const sd = (src && src.data) || {};
      await mutate("task.create", { title: sd.title, due_at: sd.due_at,
        due_all_day: sd.due_all_day !== false, priority: sd.priority,
        tags: sd.tags, list_id: sd.list_id, color: sd.color, repeat: sd.repeat });
      break;
    }
    case "ctx-del":
      if (confirm(t("confirm_del"))) await act("task.delete", { item_id: id });
      break;
    case "new-list": await openListDialog(); break;
    case "edit-list": await openListDialog(id); break;
    case "list-save": {
      const name = $("list-name").value.trim();
      if (!name) { $("list-name").focus(); break; }
      await mutate(id ? "group.update" : "group.create", { ...(id ? { item_id: id } : {}), name, icon:$('catalog-icon').value });
      $("mask").hidden = true;
      break;
    }
    case "list-delete": {
      if (!confirm(t("list_delete_confirm"))) break;
      if (state.view === "group:" + id) patch({ view: "inbox" });
      await mutate("group.delete", { item_id: id });
      await saveViewPrefs();
      $("mask").hidden = true;
      break;
    }
    case "ctx-move": {
      openModal(`<div class="mh">${esc(t("move_to"))}<button class="iconbtn" data-act="close-modal">✕</button></div><div class="mb"><div class="group-picker" id="move-list-picker"><select class="ctl" id="move-list"></select></div><div class="btnrow"><button class="btn pri" data-act="move-save" data-id="${esc(id)}">${esc(t("save"))}</button></div></div>`);
      await mountGroupPicker("move-list-picker", "move-list", "");
      break;
    }
    case "move-save": {
      const target = $("move-list").value;
      await mutate("task.move", { item_id: id, ...(target ? { list_id: target } : { to_inbox: true }) });
      $("mask").hidden = true;
      break;
    }
    case "new-task": await openTaskDialog(null); break;
    case "edit-task": await openTaskDialog(id); break;
    case "rule-open": openRuleDialog(); break;
    case "rule-cancel": cancelRuleDialog(); break;
    case "rule-save": await saveRuleDialog(); break;
    case "task-save": await taskDialogSave(id || null); break;
    case "data-import": $("import-file")?.click(); break;
    case "alert-log": {
      const box = $("alert-log-box");
      if (!box) break;
      try {
        const out = await call("alert.log", { limit: 30 });
        box.innerHTML = out.items.length
          ? out.items.map((r) => `<div class="sub">${esc(new Date(r.ts).toLocaleString(locale()))}
              · ${esc(r.channel)} · ${esc(r.level)} · ${esc(r.detail)}</div>`).join("")
          : `<div class="sub">${esc(t("log_empty"))}</div>`;
      } catch (e) { showError(e); }
      break;
    }
    case "edit-tag": await openTagDialog(id); break;
    case "tag-save": {
      const name = $("tag-name").value.trim();
      if (!name) { $("tag-name").focus(); break; }
      const old = id ? await call("tag.get", { item_id: id }) : null;
      const out = await mutate(id ? "tag.rename" : "tag.create", { ...(id ? {item_id:id}:{}), name, icon:$('catalog-icon').value });
      if (old && state.view === "tag:" + old.data.name) {
        patch({ view: "tag:" + out.data.name }); await saveViewPrefs(); await loadAll();
      }
      $("mask").hidden = true;
      break;
    }
    case "tag-delete": {
      if (!confirm(t("tag_delete_confirm"))) break;
      const old = await call("tag.get", { item_id: id });
      if (state.view === "tag:" + old.data.name) patch({ view: "inbox" });
      await mutate("tag.delete", { item_id: id }); await saveViewPrefs();
      $("mask").hidden = true;
      break;
    }
    case "new-tag": await openTagDialog(); break;
    case "new-filter": await openFilterDialog(); break;
    case "edit-filter": await openFilterDialog(id); break;
    case "catalog-icon": {
      $('catalog-icon').value=el.dataset.icon;
      document.querySelectorAll('[data-act="catalog-icon"]').forEach(b=>{const on=b.dataset.icon===el.dataset.icon;b.classList.toggle('on',on);b.setAttribute('aria-pressed',String(on));});
      break;
    }
    case "filter-save": {
      const name=$('filter-name').value.trim();if(!name){$('filter-name').focus();break;}
      await mutate(id?'filter.update':'filter.create',{...(id?{item_id:id}:{}),name,icon:$('catalog-icon').value,...(id?{}:{criteria:{overdue:true}})});
      $('mask').hidden=true;break;
    }
    case "filter-delete": {
      if(!confirm(t('filter_delete_confirm')))break;
      if(state.view==='filter:'+id)patch({view:'inbox'});
      await mutate('filter.delete',{item_id:id});await saveViewPrefs();$('mask').hidden=true;break;
    }
    case "new-countdown": await openCountdownDialog(); break;
    case "open-countdown": await openCountdownDialog(id); break;
    case "cal-month": {
      const month = new Date(state.calendar.month + "T12:00:00");
      month.setMonth(month.getMonth() + Number(v));
      patch({ calendarMonth: `${month.getFullYear()}-${String(month.getMonth()+1).padStart(2,"0")}-01` });
      await loadAll();
      break;
    }
    case "cal-today": patch({ calendarMonth: null }); await loadAll(); break;
    case "cal-day": {
      await openTaskDialog(null, { due_at: defaultDueText(new Date(), date), due_all_day: false });
      break;
    }
    case "bell": {
      el.disabled = true;
      try {
        const cfg = await call("alert.config_update", { config: { master: !state.bell } });
        state.alertConfig = cfg;
        state.bell = !!cfg.master;
        renderTopbar();
      } finally { el.disabled = false; }
      break;
    }
    case "nav": {
      if (k === "stat") { patch({ nav: "stat" }); renderStats(); break; }
      patch({ nav: k, layout: k === "cal" ? "calendar" : "list" });
      await saveViewPrefs(); await loadAll();
      break;
    }
    case "sync-quick": await quickSync(); break;
    case "auto-sync-save": {
      if(!$('auto-sync-minutes').reportValidity())break;
      await call('autosync.config_set',{enabled:$('auto-sync-enabled').checked,mode:$('auto-sync-mode').value,interval_minutes:Number($('auto-sync-minutes').value),times:$('auto-sync-times').value.split(/[,，]/).map(x=>x.trim()).filter(Boolean)});
      await loadSync();openSettings('sync');break;
    }
    case "open-settings":
      if (tab === "sync") await loadSync();
      else if (tab === "access" || tab === "cli") await loadAccess();
      openSettings(tab || "appearance");
      break;
    case "settings-tab":
      if (tab !== 'access') clearAccessSecret();
      // 页签数据现取：boot 时同步类接口在未登录下只会拿到空壳，
      // 而用户真正要看的是"点进来的这一刻"的状态。
      if (tab === "sync") await loadSync();
      else if (tab === "data") await loadData();
      else if (tab === "alert") await loadAlert();
      else if (tab === "access" || tab === "cli") await loadAccess();
      openSettings(tab);
      break;
    case "sync-login": await doLogin(); break;
    case "sync-run": await doSync("sync.run"); break;
    case "sync-pull": await doSync("sync.pull"); break;
    case "sync-logout": await doLogout(); break;
    case "sync-conflicts": await openConflicts(); break;
    case "cf-pick": {
      const c = conflictCard(id); if (!c) break;
      if (c.edits[k]) { delete c.edits[k]; c.picks[k] = v; }
      else c.picks[k] = c.picks[k] === v ? undefined : v;
      renderConflicts(); break;
    }
    case "cf-clear": { const c = conflictCard(id); if (c) { delete c.edits[k]; renderConflicts(); } break; }
    case "cf-resolve": await resolveConflict(id, v); break;
    case "close-modal":
      clearAccessSecret();
      $("mask").hidden = true;
      // 统计是弹窗不是视图，关掉就该把高亮还给任务区，否则图标一直亮着"统计"
      if (state.nav === "stat") patch({ nav: "task" });
      break;
    case "pref":
      await saveAppPref(k, v);
      openSettings(tab || (k === "lang" ? "lang" : "appearance"));
      break;
    case "alert-master": await alertSave({ master: !state.alertConfig.master }); break;
    case "alert-channel": await alertSave({ channels: { [name]: !state.alertConfig.channels[name] } }); break;
    case "alert-digest": await alertSave({ digest: { enabled: !state.alertConfig.digest.enabled } }); break;
    case "alert-test": await alertTest(name); break;
    case "alert-preview": await alertPreview(); break;
    case "alert-silence":
      try { await call("alert.silence", { minutes: 30 }); snackbar(t("bell_off"), false); }
      catch (e) { showError(e); }
      break;
    case "backup-now": await doBackup(); break;
    case "backup-restore": await doRestore(name); break;
    case "export": await doExport(v); break;
    case "autostart-toggle": await doAutostart(v); break;
  }
  // 右键菜单项执行完就关：菜单内点击不会被"点外面关闭"那条捕获逻辑关掉
  if (a && a.startsWith("ctx-")) closeTaskMenu();
}

async function onAlertChange(el) {
  const value = el.type === "password" || el.type === "text" || el.type === "select-one"
    ? el.value : el.value;
  if (!value && el.type === "password") return;         // 空密码框表示「不改」，不是清空
  if (el.dataset.alert === "smtp.to_addrs") {
    await alertSave({ smtp: { to_addrs: value.split(/[,，\s]+/).filter(Boolean) } });
  } else {
    await alertSetField(el.dataset.alert, value);
  }
}

async function saveDetail(id) {
  const title = $("title")?.value?.trim();
  const note = $("note")?.value ?? "";
  if (!title) return;
  await act("task.update", { item_id: id, title, note });
}

async function onChange(ev) {
  if (handleAccessChange(ev.target)) return;
  const imp = ev.target.closest("[data-act='import-file']");
  if (imp) { await doImport(imp); return; }
  const alertEl = ev.target.closest("[data-alert]");
  if (alertEl) { await onAlertChange(alertEl); return; }
  const el = ev.target.closest("[data-field]");
  if (!el) return;
  const f = el.dataset.field, id = el.dataset.id;
  if (f === "group") { patch({ group: el.value }); await saveViewPrefs(); await loadAll(); }
  else if (f === "sort") { patch({ sort: el.value }); await saveViewPrefs(); await loadAll(); }
  else if (f === "due") await act("task.set_due", { item_id: id, due_at: el.value || null });
  else if (f === "repeat") {
    let val = el.value || null;
    if (val === "__custom") {
      const cur = (state.detail && state.detail.data && state.detail.data.repeat) || "";
      const raw = prompt(t("rp_custom_prompt"), /^FREQ=/i.test(cur) ? cur : "FREQ=WEEKLY;BYDAY=MO,WE");
      if (raw === null) { renderDetail(); return; }        // 取消 → 把下拉画回原值，不留假选择
      val = raw.trim() || null;
    }
    await act("task.set_repeat", { item_id: id, repeat: val });
  }
}

async function onKeydown(ev) {
  // [ / ] 折展侧栏/详情。输入框里打字时不许抢键。
  if ((ev.key === "[" || ev.key === "]") && !/INPUT|TEXTAREA|SELECT/.test(ev.target.tagName)) {
    if (ev.key === "[") await setCollapse("sb", !state.viewPrefs?.sb_min);
    else await setCollapse("dt", !state.viewPrefs?.dt_min);
    return;
  }
  if (ev.target.id === "tk-title" && ev.key === "Enter") {
    ev.preventDefault();
    $("modal").querySelector('[data-act="task-save"]')?.click();
    return;
  }
  if (ev.target.id === "quickadd" && ev.key === "Enter") {
    const text = ev.target.value.trim();
    if (!text) return;
    const due = ev.target.dataset.due;
    await addQuickAdd(due ? `${due} ${text}` : text);
    ev.target.value = ""; ev.target.dataset.due = "";
    const p = $("qa-preview"); if (p) p.innerHTML = "";
    return;
  }
  if (ev.target.id === "quickadd") { previewQuickAdd(ev.target.value); return; }
  if (ev.target.id === "q" && ev.key === "Enter") {
    patch({ searchQ: ev.target.value.trim() });
    await loadAll();
    return;
  }
  if (ev.target.id === "noteadd" && ev.key === "Enter") {
    const text = ev.target.value.trim();
    if (text) { await act("note.append", { item_id: ev.target.dataset.id, text }); ev.target.value = ""; }
    return;
  }
  if (ev.target.id === "title" && ev.key === "Enter") { await saveDetail(ev.target.dataset.id); ev.target.blur(); }
  if (ev.key === "Escape" && !document.querySelector('dialog[open]')) {
    clearAccessSecret();
    $("mask").hidden = true;
  }
}

// --------------------------------------------------------------------------- 偏好

async function saveAppPref(key, value) {
  const next = { ...state.appPrefs, [key]: value };
  patch({ appPrefs: next });
  applyTheme();                       // 先即时生效，再落库
  try { await call("app.pref_set", { prefs: { [key]: value } }); }
  catch (e) { showError(e); }
}

async function saveViewPrefs() {
  const prefs = { view: state.view, layout: state.layout, group: state.group,
                  sort: state.sort, reverse: state.reverse, color_by: state.colorBy };
  try { await call("view.pref_set", { prefs }); } catch { /* 偏好失败不该打断操作 */ }
}

// --------------------------------------------------------------------------- 倒数日弹窗

// --------------------------------------------------------------------------- 任务弹窗（新建/编辑/排期）

/** 新建任务的默认到期：**明天此刻**（2026-11-10 用户指定）。
    带时间而非全天，所以「全天」复选默认不勾；编辑已有任务时不套用默认值。 */
function defaultDueText(now = new Date(), selectedDate = null) {
  const d = selectedDate ? new Date(selectedDate + "T12:00:00") : new Date(now);
  if (!selectedDate) d.setDate(d.getDate() + 1);
  d.setHours(now.getHours(), now.getMinutes(), 0, 0);
  const p = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}`;
}

/** 添加按钮与任务行 📅 都走这里。详情面板被折叠时，这是唯一的排期入口 ——
    2026-11-10 用户反馈"无法对任务进行排期"，根因正是编辑控件只活在详情面板里。 */
async function openTaskDialog(id, defaults = {}) {
  let d = { ...defaults };
  if (!id && state.view.startsWith("group:")) d.list_id = state.view.slice(6);
  if (id) {
    const it = state.tasks.find((x) => x.id === id) || await call("task.get", { item_id: id });
    d = it.data || {};
  }
  const prio = d.priority ? Number(d.priority) : 4;
  // 新建默认「明天此刻」；编辑时保留原值（没排期的老任务编辑时仍为空，不强行塞默认）
  const dueVal = d.due_at ? (String(d.due_at).length === 10 ? d.due_at + "T09:00" : String(d.due_at).slice(0, 16)) : (id ? "" : defaultDueText());
  const allDayChecked = !!d.due_at && d.due_all_day !== false;
  openModal(`
    <div class="mh">${esc(t(id ? "edit_task_t" : "new_task"))}
      <button class="iconbtn" data-act="close-modal">✕</button></div>
    <div class="mb">
      <div class="field"><label>${esc(t("d_title"))}</label>
        <input class="ctl" type="text" id="tk-title" value="${esc(d.title || "")}"
               placeholder="${esc(t("tk_title_ph"))}"></div>
      <div class="field"><label>${esc(t("d_due"))}</label>
        <div class="task-due-row">
          <input class="ctl" type="datetime-local" id="tk-due" value="${esc(dueVal)}">
          <input type="checkbox" id="tk-allday" hidden ${allDayChecked ? "checked" : ""}>
        <select class="ctl compact-repeat" id="tk-repeat" aria-label="${esc(t("d_repeat"))}">
          ${[["", "repeat_once"], ["allday", "tk_allday"], ["daily", "rp_daily"], ["weekly", "rp_weekly"], ["monthly", "rp_monthly"], ["yearly", "rp_yearly"], ["rules", "repeat_rules"]].map(([v,k]) => {
            const chosen = d.repeat ? (["daily","weekly","monthly","yearly"].includes(d.repeat) ? d.repeat : "rules") : (allDayChecked ? "allday" : "");
            return `<option value="${v}" ${chosen === v ? "selected" : ""}>${esc(t(k))}</option>`;
          }).join("")}
        </select>
        <button class="iconbtn" id="tk-edit-rule" data-act="rule-open" title="${esc(t("repeat_rules"))}" ${d.repeat && !["daily","weekly","monthly","yearly"].includes(d.repeat) ? "" : "hidden"}>⚙</button>

        </div></div>
      <div class="field"><label>${esc(t("d_prio"))}</label>
        <div class="prio" id="tk-prio">${[1, 2, 3, 4].map((p) =>
          `<button type="button" data-prio="${p}" style="--pc:${PC[p]}"
             class="${prio === p ? "on" : ""}">${p < 4 ? "P" + p : t("p_none")}</button>`).join("")}</div></div>
      <div class="field"><label>${esc(t("d_tags"))}</label>
        <input class="ctl" type="text" id="tk-tags" value="${esc((d.tags || []).join(", "))}"
               placeholder="${esc(t("tk_tags_ph"))}"></div>
      <div class="field"><label>${esc(t("d_list"))}</label>
        <div class="group-picker" id="tk-list-picker"><select class="ctl" id="tk-list"></select></div></div>
      <div class="btnrow">
        <button class="btn pri" data-act="task-save" data-id="${esc(id || "")}">${esc(t("save"))}</button>
      </div>
    </div>`);
  $("tk-repeat").dataset.rule = d.repeat || "";
  $("tk-repeat").dataset.previous = $("tk-repeat").value;
  $("tk-repeat").onchange = () => {
    if ($("tk-repeat").value === "rules") { openRuleDialog(); return; }
    $("tk-edit-rule").hidden = true;
    if (["", "allday"].includes($("tk-repeat").value)) setTaskAllDay($("tk-repeat").value === "allday");
    $("tk-repeat").dataset.previous = $("tk-repeat").value;
  };
  setTaskAllDay(allDayChecked);
  await mountGroupPicker("tk-list-picker", "tk-list", d.list_id || "");
  // 优先级是一组互斥按钮：点谁谁亮，取值时读 .on
  $("tk-prio").querySelectorAll("[data-prio]").forEach((b) => {
    b.onclick = () => $("tk-prio").querySelectorAll("[data-prio]")
      .forEach((x) => x.classList.toggle("on", x === b));
  });
  setTimeout(() => $("tk-title")?.focus(), 0);
}

async function mountGroupPicker(containerId, selectId, initial) {
  const container = $(containerId);
  container.dataset.loading = "true";
  let selected = initial, offset = 0, query = "", seq = 0, timer;
  let selectedItem = selected ? state.groups.find(g=>g.id === selected) || await call("group.get", {item_id:selected}) : null;
  if (!container.isConnected) return;
  container.innerHTML = `<input class="ctl" type="search" placeholder="${esc(t("search_lists"))}" aria-label="${esc(t("search_lists"))}"><select class="ctl" id="${selectId}"></select><div class="picker-pages"><button type="button" class="iconbtn" data-page="prev" aria-label="${esc(t("previous_page"))}">‹</button><span class="desc"></span><button type="button" class="iconbtn" data-page="next" aria-label="${esc(t("next_page"))}">›</button></div>`;
  const select = $(selectId), search = container.querySelector('input'), info = container.querySelector('span');
  const prev = container.querySelector('[data-page="prev"]'), next = container.querySelector('[data-page="next"]');
  let items = [], total = 0;
  select.onchange = () => { selected = select.value; selectedItem = items.find(g=>g.id === selected) || selectedItem; };
  async function load() {
    const ticket = ++seq;
    const result = await call("group.list", {limit:5, offset, q:query});
    if (ticket !== seq || !container.isConnected) return;
    items = result.items; total = result.total;
    const options = selectedItem && !items.some(g=>g.id===selected) ? [selectedItem,...items] : items;
    select.innerHTML = `<option value="">${esc(t("inbox"))}</option>` + options.map(g=>`<option value="${esc(g.id)}" ${g.id===selected ? "selected" : ""}>${esc(g.data.name)}</option>`).join("");
    if (!selected) select.value = "";
    info.textContent = `${total ? Math.floor(offset/5)+1 : 0} / ${Math.ceil(total/5)}`;
    prev.disabled = offset === 0; next.disabled = !result.has_more;
    container.dataset.loading = "false";
  }
  prev.onclick = () => { offset = Math.max(0,offset-5); load().catch(showError); };
  next.onclick = () => { if (offset+5 < total) { offset += 5; load().catch(showError); } };
  search.oninput = () => { clearTimeout(timer); timer = setTimeout(()=>{ offset=0; query=search.value.trim(); load().catch(showError); },180); };
  await load();
}

function setTaskAllDay(enabled) {
  const input = $("tk-due");
  const value = input.value;
  $("tk-allday").checked = enabled;
  input.type = enabled ? "date" : "datetime-local";
  input.value = value ? (enabled ? value.slice(0,10) : value.length === 10 ? value + "T09:00" : value) : "";
}

function openRuleDialog() {
  let dialog = $("rule-dialog");
  if (!dialog) {
    dialog = document.createElement("dialog"); dialog.id = "rule-dialog"; dialog.className = "rule-dialog";
    dialog.setAttribute("aria-label", t("repeat_rules")); document.body.append(dialog);
    dialog.addEventListener("cancel", ev => { ev.preventDefault(); cancelRuleDialog(); });
  }
  const raw = $("tk-repeat").dataset.rule || "";
  const fields = Object.fromEntries(raw.toUpperCase().split(";").filter(s=>s.includes("=")).map(s=>s.split("=")));
  const freq = fields.FREQ || ({daily:"DAILY",weekly:"WEEKLY",monthly:"MONTHLY",yearly:"YEARLY"}[raw] || "WEEKLY");
  const advanced = !!fields.BYMONTHDAY;
  dialog.innerHTML = `<div class="mh">${esc(t("repeat_rules"))}<button class="iconbtn" data-act="rule-cancel">✕</button></div><div class="mb">
    <div class="field"><label>${esc(t("rp_mode"))}</label><select class="ctl" id="rule-mode"><option value="form">${esc(t("rp_builder"))}</option><option value="advanced" ${advanced ? "selected" : ""}>${esc(t("rp_advanced"))}</option></select></div>
    <div id="rule-form" ${advanced ? "hidden" : ""}>
      <div class="field"><label>${esc(t("rp_frequency"))}</label><select class="ctl" id="rule-freq">${[["DAILY","rp_daily"],["WEEKLY","rp_weekly"],["MONTHLY","rp_monthly"],["YEARLY","rp_yearly"]].map(([v,k])=>`<option value="${v}" ${freq === v ? "selected" : ""}>${esc(t(k))}</option>`).join("")}</select></div>
      <div class="field"><label>${esc(t("rp_interval_label"))}</label><input class="ctl" id="rule-interval" type="number" min="1" max="365" value="${esc(fields.INTERVAL || "1")}" required></div>
      <div class="field" id="rule-weekdays" ${freq === "WEEKLY" ? "" : "hidden"}><label>${esc(t("rp_weekdays"))}</label><div class="cycle-days">${["MO","TU","WE","TH","FR","SA","SU"].map((day,i)=>`<label class="chk"><input type="checkbox" value="${day}" ${(fields.BYDAY || "").split(",").includes(day) ? "checked" : ""}>${esc(new Intl.DateTimeFormat(locale(),{weekday:"short"}).format(new Date(2024,0,1+i)))}</label>`).join("")}</div></div>
      <div class="field"><label>${esc(t("rp_end_label"))}</label><select class="ctl" id="rule-end"><option value="never">${esc(t("rp_end_never"))}</option><option value="date" ${fields.UNTIL ? "selected" : ""}>${esc(t("rp_end_date"))}</option><option value="count" ${fields.COUNT ? "selected" : ""}>${esc(t("rp_end_count"))}</option></select></div>
      <div class="field" id="rule-until-row" ${fields.UNTIL ? "" : "hidden"}><label>${esc(t("d_due"))}</label><input class="ctl" id="rule-until" type="date" value="${esc((fields.UNTIL || "").replace(/^(\d{4})(\d{2})(\d{2}).*$/, "$1-$2-$3"))}"></div>
      <div class="field" id="rule-count-row" ${fields.COUNT ? "" : "hidden"}><label>${esc(t("rp_end_count"))}</label><input class="ctl" id="rule-count" type="number" min="1" max="10000" value="${esc(fields.COUNT || "10")}" required></div>
    </div>
    <div id="rule-advanced" ${advanced ? "" : "hidden"}><textarea class="ctl" id="rule-raw" rows="3" placeholder="FREQ=WEEKLY;INTERVAL=2;BYDAY=MO,FR">${esc(raw.startsWith("FREQ=") ? raw : `FREQ=${freq}`)}</textarea></div>
    <label class="chk"><input id="rule-allday" type="checkbox" ${$("tk-allday").checked ? "checked" : ""}>${esc(t("tk_allday"))}</label>
    <div class="hint danger" id="rule-error" role="alert" hidden></div>
    <div class="btnrow"><button class="btn" data-act="rule-cancel">${esc(t("cancel"))}</button><button class="btn pri" data-act="rule-save">${esc(t("save"))}</button></div>
  </div>`;
  $("rule-mode").onchange = () => {
    const advanced = $("rule-mode").value === "advanced";
    if (advanced) { try { $("rule-raw").value = buildDialogRule(); } catch {} }
    $("rule-form").hidden = advanced; $("rule-advanced").hidden = !advanced;
  };
  $("rule-freq").onchange = () => { $("rule-weekdays").hidden = $("rule-freq").value !== "WEEKLY"; };
  $("rule-end").onchange = () => { $("rule-until-row").hidden = $("rule-end").value !== "date"; $("rule-count-row").hidden = $("rule-end").value !== "count"; };
  dialog.showModal();
}

function buildDialogRule() {
  if (!$("rule-interval").reportValidity()) throw new Error(t("rp_invalid"));
  const freq = $("rule-freq").value;
  let rule = `FREQ=${freq};INTERVAL=${$("rule-interval").value}`;
  const days = [...$("rule-weekdays").querySelectorAll("input:checked")].map(el=>el.value);
  if (freq === "WEEKLY" && days.length) rule += ";BYDAY=" + days.join(",");
  if ($("rule-end").value === "date") {
    $("rule-until").required = true;
    if (!$("rule-until").reportValidity()) throw new Error(t("rp_invalid"));
    rule += ";UNTIL=" + $("rule-until").value.replaceAll("-", "");
  }
  if ($("rule-end").value === "count") {
    if (!$("rule-count").reportValidity()) throw new Error(t("rp_invalid"));
    rule += ";COUNT=" + $("rule-count").value;
  }
  return rule;
}

function cancelRuleDialog() {
  $("tk-repeat").value = $("tk-repeat").dataset.previous || "";
  $("rule-dialog").close();
}

async function saveRuleDialog() {
  const button = $("rule-dialog").querySelector('[data-act="rule-save"]');
  button.disabled = true;
  try {
    const repeat = $("rule-mode").value === "advanced" ? $("rule-raw").value.trim() : buildDialogRule();
    const due = $("tk-due").value || defaultDueText();
    const preview = await call("task.repeat_preview", { repeat, due_at: due });
    $("tk-repeat").dataset.rule = preview.repeat;
    $("tk-repeat").dataset.previous = "rules";
    $("tk-repeat").value = "rules";
    $("tk-edit-rule").hidden = false;
    setTaskAllDay($("rule-allday").checked);
    $("rule-dialog").close();
  } catch (e) { $("rule-error").hidden = false; $("rule-error").textContent = e.message; }
  finally { button.disabled = false; }
}

async function taskDialogSave(id) {
  if ($("tk-list-picker")?.dataset.loading === "true") return;
  const title = $("tk-title").value.trim();
  if (!title) { $("tk-title").focus(); return; }      // 空标题不提交，焦点退回让用户看见原因
  const dueRaw = $("tk-due").value;
  const allDay = $("tk-allday").checked;
  const due = dueRaw ? (allDay ? dueRaw.slice(0, 10) : (dueRaw.length === 16 ? dueRaw + ":00" : dueRaw)) : null;
  const on = $("tk-prio").querySelector("[data-prio].on");
  const priority = on ? Number(on.dataset.prio) : 4;
  const tags = $("tk-tags").value.split(/[,，]/).map((s) => s.trim()).filter(Boolean);
  const list_id = $("tk-list").value || null;
  const choice = $("tk-repeat").value;
  const repeat = choice === "rules" ? $("tk-repeat").dataset.rule : (["", "allday"].includes(choice) ? null : choice);
  if (choice === "rules" && !repeat) { openRuleDialog(); return; }
  if (repeat && !due) { showError(new Error(t("repeat_needs_due"))); $("tk-due").focus(); return; }
  const params = { title, due_at: due, due_all_day: allDay, priority, tags, list_id, repeat: repeat || null };
  try {
    if (id) await mutate("task.update", { item_id: id, ...params });
    else await mutate("task.create", params);
    $("mask").hidden = true;
  } catch (e) { showError(e); }
}

/** 导入 Qingdan 导出的 JSON。幂等合并：只写入比本地更新的版本，重复导入 applied=0。
    2026-11-10 补界面入口 —— 此前 README 承诺了导入但设置页里没有按钮。 */
async function doImport(input) {
  const f = input.files && input.files[0];
  input.value = "";                       // 先清，否则同文件第二次选不触发 change
  if (!f) return;
  const text = await f.text();
  if (!confirm(t("import_confirm", { name: f.name,
                                     kb: Math.max(1, Math.round(f.size / 1024)) }))) return;
  try {
    const out = await mutate("data.import", { content: text });
    snackbar(t("import_done", { applied: out.applied, skipped: out.skipped,
                                invalid: out.invalid }), false);
    await loadAll();
  } catch (e) { showError(e); }
}

// --------------------------------------------------------------------------- 任务右键菜单

function closeTaskMenu() { const m = $("ctxmenu"); if (m) m.hidden = true; }

/** 2026-11-10 用户参照滴答要求。只放有后端支撑的动作（宁缺毋假）：
    完成/重开、日期快设、优先级、跳过这次（仅重复任务）、贪睡、创建副本、编辑、删除。 */
async function openTaskMenu(x, y, id) {
  const it = state.tasks.find((k) => k.id === id) || await call("task.get", { item_id: id });
  const d = (it && it.data) || {};
  const done = d.status === "done";
  const prio = d.priority ? Number(d.priority) : 4;
  const m = $("ctxmenu");
  m.innerHTML = `
    <button class="ci" data-act="ctx-toggle" data-id="${esc(id)}">${esc(t(done ? "reopen" : "complete"))}</button>
    <div class="sep"></div>
    <div class="cl">${esc(t("d_due"))}</div>
    <button class="ci" data-act="ctx-due" data-id="${esc(id)}" data-v="today">📅 ${esc(t("today"))}</button>
    <button class="ci" data-act="ctx-due" data-id="${esc(id)}" data-v="tomorrow">📅 ${esc(t("ctx_tomorrow"))}</button>
    <button class="ci" data-act="ctx-due" data-id="${esc(id)}" data-v="clear">✕ ${esc(t("ctx_clear_due"))}</button>
    <button class="ci" data-act="ctx-sched" data-id="${esc(id)}">🕒 ${esc(t("ctx_schedule"))}</button>
    <div class="cl">${esc(t("d_prio"))}</div>
    <div class="prow">${[1, 2, 3, 4].map((p) =>
      `<button data-act="ctx-prio" data-id="${esc(id)}" data-v="${p}" style="--pc:${PC[p]}"
         class="${prio === p ? "on" : ""}" title="${p < 4 ? "P" + p : t("p_none")}">${p < 4 ? "⚑" : "—"}</button>`).join("")}</div>
    <div class="sep"></div>
    ${d.repeat ? `<button class="ci" data-act="ctx-skip" data-id="${esc(id)}">⏭ ${esc(t("rp_skip"))}</button>` : ""}
    <button class="ci" data-act="ctx-snooze" data-id="${esc(id)}">◷ ${esc(t("snooze_t"))}</button>
    <button class="ci" data-act="ctx-move" data-id="${esc(id)}">▤ ${esc(t("move_to"))}</button>
    <button class="ci" data-act="ctx-dup" data-id="${esc(id)}">⧉ ${esc(t("ctx_duplicate"))}</button>
    <button class="ci" data-act="ctx-edit" data-id="${esc(id)}">✎ ${esc(t("edit_task_t"))}</button>
    <div class="sep"></div>
    <button class="ci danger" data-act="ctx-del" data-id="${esc(id)}">🗑 ${esc(t("del"))}</button>`;
  if (it.deleted) m.innerHTML = `<button class="ci" data-act="restore" data-id="${esc(id)}">↶ ${esc(t("restore"))}</button>`;
  m.hidden = false;
  m.setAttribute("role", "menu");
  m.querySelectorAll("button").forEach(b => b.setAttribute("role", "menuitem"));
  m.onkeydown = (ev) => {
    const buttons = [...m.querySelectorAll("button")];
    const i = buttons.indexOf(document.activeElement);
    let next;
    if (ev.key === "ArrowDown") next = (i + 1) % buttons.length;
    if (ev.key === "ArrowUp") next = (i - 1 + buttons.length) % buttons.length;
    if (ev.key === "Home") next = 0;
    if (ev.key === "End") next = buttons.length - 1;
    if (next !== undefined) { ev.preventDefault(); buttons[next].focus(); }
  };
  m.querySelector("button")?.focus();
  // 视口夹取：菜单不许超出屏幕右缘/下缘
  const r = m.getBoundingClientRect();
  m.style.left = Math.max(4, Math.min(x, innerWidth - r.width - 6)) + "px";
  m.style.top = Math.max(4, Math.min(y, innerHeight - r.height - 6)) + "px";
}

function iconPicker(value, fallback) {
  const choices=['','📁','💼','🏠','⭐','🎯','📚','💡','🛒','❤️','🔔','✈️','🛠️','📌','☀️','🌱'];
  if(value && !choices.includes(value))choices.push(value);
  return `<div class="field icon-picker-field"><label>${esc(t('f_icon'))}</label><div class="catalog-icon-picker"><input id="catalog-icon" type="hidden" value="${esc(value || '')}">${choices.map(icon=>`<button class="catalog-icon-choice ${icon===(value||'')?'on':''}" data-act="catalog-icon" data-icon="${esc(icon)}" aria-pressed="${icon===(value||'')}" title="${esc(icon||t('icon_default'))}">${esc(icon||fallback)}</button>`).join('')}</div></div>`;
}

async function openTagDialog(id = null) {
  const tag = id ? await call("tag.get", { item_id: id }) : null;
  openModal(`<div class="mh">${esc(t(id ? "edit_tag" : "new_tag_t"))}<button class="iconbtn" data-act="close-modal">✕</button></div>
    <div class="mb"><div class="field"><label>${esc(t("g_tag"))}</label><input class="ctl" id="tag-name" value="${esc(tag?.data.name || '')}"></div>
    ${iconPicker(tag?.data.icon,'#')}
    <div class="btnrow"><button class="btn pri" data-act="tag-save" data-id="${esc(id || '')}">${esc(t("save"))}</button>${id?`<button class="btn danger" data-act="tag-delete" data-id="${esc(id)}">${esc(t("del"))}</button>`:''}</div></div>`);
  $("tag-name").focus();
}

async function openListDialog(id = null) {
  const list = id ? state.groups.find(g => g.id === id) || await call("group.get", { item_id: id }) : null;
  openModal(`<div class="mh">${esc(t(id ? "edit_list" : "new_list"))}<button class="iconbtn" data-act="close-modal">✕</button></div>
    <div class="mb"><div class="field"><label>${esc(t("g_list"))}</label><input class="ctl" id="list-name" value="${esc(list?.data.name || "")}"></div>
    ${iconPicker(list?.data.icon || list?.data.emoji,'▤')}
    <div class="btnrow"><button class="btn pri" data-act="list-save" data-id="${esc(id || "")}">${esc(t("save"))}</button>
    ${id ? `<button class="btn danger" data-act="list-delete" data-id="${esc(id)}">${esc(t("del"))}</button>` : ""}</div></div>`);
  $("list-name").focus();
}

async function openFilterDialog(id = null) {
  const filter=id?await call('filter.get',{item_id:id}):null;
  openModal(`<div class="mh">${esc(t(id?'edit_filter':'new_filter_t'))}<button class="iconbtn" data-act="close-modal">✕</button></div><div class="mb"><div class="field"><label>${esc(t('g_filter'))}</label><input class="ctl" id="filter-name" value="${esc(filter?.data.name||'')}"></div>${iconPicker(filter?.data.icon,'⚑')}<p class="desc">${esc(t(id?'filter_preserve_rule':'filter_default_rule'))}</p><div class="btnrow"><button class="btn pri" data-act="filter-save" data-id="${esc(id||'')}">${esc(t('save'))}</button>${id?`<button class="btn danger" data-act="filter-delete" data-id="${esc(id)}">${esc(t('del'))}</button>`:''}</div></div>`);
  $('filter-name').focus();
}

async function openCountdownDialog(id) {
  const c = id ? state.countdowns.find((x) => x.id === id) : null;
  openModal(`
    <div class="mh">${esc(t(c ? "d_title" : "new_countdown_t"))}<button class="iconbtn" data-act="close-modal">✕</button></div>
    <div class="mb">
      <div class="field"><label>${esc(t("d_title"))}</label>
        <input class="ctl" type="text" id="cd-title" value="${esc(c?.title || "")}"></div>
      <div class="field"><label>${esc(t("d_due"))}</label>
        <input class="ctl" type="date" id="cd-date" value="${esc(c?.target_date || "")}"></div>
      <div class="btnrow">
        <button class="btn pri" data-act="cd-save" data-id="${esc(id || "")}">${esc(t("save"))}</button>
        ${id ? `<button class="btn danger" data-act="cd-del" data-id="${esc(id)}">${esc(t("del"))}</button>` : ""}
      </div>
    </div>`);
}

async function countdownSave(id) {
  const title = $("cd-title").value.trim();
  const target = $("cd-date").value;
  if (!title || !target) return;
  try {
    await mutate(id ? "countdown.update" : "countdown.create",
                 id ? { item_id: id, title, target_date: target } : { title, target_date: target });
    $("mask").hidden = true;
  } catch (e) { showError(e); }
}

// --------------------------------------------------------------------------- 启动

let externalRefreshBusy = false;
function canRefreshExternal() {
  if (!state.booted || state.busy || document.hidden || !$("mask").hidden || $("rule-dialog")?.open) return false;
  if (document.activeElement?.matches("input,textarea,select,[contenteditable=true]")) return false;
  if (["quickadd", "noteadd"].some(id => $(id)?.value.trim())) return false;
  if (state.detail && (("title" in state.detail.data && $("title") && $("title").value.trim() !== state.detail.data.title)
      || ($("note") && $("note").value !== (state.detail.data.note || "")))) return false;
  return true;
}
async function refreshExternalChanges() {
  if (externalRefreshBusy || !canRefreshExternal()) return;
  externalRefreshBusy = true;
  try {
    const health = await call("app.health");
    if (JSON.stringify(health.change_token) === JSON.stringify(state.health?.change_token)) return;
    const [ap, vp] = await Promise.all([call("app.pref_get"), call("view.pref_get")]);
    if (!canRefreshExternal()) return;
    const prefs = { ...vp.prefs, ...ap.prefs };
    patch({appPrefs: ap.prefs || {}, viewPrefs: vp.prefs || {},
      view: prefs.view || state.view, layout: prefs.layout || state.layout,
      group: prefs.group || state.group, sort: prefs.sort || state.sort,
      reverse: prefs.reverse === undefined ? state.reverse : !!prefs.reverse,
      colorBy: prefs.color_by || state.colorBy});
    applyCollapse();
    const opts = {guard: canRefreshExternal};
    await Promise.all([loadAll(opts), loadAlert(opts), loadSync(opts)]);
  } catch (error) { console.warn("[external-refresh]", error); }
  finally { externalRefreshBusy = false; }
}

async function boot() {
  configureAccessUI(() => openSettings('access'), showError);
  subscribeCatalog(() => {
    state.groups = catalogItems("group"); state.tags = catalogItems("tag"); state.filters = catalogItems('filter');
    renderSidebar();
  });
  document.addEventListener("scroll", ev => {
    const el = ev.target;
    if (!el.matches?.(".catalog-scroll")) return;
    const c = catalogs[el.dataset.catalog];
    c.scrollTop = el.scrollTop;
    scrollCatalog(el.dataset.catalog, el.scrollTop).catch(showError);
  }, true);
  // 任务行右键菜单。非任务区域的右键保持浏览器默认（开发者要能检查元素）。
  document.addEventListener("contextmenu", (ev) => {
    const row = ev.target.closest('.task, .ev[data-act="open"]');
    if (!row) { closeTaskMenu(); return; }
    ev.preventDefault();
    openTaskMenu(ev.clientX, ev.clientY, row.dataset.id);
  });
  // 点菜单外任意处关闭（捕获阶段，先关再让委托处理菜单项自己的点击）
  document.addEventListener("click", (ev) => {
    closeTaskMenu();
  }, true);
  addEventListener("keydown", (ev) => { if (ev.key === "Escape") closeTaskMenu(); });
  addEventListener("scroll", closeTaskMenu, true);
  document.addEventListener("click", (ev) => {
    const el = ev.target.closest("[data-act]");
    if (el?.dataset.act === "cd-save") { ev.stopPropagation(); return countdownSave(el.dataset.id); }
    if (el?.dataset.act === "cd-del") {
      ev.stopPropagation();
      mutate("countdown.delete", { item_id: el.dataset.id }).then(() => { $("mask").hidden = true; }).catch(showError);
      return;
    }
    onClick(ev).catch(showError);
  });
  document.addEventListener("change", (ev) => {
    const el = ev.target.closest("[data-act]");
    if (el && el.dataset.act === "cf-edit") {
      const c = conflictCard(el.dataset.id);
      if (!c) return;
      const field = c.fields.find((f) => f.key === el.dataset.k);
      const raw = el.value.trim();
      c.edits[el.dataset.k] = field && field.type === "list" && raw
        ? raw.split(/[,\uff0c]/).map((s) => s.trim()).filter(Boolean)
        : raw;
      delete c.picks[el.dataset.k];
      return renderConflicts();
    }
    return onChange(ev).catch(showError);
  });
  document.addEventListener("keydown", (ev) => onKeydown(ev).catch(showError));

  try {
    await loadFeatures();
    const [ap, vp, health] = await Promise.all([
      call("app.pref_get"), call("view.pref_get"), call("app.health")]);
    const prefs = { ...vp.prefs, ...ap.prefs };
    patch({
      appPrefs: ap.prefs || {},
      viewPrefs: vp.prefs || {},
      view: prefs.view || "today",
      layout: prefs.layout || "list",
      group: prefs.group || "date",
      sort: prefs.sort || "smart",
      reverse: !!prefs.reverse,
      colorBy: prefs.color_by || "prio",
      health,
    });
    applyCollapse();          // 折叠状态跨会话保留：首屏就要是用户上次留下的样子
    applyTheme();
    await Promise.all([loadAll(), loadAlert(), loadData()]);
    patch({ booted: true });
    render();
    setInterval(refreshExternalChanges, 2000);
    addEventListener("focus", refreshExternalChanges);
  } catch (e) {
    $("scroll").innerHTML = `<div class="empty">${t("boot_fail")}</div>
      <div class="btnrow" style="justify-content:center"><button class="btn pri" data-act="retry">${esc(t("retry"))}</button></div>`;
    console.error("[boot]", e);
  }
}

// 渲染抛错必须看得见：subscribe→render 里的异常不会被 boot 的 catch 兜住，
// 后果是界面停在旧状态而一切"看起来正常"（2026-11-10 的 TDZ 事故就是这个形态）。
// 挂全局 error 钩子把错误顶到屏幕上，而不是留给用户猜。
addEventListener("error", (e) => {
  const el = $("toast");
  if (el && e.message) { el.hidden = false; el.textContent = t("render_err") + e.message; }
});

subscribe(render);
boot();
