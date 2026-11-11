"""运行时上下文：把 Store 与登录态交给功能实现使用。

service 层只通过 ctx 拿依赖，这样测试里可以整体换到临时目录跑。

数据目录的规矩（用户明确要求）：**程序产生的一切都放在应用自己所在目录**，
不要散落到 `%LOCALAPPDATA%`、文档目录或临时目录。默认 `<应用目录>/data`，
可以用三种方式自定义（优先级从高到低）：命令行/`init(data_dir=...)`、
环境变量 `QINGDAN_DATA_DIR`、应用目录下的 `data_dir.txt`（给双击启动的人）。
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

from qingdan.core.store import Store

#: 老版本的位置。不是"正确位置"，只是**不能假装用户的数据不存在**：
#: 全新默认目录是空的，直接切过去会让人以为清单被清空了。
LEGACY_DIR = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "qingdan"
DIR_OVERRIDE_FILE = "data_dir.txt"

#: 数据目录的来源，全部列在这里 —— 界面按这个集合配文案（DIR_SOURCE_KEY）。
#: 为什么要有这个常量而不是散落的字符串字面量：来源值一旦拼错或新增而前端没跟上，
#: 状态栏就会显示裸键名或者干脆显示错的位置，而这恰好是"我的数据去哪了"唯一的线索。
DIR_SOURCES = frozenset({"explicit", "env", "file", "app", "legacy", "readonly_fallback"})


def app_dir() -> Path:
    """程序所在目录：打包后是 exe 同级，源码运行是工程根。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    # src/qingdan/core/context.py → 上三层是工程根 qingdan/
    return Path(__file__).resolve().parents[3]


def default_dir() -> Path:
    return app_dir() / "data"


def resolve_data_dir(data_dir: str | os.PathLike | None = None) -> tuple[Path, str]:
    """返回 (数据目录, 来源机器键)。来源要能显示给用户看，不能让他猜。

    explicit / env / file / app / legacy / readonly_fallback 六种来源含义不同，
    出问题时（"我的数据去哪了"）第一句要能回答"程序到底在读哪里、为什么"。
    """
    if data_dir:
        return Path(str(data_dir)).expanduser().resolve(), "explicit"

    env = os.environ.get("QINGDAN_DATA_DIR")
    if env and env.strip():
        return Path(env.strip()).expanduser().resolve(), "env"

    marker = app_dir() / DIR_OVERRIDE_FILE
    if marker.is_file():
        # 空文件/全是空白要当"没配"处理：用户用记事本建一个同名文件太常见了，
        # 而这时候抛 IndexError 会让程序根本起不来（测试就是这么抓到的）。
        lines = [ln.strip() for ln in marker.read_text(encoding="utf-8").splitlines()]
        raw = next((ln for ln in lines if ln and not ln.startswith("#")), "")
        if raw:
            p = Path(raw).expanduser()
            if not p.is_absolute():
                p = app_dir() / p          # 允许写 `data-2` 这种相对路径
            return p.resolve(), "file"

    home = default_dir()
    if (home / "db.sqlite").is_file():
        return home, "app"
    if (LEGACY_DIR / "db.sqlite").is_file():
        return LEGACY_DIR, "legacy"        # 迁移前继续用老库，不静默换一份空数据
    return home, "app"


@dataclass
class Ctx:
    data_dir: Path | None = None
    store: Store | None = None
    authed: bool = False
    gitee_user: str | None = None
    repo: str | None = None
    data_dir_source: str | None = None

    def need_store(self) -> Store:
        if self.store is None:
            raise RuntimeError("上下文未初始化，先调用 qingdan.core.context.init()")
        return self.store


#: 全局单例。init()/shutdown() **原地改字段**，绝不重新绑定这个名字 ——
#: service 层用 `from ... import ctx` 在导入时就绑定了对象引用，重新绑定会让它拿到旧的空上下文。
ctx = Ctx()


def _mkdir_or_none(path: Path) -> bool:
    try:
        path.mkdir(parents=True, exist_ok=True)
        return True
    except OSError:
        return False


def init(data_dir: str | os.PathLike | None = None, device_id: str | None = None) -> Ctx:
    path, source = resolve_data_dir(data_dir)
    if source not in DIR_SOURCES:
        # 来源值必须是个已登记的机器键：前端靠它配文案，冒出一个新值就是裸键名上屏
        raise RuntimeError(f"未知的数据目录来源：{source}（请加进 DIR_SOURCES）")
    if not _mkdir_or_none(path):
        # 装进 Program Files 时应用目录只读：不能崩在看不懂的 PermissionError 上，
        # 也不能悄悄用别处的旧库 —— 退到一个一定可写的位置，并把"为什么不在应用目录"如实报出来。
        if source == "app":
            path, source = LEGACY_DIR, "readonly_fallback"
            if not _mkdir_or_none(path):
                raise OSError(f"数据目录不可写：{path}（也可用 QINGDAN_DATA_DIR 指定别处）")
        else:
            raise OSError(f"指定的数据目录不可用：{path}")

    if ctx.store is not None:
        ctx.store.close()

    # 待恢复的备份必须赶在任何连接打开之前换成主库 —— 运行中替换底层文件是未定义行为。
    # 延迟导入：backup 依赖 ctx，模块级导入会形成循环。
    from qingdan.core.backup import apply_pending_restore
    applied = apply_pending_restore(path)
    if applied and applied.get("applied"):
        os.environ["QINGDAN_RESTORED"] = applied["restored_from"]

    ctx.data_dir = path
    ctx.data_dir_source = source
    ctx.store = Store(path / "db.sqlite", device_id=device_id)
    # 登录态从本机凭据推导：重启后仍是已登录，不用每次重填令牌。
    # 令牌解不开（换机器/换 Windows 用户）时读出来是空 → 视为未登录。
    ctx.authed = bool(ctx.store.secret_get("gitee_token"))
    conf = ctx.store.state_get("repo") or {}
    ctx.gitee_user = conf.get("owner")
    ctx.repo = conf.get("name")
    return ctx


def shutdown() -> None:
    if ctx.store is not None:
        ctx.store.close()
    ctx.store = None
    ctx.authed = False
    ctx.gitee_user = None
    ctx.repo = None
    # data_dir 与 data_dir_source 刻意保留：清掉它们没有任何安全收益（要释放的是连接），
    # 却会让人写出 `init(ctx.data_dir)` → 实际传入 None → 静默落到默认目录开另一个库。
    # 这个坑在测试里真实踩过。
