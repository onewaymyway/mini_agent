"""选题标题：有深度、参照文库风格；模板式标题被拒绝；失败时保留模板标题，不影响其它选题。全部假模型。"""

import re

import yaml

from article_garden.planning import states, topics
from tests.test_planning import _world


def test_validator_rejects_template_titles_duplicates_and_bad_kinds():
    v = topics._title_validator({"g1", "g2"}, existing_titles=["已有文章的标题示例"])
    bad = {"items": [
        {"tpid": "g1", "title": "向量数据库是什么", "angle": "x", "kind": "概念讲解", "level": "入门"},
        {"tpid": "g2", "title": "向量数据库入门指南", "angle": "x", "kind": "教程", "level": "入门"},
    ]}
    issues = " ".join(v(bad))
    assert "模板式写法" in issues
    dup = {"items": [
        {"tpid": "g1", "title": "已有文章的标题示例", "angle": "x", "kind": "概念讲解", "level": "进阶"},
        {"tpid": "g2", "title": "两个相同的深度标题示例", "angle": "x", "kind": "概念讲解", "level": "进阶"},
    ]}
    assert "雷同" in " ".join(v(dup))
    wrong = {"items": [{"tpid": "g1", "title": "一个足够长的具体问题标题", "angle": "x",
                        "kind": "随便", "level": "进阶"}]}
    assert "kind 或 level 不合法" in " ".join(v(wrong))


def test_validator_accepts_a_deep_specific_title():
    v = topics._title_validator({"g1"}, existing_titles=[])
    good = {"items": [{"tpid": "g1", "title": "向量索引为何在十亿规模下必须牺牲一部分精度",
                       "angle": "讲清召回率与延迟之间的取舍", "kind": "概念讲解", "level": "进阶"}]}
    assert v(good) == []


def test_profile_shows_library_titles_and_styles(data_home):
    ctx = _world(data_home)
    prof = topics.style_profile(ctx)
    assert "代表性标题" in prof and "文体分布" in prof and "层级分布" in prof


class TitleLLM:
    """记录标题阶段的提示词；返回预设的回复（可以是模板式标题，用来测试回退）。"""

    def __init__(self, title_reply=None):
        self.title_prompts = []
        self.title_reply = title_reply

    def ask(self, prompt, system=""):
        if "【阶段:选题标题】" in prompt:
            self.title_prompts.append(prompt)
            if self.title_reply is not None:
                return self.title_reply
            items = []
            for line in prompt.splitlines():
                m = re.match(r"tpid: (g\d+) ｜.*必须覆盖的概念：(\S+)", line)
                if m:
                    items.append({"tpid": m.group(1), "title": f"{m.group(2)}的取舍与误区解析",
                                  "angle": "讲清取舍与常见误区", "kind": "概念讲解", "level": "进阶"})
            return yaml.safe_dump({"items": items}, allow_unicode=True)
        from tests.test_planning import MidLLM
        return MidLLM(duplicate=False).ask(prompt, system)


def test_gap_topics_get_model_written_titles_grounded_in_the_library(data_home):
    ctx = _world(data_home)
    llm = TitleLLM()
    topics.run(ctx, llm)
    assert llm.title_prompts, "应调用模型撰写标题"
    assert any(first for first in llm.title_prompts if "代表性标题" in first)
    rows = [r for r in states.load_topics(ctx) if r["status"] == states.PROPOSED]
    assert rows and all("取舍与误区" in r["title"] for r in rows)
    assert all(not topics.GENERIC_TITLE.search(r["title"]) for r in rows)


def test_unusable_title_output_keeps_template_titles_and_does_not_block(data_home):
    ctx = _world(data_home)
    generic = yaml.safe_dump({"items": [{"tpid": "g1", "title": "大语言模型是什么", "angle": "x",
                                         "kind": "概念讲解", "level": "入门"}]}, allow_unicode=True)
    llm = TitleLLM(title_reply=generic)
    res = topics.run(ctx, llm)
    rows = states.load_topics(ctx)
    assert rows, "即使标题未通过，选题仍应生成"
    assert res["proposed"] >= 1
    assert any(r["title"].endswith("是什么") or r["title"].endswith("入门") for r in rows)  # 模板标题保留
