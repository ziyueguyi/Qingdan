"""快速添加解析。固定 today/now，避免测试结果随日期漂移。"""
from __future__ import annotations

from datetime import date, datetime

import pytest

from qingdan.core.nlp import parse_quick
from qingdan.core.registry import QdError, call

TODAY = date(2026, 11, 5)          # 周四
NOW = datetime(2026, 11, 5, 10, 0)
GROUPS = [{"id": "G-WORK", "name": "工作任务"}, {"id": "G-LIFE", "name": "个人备忘"}]
TAGS = ["重要", "数据", "考编", "工作任务"]


def p(text):
    return parse_quick(text, today=TODAY, now=NOW, groups=GROUPS, tags=TAGS)


def test_plain_text_becomes_title():
    out = p("买猫粮")
    assert out["title"] == "买猫粮" and out["due_at"] is None and out["priority"] is None


def test_full_sentence_parses_every_token():
    """# 与 @ 同义为标签（清单界面移除后 # 不再指向清单，见 nlp 模块 docstring）。"""
    out = p("明天下午3点 交周报 #工作任务 @重要 !P1")
    assert out["due_at"] == "2026-11-06T15:00:00"
    assert out["list_id"] is None and out["list_name"] is None
    assert out["tags"] == ["工作任务", "重要"] and out["priority"] == 1
    assert out["title"] == "交周报"
    assert not out["warnings"]


@pytest.mark.parametrize("word,expected", [
    ("今天 开会", "2026-11-05"),
    ("明天 开会", "2026-11-06"),
    ("后天 开会", "2026-11-07"),
    ("大后天 开会", "2026-11-08"),
    ("昨天 开会", "2026-11-04"),
    ("3天后 开会", "2026-11-08"),
    ("两天后 开会", "2026-11-07"),
    ("周五 开会", "2026-11-06"),
    ("下周一 开会", "2026-11-16"),
    ("12月20日 开会", "2026-12-20"),
    ("2026-12-01 开会", "2026-12-01"),
])
def test_date_words(word, expected):
    out = p(word)
    assert out["due_at"] and out["due_at"].startswith(expected), (word, out["due_at"])
    assert "开会" in out["title"]


@pytest.mark.parametrize("text,expected", [
    ("下午3点 开会", "2026-11-05T15:00:00"),
    ("上午9点 开会", "2026-11-05T09:00:00"),
    ("15:30 开会", "2026-11-05T15:30:00"),
    ("晚上8点 开会", "2026-11-05T20:00:00"),
    ("明天早上7点 开会", "2026-11-06T07:00:00"),
])
def test_time_forms(text, expected):
    assert p(text)["due_at"] == expected, text


def test_half_hour_and_noon():
    assert p("3点半 开会")["due_at"] == "2026-11-05T15:30:00"
    assert p("中午12点 吃饭")["due_at"] == "2026-11-05T12:00:00"


def test_time_without_date_falls_on_today():
    """只写时间不写日期，默认今天 —— 不能变成无日期任务。"""
    out = p("15:30 开会")
    assert out["due_at"].startswith("2026-11-05")
    assert out["due_all_day"] is False


def test_all_day_when_only_date():
    assert p("明天 交材料")["due_all_day"] is True


def test_multiple_tags():
    out = p("@重要 @数据 整理采集清单")
    assert out["tags"] == ["重要", "数据"]
    assert out["title"] == "整理采集清单"


def test_hash_of_unknown_name_is_a_tag_not_a_hidden_list():
    """# 指向不存在的名字时按新标签处理并提示，绝不悄悄归进看不见的清单。"""
    out = p("#没见过的标签 写周报")
    assert out["list_id"] is None
    assert out["tags"] == ["没见过的标签"]
    assert any(w.startswith("unknown_tag") for w in out["warnings"])
    assert out["title"] == "写周报"


def test_unknown_tag_warns():
    out = p("@没见过的标签 任务")
    assert any(w.startswith("unknown_tag") for w in out["warnings"])


def test_priority_variants():
    assert p("!P1 任务")["priority"] == 1
    assert p("！P3 任务")["priority"] == 3
    assert p("!p2 任务")["priority"] == 2


def test_past_time_is_flagged_not_rejected():
    out = p("今天上午9点 开会")     # now 是 10:00
    assert out["due_at"] == "2026-11-05T09:00:00"
    assert "due_in_past" in out["warnings"]


def test_tokens_are_stripped_from_title():
    out = p("明天下午3点 交周报 #工作任务 @重要 !P1")
    for junk in ("明天", "下午3点", "#工作任务", "@重要", "!P1"):
        assert junk not in out["title"]


def test_all_tokens_input_keeps_original_as_title():
    """全是 token 没有正文时，退回原文，绝不建出空标题任务。"""
    out = p("明天")
    assert out["title"] == "明天"
    assert "title_is_raw_text" in out["warnings"]


def test_empty_input_is_safe():
    assert p("")["title"] == ""
    assert p("   ")["title"] == ""


def test_month_day_already_passed_rolls_to_next_year():
    out = p("1月2日 开会")
    assert out["due_at"].startswith("2027-01-02")


# --------------------------------------------------------------------------- 接口层


def test_quick_add_defaults_to_parse_only(app_ctx):
    out = call("task.quick_add", text="明天下午3点 交周报 #工作任务 !P1", today="2026-11-05")
    assert out["created"] is None
    assert out["parsed"]["title"] == "交周报"
    assert out["parsed"]["tags"] == ["工作任务"]
    assert call("task.list")["total"] == 0, "默认不能落库"


def test_quick_add_create_persists(app_ctx):
    call("tag.create", name="重要")
    out = call("task.quick_add", text="明天下午3点 交周报 #工作任务 @重要 !P1",
               today="2026-11-05", create=True)
    d = out["created"]["data"]
    assert d["list_id"] is None, "清单界面已移除，快速添加不该再归进清单"
    assert d["priority"] == 1
    assert d["tags"] == ["工作任务", "重要"]
    assert d["due_at"] == "2026-11-06T15:00:00"
    assert call("task.list")["total"] == 1


def test_quick_add_plain_text_still_creates(app_ctx):
    out = call("task.quick_add", text="买猫粮", today="2026-11-05", create=True)
    assert out["created"]["data"]["title"] == "买猫粮"
    assert out["created"]["data"]["due_at"] is None


def test_quick_add_never_raises_on_garbage(app_ctx):
    """快速添加是脑随手记的入口，任何输入都不能变成报错。"""
    for junk in ["!P9", "#@@", "@@@ !!!", "99月99日 任务", "!!!!"]:
        out = call("task.quick_add", text=junk, today="2026-11-05", create=True)
        assert out["created"]["data"]["title"], junk


def test_quick_add_empty_is_the_only_rejected_case(app_ctx):
    with pytest.raises(QdError):
        call("task.quick_add", text="   ", create=True)


# --------------------------------------------------------------------------- 应用偏好


def test_app_prefs_roundtrip(app_ctx):
    out = call("app.pref_set", prefs={"theme": "dark", "lang": "en", "accent": "teal"})
    assert out["synced"] is False
    assert out["prefs"] == {"theme": "dark", "lang": "en", "accent": "teal"}
    assert call("app.pref_get")["prefs"]["theme"] == "dark"


def test_app_prefs_reject_bad_values(app_ctx):
    for bad in ({"theme": "neon"}, {"lang": "fr"}, {"size": "xl"}):
        with pytest.raises(QdError):
            call("app.pref_set", prefs=bad)


def test_app_prefs_reject_unknown_key(app_ctx):
    with pytest.raises(QdError):
        call("app.pref_set", prefs={"layout": "board"}), "layout 属视图偏好，走 view.pref_set"


def test_app_prefs_do_not_make_items_dirty(app_ctx):
    """改主题绝不能产生一次待推送 —— 否则每次切深色都会往 git 提一个 commit。"""
    call("task.create", title="A")
    app_ctx.store.mark_clean()
    assert app_ctx.store.dirty_count() == 0
    call("app.pref_set", prefs={"theme": "dark"})
    assert app_ctx.store.dirty_count() == 0
