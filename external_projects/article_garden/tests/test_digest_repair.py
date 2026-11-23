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
    assert any("原文中的逐字句子" in m for m in log)


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


ARTICLE = ("> **AI 经济的价值体系必须从“劳动数量”逐渐转向“有效贡献”。**\n\n"
           "因此，一个真正的 AI Economic Protocol，至少需要同时解决：身份、价值和结算。\n\n"
           "向量数据库用于存储和检索高维向量，常见于语义搜索与检索增强生成场景。")


def _quote_case(quote):
    llm = Scripted([_reply(quote)])
    obj = sm.summarize(ARTICLE, llm, max_repair_rounds=3)
    return llm, obj["claims"][0]["quote"]


def test_quote_marks_punctuation_and_markdown_differences_are_accepted():
    # 引号、标点、Markdown 标记不同，意思完全相同：不得因此失败，也不耗修复调用
    llm, q = _quote_case('> **AI 经济的价值体系必须从"劳动数量"逐渐转向"有效贡献".**')
    assert len(llm.prompts) == 1
    assert q == "AI 经济的价值体系必须从“劳动数量”逐渐转向“有效贡献”。"   # 去掉了引用/加粗标记
    assert q in ARTICLE                                        # 仍是原文中的逐字片段


def test_truncated_quote_is_accepted_and_stored_as_the_full_original_sentence():
    llm, q = _quote_case("因此 一个真正的AI Economic Protocol 至少需要同时解决：身份")
    assert len(llm.prompts) == 1
    assert q == "因此，一个真正的 AI Economic Protocol，至少需要同时解决：身份、价值和结算。"


def test_small_wording_change_is_accepted_but_meaning_change_is_rejected():
    llm, q = _quote_case("向量数据库用来存储和检索高维向量，常见于语义搜索与检索增强生成场景")
    assert len(llm.prompts) == 1 and q.startswith("向量数据库用于存储")
    llm2 = Scripted([_reply("向量数据库主要用于关系型交易结算与账本记录"), _reply(
        "向量数据库用于存储和检索高维向量，常见于语义搜索与检索增强生成场景。")])
    sm.summarize(ARTICLE, llm2, max_repair_rounds=3)
    assert len(llm2.prompts) == 2                             # 含义不同：仍然要求修复


def test_yaml_error_names_line_column_and_offending_text():
    from article_garden.digest.llm_io import parse_yaml
    _, err = parse_yaml("claims:\n  - text: x\n    quote: > **AI 经济\n")
    assert "第 3 行" in err and "quote: > **AI 经济" in err and "双引号" in err


def test_prompt_asks_for_double_quoted_quote_values():
    from article_garden.digest import summarize as sm
    assert "英文双引号" in sm.SYSTEM                          # 该要求放在系统指令中，每次调用都会发送


def test_round_log_names_the_source_file(data_home):
    from article_garden import domains, joblog
    from article_garden.digest import runner as drunner
    from article_garden.library import catalog, importer
    ctx = domains.create_domain("ai", "人工智能", "ai")
    importer.apply(ctx, importer.collect([("某篇文章.md", TEXT.encode("utf-8"))]))
    row = catalog.list_articles(ctx)[0]

    class Bad:
        def ask(self, prompt, system=""):
            return _reply("永远找不到的引文句子甲乙丙丁戊己庚辛")
    drunner.run_digest(ctx, Bad(), force=True)
    msgs = "\n".join(r["message"] for r in joblog.tail(ctx.root, limit=500))
    assert f"{row['id']}「" in msgs and "来源文件：某篇文章.md" in msgs
