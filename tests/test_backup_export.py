"""备份与导出（M6）。"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import time
from datetime import date
from pathlib import Path

import pytest

from qingdan.core import backup as bk
from qingdan.core import context, secrets
from qingdan.core.registry import QdError, call


# --------------------------------------------------------------------------- 创建


def test_create_produces_queryable_snapshot(app_ctx):
    call("task.create", title="要被备份的任务")
    out = bk.create()
    assert out["created"] is True and Path(out["path"]).is_file()

    probe = sqlite3.connect(out["path"])
    titles = [r[0] for r in probe.execute(
        "SELECT json_extract(data,'$.title') FROM item WHERE kind='task'")]
    probe.close()
    assert "要被备份的任务" in titles, "VACUUM INTO 出来的必须是一个能查的完整库"


def test_daily_backup_is_idempotent_within_a_day(app_ctx):
    call("task.create", title="A")
    first = bk.daily_backup()
    second = bk.daily_backup()
    assert first["created"] is True
    assert second["created"] is False, "同一天重复启动不该叠出两份备份"
    assert len(list(bk.backups_dir().glob("db-*.sqlite"))) == 1


def test_manual_backup_always_writes_a_new_snapshot(app_ctx):
    """「立即备份」不能是空操作：连着点两次必须真的落两份，且互不覆盖。

    第一版复用日期名，当天第二次点击返回 created:false 什么也没写 ——
    界面提示"已存在"，用户以为数据有备份了，其实那份改动从没进过任何快照。
    """
    one = call("backup.create", force=True)
    two = call("backup.create", force=True)
    assert one["created"] is True and two["created"] is True
    assert one["name"] != two["name"], "同一秒内连点也要各占一个文件，不能覆盖上一份"
    files = {p.name: p.stat().st_size for p in bk.backups_dir().glob("db-*.sqlite")}
    assert len(files) == 2 and all(s > 0 for s in files.values())


def test_newest_and_prune_follow_mtime_not_file_name(app_ctx):
    """按文件名排序在这里是错的：`-` (0x2D) 排在 `.` (0x2E) 前面，
    `db-2026-11-05-201530.sqlite` 会排到 `db-2026-11-05.sqlite` 之后。
    于是手动备份永远显示不成"最新"，prune 还会先把它当最旧的删掉。"""
    daily = bk.create(name=f"db-{date.today().isoformat()}.sqlite")
    manual = bk.create(force=True)                      # 日期名 + 时分秒后缀

    # 让"事实"和"文件名顺序"对立：日期名那份其实是更早写的
    old = time.time() - 3600
    os.utime(bk.backups_dir() / daily["name"], (old, old))

    status = call("backup.status")
    assert status["newest"]["name"] == manual["name"], "最新一份必须按时间认定"
    assert [b["name"] for b in bk.list_backups()] == [manual["name"], daily["name"]]

    removed = bk.prune(keep=1)
    assert removed == [daily["name"]], "清理要留最新、删最旧，不能按名字反着删"


def test_create_rejects_bad_name(app_ctx):
    with pytest.raises(QdError):
        bk.create(name="evil.txt")


def test_vacuum_reflects_uncommitted_wal_state(app_ctx):
    """WAL 模式下直接 copy 文件会漏掉未落盘事务。VACUUM INTO 不该有这个问题。"""
    tid = call("task.create", title="刚写的")["id"]
    snap = bk.create(force=True)
    probe = sqlite3.connect(snap["path"])
    assert probe.execute("SELECT COUNT(*) FROM item WHERE id=?", (tid,)).fetchone()[0] == 1
    probe.close()


# --------------------------------------------------------------------------- 保留与清理


def test_prune_keeps_newest_and_only_touches_its_own_names(app_ctx):
    names = []
    for i in range(1, 6):
        out = bk.create(name=f"db-2026-01-0{i}.sqlite")
        names.append(out["name"])
        # 同一秒内建出来的 5 份 mtime 相同，排序会退化成 glob 的任意顺序 ——
        # 不显式拉开时间，这个测试根本断言不到"留的是最新两份"，只断言了数量。
        stamp = 1_700_000_000 + i * 3600
        os.utime(bk.backups_dir() / out["name"], (stamp, stamp))
    # 目录里混进一个用户自己的文件，绝不能被当成备份删掉
    foreign = bk.backups_dir() / "我的笔记.sqlite"
    foreign.write_bytes(b"x")

    removed = bk.prune(keep=2)

    assert len(removed) == 3
    assert foreign.is_file(), "不符合命名规范的文件不能被清理"
    left = [b["name"] for b in bk.list_backups()]
    assert left == ["db-2026-01-05.sqlite", "db-2026-01-04.sqlite"], \
        f"应留下最新两份，实际 {left}"


def test_prune_rejects_absurd_keep(app_ctx):
    with pytest.raises(QdError):
        call("backup.prune", keep=0)


# --------------------------------------------------------------------------- 有效性校验


def test_is_valid_rejects_corrupt_and_foreign_db(app_ctx):
    d = bk.backups_dir()
    d.mkdir(parents=True, exist_ok=True)
    bad = d / "db-2026-01-01.sqlite"
    bad.write_bytes(b"this is not a database at all")
    assert bk.is_valid(bad) is False

    other = d / "db-2026-01-02.sqlite"
    con = sqlite3.connect(other)
    con.execute("CREATE TABLE unrelated (x)")
    con.commit()
    con.close()
    assert bk.is_valid(other) is False, "表结构不符的库不能算有效备份"


# --------------------------------------------------------------------------- 恢复


def test_restore_is_scheduled_not_hot_swapped(app_ctx):
    """运行中换底层文件是未定义行为 —— 只能写标记，重启时执行。"""
    call("task.create", title="备份前的数据")
    snap = bk.create(force=True)
    call("task.create", title="备份之后新增的")

    out = bk.schedule_restore(snap["name"])
    assert out["scheduled"] is True and out["restart_required"] is True
    assert (Path(app_ctx.data_dir) / bk.RESTORE_MARKER).is_file()
    # 主库此刻还没被动过
    assert call("task.list")["total"] == 2


def test_apply_pending_restore_swaps_and_keeps_safety(app_ctx):
    # 必须先存下路径：fixture 交出的是全局单例本身，shutdown() 会把 data_dir 清空，
    # 之后再读 app_ctx.data_dir 就是 None，init(None) 会落到默认目录 = 开了另一个库。
    data_dir = Path(app_ctx.data_dir)
    tid = call("task.create", title="老数据")["id"]
    snap = bk.create(force=True)
    call("task.delete", item_id=tid)
    assert call("task.list", include_deleted=True)["items"][0]["deleted"] is True

    bk.schedule_restore(snap["name"])
    context.shutdown()

    c2 = context.init(data_dir)
    assert c2.store.get(tid).deleted is False, "恢复应把删除撤销回来"
    assert (data_dir / bk.RESTORE_MARKER).exists() is False
    assert (data_dir / "db.sqlite.before-restore").is_file(), "换库前要留下现场"
    # restore_scheduled 写在被换掉的旧库里，恢复后不存在是正确行为 ——
    # 恢复的证据是 before-restore 文件和已消失的标记，不是数据库里的一行状态
    assert c2.store.state_get("restore_scheduled") is None


def test_restore_rejects_path_traversal(app_ctx):
    """name 直接拼进路径，必须挡住 ../ 之类的穿越。"""
    for evil in ("../../db.sqlite", "..%2fdb.sqlite", "absolute/path.sqlite"):
        with pytest.raises(QdError) as e:
            bk.schedule_restore(evil)
        assert e.value.code == "NOT_FOUND"


def test_restore_rejects_corrupt_backup(app_ctx):
    d = bk.backups_dir()
    d.mkdir(parents=True, exist_ok=True)
    (d / "db-2026-01-01.sqlite").write_bytes(b"garbage")
    with pytest.raises(QdError) as e:
        bk.schedule_restore("db-2026-01-01.sqlite")
    assert "损坏" in e.value.message


def test_restore_endpoints(app_ctx):
    call("task.create", title="x")
    snap = call("backup.create", name="db-2026-02-02.sqlite", force=True)
    assert snap["created"] is True
    assert call("backup.list")["total"] == 1
    out = call("backup.restore", name="db-2026-02-02.sqlite")
    assert out["restart_required"] is True


def test_backup_status_flags_stale(app_ctx):
    st = call("backup.status")
    assert st["count"] == 0 and st["stale"] is True, "从没备份过必须报成 stale"
    call("backup.create")
    st2 = call("backup.status")
    assert st2["count"] == 1 and st2["stale"] is False
    assert st2["age_days"] == 0.0


# --------------------------------------------------------------------------- 导出


def test_csv_has_all_columns_and_rows(app_ctx):
    g = call("group.create", name="工作任务")["id"]
    call("task.create", title="写周报", list_id=g, priority=1,
         due_at="2026-11-06T20:00:00", tags=["重要"])
    body = call("data.preview", format="csv")["content"]
    header = body.splitlines()[0]
    assert header == "标题,类型,分组,小节,状态,优先级,到期,提醒,重复,标签,颜色,备注," \
                     "创建时间,完成时间,已删除,版本戳"
    assert "写周报" in body and "工作任务" in body and "P1" in body and "重要" in body


def test_csv_on_disk_has_bom_for_excel(app_ctx):
    call("task.create", title="中文任务")
    out = call("data.export", format="csv")
    raw = Path(out["path"]).read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf"), "无 BOM 的 UTF-8 CSV 在 Excel 里中文会乱码"


def test_export_header_language_follows_the_lang_param(app_ctx):
    """表头语言由调用方指定：界面切英文，导出的文件不能还是整页中文表头。

    「不写死语言」是硬要求（开发文档 §15）。表头属于**文件内容**不是界面文案，
    所以它不进响应体文案扫描，改由这条测试单独管。
    """
    call("task.create", title="写周报", priority=4)
    en = call("data.preview", format="csv", lang="en")["content"]
    assert not re.search(r"[一-鿿]", en.splitlines()[0]), "英文表头不该有中文"
    assert en.splitlines()[0].startswith("title,kind,group,section"), en.splitlines()[0]
    assert ",none," in en, "优先级为空的标记也要跟着语言走"

    md_en = call("data.preview", format="md", lang="en")["content"]
    assert "Checklist export" in md_en and "清单导出" not in md_en

    with pytest.raises(QdError) as e:
        call("data.preview", format="csv", lang="fr")
    assert e.value.code == "VALIDATION_ERROR"


def test_markdown_groups_by_group(app_ctx):
    g = call("group.create", name="学习安排")["id"]
    call("task.create", title="行测 2 组", list_id=g, due_at="2026-11-06T10:00:00")
    call("task.create", title="没分组的")
    md = call("data.preview", format="md")["content"]
    assert "## 学习安排" in md and "## 收集箱" in md
    assert "- [ ] 行测 2 组" in md


def test_export_excludes_secrets(app_ctx):
    """导出只出 Item。令牌/DPAPI 密文一旦混出去，备份文件就成了凭据载体。"""
    call("auth.login", token="LEAKCHECK-TOKEN")
    call("alert.config_update", config={"secrets": {"dingtalk_secret": "SECANOTHER"}})
    call("task.create", title="普通任务")
    for fmt in ("csv", "json", "md"):
        body = call("data.preview", format=fmt)["content"]
        assert "LEAKCHECK" not in body and "SECANOTHER" not in body and "dpapi:" not in body
    out = call("data.export", format="json")
    assert "LEAKCHECK" not in Path(out["path"]).read_text(encoding="utf-8")


def test_export_rejects_unknown_format(app_ctx):
    with pytest.raises(QdError):
        call("data.export", format="xlsx")


def test_preview_does_not_write_files(app_ctx):
    call("task.create", title="x")
    before = set((Path(app_ctx.data_dir) / "exports").glob("*")) if (
        Path(app_ctx.data_dir) / "exports").is_dir() else set()
    call("data.preview", format="csv")
    after = set((Path(app_ctx.data_dir) / "exports").glob("*"))
    assert after == before, "preview 不该落盘"


# --------------------------------------------------------------------------- 导入


def test_export_import_roundtrip(tmp_path):
    """导出的 JSON 必须能原样导回一台空机器。"""
    context.init(tmp_path / "src")
    g = call("group.create", name="工作任务")["id"]
    t = call("task.create", title="往返任务", list_id=g, priority=1,
             due_at="2026-11-06T20:00:00", tags=["重要"])["id"]
    rev_str = call("task.get", item_id=t)["rev"]      # DTO 里是 "ms.seq.dev" 字符串
    payload = call("data.preview", format="json", limit=100)["content"]
    context.shutdown()

    context.init(tmp_path / "dst")
    out = call("data.import", content=payload)
    assert out["applied"] == 2 and out["invalid"] == 0
    got = call("task.get", item_id=t)
    assert got["data"]["title"] == "往返任务"
    assert got["data"]["list_id"] == g
    assert got["rev"] == rev_str, "导入必须保留原版本戳，否则同步会误判"
    context.shutdown()


def test_import_is_idempotent(tmp_path):
    """同一份导出反复导入，只有第一次有效果。"""
    context.init(tmp_path / "src")
    call("task.create", title="只导一次")
    payload = call("data.preview", format="json")["content"]
    context.shutdown()

    context.init(tmp_path / "dst")
    first = call("data.import", content=payload)
    second = call("data.import", content=payload)
    assert first["applied"] == 1
    assert second["applied"] == 0, "重复导入不该产生新改动"
    assert call("task.list")["total"] == 1
    context.shutdown()


def test_import_never_downgrades_newer_local(app_ctx):
    """本地已经改过的项，导入一份旧的不能把它覆盖回去。"""
    tid = call("task.create", title="本地新")["id"]
    older = json.dumps({"product": "qingdan", "items": [{
        "schema": 1, "kind": "task", "id": tid, "deleted": False, "data": {"title": "很久以前"},
        "rev": {"ms": 1, "seq": 0, "dev": "D-old"}}]})
    out = call("data.import", content=older)
    assert out["skipped"] == 1
    assert call("task.get", item_id=tid)["data"]["title"] == "本地新"


def test_import_marks_items_dirty(app_ctx):
    """导入的项必须待推送，否则另一台机器永远看不到。"""
    tid = call("task.create", title="x")["id"]
    payload = json.dumps({"product": "qingdan", "items": [{
        "schema": 1, "kind": "task", "id": "IMPORT-1", "deleted": False,
        "data": {"title": "导入的"}, "rev": {"ms": 5000, "seq": 0, "dev": "D-x"}}]})
    app_ctx.store.mark_clean()
    call("data.import", content=payload)
    assert app_ctx.store.dirty_count() == 1


def test_import_rejects_foreign_json(app_ctx):
    for bad in ('{"items": []}', '{"product":"other","items":[]}', "not json"):
        with pytest.raises(QdError):
            call("data.import", content=bad)


def test_import_from_file(tmp_path, app_ctx):
    src = call("task.create", title="文件导入")["id"]
    out = call("data.export", format="json")
    app_ctx.store.db.execute("DELETE FROM item WHERE id=?", (src,))
    app_ctx.store.db.commit()
    r = call("data.import", path=out["path"])
    assert r["applied"] >= 1
    assert call("task.get", item_id=src)["data"]["title"] == "文件导入"


def test_import_from_missing_path(app_ctx):
    with pytest.raises(QdError):
        call("data.import", path="Z:\\nope\\x.json")


def test_status_ignores_newer_invalid_backup(app_ctx):
    good = bk.create(force=True)
    bad = bk.backups_dir() / 'db-2026-01-01-broken.sqlite'
    bad.write_bytes(b'broken')
    future = time.time() + 3600
    os.utime(bad, (future, future))
    assert bk.backup_status()['newest']['name'] == good['name']


@pytest.mark.parametrize('payload', [[], None, {'name': '../db-2026-01-01.sqlite'}])
def test_pending_restore_rejects_invalid_marker(app_ctx, payload):
    data_dir = Path(app_ctx.data_dir)
    marker = data_dir / bk.RESTORE_MARKER
    marker.write_text(json.dumps(payload), encoding='utf-8')
    assert bk.apply_pending_restore(data_dir)['applied'] is False
    assert not marker.exists()


def test_failed_restore_copy_preserves_database(app_ctx, monkeypatch):
    data_dir = Path(app_ctx.data_dir)
    call('task.create', title='must survive')
    snap = bk.create(force=True)
    bk.schedule_restore(snap['name'])
    context.shutdown()
    original = (data_dir / 'db.sqlite').read_bytes()

    def fail_copy(*args, **kwargs):
        raise OSError('disk full')

    monkeypatch.setattr(bk.shutil, 'copy2', fail_copy)
    with pytest.raises(OSError, match='disk full'):
        bk.apply_pending_restore(data_dir)
    assert (data_dir / 'db.sqlite').read_bytes() == original
    assert (data_dir / bk.RESTORE_MARKER).exists()


def test_validation_rejects_same_table_names_wrong_columns(app_ctx):
    path = Path(app_ctx.data_dir) / 'foreign.sqlite'
    with sqlite3.connect(path) as con:
        for table in ('item', 'sync_state', 'settings'):
            con.execute(f'CREATE TABLE {table} (unrelated TEXT)')
    assert not bk.is_valid(path)
