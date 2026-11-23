"""摘要的修复反馈：近似引文代码校正（不耗模型调用）、最接近句子提示、失败原因写入运行日志。全部假模型。"""

import yaml

from article_garden import domains, joblog
from article_garden.digest import runner as drunner
from article_garden.digest import summarize as sm
from article_garden.digest import validate
from article_garden.library import catalog, importer
from tests.fakes import article

TEXT = article("测试文章", "")
FIRST = "向量数据库用于存储和检索高维向量，常见于语义搜索与检索增强生成第0段。"


def _reply(quote, text="向量数据库"):
    return yaml.safe_dump({"title": "测试", "thesis": "论点", "kind": "概念讲解", "level": "入门",
                           "self_score": 4, "concepts": [text],
                           "claims": [{"text": "断言", "quote": quote}], "dangling": []}, allow_unicode=True)


class Scripted:
    def __init__(self, replies):
        self.replies = list(replies)
        self.prompts = []

    def ask(self, prompt, system=""):
        self.prompts.append(prompt)
        return self.replies.pop(0) if self.replies else self.replies_default()

    def replies_default(self):
        return _reply("原文中不存在的句子")


def test_near_verbatim_quote_is_corrected_in_code_without_a_repair_call():
    near = FIRST.replace("用于", "用来")                    # 与原文一字之差
    llm = Scripted([_reply(near)])
    log = []
    obj = sm.summarize(TEXT, llm, max_repair_rounds=3, on_round=log.append)
    assert len(llm.prompts) == 1                           # 没有发起修复调用
    assert obj["claims"][0]["quote"] == FIRST              # 引文已校正为原文逐字句子
    assert any("已校正为原文逐字句子" in m for m in log)


def test_far_quote_gets_closest_real_sentence_in_the_repair_prompt():
    llm = Scripted([_reply("这是一段完全改写、与原文相距很远的说法"), _reply(FIRST)])
    log = []
    obj = sm.summarize(TEXT, llm, max_repair_rounds=3, on_round=log.append)
    assert len(llm.prompts) == 2
    real = validate.sentences(TEXT)
    assert "原文中最接近的句子" in llm.prompts[1]
    assert any(sent[:20] in llm.prompts[1] for sent in real)              # 修复提示给出一句真实原文，可直接复制
    assert any("校验未通过" in m and "逐字摘录" in m for m in log)
    assert obj["claims"][0]["quote"] == FIRST


def test_validate_autofix_leaves_unrelated_quotes_alone():
    obj = {"claims": [{"text": "x", "quote": "完全无关的一句话，与本文没有任何相似之处"}]}
    assert validate.autofix_quotes(obj, TEXT) == []
    assert obj["claims"][0]["quote"].startswith("完全无关")


def test_failed_digest_writes_concrete_reasons_to_run_log(data_home):
    ctx = domains.create_domain("ai", "人工智能", "ai")
    importer.apply(ctx, importer.collect([("测试文章.md", TEXT.encode("utf-8"))]))
    row = catalog.list_articles(ctx)[0]

    class Bad:
        def ask(self, prompt, system=""):
            return _reply("永远找不到的引文句子甲乙丙丁戊己庚辛")
    drunner.run_digest(ctx, Bad(), force=True)
    rec = drunner.load_digest(ctx, row["id"])
    assert rec["status"] == "failed" and rec["issues"]
    lines = [r["message"] for r in joblog.tail(ctx.root, limit=500)]
    text = "\n".join(lines)
    assert "失败（" in text and "逐字摘录" in text                       # 失败行带具体原因
    assert "校验未通过" in text                                          # 每一轮修复的原因都有记录
