"""导入功能模块，触发 @feature 注册。

任何入口（HTTP 壳、CLI、窗口壳、测试）启动前都必须先 `import qingdan.bootstrap` 一次。
集中在这里而不是散落在各入口，是为了避免「某个入口少导入了一个模块」
导致那批接口凭空消失 —— 那种 bug 只会在使用时才暴露，很难查。

渠道适配器在接口导入之后注册：alert_api 只按名字查注册表，注册顺序不影响接口存在性。
"""
from qingdan.core import access as _access                # noqa: F401
from qingdan.core import alert_api as _alert_api          # noqa: F401
from qingdan.core import autostart as _autostart          # noqa: F401
from qingdan.core import backup as _backup                # noqa: F401
from qingdan.core import countdown as _countdown          # noqa: F401
from qingdan.core import export as _export                # noqa: F401
from qingdan.core import instance as _instance            # noqa: F401
from qingdan.core import org as _org                      # noqa: F401
from qingdan.core import service as _service              # noqa: F401
from qingdan.core import sync_api as _sync_api            # noqa: F401
from qingdan.core import views_api as _views_api          # noqa: F401


def register_channels() -> list[str]:
    """注册告警渠道。上下文未初始化时也可调用（渠道只在发送时才读配置）。"""
    from qingdan.adapters import register_all
    return register_all()


# 引导时即注册：渠道注册不启动线程、不读盘，成本是一次 winotify 探测。
# 真正的扫描循环由 `qingdan serve` 显式 scheduler.start()，测试里不会误发。
register_channels()
