## 清单（Qingdan）桌面客户端 软件开发文档 v0.2

版本 v0.2 ｜ 日期 2026-11-05 ｜ 目标形态：Windows exe ｜ Python 3.14 ｜ 体积优先

**v0.2 变更**：① GUI 由 PySide6 改为 pywebview + WebView2（体积从 ~90MB 压到 ~20MB）；② 新增告警子系统（钉钉机器人 / 邮箱 SMTP / Windows 通知）；③ 新增视图体系（今天/近一周/收集箱/已完成/垃圾桶）、分组、任务颜色、日历、倒数日、番茄专注；④ 补竞品对照与借鉴清单。同步引擎与接口契约部分沿用 v0.1，结论未变。

---

## 0. 需求追溯表

| # | 原始需求 | 设计落点 | 验收 |
|---|---|---|---|
| R1 | 清单 exe，记录任务 | §3 §5 §15 §17 | exe 双击可用，离线全功能 |
| R2 | 可不登录；可 Gitee 登录 | §16 双态、`AUTH_REQUIRED` | 未登录不阻断任何编辑 |
| R3a | 登录后查仓库，没有则创建 | §14 幂等就绪流程 | 反复点击结果一致 |
| R3b | 双向同步、删除判定、以更新时间为准、新装机判断 | §10–§11 | §19.3 场景矩阵 22 条通过 |
| R4 | 每个功能必须有接口 | §13 `@feature` 注册表 | §19.4 契约测试 1:1 |
| R5 | Python 3.14，体积尽量小 | §4 选型、§17 打包 | onedir ≤ 30MB / onefile ≤ 20MB |
| R6 | 到期告警：钉钉、邮箱、系统通知，可关闭 | §12 告警子系统 | 三渠道实发成功 + 三级开关生效 |
| R7 | 分组、标签、重要程度、任务标色 | §6 属性规格、§7 模型 | 视图按分组渲染、颜色生效 |
| R8 | 任务日历、倒计时 | §5 视图、§6.7–6.8 | 月/周/日拖拽改期；倒数日天数正确 |
| R9 | 今天/近一周/收集箱/已完成/垃圾桶 | §5.1 内置视图 | 五个视图查询语义与截图一致 |
| R10 | 仓库权限不能公开；项目列表尽量不显示 | §14.2 权限与隐私（私有写死 + 防公开哨兵 + 历史残留说明） | §19.8 权限回归 4 条 |

---

## 1. 目标与非目标

**v1 目标**：单机可用的任务清单（任务/子任务/清单/文件夹/分组/标签/过滤器/优先级/颜色/日历/倒数日/番茄专注/回收站/搜索/撤销）；Gitee 令牌登录后多机同步且删除语义正确；到期告警三渠道且可关；所有功能先做接口、UI 只是接口的消费者。

**v1 非目标**：多人协作与共享清单、附件（图片/录音，二期走 Git LFS）、习惯打卡、笔记清单（富文本长文）、Webhook/服务端推送、订阅第三方日历（CalDAV）、macOS/Linux/手机端、自动更新、**四象限视图（不做）**。

---

## 2. 竞品对照与借鉴

调研了滴答清单（Dida/TickTick）、Todoist、Microsoft To Do、优效日历。结论：**我们的功能集合对标"滴答清单 2023 年水位"，但体积必须对标它的安装包（Windows 版 21.35MB）**。

| 能力 | 滴答清单 | Todoist | 本项目 v1 | 说明 |
|---|---|---|---|---|
| 内置智能视图 | 今天/最近7天/收集箱/四象限/已完成/垃圾桶 | Inbox/Today/预览(Next 7 days)/过滤器&标签 | ✅ 五个视图（§5.1） | 用户截图即此模型 |
| 清单 + 文件夹分组 | ✅ | 项目 + 版块(Section) | ✅ folder + section 两层 | |
| 清单内分组/排序 | 2023-04 才上 | 分组/排序分离 | ✅ group by + sort by 独立 | Todoist 的分组与排序是两个正交下拉，比滴答清晰 |
| 标签 | ✅ | ✅ `@label` | ✅ 独立 Item | |
| 自定义过滤器 | 会员功能 | 高级功能 | ✅ 免费，存为 Item | 我们用不到会员体系，直接给 |
| 优先级 | 旗帜 4 级 + 四象限 | P1–P4 | ✅ 4 级旗帜（四象限不做） | 四象限需要"重要/紧急"两套规则且派生逻辑易与用户预期冲突，v1 用优先级 + 今天视图已够用 |
| 任务/清单颜色 | 清单自定义色(2022-07)、日历按类型配色 | 日历默认按优先级着色，可切标签/项目 | ✅ 三级色：优先级色→清单色→任务自定义色覆盖 | Todoist 的"着色维度可切换"是低成本高收益设计 |
| 日历视图 | 年/月/周/日/多日/多周/日程 + 拖拽 | 日/周/3天/月布局 | ✅ 月/周/日 + 拖拽改期 | 年视图/热力图不做 |
| 看板/时间线 | ✅ 看板 + 时间线 | ✅ 看板 | ✅ 看板（按分组/状态） | 时间线（甘特轻量版）二期 |
| 倒数纪念日 | 2025-05 才上，支持天/周/年切换、分组、生日显岁数 | ❌ | ✅ 独立 `countdown` 类型 | 用户明确要"倒计时" |
| 番茄专注 | ✅ 内置 + 白噪音 + 严格模式 | ❌ | ✅ 基础计时，记录仅存本地 | 白噪音/严格模式不做 |
| 提醒 | 多渠道 + 持续提醒 + 结束时提醒 + 位置提醒 | 提醒 + 移动端推送 | ✅ 三渠道 + 免打扰 + 持续提醒 | 位置提醒不做 |
| 自然语言快速添加 | ✅ "明天下午3点开会" | ✅ 最强项 | ⚠️ v1 只做有限解析（日期/时间/#清单/@标签/!优先级） | 这是 Todoist 护城河，但正则+中文日期词表可覆盖 80% |
| 重复规则 | 周/月/年/法定工作日/农历 | 循环到期日 | ✅ 日/周/月/年 + 自定义 RRULE（2026-11-10 真落地，见 §6.5） | 支持子集：`FREQ`/`INTERVAL`/`BYDAY`(仅周)/`BYMONTHDAY`(含 `-1`)/`UNTIL`/`COUNT`；`BYSETPOS`、序数 `BYDAY`(-1MO)、农历与法定工作日留二期（需节假日数据）。不支持的键**写入时就拒绝**，不收下来再静默不推进 |

**直接抄的三条规则**（省掉自己踩坑）：

1. **默认排序**用 Todoist 的智能序：`日期时间 → 优先级 → 截止期限 → 手动序 → 创建时间`，并允许切到纯手动序。
2. **今天视图只显示有日期的任务**（含逾期），未排期任务永远只在收集箱和清单里出现 —— 这条规则决定了"今天"视图是否可信，滴答和 Todoist 都这么做。
3. **视图配置按设备独立**（滴答 2026-06 专门加了"视图多端独立配置"）：列宽、折叠状态、当前视图、排序方式属设备本地，不进同步；只有任务数据、清单、标签、过滤器定义进同步。这条直接避免多机器互相干扰，也简化了冲突面。

---

## 3. 总体架构

```
┌──────────────────────────────────────────────────────────┐
│ 窗口壳  pywebview(WebView2) — 只做窗口/托盘/通知，无业务   │  ~5MB
├──────────────────────────────────────────────────────────┤
│ 前端  静态 HTML + CSS + 原生 JS（打包进 exe，本地加载）    │  无框架依赖
│      全部通过 fetch('/api/v1/...') 调后端，零特权          │
├──────────────────────────────────────────────────────────┤
│ 服务  FastAPI + uvicorn，监听 127.0.0.1:随机端口           │
│       ├─ /api/v1/**  业务接口（R4）                        │
│       └─ /**         前端静态资源                          │
├──────────────────────────────────────────────────────────┤
│ Core 领域内核（纯 Python，无 GUI/无 HTTP 依赖）             │
│  registry / service / store(SQLite) / clock(HLC)           │
│  view(视图查询) / merge(三方合并) / sync(引擎)              │
│  alert(告警调度+渠道适配器) / focus(番茄) / backup          │
├──────────────────────────────────────────────────────────┤
│ Transport  dulwich(git) 主 / git.exe 备；Gitee REST v5     │
└──────────────────────────────────────────────────────────┘
```

分层铁律不变：Core 不 import pywebview/FastAPI；前端不碰 SQLite；同步引擎不反向依赖 UI。

**为什么这个组合能同时满足 R4 和 R5**：接口层是唯一的业务入口（HTTP），前端和 CLI 都是它的消费者。传统 Qt 方案要在 Python 里再写一层界面逻辑（等于两套业务代码）；WebView 方案下 UI 只是接口的一份 HTML 客户端，Qt 那 60MB 直接省掉。副作用是：所有能在 UI 上点的操作，天然就能用 curl 调 —— R4 自动成立。

---

## 4. 技术选型与体积预算

| 关注点 | 选型 | 体积/理由 |
|---|---|---|
| Python | **3.14**（标准 GIL 构建，不用 free-threading） | 解释器+stdlib 打进包 ~12MB |
| 窗口壳 | **pywebview**（EdgeChromium/WebView2 后端） | 纯 Python + COM，复用系统 WebView2 运行时 |
| 前端 | 原生 ES2022 + CSS（无 React/Vue），日历与看板自己画 | dist 后 ~300KB，无 node_modules 进包 |
| 后端框架 | FastAPI + uvicorn（进程内线程启动，不起子进程） | ~8MB（含 pydantic-core Rust 扩展） |
| 存储 | SQLite（标准库 `sqlite3`，WAL；搜索用 LIKE，**不用 FTS5**，见 §8.1） | 0MB（在 stdlib 里） |
| Git | dulwich（纯 Python） | ~2MB |
| 通知 | `winotify`（纯 Python，WinRT toast）+ 托盘 `pystray` 或 WebView 常驻托盘自绘 | <1MB |
| 打包 | PyInstaller 6.x **onedir**，UPX 压缩 exe | 见下表 |

**体积预算（onedir）**

M0 spike 已实测（只含 pywebview + pythonnet，未含 FastAPI 全家桶）：**26 MB**。构成拆解（实测值，不是估算）：

| 文件 | 实测 | 能否去掉 |
|---|---|---|
| `python314.dll` | 6.5 MB | ❌ 解释器本体 |
| `spike.exe`（引导器 + PYZ 归档） | 5.2 MB | ❌ |
| `libcrypto-3.dll` + `libssl-3.dll` | 5.8 MB | ❌ httpx 与 dulwich 走 HTTPS 都要 |
| `base_library.zip` | 1.3 MB | ⚠️ 可裁剪 |
| `ucrtbase.dll` | 1.1 MB | ❌ VC 运行时，UPX 必须排除 |
| `unicodedata.pyd` / `_zstd.pyd` / `_decimal.pyd` 等 | 1.7 MB | ⚠️ 部分可裁 |
| `Microsoft.Web.WebView2.Core.dll` | 0.6 MB | ❌ |
| `Python.Runtime.dll` | 0.4 MB | ❌ |

据此把预算表改为**实测外推**：26 MB（已测）+ FastAPI/uvicorn/pydantic-core/httpx/dulwich 约 10–12 MB（`pydantic-core` 是 Rust 扩展，单独就 2.5 MB）+ 自有代码与前端 1.5 MB ≈ **38–40 MB**。

**M6 实测结果（2026-11-05，86 个接口 + 告警 + 同步全部在内）：37.35 MB**，与估算吻合，门禁 42MB 通过（余量 4.65MB）。实测前 8 大文件：

| 文件 | 实测 | 说明 |
|---|---|---|
| `Qingdan.exe` | 8.41 MB | 引导器 + PYZ 归档（含全部自有代码） |
| `python314.dll` | 6.46 MB | 解释器，不可减 |
| `pydantic_core…pyd` | **5.49 MB** | 估算时按 2.5MB 算的，**实际翻倍** |
| `libcrypto-3.dll` | 4.99 MB | HTTPS 必需，不可减 |
| `sqlite3.dll` | 1.51 MB | 标准库自带 |
| `base_library.zip` | 1.32 MB | 可小幅裁剪 |
| `ucrtbase.dll` | 1.10 MB | VC 运行时，UPX 必须排除 |
| `libssl-3.dll` | 0.76 MB | 同上 |

**下一步唯一的体积杠杆是 `pydantic_core`（5.49MB）**：它只为 FastAPI 的请求体校验存在。真要压，可换成手写参数校验 + Starlette 裸路由，预计省 5MB 到 ~32MB。当前判断**不做**——37MB 已明显优于 Qt 方案的一半，而换掉 FastAPI 会牵动全部 86 个接口的实现与契约测试，收益/风险不划算。这个决定记在这里，避免以后反复讨论。

**打包踩到的真实坑**：spec 里排除 `http.server` 导致 exe 启动即崩——pywebview 经 `wsgiref.simple_server` 依赖它。这正是本文件此前警告过的失败模式（"排错的表现是运行时 ImportError，windowed 下极难定位"），结果当场应验在自己写的代码上。修法是 stdlib 的间接依赖不凭直觉砍，并在入口加 `crash.log` 落盘兜底。

**第二个同类坑（M6 托盘）**：为了压体积把 `PIL.IcoImagePlugin` 排掉了，结果托盘线程崩在 `KeyError: 'ICO'` —— pystray 在 Windows 上要把图标**在内存里转成 ICO** 再交给 `Shell_NotifyIcon`。而且它崩在自己的线程里，主程序照常启动、接口照常响应，属于"看起来一切正常、通知全哑"最坏的一类故障。修法：ICO/BMP 插件不许排除，并做成静态守卫测试（注释挡不住下一次压体积）。

### 4.1 M6 实测体积（含托盘与图标）

| 阶段 | 体积 | 说明 |
|---|---|---|
| M0 spike | 26 MB | 只有 pywebview + pythonnet |
| M6 首次（无托盘） | 37.35 MB | 86 接口 + 告警 + 同步 |
| 加 pystray + Pillow | **48.67 MB ❌ 破门禁** | Pillow 拖进 `_avif.pyd` 7.52MB（我们只需读一个 PNG） |
| 排除无用 PIL 插件 | **40.43 MB ✅** | 余量 1.57MB |
| 设置页接备份/导出/自启 + 文案机器键化 | **40.46 MB ✅** | 余量 1.54MB，门禁 42MB PASS |

**托盘 + 图标的真实代价是 3MB**（37.35 → 40.43）。余量只剩 1.5MB，以后加依赖要当心动这条线 —— 这正是门禁存在的意义：它会在越界当天就拦住，而不是等交付时才发现。本轮加备份/导出/自启三块功能只涨了 **0.03MB**（全标准库 + 前端字符串），说明这条预算线还有空间，但也提醒：余量已经薄到一次小依赖就能吃掉。

### 4.2 通知后端的最终结论

`alert.status` 实测 `backend=balloon`：**托盘气球（Shell_NotifyIcon）不需要 AUMID**，未打包应用也能正常显示，因此它是当前唯一"调用成功 = 用户真看到"的后端。优先级：`balloon`（托盘在跑就自动启用）→ `dialog`（MessageBoxW，一定能看见）→ `toast`（winotify，仅在打包注册 AUMID 后才值得选）。

**CI 体积门禁从 30 MB 上调到 42 MB**（含 2 MB 余量）。原 30MB 是我按估算拍的，实测发现 SSL 与解释器就吃掉 18 MB，硬卡 30 会让构建天天红、最后被人直接删掉这条检查——门禁要能长期存活，必须定在实测之上而不是愿望之上。

对比基准不变：PySide6 方案 75–90MB，Electron 180MB，滴答清单安装包 21.35MB。我们 38–40MB 仍只有 Qt 方案的一半，且这 40MB 里有 18MB 是 Python 解释器 + SSL 的固定成本，与功能多少无关。

**裁剪手段（写进 spec，逐条生效）**

1. `excludes` 列表：`tkinter`、`unittest`、`pydoc_data`、`setuptools`、`pip`、`numpy`（若被间接拖入）、`PySide6`/`PyQt`（防误装）、`test/*`。
2. `zipfiles` 用 `level=9`，`upx=True` 且 `upx_exclude=['vcruntime*.dll','ucrtbase.dll','python*.dll']`（压缩 VC 运行时会崩，必须排除）。
3. stdlib 裁剪靠 excludes + 手工删 `base_library.zip` 无关模块，收益有限，主要靠不装 Qt。
4. 前端资源走 `datas=[('web/dist','web')]`，运行时从 `sys._MEIPASS` 读。
5. 发布时同时给 onedir（绿色解压即用，推荐）和 onefile（单文件好传，启动慢 1–2 秒）。

**⚠️ M0 必须先做的 Spike（阻塞性，别跳过）**

`Python 3.14 + pywebview + PyInstaller` 这条链上有一个已知脆弱点：pywebview 在 Windows 上依赖 **pythonnet（CLR 绑定）** 来承载 WebView2。pythonnet 是编译扩展，新版本 Python 的 wheel 常滞后数月。因此 M0 第一天要跑：

```
py -3.14 -m venv .venv && .venv\Scripts\pip install pywebview pyinstaller
python -c "import webview; webview.create_window('t','data:text/hi'); webview.start('edgechromium')"
pyinstaller --onedir --noconsole spike.py && dist\spike\spike.exe
```

判定与降级顺序：

1. 全绿 → 按本方案走。
2. pythonnet 无 cp314 wheel → **改用 Python 3.13 打包**（体积结论完全不变，这是最小代价路径），文档其余部分不受影响。
3. 3.13 也装不上 pywebview → 换 `comtypes` 直连 WebView2 COM 接口自写宿主（+2 人日）。
4. 都不行 → 退化为"无窗口壳"模式：FastAPI + 启动时 `webbrowser.open()`，体积降到 ~15MB，代价是关掉浏览器就没界面、托盘能力弱。

---

## 5. 视图与信息架构（R9）

### 5.1 侧边栏结构（对齐截图）

```
智能视图   今天 · 近一周 · 收集箱 · 已完成 · 垃圾桶
分组       [分组文件夹：工作 / 生活] → 工作任务 · 个人备忘 · 学习安排(3) · 锻炼计划   ← 可折叠
标签       以标签维度聚合展示不同分组的任务
过滤器     用户自定义条件组合（免费）
```

术语对应（UI 叫法 ↔ 内部 kind）：**清单 = `list`**、**分组 = `folder`**、清单内小节 = `section` —— 但**界面不提供清单/分组管理入口**（2026-11-10 先按滴答式两级做过一版树，用户试用后决定去掉：侧栏删除整块，任务统一落收集箱）。后端 `list/folder/section` 接口与既有数据**保留不动**（老数据不迁移不删除，契约测试仍全覆盖），只是界面上不再出现；将来要恢复，侧栏加回一棵树即可，数据层零改动。代码与仓库路径仍用 `list/folder/section`，不改数据格式。四象限不做（v1 明确排除，见 §1）。

### 5.2 五个内置视图的精确语义（必须写死，否则实现会漂）

| 视图 | 查询定义 | 关键细节 |
|---|---|---|
| **今天** | `due_at <= 今天23:59 AND status IN (todo,doing) AND NOT deleted` | 含**逾期**任务（逾期是今天必须处理的）；已完成任务当日仍显示在底部折叠区；无日期任务**不出现** |
| **近一周** | `due_at <= 今天+7天 AND status未完成 AND NOT deleted` | 同样含逾期；按日期分组（今天/明天/周四/…/逾期） |
| **收集箱** | `list_id IS NULL AND NOT deleted` | 未排分组的任务落这里（GTD Inbox）。新建任务默认分组可在设置里改（默认收集箱） |
| **已完成** | `status = done`，按 `done_at` 倒序，分组：今天/昨天/更早 | 只显示 30 天内，更久走搜索；支持"重新打开" |
| **垃圾桶** | `deleted = true`，按 `deleted_at` 倒序 | **本地保留 30 天后不再显示**，但 git 里的墓碑保留 90 天（§11）；支持"恢复"和"彻底清空"（清空只删本地显示标记，不动远端墓碑，防误删传播） |

实现约束：视图 = 一个纯查询函数 `view.query(name, today_date)`，**不存数据副本**，切换视图不复制任务。`today_date` 参数化传入，跨天/跨时区测试才能做（§19.2）。

### 5.3 分组与排序（两个正交下拉）

- **分组**：不分组 / 按日期 / 按分组 / 按优先级 / 按标签 / 按状态 / 按创建时间（组内可拖拽排序）。
- **排序**：智能序（默认，§2 规则1）/ 截止日期 / 优先级 / 标题 / 创建时间 / 手动 / 修改时间，方向可反转。
- 分组与排序的选择存 `view_pref` 表，**设备本地、不同步**（§2 规则3）。

---

## 6. 任务属性规格（R7 R8）

### 6.1 重要程度（优先级）

`priority: 1|2|3|4` ↔ UI 显示 P1(紧急·红)/P2(高·橙)/P3(中·蓝)/P4(无·灰)，对齐 Todoist 的四级与色带。默认 4。不做四象限派生视图。

### 6.2 颜色（三级覆盖，取最高优先级）

1. 任务自定义色 `task.color`（用户手动标色，最高）
2. 所属分组色 `list.color`
3. 优先级色（P1 红 / P2 橙 / P3 蓝 / P4 灰）

日历视图额外提供"着色维度"开关：按优先级 / 按标签 / 按清单（抄 Todoist）。颜色存 hex 字符串，不做主题色板依赖，保证同步后另一台机器渲染一致。

### 6.3 分组层级

`folder（分组）→ list（清单）→ section（清单内小节）→ task → subtask（parent_id）`。folder/list/section 都是独立 Item（§7），不用内嵌数组 —— 内嵌数组无法三方合并，这是 v0.1 就定下的硬规则。删除 folder 时其下 list 的 `folder_id` 置空并提示；删除 list（清单）时其下任务转收集箱（滴答的做法）。

### 6.4 标签

`kind=tag` 独立 Item，任务通过 `tags: [tag_id]` 引用。标签色可选。按标签视图 = 跨分组聚合（截图里"以标签的维度展示不同分组的任务"）。

### 6.5 到期与重复

`due_at`（日期或日期时间）、`due_all_day: bool`、`repeat: null | 规范值 | RRULE 字符串`、`repeat_start`（循环起点，锚）。

**实现（2026-11-10 落地，此前只是一个存下来什么都不做的字段）**：

1. **只存一个 Item + 规则，不预生成实例**。`due_at` 永远表示「当前这一次」；预生成会让同步数据量随时间线性爆炸。展开只在读侧发生（日历），写侧永远只有一条记录。
2. **规则解析在 `core/rrule.py`，自己写的最小 RFC 5545 子集**：`FREQ=DAILY|WEEKLY|MONTHLY|YEARLY` + `INTERVAL` + `BYDAY`（仅 WEEKLY）+ `BYMONTHDAY`（仅 MONTHLY/YEARLY，支持 `-1` = 最后一天）+ `UNTIL` + `COUNT`。**没有引 dateutil** —— 它只为这一个功能而来却要被 PyInstaller 整包打进 exe，而体积门禁余量只有 1.5MB；不支持的键（`BYSETPOS`、`FREQ=HOURLY`）在**写入时就拒绝**，不收下来再静默不推进。
3. **完成即顺延**（`task.complete`）：把当前这一次记进本地 `occurrence` 表，`due_at` 推到下一次、`status` 回到 `todo`、`done_at` 清空。所以重复任务不会堆在「已完成」里，历史由 `task.occurrences` 提供。规则用尽（`COUNT`/`UNTIL` 到头）时正常收尾为 `done` 并回 `repeat_ended_key`。
4. **跳过这一次**（`task.skip`）：不算完成，只推进；历史记 `kind=skipped`。对没有重复规则的任务是明确的校验错误，不是静默无反应。
5. **提醒跟着平移**：`remind_at` 按与 `due_at` 相同的位移平移，保住用户设的提前量；`snoozed_until` 一律清除（贪睡只属于那一次）。漏掉这一步的表现是「任务滚到明天、提醒留在昨天，响一次之后再也不响」。
6. **锚点纪律**：`repeat_start` 只在「开始重复」或「显式改到期日」时重设（`task.set_repeat` / `task.set_due` / `task.update` 三个入口都要维护，`set_due` 是独立接口、最容易漏）；**自动推进绝不动锚**，否则 `COUNT=10` 永远数不到头。
7. **月末语义分两种，都是刻意的**：隐式锚日**夹取**（1/31 + 每月 → 2/28，跳过整个 2 月会让用户以为任务丢了）；显式 `BYMONTHDAY=31` 按 RFC **跳过**没有 31 号的月份（用户明确指定了日号，替他改成 28 号是篡改意图，要「每月最后一天」用 `-1`）。
8. **`occurrence` 表是设备本地数据，永不进同步**（与 `view_pref` 同类）。真值只有 Item 上的 `due_at`；两台机器各自补做同一次会互相覆盖，而同步它等于把 LWW 用在流水账上。
9. **推不动就不静默**：没有到期日 → `repeat_needs_due`；规则无法识别（另一台机器同步来的脏数据）→ `repeat_rule_invalid`。视图层用 `rrule.try_parse`，任何脏规则都不许把日历带崩。
10. **前端不解析 RRULE、不算下一次**（`task.repeat_info` 后端拆解成机器键 `summary` + `upcoming` + `history`）。让前端自己拆就是「已逾期硬编码进响应体」那一类重复实现泄漏。
11. **日历展开**（`view.expand_recurring`）只画「严格晚于当前 `due_at` 且不早于今天」的次数，总量封顶 400；漏掉的过去几次由「逾期」表达，重复画只会让用户以为今天有三件事。展开出的是虚拟事件（`occurrence: true`），点开仍指向同一个 Item。

**撤销**：`undo.last` 在恢复前像时会一并删掉对应的 `occurrence` 行（前像的 `due_at` 正是被记录的那一次），否则历史里会留一条没发生过的完成。改标题再撤销不会误删，因为那时前像的 `due_at` 是尚未完成的当前次。

**双机并发**：A 推进、B 同时改标题 → 按 `merge.pick` 第 4 行「双方都改 → HLC 大者**整条**胜出」，不是字段级合并；落败方进 `conflict` 留档可找回。`repeat_start` 已进 `merge.KIND_FIELDS`，否则冲突裁决路径会以「不支持的字段」拒绝合并（这条是全量测试抓出来的）。

### 6.6 提醒

`remind_at`（可多个，存 `reminders: [ISO8601]`，v1 上限 3 个）、`remind_mode: once|sticky`（sticky = 持续提醒，弹窗不关就一直响）、`alert_channels: null|["toast","dingtalk","email"]`（null = 跟随全局默认）。

### 6.7 倒数日（R8）

`kind=countdown`：`{title, target_date, repeat_yearly, color, image_hint, count_mode: days|weeks|years, include_target_day: bool}`。显示"还剩 N 天 / 已过 N 天"，支持每年重复（生日/纪念日）、生日显岁数。到期与提前 N 天触发告警，走 §12 同一套渠道。**参与同步**（它是数据）。

### 6.8 番茄专注

`focus_session` 表：`{id, task_id, start_at, minutes, interrupted, end_reason}`。**设备本地、不同步**（计时记录属个人行为，多机同步只会制造冲突，且滴答/Todoist 也不跨端合并原始计时）。UI 提供：选任务 → 25/自定义分钟倒计时 → 结束休息 5 分钟 → 今日番茄数与累计时长。

---

## 7. 数据模型

统一 Item 抽象（同 v0.1，此处只列增量）。`kind` 全集：

| kind | 含义 | 同步 |
|---|---|---|
| `task` | 任务（含子任务，靠 `parent_id`） | ✅ |
| `list` | 分组（UI 名，旧称"清单"） | ✅ |
| `folder` | 分组文件夹 | ✅ |
| `section` | 分组内小节 | ✅ |
| `tag` | 标签 | ✅ |
| `note` | 任务备注（独立 Item，`parent_id` 指向任务） | ✅ |
| `countdown` | 倒数日/纪念日 | ✅ |
| `filter` | 自定义过滤器定义 | ✅ |
| — | 视图偏好、番茄记录、提醒已发状态、主题、告警渠道配置 | ❌ 本地（§11.5） |

`task.data` 字段：`title, note_md, list_id, folder_id, section_id, parent_id, status(todo|doing|done|cancelled), priority(0-3), color, due_at, due_all_day, remind_at[], remind_mode, alert_channels[], tags[], order, repeat, done_at, created_at`。

版本戳仍是 HLC `rev = (ms, seq, dev)`，删除仍是 `deleted: true` 的墓碑版本 —— 这两条是同步协议的地基，v0.2 未动。

---

## 8. 本地存储（SQLite，增量表）

v0.1 的 `item / sync_state / conflict / settings / oplog` 保留，新增：

```sql
-- 告警调度：谁在什么时候该发、发到哪、发没发
CREATE TABLE alert_job (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  item_id TEXT NOT NULL,           -- task 或 countdown
  fire_at INTEGER NOT NULL,        -- 绝对时间戳（本地时区解释）
  channel TEXT NOT NULL,           -- toast | dingtalk | email | (all)
  status TEXT NOT NULL,            -- pending|sent|failed|skipped|snoozed
  attempts INTEGER DEFAULT 0, next_retry INTEGER,
  sent_at INTEGER, error TEXT, dedup_key TEXT UNIQUE
);
CREATE INDEX idx_alert_due ON alert_job(status, fire_at);

CREATE TABLE alert_log (ts INTEGER, item_id TEXT, channel TEXT, level TEXT, detail TEXT);
CREATE TABLE focus_session (id TEXT PRIMARY KEY, task_id TEXT, start_at INTEGER,
                            minutes INTEGER, ended_at INTEGER, interrupted INTEGER);
CREATE TABLE view_pref (key TEXT PRIMARY KEY, value TEXT);   -- 设备本地，不同步
```

`alert_job` 由"提醒字段"派生（每次任务保存时重算该 item 的 pending 集合），是幂等重建的：删了重算即可，不存业务真值。

### 8.1 搜索为什么用 LIKE 而不是 FTS5（M1 实测推翻原方案）

原计划在 §8 里建 `item_fts` 虚拟表。M1 实测（`spike/fts_probe.py`）后放弃，原因：

| 查询 | trigram 分词 | 默认 unicode61 | LIKE |
|---|---|---|---|
| `立案`（2 字） | **0 命中** | 0 命中 | 1 |
| `周报`（2 字） | **0 命中** | 0 命中 | 1 |
| `院立案`（3 字） | 1 | 0 | 1 |
| `滨江法院`（4 字） | 1 | 0 | 1 |

FTS5 的 trigram 分词器按 **3 字符**建索引，短于 3 个字符的查询无法命中；默认 unicode61 更是把整串中文当一个 token。而中文待办搜索最常用的恰恰是「立案」「周报」「猫粮」这种两字词 —— 用 FTS5 会让最常见的查询静默返回空结果，这是比慢更糟的失败模式。

LIKE 的代价实测可接受：5000 行 × 20 次扫描 35.8ms，单次约 **1.8ms**。个人清单量级（几千条）完全够用。

结论：`search.query` 走 LIKE 子串匹配；**这条决定由 `tests/test_search.py::test_two_char_chinese_query_hits` 锁死** —— 将来谁为了"性能"把 LIKE 换回 FTS5，这条测试会直接红。数据量真涨到几万条再考虑 trigram + LIKE 双路（≥3 字走索引、2 字走 LIKE）。

### 8.2 参数绑定必须拒绝未知字段（M1 立的新规矩）

`registry.bind()` 对不在函数签名里的参数**报错而非忽略**。这不是洁癖：实现当天 `task.create` 漏了 `section_id`，UI 侧传了这个字段，静默丢弃的表现是"点了没反应"，排查成本极高。同一条规矩顺带抓出了 CLI 把 argparse 的 `cmd` 键当业务参数透传的 bug。

例外：`task.update` 这类用 `**changes` 接收可变字段集的功能，由自己按字段白名单校验（`_TASK_FIELDS`），未知字段同样报错。

---

## 9. Git 仓库数据规范

同 v0.1：`items/{kind}/{id}.json`，一个 Item 一个文件，删除写墓碑，确定性序列化（2 空格、LF、UTF-8、`sort_keys`、`ensure_ascii=False`），`manifest.json` 记 `schema/tombstone_days`，`.gitattributes` 强制 `*.json text eol=lf`。v0.2 只是 `kind` 目录多了 `folder/section/countdown/filter/note`。

---

## 10. 版本与"最新更新时间为准"

结论未变，要点复述（细节见 v0.1 §7，实现照此）：

裸比墙钟有三个坑 —— 机器时钟偏差、同毫秒并发、时钟回拨。因此"最新"定义为 HLC 三元组 `(ms + 服务器offset, 本机单调seq, device_id)` 的字典序大者胜（LWW）。ms 回拨时钳制为历史最大值；seq 每次写库 `max(已知)+1`；offset 每次同步从 Gitee 响应 `Date` 头校准，偏差 > 5 分钟时 UI 红字警告并拒绝 push。

**"偏差过大拒绝 push"这条 v0.2 才真正落地**（之前只是文档承诺）。理由：版本先后完全靠 ms，钟差 5 分钟意味着"谁的更新"是假的，推上去会把别人真实更晚的改动判成旧的 —— 而这种覆盖事后从数据上**看不出发生过**。只挡推送不挡拉取（拉下来是接受远端事实，不伤害任何人），改动保持待推送，钟校好了下一轮自动推上去。守卫在 `SyncEngine._push` 入口，`sync.status` 回 `push_blocked_key="clock_skew"` 让界面说清楚。

实现过程中挖出两个静默失效，都值得记下来：

1. **服务器时钟校准一直是死代码。** `_client()` 每次都新建 `GiteeClient`，而校准写法是"调用完再读 `probe.last_offset_ms`"—— 新对象的该字段是 0，`if offset:` 永远不成立。登录路径更直接：它自己 `GiteeClient(...)` new 了一个，把回调整个漏掉。现在改成**每个响应立刻**经 `on_offset` 写进 HLC（`_client()` 是唯一构造口，有 AST 守卫禁 direct new），偏差归零也会写回（老代码那个 `if offset:` 连 0 都当"没变化"）。原来守这条的测试只断言 `isinstance(x, int)`，所以它绿了很久。
2. **新用户第一次点"立即同步"必然 `REPO_NOT_FOUND`。** `repo.ensure` 只在"本地没有仓库配置"时被界面调用，而 `auth.login` 之后本地已经有 owner/name 了 —— 于是界面那句 `if 没有 name 才 ensure` 恰好在新用户身上不成立。建仓是"要推之前必须成立的前提"，属于同步路径而不是界面的记忆：`prepare_remote()`（哨兵 + 缺仓就建，私有）现在被 `sync.run`/`sync.push`/`sync.compact` 三个推送入口共用，且顺序上哨兵仍在前面 —— 变公开的仓绝不会被"顺手重建"绕过去。

语义后果（要在 UI 里向用户解释清楚）：甲删（10:05）→ 已同步；乙 10:07 又编辑 → **任务复活**（编辑更新）。反之乙的旧编辑遇上甲的新删除 → 删除胜出，乙的版本落 `conflict` 表可人工找回。

### 10.1 版本戳的前提：一台机器同一时刻只有一个进程在写

`device_id` 持久化在 settings 表里（不是每次启动随机生成），这样同机重启后版本戳才连续。代价是：**两个实例同时打开同一个数据目录时，会共用同一个 `dev`，却各自维护一份从盘上 restore 出来的 `seq` 计数器**。同一毫秒里两边都算出 `Rev(wall, 0, dev)` —— 三元组完全相同，LWW 靠 seq 决胜负的那一半当场失效；而 `Clock.observe()` 对 `rev.dev == self.dev` 直接 return（同机版本本不需要防），把唯一的补救通道也关掉了。此外两个调度线程会把同一条提醒推两遍。

所以 §17 的单实例互斥不是体验功能，是版本戳正确性的一部分，用 `CreateMutexW` 命名内核对象（进程崩溃/被杀由系统回收；锁文件还得再造一层陈旧判断，那层本身是新 bug 来源）。锁名按数据目录**绝对路径**派生并做大小写归一，因此 `--data-dir` 指到别处不会被误判成同一实例；窗口壳与 `serve` 抢同一把锁，抢不到就拒绝启动（`serve` 返回码 2），并把已存在的窗口提到前台。

### 10.2 时间缝：`core/stamp.py`

写进数据的时间戳（`created_at`/`done_at`/`snoozed_until`/`deleted_at_ms`）与视图里"今天"的默认值，**必须来自同一个入口**。以前它们各自读墙钟，而测试传的是剧本日期 `today="2026-11-05"` —— 两条时间线只在"跑测试那天真是 11 月 5 日"时一致。日历跨到 11 月 6 日凌晨，`stats.today` 的 `done_today` 从 1 变 0：断言不是写错了，是**每天对一次**。

现在 `stamp.set_clock(...)` 可以整体钉住，`tests/test_stamp.py` 把时钟钉到一个毫不相干的日子（2027-03-14）验证"写入侧与统计侧同源"，另有一条 AST 静态守卫：`core/` 里读日历日期（`datetime.now`/`date.today`/含 `%Y %m %d` 的 `strftime`）必须走缝，例外**按文件+数量**登记理由（备份文件名、导出文件名、Gitee Date 头算 offset、仓库 manifest、缝自身）。

**2026-11-10 修订：毫秒侧也纳入守卫，原来那句"不约束 `time.time()`"的结论是错的。** 原文的理由是"后者是'过了多少毫秒'，把 13 处计时调用一起禁掉只会逼人在白名单里塞满条目，守卫反而活不下来"。理由的前半句成立（重试退避、扫描间隔、等待循环量的是真实经过时间，钉住反而测不了），但做重复任务时一次性扫出 **6 处**拿 `time.time()*1000` 与**经缝写入的时间戳**做比较：

| 位置 | 后果 |
|---|---|
| `alert_api.alert_status` 的 pending 计数 | 与 `alert_job.fire_at` 比 → 钉住时钟的测试在真实墙钟下答案不同（实测：到期时刻 == 钉住的"现在"时 pending 恒 0） |
| `scheduler.dispatch` 的默认 `now_ms` | 与 `fire_at` 比 → 决定"这一轮该发谁"，是**告警的核心裁决** |
| `alert_api.alert_silence` 写 `silence_until` | 写入用真实墙钟、`is_globally_muted(now_ms)` 比较时可能用另一把缝 |
| `alert_api.alert_preview` 的地平线 | 与 `fire_at` 比 → 干跑结果随真实时刻漂 |
| `compact.run` 的墓碑截止 | 与 `deleted_at_ms`（缝写的）比 → "90 天后回收"实际不是 90 天 |
| `adapters/toast` 测试发送的 `fire_at_ms` | 与调度器比较口径不一致 |

现在两条守卫各管一侧、互不重叠：`test_stamp.py` 管日历语义（按文件+数量豁免），`tests/test_time_seam.py` 管毫秒语义（AST 扫 `time.time`/`datetime.now`/`utcnow`，**豁免表 9 条且死条目同样算失败**，另外识别「作为函数参数默认值的可注入时钟」——`tick: Callable = time.time` 是依赖注入的好模式，不该被当成绕开）。担心的白名单膨胀没有发生：630 用例下豁免表停在 9 条。

---

## 11. 同步引擎

### 11.1 为什么删除必须是墓碑

"清单里删了但 git 里没删"在协议层不能靠"文件消失"来观测 —— 无法区分"别人删的"和"我这台机器压根没有"。删除写成 `deleted:true` 的普通新版本，走与其它修改完全相同的比较与传播路径。

### 11.2 为什么必须有 merge base

只有 local 和 remote 两份状态时，"两边都没有"是二义的。SQLite 每项存一组 `base_*`（上次同步快照），三方对比才能判定谁删的。

### 11.3 合并原语

```python
def pick(a, b, base):
    if a == base and b == base: return base, None
    if a == base:               return b, None        # 只远端改
    if b == base:               return a, None        # 只本地改
    return (a, b) if a.rev > b.rev else (b, a)        # 都改 → LWW，落败者进 conflict
```

缺失用 `ABSENT = rev(-1,-1,'')` 参与比较，删除/新增不需要特例。

### 11.4 场景决策表（R3b 逐条）

| 场景 | base | 本地 | 远端 | 动作 |
|---|---|---|---|---|
| 本机删了、git 还没删 | 在 | 墓碑 | 在 | 推墓碑 |
| 另一台删了 | 在 | 在(旧) | 墓碑 | 本地删除，进垃圾桶 |
| 两边都删 | 在 | 墓碑 | 墓碑 | 结果仍删除，静默 |
| 甲删、乙改且乙更新 | 在 | 编辑 | 墓碑 | 编辑胜出，任务复活 + 冲突留档 |
| 甲改、乙删且乙更新 | 在 | 编辑 | 墓碑 | 删除胜出，编辑进 conflict |
| 两边都改 | 在 | v1 | v2 | LWW，小者进 conflict，UI 红点 |
| **新机装、本地空** | 无 | 无 | 有 | **PULL_ONLY 引导**，绝不因"本地没有"生成墓碑 |
| **新机有离线数据后登录** | 无 | 有 | 有 | FIRST_MERGE：本地视为相对 ABSENT 的新增；远端独有项**绝不删** |
| 远端文件被网页手工删 | 在 | 在 | 缺失无墓碑 | 默认 `keep` + 告警，不删本地 |
| 空仓库（0 commit） | 无 | 有 | 空树 | 推初始提交 |
| 断网 | — | — | — | dirty 队列保留，退避 1/2/5/15/30 分钟 |
| push 撞车 | — | — | — | 重新 fetch + 重跑合并，最多 3 次 |

**新装机判据（不靠猜）**：

```python
def detect_bootstrap():
    return sync_state.get("base_commit") is None and not db.exists_any(dirty=True)
```

**全局保险丝**：任何一轮 pull 中，"远端相对 base 消失且本地无墓碑"的项一律不生成删除提交，只报警。这条保证任何 bug 或异常装机路径都不可能把远端整库清空。

### 11.6 冲突裁决：B13 承诺的兑现处

`should_record_conflict` 一直把落败版本存进 `conflict` 表，但**只有接口没有界面**就等于没兑现 —— 用户看不见，也找不回。现在设置页 → 同步 → 「⚠ N 项冲突待裁决」进面板，三个入口对应后端三个 `choice`：

| 入口 | `choice` | 行为 |
|---|---|---|
| 保留本机内容 | `local` | 把本地那份写成新版本（新 rev + dirty），下轮推出去 |
| 采用另一台内容 | `remote` | 只结案，**不重新发号**（远端已胜出并落库，再发号就是凭空多版本） |
| 保存合并结果 | `merge` | `picks` 逐字段选边 + `values` 手改，写成新版本 |

三个决定都不是随手写的，每条都有理由：

1. **差异在后端算**。`merge.diff_data()` 输出 `[{key, label_key, type, state, local, remote, base}]`，前端只翻译 `label_key`/`state`。让前端自己比，就会有两套判断 —— 视图层那次「已逾期」硬编码进响应体正是同类重复。只有一边改过的字段（`state=local_only/remote_only`）由前端**预选好**，两边都改的留空逼用户看一眼：默认值就是答案的一半。
2. **基线必须带完整字段**。`store.base_map()` 只还原 `(rev, deleted)` 用于裁决，够用；但记冲突时用它，每个字段都会显示成 `both_diff`，用户得自己猜是谁改的。基线的完整内容一直在 git 里 —— `base_commit` 就是上次双方一致的那棵树，所以 `BaseSnapshots` 在真要记冲突时才去读那一个 blob（正常路径零成本），读不到就退回 rev-only，绝不因为还原失败把整轮同步弄失败。这条是实测逼出来的：真撞车生成的冲突记录里 base 的 `data` 全是 `None`。
3. **合并不能成为校验的豁免区**。`picks` 的值只取自两侧（两份都已合法）；`values` 是用户手打的，必须走 `task_update` 同一套校验（priority 范围、status 白名单、color 十六进制、日期可解析、tags 是数组、未知字段拒绝）。否则冲突界面就是"往库里塞非法字段"的唯一后门 —— §8.2 那条规矩在这里同样生效。
4. **引用字段显示名字、提交 id**。`list_id` 之类给用户看 ULID 没有意义，后端顺手换成名字，同时保留 `local_id/remote_id` 供合并取回。名字查不到（已被删）就退回 id，不猜。

面板之外还有一条不变量：**没有任何路径会自动裁决冲突**。同步只记录、只让路，`resolved_by` 只能由 `conflict_resolve` 写入 —— 静默"帮你选"看似体贴，实际是在用户不知情时丢掉一份劳动。

### 11.5 不参与同步的数据（v0.2 明确划界）

`view_pref`（视图/分组/排序/折叠/列宽）、`focus_session`、`alert_job.status` 与已提醒状态、告警渠道配置（webhook/SMTP/收件人）、令牌、主题、数据目录设置。理由：这些是设备或个人上下文，同步会互相污染 —— 手机上设的分组不该改电脑，A 机已弹过的提醒不该让 B 机静默。

---

## 12. 告警子系统（R6，新增重点）

### 12.1 触发链路

```
保存任务/倒数日 → 重算 alert_job（幂等：先删该 item 的 pending，再按 reminders×channels 生成）
      ↓
调度器（进程内线程，每 20s 扫一次 alert_job WHERE status=pending AND fire_at<=now）
      ↓
渠道适配器 fan-out（每渠道一条 job，独立成功/失败/重试）
      ↓
toast：本地弹窗+声音；dingtalk：POST webhook；email：SMTP 发送
      ↓
写 alert_log + status=sent / failed(attempts++, next_retry 指数退避)
```

时区：`fire_at` 存绝对 epoch 毫秒，展示按本地时区。免打扰/跨天判断都基于"本地日历日"，不用 UTC。

### 12.2 渠道适配器（插件式，一个接口三个实现）

```python
class AlertChannel(Protocol):
    name: str
    def enabled(self) -> bool: ...
    def send(self, msg: AlertMessage) -> SendResult: ...
    def test(self) -> SendResult:      # 设置页"发送测试"按钮，必须实现
```

新增渠道 = 新增一个适配器 + 一个 `@feature`，主链路零改动（为二期接飞书/企微/Server酱留位）。

**① Windows 系统通知 + 托盘（默认开）**

`winotify` 发 toast：标题=任务名，正文=到期时间+清单，按钮=[完成][推迟10分钟][打开]。点击"打开"→ 唤起主窗口并定位该任务（通过本地 HTTP 接口 `POST /api/v1/ui/navigate`，前端监听）。持续提醒（`remind_mode=sticky`）：未处理则每 60 秒重发，直到完成或推迟。托盘常驻保证关窗口也能提醒。

**② 钉钉群机器人 Webhook（默认关，需配置）**

- 配置项：`webhook_url`（含 access_token）、`secret`（加签）、`at_mobiles`、`at_all`。
- 加签算法：`timestamp = 毫秒` → `sign = urlencode(base64(hmac_sha256(secret, f"{timestamp}\n{secret}")))` → 拼 `&timestamp=&sign=`。
- 消息用 **markdown** 类型，标题带关键词（若机器人开了"自定义关键词"安全设置，必须命中，默认在标题塞 `清单提醒`）。
- **限流**：单机器人 20 条/分钟，超限被限流 10 分钟。对策：适配器内做令牌桶 + 批量合并 —— 同一分钟内的多条到期**合成一条 markdown 列表**发送（这是关键设计，否则早上 9 点 5 个任务到期就撞墙）。
- 失败：HTTP 非 200 或 body `errcode != 0` 都算失败（钉钉业务错误常返回 200），记 `errcode/errmsg` 到 `alert_log`，用户能在 UI 里看到"钉钉返回 310000 关键词不匹配"这类真因。

**③ 邮箱 SMTP（默认关，需配置）**

- 配置项：`smtp_host/port/use_ssl`、`from_addr`、`auth_code`（**授权码，不是登录密码**）、`to_addrs[]`、`subject_prefix`。
- 预设：QQ `smtp.qq.com:465 SSL`、163 `smtp.163.com:465 SSL`、Gmail `smtp.gmail.com:465`（需应用专用密码）。下拉选预设自动填 host，用户只填地址+授权码。
- 合并策略同钉钉：一次 SMTP 连接发一封"今日到期汇总"，不一条一封（授权码发信也有频率风控）。
- 用 `smtplib` + `email.mime`，超时 15s，失败重试 3 次。

**④ 钉钉工作通知（企业应用）** —— v1 不做，适配器接口已留位。需 corpid/appkey/agentid + userId 映射，配置成本远高于收益，个人场景群机器人足够。

### 12.3 开关粒度（三级，用户明确要"可以关闭"）

| 层级 | 位置 | 语义 |
|---|---|---|
| 全局 | 设置 > 提醒 > 总开关 | 关 = 调度器不 fan-out，仍算 job 但不发；一键静默 |
| 渠道 | 每渠道独立开关 + 免打扰时段（如 23:00–07:30） | 免打扰内只落 toast 队列不响，过期不补发（补发无意义） |
| 单任务 | 任务详情 > 提醒 > 渠道多选/不提醒 | `alert_channels` 覆盖全局默认 |

免打扰 + 每日摘要（可选：每天 08:30 推一条"今日 N 项到期"到钉钉/邮箱）。

### 12.4 必须防的三件事

1. **重复提醒**：`dedup_key = f"{item_id}:{fire_at}:{channel}"` UNIQUE，进程重启/补扫不会重发。
2. **补发轰炸**：程序关机一天后启动，`fire_at < now - 10min` 的 pending 一律标 `skipped`，只补发今天未过期的 —— 否则开机瞬间弹 40 条。
3. **凭据泄漏**：webhook 与授权码进凭据管理器，**不进 git、不进日志、不进导出文件**；`alert_log.detail` 落库前做脱敏（截断 token）。

### 12.5 服务端兜底提醒（预留 · M7 · v1 不实现）

桌面调度器只在进程活着时工作，机器关机/休眠就漏提醒。兜底方案是服务端定时读仓库 + 推送。**本节只固化设计纪律与接口位，代码留到最后做。**

三条纪律，实现时不可违反：

1. **服务端只读。** 服务端若顺手写仓库（如记"已提醒"状态），它就没有 merge base、没有 HLC 的 `device_id`，会直接污染桌面端的同步协议。提醒状态永远只存桌面本地。
2. **去重靠时间窗口，不靠状态存储。** CI 每次运行都是全新环境、无持久状态。解法不是引入外部存储，而是把 cron 设计成幂等：每小时跑一次，只推"未来 60 分钟内到期"的任务，窗口不重叠即天然不重复。
3. **只做摘要，不做分钟级。** 每天 08:30 一条"今日 N 项到期"到钉钉 + 一封邮件，延迟 20 分钟无害；分钟级提醒由桌面本地调度器负责。这样正好绕开 CI 定时不准的缺陷，而不是跟它较劲。

平台对比（结论：**Gitee Go 优先**）

| 维度 | GitHub Actions | Gitee Go |
|---|---|---|
| 定时触发 | ✅ 但**只认 UTC**（北京时间减 8）、**最小间隔 5 分钟**、高峰延迟 15–30 分钟 | ✅ Cron，精度待实测 |
| 长期不活动 | ❌ **仓库 60 天无活动自动禁用 schedule**，需空提交保活 | 未知，需实测 |
| 连钉钉 webhook | 境外→国内，能通但延迟高、偶发失败 | ✅ 国内直连，且与钉钉/企业微信原生打通 |
| SMTP 发信 | ❌ **runner 封出站 25 端口**；465 用境外 IP 发 QQ/163 极易触发邮箱风控 | ✅ 国内 IP 正常 |
| 私有仓库额度 | ✅ 免费 2000 分钟/月 | ⚠️ 口径混乱（200 分钟/月、1000 分钟/月、500 分钟/月三种说法并存），**私有仓可用性待实测** |
| 读清单数据 | 需跨平台访问 Gitee API + 额外令牌 | ✅ runner 天然有本仓权限 |
| 国内 push/clone | ❌ 不稳定 | ✅ |

要分钟级就别用 CI，用**云函数定时触发器**（阿里云 FC / 腾讯云 SCF / Cloudflare Workers Cron Triggers，免费计划够个人用）——那才是为定时设计的，代价是多一个部署 + 第三处凭据。

**现在就留好的接口位**（避免实现时改协议）：

- 功能命名空间 `alert.server.*` 预留不占用：`alert.server.summary`、`alert.server.window`（返回未来 N 分钟到期项，供 CI 调用）。
- `AlertChannel` 协议（§12.2）服务端直接复用，渠道适配器代码两端共享一份，CI 侧只多一个入口脚本。
- 仓库读取路径固定为 `items/**/*.json` + `manifest.json`，服务端解析器与桌面端 `merge` 模块共用同一套 Item 反序列化，不写第二份解析逻辑。
- 服务端**不引入** `sync_state`/`conflict`/`base_*` 任何概念，代码层面禁止 import `sync` 模块（用 import-linter 卡住）。

**实测清单（做 M7 第一天跑）**：① Gitee 免费版私有仓能否开通并运行 Gitee Go；② 当前免费构建时长的真实口径与是否含私有仓；③ Gitee Go 定时触发器能否读到仓库文件（工作区是否自动 checkout）；④ 从 Gitee Go runner 出站到 `oapi.dingtalk.com` 与 `smtp.qq.com:465` 的连通性与耗时。任一条不通即改用 GitHub Actions + 钉钉（放弃 SMTP）或云函数方案。

---

## 13. 接口层（R4）

`@feature(name, group, http, cli, undoable, needs_auth)` 装饰器单点生成：FastAPI 路由、CLI 子命令、OpenAPI、前端调用代理、契约测试。**手写路由禁止**（同 v0.1）。

v0.2 在 v0.1 的 35 个接口之上补这些（合计 ~55）：

| 功能 | HTTP |
|---|---|
| `view.query` | `GET /api/v1/views/{name}?today=&group=&sort=&dir=` — 五个内置视图统一入口 |
| `view.calendar` | `GET /api/v1/calendar?from=&to=&granularity=day` — 日历范围取数（含跨天任务展开） |
| `view.board` | `GET /api/v1/views/board?group_by=section|status` |
| `view.pref_get/pref_set` | `GET/PATCH /api/v1/views/prefs` — 设备本地 |
| `folder.create/update/delete/move` | `POST/PATCH/DELETE /api/v1/folders...` |
| `section.create/update/delete/reorder` | `/api/v1/sections...` |
| `filter.create/update/delete` | `POST/PATCH/DELETE /api/v1/filters`，`GET /api/v1/filters/{id}/tasks` |
| `tag.create/rename/delete/assign/unassign` | `/api/v1/tags...` |
| `task.set_priority / set_color / set_due / set_repeat / set_reminder` | `POST /api/v1/tasks/{id}/{action}` |
| `task.skip` | `POST /api/v1/tasks/{id}/skip` — 跳过重复任务的当前这一次（不算完成） |
| `task.repeat_info` | `GET /api/v1/tasks/{id}/repeat` — 规则拆解成机器键 + 未来 N 次 + 本地历史统计 |
| `task.occurrences` | `GET /api/v1/tasks/{id}/occurrences` — 已完成/已跳过的那些次（设备本地，不同步） |
| `task.snooze` | `POST /api/v1/tasks/{id}/snooze {minutes}` |
| `countdown.create/update/delete/restore` | `/api/v1/countdowns...` |
| `focus.start/pause/stop/log` | `POST /api/v1/focus/...`（本地） |
| `alert.status` | `GET /api/v1/alert/status` — 待触发数、最近失败 |
| `alert.config_get/update` | `GET/PATCH /api/v1/alert/config` |
| `alert.channel_enable` | `POST /api/v1/alert/channels/{name}/enable|disable` |
| `alert.channel_test` | `POST /api/v1/alert/channels/{name}/test` — 三渠道都必须能测 |
| `alert.silence` | `POST /api/v1/alert/silence {until}` — 临时静音 |
| `alert.log` | `GET /api/v1/alert/log?limit=` |
| `ui.navigate` | `POST /api/v1/ui/navigate {item_id}` — 通知点击跳转 |
| `app.quit/tray_show/tray_hide` | `POST /api/v1/app/...` — 托盘与生命周期 |

> **M1–M8 交付实况**（2026-11-10）：已注册 **100 个功能**（M3 新增 10 个 `alert.*`；M4/M5 新增 `auth.*`/`repo.*`/`sync.*`；M6 新增 `app.autostart_*` 2 个、`backup.*` 5 个、`data.*` 3 个、`app.instance_status`；M8 新增 `task.skip`/`task.repeat_info`/`task.occurrences` 3 个）。`pytest` **630 用例全绿**（32 个文件）；CLI、HTTP、原生窗口三种入口均已实跑验证，**同步路径还用一个进程内假 Gitee + 本地裸仓库从界面点通了完整往返**（`scripts/dev_fake_cloud.py` + `scripts/dev_other_device.py`，见 §14.5）。体积 **40.54 MB / 门禁 42 MB PASS**（余量 1.46 MB）。
> M2 期间新增：`app.pref_get`、`app.pref_set`、`task.quick_add`、`view.counts`。M3 期间新增：`alert.status/config_get/config_update/channel_enable/channel_test/log/silence/preview/rebuild/tick`。
> 与上表的差异：`view.matrix`（四象限）按界面决策已删；`focus.*` 与 `ui.navigate`（通知点击跳转）**未实现** —— 后者要等打包并注册 AUMID 才做得干净：托盘气球（Shell_NotifyIcon）没有独立的点击回调，硬接只能"点图标→开窗口"，跳不到具体任务。宁缺毋假，不注册空壳接口。
> 前端契约：**后端只返回机器键**（`label_key`、`direction`、`reason_key`），展示文案一律由前端 i18n 翻译；用户数据（分组名、标签名、任务标题）走 `label` 字段，永不翻译。这条现在由契约测试**全量扫 96 个接口的响应体**兜住，见 §18.6。
> 告警配置与密钥属**设备本地**：DPAPI 密文存 SQLite `secret` 表，接口只回掩码；作业表 `alert_job` 是派生数据，**任何告警操作都不会把 item 标成待推送**（有测试锁死）。

错误码沿用 v0.1 表（`AUTH_REQUIRED / SYNC_CONFLICT / TOKEN_INVALID / ...`），新增 `CHANNEL_NOT_CONFIGURED(409)`、`CHANNEL_SEND_FAILED(502)`。

---

## 14. Gitee 仓库就绪流程与权限（R3a R10）

### 14.1 就绪流程

```python
me   = gitee.get("/user", token)                     # 验令牌 → owner=me["login"]
r    = gitee.get(f"/repos/{owner}/{name}", token)    # 200 存在 / 404 不存在
if r.status == 404:
    gitee.post("/user/repos", token, {
        "name": name, "private": True,               # ← 硬编码，不读任何用户输入
        "description": "Qingdan 任务清单数据仓库（客户端自动同步，请勿手工编辑）",
        "has_issues": False, "has_wiki": False})     # 关掉 Issue/Wiki，减少暴露面
assert_created_private(r_or_created)                 # §14.3
```

之后：空仓库（0 commit）先推初始提交（`manifest.json / .gitattributes / README.md / items/`）→ 配 HTTPS 凭据 `https://{user}:{token}@gitee.com/{owner}/{repo}.git`。

三条既有纪律保留：① 幂等，按钮可反复点；② `manifest.product` 不匹配时**拒绝接管用户已有同名仓库**，提示改名；③ 令牌 scope 缺 `projects` 报 `TOKEN_INVALID` 并给生成链接，不伪装成网络错误。

### 14.2 权限与可见性（R10，产品级约束）

**结论：仓库权限 = 私有（private），且不可被本应用改成公开。**

| 维度 | 私有仓库的实际表现 | 是否满足诉求 |
|---|---|---|
| 内容可见性 | 仅本人 + 受邀成员；匿名访问 404 | ✅ 绝不公开 |
| 他人看你的主页项目列表 | **不出现** | ✅ 不显示 |
| Gitee 搜索 / 推荐 / 开源广场 | **不收录** | ✅ 不显示 |
| 公开贡献统计、Fork、Issue、PR | 不计入 / 不允许 | ✅ |
| 匿名 `git clone` | 失败（Gitee 返回 "Repository not found" 而非 403，连"仓库存在"都不暴露） | ✅ |
| **你自己登录后的"我的仓库"列表** | **必然显示**，带 🔒 图标 | ⚠️ 无法隐藏 |

Gitee 的可见性只有「开源 / 私有」两态，**没有"隐藏"第三态**。若连自己的列表都不想看到它，唯一途径是把仓库转移到某个组织下（组织仓库不出现在个人仓库列表页），代价是要额外建一个组织 —— 文档里记为可选项，默认不做。

工程约束（写进代码，不靠自觉）：

1. **无转公开入口**：UI 与 API 都不提供 `visibility` 修改功能；`repo_visibility` 不是配置项，是常量 `private`。
2. **防公开哨兵（关键）**：每轮同步开始前 `GET /repos/{owner}/{repo}` 校验 `private == true`。一旦发现为公开（可能是网页端手滑、令牌被盗后被改），**立即中止本轮 push**，状态置 `DEGRADED`，UI 红字 + 走 §12 告警渠道通知，并提供"一键改回私有"按钮（调 `PATCH /repos/{owner}/{repo}` 带 `private=true`）。
3. **令牌最小 scope**：只勾 `projects`，不勾用户信息写、不勾删除仓库。缺 scope 时明确提示重新生成。
4. **仓库名默认无语义**：默认名 `qd-data`（而非 `qingdan`），降低在你账号列表里的辨识度，也避免别人从名称推断用途；名称可在设置里改，但改后需重新 `repo.ensure`。

### 14.3 必须让你知道的两个隐私事实

**① git 历史是永久的，删除不等于清除。** 墓碑回收（`sync.compact`）只删当前工作区的文件，历史 commit 里旧版本内容**始终可被你自己检出**。所以"删掉一条任务"在隐私意义上不是"销毁"。若某条内容确实敏感（如涉及个人纠纷、健康、财务），正确做法是：改内容 → 同步 → 若要求历史也清除，则必须重写历史（`filter-repo` + 强推），这会破坏所有机器的 merge base，需要全端重新引导。v1 的处理：垃圾桶"彻底清空"只清本地显示，不动历史；UI 在删除敏感项时给一句提示"历史版本仍保留在仓库中"。v1.1 可选方案见下。

**② 明文存储在 Gitee 服务器上。** 私有仓库挡住的是"公众"，挡不住"平台方"。若你要更强保证，v1.1 加**字段级加密**开关：`title/note/tags` 用本地主密钥（DPAPI 派生，主密钥本身不进仓库）做 AES-GCM 加密后存 JSON，`due_at/priority/status` 保持明文（日历和视图需要）。代价：Gitee 网页端变成不可读乱码、跨机器搜索依赖各端持有主密钥、密钥丢失则数据不可恢复。默认关，设置里给显式开关 + 二次确认。

### 14.4 配额核对（Gitee 社区版，2026 年核实）

| 项 | 额度 | 对本项目的影响 |
|---|---|---|
| 仓库数量 | 1000 个，不分公私有 | 无压力 |
| 私有仓库协作者 | **所有私有仓库总协作人数 ≤ 5 人**（超需企业版） | v1 单人使用，不受限；也说明"共享清单协作"确实不该进 v1 |
| 单仓库容量 | 500 MB | 见下方估算，安全 |
| 账号总容量 | 5 GB | 安全 |
| 单文件上限 | 各来源口径不一（50 MB / 500 MB），以官方当前页为准 | 我们的 JSON 文件都是 KB 级，无关 |
| Git LFS | **仅付费开放** | 附件功能二期若要做大，这是硬成本，届时需重选方案（换 GitHub 私有仓 / 换对象存储） |

**容量估算**：5000 条任务 × ~0.6KB ≈ 3MB 工作区；每次同步一个 commit，未变更文件走 git 对象复用，按每天 20 个 commit、每次改 5 个文件估算，一年新增对象约 4MB 量级 —— 即使算上历史冗余，5 年内不会触碰 500MB。真正会撑爆的是附件（图片/录音），这也是 v1 不做附件的第二个理由（第一个是 LFS 收费）。

### 14.4b 真仓库的清理：为什么不能"手工删文件"（2026-11-06 实测）

验收脚本每跑一次就往 `purple_moon/qd-data` 里留几条演示数据，早期 spike 还留下一个
`items/task/SPIKE1.json`，内容只有 `{"id":"SPIKE1"}`——**它连 Item 都解析不了**，
每次同步都被 `parse_tree` 当坏文件跳过，等于永久埋一颗哑弹。

清理分两步，都走协议内路径：

1. `scripts/clean_remote_demo.py`：拉下来 → 逐条调各自的 `*.delete` 接口 → 同步。
   实测 12 条 items 全部变成正规墓碑（`pushed=10`，第二轮 `pushed=0` 证明幂等）。
   **不用 git 直接删文件**：协议把"文件消失但没有墓碑"定义成协议外手工删除，客户端保险丝
   会报警告并保留本地——用删除来清理，就是在训练自己用错方法。
2. `scripts/quarantine_repo_junk.py`：把无法解析的文件**改写成合法墓碑**（补 `rev`、
   `deleted:true`），而不是删掉。判据是"能不能被 `Item.from_payload` 吃下"，不是
   "能不能 json.loads"。回读用一次全新 clone（本地工作副本正是刚改过的那份，用它自证等于没验）。

清理后的验收（全新客户端、真 Gitee）：`mode=bootstrap pulled=13 pushed=0 conflicts=0
**anomalies=[]**`，第二轮 `pushed=0 pulled=0 anomalies=[]`，`sync.status` 的
`pending=0 conflicts=0 push_blocked=None`，本机活着的数据 0 条。
—— 没有触发"协议外手工删除"告警，才说明这个清理方式是对的。

顺带确认的两件事：真实 Gitee 的 `Date` 头学出来的偏差是 **-330ms**（远在线值之内，
偏差守卫不误伤正常机器）；`verify_alert_live.py` 重跑仍通过——30 条同分钟到期合并成
**1 次发送**，签名错误走 HTTP 200 但 `errcode=310000` 被原样透出成 `DING_ERRCODE_310000`。

> 墓碑不会立刻消失是**设计**：`sync.compact` 只回收超过保留窗口（默认 90 天）的墓碑，
> 太早回收会让一台长期离线的机器把已删项静默复活（M5 的 D1）。要连历史 commit 一起
> 物理清除需要重写历史 + 强推，那是另一个决定，工具不做。

### 14.5 同步的界面入口与"不联网也能验"的演示通道

设置页 → 同步 是唯一入口：未登录时只有令牌框 + 权限与隐私说明（并且明确写"不登录也完全可用"）；登录后显示 `login · 仓库名 · 🔒私有`、模式（新装只拉/首次合并/常规双向，全部由后端机器键 `mode` 翻译而来）、待推送数、冲突数、最近同步时间，三个动作 立即同步 / 只拉取 / 退出登录。偏差过大时 `push_blocked_key` 直接顶成红条，不让用户对着"推送了却看不见"猜。**有未决冲突时还会多出一个 ⚠ N 项冲突待裁决 的入口**，点进去是 §11.6 的裁决面板。

**为什么要有 `scripts/dev_fake_cloud.py`**：同步是 R3 的主需求，但"已登录"这一支一直没被点过 —— 真令牌要留给用户，pytest 的假件又只在测试进程里生效。"接口测过了、界面没点过"在这个项目里出过三次事（备份按钮空操作、自启静默无反应、REPO_NOT_FOUND 首次同步失败都是这么暴露的）。这个脚本把 Gitee API 换成进程内假件、git 远端换成本地裸仓库，**走的全是产品自己的路径**：远端地址用的是 `repo` 配置里被明确支持的 `remote_url` 覆盖口子，不 monkeypatch 任何功能函数，所以演示和真实同步只差"对端是谁"。

用它在界面上实测到的事实（不是推断）：新建任务 → 立即同步 → 远端裸仓出现 `items/task/{id}.json`；删除 → 再同步 → 远端同一文件变成 `deleted: true` 且带新的 `rev`，**文件没有被物理删掉**；界面对账 `1 项待推送 → 0 项待推送`。验证读取时踩到自己一个坑：先 `fetch` 再另起进程读工作副本会看到旧 head，看起来像"墓碑没上去"——必须在同一次操作里 fetch 完再读裸仓 head。

---

## 15. UI 设计

> **可交互原型**：`清单exe-界面原型.html`（同目录，双击即可在浏览器打开，零外部依赖）。已验证深色/浅色主题、6 套强调色、3 档字号、中英双语切换在运行时生效，含今天/近一周/收集箱/已完成/垃圾桶五个内置视图、列表/看板/日历三布局、倒数日横幅、提醒渠道设置面板、同步与防公开哨兵面板。侧栏术语 2026-11-10 起为「清单 / 分组」两级（内部 kind 仍为 `list` / `folder`，不改数据格式），四象限不做。它只是早期对齐视觉与信息架构用的原型；**正式版前端是 `src/qingdan/web/`**（没有 dist/ 构建步骤，零外部依赖），两者已经不同源，改界面请改后者；折叠面板的行为原型另见 `界面原型-可折叠面板.html`。
>
> 两条硬约束（原型已按此实现，正式版不得回退）：
> ① **主题不写死** —— 所有颜色只能来自 `:root[data-theme][data-accent]` 的 CSS 变量 token，组件样式里禁止出现字面量色值；主题/强调色/字号是运行时属性切换，不是重新生成样式表。
> ② **语言不写死** —— 界面文案一律走 `I18N` 字典 + `t(key)`，`data-i18n` 标记；**任务内容属于数据，永不翻译**，日期/相对时间用 `Intl` 按 locale 格式化。新增文案必须先补两个语言的键，缺键即测试失败。

三栏（对齐截图）+ 顶部视图切换 + 右下角日历面板。

```
┌─ 顶栏: 视图名(今天) │ 排序/分组下拉 │ 着色维度 │ 搜索 │ 同步胶囊 │ 提醒开关🔔 │ 设置 ┐
│ 左栏  智能视图 / 清单(文件夹可折叠) / 标签 / 过滤器 / 已完成 / 垃圾桶  [+新建清单] │
│ 中栏  任务列表：☐ 标题  ●优先级色条  清单名  到期  标签  │ 悬停出快捷(今天/推迟/完成) │
│ 右栏  详情：标题 备注(Markdown) 子任务 提醒 重复 颜色 标签 倒数日  │ 同步元数据 rev/dev │
└ 状态栏: 数据目录 │ 上次同步 09:41 │ 待推送 3 │ 冲突 1 │ 今日到期 5 │ 番茄 2 ┘
```

视图切换：列表 / 看板 / 日历（月·周·日）。日历支持拖任务块改 `due_at`（拖完即调 `task.set_due`，产生一次 rev）。倒数日在日历上以旗标显示，列表页顶部横条显示"距 XX 还有 N 天"。

必须做的交互细节：全局快捷键 `Ctrl+Shift+N` 快速添加（解析 `明天下午3点 开会 #学习 @标签 !P1`）；`N` 新建；`Esc` 退出编辑；删除后 5 秒内底部 Snackbar 可撤销（走 `undo.last`）；提醒弹窗不阻塞主窗口；未登录时同步胶囊显示"离线模式 · 数据仅本机"，点击进登录页；垃圾桶/已完成用不同图标区分"软删"与"完成"，避免用户误以为垃圾桶里的东西被永久删了。

技术约束：前端不用框架，`fetch` + 手写渲染，单页 hash 路由；`web/dist` 打进 exe；离线可用（不访问任何 CDN，字体/图标全部本地）。

### 15.1 图标条与弹窗的两条规矩（本轮被截图抓出来的）

- **按钮必须有归宿，没实现的别摆上来。** 左侧图标条四个按钮（任务/日历/专注/统计）以前 `data-act="nav"` **根本没有 handler**，高亮还是 `i === 0` 硬写第一个 —— 点了没反应，用户只能猜"是不是坏了"。现在 `nav` 由 `state.nav` 驱动，任务/日历切布局、统计开面板；**番茄专注没做，按钮就从界面上摘掉**：留个只说"尚未实现"的空壳，比没有这个按钮更糟（§13 那条"宁缺毋假"在 UI 上同样成立）。同理 `⟳` 以前只弹一句"该功能尚未实现"，而同步早就做完了 —— 现在它真的同步；没登录就把设置页开到登录入口并说明原因。`not_impl` 这个文案键随之删除：留着它，下次就会有人再拿它当挡箭牌。
- **弹窗尺寸固定，切页签不许跳。** 四处 `$("modal").innerHTML = ...` 各自渲染，短内容的页签就把框缩成一小块，长内容的（提醒）撑到 84vh，切一次跳一次。收敛成一个 `openModal(html, fixed)` 出口：设置/冲突/统计传 `fixed=true`（640 × 84vh，内部滚动），倒数日那种小表单继续按内容收缩。之所以做成一个函数而不是逐个加 class —— 新增弹窗时"忘了设样式"正是这次的成因。

状态栏与设置页 → 数据 还要显示**数据实际读自哪里、为什么**（来源是 `DIR_SOURCES` 有限集合，前端用显式表 `DIR_SOURCE_KEY` 配文案，不拼键名；契约测试双向校验两边不脱节）。截图里那句"数据库 qingdan"就是反面教材：那是路径最后一段，而老位置与新默认目录末段同名，等于什么也没说。

---

## 16. 鉴权双态与凭据

| 能力 | 未登录 | 已登录 |
|---|---|---|
| 任务/分组/标签/视图/日历/倒数日/番茄/搜索/告警/导出/撤销 | ✅ 全可用 | ✅ |
| 同步、仓库、冲突、compact | ❌ `AUTH_REQUIRED` | ✅ |

**凭据按信任域分三处存放，永不共用一份**：

| 存放处 | 内容 | 权限 | 泄漏后果 |
|---|---|---|---|
| Windows 凭据管理器（本机） | Gitee 可写令牌、钉钉 webhook+secret、SMTP 授权码 | `projects` 读写 | 本机被控才有风险 |
| CI Secrets（服务端，M7 预留） | **单独生成的只读令牌**（`repos:readonly`）+ webhook + 授权码 | 只读 | 改不了数据，只能读 |
| 仓库内 | **什么都没有** | — | 拖库也拿不到凭据 |

CI 那份令牌必须独立生成、独立吊销，不复用桌面端令牌 —— 否则 CI 被入侵等于仓库写权限被接管，§11 的同步协议再严也白搭。

**本机有明文泄漏，是本轮查出来的（2026-11-06），而且我第一版修得不彻底**。
`porcelain.clone(url)` 会把 `https://user:TOKEN@gitee.com/...` 原样写上磁盘。这条尤其讽刺，
因为本轮同时把"整个数据目录拷走就是备份"定成了官方说法：照这个做法搬一次文件夹，
就等于把令牌复制到 U 盘 / 备份盘 / 另一台机器上。上表的"仓库内什么都没有"针对的是**远端**，
本机工作副本当时并不符合，所以这里记下来，而不是让它继续藏在"DPAPI 已加密"的结论后面。

清理面最终覆盖四处，缺一处都算没修：

| 位置 | 谁写的 | 处理 |
|---|---|---|
| `.git/config` 的 `remote.url` | clone | dulwich Config 层剥掉凭据段 |
| `.git/logs/**`（reflog） | clone / fetch / push | 按 `CRED_RE` 重写文本行，记录本身保留 |
| `.git/FETCH_HEAD` | fetch | 同上 |
| `db.sqlite` settings 表里的 `repo.remote_url` | 自建 git 的覆盖项 | 读到带凭据就剥掉并**回写**，同时记 `remote_url_credentials_stripped` 留痕 |

三点值得记下来，都是我自己踩的：

1. **第一版只清了 `.git/config`，我就据此宣布"磁盘上没有令牌"**。真跑一遍才发现 reflog 里写着
   `clone: from https://purple_moon:TOKEN@gitee.com/...`。**断言范围必须等于风险范围**，
   所以现在的测试是"扫描整个 `.git` 目录"，而不是"检查 config 那一行"。
2. **清理绝不遍历重写整个 `.git`**：压缩对象里正则匹配不到，但"顺手全量替换"一旦误伤就是仓库损坏。
   范围限定在已知会存 URL 的文本文件，并有专门一条测试断言对象库的字节数一个都没变。
3. **第二版剥掉凭据后同步直接失败（`HTTPUnauthorized`）**：`url or push_url(...)` 里剥过的地址是非空串，
   兜底永远走不到。单元测试当时只断言"令牌没了"、**没断言"还能同步"** —— 弱断言放行了一版会搞坏
   用户同步的代码。最终规则：本次仍用原地址（不打断别人配好的自建 git）、落库的剥干净、
   下一轮若无凭据且主机是 `gitee.com` 就用 DPAPI 令牌现拼；自建主机**绝不**塞 Gitee 令牌。

实测（真 Gitee，扫全目录）：一轮 `sync.run`（`ok=True mode=bootstrap pulled=14`）之后，
数据目录下**含令牌字节的文件数 = 0**；单元测试另有 6 条覆盖 config / reflog / FETCH_HEAD /
数据库 / 对象库不动 / 两轮连接。默认路径本来就不写凭据（`repo.ensure` 只保留已有的 `remote_url`，
不新建），第四条防线针对的是"用户或脚本手动填了带凭据的自建 git 地址"这个合法入口。

> 顺手记一条排查方法：验证"令牌有没有落盘"不能只看代码写了什么，要**去临时目录里 grep 真文件**。
> 这次是在 `%TEMP%` 下十几个验证用的克隆里发现 `qd-*/c/.git/config` 全都带着明文令牌，
> 才反推出产品路径同样如此 —— 我的验证工具和产品用的是同一段代码，这本身就是一条证据。

**自查工具本身也会骗人（`scripts/check_token.ps1`）**。为了让用户能查老数据目录，我写了两版
PowerShell 自查命令，两版都在**真有凭据的样本上输出了空**：

1. 第一版把中文注释写进 `.ps1`。Windows PowerShell 5.1 在没有 BOM 时按系统 ANSI（中文系统是 GBK）
   读脚本，UTF-8 的多字节序列会吞掉后面的 ASCII 字节，把正则字面量弄坏 —— 语法不报错，只是永远匹配不上。
2. 中途我还误判过一次成因（怀疑是 FileInfo 管道不进 `Select-String`），因为**没有先做双向验证**：
   只跑了"应该命中"的一侧，没跑"应该为空"的一侧，于是把环境问题当成了 API 问题。

现在的规则：`check_token.ps1` 保持**纯 ASCII**（文件里第一段注释就是这个原因），
而且测试同时断言脏样本报 `HIT`、干净样本报 `CLEAN`。
**假阴性的安全自查比没有自查更危险** —— 它交付的是"磁盘是干净的"这个错误结论。
同理适用于任何"检查型"工具：只验证它会报警，等于没验证。

> 同一天还发现自己清理得不够彻底：`%TEMP%` 里除了克隆，还有一份昨天真验收的重定向日志
> `live.log` 也带着完整推送 URL（就是上面记的"第三方库往自己 stdout 打凭据"那条）。
> 记录过这个坑 ≠ 清掉了自己的文件 —— 排查完必须回头把产物也处理掉。

条目名：`qingdan/gitee`、`qingdan/dingtalk_webhook`、`qingdan/smtp`（`qingdan/gitee-readonly` 只记用途，值留在 CI 侧）。DPAPI 兜底。**任何凭据不写 config.toml、不进 git、不进日志、不进导出、不进 CI 日志**（workflow 里禁止 `echo $SECRET`，Actions 的自动脱敏不能当保险）。登出 = 只清本机凭据 + 停自动同步，不动数据、不删 dirty 队列、不吊销 CI 令牌。

---

## 17. 打包与发布

- `pyinstaller packaging/qingdan.spec`：`console=False`、`onedir`、`upx=True`（排除 VC/UCRT/python dll）、`excludes` 见 §4、`datas=[('src/qingdan/web','qingdan/web')]`（前端源文件直接随包发布，**没有 dist/ 构建步骤**，零外部依赖）、`hiddenimports=['uvicorn.logging','uvicorn.loops.asyncio','uvicorn.protocols.http.h11_impl','webview.platforms.edgechromium']`。
- 运行时路径统一走 `resource_path()`（兼容 `sys._MEIPASS` 与开发态）。
- **数据目录：一律在应用所在目录下（用户明确要求）**。默认 `<应用目录>\data\`：`db.sqlite`、`backups/`、`exports/`、`repo/`、`http.json`；`crash.log` 在应用目录根；本项目的文档在 `<应用目录>\docs\`。打包版应用目录 = exe 同级，源码版 = `qingdan\`。
  - 自定义三种，优先级：启动参数 `<目录>` > 环境变量 `QINGDAN_DATA_DIR` > 应用目录下 `data_dir.txt`（给双击启动的人；空文件/注释行按"没配"处理，不能因此崩）。
  - **老位置不抛弃**：`%LOCALAPPDATA%\qingdan` 有库而应用目录没有时，继续读老库并把来源标成 `legacy` —— 直接切默认值会让人打开程序看到空清单，那比路径不合规严重得多。搬迁用 `scripts/migrate_data.py`（预演/`--apply`，复制后校验 `integrity_check` 与四张表行数，**绝不删源**，且要求应用没在运行——用单实例锁判断）。
  - 只读安装位置（Program Files）不崩：退到用户目录，来源标 `readonly_fallback`，界面说明原因。反过来，**用户明确指定的目录不可用必须报错**，悄悄换个地方存是最难查的行为。
  - 来源是有限集合 `DIR_SOURCES`，界面按 `DIR_SOURCE_KEY` 显式表配文案（不拼键名），契约测试双向校验两边不脱节；状态栏与设置页 → 数据 都显示"读的是哪儿、为什么"。
- **WebView2 Runtime 检测**：Win11 自带；缺失时启动页给引导链接（微软官方 Evergreen Bootstrapper，约 2MB），或允许 `--browser` 参数降级为默认浏览器模式。检测失败要给**人话**错误，不是白屏。
- 单实例：**已实现**（`core/instance.py`，`CreateMutexW` 内核对象 + 按数据目录绝对路径派生锁名）。窗口壳与 `serve` 共用一把锁，且锁的是 `resolve_data_dir()` **解析后的真实目录**（不是某个常量，否则两个进程可能锁在 A、写在 B）；抢不到时窗口壳把已有窗口提到前台，提不动就弹原生提示框（windowed 没有控制台，只 `print` 等于什么都没说），`serve` 返回码 2。为什么这属于数据正确性见 §10.1。
- 开机自启：写 `HKCU\...\Run`（提醒功能依赖进程常驻，自启默认开）。这是**唯一**落在应用目录之外的写入，且只是一个路径指针、不是数据 —— 注册表没有"应用目录"这个概念，别为了合规把它硬塞进文件里。
- SmartScreen/杀软：无签名会被拦，README 给"仍要运行"指引；二期买证书。
- 发布产物：`Qingdan-0.1.0-win64.zip`（onedir）。**不出 onefile**：它每次启动往临时目录解包，既违反"东西都放应用目录"，又是杀软最容易拦的行为，而托盘常驻被拦一次就再也收不到提醒。体积实测由 `scripts/check_size.py` 写进 `dist/SIZE` + `SIZE.json`（门禁只说"这次没超"，留档才看得出"比上次胖了多少"）。

**冻结版实测**（2026-11-05，`dist\Qingdan\Qingdan.exe serve <空目录>`）：`/api/v1/meta/features` 返回 **96 个功能全在**（含 `backup.*`、`data.*`、`app.autostart_*`）；`app.autostart_get` 报 `frozen=true` 且 `expected` 指向 dist 里的 exe 路径 —— 也就是打包版才允许注册自启、且注册的就是这个路径；首启自动落了一份 `db-2026-11-05.sqlite` 备份（`backup.status` `count=1`、`valid=true`）。用 `serve` 而不是双击窗口做冒烟，是因为 windowed 版没有控制台，`serve` 会把端口写进 `http.json`，可以直接 curl 断言接口层，窗口形态另由用户目检。

---

## 18. 备份、导出与恢复

每日首启 `VACUUM INTO` 快照到 `backups/db-YYYY-MM-DD.sqlite`，留 14 份滚动；手动「立即备份」走 `force`，落到 `db-YYYY-MM-DD-HHMMSS.sqlite`（同一天可以有多份，互不覆盖）。导出 CSV / JSON（原始 Item 全集，不含凭据）/ Markdown（按分组分节的可读清单）。CSV 列固定 16 列（标题/类型/分组/小节/状态/优先级/到期/提醒/重复/标签/颜色/备注/创建时间/完成时间/已删除/版本戳），表头语言由 `lang` 参数决定（界面切英文就导英文表头），`utf-8-sig` 保证 Excel 双击不乱码。

### 18.1 三个不那么显然的设计决定

1. **用 `VACUUM INTO` 而不是复制文件**。WAL 模式下直接 copy `db.sqlite` 会漏掉 `-wal` 里未落盘的事务，快照可能损坏。`VACUUM INTO` 是 SQLite 官方的在线备份路径，产出一致且紧凑的单文件。
2. **恢复是"重启时执行"，不是当场替换**。uvicorn 线程池和告警调度线程都持有连接，运行中换掉底层文件是未定义行为。`backup.restore` 只写 `restore_pending.json` 标记，并先给当前状态留一份 `-pre-restore` 快照；下次启动在建连接之前完成替换，旧库留成 `db.sqlite.before-restore`。
3. **恢复不会把远端也回滚**（本节修正了早期草案）。草案里写的"恢复后强制 base 失效走 FIRST_MERGE"没有采用 —— `base_*` 是随行数据，恢复时应当一起回来。保留 base 之后，三方合并会正确裁决：某项若在备份之后被别的设备删掉，恢复回来后同步会再次删除它。这是正确行为（避免复活已删项），但和"恢复"的直觉不同，UI 必须讲清楚。
4. **"最新一份"必须按 mtime 判定，不能按文件名排**。备份名允许后缀（手动的 `-HHMMSS`、恢复前的 `-pre-restore`），而 `-`(0x2D) 排在 `.`(0x2E) 前面，按名字倒序会把 `db-2026-11-05.sqlite` 排到 `db-2026-11-05-201530.sqlite` **前面**：手动备份永远显示不成"最近备份"，`prune()` 更会把最新那份当最旧的删掉。`list_backups()` 统一按 mtime 倒序，`backup.status` 取 `items[0]`、`prune()` 取 `items[keep:]` 都跟着它走（有测试用 `os.utime` 把"文件名顺序"和"时间顺序"故意摆成对立来验）。
5. **手动备份不能是空操作**。第一版 `create(force=True)` 仍复用日期名，当天第二次点击返回 `created:false` 且什么文件都不写 —— 界面提示"今天的备份已存在"，用户以为那份改动有备份了，其实它从没进过任何快照。现在 `force` 必然产出一个**没用过**的文件名（同一秒撞名就 `-2`、`-3` 递增），且绝不覆盖已有快照：覆盖等于"用户以为备了两份，实际只有一份"。每日首启仍用日期名的幂等性（`daily_backup()` 不带 force），两条语义分开。

### 18.2 为什么没有 xlsx

导出用 openpyxl 要多背约 1.5MB 依赖，与"exe 体积尽量小"直接冲突。CSV + `utf-8-sig` 在 Excel 里双击即可正确显示中文，个人清单量级足够。实测备份+导出的体积代价是 **+0.01MB**（40.43 → 40.44），因为全部走标准库。真要 xlsx 可作为可选依赖单独加，不进主包。

### 18.3 清理的安全边界

`prune()` 只删匹配 `db-YYYY-MM-DD*.sqlite` 命名规范的文件，目录里混进的用户自己的文件一律不碰（有测试）。同一套命名正则是**路径穿越防线**：字符集不含 `/`、`\`，`backup.restore(name)` 传 `../../db.sqlite` 会被直接拒绝。

### 18.4 备份健康度要能被看见

`backup.status` 返回 `age_days` 与 `stale`（超过 3 天没备份即 true，从没备份过也算 stale）。数据放在手上却没有备份可见性，是实际风险 —— 必须一眼能查，而不是等库坏了才发现。

### 18.5 导入的幂等性

`data.import` 只在来版比本地更新时才写入：重复导入同一份 `applied=0`；导入更旧的版本 `skipped`；导入的项一律标 dirty，否则另一台机器永远看不到。

### 18.6 界面文案不许从后端下发（实测把口径扩到全部接口）

最早踩的坑在视图层：后端把"已逾期""待办"这类展示文案直接下发，切到英文界面就残留中文。当时立了规矩「后端只回 `label_key`，用户数据才回 `label`」，但**只修了视图层**。这次把备份/导出/自启接进设置页时顺手扫了一遍全接口，同一个根因还留着 6 处：`autostart.reason`（"开发环境不注册自启…"）、`backup` 的 `note`/`reason` 两处、`alert.status.note`、`auth.login.note`、`countdown.label`（"还剩 3 天"）、告警日志的"测试："前缀。

统一改成机器键：`reason_key="dev_mode"`、`direction="today"`（前端配 `days` 自己拼文案）、导出正文语言由 `lang` 参数决定。判定办法不是 grep 而是**跑一遍**：用全 ASCII 的用户数据把 96 个功能经 HTTP 全调一次，输入里没有中文，输出里任何中文字符都只可能是后端硬编码。三个细节：

- 例外只有 `data.preview.content` —— 那是**文件正文**（CSV 表头、Markdown 标题），语言由 `lang` 决定，属于内容不属于界面文案；它的正确性由 `test_export_header_language_follows_the_lang_param` 单独管。守卫常年带合法噪音，下一次改动就会把它当误报删掉。
- 扫描必须独占一个数据库。和中文那轮共用时，列表类接口会把上一轮的中文数据原样吐回来，守卫满屏误报（第一次跑就是这样报了 18 个接口）。
- 前端也不许拼键名（`t("autostart_" + key)`）：i18n 静态扫描查不到拼出来的键，缺键会把裸键名糊在界面上。要么显式分支，要么加一条"每个 `reason_key` 都有对应 i18n 键"的断言。

### 18.7 设置面板不会跟着 state 自动重画

`openSettings(tab)` 是一次性渲染 `innerHTML`，`loadData()` 更新 state 后正文仍是旧的：实测点「立即备份」后备份确实生成了、接口也返回了新列表，界面还停在"共 1 份"。凡是**在面板里改了状态**的动作（备份/恢复/自启/告警保存/偏好保存）都必须显式 `openSettings(当前页签)` 重画。

同一条规矩的另一半：**不能生效的控件要看着就不能用**。开发态后端拒绝注册开机自启，开关原来照常可点、点完静默无反应 —— 现在 `!frozen` 时加 `.tog.dis`（灰化 + `cursor:not-allowed`），并在 `changed === false` 时用 snackbar 把拒绝原因讲出来。「调用没报错」不等于「用户真看见」。

---

## 19. 测试与验收

### 19.1 单元
`clock`（回拨钳制/offset/同毫秒全序）、`serialize`（同 Item 两次 dump 字节一致）、`pick`（ABSENT×墓碑组合穷举）、`rrule` 展开、`natural_parse`（中文日期词表：今天/明天/后天/周五/下周一/X月Y日/上午9点…）、`dingtalk_sign`（固定向量断言）、`alert_dedup`。

### 19.2 属性测试（hypothesis）
同步四条不变量（幂等/收敛/不复活/不静默丢）+ 视图两条：`view.query('今天', d)` 在跨时区与跨午夜边界不吞任务；任何 `group_by` 下任务总数 = 不分组时总数（分组不丢不重）。

### 19.3 同步场景矩阵
§11.4 表逐条跑，双临时目录 + 本地裸 git 仓库模拟 Gitee。含新机 PULL_ONLY、离线后首登、push 撞车、**重试耗尽与断网**、网页手工删文件、空仓初始化、时钟慢 8 分钟与**快一天**、同毫秒并发、墓碑回收后离线机复活。**22 条 B 场景 + 6 条 D 场景全部有对应测试，矩阵状态列已无 🔴**（M5 拆解文档里逐行标了测试名）。失败路径单独放 `tests/test_sync_failure.py`：成功分支写得再对，也不代表异常分支守住了待推送标记与墓碑。

### 19.4 契约测试（R4）
① 注册表 ↔ `app.routes` 1:1，缺路由即失败（静态路径必须排在参数化路径之前，防遮蔽）；② 每个 feature 至少一条"经 HTTP 调用"的端到端用例，参数样例从签名与路径模板**推导**，不手写；③ 有 `@feature` 的模块必须真的被导入 —— 断言看 `sys.modules` 而不是 bootstrap 源码文本（`from qingdan.core import backup` 里根本不出现 `qingdan.core.backup`，文本匹配会把每个模块都误判成漏导入，第一版就是这么全红的）；④ **响应体不得含服务端自产中文**（§18.6）。③④ 的判定函数自己也有测试：用真的装饰器和"只在文档字符串里提到 `@feature(`)"两个样例文件验证它不会漏报也不会误报 —— 守卫有假阳性，下次改动就会把它当噪音删掉。

### 19.5 告警测试（R6）
- 三渠道 `test` 接口真发一次（钉钉/邮箱需用户实配，CI 用 mock server）。
- 合并发送：造 30 条同分钟到期 → 断言只发 1 条钉钉消息、1 封邮件（限流防线）。
- 补发轰炸：造 40 条 `fire_at` 在昨天 → 启动后断言全部 `skipped`，toast 数 = 0。
- 免打扰：23:30 触发 → 不响；07:31 不补发。
- 三级开关各自关闭后无外发。

### 19.6 体积门禁（R5）
CI 脚本读 `dist/` 总大小，> 30MB（onedir）即构建失败。这条必须有，否则体积会在某次"顺手加个依赖"后无声膨胀。

### 19.7 手工冒烟
WebView2 缺失机器、休眠唤醒后提醒、2000 任务滚动流畅度、托盘菜单、通知点击跳转、杀软环境。

### 19.8 权限与隐私回归（R10）
1. 新建仓库后断言 API 返回 `private == true`，且代码里 `visibility` 无任何可写路径（grep `private.*False` / `private: bool` 应无结果）。
2. 人工把仓库在网页端改成公开 → 跑一轮同步 → 断言 **push 被中止**、状态 `DEGRADED`、告警已发出、"改回私有"按钮可用且生效。
3. 匿名（无 token）访问 `GET /repos/{owner}/{repo}` 与 `git clone` 均失败。
4. 导出 JSON/CSV、`alert_log`、崩溃日志三类产物中 grep 不到 webhook access_token、加签 secret、SMTP 授权码、Gitee 令牌的任何片段。

---

## 20. 里程碑

| 阶段 | 内容 | 判据 | 估时 |
|---|---|---|---|
| **M0 Spike + 骨架** | **§4 四件套验证（py3.14/pywebview/pythonnet/PyInstaller）**；registry+`@feature`；SQLite；import-linter | spike 出可运行 exe 且体积 <30MB；CLI 能建/列任务 | **2d** |
| M1 业务内核 | 全部 Item CRUD、五视图、分组排序、标签、过滤器、四象限、搜索、撤销 | R7/R9 达成，HTTP 全通 | 5d |
| M2 WebView 前端 | 三栏 + 看板 + 日历拖拽 + 详情面板 + 快速添加解析 + 深色主题 | 界面达到截图水位 | 5d ✅**已完成** |
| M3 告警 | 调度器 + 三适配器 + 三级开关 + 免打扰 + 合并发送 + 托盘 | R6 达成，§19.5 全绿 | 4d ✅**已完成**（2026-11-06 真钉钉端到端 PASS：30 条同分钟到期只发 1 条；签名错误 HTTP 200 但 `errcode=310000` 原样透出） |
| M4 Gitee | 令牌存储、`repo.ensure`（private 写死）、空仓初始化、连通性、**防公开哨兵** | R3a 幂等 + §19.8 四条通过 | 2.5d ✅**已完成**（真实 Gitee 端到端 PASS） |
| M5 同步 | base 快照、`pick`、§11 全流程、冲突面板、倒数日同步、墓碑回收+名册 | R3b 全场景 | 6d ✅**已完成**（矩阵 44 条覆盖，见 `docs/M5-同步测试矩阵.md`） |
| M6 加固发布 | 属性测试 + 矩阵 + 体积门禁 + 备份导出 + spec/图标/自启 | §19 全绿，出安装包 | 4d 🟡**大部分完成**（打包/图标/托盘/自启/备份/导出/体积门禁/冻结实跑/**属性测试已在 M8 补齐**/设置页四页签已接入），**只剩代码签名**（需买 OV 证书，约 ¥1500/年，属成本决定） |
| **M7 服务端兜底（预留）** | §12.5 三条纪律 + Gitee Go 实测清单 4 条 + `alert.server.*` 接口位 + CI 只读令牌 | **v1 不实现**，仅保证接口位与凭据隔离不返工 | 3d（最后做） |
| **M8 重复任务（计划外补做，2026-11-10）** | §6.5 全部 11 条：`core/rrule.py` + `occurrence` 本地表 + 完成/跳过顺延 + 提醒平移 + 锚点纪律 + 日历展开 + 3 个新接口 + 前端接线 | 630 用例全绿、100 功能、exe 40.54MB | 1d ✅**已完成** |

合计 **31.5 人日**（其中 M7 的 3d 排在最后，v1 交付按 28.5 人日算）。顺序不可调：M0 的 spike 决定技术栈成立与否，M1 的 registry 是 R4 的制度保障，M5 的 base 快照是 R3b 的技术前提。

---

## 21. 风险与待确认

**风险（按严重度）**

1. **pythonnet 无 cp314 wheel**（§4 已给四级降级）—— 唯一能让整个技术栈重选的风险，所以放 M0 第一天。
2. **WebView2 缺失/损坏**：Win10 老版本可能没有；启动检测 + 引导下载 + `--browser` 降级。
3. **钉钉机器人 20 条/分钟限流**：靠合并发送规避；若用户自建机器人设了"自定义关键词"而消息未命中，钉钉回 `310000` —— 适配器必须把 `errcode/errmsg` 原样显示，否则用户只看到"发送失败"。
4. **Gitee HTTPS + 私人令牌推送**：历史上有兼容性问题（改密/开两步验证后令牌失效）。M4 第一件事做连通性验证，失败切 SSH key。
5. ~~**dulwich 成熟度**~~ —— **已排除**（M5 前置 spike 实测通过，见 `spike/dulwich_spike.py`）。两条结论要落进实现：
   ① **同步只用 `fetch`，禁止 `pull`**：`porcelain.pull` 在分叉时抛 `DivergedBranches` 拒绝自动合并，而这正是我们要的——清单合并是应用层按 item 的三方合并（LWW + 墓碑），git 的行级文本合并会把两个 JSON 合成非法内容；
   ② 非快进 push 的信号是 `DivergedBranches(current_sha, new_sha)`，类型明确可编程，「撞车 → 重新 fetch → 重跑合并」已验证可走通。
   仍待验证：HTTPS + 私人令牌推送通道（M4 用真令牌）。
6. **前端无框架的维护成本**：日历拖拽 + 看板手写有工作量，若失控可引入 htmx 或 Alpine.js（仍零构建、体积 +50KB）。
7. **墓碑回收 × 超长离线**：90 天后复活已删项，缓解为复活走 conflict 不静默。
8. **提醒依赖进程常驻**：机器休眠/关机期间漏提醒。缓解：免打扰 + 启动补发窗口 10 分钟 + 每日摘要兜底。

**待你确认（影响 M1 数据模型，开工前定）**

1. 提醒默认渠道：只 toast，还是 toast + 钉钉（若已配）？
2. 快速添加的自然语言解析要不要做（做则 +1.5d，是体验分水岭）？
3. 倒数日是否需要"提前 N 天提醒"的默认值（生日一般提前 1 天）？
4. 仓库命名：默认 `{你的Gitee用户名}/qingdan` 私有，要不要换名或放组织下？
5. 番茄专注要不要计入统计报表（v1 只给今日番茄数与累计时长，不做趋势图）。
6. 清单里会不会记敏感内容（个人纠纷、健康、财务等）？会 → 建议把 §14.3 的**字段级加密**从 v1.1 提前到 v1（+2 人日，代价是 Gitee 网页端不可读）；不会 → 保持明文，方便你直接在手机上打开网页看清单。

---

## 22. 附录：目录结构（M6 实况，非计划）

```
qingdan/
├── pyproject.toml
├── src/qingdan/
│   ├── bootstrap.py                     ← 集中导入功能模块，触发 @feature 注册
│   ├── __main__.py                      ← `qingdan serve` / `qingdan app` / CLI 分流
│   ├── cli.py
│   ├── core/
│   │   ├── models.py store.py clock.py registry.py context.py   ← 数据与制度底座
│   │   ├── merge.py sync.py compact.py gitrepo.py gitee.py sync_api.py
│   │   ├── view.py views_api.py service.py org.py nlp.py        ← 视图/任务/组织/NLP
│   │   ├── rrule.py                       ← 重复规则（自写 RFC5545 子集，零第三方依赖）
│   │   ├── alerting/{base,config,dingtalk,emailch,scheduler}.py
│   │   ├── alert_api.py backup.py export.py autostart.py secrets.py
│   │   ├── instance.py                    ← 单实例互斥（CreateMutexW，可注入后端）
│   │   └── stamp.py                       ← 统一时间缝，日历与毫秒语义都只从这里取
│   ├── adapters/{__init__.py,toast.py}  ← 系统通知渠道（托盘气球→dialog→toast 降级链）
│   ├── api/http.py                      ← registry → Starlette Route（按具体度排序挂载）
│   ├── shell/{app.py,tray.py}           ← pywebview 原生窗口 + pystray 托盘
│   └── web/{index.html,app.css,api.js,i18n.js,store.js,app.js}
├── packaging/{qingdan.spec,entry.py,icon.png,qd.ico}
├── scripts/{check_size.py,make_icon.py,seed_demo.py,verify_alert_live.py,verify_gitee_live.py,dev_fake_cloud.py,dev_other_device.py,migrate_data.py,clean_remote_demo.py,quarantine_repo_junk.py,check_token.ps1}

├── README.md                            ← 用户 + 开发者两侧说明（SmartScreen 放行、恢复要重启、已知限制）
├── spike/                               ← M0 阻塞项实测（体积/FTS5/通知/dulwich/Gitee）
├── tests/{test_*.py,conftest.py,harness.py}   ← 32 个文件 / 630 用例；含 test_rrule.py（边界）、
│                                                test_rrule_props.py（属性测试，M6 欠的那条）、
│                                                test_recurring.py（顺延端到端）、
│                                                test_time_seam.py（毫秒侧时间缝守卫）
├── data/                                ← 默认数据目录（db.sqlite/backups/exports/repo）
└── docs/{清单exe-开发文档.md,清单exe-界面原型.html,M2-任务拆解.md,M3-任务拆解.md,M5-同步测试矩阵.md}
```

与 v0.1 草案的差异（照实写，不留假结构）：`core/alerting/` 而不是顶层 `alert/`；邮箱渠道文件名是 `emailch.py`（`email` 与标准库撞名）；`focus/` 未实现（v1 不做）；`web/` 是源文件直接随包发布，没有单独的 `dist/` 构建步骤（零外部依赖，不引入打包器）；`openapi_gen.py` 与 `docs/api.md` 取消 —— 接口清单的唯一真相是 `REGISTRY`，前端启动时从 `/api/v1/meta/features` 拉，静态文档只会过期。`webview2_check.py` 也没有独立文件：Runtime 缺失由 `shell/app.py` 建窗失败时落 `crash.log` 兜住。
