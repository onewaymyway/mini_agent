"""P2 测试辅助：已确认大纲的项目 + 可编排的写稿假模型。"""
import json
import re

from article_to_video.planning.runner import confirm_spine, plan_outline
from helpers_p1 import make_project

FILLER = "这是口播中的一句话，用来说明本章的要点内容。"


def ready_project(root):
    """导入参考文章，生成规则大纲并确认（C0）。"""
    pdir = make_project(root)
    plan_outline(pdir, engine="rules")
    confirm_spine(pdir)
    return pdir


def _budget_and_source(prompt):
    budget = int(re.search(r"BUDGET_CHARS=(\d+)", prompt).group(1))
    refs = json.loads(re.search(r"SECTION_IDS=(\[.*?\])", prompt).group(1))
    part = re.search(r"PART_ID=(P\d+)", prompt).group(1)
    source = prompt.split("【本章原文】\n", 1)[1]
    return part, refs, budget, source


def good_script(prompt: str) -> str:
    """每句都是通用中文（无数字、无英文），字数按预算生成，引用各节。"""
    part, refs, budget, _src = _budget_and_source(prompt)
    n = max(1, budget // len(FILLER)) + 1
    beats = []
    per = max(1, n // max(1, len(refs)))
    sents = [{"text": FILLER, "quote": False, "emphasis": []} for _ in range(n)]
    for i in range(0, n, per):
        beats.append({"role": "claim", "source_refs": [refs[min(len(beats), len(refs) - 1)]],
                      "sentences": sents[i:i + per], "pace": "normal", "pause_after_ms": 300})
    return json.dumps({"beats": beats}, ensure_ascii=False)


def script_with(text_sentences, refs_from):
    def responder(prompt):
        _part, refs, _b, _s = _budget_and_source(prompt)
        return json.dumps({"beats": [{"role": "claim", "source_refs": [refs[0]],
                                      "sentences": [{"text": t} for t in text_sentences]}]},
                          ensure_ascii=False)
    return responder


class ScriptLLM:
    """responder(prompt, attempt_index) -> str；记录调用次数。"""

    def __init__(self, responder):
        self.responder = responder
        self.calls = 0

    def ask(self, prompt, system=""):
        self.calls += 1
        return self.responder(prompt)
