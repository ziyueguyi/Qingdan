# M0 Spike 结果（已跑通，2026-11-05）

结论：**Python 3.14 + pythonnet + pywebview/WebView2 + PyInstaller 这条链成立**，
开发文档 §4 的四级降级链一条都没启用，技术栈不用重选。

## 实测结果

| 检查项 | 结果 |
|---|---|
| `python --version` | 3.14.5（本机默认，装在 `D:\SoftWare\Develop\Python3.14`） |
| 该 Python 自带 pip | ❌ **没有 pip**，必须 `python -m venv` 建虚拟环境（venv 自带 pip 26.1.1） |
| 直连 pypi.org | ⚠️ 频繁 15s 读超时并重试；**必须配国内镜像** |
| `pip install pythonnet` | ✅ 3.2.1，有 cp314 可用分发（带 clr_loader 0.3.1 + cffi 2.1.1） |
| `pip install pywebview` | ✅ 6.2.1 |
| `import clr`（真加载 .NET 运行时） | ✅ 0.4s |
| `webview.start(gui="edgechromium")` | ✅ 窗口真能开，WebView2 正常初始化 |
| PyInstaller 冻结后 exe | ✅ 同样 PASS，`onedir` 实测 **26 MB** |

冻结产物体积拆解（决定 §4 体积门禁为什么从 30MB 上调到 42MB）：
`python314.dll` 6.5 + exe 5.2 + `libcrypto/libssl` 5.8 + `base_library.zip` 1.3 +
`ucrtbase` 1.1 + 其它 pyd 1.7 + `WebView2.Core.dll` 0.6 + `Python.Runtime.dll` 0.4。
其中解释器 + SSL + VC 运行时约 18MB 是**固定成本**，与功能多少无关。

## 踩到的两个坑

1. **`Window` 没有 `close()`** —— pywebview 6.x 是 `win.destroy()`。写错会让
   `webview.start()` 永不返回，看起来像"WebView2 卡死"，实际是脚本 bug。
2. **stdout 缓冲会吞掉全部诊断信息** —— 进程被强杀后一行输出都拿不到。
   spike 必须用 `python -u` 跑，并自带看门狗超时退出。

## 复现命令

```bash
cd D:/Data/Develop/Python/qingdan
python -m venv .venv
.venv/Scripts/python.exe -m pip install -i https://pypi.tuna.tsinghua.edu.cn/simple \
    pythonnet pywebview fastapi uvicorn httpx dulwich pyinstaller pytest
.venv/Scripts/python.exe -u spike/spike.py                 # 期望最后一行 PASS，exit=0

.venv/Scripts/pyinstaller.exe --noconfirm --console --onedir --name spike \
    --distpath spikebuild/dist --workpath spikebuild/build --specpath spikebuild spike/spike.py
./spikebuild/dist/spike/spike.exe                          # 同样期望 PASS
```

## M5 前置 spike：dulwich（已跑通，2026-11-05）

`spike/dulwich_spike.py`，用本地裸仓库验证，不需要 Gitee 令牌。结果 **PASS**：

| 能力 | 结果 |
|---|---|
| clone 空仓库 / 有内容仓库 | ✅ |
| commit + push | ✅ |
| fetch 拿到远端新 head | ✅ |
| **一次 ls-tree 读全量文件** | ✅（同步必须批量读，不能一个文件一次请求） |
| 读任意历史 commit 的 tree | ✅（「从某天恢复」要用） |
| 非快进 push 的信号 | ✅ 抛 `DivergedBranches(current_sha, new_sha)`，类型明确可编程 |
| 分叉后的恢复路径 | ✅ fetch → 对齐本地 ref → 合并结果作为新提交 → push 成功 |

### 关键发现：**不能用 `pull`，必须用 `fetch`**

`porcelain.pull` 在分支分叉时直接抛 `DivergedBranches`，dulwich 拒绝做自动文本合并。

这不是缺陷，**正好符合同步协议的设计**：清单的合并是**应用层三方合并**（按 item id 逐项 `pick()`，LWW + 墓碑），不是 git 的行级文本合并。如果 git 能自动合并反而危险——两个 JSON 文件被文本合并成非法 JSON，墓碑语义就没了。

所以 M5 的实现路径固定为：`fetch` → 读远端 tree → 与本地 + base 三方合并 → 把合并结果写成新提交 → `push`；被拒（`DivergedBranches`）就重来一轮，最多 3 次。**代码里禁止出现 `pull`**，这条要写成断言。

### dulwich 1.2 API 坑（写实现时会再撞）

- `Commit` 不再支持下标：用 `commit.tree`，不是 `commit["tree"]`。
- `Tree.items()` 返回 `TreeEntry(path, mode, sha)` **三元组**，不是过去的 `(name, sha)`。
- `porcelain.commit()` 返回 bytes SHA，不是带 `.hex` 的对象。
- 空仓库 clone 后没有 HEAD，读 ref 前要判空。

---
