"""同步失败路径（M5 矩阵 B8 / B9 / B12 / B17 + 时钟偏差守卫）。

单独成文件而不是塞进 test_sync.py，是因为这四条测的是"事情没成"的时候：
成功路径的用例已经够密，失败路径反而只有一句"报错了"。而恰恰是失败时最容易
悄悄丢数据 —— 引擎在成功分支上写得再对，也不代表异常分支守住了 dirty 与墓碑。
"""
from __future__ import annotations

import json

from harness import Machine, remote, root          # noqa: F401  （fixture 由 conftest 注入）
from qingdan.core.gitrepo import GitError, PushRejected
from qingdan.core.merge import diff_data
from qingdan.core.sync import SKEW_LIMIT_MS, MAX_PUSH_ATTEMPTS, parse_tree


def machine(root, name, remote, device=None):
    return Machine(root, name, remote, device or f"D-{name}")


# --------------------------------------------------------------------------- B8 / B9 失败时不许丢


def test_push_rejected_three_times_keeps_every_change(root, remote):
    """B8：重试耗尽必须「没推上去，但一条也没丢」。

    这是同步最坏的一条路径：报了错却又把改动当已同步，用户下次打开看到的还是旧数据，
    而且再也想不起来要重推。所以除了断言「还在」，还必须断言「恢复后能直接推上去」。
    """
    a = machine(root, "a8", remote)
    item = a.add(title="基线")
    a.sync()
    a.edit(item.id, title="推不上去的改动")
    a.add(title="断连期间新建的")

    real_push = a.git.commit_and_push

    def always_rejected(*args, **kwargs):
        # 真实构造签名是 (local_sha, remote_sha)，桩必须照它来 —— 参数写错时
        # TypeError 会被引擎的 except 吞掉，测试就会以"通过"的样子测不到东西。
        raise PushRejected(b"local", b"remote")

    a.git.commit_and_push = always_rejected
    report = a.sync()

    assert report.ok is False
    assert report.attempts == MAX_PUSH_ATTEMPTS, "应当真的试满三轮，而不是试一次就放弃"
    assert "保留" in (report.error or ""), "错误信息必须说清改动没丢"
    assert {"推不上去的改动", "断连期间新建的"} <= a.titles()
    assert a.store.dirty_count() == 2, "推送失败后待推送标记必须原样留着"

    a.git.commit_and_push = real_push               # 相当于远端恢复了
    back = a.sync()
    assert back.ok, f"恢复后应能直接推上去，实际 error={back.error}"
    assert a.store.dirty_count() == 0, "推成功了还不归零，等于每轮都重推一遍"

    b = machine(root, "b8", remote)
    b.sync()
    assert b.item(item.id).data["title"] == "推不上去的改动"


def test_fetch_failure_leaves_local_fully_usable(root, remote):
    """B9：断网。只读失败绝不能影响本地读写，也不能吞掉待推送标记。"""
    a = machine(root, "a9", remote)
    a.add(title="联网时建的")
    a.sync()

    fresh = a.add(title="断网期间新建")
    real_fetch = a.git.fetch

    def unreachable(url):
        raise GitError("网络不可达：getaddrinfo failed")

    a.git.fetch = unreachable
    report = a.sync()
    assert report.ok is False and "网络不可达" in report.error
    assert report.attempts == 0, "连远端都没读到，不该声称尝试过推送"
    assert a.store.dirty_count() == 1

    # 本地照常读写 —— 断网不能把应用变成只读
    a.edit(fresh.id, title="断网时还能继续改")
    assert a.item(fresh.id).data["title"] == "断网时还能继续改"
    late = a.add(title="断网时又建了一条")
    # dirty_count 数的是**条数**（同一项改十次仍是一条待推送），这里必须补一条新的
    assert a.store.dirty_count() == 2, f"待推送条目数不对：{a.store.dirty_count()}"

    a.git.fetch = real_fetch
    back = a.sync()
    assert back.ok and a.store.dirty_count() == 0

    b = machine(root, "b9", remote)
    b.sync()
    assert b.item(fresh.id).data["title"] == "断网时还能继续改"
    assert b.item(late.id).data["title"] == "断网时又建了一条", "断网期间的两条改动都要能补推上去"


# --------------------------------------------------------------------------- B12 / B17 时间与复活


def test_tombstone_never_resurrects_across_many_rounds(root, remote):
    """B12：删除之后没再被编辑，那么同步多少轮都不该复活。

    复活不需要多复杂的 bug —— 一轮合并判断把「谁新谁赢」的方向写反就够，
    所以这里连跑五轮逐轮断言，而不是只断言一次。
    """
    a = machine(root, "a12", remote)
    item = a.add(title="会被删掉的任务", due_at="2026-11-05T10:00:00")
    a.sync()
    b = machine(root, "b12", remote)
    b.sync()
    b.delete(item.id)
    b.sync()

    for round_no in range(1, 6):
        assert a.sync().ok, f"第 {round_no} 轮 A 同步失败"
        assert b.sync().ok, f"第 {round_no} 轮 B 同步失败"
        assert a.item(item.id).deleted, f"第 {round_no} 轮 A 侧复活了"
        assert b.item(item.id).deleted, f"第 {round_no} 轮 B 侧复活了"
        assert a.item(item.id).rev == b.item(item.id).rev, \
            "两边必须停在同一个版本戳，否则下一轮又开始比大小"

    tree = a.git.read_tree(a.git.head())
    payload = json.loads(tree[f"items/task/{item.id}.json"].decode("utf-8"))
    assert payload["deleted"] is True, "远端也得是墓碑，而不只是「文件不见了」"
    assert a.conflicts() == [] and b.conflicts() == [], "纯粹的删除不该制造冲突记录"


def test_fast_clock_is_blocked_then_sub_limit_skew_is_demonstrated(root, remote):
    """B17：机器钟不准的两个层次，实测边界就在这条线上。

    最初这条用例写的是"钟快一天的机器会赢"。加了 §10 的偏差守卫之后，那种机器
    **根本推不上去**（|offset| > 5 分钟直接挡下推送），所以原来的断言当场失败 ——
    失败得很有价值：它证明守卫确实把最坏情形关掉了，也把剩下的风险精确地
    缩小到"5 分钟以内"这一段。这段仍然要如实演示，不能当它不存在：
    钟快 4 分钟的机器，离线同期改动会压过真实世界里更晚的那一份。
    """
    # ① 超出限值：不是"谁赢"，而是根本不许推
    a = machine(root, "a17big", remote)
    base = a.add(title="共同基线")
    a.sync()
    a.edit(base.id, title="钟快一天的改动")
    a.store.clock.set_offset(86_400_000)
    blocked = a.sync()
    assert blocked.ok is False and blocked.error_key == "clock_skew"
    assert blocked.pushed == 0 and a.store.dirty_count() == 1, "挡下时必须还是待推送"

    # ② 限值以内（4 分钟）：HLC 的固有代价，如实演示
    x = machine(root, "a17", remote)
    item = x.add(title="共同基线")
    x.sync()
    y = machine(root, "b17", remote)
    y.sync()

    x.store.clock.set_offset(240_000)               # 只快 4 分钟，守卫不拦
    x.edit(item.id, title="快四分钟的机器改的")
    y.edit(item.id, title="真实世界里更晚的改动")     # 后写的，但版本戳更小

    assert x.sync().ok
    assert y.sync().ok
    assert y.item(item.id).data["title"] == "快四分钟的机器改的", "ms 大者胜，与真实先后无关"
    assert y.conflicts(), "落败那份必须能从冲突表找回，不能静默消失（B13）"

    # ③ 自愈：y observe 过超前的戳之后，后续修改重新压在它上面
    y.edit(item.id, title="校正之后又改的")
    assert y.sync().ok and x.sync().ok
    assert x.item(item.id).data["title"] == "校正之后又改的"
    assert y.item(item.id).data["title"] == "校正之后又改的"


def test_real_clash_leaves_enough_diff_for_the_conflict_ui(root, remote):
    """两台机器真撞车之后，引擎留下的冲突记录必须**够界面摆出字段对照**。

    前面那些冲突用例都是手工 `record_conflict` 喂数据的；只有走完整同步路径才知道
    真实记录里 local/remote/base 三份是不是都在、字段名对不对得上 FIELD_META。
    缺一份，冲突界面就只能显示"有冲突"而没法让用户裁决 —— 那等于没解决问题。
    """
    a = machine(root, "cfA", remote)
    item = a.add(title="共同起点", priority=3)
    a.sync()
    b = machine(root, "cfB", remote)
    b.sync()

    a.edit(item.id, title="A 改的标题", priority=1)
    a.sync()
    b.edit(item.id, title="B 改的标题", note="B 写的备注")
    report = b.sync()
    assert report.ok and report.conflicts >= 1, report

    row = list(b.store.db.execute("SELECT * FROM conflict WHERE item_id=?", (item.id,)))
    assert len(row) == 1, "撞一次就该留一条，留多了是骚扰"
    payload = {c: json.loads(row[0][c]) if row[0][c] else None
               for c in ("local_json", "remote_json", "base_json")}
    assert all(payload.values()), f"三份都得在，否则没法判断谁改了什么：{payload.keys()}"
    assert row[0]["reason"] == "both_changed"

    diffs = diff_data("task", payload["local_json"]["data"],
                      payload["remote_json"]["data"], payload["base_json"]["data"])
    states = {d["key"]: d["state"] for d in diffs}
    assert states.get("title") == "both_diff", states
    assert states.get("note") == "local_only", f"B 自己新加的备注不该被算成远端改动：{states}"
    # priority 只有 A 改过 → remote_only。这一格能区分出来，全靠记冲突时把基线的
    # **完整字段**从 base_commit 那棵树里取回来；只存 (rev, deleted) 的话这里永远
    # 显示 both_diff，用户得自己猜是谁改的 —— 那界面就等于没解决问题。
    assert states.get("priority") == "remote_only", states
    for d in diffs:
        assert d["label_key"].startswith("f_") and d["type"], d

    # 这台机器上 B 的版本号更晚，所以 B 胜出、A 的改动落到冲突表里等人裁决。
    # 关键不是谁赢，而是**输的那份原文必须还在**（B13 的承诺），否则"手工合并"无从谈起。
    assert b.item(item.id).data["title"] == "B 改的标题"
    loser = json.loads(row[0]["remote_json"])["data"]
    assert loser["title"] == "A 改的标题" and loser["priority"] == 1, loser


def test_clock_skew_blocks_push_but_never_the_pull(root, remote):
    """§10 承诺的守卫：本机钟与服务器差太多就拒绝推送 —— 拉取照常。

    为什么值得为它单独写一条：版本先后完全靠 ms。钟差 6 分钟时推上去，
    会把对方"其实更晚"的改动判成旧的并覆盖掉，而事后从数据上**看不出发生过覆盖**。
    反过来，拉取不会伤害任何人（那是接受远端的事实），所以只该挡推送。
    """
    a = machine(root, "askew", remote)
    kept = a.add(title="钟准时候推上去的")
    a.sync()
    assert a.store.dirty_count() == 0

    a.add(title="钟差期间的改动")
    a.store.clock.set_offset(SKEW_LIMIT_MS + 60_000)
    report = a.sync()

    assert report.ok is False and report.error_key == "clock_skew"
    assert report.pushed == 0
    assert a.store.dirty_count() == 1, "挡下推送时改动必须仍然标着待推送，不能当已同步"
    remote_titles = {it.data.get("title") for it in parse_tree(a.git.read_tree(a.git.head())).values()}
    assert "钟准时候推上去的" in remote_titles
    assert "钟差期间的改动" not in remote_titles, "远端不能被这一轮写脏"

    a.store.clock.set_offset(0)                     # 校正之后自动推上去，不用用户重做
    fixed = a.sync()
    assert fixed.ok and a.store.dirty_count() == 0
    remote_titles = {it.data.get("title") for it in parse_tree(a.git.read_tree(a.git.head())).values()}
    assert "钟差期间的改动" in remote_titles

    # 钟差期间照样能拉：别人的改动照常进来
    b = machine(root, "bskew", remote)
    b.sync()
    b.add(title="另一台机器写的")
    b.sync()
    a.store.clock.set_offset(SKEW_LIMIT_MS + 60_000)
    pulled = a.sync()
    assert pulled.pulled >= 1, "只该挡推送，不该把同步整体废掉"
    assert any(it.data.get("title") == "另一台机器写的" for it in a.store.list_items())
