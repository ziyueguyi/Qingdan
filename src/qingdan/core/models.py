"""Item 模型与确定性序列化。

所有可同步对象统一成 Item —— 删除只是「带 deleted=true 的一个版本」，
这是 §11 同步协议能成立的前提：删除走和其它修改完全相同的比较与传播路径。

序列化必须**完全确定性**（同内容两次 dump 字节相同），否则 git 会产生大量伪 diff。
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from typing import Any

from qingdan.core.clock import ABSENT, Rev

SCHEMA_VERSION = 1

#: 参与同步的对象类型。目录即 kind，见开发文档 §9。
KINDS = frozenset({"task", "list", "folder", "section", "tag", "note", "countdown", "filter"})

_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def new_id(now_ms: int | None = None) -> str:
    """ULID：48bit 毫秒 + 80bit 随机，26 字符 Crockford base32。

    自带时间前缀 → 可排序；跨机器不冲突；不用向服务端要号。
    """
    ts = int(now_ms if now_ms is not None else time.time() * 1000)
    if ts < 0 or ts >= (1 << 48):
        raise ValueError("时间戳超出 ULID 范围")
    val = (ts << 80) | int.from_bytes(os.urandom(10), "big")
    return "".join(_CROCKFORD[(val >> (125 - 5 * i)) & 31] for i in range(26))


@dataclass
class Item:
    kind: str
    id: str
    rev: Rev
    data: dict[str, Any] = field(default_factory=dict)
    deleted: bool = False
    deleted_at_ms: int | None = None
    schema: int = SCHEMA_VERSION
    meta: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.kind not in KINDS:
            raise ValueError(f"未知 kind：{self.kind}（允许：{sorted(KINDS)}）")
        if not self.id:
            raise ValueError("id 不能为空")

    # -- 版本语义 ----------------------------------------------------------
    @property
    def version(self) -> tuple[Rev, bool]:
        """比较用的「版本」。同一 rev 不会既删又活，所以带上 deleted 无害。"""
        return (self.rev, self.deleted)

    def is_absent(self) -> bool:
        return self.rev == ABSENT

    def path(self) -> str:
        """仓库内路径：items/{kind}/{id}.json —— 扁平按 kind，不按日期，避免 rename 抖动。"""
        return f"items/{self.kind}/{self.id}.json"

    def payload(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "kind": self.kind,
            "id": self.id,
            "rev": self.rev.to_json(),
            "deleted": self.deleted,
            "deleted_at_ms": self.deleted_at_ms,
            "data": self.data,
            "meta": self.meta,
        }

    def to_dict(self) -> dict[str, Any]:
        """给 HTTP / CLI 的 DTO。"""
        out = dict(self.payload())
        out["rev"] = f"{self.rev.ms}.{self.rev.seq}.{self.rev.dev}"
        return out

    # -- 确定性字节 --------------------------------------------------------
    def to_bytes(self) -> bytes:
        text = json.dumps(
            self.payload(),
            ensure_ascii=False,      # 中文不转义，可读
            indent=2,
            sort_keys=True,          # 递归排序，含 data 内部
        )
        return (text + "\n").encode("utf-8")

    @classmethod
    def from_bytes(cls, blob: bytes | str) -> "Item":
        raw = blob.decode("utf-8") if isinstance(blob, (bytes, bytearray)) else blob
        d = json.loads(raw)
        return cls.from_payload(d)

    @classmethod
    def from_payload(cls, d: dict[str, Any]) -> "Item":
        return cls(
            kind=d["kind"],
            id=d["id"],
            rev=Rev.from_json(d["rev"]),
            data=d.get("data") or {},
            deleted=bool(d.get("deleted", False)),
            deleted_at_ms=d.get("deleted_at_ms"),
            schema=int(d.get("schema", SCHEMA_VERSION)),
            meta=d.get("meta") or {},
        )


def parse_rev(text: str) -> Rev:
    """把 DTO 里的 "ms.seq.dev" 还原成 Rev。"""
    ms, seq, dev = text.split(".", 2)
    return Rev(int(ms), int(seq), dev)
