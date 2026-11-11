"""体积门禁：构建产物超过阈值就失败，并把实测值写成 SIZE 文件留档。

阈值必须定在**实测值之上**，不是估算值。定低了构建天天红，
这条检查最终会被人直接删掉 —— 那比没有检查更糟。

历史：M0 spike 实测 26MB（只含 pywebview），当时估 38–40MB、门禁定 42MB；
M6 实测 37.35MB，与估算吻合，门禁维持 42MB。

SIZE 文件不是仪式：门禁只说"这次没超"，看不出"比上次胖了多少"。
体积回退几乎总是"顺手加了个依赖"，只有留下可对比的历史值才发现得了。
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

LIMIT_MB = 42.0
TOP_N = 8


def size_of(path: Path) -> int:
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())


def write_size_artifact(root: Path, total: int, files: list[Path]) -> Path:
    """写 `dist/SIZE.json` + `dist/SIZE`（人读的一行）。"""
    payload = {
        "built_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "product": root.name,
        "bytes": total,
        "mb": round(total / 1048576, 2),
        "limit_mb": LIMIT_MB,
        "top_files": [{"path": str(f.relative_to(root)),
                       "bytes": f.stat().st_size} for f in files],
    }
    (root.parent / "SIZE.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    line = f"{payload['mb']} MB / limit {LIMIT_MB:.0f} MB  built {payload['built_at']}\n"
    (root.parent / "SIZE").write_text(line, encoding="utf-8")
    return root.parent / "SIZE"


def main(dist: str = "dist/Qingdan") -> int:
    root = Path(dist)
    if not root.is_dir():
        print(f"[size] 找不到构建产物：{root}（先跑 pyinstaller packaging/qingdan.spec）")
        return 1

    total = size_of(root)
    mb = total / 1048576
    print(f"[size] {root} = {mb:.2f} MB / 上限 {LIMIT_MB:.0f} MB")

    files = sorted((f for f in root.rglob("*") if f.is_file()),
                   key=size_file, reverse=True)[:TOP_N]
    print(f"[size] 最大 {TOP_N} 个文件：")
    for f in files:
        print(f"  {size_file(f) / 1048576:6.2f} MB  {f.relative_to(root)}")

    artifact = write_size_artifact(root, total, files)
    print(f"[size] 已写留档：{artifact}")

    if mb > LIMIT_MB:
        print(f"[size] FAIL —— 超出 {mb - LIMIT_MB:.2f} MB。"
              f"要么裁剪依赖，要么把阈值调整写进开发文档 §4 并说明理由，不许默默改数字。")
        return 1
    print(f"[size] PASS（余量 {LIMIT_MB - mb:.2f} MB）")
    return 0


def size_file(f: Path) -> int:
    return f.stat().st_size


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1] if len(sys.argv) > 1 else "dist/Qingdan"))
