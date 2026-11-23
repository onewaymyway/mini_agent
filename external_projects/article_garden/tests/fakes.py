"""测试用的假 LLM 与示例文章（不访问任何模型）。"""

from __future__ import annotations

import re

import yaml


def _block(prompt: str, tag: str) -> str:
    m = re.search(rf"<{tag}>\n(.*)\n</{tag}>", prompt, re.S)
    return m.group(1) if m else ""


def _pick_quote(text: str) -> str:
    for line in text.splitlines():
        s = line.strip()
        if len(s) >= 12 and not s.startswith("#"):
            return s[:60]
    return text[:20]


def _concepts(text: str):
    return [c for c in ("向量数据库",) if c in text]


def good_reply(prompt: str) -> str:
    """按提示词阶段返回结构合法、引文真实存在的 YAML。"""
    if "<article>" in prompt:
        text = _block(prompt, "article")
        obj = {"title": "测试文章", "thesis": "论点", "kind": "概念讲解", "level": "入门", "self_score": 4,
               "concepts": _concepts(text), "claims": [{"text": "断言", "quote": _pick_quote(text)}],
               "dangling": []}
    elif "<fragment>" in prompt:
        text = _block(prompt, "fragment")
        obj = {"concepts": _concepts(text), "claims": [{"text": "断言", "quote": _pick_quote(text)}]}
    else:  # 长文元信息步骤
        obj = {"title": "长文", "thesis": "论点", "kind": "综述", "level": "进阶", "self_score": 3, "dangling": []}
    return yaml.safe_dump(obj, allow_unicode=True, sort_keys=False)


class FakeLLM:
    """script：按顺序返回的固定回复（用完后交给 fn）；calls 记录每次提示词。"""

    def __init__(self, fn=None, script=None):
        self.fn = fn or good_reply
        self.script = list(script or [])
        self.calls = []

    def ask(self, prompt, system=""):
        self.calls.append(prompt)
        if self.script:
            return self.script.pop(0)
        return self.fn(prompt)


def article(title: str, marker: str = "", paragraphs: int = 8) -> str:
    body = [f"# {title}", ""]
    for i in range(paragraphs):
        body.append(f"向量数据库用于存储和检索高维向量，常见于语义搜索与检索增强生成第{i}段。{marker}")
        body.append("")
    return "\n".join(body) + "\n"
